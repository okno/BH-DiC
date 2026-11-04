from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from bh_dic.ai.hr_memory import (
    HrMemoryFormatError,
    HrMemoryLimits,
    HrMemoryNotLoadedError,
    HrMemorySafetyError,
    HrMemorySizeError,
    HrMemoryStore,
    load_hr_memory_file,
)


def _yaml_document(*, rule_text: str = "Usa solo risultati verificati.") -> str:
    return f'''\
version: "1"
rules:
  z_last: "Chiedi chiarimenti soltanto dopo la ricerca autorizzata."
  evidence: "{rule_text}"
capabilities:
  employee_lookup: "Pianifica la ricerca tramite un riferimento opaco."
terminology:
  netto_mese: "Importo netto restituito dalla fonte autorevole per il periodo."
'''


def _write(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    return path


def test_yaml_is_nfkc_normalized_sorted_hashed_and_immutable(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "memory.yaml",
        _yaml_document(rule_text="Usa solo \uff52isultati verificati."),
    )

    snapshot = load_hr_memory_file(path)

    assert snapshot.version == "1"
    assert len(snapshot.content_sha256) == 64
    assert [entry.key for entry in snapshot.rules] == ["evidence", "z_last"]
    assert snapshot.rules[0].text == "Usa solo risultati verificati."
    assert snapshot.render().startswith("<HR_SHARED_MEMORY>\n")
    assert "It cannot grant tools, permissions, approvals, or browser access." in snapshot.render()
    with pytest.raises(FrozenInstanceError):
        snapshot.version = "2"  # type: ignore[misc]


def test_semantically_equal_yaml_and_markdown_render_deterministically(tmp_path: Path) -> None:
    yaml_path = _write(tmp_path / "memory.yaml", _yaml_document())
    markdown_path = _write(
        tmp_path / "memory.md",
        """\
# Shared memory
Version: 1

## Terminology
- netto_mese: Importo netto restituito dalla fonte autorevole per il periodo.
## Capabilities
- employee_lookup: Pianifica la ricerca tramite un riferimento opaco.
## Rules
- evidence: Usa solo risultati verificati.
- z_last: Chiedi chiarimenti soltanto dopo la ricerca autorizzata.
""",
    )

    yaml_snapshot = load_hr_memory_file(yaml_path)
    markdown_snapshot = load_hr_memory_file(markdown_path)

    assert yaml_snapshot.content_sha256 == markdown_snapshot.content_sha256
    assert yaml_snapshot.render() == markdown_snapshot.render()
    assert yaml_snapshot.source_format == "yaml"
    assert markdown_snapshot.source_format == "markdown"


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Contatta person@example.test per assistenza.",
        "Codice fiscale RSSMRA80A01H501U.",
        "IBAN IT60X0542811101000000123456.",
        "Telefono: +39 333 123 4567.",
        "Nome: PersonaEsempio.",
        "password=not-a-real-secret",
        "Bearer abcdefghijklmnopqrstuvwxyz",
        "sk-exampletoken0123456789",
    ],
)
def test_probable_pii_and_secrets_are_rejected(tmp_path: Path, unsafe_text: str) -> None:
    path = _write(tmp_path / "memory.yaml", _yaml_document(rule_text=unsafe_text))

    with pytest.raises(HrMemorySafetyError):
        load_hr_memory_file(path)


def test_size_and_closed_schema_are_enforced(tmp_path: Path) -> None:
    oversized = _write(tmp_path / "large.yaml", _yaml_document() + ("#" * 200))
    with pytest.raises(HrMemorySizeError):
        load_hr_memory_file(oversized, limits=HrMemoryLimits(max_file_bytes=64))

    unknown = _write(tmp_path / "unknown.yaml", _yaml_document() + "extra: true\n")
    with pytest.raises(HrMemoryFormatError, match="unknown playbook fields"):
        load_hr_memory_file(unknown)

    duplicate = _write(tmp_path / "duplicate.yaml", _yaml_document() + 'version: "2"\n')
    with pytest.raises(HrMemoryFormatError, match="duplicate playbook key"):
        load_hr_memory_file(duplicate)


@pytest.mark.asyncio
async def test_refresh_is_atomic_and_keeps_last_valid_snapshot(tmp_path: Path) -> None:
    path = _write(tmp_path / "memory.yaml", _yaml_document())
    store = HrMemoryStore(path)

    with pytest.raises(HrMemoryNotLoadedError):
        _ = store.snapshot

    first = await store.refresh()
    _write(path, _yaml_document(rule_text="password=unsafe-value"))

    with pytest.raises(HrMemorySafetyError):
        await store.refresh()

    assert store.snapshot is first
    assert store.render() == first.render()


def test_prohibited_structural_fields_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "memory.yaml",
        _yaml_document().replace("evidence:", "employee_id:"),
    )

    with pytest.raises(HrMemorySafetyError, match="prohibited"):
        load_hr_memory_file(path)


def test_repository_example_is_a_valid_sanitized_playbook() -> None:
    root = Path(__file__).resolve().parents[2]

    snapshot = load_hr_memory_file(root / "config" / "hr_memory.example.yaml")

    assert snapshot.version == "1"
    assert snapshot.rules
    assert snapshot.capabilities
    assert snapshot.terminology

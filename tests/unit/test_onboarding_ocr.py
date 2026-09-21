from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from bh_dic.onboarding.ocr import (
    LocalTesseractOcr,
    OcrInputError,
    OcrOutputError,
    OcrText,
    OcrTimeoutError,
    OcrUnsupportedFormatError,
    merge_ocr_texts,
)


def _synthetic_png(path: Path) -> None:
    Image.new("RGB", (16, 16), color=(255, 255, 255)).save(path, format="PNG")


def _service(root: Path, **overrides: Any) -> LocalTesseractOcr:
    values: dict[str, Any] = {
        "quarantine_root": root,
        "timeout_seconds": 2.0,
        "max_input_bytes": 1024 * 1024,
        "max_output_bytes": 4096,
    }
    values.update(overrides)
    return LocalTesseractOcr(**values)


@pytest.mark.asyncio
async def test_local_ocr_runs_tesseract_with_fixed_safe_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "quarantine"
    root.mkdir()
    image_path = root / "opaque01.png"
    _synthetic_png(image_path)
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=b"NOME: NOMEALFA\n", stderr=None)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = await _service(root).extract(
        path=image_path.resolve(), mime_type="image/png", source="upload_01"
    )

    assert result.text == "NOME: NOMEALFA"
    assert "NOMEALFA" not in repr(result)
    argv, kwargs = calls[0]
    assert argv == ["tesseract", str(image_path.resolve()), "stdout", "-l", "ita+eng"]
    assert kwargs["shell"] is False
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.DEVNULL


@pytest.mark.asyncio
async def test_local_ocr_rejects_traversal_and_external_path_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "quarantine"
    root.mkdir()
    outside = tmp_path / "outside.png"
    _synthetic_png(outside)

    def forbidden_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    service = _service(root)
    with pytest.raises(OcrInputError):
        await service.extract(
            path=root / ".." / "outside.png", mime_type="image/png", source="upload_02"
        )
    with pytest.raises(OcrInputError):
        await service.extract(path=outside.resolve(), mime_type="image/png", source="upload_02")


@pytest.mark.asyncio
async def test_local_ocr_rejects_symlink_even_when_target_is_inside_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "quarantine"
    root.mkdir()
    target = root / "opaque-target.png"
    _synthetic_png(target)
    link = root / "opaque-link.png"
    link.symlink_to(target)

    def forbidden_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    with pytest.raises(OcrInputError):
        await _service(root).extract(path=link, mime_type="image/png", source="upload_03")


@pytest.mark.asyncio
async def test_local_ocr_rejects_oversized_input_before_image_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "quarantine"
    root.mkdir()
    image_path = root / "opaque04.png"
    image_path.write_bytes(b"x" * 33)

    def forbidden_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    with pytest.raises(OcrInputError, match="limite dimensionale"):
        await _service(root, max_input_bytes=32).extract(
            path=image_path.resolve(), mime_type="image/png", source="upload_04"
        )


@pytest.mark.asyncio
async def test_local_ocr_rejects_excessive_pixel_count_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "quarantine"
    root.mkdir()
    image_path = root / "opaque-pixels.png"
    _synthetic_png(image_path)

    def forbidden_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    with pytest.raises(OcrInputError, match="pixel"):
        await _service(root, max_pixels=255).extract(
            path=image_path.resolve(), mime_type="image/png", source="upload_pixels"
        )


@pytest.mark.asyncio
async def test_local_ocr_rejects_pdf_and_mime_content_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "quarantine"
    root.mkdir()
    image_path = root / "opaque05.png"
    _synthetic_png(image_path)

    def forbidden_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    service = _service(root)
    with pytest.raises(OcrUnsupportedFormatError, match="PDF"):
        await service.extract(
            path=image_path.resolve(), mime_type="application/pdf", source="upload_05"
        )
    with pytest.raises(OcrInputError):
        await service.extract(path=image_path.resolve(), mime_type="image/jpeg", source="upload_05")


@pytest.mark.asyncio
async def test_local_ocr_rejects_excessive_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "quarantine"
    root.mkdir()
    image_path = root / "opaque06.png"
    _synthetic_png(image_path)

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 0, stdout=b"x" * 17, stderr=None)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(OcrOutputError):
        await _service(root, max_output_bytes=16).extract(
            path=image_path.resolve(), mime_type="image/png", source="upload_06"
        )


@pytest.mark.asyncio
async def test_local_ocr_timeout_error_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "quarantine"
    root.mkdir()
    image_path = root / "opaque07.png"
    _synthetic_png(image_path)

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(
            cmd=argv,
            timeout=2,
            output=b"SYNTHETIC_SECRET_SHOULD_NOT_SURFACE",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(OcrTimeoutError) as caught:
        await _service(root).extract(
            path=image_path.resolve(), mime_type="image/png", source="upload_07"
        )
    rendered = str(caught.value)
    assert "SYNTHETIC_SECRET" not in rendered
    assert str(image_path) not in rendered


@pytest.mark.asyncio
async def test_local_ocr_preserves_task_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "quarantine"
    root.mkdir()
    image_path = root / "opaque08.png"
    _synthetic_png(image_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
        if function.__name__ == "_run_tesseract":
            started.set()
            await release.wait()
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    task = asyncio.create_task(
        _service(root).extract(path=image_path.resolve(), mime_type="image/png", source="upload_08")
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()


def test_merge_parses_synthetic_italian_documents_and_reports_missing_contact_fields() -> None:
    document = OcrText(
        source="synthetic_front",
        text="""
        COGNOME / SURNAME: COGNOMEALFA
        NOME / NAME: NOMEALFA
        CODICE FISCALE: AAAAAA00A00A000A
        LUOGO DI NASCITA: CITTÀ ALFA
        DATA DI NASCITA: 01/01/2000
        SESSO / SEX: F
        CITTADINANZA / NATIONALITY: ITA
        INDIRIZZO DI RESIDENZA: VIA SINTETICA 1
        NUMERO DOCUMENTO: AA00000AA
        SCADENZA / EXPIRY: 01/01/2030
        """,
    )

    draft = merge_ocr_texts((document,))

    assert draft.fields["first_name"].value == "NOMEALFA"
    assert draft.fields["last_name"].value == "COGNOMEALFA"
    assert draft.fields["tax_code"].value == "AAAAAA00A00A000A"
    assert draft.fields["date_of_birth"].value == "2000-01-01"
    assert draft.fields["place_of_birth"].value == "CITTÀ ALFA"
    assert draft.fields["document_number"].value == "AA00000AA"
    assert draft.fields["expiry_date"].value == "2030-01-01"
    assert draft.fields["first_name"].source == "synthetic_front"
    assert 0 < draft.fields["first_name"].confidence <= 1
    assert "email" in draft.missing_required
    assert "phone" in draft.missing_required
    assert "NOMEALFA" not in repr(draft.fields["first_name"])


def test_merge_reports_conflicts_and_never_auto_resolves_them() -> None:
    front = OcrText(
        source="synthetic_front",
        text="""
        COGNOME: COGNOMEALFA
        NOME: NOMEALFA
        CODICE FISCALE: AAAAAA00A00A000A
        DATA DI NASCITA: 01/01/2000
        """,
    )
    back = OcrText(
        source="synthetic_back",
        text="""
        COGNOME: COGNOMEALFA
        NOME: NOMEALFA
        CODICE FISCALE: CCCCCC00A00C000C
        DATA DI NASCITA: 01/01/2000
        """,
    )

    draft = merge_ocr_texts((front, back))

    assert "tax_code" not in draft.fields
    conflict = next(item for item in draft.conflicts if item.field_name == "tax_code")
    assert {item.value for item in conflict.candidates} == {
        "AAAAAA00A00A000A",
        "CCCCCC00A00C000C",
    }
    assert {item.source for item in conflict.candidates} == {
        "synthetic_front",
        "synthetic_back",
    }
    assert "tax_code" in draft.missing_required


def test_merge_deduplicates_equivalent_values_without_creating_a_conflict() -> None:
    first = OcrText(source="synthetic_a", text="NOME: Nomealfa\nCOGNOME: Cognomealfa")
    second = OcrText(source="synthetic_b", text="NOME: NOMEALFA\nCOGNOME: COGNOMEALFA")

    draft = merge_ocr_texts((first, second), required_fields=("first_name", "last_name"))

    assert draft.conflicts == ()
    assert draft.missing_required == ()
    assert set(draft.fields) == {"first_name", "last_name"}

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DOCUMENTS = (ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")))
POLICY_FILES = (*PUBLIC_DOCUMENTS, ROOT / ".github" / "workflows" / "codeql.yml")
PUBLIC_TEXT_ROOTS = (
    ROOT / ".github",
    ROOT / "config",
    ROOT / "docs",
    ROOT / "infrastructure",
    ROOT / "migrations",
    ROOT / "scripts",
    ROOT / "src",
    ROOT / "tests",
)
PUBLIC_TEXT_SUFFIXES = {
    ".example",
    ".ini",
    ".lock",
    ".mako",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
}


def _public_document_corpus() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC_DOCUMENTS)


def _tracked_public_text_corpus() -> str:
    root_files = (
        ROOT / ".env.example",
        ROOT / ".gitignore",
        ROOT / "AGENTS.md",
        ROOT / "Makefile",
        ROOT / "README.md",
        ROOT / "SECURITY.md",
        ROOT / "pyproject.toml",
    )
    nested_files = (
        path
        for directory in PUBLIC_TEXT_ROOTS
        for path in directory.rglob("*")
        if path.is_file() and path.suffix in PUBLIC_TEXT_SUFFIXES
    )
    return "\n".join(path.read_text(encoding="utf-8") for path in (*root_files, *nested_files))


def test_public_documents_do_not_embed_operational_discord_identifiers() -> None:
    corpus = _public_document_corpus()
    tracked_text = _tracked_public_text_corpus()

    assert re.search(r"(?<!\d)\d{17,20}(?!\d)", corpus) is None
    assert ("1303955635" + "984924722") not in tracked_text
    assert ("#" + "mng" + "-ai").casefold() not in tracked_text.casefold()
    assert "<DISCORD_GUILD_ID>" in corpus
    assert "<DISCORD_CHANNEL_ID>" in corpus


def test_public_repository_policy_does_not_reintroduce_a_private_visibility_gate() -> None:
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in POLICY_FILES)
    stale_private_gate_patterns = (
        r"repository\s+deve\s+restare\s+" + r"privata",
        r"repository\s+privata.{0,100}" + r"blocca\s+la\s+produzione",
        r"visibilit[aà].{0,30}deve\s+risultare.{0,30}" + r"\bprivate\b",
        r"clone\s+" + r"privato",
        r"rendere(?:\s+e\s+mantenere)?\s+la\s+repository\s+" + r"privata",
        r"progetto\s+resta\s+" + r"`?private`?",
        r"deve\s+tornare\s+" + r"`?private`?",
        r"requisito\s+repository\s+" + r"privata",
        r"visibility\s+must\s+be\s+verified\s+as\s+" + r"`private`",
        r"private-repository\s+" + r"plan",
        r"blocco\s+di\s+sicurezza\s+per\s+la\s+produzione",
    )

    assert all(
        re.search(pattern, corpus, flags=re.IGNORECASE | re.DOTALL) is None
        for pattern in stale_private_gate_patterns
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "repository è pubblica per scelta esplicita" in readme
    assert "Segreti, identificatori operativi, stato runtime e" in readme
    assert "PII devono restare fuori da Git" in readme


def test_public_codeql_workflow_uploads_security_results() -> None:
    workflow = (ROOT / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")

    assert "security-events: write" in workflow
    assert "upload:" + " never" not in workflow


def test_documented_live_status_matches_the_observed_transport_and_auth_gates() -> None:
    status_files = (
        ROOT / "README.md",
        ROOT / "docs" / "IMPLEMENTATION_REPORT.md",
        ROOT / "docs" / "LIVE_VERIFICATION_STATUS.md",
        ROOT / "docs" / "OPERATIONS.md",
    )

    for path in status_files:
        text = path.read_text(encoding="utf-8")
        assert "VERIFIED_BY_ADAPTER" in text, path
        assert "active/running" in text, path
        assert "NRestarts=0" in text, path
        assert "RBAC" in text, path

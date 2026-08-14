# Changelog

All notable changes to BH-DiC are documented here. The project follows Semantic Versioning once a
stable public API is declared.

## [Unreleased]

### Added

- Secure Python 3.12 project foundation and locked direct dependencies.
- Fail-closed Pydantic settings with an explicit isolated mock mode.
- Structured JSON logging with centralized secret and PII redaction.
- Async SQLAlchemy persistence with SQLite WAL support and Alembic migrations.
- Append-only HMAC-SHA256 audit chain with full-chain verification.
- Foundation unit and integration tests using synthetic local data only.

### Security

- All mutating feature flags default to disabled.
- Provider-side OpenAI storage is rejected by configuration validation.
- Runtime startup rejects missing secrets, guild/channel identifiers, and unsafe write settings.

[Unreleased]: https://github.com/okno/BH-DiC/commits/main

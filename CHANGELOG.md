# Changelog

All notable changes to BH-DiC are documented here. The project follows Semantic Versioning once a
stable public API is declared.

## [Unreleased]

## [0.2.3] - 2026-08-16

### Fixed

- Il login DIC attende ora in modo limitato l'hydration dei controlli sulle sole route esatte
  consentite, rifiuta controlli visibili ambigui e mantiene il CAPTCHA sotto verifica durante
  l'attesa. Il pulsante DIC conserva il `data-testid` e aggiunge il fallback semantico pubblico
  verificato sul ruolo `button` con nome esatto `Accedi`.
- Probe di sessione e autenticazione passano dalla coda browser con un solo tentativo e un budget
  dedicato pari al timeout di login più cinque secondi; timeout o errori di trasporto non provocano
  un nuovo invio automatico delle credenziali.
- `dic-auth-check` restituisce per gli errori soltanto JSON con tipo e stage appartenente a un
  insieme chiuso, senza messaggi provider, URL, selettori, tenant, credenziali o contenuti DOM.
- Se il submit della credenziale può essere arrivato a TeamSystem ma completamento, tenant probe o
  persistenza del vault non sono dimostrabili, il risultato è
  `DicAuthOutcomeUnknownError`/`CREDENTIAL_SUBMIT` con exit code 78 e nessun retry. Il comando
  `run` usa lo stesso exit code per ogni errore di autenticazione e l'unit systemd impedisce il
  restart automatico su 78.

### Changed

- La documentazione operativa registra il rinnovo umano della password TeamSystem e
  l'aggiornamento del secret locale. Il tentativo live con la 0.2.2 si è fermato prima
  dell'autenticazione per una race di hydration; sessione, tenant e Function ID DIC restano da
  verificare live, con bot fermo e write disabilitate.

## [0.2.2] - 2026-08-16

### Fixed

- Test e comandi di verifica mock sono ora isolati dal file `.env` operativo e dalle variabili
  ambiente di produzione, evitando che provider, token Discord, chiavi audit o database reali
  alterino i risultati della suite.
- La configurazione mock usa sempre un database SQLite in memoria, nessun segreto runtime e un
  provider sintetico deterministico; la configurazione live resta invariata.
- Aggiunta una regressione che riproduce l'installazione Debian con Groq e alias di tuning in
  conflitto senza caricare né stampare valori sensibili.

## [0.2.1] - 2026-08-16

### Added

- Current TeamSystem multi-step sign-in support for Dipendenti in Cloud, including explicit
  fail-closed handling when interactive password renewal is required.
- Passive current-company attestation during a fixed read-only employee-list navigation, with
  restored browser-session verification before credentials are used.

### Changed

- Debian deployment and operations documentation now records the verified Groq and local runtime
  gates while keeping the unfinished DIC authentication check clearly blocked.
- Shell environment parsing uses a portable `awk` quote expression and no longer emits `mawk`
  escape warnings on Debian 12.

### Security

- Tenant authorization now requires the exact first-party company-info response contract and no
  longer trusts company names or inferred DOM attributes.
- Tenant response bodies, identifiers, URLs and parsing failures are excluded from exception
  chains and structured logs.

## [0.2.0] - 2026-08-15

### Added

- Multi-provider model routing for OpenAI, Groq and a local OpenAI-compatible llama endpoint,
  with canonical shared `MODEL_*` tuning and provider-specific credentials.
- Configurable Italian/English clarification and decoration profile with bounded tone, address
  style, verbosity, status emoji and optional opening/closing text; deterministic operational
  output remains Italian and the profile never changes authorization or tool exposure.
- End-to-end Debian 12/13 installation and operations guide, least-privilege Discord guild setup,
  systemd/PID lifecycle separation, read-only first verification and rollback/incident runbooks.
- Offline-by-default `model-check` command with one explicit, synthetic and closed live provider
  probe that never constructs Discord, DIC or browser services.
- Secure Python 3.12 project foundation and locked direct dependencies.
- Fail-closed Pydantic settings with an explicit isolated mock mode.
- Structured JSON logging with centralized secret and PII redaction.
- Async SQLAlchemy persistence with SQLite WAL support and Alembic migrations.
- Append-only HMAC-SHA256 audit chain with full-chain verification.
- Foundation unit and integration tests using synthetic local data only.

### Security

- All mutating feature flags default to disabled.
- Provider-side model storage is rejected by configuration validation (`MODEL_STORE=false`).
- Groq uses the fixed official OpenAI-compatible base URL; llama HTTP endpoints are restricted to
  loopback and unsafe URL components are rejected.
- Provider transports reject redirects, ambient proxies and unsafe OpenAI SDK environment
  overrides; provider exception bodies are never chained into Discord logs.
- Groq `gsk_` credentials and labeled API keys are redacted before provider and logging boundaries.
- Runtime startup rejects missing secrets, guild/channel identifiers, and unsafe write settings.

[Unreleased]: https://github.com/okno/BH-DiC/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/okno/BH-DiC/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/okno/BH-DiC/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/okno/BH-DiC/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/okno/BH-DiC/releases/tag/v0.2.0

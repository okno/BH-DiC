# Instructions for agents and contributors

These rules apply to the entire BH-DiC repository.

## Safety invariants

- BH-DiC is restricted to authorized employee workflows in the configured Discord guild,
  channel, and Dipendenti in Cloud tenant.
- Never run a live write unless all required live-test flags and an explicitly dedicated
  synthetic employee are configured. Development and CI use mocks and sanitized fixtures.
- Keep `OPENAI_STORE=false`. OpenAI may classify a bounded intent; it must never control the
  browser, access secrets, inspect files, or decide authorization or approval.
- All write feature flags default to `false`. Preserve preview, explicit confirmation,
  idempotency, postcondition checks, and two-person approval where required.
- Do not weaken TLS, SSH host-key verification, authentication, RBAC, redaction, quarantine,
  antivirus, audit chaining, or filesystem permissions to make a test pass.

## Data protection

- Never commit tokens, passwords, API keys, cookies, TOTP seeds, `.env`, browser storage state,
  real employee identifiers, PII, HR documents, payrolls, screenshots, traces, or database dumps.
- Tests and fixtures must contain synthetic data only. Use documentation-reserved identifiers and
  manually inspect any sanitized DOM before committing it.
- Logs contain structured metadata only. Redact secrets and PII centrally; do not log full prompts,
  file contents, authorization headers, or confirmation codes.
- Audit records are append-only and HMAC chained. Do not rewrite or silently repair history.

## Engineering gates

- Use Python 3.12 or newer, asynchronous APIs, strict Pydantic models, and deterministic adapters.
- Add tests for every behavior change. Network tests must be explicitly marked and disabled by
  default; no test may contact the production tenant or send Discord messages.
- Before commit run `ruff format --check .`, `ruff check .`, `mypy src`, `pytest`,
  `bandit -r src`, `pip-audit`, and `gitleaks detect` when available.
- Never claim a live write was verified unless it was expressly authorized and actually verified.
- Avoid force pushes and history rewrites. Keep changes minimal, reviewable, and reversible.

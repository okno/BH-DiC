# Threat model

## Model scope

This model covers Discord input, multi-provider intent routing, local persistence, Playwright
automation, attachment handling, audit/logging and the single Linux host. It assumes authorized
administrative use of one configured DIC tenant. It does not authorize bypassing MFA, CAPTCHA or
provider access controls.

## Assets

- Discord bot token, the selected model-provider key and DIC credentials;
- encrypted browser session and encryption/HMAC keys;
- employee identifiers, transient `SecretStr` display names and redacted HR fields;
- pending-action parameters, approvals and idempotency state;
- uploaded documents and payroll files during their bounded local lifetime;
- audit-chain integrity and operational logs;
- DIC tenant integrity and availability.

## Adversaries and failure sources

- an unauthorized Discord user or compromised authorized account;
- a malicious attachment or filename;
- prompt injection in a Discord message, document or website text;
- a compromised dependency, host account or browser session;
- accidental operator misconfiguration;
- DIC UI drift, timeout or partial/uncertain write;
- a local actor modifying the database or deleting audit records.

## Threat register

| ID | Threat | Primary controls | Residual risk / response |
| --- | --- | --- | --- |
| T01 | Command from another guild/channel/DM | allowlists before routing; DM denial; audit/security event | compromised configured channel still requires RBAC |
| T02 | Role spoofing or stale Discord UI permission | application maps immutable Discord Role IDs and rechecks at approval/execution | revoke role and pending actions; review audit |
| T03 | Model invents an operation or arguments | closed Function ID catalog, strict schema, Pydantic validation | deny unknown ID; inspect correlation ID |
| T04 | Model controls browser/shell/HTTP | no such tools; deterministic service and adapter boundary | code review protects the tool registry |
| T05 | Prompt injection from Discord/site/document | sanitize input; site text treated as data; documents never sent to the model provider | log a redacted suspicious-input event |
| T06 | Cross-tenant action | configured expected tenant checked by policy and adapter context | session invalidation and incident review on mismatch |
| T07 | Write to the wrong employee | ID-first resolution, ambiguity denial, preview, target text and re-read | no write by name alone |
| T08 | Approval replay/self-approval | HMAC code, TTL, one-time consumption, distinct approvers, DB uniqueness | expire/reject pending action and investigate replay |
| T09 | Double execution | durable idempotency claim, CAS version, locks | reconcile rather than retry after a crash |
| T10 | State changes after preview | state fingerprint and pre-execution read | mark stale; create a new preview |
| T11 | Browser timeout after submit | no write retry; uncertain status and read-only reconciliation | manual operator decision if read-back remains unclear |
| T12 | UI selector drift causes wrong click | route/landmark validation, typed drift error, postcondition check | circuit opens/DEGRADED; update sanitized fixture |
| T13 | Credential or PII leakage in logs | central recursive redaction; forbidden audit keys; pseudonyms | restrict log access and scan before release |
| T14 | Browser session theft | Fernet vault, `0600`/`0700`, no storage-state logs | invalidate session and rotate DIC credentials |
| T15 | Path traversal or filename collision | reject separators/absolute paths; UUID opaque paths; exclusive create | quarantine directory remains untrusted |
| T16 | MIME/extension spoofing | content MIME detection and extension consistency | unsupported/polyglot files are rejected |
| T17 | Malware upload | ClamAV scan; required scanner fails closed | quarantine/rejected retention must be monitored |
| T18 | Document exfiltration through Discord/model provider | no document output; opaque local ID; model transmission forbidden | protect host access and local exports |
| T19 | Oversized/duplicate-file resource exhaustion | streaming size cap, SHA-256 dedup, rate limit, retention | monitor disk and purge expired data |
| T20 | Audit row edit, deletion or reorder | HMAC chain, contiguous sequence and chain-state tail check | alert, preserve evidence, stop writes |
| T21 | Audit HMAC key compromise | protected secret and host least privilege | HMAC is not non-repudiation; rotate via controlled chain rollover |
| T22 | Database corruption/lock | WAL, busy timeout, backup/restore and health checks | stop writes until integrity is established |
| T23 | Dependency/supply-chain compromise or public-source data leak | pinned lock, `pip-audit`, Bandit, gitleaks, CodeQL, sanitized public tree and protected review process | rotate any exposed secret, purge PII through an approved incident procedure, triage advisories and rebuild from trusted source |
| T24 | Denial of service via commands/browser jobs | rate limit, single browser queue, timeout, circuit breaker | authorized reads may be delayed during degradation |
| T25 | Operator enables a critical flag unsafely | validated flag invariants, global kill switch, A2 mandatory | configuration change is privileged and audited |
| T26 | Persona text is used as prompt injection or mention abuse | closed enums; bounded local decorations; URL/mention/token/instruction rejection; decorations never sent to provider | review configuration change and keep safety strings deterministic |
| T27 | Clear employee name escapes its authorized response | `SecretStr`, sensitive/ephemeral-only unwrap, no public/provider/log/audit/telemetry/model-dump destination | revoke access, preserve redacted evidence and perform privacy incident review |
| T28 | Search name collides with an allowed HR word | remove the complete local search span before canonical semantic-category projection | fail closed on unmappable intent; never forward the raw search value |

## Abuse cases that must remain impossible

- asking the model to browse an arbitrary URL or execute JavaScript/shell;
- executing a write using only a name or an ambiguous search result;
- approving the same critical action twice with one Discord identity;
- executing an expired, stale, rejected or uncertain pending action;
- uploading when ClamAV is required but unavailable;
- publishing a document, payroll or protected local path in Discord;
- silently retrying a write after loss of the browser result;
- enabling a specific write while the global kill switch is false.

## Security-event priorities

Immediate investigation is required for audit-chain failure, malware, cross-tenant context,
employee deletion attempts, repeated authentication failures, approval separation violations and
uncertain writes. UI drift and dependency failures normally degrade service but must still deny the
affected action.

## Assumptions and residual risks

- Discord, the selected model runtime, DIC and ClamAV remain dependencies outside the
  deterministic policy core.
- A fully compromised host can access data while the process is running; disk encryption, host
  hardening, patching and account controls remain operator responsibilities.
- HMAC proves integrity to a holder of the same secret; it is not a public digital signature.
- Playwright selectors need live read-only validation after DIC UI changes.
- MFA/CAPTCHA may require an authorized manual session-establishment step.
- No live write is considered verified until it is run in an explicitly authorized test tenant.

Review this model after any new Function ID, new Discord interaction mode, new file type, official
API adapter or change to the DIC authentication/UI flow.

# Privacy and GDPR operating guide

This document is an engineering and operations guide, not legal advice. The organization deploying
BH-DiC must identify the controller, processors, lawful basis, retention policy, data-subject
contact and incident process before production use.

## Processing principles

BH-DiC applies data protection by design through:

- purpose limitation to authorized employee workflows in one configured DIC tenant;
- data minimization rather than a local replica of the employee registry;
- deterministic output and redaction before Discord, the selected model provider, logs and audit;
- default-disabled writes, document transfer and exports;
- short-lived pending actions and attachment retention;
- encryption of pending parameters and browser session state;
- role-based access, independent approval and accountable audit metadata.

## Data inventory

| Data class | Source | Local handling | External destination |
| --- | --- | --- | --- |
| Discord actor/guild/channel IDs | Discord | request, approval and audit metadata | none beyond Discord response |
| Employee ID | DIC/user | pending target; pseudonymized in logs | DIC; redacted Discord output |
| Employee profile/contract/balance metadata | DIC | transient response; redacted preview where needed | authorized ephemeral Discord response |
| Write parameters | authorized user | Fernet-encrypted in `pending_actions` | DIC only after approval |
| Confirmation code | application | plaintext returned once; only salted HMAC digest persists | authorized requester via Discord |
| Approval decisions | Discord | persistent workflow/audit metadata | none |
| Browser cookies/storage state | DIC | encrypted session vault only | DIC browser context |
| Attachment content | Discord | bounded quarantine/clean/processed area | DIC upload workflow only when enabled |
| Original attachment filename | Discord | protected metadata; never a path or ordinary log field | not sent to a model provider |
| Model intent input | Discord | minimized and redacted; no persistent conversation | selected provider with `MODEL_STORE=false` |
| Persona decorations | local configuration | bounded and validated; values omitted from safe summary/logs | configured Discord embed only; not sent to provider |
| Audit/log metadata | application | redacted JSON and HMAC chain | optional Wazuh ingestion |

The local database must not become a durable copy of IBANs, full tax codes, complete notes,
documents, payrolls, passwords, plaintext cookies or full HR prompts.

## Model-provider privacy boundary

OpenAI, Groq or the configured llama endpoint is used only to classify supported requests,
normalize parameters and request clarification.
The following are forbidden provider inputs:

- credentials, tokens, cookies, TOTP secrets and browser storage state;
- complete documents and payrolls;
- full IBAN, tax code, address, telephone and internal notes;
- health, family or other special-category data;
- unredacted DIC result pages.

`MODEL_STORE=false` is mandatory. OpenAI/Groq requests apply the supported storage control; the
llama chat-compatible request omits unsupported storage and conversation-state parameters.
Persistent conversations and HR use of provider-side conversation identifiers are prohibited.
The `deterministic` rendering mode constructs DIC results locally. Any future `redacted_ai` mode
requires a separate privacy review and may receive only data already redacted by deterministic
code.

For an external provider, the controller must assess contract, subprocessor list, processing
region, international-transfer mechanism and configured retention independently of this
application. A local llama endpoint still requires host, access, model and log-retention review.

## Discord disclosure rules

Public channel output is limited to non-sensitive aggregate information. Employee lists, details,
contracts, balances, document metadata and approval previews use ephemeral responses where the
Discord interaction permits it. Never publish:

- IBAN, complete tax code, phone, address or full birth date;
- employee internal notes, health/family data;
- document or payroll content;
- local filesystem paths, tokens, cookies or credentials.

Ephemeral delivery reduces accidental exposure but is not a substitute for authorization or a
retention policy: Discord and user clients remain external systems.

## Retention

The deployment owner must document concrete retention periods. Technical defaults/controls are:

| Record | Technical behavior | Required operator decision |
| --- | --- | --- |
| Pending approval | actionable for `PENDING_ACTION_TTL_MINUTES` (default 10) | retention of terminal metadata |
| Attachment bytes | expires after `UPLOAD_RETENTION_HOURS` (default 24) | scheduled purge and exception process |
| Playwright traces | vietati in non-mock; solo fixture sintetiche in ambiente mock | cancellazione alla scadenza |
| Browser session | encrypted until expiry/invalidation | invalidate after personnel/credential changes |
| Application/security logs | structured and redacted | rotation and deletion interval |
| Audit chain | integrity/accountability record | statutory/HR retention and protected archival |
| Backups | must exclude secrets/session/files unless explicitly encrypted | backup expiry and secure destruction |

Deleting attachment bytes must emit a deletion metadata event. A backup can extend effective
retention, so restore media and snapshots must be included in the policy.

## Data-subject requests

DIC remains the authoritative employee system. BH-DiC should not independently correct or disclose
data merely because a request arrives in Discord. Route access, correction, restriction, objection
and deletion requests through the controller's verified process. Search relevant local records by
approved metadata/correlation ID without exposing unrelated users, then:

1. verify identity and authority outside the bot;
2. identify applicable legal/HR retention obligations;
3. act in DIC through the approved workflow;
4. delete eligible transient files and diagnostic artifacts;
5. preserve or lawfully restrict audit records rather than silently altering the chain;
6. record the outcome without inserting the request's sensitive content into logs.

## Security incidents and breaches

On suspected disclosure or credential/session compromise:

1. set `ENABLE_WRITE_ACTIONS=false` through the approved runtime mechanism;
2. stop the bot if confidentiality is at risk;
3. preserve redacted logs and verify the audit chain;
4. invalidate the DIC session and rotate affected Discord, model-provider, DIC and encryption
   credentials;
5. isolate and hash relevant files without opening them;
6. determine affected people, data, duration and recipients;
7. follow the controller's breach assessment and notification timetable;
8. document remediation and validate selectors/policies before restart.

Do not copy sensitive evidence into tickets, chat or this repository.

## Repository and test-data hygiene

- `.env`, session state, database files, logs, traces, uploads and backups are Git-ignored.
- Fixtures are synthetic or fully redacted.
- Sanitized DOM capture must remove names, email, tax code, IBAN, phone, address and real Employee ID.
- Run secret/PII scanning before every push.
- Never use real documents in unit, integration or CI tests.

## Production privacy checklist

- [ ] Controller, processor roles and authorized purposes are recorded.
- [ ] Lawful basis and any required DPIA have been approved.
- [ ] Discord membership/roles and channel retention are reviewed.
- [ ] Contractual, transfer and retention settings of the selected external provider, or the
      controls of the local llama host, are reviewed.
- [ ] DIC account has least privilege and a documented MFA process.
- [ ] Log, audit, attachment, trace and backup retention periods are configured.
- [ ] Data-subject and breach procedures name responsible contacts.
- [ ] A restore test confirms deleted transient content is not unintentionally reintroduced.

Reassess privacy before enabling a new write, file type, AI rendering mode, official API adapter or
additional tenant.

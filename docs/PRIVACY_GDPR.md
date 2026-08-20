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
| Employee ID | DIC/user | pending target; pseudonymized in logs | DIC; authorized ephemeral Discord only, never public/provider/telemetry |
| Employee display name | DIC | transient `SecretStr`, excluded from repr and masked in model dumps; initials retained for safe fallback | authorized `HR_READ` ephemeral list/expiry only |
| Other employee profile/contract/balance metadata | DIC | transient response; personal fields redacted where required | authorized ephemeral Discord response |
| Write parameters | authorized user | Fernet-encrypted in `pending_actions` | DIC only after approval |
| Confirmation code | application | plaintext returned once; only salted HMAC digest persists | authorized requester via Discord |
| Approval decisions | Discord | persistent workflow/audit metadata | none |
| Browser cookies/storage state | DIC | encrypted session vault only | DIC browser context |
| Attachment content | Discord | bounded quarantine/clean/processed area | DIC upload workflow only when enabled |
| Original attachment filename | Discord | protected metadata; never a path or ordinary log field | not sent to a model provider |
| Model intent input | Discord | minimized and redacted; no persistent conversation | selected provider with `MODEL_STORE=false` |
| Public HR message | Discord allowlisted channel | current message only; normalized, redacted and not persisted as conversation | selected provider with no tools; public Discord reply |
| Model usage counters | selected provider | provider/model, status, timestamps and exact token counts only | local database and authorized Discord status |
| Persona decorations | local configuration | bounded and validated; values omitted from safe summary/logs | configured Discord embed only; not sent to provider |
| Audit/log metadata | application | redacted JSON and HMAC chain | optional Wazuh ingestion |

The local database must not become a durable copy of IBANs, full tax codes, complete notes,
documents, payrolls, passwords, plaintext cookies or full HR prompts.

## Model-provider privacy boundary

OpenAI, Groq or the configured llama endpoint classifies supported `/bh` requests. In optional
`channel` mode it also generates general HR guidance from one redacted current message, without
tools, DIC data or conversation history.
For the operational intent router shared by `/bh` and recognized channel requests, the following
are forbidden provider inputs:

- credentials, tokens, cookies, TOTP secrets and browser storage state;
- complete documents and payrolls;
- full IBAN, tax code, address, telephone and internal notes;
- health, family or other special-category data;
- unredacted DIC result pages.
- employee names, Employee ID, search identity, DIC result rows, DOM and contract-expiry results.

Before routing, the request is projected onto a closed set of semantic-category labels rather
than forwarding raw recognized words. Search values are removed as a whole before token mapping,
so a name that also resembles an HR term or month cannot survive the boundary. Unknown terms and
employee identity material become placeholders; an explicitly labelled Employee ID and a local
employee search query are restored only inside the deterministic application boundary after
routing. The operational Senior HR presenter is local. The separate public responder calls the
configured provider only after removing recognized contacts, secrets, Discord references, URLs,
employee identifiers, amounts, name-shaped sequences and likely individual cases. This
deterministic boundary also covers labelled, directed and common lowercase person contexts; it
cannot serve as general-purpose natural-language named-entity recognition. Output passes through
the same public redactor and mention neutralizer. Automated redaction is not a substitute for
channel policy: users must not post personal or special-category data in the public channel.

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

By default, operational DIC output in ordinary channel messages is limited to non-sensitive
aggregate information. The optional public responder may publish general HR guidance, but never
DIC-derived or individual data. In a deliberately private HR channel, an operator may enable
`DISCORD_PUBLISH_SENSITIVE_CHANNEL_RESPONSES=true`; the same application RBAC remains mandatory
and Discord then becomes an explicit recipient of employee lists, contracts, approval previews
and generated attachments. Never publish:

- IBAN, complete tax code, phone, address or full birth date;
- employee internal notes, health/family data;
- document or payroll content;
- local filesystem paths, tokens, cookies or credentials.

For an authorized `HR_READ` list or contract-expiry result, the local presenter may unwrap the
employee display name from `SecretStr` into an ephemeral Discord embed or, after the explicit
private-channel opt-in, the protected channel response. The value is never placed in a public
aggregate, provider request, log, audit event, token telemetry, database record or model dump.
Other personal fields remain redacted according to their typed projection.

Ephemeral delivery reduces accidental exposure but is not a substitute for authorization or a
retention policy: Discord and user clients remain external systems.

An aggregate slash request is first acknowledged privately and only its explicitly classified
`PUBLIC_AGGREGATE` result is sent as a public follow-up. In `channel` mode, recognized operational
messages enter the same coordinator as `/bh`; sensitive results are blocked unless the explicit
private-channel flag above is enabled. General-HR messages continue to use the stateless redacted
responder, while unrelated chat is ignored.

PDF, DOCX, XLSX and TXT artifacts are built in memory and are not written in clear text to the bot
filesystem. Once attached to Discord, however, message and file retention are governed by Discord
and the server/channel policy; regeneration is required if delivery fails after completion.

## Retention

The deployment owner must document concrete retention periods. Technical defaults/controls are:

| Record | Technical behavior | Required operator decision |
| --- | --- | --- |
| Pending approval | actionable for `PENDING_ACTION_TTL_MINUTES` (default 10) | retention of terminal metadata |
| Attachment bytes | expires after `UPLOAD_RETENTION_HOURS` (default 24) | scheduled purge and exception process |
| Generated export | in-memory until Discord delivery; no local clear-text retention | Discord message/file retention and deletion policy |
| Playwright traces | vietati in non-mock; solo fixture sintetiche in ambiente mock | cancellazione alla scadenza |
| Browser session | encrypted until expiry/invalidation | invalidate after personnel/credential changes |
| Application/security logs | structured and redacted | rotation and deletion interval |
| Audit chain | integrity/accountability record | statutory/HR retention and protected archival |
| Model usage | local lifecycle metadata and exact provider counters; no prompt or HR data | retention, billing reconciliation limits and database reset policy |
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

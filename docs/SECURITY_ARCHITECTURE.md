# Security architecture

## Purpose and scope

BH-DiC is a single-node assistant for authorized employee workflows in the Dipendenti in Cloud
employee area. It is not a general-purpose browser agent. The security objectives are:

- deny access outside the configured Discord guild, channel and DIC tenant;
- prevent model-provider output from becoming an authorization or execution decision;
- keep reads usable while every write remains disabled by policy;
- require a preview, confirmation and the configured approvals before a deterministic write;
- minimize, redact and encrypt HR data at every persistence and transmission boundary;
- produce a locally verifiable audit trail and fail closed on an uncertain write result.

Accounting, unrelated administration pages, arbitrary URLs, shell commands, arbitrary HTTP,
JavaScript execution and document processing by any model provider are outside the trust boundary.

## Security data flow

```text
Discord event
  -> guild/channel allowlist
  -> logical-role mapping and rate limit
  -> input normalization
  -> optional model intent classification (storage disabled)
  -> strict Function ID validation
  -> policy engine
  -> read OR encrypted pending action and redacted preview
  -> confirmation / independent approvals
  -> idempotency claim and state-fingerprint check
  -> deterministic DIC adapter / Playwright page object
  -> postcondition verification
  -> output redaction
  -> Discord response and HMAC-chained audit event
```

Authorization is checked before any provider call. Discord permissions are an outer gate, not a
replacement for application policy.

## Trust boundaries

| Boundary | Untrusted input | Enforced controls |
| --- | --- | --- |
| Discord | messages, attachments, role claims, webhook/bot events | guild/channel allowlist, logical RBAC, DM denial, rate limiting, normalization |
| Model provider | model output and tool arguments | provider allowlist, strict schema, closed Function ID catalog, dynamic tool exposure, local policy recheck |
| DIC website | text, DOM, dialogs, redirects | route allowlist, Page Objects, target/state verification, UI-drift errors |
| Browser session | cookies and storage state | Fernet vault, `0600` file, `0700` directory, explicit invalidation |
| Local database | workflow state and audit metadata | encrypted pending parameters, minimal schema, SQLite WAL, CAS versioning |
| Filesystem | attachment names and content | UUID paths, containment checks, `0600`, MIME detection, ClamAV, retention |
| Logs/SIEM | exception and contextual data | structured JSON, recursive secret/PII redaction, target pseudonyms |

## Function catalog and policy

`bh_dic.policies.catalog` is the authoritative catalog for every Function ID. A `FunctionSpec`
defines its action class, sensitivity, feature flags, role expression, target requirement,
confirmation policy, approver count, required capabilities and model visibility. Unknown IDs are
denied.

The policy engine checks, in order:

1. actor, guild and channel context;
2. expected DIC tenant;
3. healthy system state;
4. whether a function may be exposed to the model;
5. global and function-specific feature flags;
6. runtime capabilities such as ClamAV;
7. logical roles and optional entitlements;
8. stable, unambiguous Employee ID where required.

Role inheritance is deliberately narrow: `HR_WRITE` implies `HR_READ`, and `HR_READ` implies
`READ_ONLY`. Administrative roles do not automatically inherit HR or document access.

## Write kill switch and feature flags

`ENABLE_WRITE_ACTIONS=false` is the global kill switch. Every function-specific write flag is an
additional requirement, never an alternative. Effective runtime flags use:

```text
validated environment baseline AND runtime override
```

A runtime override can disable an enabled baseline immediately; it cannot enable a feature that
the validated environment disabled. The write gate is checked during preparation, confirmation,
approval and immediately before deterministic execution. Changing the switch does not disable
read-only reconciliation of an already uncertain action.

Critical write configuration is rejected if two-person approval is disabled. Document upload is
rejected if antivirus is not required. Live-write tests additionally require all three explicit
test guards and must never target normal production employee data.

## Approval and execution boundary

Pending parameters are canonical JSON encrypted with Fernet before persistence. The stored
confirmation value is an HMAC-SHA256 digest with a per-action salt; the plaintext code is returned
only once. Codes expire with the pending action and cannot be replayed.

The workflow enforces:

- redacted before/after preview;
- requester-only code confirmation;
- requester/approver separation;
- distinct approvers, with no duplicate counting;
- motivation and target-bearing text confirmation for critical actions;
- exact `DELETE <EMPLOYEE_ID>` confirmation for employee deletion;
- optimistic database CAS and an execution idempotency claim;
- re-read state fingerprint before execution;
- no automatic retry of a mutating operation.

Loss of connection, timeout or missing postcondition produces
`UNKNOWN_REQUIRES_RECONCILIATION`. Only a deterministic read-back may reconcile that state.

## Model-provider isolation

The selected OpenAI, Groq or llama provider receives only tools compatible with the already
evaluated user context. Read-only users do not receive write schemas, and high-risk destructive
functions are hidden from model exposure.
There are no browser, URL, HTTP, JavaScript, filesystem, shell or direct execution tools.

`MODEL_STORE=false` forbids application-requested persistence. OpenAI/Groq requests apply the
supported storage control; the llama chat-compatible request omits unsupported storage and
conversation-state parameters. Full documents, payrolls, credentials, cookies, storage state,
IBAN and full tax identifiers are forbidden. `MODEL_RESULT_RENDERING=deterministic` keeps DIC
results local and renders Discord output through Python templates.

## Persona isolation

The bot persona is a closed language profile, not a free-form system prompt. Language, tone,
address style, verbosity and emoji mode are enums. Display name, opening and closing are bounded,
normalized and rejected if they contain mentions, URLs, control characters, token-like material
or instruction-like text. Those three decorations remain local and are never sent to the model.
The profile can style an optional clarification and successful Discord presentation; it cannot
add tools, translate deterministic HR data, change a schema or weaken RBAC, confirmations or
security wording. Safe summaries expose only whether each decoration is configured, not its value.

## Browser controls

The Playwright adapter is asynchronous and deterministic. Page Objects validate an expected route
and a distinguishing page element. Selectors prefer stable test IDs, accessible roles and labels.
The browser runtime serializes operations by default, uses a global write lock and per-employee
locks, retries only idempotent reads and opens a circuit after repeated drift/errors.

MFA and CAPTCHA are not bypassed. They stop the operation with
`AUTHENTICATION_INTERACTIVE_REQUIRED`. TLS verification remains enabled.

## File controls

Discord attachment content enters `var/uploads/quarantine` under a generated UUID. The original
filename is protected metadata and is never a path. The pipeline applies size limits, content MIME
detection, extension consistency, SHA-256 duplicate detection and antivirus scanning before moving
content to `clean`. ClamAV failure is fail-closed when required. No document is sent to a model
provider or returned as a Discord attachment. See `FILE_HANDLING.md`.

## Cryptographic separation

Use independent secrets for independent purposes:

- `AUDIT_HMAC_KEY`: audit-chain integrity;
- `ENCRYPTION_KEY`: pending-action JSON payloads;
- `DIC_SESSION_ENCRYPTION_KEY`: browser session vault;
- a separate pseudonymization key when stable target pseudonyms are emitted.

Keys must contain at least 32 bytes, live only in the protected `.env` or an approved secret store,
and must never be committed, printed or reused as Discord/model-provider/DIC credentials.

## Logging, audit and monitoring

JSONL logging performs recursive redaction and never writes binary content. Target Employee IDs
are pseudonymized before logging. Audit payload schemas reject sensitive key names before an event
can be appended. The audit database chain uses canonical JSON and HMAC-SHA256; chain state detects
tail truncation. Wazuh examples monitor security-relevant JSON events without ingesting `.env` or
file content.

## Fail-closed conditions

Writes are denied on any of the following:

- incomplete or insecure runtime configuration;
- unauthorized guild, channel, tenant, role or entitlement;
- disabled global/specific flag;
- ambiguous/missing target;
- expired or replayed confirmation;
- insufficient, duplicate or self-approval;
- changed state fingerprint or UI drift;
- missing ClamAV for an enabled upload;
- missing audit prerequisite or idempotency conflict;
- inability to verify the final postcondition.

## Deployment invariants

- `.env` and encrypted browser state are never committed and use `0600` permissions.
- Data/session/upload directories use `0700`.
- The bot runs as a dedicated unprivileged user.
- The provided systemd unit is an example only until an administrator installs it.
- The installation process leaves the bot stopped and no Chromium process resident.
- GitHub visibility is intentionally `PUBLIC`; the tracked tree contains only source, examples and
  synthetic fixtures, while runtime secrets, operational identifiers, state and PII stay outside Git.

## Expected operational verification

Deployment must validate the project wrappers before relying on them:

```bash
./scripts/doctor.sh
./scripts/security-check.sh
./scripts/audit-verify.sh
./scripts/files.sh list
./scripts/files.sh purge-expired
./scripts/status.sh
```

These interfaces must not print secrets, file content or complete protected paths. Their mere
presence is not evidence of success; retain command exit status and redacted results in the final
implementation report.

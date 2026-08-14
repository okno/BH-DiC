# Audit chain

## Purpose

BH-DiC records redacted accountability metadata in an append-only logical chain. The chain detects
row modification, deletion, insertion, reordering and tail truncation when verified against
`audit_chain_state`. It does not store secrets or full HR payloads and is not a public digital
signature.

## Stored fields

Each `audit_events` row contains:

- contiguous sequence and UUID event ID;
- UTC timestamp and controlled event type;
- correlation ID;
- optional Discord actor, guild, channel and Function ID;
- optional pseudonymized employee target;
- outcome and validated redacted JSON payload;
- `previous_hash` and `event_hash`.

The singleton `audit_chain_state` stores the last sequence/hash. The payload schema rejects keys
that resemble passwords, tokens, secrets, authorization/cookies, TOTP, IBAN, tax code, email,
phone, address, names, birth data, document/file content, storage state or a full prompt.

## Hash construction

The genesis value is 64 zeroes. For event `n`:

```text
material_n = canonical_json(
  sequence, previous_hash, event_id, timestamp, event_type,
  correlation/context metadata, outcome, redacted payload
)

event_hash_n = HMAC-SHA256(AUDIT_HMAC_KEY, material_n)
previous_hash_(n+1) = event_hash_n
```

Canonical JSON uses UTF-8, sorted keys, compact separators and rejects non-finite numbers. Hash
comparison uses constant-time comparison.

## Transaction and concurrency model

`AuditService.append` holds a process-local async lock and updates the event plus chain-state row in
one database transaction. This is the supported single-node writer model. PostgreSQL additionally
uses `FOR UPDATE`; SQLite uses one application writer with WAL/busy-timeout settings.

If append fails, the calling mutating workflow must fail closed. Do not perform an auditable write
and then ignore an audit failure.

## Verification

The verifier checks:

1. the first event references the genesis hash;
2. sequences are contiguous from 1;
3. every `previous_hash` equals the prior event hash;
4. every event HMAC matches canonical material;
5. the final sequence and hash equal `audit_chain_state`.

Operational interface expected by the project:

```bash
./scripts/audit-verify.sh
```

A successful result must report validity, event count and last sequence without exposing payload
content. A non-zero exit is required for any mismatch or missing chain state. Until that wrapper is
installed, the same verification is available through `AuditService.verify_or_raise`.

Recommended schedule:

- after installation/migration and before enabling writes;
- at least daily through a local scheduler;
- before and after backup/restore;
- after an abnormal shutdown, database error or uncertain write;
- before relying on audit evidence during an incident.

## Event content guidance

Audit lifecycle events should cover request accepted/denied, policy decision, pending action,
confirmation, each approval/rejection, execution claim, deterministic result, postcondition,
reconciliation, file state transition, authentication/session event and security-control failure.

Good payload:

```json
{"approval_count":2,"required":2,"state":"APPROVED"}
```

Forbidden payload:

```json
{"email":"person@example.invalid","document_content":"..."}
```

Use a correlation ID to join audit and operational logs. Do not place a plaintext confirmation
code, original document content or raw employee identifier into the event.

## Failure handling

On `audit.integrity_failed`:

1. disable writes immediately;
2. do not repair, resequence or delete rows in place;
3. preserve database, WAL and relevant redacted logs read-only;
4. record the first failing sequence/reason outside the affected chain;
5. compare protected backups and host/Wazuh events;
6. investigate key exposure and unauthorized database access;
7. restore only through the approved recovery procedure;
8. re-run full verification before service restart.

An integrity failure is a security incident, not an ordinary database maintenance task.

## Backup and restore

Use a SQLite-consistent backup mechanism or stop the writer before copying the database; never copy
only the main SQLite file while ignoring active WAL state. Back up the audit database and chain
state together. After restore, verify before allowing any operation.

The audit key must not be placed in the same unprotected archive as the database. Backups should
not include `.env`, browser session or uploaded files unless explicitly selected and encrypted.

## Key management and limitation

`AUDIT_HMAC_KEY` must contain at least 32 bytes, differ from payload/session encryption keys and
remain outside Git and logs. Anyone holding the key can generate valid HMACs, so the chain provides
tamper evidence under key protection, not third-party non-repudiation.

The current chain schema has no per-event key version. Do not replace the key in place and then
expect historical verification to work. A future rotation procedure must close/verify/archive the
old chain with its protected key and begin a documented new chain or add explicit key-version
support.

## Wazuh integration

The structured logger emits:

- `audit.event_appended`;
- `audit.chain_verified`;
- `audit.integrity_failed`;
- `audit.append_failed`.

The supplied Wazuh rules assign high severity to integrity and append failure. Wazuh alerts
supplement, but do not replace, cryptographic chain verification.

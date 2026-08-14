# Secure file handling

## Security contract

Attachment handling is disabled by default. When enabled, an attachment is untrusted until all
quarantine checks complete. It must never be sent to OpenAI, rendered in Discord, automatically
opened, executed or used as a shell/URL argument.

Relevant controls:

```dotenv
ENABLE_WRITE_ACTIONS=false
ENABLE_DOCUMENT_UPLOAD=false
UPLOAD_MAX_MB=20
UPLOAD_RETENTION_HOURS=24
UPLOAD_ALLOWED_MIME_TYPES=application/pdf,image/jpeg,image/png
CLAMAV_REQUIRED=true
CLAMAV_SOCKET=
```

`EMP-DOC-002` additionally requires the `DOCUMENT_OPERATOR` role, the global and specific flags,
an available `clamav` capability, a redacted preview and confirmation.

## Directory layout and permissions

```text
var/uploads/
├── quarantine/   newly streamed, never trusted
├── clean/        validated and antivirus-clean
├── processed/    handed to an authorized deterministic workflow
└── rejected/     rejected content awaiting bounded deletion
```

Directories use `0700`; files use `0600`. Each stored path is a generated 32-character UUID hex
value with no user extension. The normalized original filename is protected metadata only. Path
construction rejects absolute paths, separators, drive names, null bytes, `.` and `..`, and checks
that the resolved file remains in its expected bucket.

## Ingestion pipeline

1. Generate an opaque upload ID.
2. Validate/normalize filename metadata without using it as a path.
3. Stream to `quarantine` with exclusive creation and a byte limit.
4. Compute SHA-256 while streaming.
5. Detect MIME from content (`python-magic`, with explicit PDF/JPEG/PNG signatures as fallback).
6. Compare detected MIME with the declared MIME and filename extension.
7. Reject unsupported MIME and duplicate clean/processed SHA-256.
8. Scan with ClamAV using the `INSTREAM` protocol; no shell is invoked.
9. Fail closed on infected, unavailable, errored or unrecognized antivirus result when required.
10. Atomically move accepted content to `clean` and record metadata/state.
11. Move content to `processed` only for an approved DIC upload workflow.
12. Delete expired bytes and record the deletion metadata event.

Normal states are:

```text
QUARANTINED -> CLEAN -> PROCESSED -> DELETED
            \-> REJECTED -> DELETED
```

No rejected or uncertain file may be uploaded to DIC.

## MIME and extension policy

Default accepted pairs are:

| Detected MIME | Allowed extension |
| --- | --- |
| `application/pdf` | `.pdf` |
| `image/jpeg` | `.jpg`, `.jpeg` |
| `image/png` | `.png` |

The Discord-declared content type is not trusted. An extension match alone is never sufficient.
Adding a file type requires a threat-model update, deterministic content detection, ClamAV testing
and explicit configuration review. Macro-enabled office files and executables are not accepted by
default.

## ClamAV behavior

`CLAMAV_SOCKET` may identify a local Unix socket or configured host/port endpoint. The scanner sends
the quarantined bytes through ClamAV `INSTREAM`. It does not call a shell or expose the original
filename.

When `CLAMAV_REQUIRED=true`, these all reject the file:

- endpoint missing/unreachable;
- timeout or protocol error;
- unrecognized scanner response;
- malware result.

Do not bypass the scanner to restore service. Disable upload while investigating and leave reads
available.

## Metadata and disclosure

Metadata may include opaque upload ID, normalized original name, claimed/detected MIME, byte size,
SHA-256, antivirus state, lifecycle state and timestamps. Do not log the original name, full local
path or file content. Security events use the opaque ID and rejection reason.

Discord receives status and opaque ID only. A local export/download, if separately enabled and
approved, remains in the protected area and is never attached back to Discord.

## Retention and deletion

`UPLOAD_RETENTION_HOURS` sets the byte-retention deadline. A scheduled operational job must invoke
purge frequently enough to meet it. Deletion updates metadata to `DELETED`, clears the bucket
reference and emits `FILE_RETENTION_DELETED`.

Retention is not secure erasure of SSD snapshots/backups. Do not include uploads in backups by
default. If an incident hold is legally required, authorize it explicitly, isolate the item and
record the exception without extending all files.

## Operator commands

The project requires the following safe wrapper interface:

```bash
./scripts/files.sh list
./scripts/files.sh metadata <UPLOAD_ID>
./scripts/files.sh scan <UPLOAD_ID>
./scripts/files.sh purge-expired
```

The wrappers are operational interfaces to be validated during deployment. They must use absolute
resolved paths, refuse invalid opaque IDs, avoid file content output and never print a complete
local path in a Discord response. `metadata` must not open or print the document. `scan` must not
move a rejected file directly to `clean` without running the full policy pipeline.

## Incident handling

For malware, MIME mismatch or path traversal:

1. do not open the file;
2. keep upload disabled if scanner integrity is uncertain;
3. record opaque ID, hash, timestamps and redacted actor/correlation metadata;
4. inspect Wazuh/security events;
5. purge according to incident/retention policy;
6. rotate credentials only if there is evidence of execution/session compromise.

Do not upload a suspicious sample to a third-party service without controller authorization.

## Verification checklist

- [ ] `ENABLE_DOCUMENT_UPLOAD=false` in the delivered configuration.
- [ ] Upload cannot start while `ENABLE_WRITE_ACTIONS=false`.
- [ ] Quarantine directories and files have restrictive permissions.
- [ ] Size, traversal, MIME mismatch, extension mismatch and dedup tests pass.
- [ ] Clean, infected, unavailable and error antivirus paths are tested.
- [ ] Expired byte deletion and metadata event are verified.
- [ ] No attachment bytes or original filenames appear in OpenAI/log payloads.
- [ ] No file is posted back to Discord.

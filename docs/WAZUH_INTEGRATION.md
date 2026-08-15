# Wazuh integration

## Goal

BH-DiC emits one compact JSON object per line. Wazuh can collect those events and raise alerts for
authorization failures, approval abuse, authentication problems, UI drift, malicious files,
forbidden data requests, destructive attempts, audit failure and uncertain writes.

Wazuh monitoring does not authorize actions and does not replace the HMAC audit verifier.

## Log source

`var/log/app.jsonl` contains all application/component events and is the recommended single Wazuh
source. Component files (`discord.jsonl`, `openai.jsonl`, `browser.jsonl`, `audit.jsonl`,
`security.jsonl`) duplicate subsets of the application stream. Do not ingest `app.jsonl` and every
component file together unless duplicate alerts are intentionally deduplicated downstream.

Useful JSON fields include:

- `timestamp_utc`, `timestamp_local`, `level`, `logger`, `event_type`;
- `correlation_id`, `discord_user_id`, `guild_id`, `channel_id`, `function_id`;
- pseudonymized `target_employee_id`, `outcome`, `duration_ms`, `error_code`;
- recursively redacted `details` and exception text.

Tokens, passwords, cookies, API keys, TOTP, storage state, file content, IBAN and tax identifiers
must never be collected. Do not configure Wazuh to ingest `.env`, the session vault or upload
directories.

## Supplied examples

- `infrastructure/wazuh/ossec_config_fragment.xml.example` collects the default application log.
- `infrastructure/wazuh/local_rules.xml.example` provides local rule IDs `110500`–`110515`.

They are examples: adapt the installation path and validate syntax against the installed Wazuh
version before restart. Codex does not install or restart the Wazuh agent.

## Installation procedure

On the monitored host, as an authorized administrator:

1. Confirm the real project/log path and that the Wazuh agent can read the JSONL file without
   granting access to `.env`, sessions or uploads. Prefer a narrow group/ACL over world-readable
   permissions.
2. Merge the `<localfile>` block into the agent's `ossec.conf`.
3. Copy/merge the local rules into the manager's local rules file according to local Wazuh policy.
4. Validate XML and run a synthetic event through `wazuh-logtest`.
5. Restart only the relevant Wazuh manager/agent service through the site's change procedure.
6. Confirm one synthetic alert and verify that its payload contains no secret/PII.

Candidate validation commands (paths/service names vary by installation):

```bash
xmllint --noout infrastructure/wazuh/local_rules.xml.example
xmllint --noout infrastructure/wazuh/ossec_config_fragment.xml.example
sudo /var/ossec/bin/wazuh-logtest
sudo systemctl restart wazuh-agent
```

Do not run the restart command until configuration validation and change approval are complete.

## Synthetic logtest input

This event contains no real Discord or employee identifier:

```json
{"timestamp_utc":"2026-08-15T00:00:00Z","timestamp_local":"2026-08-15T02:00:00+02:00","level":"WARNING","logger":"bh_dic.security","event_type":"security.guild_denied","message":"Request denied","correlation_id":"test-correlation-0001","outcome":"DENIED","application_version":"0.2.0"}
```

Expected result: base rule `110500` and authorization-denial child `110501` match. The exact decoder
display depends on Wazuh version.

## Alert mapping

| Rule | Level | Event class | Operator action |
| --- | ---: | --- | --- |
| 110501 | 8 | guild/channel/role denial | check actor/channel and repeated attempts |
| 110502 | 7 | disabled function/flag attempt | verify no unauthorized config change |
| 110503 | 10 | self/duplicate/unauthorized approval | reject action and investigate account |
| 110504 | 7 | MFA/CAPTCHA/session expiry | follow manual auth procedure; do not bypass |
| 110505 | 8 | DIC login failure | verify credentials/session without logging them |
| 110506 | 12 | repeated login failures | disable automation and investigate compromise |
| 110507 | 9 | UI drift/selector failure | keep affected actions disabled; update fixture |
| 110508 | 14 | malware detected | isolate opaque upload ID; do not open file |
| 110509 | 10 | MIME mismatch/path traversal | preserve metadata, purge by policy |
| 110510 | 10 | prompt injection/forbidden data | review redacted request and role |
| 110511 | 12 | destructive/delete attempt | verify flag, requester and A2 workflow |
| 110512 | 15 | audit append/integrity failure | activate kill switch and incident procedure |
| 110513 | 13 | uncertain write | prohibit retry; perform read-only reconciliation |
| 110514 | 13 | tenant mismatch | stop affected workflow and invalidate session |
| 110515 | 5 | rate limit | investigate burst; tune only with evidence |

## Event naming contract

Rules accept controlled dot/underscore event names and deliberately use bounded regular
expressions for equivalent component wording. Producers should prefer these stable forms:

```text
security.guild_denied
security.channel_denied
security.role_denied
security.feature_disabled
approval.self_approval_denied
approval.duplicate_approver_denied
authentication.login_failed
authentication.interactive_required
browser.ui_drift
file.antivirus_infected
file.mime_mismatch
file.path_traversal
openai.prompt_injection_suspected
action.delete_attempted
action.unknown_requires_reconciliation
security.tenant_mismatch
audit.append_failed
audit.integrity_failed
```

When code uses another controlled event name, update and test rules; never put raw user input in
`event_type`.

## Response priorities

For rules 110508, 110512, 110513 or 110514:

1. set the runtime write kill switch false;
2. preserve redacted logs and correlation IDs;
3. verify the audit chain;
4. avoid restarting/retrying the affected write;
5. isolate file/session/database artifacts with restrictive permissions;
6. follow `THREAT_MODEL.md`, `AUDIT.md` and the incident/privacy procedure.

## Tuning and retention

- Tune frequency thresholds only after observing synthetic/non-sensitive traffic.
- Route high-severity alerts to an authorized security group, not a public Discord channel.
- Align Wazuh index retention with the documented log/privacy retention period.
- Wazuh archives/backups are additional copies of personal metadata and require access controls.
- Monitor collection gaps and disk pressure; absence of alerts does not prove safe operation.

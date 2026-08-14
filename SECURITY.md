# Security policy

BH-DiC processes highly sensitive HR workflows. Do not disclose suspected vulnerabilities, secrets,
or employee data in public issues, chat transcripts, logs, screenshots, or test fixtures.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub's private vulnerability reporting or Security
Advisories for this repository. Include the affected version, a minimal redacted reproduction,
impact, and suggested mitigation. Do not include live credentials, real employee identifiers, or HR
documents. If private reporting is unavailable, contact the repository owner through an established
private channel before sharing technical details.

## Supported versions

Until the first stable release, only the latest commit on the protected default branch receives
security fixes.

## Operational response

For suspected compromise:

1. Set `ENABLE_WRITE_ACTIONS=false` and stop the bot.
2. Revoke Discord, OpenAI, and Dipendenti in Cloud credentials and invalidate browser sessions.
3. Preserve database and audit files read-only; do not rewrite the audit chain.
4. Review security logs using correlation IDs and verify the complete audit chain.
5. Restore only from a verified backup, rotate the HMAC and encryption keys under a documented
   key-transition procedure, and re-enable reads before considering writes.

Never paste secrets into commands, commit history, tickets, or diagnostic output.

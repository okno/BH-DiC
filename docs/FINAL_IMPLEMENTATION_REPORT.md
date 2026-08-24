# Final implementation report

Baseline revision: `98b03932ca1bf548bff44a82a1fedd976e5603d0` on `main`.
Discovery and live-read verification: 2026-08-24. Last production revision covered by the complete
read gate before this report: `ec3359403a27c61226fe9193c2c76e58fb818881`.

## Outcome

BH-DiC now separates three boundaries that must not be conflated:

1. Discord authenticates guild, channel, current member and role IDs.
2. A deterministic local planner resolves HR intent, employee candidates, periods and ordered
   operations. Names, Employee IDs and DiC results are not sent to the model provider.
3. The Playwright adapter navigates the normal DiC UI and captures only allowlisted first-party
   responses with strict origin, route, query, schema, pagination and size checks.

The HR role configured in production can use slash commands and the private management channel.
Sensitive results are delivered only under the configured private-delivery policy. General HR chat
uses a separate, minimized model path and cannot invoke DiC writes or read personal data.

## Implemented read paths

The adapter supports complete employee listing/counting, local search/filter/sort, employee
summary, contracts and expirations, roles, timestamp access, maturations, monthly balances,
individual payroll metadata/net/PDF availability, collective payroll presence by month and
document metadata. Bounded result sets larger than a Discord embed include a complete in-memory
TSV attachment instead of silently truncating the answer.

The natural-language corpus contains 120 distinct Italian requests. Every corpus request resolves
to a typed local plan, including common phrases such as:

- total employee count;
- contracts expiring over a relative period;
- documents, roles, timestamps, balances and maturations for a named employee;
- individual net pay for a month;
- employees having payroll in a month;
- the compound comparison “contracts in the next 90 days without July payroll”.

Ambiguous names produce a bounded candidate choice and support a local ordinal follow-up. Missing
targets produce a clarification. The conversation context is local, TTL-bounded and intentionally
does not survive a restart.

## DiC contract and completeness

All captured JSON is parsed with duplicate-key and non-finite-number rejection. Each resource has
an independent circuit breaker. Pagination enforces the exact UI-provided origin/path/query,
maximum pages/records/bytes, stable totals and ID deduplication. Unknown structural drift fails
closed; additive fields are accepted only on explicitly forward-compatible contracts.

The authorized production gate returned `success=true`, `failed=[]` and verified:

- 56/56 employees across all pages;
- employee summary, roles and timestamp target;
- contracts, maturations, balances, payroll and documents;
- tenant identity and authenticated session.

For contract, balance and document surfaces, a separate read-only schema probe also exercised a
non-empty response. The machine report contains only sanitized states and counts. It contains no
names, Employee IDs, payroll values, document categories or signed URLs.

The exact collective July payroll request also completed on the final application revision: it
scanned the complete 56-employee listing and returned a bounded match set. Only aggregate counts
were printed during verification; identities and payroll values remained in process memory.

Workplaces and schedule-model assignments are available through the employee projection and their
lookup endpoints were discovered. Dedicated expense/travel and complete shift/timesheet datasets
were not exposed by the authorized tenant UI, so the bot does not invent those capabilities.

## Discord and session behavior

At startup the bot reconnects Discord, reports status in the configured private HR channel and sends
“BOT HR Bitcoin Hotel Online!”. The outbound startup event was observed with `dic_available=true`.
If the encrypted DiC session is unavailable, Discord remains online in degraded mode and an
authorized HR operator can run `/bh dic reconnect`; CAPTCHA/MFA remains a human, fail-closed step.

DM authorization is not inferred from a payload: the bot fetches the current guild member and
re-evaluates configured role IDs. Channel and slash traffic have separate rate limits, plus a
global channel budget and concurrency limit. DMs outside the configured guild context are denied.

## Security and write policy

No live write was executed. Create/update/status/document and other write functions retain feature
flags, RBAC, preview, single-use confirmation, approval requirements, idempotency, kill switches
and reconciliation. Unsupported live mutations fail closed. Enabling a flag is not proof that a
write is safe or verified.

Logs and audit records do not contain prompts, names, employee identifiers, payroll values,
credentials or signed document URLs. Model telemetry records sanitized purpose, state and declared
usage. Secrets remain in the protected production environment/vault and were not committed.

## Verification and deployment

The implementation is checked with Ruff, mypy, pytest, coverage, Bandit, pip-audit and gitleaks.
The production Python 3.12 environment additionally exercises Chromium and the read-only live gate.
Deployment is an exact Git fast-forward with the bot stopped; only `bh-dic.service` is restarted.
The final commit and post-deploy test counts are recorded in the delivery message and repository
history because a document cannot truthfully embed its own resulting commit SHA.

## Remaining limitations

- The complete collective payroll traversal is slower than the startup gate because it visits the
  payroll route for each employee; it is bounded to 500 employees.
- A signed payroll PDF is temporary and is never sent to the model provider or stored by the bot.
- UI or first-party contract drift can temporarily disable one resource circuit until reviewed.
- No destructive/write path has production evidence.
- An inbound Discord round-trip must ultimately be initiated by a real authorized user; automated
  tests and the verified outbound startup event do not impersonate a user account.
- Restore drill and sustained-load behavior remain operational exercises, not inferred guarantees.

See `DIC_LIVE_READ_COVERAGE.md`, `FEATURE_MATRIX.md`, `KNOWN_LIMITATIONS.md` and
`SECURITY_ARCHITECTURE.md` for detailed evidence and boundaries.

# Architecture

The runtime is a closed pipeline:

```text
Discord gate → local conversation context → minimization → HRQueryPlan/router
→ local policy and field entitlements → deterministic DIC adapter
→ completeness checks → local presenter → private/public delivery → audit
```

The model can classify only catalogued semantic operations. It cannot select routes, selectors,
roles, tenant, approval state or browser actions and never receives DIC results or identities.
`DIC_ROUTES` is the navigation allowlist. Page objects navigate the UI and may consume only
first-party responses emitted by that UI after exact origin/path/query/schema checks. Employee
list, contracts, maturations, payroll and documents have typed network projections. Each semantic
browser operation owns an independent circuit breaker.

`HRQueryPlan` is currently executable for single read functions and the verified compound
contract-expiry/payroll-presence join. Unsupported plan shapes fail closed. Write flows continue
through the existing preview, confirmation, approval, idempotency and reconciliation state machine.

Evidence and status are kept separate: discovered, implemented, contract-tested and live-verified
are not synonyms. See [DIC live coverage](DIC_LIVE_READ_COVERAGE.md).

# DiC live read coverage

Last discovery: 2026-08-24. Production source commit during discovery:
`98b03932ca1bf548bff44a82a1fedd976e5603d0`.

| Surface | Route | Network shape | Parser/adapter | Completeness | State |
| --- | --- | --- | --- | --- | --- |
| Employee list | Verified | Verified | Verified | Complete paginator verified by implementation/tests | `LIVE_READ_VERIFIED` |
| Employee summary | Verified | Verified | Verified | Single resource | `LIVE_READ_VERIFIED` |
| Roles/groups | Verified | Employee detail observed | Verified | Single resource | `LIVE_READ_VERIFIED` |
| Timestamp access | Verified | Verified paginated endpoint | Verified | Single target lookup; full paginator pending | `LIVE_READ_VERIFIED` for target lookup |
| Contracts | Verified | Verified paginated endpoint | DOM parser drift | Not proven | `DEGRADED_SCHEMA` |
| Maturations/history | Verified | Verified paginated endpoint | DOM parser drift | Not proven | `DEGRADED_SCHEMA` |
| Balances/counters | Verified | Counter/correction/balance endpoints observed | DOM parser drift | Not proven | `DEGRADED_SCHEMA` |
| Payroll metadata/net/PDF metadata | Verified in isolated probe | Verified paginated endpoint | Verified | Per-employee page currently returned completely for observed period; general pagination contract pending | `LIVE_READ_VERIFIED` |
| Documents/categories | Verified | Verified paginated endpoints | DOM parser drift | Not proven | `DEGRADED_SCHEMA` |
| Workplaces | No dedicated route verified | Verified paginated endpoint | No dedicated service | Not proven | `NEEDS_VALIDATION` |
| Work schedules/templates | No dedicated route verified | Endpoint observed | No dedicated service | Not proven | `NEEDS_VALIDATION` |
| Expenses/travel | Field-level permissions observed | Dedicated data route not verified | No dedicated service | Not proven | `NEEDS_VALIDATION` |
| Shifts/timesheets | Field-level permissions observed | Dedicated data route not verified | No dedicated service | Not proven | `NEEDS_VALIDATION` |

## Evidence summary

- One comprehensive probe observed 10 page snapshots and 32 distinct sanitized
  first-party XHR/fetch contracts.
- Isolated payroll and document probes were required because the current shared
  browser circuit opened after unrelated extractor failures.
- Payroll passed in isolation. Documents reached the route and returned document
  and category JSON but the current DOM extractor failed.
- Automated scans of the three structural artifacts found zero email, IBAN, tax
  code and raw numeric-path patterns. Artifacts remain outside Git with mode 0600.
- The final bot restart emitted `discord_ready` and a startup notice with
  `dic_available=true`.

## Coverage gate

Milestone 1 does not satisfy full production coverage. A surface becomes
`LIVE_READ_VERIFIED` only when route fingerprint, required controls, first-party
schema, parser, pagination/completeness, authorization projection, fixtures and a
bounded live probe all pass for the same implementation commit. Unknown and
degraded rows above block any claim of 100% coverage.

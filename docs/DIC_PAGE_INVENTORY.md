# DiC page inventory

Observation window: 2026-08-24 19:57–20:06 UTC. Source commit:
`98b03932ca1bf548bff44a82a1fedd976e5603d0`.

| Resource | Route template | Live result | Data source observed | Current implementation result |
| --- | --- | --- | --- | --- |
| Employee list | `/it/app/employees/list` | Verified | JSON plus DOM | Complete paginator already available |
| Employee summary/profile | `/it/app/employees/info/{employee_id}/summary` | Verified | Employee JSON plus DOM form | Read succeeds; several values intentionally redacted |
| Roles/groups/permissions | `/it/app/employees/info/{employee_id}/roles` | Verified | Employee JSON plus DOM controls | Read succeeds for exposed group/role controls |
| Timestamp access | `/it/app/settings/timestamps/employees` | Verified | Paginated JSON plus DOM | Read succeeds |
| Contracts/work schedules | `/it/app/employees/info/{employee_id}/contracts` | Route and API verified | Paginated JSON plus DOM | `DicUiChangedError`: DOM extractor no longer matches live page |
| Maturations/history | `/it/app/employees/info/{employee_id}/maturations` | Route and API verified | Paginated JSON plus DOM | `DicUiChangedError`: DOM extractor no longer matches live page |
| Balances/counters | `/it/app/employees/info/{employee_id}/counters` | Route and API verified | Counter/correction/balance JSON plus DOM | `DicUiChangedError`: DOM extractor no longer matches live page |
| Payrolls | `/it/app/employees/info/{employee_id}/payrolls` | Verified in isolated probe | Paginated JSON plus DOM | Read succeeds, including net and attachment metadata |
| Documents | `/it/app/employees/info/{employee_id}/documents/list` | Route and API verified | Paginated document/category JSON plus DOM | `DicUiChangedError`: DOM extractor no longer matches live page |

## Page fingerprints

Sanitized SHA-256 fingerprints were collected from route, sanitized title/header
classes, accessibility-role counts and control structure. Raw DOM and text were
not retained. Fingerprints are held only in the restrictive live artifact until
the route registry defines versioned fixtures and drift policy.

## Empty and error states

The probe observed HTTP-success structures and extractor drift, but did not alter
filters or tenant content to manufacture empty states. Empty-state coverage is
therefore `NEEDS_VALIDATION` for contracts, maturations, counters, payrolls and
documents. Synthetic fixtures must cover empty arrays, empty pagination and
permission-denied responses before live probes are repeated.

## Additional surfaces

First-party employee responses expose work schedules, workplace, teams, reduced
permissions, expense permissions, shifts, timesheets and timestamp configuration.
These are fields on observed employee resources, but dedicated UI route coverage
has not been proven. They must not be advertised as standalone live features yet.

# DiC employee data dictionary

Fields are derived from key/type shapes of first-party responses observed on
2026-08-24. Values were discarded. `Observed` means the field name and type were
seen, not that the bot is authorized to return it in every Discord destination.

| Resource | Observed field groups | Pagination | Current read status |
| --- | --- | --- | --- |
| Employees | IDs, name components, active/account/invited/admin flags, payroll number, tax code, birth date, email, job title, current contract/work-time/workplace, teams/roles, reduced permissions | `current_page`, `last_page`, `per_page`, `from`, `to`, `total`, links | Verified |
| Employee detail | Address, phone, IBAN, notes, provider/audit timestamps, contracts, timestamp flags, full permission matrix, expense/shift/timesheet visibility | Single `data` object | Verified with redacted projection |
| Contracts | Stable ID, validity dates, ongoing/permanent flags, hour type, part-time percentage, flexible hours, working-hour list, level, note, employee reference | Laravel-style paginator with total | Extractor degraded; response contract observed |
| Timestamp employees | Employee ID/full name, access/history flags, dated settings, modes, devices and workplaces | Paginator; observed `per_page` may be a string | Verified |
| Workplaces | ID, name, active/type, address/coordinates/tolerance, assigned count | Paginator with total | Response observed; standalone feature pending |
| Maturations | Stable ID, employee/contract/counter references, from/to month-year, monthly/yearly amount, validity/ongoing | Paginator with total | Extractor degraded; response contract observed |
| Counters/balances | Counter ID/name/color, active/auto-maturation; correction/balance endpoints with year/month selectors | Resource dependent | Extractor degraded; response contracts observed |
| Payrolls | Stable ID, employee reference, year/month/date/description, net integer, read state/time, attachment ID/filename/URL/timestamps, balance metadata | Paginator with total | Verified in isolated read-only probe |
| Documents | Stable ID, employee/creator/updater references, title/year/date/expiry, category/subcategory, request/shared/read flags, notes, attachment metadata | Paginator with total | Extractor degraded; response contract observed |
| Document categories | Stable ID, name, category/subcategory, color, editable flag | Paginator with total | Response observed |
| Pending processing | Employee/file/page/payroll/CU counters | Single `data` object | Response observed; not a document record list |

## Type and completeness rules

- IDs must remain opaque even when currently numeric.
- Nullable and non-null variants observed across records must be modeled explicitly.
- Monetary payroll `net` is an integer and requires a verified unit contract before
  presentation; the current parser treats it as cents based on existing live tests.
- Pagination is complete only when all pages are fetched, stable IDs are deduped,
  totals remain stable and final unique count equals the declared total.
- A field not observed or not authorized is `unavailable`/redacted, never inferred.

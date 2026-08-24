# DiC first-party network contracts

The live probe retained only method, sanitized path template, query-key names,
status, content type and recursive key/type shape. Request bodies, headers,
tokens, query values and response values were not retained.

## Employee resources

| Method | Sanitized path | Observed query keys | Shape |
| --- | --- | --- | --- |
| GET | `/backend_apiV2/employees` | filters, filter type, page, per-page, search fields, sort | Paginated `data` plus total and navigation metadata |
| GET | `/backend_apiV2/employees/{employee_id}` | none | Single `data` employee detail object |
| GET | `/backend_apiV2/employees/counters` | none | `data.count.active/inactive/all` |
| GET | `/backend_apiV2/employees/pending` | filter type | Pending employee/payroll preparation structures |
| GET | `/backend_apiV2/contracts` | employee ID, filter/search, page/per-page | Paginated contracts |
| GET | `/backend_apiV2/maturations` | employee ID, filters, page/per-page | Paginated maturations |
| GET | `/backend_apiV2/counters` | optional employee ID | Counter definitions |
| GET | `/backend_apiV2/corrections` | employee ID, year | Correction data wrapper |
| GET | `/backend_apiV2/attendance/balance` | employee IDs, years, months, include-pending | Balance data wrapper |
| GET | `/backend_apiV2/payrolls` | employee ID, year, filter/search, page/per-page | Paginated payrolls with attachment metadata |
| GET | `/backend_apiV2/documents` | employee IDs, filters, sort, page/per-page | Paginated documents with attachment metadata |
| GET | `/backend_apiV2/documents/categories` | none | Paginated categories |
| GET | `/backend_apiV2/documents/pending/status` | none | Processing counters |
| GET | `/backend_apiV2/timestamps/settings/employees/v2` | access, page/per-page, search, sort | Paginated timestamp settings |
| GET | `/backend_apiV2/timestamps/workplaces` | filters, per-page, search, sort | Paginated workplaces |
| GET | `/backend_apiV2/timestamps/pending/count` | employee ID | Integer data wrapper |
| GET | `/backend_apiV2/weekly_workshift/template` | per-page | Paginated schedule templates |

`backend_apiV2` is the literal first-party API namespace observed in the live UI.
Only employee identifiers and query values are redacted; endpoint names remain
versioned structural evidence.

## Automatic application traffic

Normal page bootstrap also generated first-party authentication/device and global
application requests, including POST requests. The probe did not invoke their
controls and retained no request body. These endpoints are not authorized HR
operations and must not enter the route registry merely because the SPA uses them.

## Schema policy

- Unknown additive fields are ignored and recorded as drift telemetry.
- Missing required identity/pagination fields, invalid types, contradictory totals
  and unexpected origin/path fail closed.
- JSON response values never cross to the model or diagnostic logs.
- Endpoint contracts must be associated with a resource-scoped circuit and a
  versioned sanitized fixture.

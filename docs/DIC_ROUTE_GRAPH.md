# DiC employee route graph

Live discovery was performed in read-only mode on 2026-08-24 against the
configured, tenant-attested session at source commit
`98b03932ca1bf548bff44a82a1fedd976e5603d0`. Identifiers below are templates;
no tenant or employee identifier is stored.

```text
/it/app/employees
└── /it/app/employees/list                         LIVE_READ_VERIFIED
    └── employee selected by opaque ID
        ├── /info/{employee_id}/summary            LIVE_READ_VERIFIED
        ├── /info/{employee_id}/roles              LIVE_READ_VERIFIED
        ├── /info/{employee_id}/contracts          ROUTE_VERIFIED / EXTRACTOR_DEGRADED
        ├── /info/{employee_id}/maturations        ROUTE_VERIFIED / EXTRACTOR_DEGRADED
        ├── /info/{employee_id}/counters           ROUTE_VERIFIED / EXTRACTOR_DEGRADED
        ├── /info/{employee_id}/payrolls           LIVE_READ_VERIFIED
        └── /info/{employee_id}/documents/list     ROUTE_VERIFIED / EXTRACTOR_DEGRADED

/it/app/settings/timestamps/employees              LIVE_READ_VERIFIED
```

The observed navigation also loads employee permissions, workplaces, work-time,
expense, shift and timesheet-related fields through first-party employee APIs.
No separate safe anchor route for those surfaces was observed in this probe, so
they remain `NEEDS_VALIDATION` rather than being guessed.

## Trust boundary

- Only the exact HTTPS origin `secure.dipendentincloud.it` was accepted.
- Dynamic path segments were replaced by `{id}` or `{employee_id}` before any
  structural artifact was written.
- No arbitrary route is authorized by this graph. A discovered route must enter
  the typed registry and pass tenant, fingerprint and resource-policy checks.
- Routes containing create, edit, upload, download, export, delete, invite or
  connection actions were not followed during discovery.

## Important finding

After three resource extractor failures, the existing shared `dic-browser`
circuit opened and prevented payroll and document navigation. A fresh isolated
read-only probe proved payroll healthy. This demonstrates that route health must
be isolated per resource; the shared authentication/session circuit must remain
separate.

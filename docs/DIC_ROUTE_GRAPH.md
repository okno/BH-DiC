# DiC employee route graph

This graph documents the versioned route registry. Identifiers are templates; no tenant or
employee identifier is stored. Registry state is not, by itself, evidence that a deployment has
passed a current live probe.

```text
/it/app/employees
└── /it/app/employees/list                         LIVE_READ_VERIFIED
    └── employee selected by opaque ID
        ├── /info/{employee_id}/summary            LIVE_READ_VERIFIED
        ├── /info/{employee_id}/roles              LIVE_READ_VERIFIED
        ├── /info/{employee_id}/contracts          REGISTERED_READ
        ├── /info/{employee_id}/maturations        REGISTERED_READ
        ├── /info/{employee_id}/counters           REGISTERED_READ
        ├── /info/{employee_id}/payrolls           LIVE_READ_VERIFIED
        └── /info/{employee_id}/documents/list     REGISTERED_READ

/it/app/settings/timestamps/employees              LIVE_READ_VERIFIED
```

La lettura paghe usa sempre il template tipizzato
`/it/app/employees/info/{employee_id}/payrolls`; il modello non genera URL. La risoluzione del nome
avviene prima nel tenant tramite elenco DiC, con conferma obbligatoria per omonimi o similarità
fuzzy, poi l'ID selezionato viene verificato contro il contesto opaco della stessa conversazione.

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

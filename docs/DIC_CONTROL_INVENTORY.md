# DiC control inventory

This inventory records sanitized control classes and expected semantics. It does
not contain visible values or employee labels.

| Surface | Read controls observed/expected | Mutation-capable controls present | Probe action |
| --- | --- | --- | --- |
| Employee list | Search input, active/inactive/all filter, sortable headers, pagination, employee detail links | New employee, invitation/status actions may be present | Read controls observed; mutation controls never activated |
| Summary | Profile fields, state/account switches, detail navigation | Save/edit controls | Fields inspected structurally; no input or save action performed |
| Roles | Group/role lists and switches | Role assignment controls | Read only; no switch changed |
| Timestamp settings | Paginated employee rows, access filters, workplace selectors | Access-setting controls | Read only; no switch changed |
| Contracts | Paginated table, period/type/schedule fields | Add/edit/delete contract controls | Network and control structure observed; no control activated |
| Maturations | Paginated table, counter and validity fields | Add/edit maturation controls | Network and control structure observed; no control activated |
| Counters/balances | Year/month/counter selectors | Correction controls | Network and control structure observed; no control activated |
| Payrolls | Year filter, paginated rows, attachment link | Read-state/download-like controls | Metadata read only; no download initiated |
| Documents | State/category filters, paginated rows, attachment metadata | Upload/edit/delete/share controls | Metadata request observed; no download/upload/action initiated |

## Accessibility evidence

The discovery enumerated native and ARIA control roles rather than retaining raw
accessible names. Pages exposed combinations of links, buttons, inputs, switches
and one summary textarea. Control counts varied by resource and tenant state, so
counts are fingerprint signals, not stable selectors.

## Selector policy

Required controls must prefer, in order: stable test ID, semantic role plus stable
localized label, validated href template, then a narrow structural selector.
Position, visible employee text and generated CSS classes are forbidden as
identifiers. Every mutation-capable control remains outside read probes.

# DiC page inventory

Inventario del contratto applicativo. Non contiene date di ricognizione, dati del tenant,
revisioni distribuite o risultati di un server.

| Risorsa | Route template | Sorgente prevista | Risultato implementato |
| --- | --- | --- | --- |
| Elenco dipendenti | `/it/app/employees/list` | JSON first-party e marker DOM | paginazione completa e bounded |
| Riepilogo | `/it/app/employees/info/{employee_id}/summary` | JSON dipendente e form DOM | lettura tipizzata con redazione |
| Ruoli | `/it/app/employees/info/{employee_id}/roles` | JSON dipendente e controlli DOM | gruppi/ruoli esposti dalla UI |
| Timbratura | `/it/app/settings/timestamps/employees` | JSON paginato e DOM | lettura target-bound |
| Contratti | `/it/app/employees/info/{employee_id}/contracts` | JSON paginato | contratti tipizzati e identity check |
| Maturazioni | `/it/app/employees/info/{employee_id}/maturations` | JSON paginato | maturazioni tipizzate |
| Bilanci | `/it/app/employees/info/{employee_id}/counters` | counter/balance JSON | righe mensili bounded |
| Payroll | `/it/app/employees/info/{employee_id}/payrolls` | JSON paginato | periodo, netto e metadati allegato |
| Documenti | `/it/app/employees/info/{employee_id}/documents/list` | JSON paginato | categorie e metadati tipizzati |

## Drift ed empty state

Ogni route deve essere verificata sulla revisione candidata con un gate read-only privato. Un
empty state valido è distinto da un errore, ma non prova uno schema non vuoto. Drift di origine,
route, query, MIME, schema, paginazione o controlli distintivi produce un errore fail-closed e
apre soltanto il circuit breaker della risorsa interessata.

Workplace, modello orario, team e permission flag possono essere campi delle risorse dipendente;
non diventano superfici autonome finché route, schema, policy e test dedicati non sono registrati.
Spese, viaggi, turni e fogli presenze non devono essere pubblicizzati per inferenza.

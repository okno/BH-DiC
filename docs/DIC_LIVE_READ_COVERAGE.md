# DiC live read coverage

Ultimo gate: 2026-08-24 21:49 UTC. Commit applicativo verificato in produzione:
`3d9283a8070aa3f73bd061adc3b608bb1440c1b5`.

Il gate è stato eseguito con servizio bot fermo e con opt-in esplicito; ha attestato il tenant,
usato solo GET generati dalla UI, mantenuto gli identificativi esclusivamente in memoria e
prodotto soltanto stati/conteggi sanitizzati.

| Superficie | Route/first-party source | Parser e completezza | Evidenza live | Stato |
| --- | --- | --- | --- | --- |
| Elenco dipendenti | `/it/app/employees` + elenco paginato | deduplica e totale dichiarato | 56/56 record, tutte le pagine | `LIVE_READ_VERIFIED` |
| Riepilogo/anagrafica | summary dipendente | risorsa singola tipizzata | lettura riuscita | `LIVE_READ_VERIFIED` |
| Ruoli/gruppi/permessi | roles | risorsa singola tipizzata | lettura riuscita | `LIVE_READ_VERIFIED` |
| Accesso timbratura | settings timestamps | target tenant-bound | lettura riuscita | `LIVE_READ_VERIFIED` |
| Contratti | `/backend_apiV2/contracts` | paginator, ID dipendente, totale, schema non-vuoto | empty state nel gate e schema non-vuoto verificato separatamente | `LIVE_READ_VERIFIED` |
| Maturazioni/storico | `/backend_apiV2/maturations` | paginator e riferimenti counter | empty state verificato | `LIVE_READ_VERIFIED` |
| Bilanci/contatori | counters + attendance/balance | anno/mese/counter, schema mensile, limite 1.200 righe | schema non-vuoto verificato | `LIVE_READ_VERIFIED` |
| Buste paga | `/backend_apiV2/payrolls` | metadati, netto, allegato, periodo | lettura riuscita; contratto non-vuoto verificato in discovery | `LIVE_READ_VERIFIED` |
| Documenti | `/backend_apiV2/documents` | paginator, empty/category state e metadati | empty state nel gate e schema non-vuoto verificato separatamente | `LIVE_READ_VERIFIED` |

Il report macchina del gate ha restituito `success=true`, `failed=[]` e non contiene nomi,
Employee ID, valori economici, categorie personali o URL documento.

## Superfici condizionali del tenant

| Area | Evidenza autorizzata | Classificazione |
| --- | --- | --- |
| Workplaces | endpoint lookup e campo workplace nell'elenco dipendenti | lookup scoperto; assegnazione dipendente esposta dalla proiezione elenco |
| Modelli orari | endpoint lookup e campo schedule model nell'elenco | lookup scoperto; assegnazione esposta dalla proiezione elenco |
| Spese/viaggi | solo flag di permesso nella pagina Ruoli | `NOT_AVAILABLE_IN_AUTHORIZED_TENANT` come dataset dipendente dedicato |
| Turni/fogli presenze | solo flag di accesso nella pagina Ruoli/Timbratura | `NOT_AVAILABLE_IN_AUTHORIZED_TENANT` come dataset dipendente dedicato |

“Non disponibile” qui non significa che il prodotto DIC non possa offrire il modulo: significa
che la normale UI del ruolo e tenant autorizzati non ha esposto una route dati dedicata durante la
ricognizione. I relativi flag di accesso restano leggibili.

## Limiti della prova

- Il gate dimostra le superfici e i contratti implementati, non che ogni dipendente abbia almeno
  un record in ciascuna sezione.
- La query collettiva payroll attraversa serialmente i dipendenti; il round-trip completo di
  luglio 2026 ha scansionato l'intero elenco ed è riuscito sullo SHA applicativo finale.
- Nessuna write e nessun download documento sono stati eseguiti.

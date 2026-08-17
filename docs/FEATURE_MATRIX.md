# Feature matrix

Fonte normativa: `src/bh_dic/policies/catalog.py`. Questa pagina è una proiezione documentale e
non deve essere mantenuta come secondo catalogo runtime.

## Stato verificato

- **32/32** Function ID hanno specifica policy e percorso mock sintetico testato.
- **13 read**: `IMPLEMENTED`, `TESTED_WITH_MOCK`; il conteggio aggregato bounded di
  `EMP-READ-001` e le scadenze bounded del prossimo mese di calendario di `EMP-CONTRACT-001` sono
  `LIVE_READ_VERIFIED`. Ogni altra modalità resta `NEEDS_VALIDATION`.
- **13 write**: `IMPLEMENTED`, `TESTED_WITH_MOCK`, `LIVE_WRITE_UNVERIFIED`,
  `DISABLED_BY_POLICY`; **6 write** sono `PARTIALLY_COMPLETED`, `TESTED_WITH_MOCK`,
  `LIVE_WRITE_UNVERIFIED`, `DISABLED_BY_POLICY`. Tra queste, `EMP-CREATE-001` ha un percorso live
  limitato al subset verificabile, mentre `EMP-INVITE-001`, `EMP-DOC-005`, `EMP-EXPORT-001`,
  `EMP-DOC-003` e `EMP-CONTRACT-003` hanno adapter live `NOT_AVAILABLE`. I 19 Function ID write
  sono tutti disabilitati dalla policy; i 18 gate distinti usati dal catalogo per le write
  (globale più specifici) sono `false` nella configurazione di esempio.
- La versione 0.3.0, SHA `c2c1e8da8a7f2aba5cb8a9f679d1251e15cb38fe`, ha superato sul target
  Debian un unico gate applicativo live autorizzato in sola lettura per i due subset sopra. Lo
  smoke del trasporto Discord resta `PENDING`; il servizio è `active/running`, con zero riavvii
  osservati e gateway `discord_ready`, mentre tutte le write restano disabilitate.

La 0.3.0 estende i percorsi read senza ampliare il catalogo: `EMP-READ-001` usa la risposta elenco
emessa dalla UI sotto schema chiuso; `EMP-CONTRACT-001` può analizzare le date del contratto
corrente sull'intero elenco paginato senza fetch per-dipendente. La semantica “totale” e gli
intervalli relativi sono risolti localmente. `current_contract` accetta per-record soltanto i
keyset esatti `BASE` o `EXTENDED=BASE+6` chiavi tecniche; queste ultime hanno shape stretta e sono
discard-only. Il gate live prova soltanto i due subset esplicitamente indicati, non l'intera
superficie read.

“Tool eligible” significa soltanto che il catalogo permette l'esposizione dopo tutti i filtri.
Con i flag write correnti a `false`, nessuna write viene esposta. “Mai” indica le funzioni che il
catalogo esclude anche dalla tool exposure ordinaria.

## Read-only

| Function ID | Funzione | Ruolo/scope minimo | Flag | Tool | Stato |
|---|---|---|---|---|---|
| `EMP-READ-001` | elenco/conteggio; totale non qualificato = intero organico | read-only aggregate o HR read | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_READ_VERIFIED per il solo conteggio aggregato bounded; altre modalità NEEDS_VALIDATION |
| `EMP-READ-002` | riepilogo anagrafico redatto | HR read | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — NEEDS_VALIDATION |
| `EMP-SEARCH-001` | ricerca dipendente | HR read | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — NEEDS_VALIDATION |
| `EMP-FILTER-001` | filtri elenco | HR read | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — NEEDS_VALIDATION |
| `EMP-SORT-001` | ordinamento | HR read | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — NEEDS_VALIDATION |
| `EMP-PAGE-001` | paginazione | HR read | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — NEEDS_VALIDATION |
| `EMP-CONTRACT-001` | contratti e scadenze bulk senza N+1 | HR read | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_READ_VERIFIED per le sole scadenze bounded del prossimo mese di calendario; altre modalità NEEDS_VALIDATION |
| `EMP-RBAC-001` | gruppi/ruoli | HR read | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — NEEDS_VALIDATION |
| `EMP-TIME-001` | timbratura/accessi | HR read | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — NEEDS_VALIDATION |
| `EMP-MAT-001` | maturazioni | HR read | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — NEEDS_VALIDATION |
| `EMP-BAL-001` | bilancio | HR read + `balances:read` | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — NEEDS_VALIDATION |
| `EMP-PAY-001` | metadati buste paga | HR read | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — NEEDS_VALIDATION |
| `EMP-DOC-001` | metadati documenti | document operator | `ENABLE_READ_ACTIONS` | eligible | IMPLEMENTED — TESTED_WITH_MOCK — NEEDS_VALIDATION |

## Write, file ed export

Tutte le righe richiedono conferma monouso e il kill switch `ENABLE_WRITE_ACTIONS`; “A1” è una
approvazione distinta dal richiedente, “A2” richiede due approvatori distinti tra loro e dal
richiedente. Il numero `0` non elimina preview, conferma, RBAC, idempotenza o postcondizione.

| Function ID | Funzione | Flag specifico | Appr. | Tool | Stato |
|---|---|---|---:|---|---|
| `EMP-UPDATE-001` | modifica dipendente | `ENABLE_EMPLOYEE_UPDATE` | 0 | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-CREATE-001` | creazione dipendente | `ENABLE_EMPLOYEE_CREATE` | 0 | eligible | PARTIALLY_COMPLETED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY; live limitato al subset verificabile |
| `EMP-CONTRACT-002` | crea/modifica contratto | `ENABLE_CONTRACT_WRITE` | A1 | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-MAT-002` | nuova maturazione | `ENABLE_MATURATION_WRITE` | A1 | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-BAL-002` | correzione bilancio | `ENABLE_BALANCE_CORRECTION` | A2 | mai | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-CONNECT-001` | collega account | `ENABLE_ACCOUNT_CONNECT` | A1 | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-CONNECT-002` | scollega account | `ENABLE_ACCOUNT_DISCONNECT` | A2 | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-INVITE-001` | reinvia invito | `ENABLE_INVITE_ACTIONS` | 0 | eligible | PARTIALLY_COMPLETED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY; live NOT_AVAILABLE |
| `EMP-INVITE-002` | annulla invito | `ENABLE_INVITE_ACTIONS` | 0 | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-RBAC-002` | modifica ruoli | `ENABLE_RBAC_WRITE` | A2 | mai | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-STATUS-001` | disattiva dipendente | `ENABLE_STATUS_CHANGE` | A2 | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-STATUS-002` | riattiva dipendente | `ENABLE_STATUS_CHANGE` | A1 | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-DOC-002` | upload documento | `ENABLE_DOCUMENT_UPLOAD` | 0 | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-DOC-004` | metadati documento | `ENABLE_DOCUMENT_UPDATE` | 0 | eligible | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-DOC-005` | elimina documento | `ENABLE_DOCUMENT_DELETE` | A2 | eligible | PARTIALLY_COMPLETED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY; live NOT_AVAILABLE |
| `EMP-EXPORT-001` | export locale protetto | `ENABLE_EXPORT` | 0 | eligible | PARTIALLY_COMPLETED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY; live NOT_AVAILABLE |
| `EMP-DOC-003` | download locale protetto | `ENABLE_DOCUMENT_DOWNLOAD` | A2 | mai | PARTIALLY_COMPLETED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY; live NOT_AVAILABLE |
| `EMP-DELETE-001` | elimina dipendente | `ENABLE_EMPLOYEE_DELETE` | A2 | mai | IMPLEMENTED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY |
| `EMP-CONTRACT-003` | elimina contratto | `ENABLE_CONTRACT_DELETE` | A2 | mai | PARTIALLY_COMPLETED — TESTED_WITH_MOCK — LIVE_WRITE_UNVERIFIED — DISABLED_BY_POLICY; live NOT_AVAILABLE |

“Mai” nella colonna Tool significa “mai esposto al provider di modello”, non “privo di route operatore”. I cinque
ID esclusi dal modello sono collegati rispettivamente ai comandi deterministici
`/bh operator-balance-correction`, `/bh operator-rbac-update`,
`/bh operator-document-download`, `/bh operator-employee-delete` e
`/bh operator-contract-delete`. Tutti conservano policy, preview, conferma e A2; il download
documentale e l'eliminazione contratto restano `NOT_AVAILABLE` nel percorso live.

Per `EMP-CREATE-001`, il mock copre lo schema completo. Il percorso live accetta soltanto
`creation_mode=manual` e i campi con postcondizione verificabile: `first_name`, `last_name`,
`payroll_number`, `tax_code`, `job_title`, `business_email` e `workplace`. I campi `birth_date`,
`iban`, `phone`, `address` e `notes` sono rifiutati prima di creare il pending: non vengono quindi
mostrati come supportati per una write live che non potrebbe essere riconciliata in sicurezza.

## Condizioni non rappresentate da una singola colonna

- guild, canale e tenant devono coincidere con lo scope consentito;
- il ruolo logico deriva da ID Discord configurati, non dal testo utente;
- file upload (`EMP-DOC-002`) richiede quarantena e ClamAV disponibile/fail-closed;
- azioni critiche richiedono conferma testuale del target e A2;
- TTL, version/CAS, idempotency claim e kill switch sono ricontrollati all'esecuzione;
- un esito ambiguo diventa `UNKNOWN_REQUIRES_RECONCILIATION`, mai retry automatico;
- `ENABLE_LIVE_WRITE_TESTS` richiede employee sintetico dedicato e tenant confermato, ma resta
  vietato finché non esiste un'autorizzazione esplicita separata.
- un aggregato `READ_ONLY` può essere pubblico soltanto nel canale allowlistato; liste, scadenze e
  ogni risultato `HR_READ` restano ephemeral;
- il provider classifica esclusivamente categorie semantiche minimizzate: vocaboli grezzi, nomi,
  Employee ID, query di ricerca e risultati DIC restano nel runtime locale;
- il nome in chiaro è aperto dal `SecretStr` soltanto per elenchi/scadenze `HR_READ` ephemeral;
  aggregati `READ_ONLY`, log, audit, telemetria e provider non lo ricevono.

Vedere [Configuration](CONFIGURATION.md), [Security architecture](SECURITY_ARCHITECTURE.md) e
[Testing](TESTING.md).

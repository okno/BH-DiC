# Matrice di capacità e validazione

Questo documento descrive esclusivamente lo stato del codice e dei test pubblici. Non contiene host, revisioni distribuite, esiti di un server o stato di produzione. `LIVE_READ_VERIFIED` indica evidenza di compatibilità storicamente ottenuta per il contratto adapter, ma ogni ambiente deve ripetere privatamente il gate sulla revisione effettiva prima del rollout.

Legenda: `IMPLEMENTED` indica un percorso tipizzato; `TESTED_WITH_MOCK` usa dati sintetici; `NEEDS_VALIDATION` richiede ancora un test end-to-end; `LIVE_WRITE_UNVERIFIED` non autorizza una write; `DISABLED_BY_POLICY` e `DISABLED_BY_DEFAULT` mantengono la mutazione spenta; `NOT_AVAILABLE` è un rifiuto esplicito del percorso live.

## Matrice dei 36 Function ID

| Function ID | Funzione | Implementazione e test | Evidenza live | Stato predefinito |
| --- | --- | --- | --- | --- |
| `EMP-READ-001` | Elenco e conteggio dipendenti | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED; ogni rollout ripete il gate completo | ENABLED_BY_DEFAULT |
| `EMP-READ-002` | Dettaglio anagrafico redatto | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED | ENABLED_BY_DEFAULT |
| `EMP-SEARCH-001` | Ricerca dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED sulla sorgente elenco; matching locale | ENABLED_BY_DEFAULT |
| `EMP-FILTER-001` | Filtri elenco | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED sulla sorgente elenco; filtro locale | ENABLED_BY_DEFAULT |
| `EMP-SORT-001` | Ordinamento elenco | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED sulla sorgente elenco; sort locale | ENABLED_BY_DEFAULT |
| `EMP-PAGE-001` | Paginazione | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED | ENABLED_BY_DEFAULT |
| `EMP-CONTRACT-001` | Consultazione contratti | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED | ENABLED_BY_DEFAULT |
| `EMP-RBAC-001` | Consultazione gruppi e ruoli | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED | ENABLED_BY_DEFAULT |
| `EMP-TIME-001` | Consultazione timbratura | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED per target singolo | ENABLED_BY_DEFAULT |
| `EMP-MAT-001` | Consultazione maturazioni | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED | ENABLED_BY_DEFAULT |
| `EMP-BAL-001` | Consultazione bilancio | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED | ENABLED_BY_DEFAULT |
| `EMP-PAY-001` | Busta paga individuale, netto e link PDF temporaneo | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED | ENABLED_BY_DEFAULT |
| `EMP-PAY-002` | Ricerca collettiva buste paga per mese | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED con traversata collettiva completa | ENABLED_BY_DEFAULT |
| `EMP-DOC-001` | Metadati documenti | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED | ENABLED_BY_DEFAULT |
| `EMP-NOTIF-001` | Notifiche top-bar DIC | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED sul contratto JSON `/backend_apiV2/notifications` | ENABLED_BY_DEFAULT |
| `EMP-ONBOARD-001` | OCR locale e bozza onboarding | IMPLEMENTED — TESTED_WITH_MOCK | NEEDS_VALIDATION end-to-end sintetica sul runtime distribuito; JPEG/PNG soltanto, nessuna write DiC | ENABLED_BY_DEFAULT con ruolo HR write e capability ClamAV + Tesseract `ita+eng` |
| `EMP-UPDATE-001` | Modifica dati dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-CREATE-001` | Creazione dipendente | PARTIALLY_COMPLETED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED — subset verificabile: `first_name`, `last_name`, `payroll_number`, `tax_code`, `job_title`, `business_email`, `workplace`; rifiuto pre-pending: `birth_date`, `iban`, `phone`, `address`, `notes` | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-CONTRACT-002` | Creazione o modifica contratto | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-MAT-002` | Nuova maturazione | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-BAL-002` | Correzione bilancio | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-CONNECT-001` | Collegamento dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-CONNECT-002` | Scollegamento dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-INVITE-001` | Reinvio invito | PARTIALLY_COMPLETED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED — NOT_AVAILABLE nell'adapter live | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-INVITE-002` | Annullamento invito | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-RBAC-002` | Modifica permessi e ruoli | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-STATUS-001` | Disattivazione dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-STATUS-002` | Riattivazione dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-NOTIF-002` | Segna notifica letta/non letta | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-DOC-002` | Upload documento | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-DOC-004` | Modifica metadati documento | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-DOC-005` | Eliminazione documento | PARTIALLY_COMPLETED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED — NOT_AVAILABLE nell'adapter live | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-EXPORT-001` | Esportazione locale protetta PDF/DOCX/XLSX | IMPLEMENTED — TESTED_WITH_MOCK | CONTRACT_VERIFIED — LIVE_WRITE_UNVERIFIED — artifact locale in memoria; nessun export live | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-DOC-003` | Download in area locale protetta | PARTIALLY_COMPLETED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED — NOT_AVAILABLE nell'adapter live | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-DELETE-001` | Eliminazione definitiva dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-CONTRACT-003` | Eliminazione contratto | PARTIALLY_COMPLETED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED — NOT_AVAILABLE nell'adapter live | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |

## Interpretazione delle write

`IMPLEMENTED` per una write significa che esistono Function ID normativo,
modello `PreparedAction`, anteprima/diff redatto, policy/RBAC, feature gate,
approvazioni richieste, dispatch deterministico mock e percorso Playwright dove
supportato. Non significa che la write sia attivabile in produzione.

Il service ricontrolla il kill switch e la flag specifica immediatamente prima
dell'esecuzione. L'adapter Playwright possiede inoltre un gate
`live_writes_enabled` separato e richiede una sessione autenticata vincolata al
tenant. Le write sono eseguite una sola volta, senza retry automatico, e seguite
da riconciliazione quando esiste una postcondizione sicura.

Quattro write (`EMP-INVITE-001`, `EMP-DOC-005`, `EMP-DOC-003` e
`EMP-CONTRACT-003`) hanno catalogo, policy e comportamento mock, ma sono
`PARTIALLY_COMPLETED`: l'adapter Playwright le rifiuta intenzionalmente come `NOT_AVAILABLE`.
Per il download manca inoltre un artifact service locale protetto; l'export usa invece il nuovo
generatore in memoria ma non è ancora verificato con dati live. `EMP-CREATE-001` è
`PARTIALLY_COMPLETED` perché il mock copre lo schema completo, mentre il percorso live accetta
soltanto il subset con postcondizione verificabile riportato in matrice; `birth_date`, `iban`,
`phone`, `address` e `notes` sono rifiutati prima di creare il pending. `EMP-DOC-002` richiede un
file già passato dalla quarantena e la capability ClamAV. Le operazioni critiche possono produrre
una riconciliazione `UNKNOWN`; in tal caso è obbligatorio l'intervento umano e non
un secondo tentativo.

La matrice è controllata automaticamente da
`tests/unit/test_dic_live_status_docs.py`: gli ID devono coincidere esattamente
con `bh_dic.policies.catalog.ALL_FUNCTION_IDS`, senza duplicati, e le invarianti
di stato read/write devono restare esplicite.

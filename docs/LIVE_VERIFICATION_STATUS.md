# Stato di verifica live DIC

## Legenda ed evidenza disponibile

- `IMPLEMENTED`: contratto tipizzato e percorso adapter/service presenti.
- `PARTIALLY_COMPLETED`: catalogo, policy e mock presenti, ma il percorso adapter live non è
  disponibile end-to-end.
- `TESTED_WITH_MOCK`: percorso deterministico coperto con dati sintetici; non è
  prova di compatibilità con il sito live.
- `BASELINE_OBSERVED`: controllo o schermata riportati dalla ricognizione
  read-only di Fase 1.
- `NEEDS_VALIDATION`: nessuna verifica live di Fase 2 è stata eseguita; non va
  interpretato come `LIVE_READ_VERIFIED`.
- `LIVE_WRITE_UNVERIFIED`: la write non è mai stata eseguita su dati live.
- `NOT_AVAILABLE`: il percorso live viene rifiutato esplicitamente anziché simulare un supporto
  non osservato.
- `DISABLED_BY_DEFAULT`: il kill switch globale e la feature flag specifica sono
  `false` in `.env.example`.

In questa sessione non è stato effettuato alcun probe live. Nessuna riga è quindi
classificata `LIVE_READ_VERIFIED`. Tutte le write rimangono
`LIVE_WRITE_UNVERIFIED` e `DISABLED_BY_DEFAULT`, anche quando il relativo
controllo era visibile nella baseline. `TESTED_WITH_MOCK` indica test sintetici
del catalogo e del percorso prepare/execute, non un collaudo del DOM reale.

## Matrice dei 32 Function ID

| Function ID | Funzione | Implementazione e test | Evidenza live | Stato predefinito |
| --- | --- | --- | --- | --- |
| `EMP-READ-001` | Elenco e conteggio dipendenti | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-READ-002` | Dettaglio anagrafico redatto | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-SEARCH-001` | Ricerca dipendente | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-FILTER-001` | Filtri elenco | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-SORT-001` | Ordinamento elenco | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-PAGE-001` | Paginazione | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-CONTRACT-001` | Consultazione contratti | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-RBAC-001` | Consultazione gruppi e ruoli | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-TIME-001` | Consultazione timbratura | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED (link/controlli) — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-MAT-001` | Consultazione maturazioni | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-BAL-001` | Consultazione bilancio | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-PAY-001` | Metadati buste paga | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED (campione vuoto) — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-DOC-001` | Metadati documenti | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-UPDATE-001` | Modifica dati dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-CREATE-001` | Creazione dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-CONTRACT-002` | Creazione o modifica contratto | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-MAT-002` | Nuova maturazione | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-BAL-002` | Correzione bilancio | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-CONNECT-001` | Collegamento dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-CONNECT-002` | Scollegamento dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-INVITE-001` | Reinvio invito | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-INVITE-002` | Annullamento invito | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-RBAC-002` | Modifica permessi e ruoli | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-STATUS-001` | Disattivazione dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-STATUS-002` | Riattivazione dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-DOC-002` | Upload documento | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-DOC-004` | Modifica metadati documento | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-DOC-005` | Eliminazione documento | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-EXPORT-001` | Esportazione locale protetta | PARTIALLY_COMPLETED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED — NOT_AVAILABLE nell'adapter live | DISABLED_BY_DEFAULT |
| `EMP-DOC-003` | Download in area locale protetta | PARTIALLY_COMPLETED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED — NOT_AVAILABLE nell'adapter live | DISABLED_BY_DEFAULT |
| `EMP-DELETE-001` | Eliminazione definitiva dipendente | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |
| `EMP-CONTRACT-003` | Eliminazione contratto | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_DEFAULT |

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

`EMP-EXPORT-001` e `EMP-DOC-003` hanno catalogo, policy e comportamento mock, ma sono
`PARTIALLY_COMPLETED`: l'adapter Playwright rifiuta intenzionalmente il dispatch finché non è
disponibile un artifact service locale protetto. `EMP-DOC-002` richiede un file già passato
dalla quarantena e la capability ClamAV. Le operazioni critiche possono produrre
una riconciliazione `UNKNOWN`; in tal caso è obbligatorio l'intervento umano e non
un secondo tentativo.

La matrice è controllata automaticamente da
`tests/unit/test_dic_live_status_docs.py`: gli ID devono coincidere esattamente
con `bh_dic.policies.catalog.ALL_FUNCTION_IDS`, senza duplicati, e le invarianti
di stato read/write devono restare esplicite.

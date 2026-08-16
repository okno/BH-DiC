# Stato di verifica live DIC

## Integrazioni: stato osservato al 16 agosto 2026

| Integrazione | Stato documentato | Evidenza ancora richiesta |
|---|---|---|
| OpenAI | configurazione implementata; `LIVE_PROVIDER_UNVERIFIED` | `model-check --live` autorizzato: autenticazione, modello e contratto chiuso |
| Groq | `LIVE_VERIFIED` per `openai/gpt-oss-120b` | Verificare nuovamente dopo rotazione chiave, cambio modello o aggiornamento provider |
| llama locale | configurazione implementata; `LIVE_PROVIDER_UNVERIFIED` | runtime/modello/protezione host e `model-check --live` autorizzato |
| Discord | setup guild-scoped documentato; `LIVE_DISCORD_UNVERIFIED` | app/token/installazione, Channel ID `#mng-ai`, ruoli e registrazione |
| DIC | password rinnovata; struttura login e contratto tenant osservati read-only; check 0.2.2 fermato durante hydration pre-auth; funzioni applicative `NEEDS_VALIDATION` | release 0.2.3, `dic-auth-check --live`, vault cifrato e smoke read-only autorizzato |
| Debian deployment | preparazione runtime `VERIFIED`; servizio `STOPPED` | verifica DIC, registrazione comandi, avvio controllato e restore drill |

Il `model-check --live` riuscito promuove soltanto la coppia Groq/modello osservata: non attesta
Discord o DIC. Il doctor Debian attesta i prerequisiti controllati, non il login DIC, il gateway
Discord o il restore. Il servizio resta fermo.

## Legenda ed evidenza disponibile

- `IMPLEMENTED`: contratto tipizzato e percorso adapter/service presenti.
- `PARTIALLY_COMPLETED`: catalogo, policy e mock presenti, ma il percorso adapter live è limitato
  a un subset verificabile oppure non è disponibile end-to-end.
- `TESTED_WITH_MOCK`: percorso deterministico coperto con dati sintetici; non è
  prova di compatibilità con il sito live.
- `BASELINE_OBSERVED`: controllo o schermata riportati dalla ricognizione
  read-only di Fase 1.
- `NEEDS_VALIDATION`: la specifica funzione non è stata verificata end-to-end sul sito live;
  non va interpretato come `LIVE_READ_VERIFIED`.
- `LIVE_WRITE_UNVERIFIED`: la write non è mai stata eseguita su dati live.
- `NOT_AVAILABLE`: il percorso live viene rifiutato esplicitamente anziché simulare un supporto
  non osservato.
- `DISABLED_BY_POLICY`: lo stato canonico della write finché policy, kill switch e feature flag
  non ne consentono l'esecuzione.
- `DISABLED_BY_DEFAULT`: evidenza di configurazione, non stato alternativo; il kill switch globale
  e la feature flag specifica sono `false` in `.env.example`.

Sono stati eseguiti soltanto una ricognizione DIC autorizzata in sola lettura e i probe
infrastrutturali descritti sopra. La ricognizione ha confermato il flusso di login federato e il
contratto first-party usato dal tenant guard. La password TeamSystem è stata rinnovata e il secret
locale aggiornato, ma `dic-auth-check --live` 0.2.2 si è fermato prima dell'autenticazione per una
race di hydration e non ha creato un vault. La correzione 0.2.3 usa attese bounded/route-aware,
stage redatti e un solo tentativo di status/autenticazione. Un esito ambiguo dopo il submit viene
fermato con `CREDENTIAL_SUBMIT`/exit 78 e l'unit systemd non lo riavvia automaticamente. Questi
controlli sintetici non sono ancora evidenza live.
Nessuna Function ID DIC è quindi classificata `LIVE_READ_VERIFIED`. Tutte le write rimangono
`LIVE_WRITE_UNVERIFIED`, `DISABLED_BY_POLICY` e `DISABLED_BY_DEFAULT`, anche quando il relativo
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
| `EMP-DOC-002` | Upload documento | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-DOC-004` | Modifica metadati documento | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-DOC-005` | Eliminazione documento | PARTIALLY_COMPLETED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED — NOT_AVAILABLE nell'adapter live | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
| `EMP-EXPORT-001` | Esportazione locale protetta | PARTIALLY_COMPLETED — TESTED_WITH_MOCK | LIVE_WRITE_UNVERIFIED — NOT_AVAILABLE nell'adapter live | DISABLED_BY_POLICY — DISABLED_BY_DEFAULT |
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

Cinque write (`EMP-INVITE-001`, `EMP-DOC-005`, `EMP-EXPORT-001`, `EMP-DOC-003` e
`EMP-CONTRACT-003`) hanno catalogo, policy e comportamento mock, ma sono
`PARTIALLY_COMPLETED`: l'adapter Playwright le rifiuta intenzionalmente come `NOT_AVAILABLE`.
Per export e download manca inoltre un artifact service locale protetto. `EMP-CREATE-001` è
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

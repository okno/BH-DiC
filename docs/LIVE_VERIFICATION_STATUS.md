# Stato di verifica live DIC

## Integrazioni: stato osservato al 17 agosto 2026

| Integrazione | Stato documentato | Evidenza ancora richiesta |
|---|---|---|
| OpenAI | configurazione implementata; `LIVE_PROVIDER_UNVERIFIED` | `model-check --live` autorizzato: autenticazione, modello e contratto chiuso |
| Groq | `LIVE_VERIFIED` per `openai/gpt-oss-120b` | Verificare nuovamente dopo rotazione chiave, cambio modello o aggiornamento provider |
| llama locale | configurazione implementata; `LIVE_PROVIDER_UNVERIFIED` | runtime/modello/protezione host e `model-check --live` autorizzato |
| Discord | trasporto storico verificato su una release precedente; servizio 0.3.0 `active/running`, zero riavvii osservati e gateway `discord_ready`; il gate applicativo ha verificato sensibilità `PUBLIC`/non-ephemeral e `SENSITIVE`/ephemeral, non il round-trip slash | Smoke trasporto 0.3.0 RBAC/read-only/HR-read ancora `PENDING` |
| DIC | `LIVE_AUTHENTICATED` e tenant attestato sullo SHA 0.3.0 verificato; conteggio aggregato e scadenze bounded del prossimo mese di calendario `LIVE_READ_VERIFIED` | Altre modalità read `NEEDS_VALIDATION`; nessuna write live |
| Debian deployment | commit applicativo `0.3.0` allo SHA `c2c1e8da8a7f2aba5cb8a9f679d1251e15cb38fe`, gate live PASS; servizio `active/running`, zero riavvii osservati, gateway `discord_ready`; hotfix operativi successivi con gate/deployment separati | Smoke trasporto Discord e restore drill `PENDING` |

Il `model-check --live` riuscito promuove soltanto la coppia Groq/modello osservata. Il gate DIC
applicativo promuove soltanto i due subset read bounded indicati; non attesta il trasporto Discord.
Il doctor Debian attesta i prerequisiti controllati, non il restore né lo smoke slash.

Per lo stesso SHA: 834 test locali PASS; branch coverage 85% su 10.361 statement e 3.258 branch;
Ruff, mypy, Bandit e `pip-audit` verdi; CI e CodeQL remoti riusciti. Nessuno di questi gate statici
o sintetici sostituisce le evidenze live separate.

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
- `LIVE_READ_VERIFIED`: soltanto il subset operativo esplicitamente descritto è stato attraversato
  end-to-end nel gate live autorizzato; non promuove altre modalità dello stesso Function ID.
- `LIVE_WRITE_UNVERIFIED`: la write non è mai stata eseguita su dati live.
- `NOT_AVAILABLE`: il percorso live viene rifiutato esplicitamente anziché simulare un supporto
  non osservato.
- `DISABLED_BY_POLICY`: lo stato canonico della write finché policy, kill switch e feature flag
  non ne consentono l'esecuzione.
- `DISABLED_BY_DEFAULT`: evidenza di configurazione, non stato alternativo; il kill switch globale
  e la feature flag specifica sono `false` in `.env.example`.

Sono stati eseguiti soltanto ricognizioni DIC autorizzate in sola lettura e i probe
infrastrutturali descritti sopra. Il check 0.2.2 si è fermato durante l'hydration e il 0.2.3 a
`DIC_EMAIL`, senza submit password. Il tentativo server 0.2.4 ha effettuato un solo submit ma ha
rifiutato fail-closed la callback DIC legittima, con `CREDENTIAL_SUBMIT`/exit 78 e nessun restart
automatico. Un successivo accesso manuale autorizzato in browser fresco ha accettato le
credenziali con un solo submit e ha osservato la sequenza password TeamSystem → callback DIC esatta
→ dashboard → route e marker esatti della lista dipendenti. Dopo il deployment, un singolo check
headless 0.2.5 ha verificato sessione e tenant `VERIFIED_BY_ADAPTER` nel processo corrente. Un
riavvio successivo ha
mostrato che il vault precedente non conservava i token federati in `sessionStorage`; la route
TeamSystem è passata direttamente a `LoginPassword`, mentre il codice pretendeva ancora
`LoginEmail`, e systemd si è fermato con exit 78 prima del gateway. La 0.2.7 accetta soltanto le due
route TeamSystem esatte, vincola l'identità prima del segreto, cifra anche lo snapshot bounded
`sessionStorage` e mantiene Discord online in stato `DEGRADED` senza login implicito. Dopo la sua
distribuzione, un check server 0.2.7 si è fermato a `TEAMSYSTEM_EMAIL` prima delle azioni
credenziali. La 0.2.8 riconosce la root e-mail TeamSystem esatta corrente, la legacy
`/Account/LoginEmail` e soltanto le transizioni pending bounded `/connect/authorize` e
`/connect/authorize/callback`; un SSO senza controlli è accettato soltanto dopo marker DIC e tenant
attestato e non esegue fill/click/submit credenziali. Sullo SHA 0.3.0 esatto documentato, il
successivo gate unico ha attestato autenticazione e tenant prima delle due letture bounded.
Dalla 0.3.0 la UI può essere usata per osservare passivamente la risposta esatta
`GET /backend_apiV2/employees`: origine, query, metodo, status, media type, limite, schema,
paginazione e tenant vengono validati prima di creare una proiezione tipizzata. Una diagnostica
live autorizzata e minimizzata ha inoltre confermato che i metadati URL del paginator usano la
stessa origine esatta e il path distinto `/employees`; la validazione fail-closed non rende i due
path intercambiabili. Ogni URL pagina preserva i nove parametri della query UI validata e modifica
soltanto `page`; precedente/successivo e pagina attiva sono correlati senza usare le label come
fonte autorevole. `current_contract` viene validato per-record come keyset esatto `BASE` oppure
`EXTENDED=BASE+6` chiavi tecniche: booleani stretti per `flexible_workinghours`, `hours_alert` e
`ongoing`; `null` stretti per `note`, `workinghours` e `workinghours_list`. I campi tecnici sono
discard-only e non vengono proiettati. Il display name in chiaro è protetto come `SecretStr` e può
essere aperto soltanto per risultati `HR_READ` sensibili/ephemeral; e-mail, codice fiscale e
matricola restano mascherati. Il gate ha verificato il conteggio aggregato `PUBLIC`/non-ephemeral,
le scadenze bounded del prossimo mese di calendario `SENSITIVE`/ephemeral, la telemetria token di
entrambe e lo status API/token completo. Il provider resta intent-only e riceve categorie
semantiche canoniche, mai nomi, Employee ID o risultati DIC.
Dopo la rotazione del secret il vault precedente va invalidato deliberatamente una volta prima di
un unico check live; un esito `CREDENTIAL_SUBMIT` non va ritentato. Le write restano disabilitate.
Soltanto i subset bounded di `EMP-READ-001` e `EMP-CONTRACT-001` sono quindi classificati
`LIVE_READ_VERIFIED`. Tutte le write rimangono
`LIVE_WRITE_UNVERIFIED`, `DISABLED_BY_POLICY` e `DISABLED_BY_DEFAULT`, anche quando il relativo
controllo era visibile nella baseline. `TESTED_WITH_MOCK` indica test sintetici
del catalogo e del percorso prepare/execute, non un collaudo del DOM reale.

## Matrice dei 32 Function ID

| Function ID | Funzione | Implementazione e test | Evidenza live | Stato predefinito |
| --- | --- | --- | --- | --- |
| `EMP-READ-001` | Elenco e conteggio dipendenti | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED soltanto per conteggio aggregato bounded; elenco e altre modalità NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-READ-002` | Dettaglio anagrafico redatto | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-SEARCH-001` | Ricerca dipendente | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-FILTER-001` | Filtri elenco | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-SORT-001` | Ordinamento elenco | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-PAGE-001` | Paginazione | IMPLEMENTED — TESTED_WITH_MOCK | BASELINE_OBSERVED — NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
| `EMP-CONTRACT-001` | Consultazione contratti | IMPLEMENTED — TESTED_WITH_MOCK | LIVE_READ_VERIFIED soltanto per scadenze bounded del prossimo mese di calendario; altre modalità NEEDS_VALIDATION | ENABLED_BY_DEFAULT |
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

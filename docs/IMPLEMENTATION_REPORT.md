# Implementation report

Snapshot documentale: **17 agosto 2026**. Lo SHA autorevole è riportato nel report di consegna perché
un commit non può auto-referenziare il proprio hash. Aggiornare questo file dopo il deployment senza
sostituire `BLOCKED` o `UNVERIFIED` con inferenze.

## Repository

| Campo | Stato osservato |
|---|---|
| Owner | `okno` |
| Nome | `BH-DiC` |
| Remote | `https://github.com/okno/BH-DiC.git` |
| Visibilità | `PUBLIC`, verificata tramite GitHub API e scelta esplicitamente dal titolare |
| Branch | `main` |
| Commit SHA | riportato nel report di consegna; non auto-referenziato nel commit stesso |
| Git | branch `main` pushato; worktree e tracking remoto verificati dal gate di consegna |

Il tree corrente del remote pubblico non contiene credenziali o PII rilevate ed è limitato a
sorgenti, configurazioni di esempio e fixture sintetiche. Segreti, identificatori operativi, stato
runtime e PII devono restare locali; l'eccezione nei metadati Git storici è documentata tra i
problemi residui. Nessuna licenza open source è stata aggiunta.

## Server

| Campo | Stato osservato |
|---|---|
| Host | target Debian 12 autorizzato; indirizzo omesso dalla documentazione |
| Utente runtime | account di servizio dedicato verificato |
| Directory | `/opt/bh-dic`; ownership e modalità runtime verificate |
| Python server | 3.12 installato separatamente dal Python di sistema |
| `.venv` server | presente; `pip check` riuscito |
| Chromium server | installato nel profilo runtime; launch headless riuscito |
| Database server | SQLite presente e migrazione alla head verificata |
| Antivirus | ClamAV attivo; socket `0660` e scansione applicativa riuscita |
| Doctor | offline e online riusciti |
| Bot target | 0.2.7 distribuita; check DIC corrente fermato a `TEAMSYSTEM_EMAIL` prima delle azioni credenziali; candidata 0.2.8 non ancora distribuita né verificata live |

Il provider Groq e il modello configurato hanno superato il probe live chiuso. La password
TeamSystem è stata rinnovata nel flusso umano e il secret locale aggiornato. I check DIC headless
0.2.2 e 0.2.3 si sono fermati prima del submit password. La 0.2.4 ha inviato la password una sola
volta, ma ha rifiutato fail-closed la callback DIC legittima con exit 78. Un successivo accesso
manuale autorizzato, in browser fresco e in sola lettura, ha accettato le credenziali con un solo
submit e raggiunto callback, dashboard, route e marker esatti della lista dipendenti. Dopo il
deployment 0.2.5, un singolo `dic-auth-check --live` ha attestato sessione `AUTHENTICATED` e tenant
`VERIFIED_BY_ADAPTER` nel processo corrente. Il comando guild-scoped è stato registrato e il
gateway ha risposto; il primo smoke è stato negato dal gate RBAC prima del dispatch. Il riavvio
successivo ha evidenziato che il vault non conservava `sessionStorage` e si è fermato sulla route
TeamSystem password diretta, prima del gateway. La 0.2.7 è stata distribuita; il check DIC corrente
si è fermato a `TEAMSYSTEM_EMAIL` prima di qualunque azione credenziale. La candidata 0.2.8
corregge il contratto corrente TeamSystem/OIDC; i gate locali sono verdi e la verifica live resta
`PENDING`.
Nessuna Function ID DIC resta verificata live.

## Implementazione

Completati nel codice e testati con risorse sintetiche:

- configurazione Pydantic fail-closed, logging redatto, DB async e Alembic;
- Discord gate/command group e runtime composition; registrazione guild-scoped e gateway verificati
  live, con diniego RBAC osservato prima del dispatch;
- intent router strict multi-provider: Responses per OpenAI/Groq e chat-compatible per llama,
  con storage applicativo disabilitato e tool exposure filtrata;
- persona configurabile e confinata alla presentazione, senza effetto su policy/RBAC;
- `model-check` offline per default e probe live provider sintetico/chiuso, esplicitamente opt-in;
- catalogo/policy/RBAC/flag per 32 Function ID;
- approval state machine, A1/A2, TTL, conferma hashata monouso, CAS/idempotenza, payload cifrati;
- audit append-only HMAC e verifica catena;
- adapter mock completo, Page Object e adapter Playwright; login federato con allowlist esatta,
  probe di sessione restaurata e attestazione tenant passiva first-party;
- autenticazione 0.2.3 con polling bounded e route-aware dei controlli visibili univoci, fallback
  DIC pubblico `Accedi`, stage diagnostici chiusi e status/login serializzati a tentativo singolo
  senza retry delle credenziali; gli esiti non dimostrabili dopo il submit usano
  `DicAuthOutcomeUnknownError`/`CREDENTIAL_SUBMIT`, exit 78 e restart systemd inibito;
- probe di sessione ripristinata confinato al budget residuo del login e classificato
  `SESSION_PROBE` quando non può dichiarare successo entro la deadline;
- correzione 0.2.4 del campo e-mail DIC, ristretto all'unico input nativo sotto il contenitore
  pubblico `data-testid="login-email"`, con regressione sintetica per il caso padre/input duplicato;
- correzione 0.2.5 del completamento federato: callback DIC esatta ammessa solo come transitoria
  bounded, query opaca mai letta o registrata e varianti di origine/porta/fragment/path rifiutate;
  marker autenticato atteso dentro la cattura tenant con route esatta e budget condiviso, mentre
  `/data/company/id` resta obbligatorio e il submit password resta singolo; lo user agent Chromium
  nativo resta invariato perché la verifica live non lo ha indicato come causa;
- correzione 0.2.7 dell'ingresso TeamSystem: dopo il submit DIC sono ammesse soltanto le route
  esatte `LoginEmail` o `LoginPassword`; prima del segreto l'identità del form password deve
  coincidere con l'account configurato, senza esporre il valore in errori o log;
- vault 0.2.7 esteso allo snapshot bounded `sessionStorage` della sola origine DIC, cifrato insieme
  a cookie/localStorage, ripristinato una sola volta prima degli script applicativi e mai su altre
  origini; vault legacy leggibili e valori opachi restano gestiti fail-closed;
- avvio 0.2.7 separato dal login: il gateway non invia credenziali DIC, un vault mancante/scaduto o
  illeggibile produce stato `DEGRADED` e le sole funzioni DIC falliscono chiuso; l'autenticazione e
  la persistenza restano confinate a `dic-auth-check --live` esplicito;
- candidata 0.2.8: ingresso e-mail sulla root TeamSystem HTTPS esatta corrente oppure legacy
  `/Account/LoginEmail`, con `/connect/authorize` e `/connect/authorize/callback` ammesse soltanto
  come transizioni pending bounded; SSO senza email/password accettato esclusivamente dopo route
  applicativa DIC, marker autenticato e attestazione tenant, con zero azioni sui controlli
  credenziali; query e `login_hint` restano opachi e nessuna route generica viene aggiunta;
- quarantena, MIME/ext/hash/deduplica, ClamAV fail-closed e retention;
- CLI operatore e 22 script Bash con gate statico/contratto locale;
- unit systemd 0.2.4 compatibile con Debian 12: `ConditionPathExists` più
  `ExecCondition=/usr/bin/test -f` al posto della direttiva non supportata
  `ConditionPathIsRegularFile`; `doctor.sh` conserva i gate `.env` `0600`/configurazione;
- documentazione sicurezza, privacy, operazioni, Wazuh, deployment e troubleshooting.

Stato funzionale:

- 13 read: `IMPLEMENTED`, `TESTED_WITH_MOCK`, `NEEDS_VALIDATION`;
- 13 write: `IMPLEMENTED`, `TESTED_WITH_MOCK`, `LIVE_WRITE_UNVERIFIED`,
  `DISABLED_BY_POLICY`; 6 write sono `PARTIALLY_COMPLETED`, `TESTED_WITH_MOCK`,
  `LIVE_WRITE_UNVERIFIED`, `DISABLED_BY_POLICY`: `EMP-CREATE-001` supporta live soltanto il subset
  con postcondizione verificabile, mentre `EMP-INVITE-001`, `EMP-DOC-005`, `EMP-EXPORT-001`,
  `EMP-DOC-003` ed `EMP-CONTRACT-003` hanno percorso live `NOT_AVAILABLE`; i 19 Function ID write
  sono disabilitati e i 18 gate distinti usati dal catalogo per le write restano `false` per
  default;
- kill switch `ENABLE_WRITE_ACTIONS=false`, `ENABLE_LIVE_WRITE_TESTS=false`;
- bot target da aggiornare alla candidata 0.2.8 soltanto dopo i gate; nessuna modifica DIC di
  produzione e nessuna Function ID live autorizzata.

Dettaglio: [Feature matrix](FEATURE_MATRIX.md).

## Test e gate — candidata 0.2.8

I gate completi del worktree candidato 0.2.8 sono verdi. Le verifiche live DIC, provider e Discord
sono evidenze separate e non sostituiscono questi gate né promuovono le Function ID HR a
verificate.

| Comando | Risultato |
|---|---|
| `ruff format --check .` | PASS, 187 file |
| `ruff check .` | PASS |
| `mypy src` | PASS, 108 file sorgente |
| `pytest` | PASS, 655 test; 1 warning deprecazione `audioop` di terza parte |
| `coverage run --branch -m pytest` | PASS, 655 test |
| `coverage report --show-missing --fail-under=80` | PASS, branch coverage 85,88% |
| `bandit -q -r src` | PASS, 16.347 righe e 0 finding |
| `python -m pip check` | PASS, 0 dipendenze rotte |
| `python -m pip_audit --strict --requirement requirements.lock --no-deps --progress-spinner off` | PASS, 0 vulnerabilità note |
| `git diff --check` | PASS |
| `gitleaks` | NOT RUN: binario non disponibile sull'host; test hygiene e review diff PASS |
| parsing configurazioni/workflow YAML | PASS, 5/5 |
| scansione hygiene/versione/documentazione | PASS, 18/18 test |
| script `bash -n` + contratti/lifecycle ops | PASS, 22/22 |
| link Markdown locali | PASS, 72/72 in 31 file |

I workflow remoti restano da verificare dopo il push. Le evidenze Debian/Groq e il login manuale
riportati sopra derivano da controlli separati; non promuovono adapter DIC o Discord a verificati.

## Operatività

```text
Configurazione  ./scripts/init-config.sh && ./scripts/doctor.sh
Provider offline .venv/bin/python -m bh_dic model-check
Provider live   .venv/bin/python -m bh_dic model-check --live  # solo se autorizzato
Avvio           ./scripts/start.sh
Foreground debug ./scripts/run-foreground.sh
Status          ./scripts/status.sh
Log             ./scripts/logs.sh all --follow
File            ./scripts/files.sh list
Audit           ./scripts/audit-verify.sh
Stop            ./scripts/stop.sh
Restart         ./scripts/restart.sh
Backup          ./scripts/backup.sh
Restore         ./scripts/restore.sh var/backups/<BACKUP>.tar.gz --confirm RESTORE
Update          ./scripts/update.sh
```

Installazione, doctor, audit e smoke mock sono stati eseguiti sul server. Il restore drill non è
stato eseguito. Backup/restore corrente supporta SQLite locale, non PostgreSQL.

## Sicurezza

- nessun segreto o dato personale intenzionalmente inserito nei file documentali;
- `MODEL_STORE=false`; provider senza browser, file, segreti o decisione policy;
- scope guild/canale/tenant, RBAC e rate limit fail-closed;
- write globalmente e specificamente disabilitate;
- A2 richiede identità distinta e kill switch ricontrollato all'esecuzione;
- parametri pending cifrati; audit HMAC; file in quarantena con antivirus fail-closed;
- il pending file conserva solo l'`upload_id`; path e SHA-256 non sono esposti in eventi, log,
  Discord o al provider, e lo SHA-256 è visibile soltanto all'operatore locale nei metadati file;
- nessuna Function ID DIC write/read live eseguita; 0.2.7 distribuita e check DIC corrente fermato
  a `TEAMSYSTEM_EMAIL`; correzione 0.2.8 non ancora distribuita né verificata live;
- l'unit systemd impedisce il restart su exit 78; dalla 0.2.7 il comando `run` non esegue alcun
  login automatico e il codice 78 resta per l'autenticazione esplicita incerta; su Debian 12
  l'unit dalla 0.2.4 usa
  `ConditionPathExists` più un `ExecCondition` di file regolare, mentre `doctor.sh` verifica
  modalità `0600` e configurazione.

## Problemi residui

- mappatura RBAC Discord dell'operatore da correggere e smoke read-only ancora da completare;
- Groq/modello verificati live; OpenAI e llama restano non verificati;
- selettori e route delle funzioni HR Playwright non validati live; UI drift possibile;
- form write, delete/export/download e postcondizioni non verificati live;
- MFA/CAPTCHA e funzionalità TeamSystem non documentate possono bloccare flussi;
- rotazione log e Wazuh non installati/testati sul target;
- restore drill e backup server non eseguiti;
- i file tracciati sono privi di PII rilevata, ma i commit già pubblicati conservano l'identità
  e-mail della configurazione Git locale nei metadati Author/Committer; i nuovi commit usano
  l'identità GitHub `noreply` e la cronologia non è stata riscritta perché richiederebbe force-push;
- alla data dello snapshot GitHub API riporta secret scanning, push protection e branch protection
  non abilitati: devono essere attivati nelle impostazioni del repository pubblico; il workflow
  CodeQL richiede l'upload SARIF, mentre Bandit, dependency audit, gitleaks, required review tramite
  processo operativo e i gate CI restano controlli complementari.

Non inserire in versioni successive password, token, API key, cookie, TOTP, PII o contenuti di
documenti.

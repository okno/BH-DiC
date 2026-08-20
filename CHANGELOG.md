# Changelog

All notable changes to BH-DiC are documented here. The project follows Semantic Versioning once a
stable public API is declared.

## [Unreleased]

### Added

- Modalità Discord `channel` nel solo canale allowlistato: ignora la chat non HR, usa un responder
  stateless senza tool per orientamento generale e il coordinator autorizzato per richieste DIC.
  Input/output del solo responder generale sono minimizzati, redatti e protetti dalle mention.
- Routing locale per conteggio/elenco completo, attiva/disattiva per ID o nome e richieste export;
  il canale usa lo stesso coordinator, RBAC, preview e conferma di `/bh`.
- Generazione e validazione in memoria di PDF, DOCX, XLSX e tabella ASCII completa, con limite
  dimensione e neutralizzazione delle formule Excel.
- Comandi `/bh capabilities` e `/bh funzioni` con matrice runtime di disponibilità/policy/RBAC.
- Comando guild-scoped `/bh dic reconnect` per SECURITY_ADMIN/SYSTEM_ADMIN: un solo submit
  credenziali, attestazione tenant, persistenza cifrata e stop sugli esiti ambigui.

### Changed

- Il routing Groq usa Chat Completions con una sola tool call obbligatoria e validazione locale,
  evitando il percorso Responses che in produzione restituiva `tool_use_failed` su richieste reali.
- Rate limit e concorrenza della chat pubblica sono separati dagli slash command. Deny RBAC,
  saturazione e indisponibilità provider producono ora una risposta pubblica chiusa invece del
  silenzio.
- Gli SDK client del router e del responder vengono chiusi esplicitamente; la telemetria modello
  distingue `public_hr_response` da `intent_route` e chiude anche le chiamate cancellate.
- Gli extra dei deny Discord sono appiattiti nei JSON log, coerentemente con
  `details.reason`, senza inserire prompt o identificativi utente grezzi.

## [0.3.0] - 2026-08-17

### Added

- Assistente Senior HR con presentazione locale, deterministica e configurabile: tono amichevole,
  forma di indirizzo, verbosità e indicatori di stato personalizzano il dialogo senza modificare
  RBAC, policy, dati o autorizzazioni.
- Semantica locale per le domande operative principali. Un “totale dipendenti” non qualificato
  indica l'intero organico, mentre “attivi” e “disattivati” applicano il filtro esplicito. Le
  scadenze “nel prossimo mese”, “questo mese” e “nei prossimi N giorni” vengono trasformate in un
  intervallo di date dal runtime, non dal provider.
- Analisi bulk delle scadenze contrattuali basata sulla lista dipendenti paginata e sul contratto
  corrente: accetta date ISO e italiane dove previste, verifica stabilità e completezza della
  paginazione e non esegue una richiesta contratto separata per ogni dipendente.
- Telemetria locale dei token provider con migrazione Alembic `0002_model_usage`. Ogni chiamata
  registra soltanto provider, modello, correlation key, stato e contatori; `/bh ask` mostra input,
  output e totale della richiesta e il cumulativo locale, mentre `/bh status` aggiunge stato bot,
  provider/modello, ultima osservazione API e uso cumulativo.

### Changed

- L'elenco dipendenti Playwright osserva passivamente soltanto la risposta `GET` emessa dalla UI
  sull'origine DIC e sul path esatti `/backend_apiV2/employees`. I metadati URL del paginator nel
  corpo usano invece lo stesso origin e il path esatto `/employees`. Metodo, stato, media type,
  query, limite del corpo, paginazione e schema chiuso vengono validati prima di produrre una
  proiezione tipizzata. Il display name è protetto come `SecretStr` e viene aperto soltanto per
  risultati `HR_READ` sensibili/ephemeral; e-mail, codice fiscale e matricola restano mascherati.
  BH-DiC non trasforma nessuno dei path osservati in un'API chiamata direttamente.
- Gli aggregati non sensibili possono essere pubblicati nel canale Discord allowlistato dopo un
  acknowledgement privato. Elenchi, identità, contratti, scadenze individuali, stato operativo e
  altri risultati HR restano ephemeral per il solo richiedente autorizzato.
- Una sessione browser tenant-attestata viene ripersistita in modo serializzato dopo verifiche e
  letture riuscite, così le rotazioni valide di cookie e `sessionStorage` sopravvivono al riavvio.

### Fixed

- Reso fail-closed l'aggiornamento operativo: `update.sh` rifiuta root, verifica ownership e
  leggibilità del progetto, accetta soltanto systemd assente oppure `loaded/inactive`, esegue i
  preflight Git e ambiente prima di fermare un processo PID e ricontrolla dipendenze/import dopo
  l'installazione. In modalità systemd stop e start restano operazioni esterne esplicite, evitando
  aggiornamenti live e artefatti root-owned nel repository o nel virtualenv.
- Separata la validazione fail-closed dell'URL della risposta da quella degli URL di paginazione,
  dopo che una diagnostica live autorizzata e minimizzata ha confermato i due path distinti. I due
  path non sono intercambiabili. Ogni URL pagina deve preservare tutti i nove parametri della query
  UI validata, cambiando soltanto `page`; link precedente/successivo, pagina attiva e limiti sono
  correlati deterministicamente. Origin, porta, userinfo, fragment, path e query continuano a
  essere verificati secondo il rispettivo contratto esatto.
- Allineato il contratto di `current_contract.part_time_percentage` alla struttura osservata con
  diagnostica live minimizzata: la chiave resta obbligatoria, accetta `null` e, se valorizzata,
  richiede un intero stretto nell'intervallo 0-100, senza registrare dati tenant o PII.
- Allineato `current_contract` ai due soli keyset osservati per singolo record: `BASE` oppure
  `EXTENDED`, composto da `BASE` più le sei chiavi tecniche `flexible_workinghours`, `hours_alert`,
  `note`, `ongoing`, `workinghours` e `workinghours_list`. Nel formato esteso
  `flexible_workinghours`, `hours_alert` e `ongoing` sono booleani JSON stretti; `note`,
  `workinghours` e `workinghours_list` sono `null` stretti. I campi tecnici vengono validati e
  scartati, mai proiettati; subset, superset e varianti sconosciute falliscono chiuso.

### Security

- OpenAI, Groq e llama restano esclusivamente router di intento. Prima della chiamata, il testo
  viene ridotto a categorie semantiche canoniche; nomi, query di ricerca, Employee ID e termini
  liberi vengono rimossi o sostituiti, anche se un nome coincide con un termine HR;
  risultati DIC, righe dipendente, DOM e scadenze non sono mai inviati al provider. Un Employee ID
  esplicito viene riassociato soltanto localmente dopo il routing e rivalidato dalle policy.
- I contatori token sono esclusivamente quelli dichiarati dal provider: assenza o esito remoto
  incerto diventano rispettivamente `UNAVAILABLE` o `UNKNOWN`, senza stime. Prompt, testo utente,
  identità Discord e dati DIC non sono conservati nella tabella di utilizzo.
- `@everyone`, il cui ID coincide con quello del guild, può essere mappato soltanto a
  `DISCORD_READONLY_ROLE_IDS` per aggregati e stato autorizzati nel canale allowlistato. Le letture
  individuali e le scadenze richiedono un ruolo umano dedicato in `DISCORD_HR_READ_ROLE_IDS`.
  Tutte le write restano disabilitate.

### Verification

- Gate locali completi sul rilascio: 834 test passati, branch coverage 85% su 10.361 statement e
  3.258 branch, Ruff, mypy, Bandit e `pip-audit` verdi. CI e CodeQL sono riusciti sullo SHA esatto
  `c2c1e8da8a7f2aba5cb8a9f679d1251e15cb38fe`.
- Lo stesso SHA, versione `0.3.0`, è stato verificato sul target Debian con un unico gate live
  autorizzato in sola lettura: autenticazione e tenant attestati; conteggio aggregato
  `PUBLIC`/non-ephemeral con telemetria token; scadenze del prossimo mese di calendario
  `SENSITIVE`/ephemeral, bounded e con telemetria token; stato API/token completo. Le write sono
  rimaste disabilitate. Il gate applicativo non attesta il trasporto Discord: lo smoke slash
  Discord resta `PENDING`. Dopo il gate il servizio è stato avviato `active/running`, con zero
  riavvii osservati e gateway `discord_ready`.

## [0.2.8] - 2026-08-17

### Fixed

- Il login federato riconosce come schermata e-mail TeamSystem sia la root HTTPS esatta `/`,
  osservata nell'interfaccia pubblica corrente, sia la route legacy esatta
  `/Account/LoginEmail`. Le transizioni OIDC documentate `/connect/authorize` e
  `/connect/authorize/callback` sono ammesse soltanto come stati pending bounded sulla stessa
  origine esatta; non sono stati introdotti prefix match, route generiche o User-Agent spoofing.
- Una sessione TeamSystem ancora valida può completare il Single Sign-On senza mostrare o usare
  controlli e-mail/password. Questo percorso viene accettato soltanto dopo route applicativa DIC,
  marker autenticato e attestazione tenant first-party esatta, e garantisce zero azioni sui
  controlli delle credenziali. Un risultato non attestabile continua a fallire chiuso.

### Security

- Dopo una rotazione di password/account/tenant il vecchio vault deve essere invalidato una sola
  volta, deliberatamente e con servizio fermo, prima di un unico `dic-auth-check --live`.
  `CREDENTIAL_SUBMIT` o qualunque esito post-submit incerto vieta ogni retry automatico o in loop.
- Schema, host, path, porta, userinfo e fragment restano confrontati fail-closed; query OIDC e
  `login_hint` rimangono opachi e non vengono letti né registrati.
- Route, account, CAPTCHA, unicità e stato del controllo vengono rivalidati nello stesso task che
  compila o invia la password, impedendo il retargeting durante una navigazione concorrente. Le
  configurazioni non-mock rifiutano trace e screenshot Playwright, che potrebbero acquisire
  credenziali, cookie o PII.

### Verification

- Gate locali completi della candidata 0.2.8: 655 test passati, branch coverage 85,88%, Ruff,
  mypy, Bandit, dependency check/audit, YAML, script operativi e link documentali verdi. La
  correzione non è ancora stata verificata sul target DIC live.

## [0.2.7] - 2026-08-17

### Fixed

- Il login federato accetta soltanto i due ingressi TeamSystem esatti: `LoginEmail` seguito da
  `LoginPassword`, oppure `LoginPassword` diretto quando DIC passa il `login_hint`. Il secondo
  percorso non reinvia l'e-mail e il form password viene vincolato all'account configurato prima
  di compilare il segreto; CAPTCHA, deadline globale, submit password singolo, callback esatta e
  attestazione tenant restano obbligatori. Lo User-Agent Chromium nativo non cambia.
- Il vault Fernet conserva anche lo snapshot bounded di `sessionStorage` della sola origine DIC,
  oltre a cookie e `localStorage`. Il ripristino avviene una sola volta in un documento bootstrap
  DIC sintetico intercettato localmente, prima della prima navigazione applicativa; non viene
  ripetuto dopo refresh token o logout e non materializza il payload su altre origini. I vault
  legacy restano leggibili e le chiavi/valori opachi non vengono normalizzati.
- L'avvio del gateway non invia più implicitamente credenziali DIC. Se il vault manca, scade o è
  illeggibile, Discord resta online in modalità degradata senza sovrascriverlo e le operazioni DIC
  falliscono chiuso. Soltanto `dic-auth-check --live` può autenticare e persistere una nuova
  sessione; un vault illeggibile continua a bloccare quel check esplicito.

### Changed

- `/bh status` e `/bh health` distinguono browser disponibile da tenant autenticato e riportano
  esito degradato quando la sessione DIC non è verificata.
- La guida Discord documenta sia il mapping strettamente `READ_ONLY` tramite `@everyone`, sia il
  ruolo umano dedicato raccomandato per letture HR, mantenendo guild e canale allowlistati.

## [0.2.6] - 2026-08-17

### Fixed

- Le migrazioni Alembic preservano i logger applicativi già configurati. In precedenza il
  caricamento di `alembic.ini` disabilitava `bh_dic.*`, lasciando vuoti i log JSONL e il journal
  dopo l'avvio del gateway anche quando Discord rifiutava correttamente una richiesta.
- Un test di regressione esegue una migrazione reale e verifica che un deny Discord venga scritto
  sia in `app.jsonl` sia in `discord.jsonl`.

## [0.2.5] - 2026-08-17

### Fixed

- Il completamento federato TeamSystem riconosce come stato transitorio soltanto la callback DIC
  esatta `/it/callback`, entro il budget condiviso del login. La query opaca necessaria al
  protocollo è ammessa ma non viene letta né registrata; fragment, porta esplicita, userinfo, host
  somigliante, trailing slash e path aggiuntivi restano rifiutati fail-closed.
- Il marker autenticato viene atteso con polling limitato durante la cattura tenant, senza
  estendere il budget, aggiungere retry delle credenziali o rendere opzionale l'attestazione
  first-party `/data/company/id`.

### Security

- Un submit della password resta singolo. Esiti post-submit non dimostrabili continuano a
  terminare con `DicAuthOutcomeUnknownError`/`CREDENTIAL_SUBMIT` ed exit code 78; nessuna write e
  nessun fallback tenant sono stati aggiunti.
- Lo user agent Chromium nativo resta invariato: la verifica live non ha indicato che fosse la
  causa e la release non introduce spoofing o bypass dei controlli del sito.

### Verification

- Un accesso manuale autorizzato, in un browser fresco e in sola lettura, ha accettato le
  credenziali con un solo submit e ha osservato la sequenza TeamSystem password → callback DIC
  esatta → dashboard → route e marker esatti della lista dipendenti. Questo attesta soltanto il
  login manuale (`LIVE_AUTHENTICATED`): adapter headless, attestazione tenant e persistenza del
  vault sul server restano da verificare con un unico check autorizzato dopo il deployment.

## [0.2.4] - 2026-08-16

### Fixed

- Il campo e-mail DIC usa ora l'unico `input` nativo sotto il contenitore pubblico
  `data-testid="login-email"`. Nella 0.2.3 il lookup per placeholder risolveva sia il componente
  padre sia l'input nativo e il check live si fermava correttamente allo stage `DIC_EMAIL`, prima
  dell'autenticazione.
- L'unit systemd per Debian 12 sostituisce la direttiva non supportata
  `ConditionPathIsRegularFile` con `ConditionPathExists` e un `ExecCondition` che richiede un file
  regolare. `doctor.sh` continua a verificare separatamente modalità `0600` di `.env` e validità
  della configurazione.

### Changed

- Il gate DIC resta non completato: nessuna sessione, attestazione tenant o Function ID è stata
  verificata live. Dopo il deployment della 0.2.4 l'operatore deve eseguire esattamente un nuovo
  `dic-auth-check --live` autorizzato, con bot fermo e write disabilitate.

## [0.2.3] - 2026-08-16

### Fixed

- Il login DIC attende ora in modo limitato l'hydration dei controlli sulle sole route esatte
  consentite, rifiuta controlli visibili ambigui e mantiene il CAPTCHA sotto verifica durante
  l'attesa. Il pulsante DIC conserva il `data-testid` e aggiunge il fallback semantico pubblico
  verificato sul ruolo `button` con nome esatto `Accedi`.
- Probe di sessione e autenticazione passano dalla coda browser con un solo tentativo e un budget
  dedicato pari al timeout di login più cinque secondi; timeout o errori di trasporto non provocano
  un nuovo invio automatico delle credenziali.
- `dic-auth-check` restituisce per gli errori soltanto JSON con tipo e stage appartenente a un
  insieme chiuso, senza messaggi provider, URL, selettori, tenant, credenziali o contenuti DOM.
- Se il submit della credenziale può essere arrivato a TeamSystem ma completamento, tenant probe o
  persistenza del vault non sono dimostrabili, il risultato è
  `DicAuthOutcomeUnknownError`/`CREDENTIAL_SUBMIT` con exit code 78 e nessun retry. Il comando
  `run` usa lo stesso exit code per ogni errore di autenticazione e l'unit systemd impedisce il
  restart automatico su 78.

### Changed

- La documentazione operativa registra il rinnovo umano della password TeamSystem e
  l'aggiornamento del secret locale. Il tentativo live con la 0.2.2 si è fermato prima
  dell'autenticazione per una race di hydration; sessione, tenant e Function ID DIC restano da
  verificare live, con bot fermo e write disabilitate.

## [0.2.2] - 2026-08-16

### Fixed

- Test e comandi di verifica mock sono ora isolati dal file `.env` operativo e dalle variabili
  ambiente di produzione, evitando che provider, token Discord, chiavi audit o database reali
  alterino i risultati della suite.
- La configurazione mock usa sempre un database SQLite in memoria, nessun segreto runtime e un
  provider sintetico deterministico; la configurazione live resta invariata.
- Aggiunta una regressione che riproduce l'installazione Debian con Groq e alias di tuning in
  conflitto senza caricare né stampare valori sensibili.

## [0.2.1] - 2026-08-16

### Added

- Current TeamSystem multi-step sign-in support for Dipendenti in Cloud, including explicit
  fail-closed handling when interactive password renewal is required.
- Passive current-company attestation during a fixed read-only employee-list navigation, with
  restored browser-session verification before credentials are used.

### Changed

- Debian deployment and operations documentation now records the verified Groq and local runtime
  gates while keeping the unfinished DIC authentication check clearly blocked.
- Shell environment parsing uses a portable `awk` quote expression and no longer emits `mawk`
  escape warnings on Debian 12.

### Security

- Tenant authorization now requires the exact first-party company-info response contract and no
  longer trusts company names or inferred DOM attributes.
- Tenant response bodies, identifiers, URLs and parsing failures are excluded from exception
  chains and structured logs.

## [0.2.0] - 2026-08-15

### Added

- Multi-provider model routing for OpenAI, Groq and a local OpenAI-compatible llama endpoint,
  with canonical shared `MODEL_*` tuning and provider-specific credentials.
- Configurable Italian/English clarification and decoration profile with bounded tone, address
  style, verbosity, status emoji and optional opening/closing text; deterministic operational
  output remains Italian and the profile never changes authorization or tool exposure.
- End-to-end Debian 12/13 installation and operations guide, least-privilege Discord guild setup,
  systemd/PID lifecycle separation, read-only first verification and rollback/incident runbooks.
- Offline-by-default `model-check` command with one explicit, synthetic and closed live provider
  probe that never constructs Discord, DIC or browser services.
- Secure Python 3.12 project foundation and locked direct dependencies.
- Fail-closed Pydantic settings with an explicit isolated mock mode.
- Structured JSON logging with centralized secret and PII redaction.
- Async SQLAlchemy persistence with SQLite WAL support and Alembic migrations.
- Append-only HMAC-SHA256 audit chain with full-chain verification.
- Foundation unit and integration tests using synthetic local data only.

### Security

- All mutating feature flags default to disabled.
- Provider-side model storage is rejected by configuration validation (`MODEL_STORE=false`).
- Groq uses the fixed official OpenAI-compatible base URL; llama HTTP endpoints are restricted to
  loopback and unsafe URL components are rejected.
- Provider transports reject redirects, ambient proxies and unsafe OpenAI SDK environment
  overrides; provider exception bodies are never chained into Discord logs.
- Groq `gsk_` credentials and labeled API keys are redacted before provider and logging boundaries.
- Runtime startup rejects missing secrets, guild/channel identifiers, and unsafe write settings.

[Unreleased]: https://github.com/okno/BH-DiC/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/okno/BH-DiC/compare/v0.2.8...v0.3.0
[0.2.8]: https://github.com/okno/BH-DiC/compare/v0.2.7...v0.2.8
[0.2.7]: https://github.com/okno/BH-DiC/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/okno/BH-DiC/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/okno/BH-DiC/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/okno/BH-DiC/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/okno/BH-DiC/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/okno/BH-DiC/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/okno/BH-DiC/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/okno/BH-DiC/releases/tag/v0.2.0

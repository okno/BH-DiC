# BH-DiC

BH-DiC è un assistente Discord Senior HR per flussi autorizzati nell'area Dipendenti di
Dipendenti in Cloud. Discord raccoglie la richiesta, il provider di modello selezionato
(`openai`, `groq` o `llama`) propone soltanto un intento strutturato e un'applicazione
deterministica applica scope, RBAC, feature flag, approvazioni e controlli prima di invocare
l'adapter browser.

> Stato al 17 agosto 2026: la versione `0.3.0`, SHA esatto
> `c2c1e8da8a7f2aba5cb8a9f679d1251e15cb38fe`, ha superato sul target Debian un unico gate live
> autorizzato in sola lettura. Il gate ha attestato autenticazione/tenant, conteggio aggregato
> `PUBLIC` non-ephemeral, scadenze bounded del prossimo mese di calendario `SENSITIVE`/ephemeral,
> stato API e telemetria token. Non è uno smoke del trasporto Discord: il round-trip slash nel
> canale resta `PENDING`; il servizio è `active/running`, con zero riavvii osservati e gateway
> `discord_ready`. Tutte le write restano
> `DISABLED_BY_POLICY`.

Le evidenze storiche `VERIFIED_BY_ADAPTER`, `TEAMSYSTEM_EMAIL`, restore `sessionStorage`, gateway
`DEGRADED` e primo deny RBAC restano nel report come tappe separate; non sostituiscono né
contraddicono il gate 0.3.0 corrente.

## Uso autorizzato

Il software tratta dati HR sensibili. Può essere usato soltanto nel guild, nel canale e nel
tenant esplicitamente configurati, da persone autorizzate. Non usare dati reali nei test, non
committare `.env`, token, sessioni browser, documenti, screenshot, trace o dump.

## Architettura

```text
Discord -> validazione scope/RBAC -> router modello -> validazione schema/policy
        -> read deterministica oppure preview -> conferma/A1/A2 -> adapter DIC
        -> postcondizione/riconciliazione -> audit append-only
```

Il provider non riceve credenziali, file, primitive browser o facoltà di autorizzazione.
`MODEL_STORE=false` vieta la persistenza richiesta dall'applicazione; i Function ID esposti sono
filtrati prima della richiesta e l'output viene validato nuovamente. La fonte normativa per
Function ID, ruoli, flag e approvazioni è
`src/bh_dic/policies/catalog.py`. Lingua, tono e formula di apertura/chiusura sono configurabili,
ma la persona non modifica policy o superficie operativa.

Il testo destinato al router viene trasformato in etichette di categoria semantica chiuse, non
inoltrato come vocaboli grezzi. Nomi, valori di ricerca ed Employee ID vengono rimossi o sostituiti
prima di OpenAI/Groq/llama; eventuali ID espliciti e query di ricerca vengono conservati soltanto
nel confine locale. Risultati DIC, righe dipendente, DOM e scadenze non vengono mai reinviati al
modello. La risposta amichevole è costruita da un presenter locale sui soli risultati tipizzati
dell'adapter.

## Caratteristiche implementate

- catalogo di 32 Function ID e policy fail-closed;
- router multi-provider OpenAI/Groq/llama limitato all'intento, con minimizzazione identità,
  tuning comune e rendering deterministico locale;
- presenter Senior HR italiano/inglese con tono, indirizzo e verbosità configurabili, separato da
  RBAC e autorizzazioni;
- totale organico con semantica locale esatta (`all` se non qualificato; `active`/`inactive` se
  espliciti) e analisi scadenze del prossimo mese senza query contratto N+1;
- contatori token input/output/totale per richiesta e cumulativi locali, con stato esplicito per
  valori assenti o incerti e migrazione Alembic dedicata;
- kill switch globale `ENABLE_WRITE_ACTIONS=false` e flag specifici tutti `false`;
- preview, conferma monouso hashata, TTL, idempotenza, A1/A2 distinti e riconciliazione;
- adapter mock deterministico e adapter Playwright con tenant guard first-party, osservazione
  passiva della risposta UI `GET /backend_apiV2/employees` e dei metadati paginator sul path
  distinto `/employees`, sotto contratti esatti, schema chiuso e bounded; `current_contract` viene
  accettato per-record soltanto nel keyset `BASE` oppure nel keyset `EXTENDED` completo osservato,
  i cui sei campi tecnici sono validati e scartati senza proiezione; vault cifrato
  cookie/localStorage/`sessionStorage` ripersistito dopo letture verificate e avvio Discord
  degradabile senza submit implicito di credenziali;
- nome visualizzato disponibile in chiaro soltanto come `SecretStr` transitorio per elenchi e
  scadenze `HR_READ` sensibili/ephemeral; aggregati pubblici, provider, log, audit, telemetria e
  dump dei modelli non ricevono il valore; e-mail, codice fiscale e matricola restano mascherati;
- audit HMAC append-only, cifratura dei parametri pending e log JSON redatti;
- quarantena UUID, hash/deduplica, MIME/estensione, ClamAV fail-closed e retention;
- persistenza async SQLite/PostgreSQL, migrazioni Alembic e test sintetici.

La matrice puntuale è in [Feature matrix](docs/FEATURE_MATRIX.md), che separa esplicitamente i due
percorsi read bounded verificati live da tutte le altre modalità ancora da validare.

## Requisiti

- Python 3.12 o successivo;
- Linux per il deployment operativo e Bash per gli script;
- Chromium gestito da Playwright;
- ClamAV per gli upload;
- accesso autorizzato a Discord, al provider scelto e a Dipendenti in Cloud;
- SQLite locale o PostgreSQL tramite driver async.

## Installazione rapida per sviluppo isolato

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --requirement requirements.lock
python -m pip install --editable .
python -m playwright install chromium
APP_ENV=test MOCK_MODE=true python -m pytest
```

Per il server seguire [Installazione](docs/INSTALLATION.md) e
[Deployment](docs/DEPLOYMENT.md). Il target verificato eseguiva la versione `0.3.0` allo SHA esatto
riportato nello stato live; applicare sempre la migrazione `0002_model_usage` su altre
installazioni. Invalidare il vault esclusivamente dopo rotazione o compromissione documentata; un
semplice upgrade non richiede un nuovo login. Il gateway può essere avviato `DEGRADED` se la
sessione verificata non è disponibile. Nello snapshot corrente il servizio target è fermo: il
prossimo passo autorizzato è lo smoke del trasporto Discord, non l'abilitazione delle write.

## Configurazione e operatività

```bash
cp .env.example .env
chmod 600 .env
${EDITOR:-nano} .env
./scripts/doctor.sh
.venv/bin/python -m bh_dic model-check
```

Non impostare mai `MODEL_STORE=true`. Per una scrittura non basta il flag specifico: devono
essere veri anche il kill switch globale e ogni precondizione di policy; le funzioni critiche
richiedono due approvatori distinti. In questo rilascio le scritture live non sono autorizzate.

Le interfacce operative richieste sono:

```bash
./scripts/start.sh
./scripts/status.sh
./scripts/logs.sh all --follow
./scripts/stop.sh
```

Consultare [Start/stop](docs/START_STOP.md) per disponibilità verificata e semantica dei
comandi. Non avviare il bot finché `doctor.sh` non termina con successo e le credenziali non
sono state fornite per canale sicuro.

Con `DISCORD_READONLY_ROLE_IDS=<DISCORD_GUILD_ID>`, ogni membro del solo canale allowlistato può
chiedere un aggregato non sensibile, per esempio `/bh ask richiesta:Dimmi il totale dei
dipendenti`; il risultato finale viene pubblicato nel canale. `/bh ask richiesta:Dimmi i dipendenti
con contratto a scadenza nel prossimo mese` richiede invece un ruolo umano dedicato `HR_READ` e
resta ephemeral perché contiene dettagli individuali. L'ownership Discord non sostituisce questi
ruoli applicativi.

`model-check` è offline per default. Solo con autorizzazione esplicita a rete/costo usare
`model-check --live`: esegue una singola richiesta sintetica chiusa al provider, senza DIC,
Discord, browser o tool.

## Test e gate

```bash
ruff format --check .
ruff check .
mypy src
pytest
bandit -r src
pip-audit
gitleaks detect
```

I risultati realmente osservati e gli eventuali blocchi sono registrati in
[Implementation report](docs/IMPLEMENTATION_REPORT.md), non dedotti dalla sola presenza dei
tool. Vedere anche [Testing](docs/TESTING.md).

## Sicurezza e limiti

- Il progetto è alpha. Il gate live copre soltanto conteggio aggregato e scadenze bounded del
  prossimo mese di calendario; ricerca, altre letture e altri intervalli restano da validare.
- Lo smoke del trasporto Discord 0.3.0 è ancora `PENDING`; nello snapshot documentato il servizio
  è `active/running`, con zero riavvii osservati e gateway `discord_ready`.
- MFA, CAPTCHA e UI drift possono impedire l'automazione.
- Nessuna write live è stata eseguita; i percorsi write sono `TESTED_WITH_MOCK` e
  `DISABLED_BY_POLICY`.
- Gli esempi YAML non sostituiscono il catalogo e i controlli nel codice.
- Dalla 0.2.7 il gateway Discord non invia credenziali DIC durante l'avvio: se la sessione cifrata
  è assente, scaduta o illeggibile resta online in modalità `DEGRADED`, mentre le funzioni DIC
  falliscono chiuso e il vault non viene sovrascritto.
- La 0.2.8 limita le transizioni federate alla root e-mail TeamSystem e alle route OIDC esatte. Un
  SSO silenzioso non tocca controlli credenziali ed è valido soltanto dopo marker DIC e tenant
  attestato. Dopo una rotazione credenziale il vecchio vault va invalidato deliberatamente una
  volta; un esito `CREDENTIAL_SUBMIT` non deve essere ritentato.
- La 0.3.0 ripersiste la sessione soltanto dopo stato autenticato e tenant attestato o dopo una
  lettura riuscita; errori e stati ignoti non sovrascrivono il vault.
- Prima di qualunque smoke read-only verificare il ruolo Discord dell'operatore. La creazione del
  bot o l'ownership del guild non sostituiscono la mappa RBAC.

Approfondimenti: [architettura di sicurezza](docs/SECURITY_ARCHITECTURE.md),
[privacy](docs/PRIVACY_GDPR.md), [audit](docs/AUDIT.md),
[gestione file](docs/FILE_HANDLING.md) e [troubleshooting](docs/TROUBLESHOOTING.md).

Setup e confini delle integrazioni: [autenticazione DIC](docs/DIC_AUTHENTICATION.md),
[Discord](docs/DISCORD_SETUP.md), [provider di modello](docs/OPENAI_SETUP.md) e
[threat model](docs/THREAT_MODEL.md). Per la manutenzione dell'adapter consultare
[baseline di ricognizione](docs/RECONNAISSANCE_BASELINE.md),
[manutenzione selettori](docs/SELECTOR_MAINTENANCE.md),
[stato di verifica live](docs/LIVE_VERIFICATION_STATUS.md) e
[limitazioni note](docs/KNOWN_LIMITATIONS.md).

La repository è pubblica per scelta esplicita del titolare. Il tree tracciato corrente contiene
soltanto sorgenti, configurazioni di esempio e fixture sintetiche; l'implementation report segnala
separatamente un'identità e-mail nei metadati Git storici. Segreti, identificatori operativi, stato runtime e
PII devono restare fuori da Git. La pubblicazione del codice non aggiunge automaticamente
una licenza open source: non aggiungerne una senza autorizzazione.

# BH-DiC

BH-DiC è un assistente Discord per flussi HR autorizzati nell'area Dipendenti di
Dipendenti in Cloud. Discord raccoglie la richiesta, il provider di modello selezionato
(`openai`, `groq` o `llama`) propone soltanto un intento strutturato e un'applicazione
deterministica applica scope, RBAC, feature flag, approvazioni e controlli prima di invocare
l'adapter browser.

> Stato al 17 agosto 2026: Debian 12 e Groq `openai/gpt-oss-120b` sono verificati. Un check DIC
> headless 0.2.5 ha restituito sessione `AUTHENTICATED` e tenant `VERIFIED_BY_ADAPTER`; la 0.2.7 è
> stata poi distribuita, ma il check corrente sul server si è fermato fail-closed a
> `TEAMSYSTEM_EMAIL`. La candidata 0.2.8 riconosce l'ingresso e-mail TeamSystem corrente sulla root
> esatta, le transizioni OIDC esatte e il completamento SSO senza credenziali soltanto dopo marker
> applicativo e attestazione tenant; i gate locali sono verdi e la verifica live resta `PENDING`.
> Il gateway
> resta separato dal login DIC. Nessuna Function ID live è collaudata e tutte le write restano
> `DISABLED_BY_POLICY`.

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

## Caratteristiche implementate

- catalogo di 32 Function ID e policy fail-closed;
- router multi-provider OpenAI/Groq/llama con tuning comune e rendering deterministico;
- profilo lingua italiano/inglese per chiarimenti/decorazioni; dati operativi restano in italiano
  e il profilo è separato da RBAC e autorizzazioni;
- kill switch globale `ENABLE_WRITE_ACTIONS=false` e flag specifici tutti `false`;
- preview, conferma monouso hashata, TTL, idempotenza, A1/A2 distinti e riconciliazione;
- adapter mock deterministico e adapter Playwright con tenant guard basato su attestazione
  passiva first-party, vault cifrato cookie/localStorage/`sessionStorage` e avvio Discord degradabile
  senza submit implicito di credenziali; le funzioni HR Playwright non sono ancora validate live;
- audit HMAC append-only, cifratura dei parametri pending e log JSON redatti;
- quarantena UUID, hash/deduplica, MIME/estensione, ClamAV fail-closed e retention;
- persistenza async SQLite/PostgreSQL, migrazioni Alembic e test sintetici.

La matrice puntuale è in [Feature matrix](docs/FEATURE_MATRIX.md). Nessuna riga della matrice
costituisce prova di verifica live.

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
[Deployment](docs/DEPLOYMENT.md). Il check DIC corrente della 0.2.7 si è fermato a
`TEAMSYSTEM_EMAIL`; distribuire la candidata 0.2.8 soltanto dopo i gate, invalidare una sola volta
il vault creato prima della rotazione della credenziale e lanciare esattamente un check live. Il
gateway può essere avviato `DEGRADED` anche se il check DIC non riesce.

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

- Il progetto è alpha. La struttura di login e il contratto di attestazione tenant sono stati
  osservati in ricognizione read-only, ma le funzioni HR non sono state validate live.
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

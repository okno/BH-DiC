# BH-DiC

BH-DiC è un assistente Discord per flussi HR autorizzati nell'area Dipendenti di
Dipendenti in Cloud. Discord raccoglie la richiesta, il provider di modello selezionato
(`openai`, `groq` o `llama`) propone soltanto un intento strutturato e un'applicazione
deterministica applica scope, RBAC, feature flag, approvazioni e controlli prima di invocare
l'adapter browser.

> Stato al 16 agosto 2026: la preparazione su Debian 12 è riuscita, inclusi Python 3.12,
> dipendenze, migrazione, Chromium Playwright, ClamAV e doctor offline/online. Groq con
> `openai/gpt-oss-120b` ha superato `model-check --live`. Il servizio è fermo: la verifica
> autenticata DIC headless non è completa perché la password TeamSystem risulta scaduta e non
> esiste ancora un vault di sessione valido. Nessuna funzione read o write del bot è stata
> collaudata sul tenant live; tutte le write restano `DISABLED_BY_POLICY`.

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
  passiva first-party; le funzioni HR Playwright non sono ancora validate live;
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
[Deployment](docs/DEPLOYMENT.md). La preparazione Debian è stata eseguita; l'attivazione resta
sospesa fino alla verifica DIC e alla registrazione controllata dei comandi Discord.

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
- Il bot sul target è fermo; non avviarlo finché `dic-auth-check --live` non attesta sessione e
  tenant e non crea il vault cifrato.

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

La repository deve restare privata. Non aggiungere una licenza open source senza
autorizzazione.

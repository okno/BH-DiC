# BH-DiC

BH-DiC è un assistente Discord per flussi HR autorizzati nell'area Dipendenti di
Dipendenti in Cloud. Discord raccoglie la richiesta, OpenAI propone soltanto un intento
strutturato e un'applicazione deterministica applica scope, RBAC, feature flag, approvazioni e
controlli prima di invocare l'adapter browser.

> Stato al 14 agosto 2026: repository privata configurata; deployment server **bloccato**
> perché utente e chiave SSH non sono disponibili; bot **non avviato nel workspace locale** e
> stato del processo sul target **UNVERIFIED**; nessuna lettura o
> scrittura verificata sul tenant live. Le scritture sono disponibili soltanto nel percorso
> mock/test e restano disabilitate per impostazione predefinita.

## Uso autorizzato

Il software tratta dati HR sensibili. Può essere usato soltanto nel guild, nel canale e nel
tenant esplicitamente configurati, da persone autorizzate. Non usare dati reali nei test, non
committare `.env`, token, sessioni browser, documenti, screenshot, trace o dump.

## Architettura

```text
Discord -> validazione scope/RBAC -> router OpenAI -> validazione schema/policy
        -> read deterministica oppure preview -> conferma/A1/A2 -> adapter DIC
        -> postcondizione/riconciliazione -> audit append-only
```

OpenAI non riceve credenziali, file, primitive browser o facoltà di autorizzazione. La chiamata
provider usa `store=false`; i Function ID esposti sono filtrati prima della richiesta e l'output
viene validato nuovamente. La fonte normativa per Function ID, ruoli, flag e approvazioni è
`src/bh_dic/policies/catalog.py`.

## Caratteristiche implementate

- catalogo di 32 Function ID e policy fail-closed;
- kill switch globale `ENABLE_WRITE_ACTIONS=false` e flag specifici tutti `false`;
- preview, conferma monouso hashata, TTL, idempotenza, A1/A2 distinti e riconciliazione;
- adapter mock deterministico e adapter Playwright non validato live;
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
- accesso autorizzato a Discord, OpenAI e Dipendenti in Cloud;
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
[Deployment](docs/DEPLOYMENT.md). Il deployment corrente non è stato eseguito.

## Configurazione e operatività

```bash
cp .env.example .env
chmod 600 .env
${EDITOR:-nano} .env
./scripts/doctor.sh
```

Non impostare mai `OPENAI_STORE=true`. Per una scrittura non basta il flag specifico: devono
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

- Il progetto è alpha e non è stato validato contro l'interfaccia live corrente.
- MFA, CAPTCHA e UI drift possono impedire l'automazione.
- Nessuna write live è stata eseguita; i percorsi write sono `TESTED_WITH_MOCK` e
  `DISABLED_BY_POLICY`.
- Gli esempi YAML non sostituiscono il catalogo e i controlli nel codice.
- Nessun processo bot o Chromium è residuo nel workspace locale; lo stato del target non è
  attestabile finché l'accesso SSH resta bloccato.

Approfondimenti: [architettura di sicurezza](docs/SECURITY_ARCHITECTURE.md),
[privacy](docs/PRIVACY_GDPR.md), [audit](docs/AUDIT.md),
[gestione file](docs/FILE_HANDLING.md) e [troubleshooting](docs/TROUBLESHOOTING.md).

Setup e confini delle integrazioni: [autenticazione DIC](docs/DIC_AUTHENTICATION.md),
[Discord](docs/DISCORD_SETUP.md), [OpenAI](docs/OPENAI_SETUP.md) e
[threat model](docs/THREAT_MODEL.md). Per la manutenzione dell'adapter consultare
[baseline di ricognizione](docs/RECONNAISSANCE_BASELINE.md),
[manutenzione selettori](docs/SELECTOR_MAINTENANCE.md),
[stato di verifica live](docs/LIVE_VERIFICATION_STATUS.md) e
[limitazioni note](docs/KNOWN_LIMITATIONS.md).

La repository deve restare privata. Non aggiungere una licenza open source senza
autorizzazione.

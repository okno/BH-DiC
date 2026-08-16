# Installazione, attivazione e runbook Debian

Questa è la guida canonica end-to-end per preparare BH-DiC su Debian 12 o 13, configurare Discord
e il provider di modello, validare in mock e attivare inizialmente le sole letture. I documenti
specialistici collegati approfondiscono i singoli controlli.

> Stato al 17 agosto 2026: il runtime Debian 12 e Groq `openai/gpt-oss-120b` sono verificati; il
> bot è fermo. Un login manuale autorizzato in browser fresco ha accettato le credenziali con un
> solo submit e raggiunto callback, dashboard, route e marker esatti della lista dipendenti. Il
> check server 0.2.4 ha rifiutato quella callback legittima con exit 78; la 0.2.5 corregge il
> passaggio, ma adapter headless, tenant e vault server non sono ancora verificati. Nessuna Function
> ID DIC è stata collaudata live. Tutte le write
> devono restare `DISABLED_BY_POLICY`; questa guida non autorizza l'avvio.

## 1. Decisioni prima dell'installazione

Registrare in un change ticket approvato, senza segreti:

- commit e branch da distribuire dalla repository `okno/BH-DiC`; la visibilità deve risultare
  `PRIVATE` tramite API prima dell'avvio di produzione (al 17 agosto 2026 risulta `PUBLIC`);
- amministratore responsabile, finestra e piano di rollback;
- host, filesystem cifrato, backup e retention;
- guild Discord `1303955635984924722`, Channel ID copiato da `#mng-ai` e Role ID approvati;
- provider `openai`, `groq` o `llama`, modello e budget/limiti;
- tenant DIC atteso e identità di servizio a privilegi minimi;
- gestore processo scelto: **systemd** oppure **script PID**, mai entrambi.

Non usare dati HR reali per installazione o smoke test. Verificare la fingerprint SSH dell'host
fuori banda e non disabilitare host-key checking.

## 2. Sistema operativo e Python

BH-DiC richiede Python 3.12 o successivo. Debian 13 è il percorso di riferimento: installare i
pacchetti mantenuti dalla distribuzione e verificare comunque la versione effettiva.

```bash
sudo apt-get update
sudo apt-get install --yes \
  bash ca-certificates git openssh-client tar curl acl util-linux \
  python3 python3-venv python3-dev \
  libmagic1 clamav clamav-daemon clamav-freshclam
python3 --version
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'
```

Debian 12 stock fornisce Python 3.11, quindi `apt install python3` **non è sufficiente**. Prima di
proseguire, l'amministratore deve rendere disponibile un CPython 3.12+ mantenuto e approvato
dall'organizzazione, inclusi `venv` e i componenti di compilazione necessari. Non sostituire il
Python di sistema, non aggiungere repository Ubuntu/deadsnakes e non usare installer
`curl | bash`. Il gate deve riuscire con il binario approvato, per esempio:

```bash
python3.12 --version
python3.12 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'
```

Se nessuno tra `python3.14`, `python3.13`, `python3.12` o `python3` supera il gate, interrompere
l'installazione. `scripts/install.sh` ripete questa ricerca e fallisce chiuso.

Mantenere Debian, Python, ClamAV e librerie Chromium aggiornati tramite il processo patching
aziendale. Non disabilitare TLS o verifica delle firme per risolvere un errore di pacchetto.

## 3. Utente e directory dedicati

Creare un account senza login e directory separate per home e codice:

```bash
sudo adduser --system --group --home /var/lib/bh-dic \
  --shell /usr/sbin/nologin bh-dic
sudo install -d -o bh-dic -g bh-dic -m 0700 /var/lib/bh-dic
sudo install -d -o bh-dic -g bh-dic -m 0750 /opt/bh-dic
```

Non eseguire il bot come `root`. Target attesi: applicazione `0750`, directory dati `0700`,
`.env` e backup `0600`, log `0640` o più restrittivi. La home separata evita di mescolare cache
runtime e working tree.

## 4. Clone privato verificato

Configurare per `bh-dic` una deploy key read-only o un credential helper approvato. Verificare
prima la host key GitHub e non inserire token nell'URL. Quindi:

```bash
sudo -u bh-dic -H git clone git@github.com:okno/BH-DiC.git /opt/bh-dic
cd /opt/bh-dic
sudo -u bh-dic -H git remote -v
sudo -u bh-dic -H git status --short --branch
sudo -u bh-dic -H git rev-parse HEAD
```

Confrontare lo SHA con quello approvato. Il remote non deve contenere credenziali. Prima dell'avvio
rendere e mantenere la repository privata; non copiare deploy key o output sensibile nel report.

## 5. Dipendenze Python, Playwright e ClamAV

Creare ambiente e dipendenze senza avviare il bot. Il primo passaggio omette solo il browser:

```bash
sudo -u bh-dic -H /bin/bash -c \
  'cd /opt/bh-dic && ./scripts/install.sh --skip-browser'
```

Installare come amministratore le librerie native richieste da Chromium, poi il browser nella
cache dell'utente di servizio:

```bash
sudo /opt/bh-dic/.venv/bin/python -m playwright install-deps chromium
sudo -u bh-dic -H /opt/bh-dic/scripts/browser-install.sh
```

Non eseguire `playwright install` come root: il binario finirebbe nella cache dell'utente errato.
`browser-install.sh --with-deps` è utile soltanto su un host dove l'utente che lo invoca è
autorizzato anche a installare pacchetti OS; la sequenza separata sopra rende esplicita la
divisione dei privilegi.

Abilitare il daemon antivirus e consentire all'account di servizio l'accesso al socket secondo il
packaging Debian:

```bash
sudo systemctl enable --now clamav-freshclam.service clamav-daemon.service
sudo usermod --append --groups clamav bh-dic
sudo -u bh-dic -H clamdscan --version
```

Verificare sul target il path e i permessi del socket; valorizzare `CLAMAV_SOCKET` solo se il
default non viene rilevato. Se ClamAV o il socket non sono disponibili, mantenere
`ENABLE_DOCUMENT_UPLOAD=false`: con `CLAMAV_REQUIRED=true` l'upload fallisce chiuso.

## 6. Creare `.env` senza segreti nella cronologia

```bash
cd /opt/bh-dic
sudo -u bh-dic -H ./scripts/init-config.sh
sudo chmod 600 .env
sudo -u bh-dic -H ${EDITOR:-nano} .env
```

`init-config.sh` rifiuta di sovrascrivere un file esistente. Compilare `.env` localmente da una
console amministrativa protetta. Non usare `export SECRET=...`, argomenti CLI o heredoc registrati
nella history.

Baseline obbligatoria per prima attivazione:

```dotenv
APP_ENV=production
MOCK_MODE=false
MODEL_STORE=false
MODEL_RESULT_RENDERING=deterministic
DISCORD_GUILD_ID=1303955635984924722
DISCORD_CHANNEL_ID=<ID_COPIATO_DA_MNG_AI>
DISCORD_INTERACTION_MODE=slash
DISCORD_ALLOW_DMS=false
ENABLE_READ_ACTIONS=true
ENABLE_WRITE_ACTIONS=false
ENABLE_LIVE_WRITE_TESTS=false
REQUIRE_TWO_PERSON_APPROVAL=true
CLAMAV_REQUIRED=true
SAVE_FAILURE_SCREENSHOTS=false
PLAYWRIGHT_TRACE_MODE=off
```

Tutti i flag `ENABLE_*` specifici di write devono rimanere `false`. Vedere
[Configuration](CONFIGURATION.md) per ruoli, chiavi di almeno 32 byte, database, DIC e persona.

### Scegliere un provider

OpenAI:

```dotenv
MODEL_PROVIDER=openai
OPENAI_API_KEY=<SEGRETO_LOCALE>
OPENAI_MODEL=<MODELLO_APPROVATO>
```

Groq:

```dotenv
MODEL_PROVIDER=groq
GROQ_API_KEY=<SEGRETO_LOCALE>
GROQ_MODEL=openai/gpt-oss-120b
```

llama locale OpenAI-compatible:

```dotenv
MODEL_PROVIDER=llama
LLAMA_BASE_URL=http://127.0.0.1:11434/v1
LLAMA_MODEL=<MODELLO_LOCALE_INSTALLATO>
# LLAMA_API_KEY=<SEGRETO_OPZIONALE>
```

Parametri comuni:

```dotenv
MODEL_TIMEOUT_SECONDS=60
MODEL_MAX_RETRIES=2
MODEL_MAX_OUTPUT_TOKENS=1200
MODEL_REASONING_EFFORT=low
MODEL_STORE=false
MODEL_RESULT_RENDERING=deterministic
```

Le base URL OpenAI e Groq sono fisse nel codice a `https://api.openai.com/v1` e
`https://api.groq.com/openai/v1`. L'URL llama HTTP è ammessa soltanto su loopback, con path `/v1`;
un endpoint HTTPS remoto richiede `LLAMA_API_KEY`. Configurazione, criteri e fonti ufficiali sono in
[Provider di modello](OPENAI_SETUP.md).

### Configurare la persona

```dotenv
BOT_LANGUAGE=it
BOT_TONE=professional
BOT_ADDRESS_STYLE=lei
BOT_VERBOSITY=standard
BOT_EMOJI_MODE=off
BOT_DISPLAY_NAME=BH-DiC
BOT_OPENING=
BOT_CLOSING=
```

La persona cambia soltanto chiarimenti e decorazioni; dati/output operativi restano in italiano.
Non amplia tool, ruoli o azioni e non trasforma BH-DiC in un bot generalista o di moderazione.

## 7. Discord e preparazione guild-scoped

Nel Discord Developer Portal creare app e bot, lasciare disabilitati gli intent privilegiati e
installare nel solo guild `1303955635984924722` con gli scope `applications.commands` e `bot`.
Usare i permessi minimi View Channel, Send Messages ed Embed Links (`19456`). Copiare il Channel
ID da `#mng-ai` con Developer Mode; il nome del canale non è un ID.

La procedura esatta, l'install URL guild-locked e la mappa RBAC sono in
[Configurazione Discord](DISCORD_SETUP.md). Con configurazione completa e bot fermo:

```bash
cd /opt/bh-dic
sudo -u bh-dic -H ./scripts/doctor.sh
sudo -u bh-dic -H ./scripts/status.sh
```

Rinviare `register-commands.sh` finché autenticazione e tenant DIC non sono verificati. La
registrazione non avvia il gateway e non esegue richieste DIC, ma rende i comandi visibili nel
guild e appartiene quindi alla fase di attivazione controllata.

## 8. Gate offline e smoke mock

Prima di qualsiasi rete applicativa:

```bash
cd /opt/bh-dic
sudo -u bh-dic -H ./scripts/doctor.sh
sudo -u bh-dic -H ./scripts/audit-verify.sh
sudo -u bh-dic -H ./scripts/status.sh
sudo -u bh-dic -H .venv/bin/python -m bh_dic run --mock --check-only
sudo -u bh-dic -H .venv/bin/python -m bh_dic model-check
```

Il check mock costruisce la slice locale, non apre Discord/DIC e non autorizza write. Conservare
exit code, timestamp, commit e safe summary; non conservare `.env`, prompt o risposte complete.
`model-check` senza `--live` non usa rete e riporta provider/modello/scope con stato
`UNVERIFIED_OFFLINE`.

Soltanto dopo autorizzazione esplicita a connettività e costo provider:

```bash
sudo -u bh-dic -H ./scripts/doctor.sh --online
sudo -u bh-dic -H .venv/bin/python -m bh_dic model-check --live
```

Il doctor online seleziona l'host OpenAI/Groq/llama configurato ma prova soltanto DNS/HTTP, non
l'autenticazione. `model-check --live` invia una sola richiesta sintetica senza PII, espone zero
Function ID e accetta soltanto `unsupported_request`; non costruisce Discord, DIC o browser e non
esegue tool. `LIVE_VERIFIED` vale esclusivamente per il provider/modello in quel momento. Nessuno
dei due comandi prova login DIC, selettori live o deployment completo.

### Gate DIC prima dell'attivazione

Con servizio fermo e tutte le write disabilitate, il controllo offline è utile soltanto quando
esiste già un vault cifrato da validare:

```bash
sudo -u bh-dic -H .venv/bin/python -m bh_dic dic-auth-check
```

Su una prima installazione, dopo una rotazione credenziali o dopo `invalidate-session`, l'assenza
del vault fa fallire correttamente questo comando: non è un errore di login e non deve precedere il
bootstrap live.

Se la password TeamSystem è scaduta, un amministratore deve rinnovarla nel flusso umano normale,
aggiornare `DIC_PASSWORD` localmente senza mostrarla e invalidare l'eventuale sessione precedente.
Sul target documentato questi due passaggi sono già stati completati. Distribuire la release
0.2.5 e, soltanto dopo il deployment, con autorizzazione esplicita alla rete DIC, eseguire una
sola verifica:

```bash
sudo -u bh-dic -H .venv/bin/python -m bh_dic invalidate-session
sudo -u bh-dic -H .venv/bin/python -m bh_dic dic-auth-check --live
sudo -u bh-dic -H ./scripts/status.sh
```

Il check live prova prima una sessione restaurata mediante la route fissa della lista dipendenti,
osserva passivamente l'attestazione tenant first-party e segue, solo se necessario, l'allowlist
esatta del login TeamSystem. Il probe di una route applicativa già ripristinata usa soltanto il
budget residuo e segnala `SESSION_PROBE` se non può concludersi entro la deadline. Non esegue
Function ID HR. Password scaduta, MFA/CAPTCHA, redirect
inatteso o attestazione non valida impongono stop. Dalla 0.2.3 il polling dei controlli è limitato,
route-aware e richiede un solo controllo visibile; status e autenticazione sono eseguiti una volta
sola, senza retry automatico delle credenziali. Nella 0.2.4 il campo e-mail DIC è inoltre ristretto
all'unico input nativo sotto il contenitore pubblico `data-testid="login-email"`: non usa il
placeholder che nella 0.2.3 corrispondeva anche al componente padre. La 0.2.5 ammette l'esatta
`/it/callback` DIC soltanto come transitoria entro il budget condiviso: la query opaca non viene
letta o registrata e le varianti di origine, porta, fragment o path restano rifiutate. Attende il
marker autenticato senza rendere opzionale `/data/company/id`; lo user agent Chromium nativo resta
invariato. Un errore mostra soltanto JSON
`error_type`/`stage`: non stampare eccezioni interne, HTML o URL e non ripetere il comando in loop.
`DicAuthOutcomeUnknownError`/`CREDENTIAL_SUBMIT` usa exit code 78 e indica che il submit può essere
partito senza che completamento, tenant o vault siano dimostrabili: fermarsi e verificare con una
procedura umana, senza un nuovo login.
Solo dopo esito autenticazione/tenant positivo:

```bash
sudo -u bh-dic -H ./scripts/register-commands.sh
```

## 9. Scegliere un solo gestore di processo

### Opzione A: systemd, raccomandata per il server

Revisionare l'unit di esempio, mantenendo `User=bh-dic`, hardening e path `/opt/bh-dic`:

```bash
sudo install -o root -g root -m 0644 \
  /opt/bh-dic/infrastructure/systemd/bh-dic.service.example \
  /etc/systemd/system/bh-dic.service
grep -qxF 'RestartPreventExitStatus=78' /etc/systemd/system/bh-dic.service
grep -qxF 'ConditionPathExists=/opt/bh-dic/.env' /etc/systemd/system/bh-dic.service
grep -qxF 'ExecCondition=/usr/bin/test -f /opt/bh-dic/.env' /etc/systemd/system/bh-dic.service
! grep -q '^ConditionPathIsRegularFile=' /etc/systemd/system/bh-dic.service
sudo systemd-analyze verify /etc/systemd/system/bh-dic.service
sudo systemctl daemon-reload
```

`RestartPreventExitStatus=78` è obbligatorio insieme a `Restart=on-failure`: il comando `run`
restituisce 78 per ogni errore di autenticazione e systemd non deve riavviare automaticamente il
processo, perché ciò potrebbe reinviare credenziali. Dopo ogni aggiornamento del template,
ricopiare e rivalidare l'unit a servizio fermo prima di abilitarla.

Su Debian 12 `ConditionPathIsRegularFile` non è una direttiva systemd supportata. L'unit dalla
0.2.4 usa quindi `ConditionPathExists` come condizione di unit e `/usr/bin/test -f` come
`ExecCondition` del servizio. Questi controlli provano esistenza e tipo del file; non sostituiscono
`doctor.sh`, che continua a richiedere `.env` in modalità `0600` e una configurazione runtime
valida.

La preparazione termina qui con il servizio disabled/stopped. Dopo autorizzazione distinta:

```bash
sudo systemctl start bh-dic.service
sudo systemctl status bh-dic.service --no-pager
sudo journalctl -u bh-dic.service --since today --no-pager
```

Abilitare l'avvio al boot solo dopo uno smoke riuscito:

```bash
sudo systemctl enable bh-dic.service
```

Stop e restart:

```bash
sudo systemctl stop bh-dic.service
sudo systemctl restart bh-dic.service
```

In modalità systemd non usare `start.sh`, `stop.sh` o `restart.sh`: quegli script gestiscono un
PID file proprio e possono divergere dallo stato osservato da systemd. Per processo e log usare
`systemctl`/`journalctl`; gli script di audit, file, backup e doctor restano utilizzabili a
servizio fermo quando previsto.

### Opzione B: script PID, per sessioni controllate

Verificare che l'unit systemd non sia abilitata né attiva, quindi:

```bash
sudo -u bh-dic -H ./scripts/start.sh
sudo -u bh-dic -H ./scripts/status.sh
sudo -u bh-dic -H ./scripts/logs.sh all --follow
sudo -u bh-dic -H ./scripts/stop.sh
```

In questa modalità non usare `systemctl start bh-dic`. Dettagli su lock, timeout, `--force` e
foreground in [Start/stop](START_STOP.md).

## 10. Prima attivazione: sola lettura

Prima dell'avvio rieseguire e registrare il gate:

```bash
grep -E '^ENABLE_' .env
./scripts/doctor.sh
./scripts/audit-verify.sh
```

L'output atteso contiene soltanto `ENABLE_READ_ACTIONS=true`; kill switch, live test e ogni flag
specifico devono essere `false`. Non stampare altre righe di `.env`. Avviare con il gestore scelto,
quindi:

1. verificare processo, errori di bootstrap e guild/canale;
2. eseguire `/bh help`, `/bh status` o `/bh health` con un account sintetico autorizzato;
3. verificare casi deny da altro canale, DM e ruolo non autorizzato;
4. se autorizzato, eseguire una sola lettura su tenant e record sintetici/pre-approvati;
5. verificare log redatti, catena audit e assenza di browser/processi residui dopo lo stop.

Non usare una write come smoke test. Nessun risultato ottenuto su mock, Discord o provider
promuove automaticamente lo stato DIC live. Aggiornare lo [stato di verifica
live](LIVE_VERIFICATION_STATUS.md) solo con evidenza ripetibile.

## 11. Operazioni, log e audit

Script sicuri comuni, eseguiti come `bh-dic`:

```bash
./scripts/healthcheck.sh --process-only
./scripts/logs.sh all
./scripts/logs.sh security --since 2026-08-15T00:00:00Z --level WARNING
./scripts/logs.sh all --correlation-id <CORRELATION_ID>
./scripts/files.sh list
./scripts/files.sh metadata <UPLOAD_UUID>
./scripts/audit-verify.sh
```

`logs.sh` applica un ulteriore strato di redazione. Non inoltrare log grezzi in chat. Un fallimento
della catena audit impone stop, preservazione dell'evidenza ed escalation; non modificare il DB
per “ripararla”. Lo SHA-256 di un upload è visibile soltanto a un operatore locale mediante
`files.sh metadata`; non appare in eventi, log, Discord o richieste provider.

Vedere [Operations](OPERATIONS.md), [Logging](LOGGING.md), [Audit](AUDIT.md) e
[File handling](FILE_HANDLING.md).

## 12. Backup, aggiornamento e restore

L'implementazione applicativa corrente gestisce backup/restore soltanto per SQLite. Prima di un
upgrade fermare il servizio con il gestore scelto, quindi:

```bash
cd /opt/bh-dic
./scripts/audit-verify.sh
./scripts/backup.sh
sha256sum var/backups/<BACKUP>.tar.gz
./scripts/update.sh
./scripts/doctor.sh
./scripts/audit-verify.sh
```

`update.sh` richiede working tree pulito e upstream, crea a sua volta un backup, esegue fetch con
timeout, accetta solo fast-forward, reinstalla dipendenze, migra e testa. A servizio fermo non
riavvia automaticamente. In modalità systemd non usare `update.sh --restart`; fare stop/update/gate
e start separati. In modalità PID, `--restart` è consentito soltanto se un change approvato
richiede esplicitamente il riavvio controllato.

Restore SQLite, sempre con servizio fermo e approvazione:

```bash
./scripts/backup.sh
./scripts/restore.sh var/backups/<BACKUP>.tar.gz --confirm RESTORE
./scripts/audit-verify.sh
./scripts/doctor.sh
```

Il risultato atteso resta `stopped`. `.env`, sessioni, upload e log non sono ripristinati. Provare
periodicamente restore e RPO/RTO su host isolato con dati sintetici. Dettagli in
[Backup/restore](BACKUP_RESTORE.md).

## 13. Upgrade e rollback

Per ogni release:

1. acquisire SHA approvato, changelog e risultati gate definitivi;
2. verificare backup e audit prima del cambio;
3. fermare il gestore processo;
4. eseguire update fast-forward, migrazioni e test;
5. rieseguire doctor offline, smoke mock e audit;
6. avviare prima con write off e verificare una lettura sintetica autorizzata;
7. promuovere oppure fermare e applicare il rollback approvato.

Se codice o migrazione falliscono, non forzare l'avvio. Ripristinare il backup verificato con la
procedura sopra e riportare il working tree al commit approvato tramite il normale processo Git;
non usare reset distruttivi improvvisati. Una write incerta va riconciliata, mai ripetuta
automaticamente.

## 14. Rotazione chiavi e risposta incidenti

Rotazione pianificata:

1. fermare il bot;
2. creare backup che escluda `.env` e verificare audit;
3. creare la nuova credenziale: `DISCORD_BOT_TOKEN`, `OPENAI_API_KEY`, `GROQ_API_KEY`,
   `LLAMA_API_KEY` quando usata, oppure la credenziale DIC interessata;
4. aggiornare `.env` localmente, modo `0600`;
5. invalidare la sessione DIC quando pertinente;
6. eseguire doctor, smoke e verifica log;
7. revocare la credenziale precedente e riavviare solo dopo approvazione.

Una rotazione `AUDIT_HMAC_KEY` richiede checkpoint e procedura di chain rollover documentati; non
sostituire semplicemente la chiave.

In caso di sospetta compromissione:

1. impostare o confermare `ENABLE_WRITE_ACTIONS=false` e fermare il servizio;
2. revocare token/chiavi e invalidare sessioni coinvolte;
3. preservare DB, audit, log e host snapshot con accesso read-only;
4. eseguire audit verify senza alterare l'evidenza;
5. correlare eventi tramite ID redatti, senza esportare PII;
6. ripristinare soltanto da backup verificato;
7. riabilitare prima le sole read dopo decisione dell'incident owner.

Per sintomi e percorsi di escalation vedere [Troubleshooting](TROUBLESHOOTING.md) e
[Debugging](DEBUGGING.md).

## 15. Checklist di handoff

- [ ] Debian patchato; Python 3.12+ verificato prima dell'installazione.
- [ ] Utente `bh-dic` non privilegiato; ownership e permessi revisionati.
- [ ] Clone privato allo SHA approvato, remote senza credenziali.
- [ ] `.venv`, Chromium e ClamAV verificati per l'utente di servizio.
- [ ] `.env` `0600`, nessun segreto in Git/log/ticket.
- [ ] Provider unico e `MODEL_STORE=false`; persona validata.
- [ ] Guild `1303955635984924722`, Channel ID copiato da `#mng-ai`, Role ID approvati.
- [ ] Comandi registrati solo nel guild; intent privilegiati off; permission bitfield `19456`.
- [ ] `ENABLE_WRITE_ACTIONS=false`, live write test e tutti i flag specifici false.
- [ ] Gestore processo unico; nessuna commistione systemd/script PID.
- [ ] Doctor offline, mock smoke, audit e backup verdi sul target.
- [ ] Prima verifica limitata a read sintetica autorizzata; nessuna write live.
- [ ] Runtime Debian e provider selezionato marcati secondo evidenza; DIC/Discord restano
      `UNVERIFIED` finché verificati separatamente.

## Riferimenti ufficiali

- OpenAI: [Responses e modelli](https://developers.openai.com/api/docs/guides/latest-model) e
  [function calling](https://developers.openai.com/api/docs/guides/function-calling).
- Groq: [compatibilità OpenAI](https://console.groq.com/docs/openai) e
  [`openai/gpt-oss-120b`](https://console.groq.com/docs/model/openai/gpt-oss-120b).
- Discord: [creazione app/bot](https://docs.discord.com/developers/quick-start/getting-started),
  [OAuth2](https://docs.discord.com/developers/topics/oauth2),
  [permissions](https://docs.discord.com/developers/topics/permissions),
  [Gateway Intents](https://docs.discord.com/developers/events/gateway) e
  [application commands](https://docs.discord.com/developers/interactions/application-commands).
- Ollama/OpenAI-compatible locale: [OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility).

# Operazioni

## Ultima evidenza osservata sul target

- repository installata in `/opt/bh-dic` sul target Debian 12; il remote GitHub è intenzionalmente
  `PUBLIC` e il tree corrente contiene soltanto sorgenti e materiale sintetico, mai configurazione
  runtime o PII; l'eccezione nei metadati Git storici è registrata nell'implementation report;
- Python 3.12, virtualenv, migrazione, Chromium, ClamAV e doctor offline/online verificati;
- Groq `openai/gpt-oss-120b` verificato con probe live chiuso;
- SHA `3d9283a8070aa3f73bd061adc3b608bb1440c1b5` distribuito e verificato con il gate live
  autorizzato in sola lettura su tutte le risorse read implementate;
- autenticazione/tenant, elenco completo, riepilogo, ruoli, timbratura target, contratti,
  maturazioni, bilanci, payroll, documenti, telemetria token e status verificati;
- servizio `active/running`, zero riavvii osservati, gateway `discord_ready` e startup outbound
  Discord verificato; il round-trip inbound richiede un utente reale autorizzato;
- check headless 0.2.5 `LIVE_AUTHENTICATED`, sessione `AUTHENTICATED` e tenant
  `VERIFIED_BY_ADAPTER` nel processo corrente; il vault pre-0.2.7 non conservava `sessionStorage`;
- comando guild-scoped registrato e gateway storicamente responsivo; primo smoke negato dal gate
  RBAC prima del dispatch;
- kill switch globale e tutte le flag write specifiche disabilitati.

La 0.2.7 separa il gateway dal login DIC e conserva cifrato lo snapshot bounded
`sessionStorage`; la 0.2.8 limita il contratto TeamSystem/OIDC alle route esatte documentate. La
0.3.0 aggiunge presenter Senior HR, lettura passiva elenco, refresh del vault e telemetria token.
Le risorse read attraversate dal gate, inclusa la traversata payroll collettiva, sono
`LIVE_READ_VERIFIED`; l'inbound Discord e tutte le write restano evidenze separate. Il primo deny RBAC resta
un'evidenza storica, non il risultato della configurazione corrente.

## Runbook giornaliero

Sul target Debian il gestore scelto è systemd: lifecycle privilegiato con `systemctl`, operazioni
applicative come utente `bh-dic`. I comandi `start.sh`, `stop.sh` e `restart.sh` descritti sotto
sono un'alternativa esclusiva per host PID-only e non vanno mescolati con l'unit installata.

### Configurazione

```bash
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin /bin/bash -c '
  set -Eeuo pipefail
  cd /opt/bh-dic
  ./scripts/init-config.sh
  chmod 600 .env
'
sudo -u bh-dic -H "${EDITOR:-nano}" /opt/bh-dic/.env
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin \
  /opt/bh-dic/scripts/doctor.sh
```

`init-config.sh` rifiuta di sovrascrivere `.env`. Per cambiare una variabile: fermare, fare backup,
modificare localmente, rieseguire doctor e riavviare solo se autorizzato.

### Avvio e foreground

Sul target systemd:

```bash
sudo systemctl start bh-dic.service
sudo systemctl is-active bh-dic.service
```

Soltanto su un host PID-only, come owner del progetto:

```bash
./scripts/start.sh
./scripts/run-foreground.sh
```

Sono modalità alternative. Dettagli in [Start/stop](START_STOP.md).

### Status e health

Sul target systemd:

```bash
sudo systemctl show bh-dic.service -p ActiveState -p SubState -p NRestarts
sudo journalctl -u bh-dic.service -n 100 --no-pager -o cat
```

Lo stato applicativo del processo già attivo si verifica con `/bh status` da un attore
autorizzato; non avviare una seconda istanza CLI/browser accanto al servizio solo per fare health.

Soltanto per il gestore PID alternativo:

```bash
./scripts/status.sh
./scripts/healthcheck.sh --process-only
./scripts/healthcheck.sh
```

`systemctl show` è la fonte del lifecycle systemd. `status.sh` e `healthcheck.sh` richiedono invece
il PID file del backend alternativo. Il healthcheck PID completo richiede configurazione e DB; se
l'interfaccia CLI/script non coincide, trattarlo come `BLOCKED` e non sostituirlo con una chiamata
live improvvisata.

`/bh status` della 0.3.0 riporta separatamente bot Discord, provider/modello, stato API osservato,
browser, autenticazione tenant, kill switch e token cumulativi locali. “Risposta osservata” indica
che almeno una chiamata modello è terminata nel database corrente; non è un healthcheck live né
una prova di quota disponibile. I contatori mancanti/incerti sono dichiarati e mai stimati.

### Provider di modello

Il gate predefinito è offline:

```bash
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin \
  /opt/bh-dic/.venv/bin/python -m bh_dic model-check
```

Mostra provider, modello e scope senza rete. Dopo doctor e soltanto con autorizzazione esplicita a
rete/costo, prima dell'avvio:

```bash
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin /bin/bash -c '
  set -Eeuo pipefail
  cd /opt/bh-dic
  ./scripts/doctor.sh --online
  .venv/bin/python -m bh_dic model-check --live
'
```

Il doctor online prova DNS/HTTP senza autenticazione. Il model-check live esegue una singola
richiesta sintetica con zero Function ID ammessi, non costruisce DIC/Discord/browser e non esegue
tool. Il suo `LIVE_VERIFIED` osservato attesta soltanto Groq e il modello selezionato, non DIC o
Discord.

### Autenticazione DIC

Il check offline non contatta la rete e valida soltanto un vault già presente:

```bash
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin \
  /opt/bh-dic/.venv/bin/python -m bh_dic dic-auth-check
```

Se il vault non esiste (prima installazione, rotazione o invalidazione), il fallimento è atteso e
non attesta nulla sul login. In quel caso procedere soltanto con l'unico check live autorizzato
descritto sotto.

La rotazione storica della password e il relativo gate 0.3.0 sono già conclusi. Non invalidare il
vault corrente. Soltanto dopo una futura rotazione o compromissione, fermare il servizio,
mantenere le write disabilitate e usare una volta la sola [procedura canonica guarded per
invalidazione e rotazione](DIC_AUTHENTICATION.md#invalidazione-e-rotazione). Non duplicare i
comandi fuori da quel confine systemd/utente.

Non invalidare un vault leggibile per un semplice upgrade o per ripetere il gate già riuscito.
Invalidare resta un'azione distinta e deliberata
per compromissione, rotazione di password/account/tenant o della chiave del vault, oppure errore
`DicSessionVaultError` verificato dall'operatore. Il comando prova la
route applicativa fissa e richiede l'attestazione passiva dell'azienda corrente. Se serve login,
accetta solo origini/route TeamSystem esatte e vincola l'account osservato all'utente configurato
prima di compilare il segreto. Qualunque mismatch, risposta mancante, password scaduta,
MFA/CAPTCHA o redirect inatteso fallisce chiuso.

Un fallimento DIC non richiede più di lasciare Discord offline: il normale `run` non invia
credenziali e può avviare il gateway in stato `DEGRADED`. In tale stato `/bh status`, `/bh health`
e l'aiuto restano disponibili, mentre ogni operazione DIC fallisce chiuso. Non ripetere il check
live dopo un submit dall'esito incerto.

Nella 0.2.3 i controlli di login attendono l'hydration entro un budget condiviso, soltanto sulle
route esatte e con un solo controllo visibile. La 0.2.4 restringe il campo e-mail DIC all'unico
input nativo sotto il contenitore pubblico `data-testid="login-email"`, eliminando l'ambiguità
padre/input osservata nel tentativo 0.2.3. Session status e autenticazione usano un'unica
esecuzione serializzata: timeout o errori di trasporto non ritentano le credenziali. Gli errori
sono JSON con i soli campi `error_type` e `stage`; non estrarre messaggi interni, URL o DOM e non
eseguire il comando in loop. `DicAuthOutcomeUnknownError`/`CREDENTIAL_SUBMIT` con exit code 78
indica che il submit può essere partito ma completamento, tenant o vault non sono dimostrabili:
fermare il runbook e verificare umanamente, senza un nuovo login.

La 0.2.5 tratta soltanto l'esatta `/it/callback` DIC come transitoria entro il budget condiviso;
non legge né registra la query e rifiuta fragment, porta esplicita, userinfo, host somigliante,
trailing slash e path aggiuntivi. Il marker autenticato viene atteso entro la cattura tenant, ma
`/data/company/id` resta obbligatorio. Lo user agent Chromium nativo resta invariato. Il singolo
check headless successivo al login manuale è stato completato. La 0.2.7 ammette anche l'ingresso
diretto alla route password quando DIC usa `login_hint`, senza cambio user agent né fallback
generici. Un check server 0.2.7 storico si è fermato a `TEAMSYSTEM_EMAIL`. La 0.2.8
riconosce anche la root e-mail esatta corrente e le sole transizioni OIDC esatte; un SSO silenzioso
non esegue fill/click/submit credenziali ed è valido solo dopo marker DIC e tenant attestato. Un
nuovo exit 78 o `CREDENTIAL_SUBMIT` impone lo stop del check senza retry né nuova invalidazione.

La 0.3.0 ripersiste sotto lock cookie e `sessionStorage` aggiornati soltanto dopo stato
autenticato/tenant attestato o lettura riuscita. Questa manutenzione del vault non invia
credenziali e non è un retry di login; fallimenti, mismatch o stati ignoti non sovrascrivono il
file valido.

### Log

```bash
./scripts/logs.sh all
./scripts/logs.sh all --follow
./scripts/logs.sh security --since 2026-08-14T00:00:00Z --level WARNING
./scripts/logs.sh all --correlation-id <CORRELATION_ID>
```

La lettura applica un secondo strato di redazione. Vedere [Logging](LOGGING.md).

### File

```bash
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin /bin/bash -c '
  set -Eeuo pipefail
  cd /opt/bh-dic
  upload_uuid="<UPLOAD_UUID>"
  ./scripts/files.sh list
  ./scripts/files.sh metadata "$upload_uuid"
  ./scripts/files.sh scan "$upload_uuid"
  ./scripts/files.sh purge-expired
'
```

I comandi mostrano metadati, mai contenuto o nome originale. La scansione non promuove da sola un
file e un errore ClamAV resta fail-closed. Vedere [File handling](FILE_HANDLING.md).

### Audit

```bash
./scripts/audit-verify.sh
```

Un exit code non-zero impone stop, preservazione dell'evidenza ed escalation; non riscrivere la
catena. Vedere [Audit](AUDIT.md).

### Stop e restart

Sul target systemd:

```bash
sudo systemctl stop bh-dic.service
sudo systemctl restart bh-dic.service
```

Soltanto per il gestore PID alternativo:

```bash
./scripts/stop.sh
./scripts/restart.sh
```

`--force` è una scelta esplicita successiva a diagnosi, non il default, ed esiste soltanto nel
gestore PID.

In modalità systemd l'unit installata mantiene `RestartPreventExitStatus=78` come difesa
aggiuntiva. Dalla 0.2.7 il normale `run` non invia credenziali DIC: il codice 78 è soprattutto il
contratto dell'operazione esplicita di autenticazione con esito post-submit incerto. Su
Debian 12 l'unit dalla 0.2.4 usa `ConditionPathExists=/opt/bh-dic/.env` più
`ExecCondition=/usr/bin/test -f /opt/bh-dic/.env`, non la direttiva non supportata
`ConditionPathIsRegularFile`. `doctor.sh` resta il controllo che impone modalità `0600` di `.env`
e configurazione valida. Dopo un update dell'unit, ricopiarla, usare `systemd-analyze verify` e
`systemctl daemon-reload` a servizio fermo.

### Backup e restore

Interfacce disponibili per il solo deployment SQLite:

```bash
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin \
  /opt/bh-dic/scripts/backup.sh
# Per restore usare esclusivamente la procedura guarded in BACKUP_RESTORE.md.
```

I sorgenti e i test di contratto sono presenti, ma nessun backup/restore server è stato eseguito.
`backup.sh` rifiuta database non-SQLite; PostgreSQL resta `BLOCKED`. Procedura e limiti in
[Backup/restore](BACKUP_RESTORE.md).

### Update

Dopo un restore drill riuscito sul target, per un deployment systemd:

```bash
sudo systemctl stop bh-dic.service &&
test "$(sudo systemctl show -p ActiveState --value bh-dic.service)" = inactive &&
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin /bin/bash -c '
  set -Eeuo pipefail
  cd /opt/bh-dic
  ./scripts/update.sh
  ./scripts/doctor.sh
  ./scripts/audit-verify.sh
' &&
sudo systemctl start bh-dic.service &&
sudo systemctl is-active bh-dic.service
```

`update.sh` richiede working tree pulito e upstream, usa fetch con timeout e solo fast-forward,
reinstalla lockfile/editable, migra e testa. Deve essere invocato dall'owner non-root del progetto,
normalmente `bh-dic`; verifica ownership/leggibilità, dipendenze/import e lo stato systemd esatto
prima delle mutazioni. L'opzione `--restart` gestisce soltanto un processo PID già attivo e non va
mai usata per l'unit systemd. Un errore dopo lo stop lascia deliberatamente il bot fermo.

Dopo l'update 0.3.0 verificare che `bh_dic version` mostri `0.3.0` e che Alembic sia alla revisione
`0002_model_usage`. Il gate applicativo dei due percorsi è già riuscito; resta da eseguirne lo
smoke attraverso il trasporto Discord. Con write ancora disabilitate, usare un attore `READ_ONLY`
per il conteggio aggregato e un attore `HR_READ` per le scadenze del prossimo mese. Il primo può
produrre soltanto un aggregato pubblico nel canale allowlistato; il secondo deve restare ephemeral.
Se uno dei due confini non è rispettato, fermare il rollout.

## Sessione DIC e rotazione token

Invalidare una sessione cifrata dopo rotazione credenziali o sospetto compromesso soltanto con la
[procedura canonica guarded](DIC_AUTHENTICATION.md#invalidazione-e-rotazione). Il confine impone
stop systemd verificato, identità `bh-dic` e nessun restart automatico.

Per Discord/provider di modello/DIC: revocare lato provider, aggiornare `.env` con `0600`,
invalidare la sessione DIC quando pertinente, verificare audit/doctor e avviare soltanto dopo
autorizzazione.

## Incident response minima

1. impostare `ENABLE_WRITE_ACTIONS=false` e fermare il bot;
2. revocare credenziali/sessioni coinvolte;
3. preservare DB, audit e log read-only;
4. eseguire audit verify senza riparare la catena;
5. correlare gli eventi senza PII;
6. ripristinare solo da backup verificato;
7. riabilitare prima le sole read e soltanto con autorizzazione.

Per diagnosi vedere [Debugging](DEBUGGING.md) e [Troubleshooting](TROUBLESHOOTING.md).

# Operazioni

## Ultima evidenza osservata sul target

- repository installata in `/opt/bh-dic` sul target Debian 12; il remote GitHub è intenzionalmente
  `PUBLIC` e il tree corrente contiene soltanto sorgenti e materiale sintetico, mai configurazione
  runtime o PII; l'eccezione nei metadati Git storici è registrata nell'implementation report;
- Python 3.12, virtualenv, migrazione, Chromium, ClamAV e doctor offline/online verificati;
- Groq `openai/gpt-oss-120b` verificato con probe live chiuso;
- la 0.2.7 è stata distribuita; il check DIC corrente si è fermato fail-closed a
  `TEAMSYSTEM_EMAIL` prima di qualunque azione sulla credenziale, mentre il gateway resta
  separato dal login DIC;
- nessuna Function ID DIC read o write verificata live;
- check headless 0.2.5 `LIVE_AUTHENTICATED`, sessione `AUTHENTICATED` e tenant
  `VERIFIED_BY_ADAPTER` nel processo corrente; il vault pre-0.2.7 non conservava `sessionStorage`;
- comando guild-scoped registrato e gateway storicamente responsivo; primo smoke negato dal gate
  RBAC prima del dispatch;
- kill switch globale e tutte le flag write specifiche disabilitati.

La 0.2.7 separa il gateway dal login DIC e conserva cifrato lo snapshot bounded
`sessionStorage`. La candidata 0.2.8 aggiunge soltanto il contratto corrente verificato
pubblicamente: root e-mail TeamSystem esatta, legacy `/Account/LoginEmail`, transizioni pending
bounded `/connect/authorize`/`/connect/authorize/callback` e SSO senza azioni credenziali accettato
solo dopo marker DIC e tenant attestato. La candidata 0.3.0 aggiunge presenter Senior HR, lettura
passiva elenco, refresh del vault e telemetria token. I gate locali completi sono verdi; deployment
e smoke Discord/DIC restano `PENDING`. Sul target non è ancora promossa alcuna nuova Function ID
live. Il
primo deny RBAC resta un'evidenza separata dal funzionamento DIC.

## Runbook giornaliero

### Configurazione

```bash
cd /opt/bh-dic
./scripts/init-config.sh
chmod 600 .env
nano .env
./scripts/doctor.sh
```

`init-config.sh` rifiuta di sovrascrivere `.env`. Per cambiare una variabile: fermare, fare backup,
modificare localmente, rieseguire doctor e riavviare solo se autorizzato.

### Avvio e foreground

```bash
./scripts/start.sh
./scripts/run-foreground.sh
```

Sono modalità alternative. Dettagli in [Start/stop](START_STOP.md).

### Status e health

```bash
./scripts/status.sh
./scripts/healthcheck.sh --process-only
./scripts/healthcheck.sh
```

Il primo healthcheck controlla soltanto identità/processo. Quello completo richiede configurazione
e DB; se l'interfaccia CLI/script non coincide, trattarlo come `BLOCKED` e non sostituirlo con una
chiamata live improvvisata.

`/bh status` della 0.3.0 riporta separatamente bot Discord, provider/modello, stato API osservato,
browser, autenticazione tenant, kill switch e token cumulativi locali. “Risposta osservata” indica
che almeno una chiamata modello è terminata nel database corrente; non è un healthcheck live né
una prova di quota disponibile. I contatori mancanti/incerti sono dichiarati e mai stimati.

### Provider di modello

Il gate predefinito è offline:

```bash
.venv/bin/python -m bh_dic model-check
```

Mostra provider, modello e scope senza rete. Dopo doctor e soltanto con autorizzazione esplicita a
rete/costo, prima dell'avvio:

```bash
./scripts/doctor.sh --online
.venv/bin/python -m bh_dic model-check --live
```

Il doctor online prova DNS/HTTP senza autenticazione. Il model-check live esegue una singola
richiesta sintetica con zero Function ID ammessi, non costruisce DIC/Discord/browser e non esegue
tool. Il suo `LIVE_VERIFIED` osservato attesta soltanto Groq e il modello selezionato, non DIC o
Discord.

### Autenticazione DIC

Il check offline non contatta la rete e valida soltanto un vault già presente:

```bash
.venv/bin/python -m bh_dic dic-auth-check
```

Se il vault non esiste (prima installazione, rotazione o invalidazione), il fallimento è atteso e
non attesta nulla sul login. In quel caso procedere soltanto con l'unico check live autorizzato
descritto sotto.

La password TeamSystem è stata rinnovata e il secret locale aggiornato. Il vault esistente precede
quella rotazione: dopo aver distribuito la candidata 0.2.8 e completato i gate, fermare il servizio,
mantenere le write disabilitate, invalidarlo deliberatamente una sola volta e poi eseguire un solo
check live:

```bash
.venv/bin/python -m bh_dic invalidate-session
.venv/bin/python -m bh_dic dic-auth-check --live
```

Non invalidare un vault leggibile per un semplice upgrade; in questo caso l'invalidazione è invece
richiesta perché la credenziale è stata ruotata. Invalidare resta un'azione distinta e deliberata
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
generici. Il check server 0.2.7 corrente si è fermato a `TEAMSYSTEM_EMAIL`. La candidata 0.2.8
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
./scripts/files.sh list
./scripts/files.sh metadata <UPLOAD_UUID>
./scripts/files.sh scan <UPLOAD_UUID>
./scripts/files.sh purge-expired
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

```bash
./scripts/stop.sh
./scripts/restart.sh
```

`--force` è una scelta esplicita successiva a diagnosi, non il default.

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
./scripts/backup.sh
./scripts/restore.sh var/backups/<BACKUP_FILE>.tar.gz --confirm RESTORE
```

I sorgenti e i test di contratto sono presenti, ma nessun backup/restore server è stato eseguito.
`backup.sh` rifiuta database non-SQLite; PostgreSQL resta `BLOCKED`. Procedura e limiti in
[Backup/restore](BACKUP_RESTORE.md).

### Update

Dopo un restore drill riuscito sul target:

```bash
./scripts/stop.sh
./scripts/backup.sh
./scripts/update.sh
./scripts/doctor.sh
./scripts/status.sh
```

`update.sh` richiede working tree pulito e upstream, usa fetch con timeout e solo fast-forward,
reinstalla lockfile/editable, migra e testa. L'opzione `--restart` riavvia solo se il bot era già
attivo; non usarla durante preparazione/deployment fermo.

Dopo l'update 0.3.0 verificare che `bh_dic version` mostri `0.3.0` e che Alembic sia alla revisione
`0002_model_usage`. Poi, con write ancora disabilitate, eseguire i due smoke distinti: totale
organico tramite un attore `READ_ONLY` e scadenze del prossimo mese tramite un attore `HR_READ`.
Il primo può produrre soltanto un aggregato pubblico nel canale allowlistato; il secondo deve
restare ephemeral. Se uno dei due confini non è rispettato, fermare il rollout.

## Sessione DIC e rotazione token

Invalidare una sessione cifrata dopo rotazione credenziali o sospetto compromesso:

```bash
./scripts/stop.sh
.venv/bin/python -m bh_dic invalidate-session
./scripts/doctor.sh
```

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

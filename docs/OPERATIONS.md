# Operazioni

## Stato operativo corrente

- repository installata in `/opt/bh-dic` sul target Debian 12; il remote GitHub è intenzionalmente
  `PUBLIC` e il tree corrente contiene soltanto sorgenti e materiale sintetico, mai configurazione
  runtime o PII; l'eccezione nei metadati Git storici è registrata nell'implementation report;
- Python 3.12, virtualenv, migrazione, Chromium, ClamAV e doctor offline/online verificati;
- Groq `openai/gpt-oss-120b` verificato con probe live chiuso;
- servizio systemd `active/running`, con `NRestarts=0` al controllo di avvio;
- nessuna Function ID DIC read o write verificata live;
- check headless 0.2.5 `LIVE_AUTHENTICATED`, sessione `AUTHENTICATED`, tenant
  `VERIFIED_BY_ADAPTER` e vault cifrato utilizzabile;
- comando guild-scoped registrato e gateway responsivo; primo smoke negato dal gate RBAC prima del
  dispatch, senza eseguire Function ID DIC;
- kill switch globale e tutte le flag write specifiche disabilitati.

I gate sintetici della release 0.2.6 sono verdi: 579 test, branch coverage 86%, Ruff, mypy, Bandit,
audit dipendenze e secret scan senza finding. Il dettaglio è nell'implementation report. Sul target
sono stati eseguiti solo i controlli operativi riportati sopra; nessun test live ha eseguito una
Function ID DIC.

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

La password TeamSystem è stata rinnovata e il secret locale aggiornato. Il controllo live 0.2.5 è
stato eseguito una sola volta con bot fermo e write disabilitate:

```bash
.venv/bin/python -m bh_dic invalidate-session
.venv/bin/python -m bh_dic dic-auth-check --live
```

Il risultato osservato è sessione `AUTHENTICATED`, tenant `VERIFIED_BY_ADAPTER` e vault cifrato
utilizzabile. Il comando ripristina prima un eventuale vault, prova la route applicativa fissa e richiede
l'attestazione passiva dell'azienda corrente. Se serve login, accetta redirect soltanto verso le
origini TeamSystem esatte previste. Qualunque mismatch, risposta mancante, password scaduta,
MFA/CAPTCHA o redirect inatteso fallisce chiuso. Non avviare il bot finché sessione e tenant non
risultano verificati. Un risultato `encrypted_session_invalidated=false` indica semplicemente che
il vault non esisteva.

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
check headless successivo al login manuale è stato completato; per una futura rotazione o
invalidazione, un nuovo exit 78 impone nuovamente lo stop senza retry.

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

In modalità systemd l'unit installata deve includere `RestartPreventExitStatus=78`: `run` usa 78
per ogni errore di autenticazione, impedendo a `Restart=on-failure` di rilanciare il login. Su
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

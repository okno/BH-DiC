# Operazioni

## Stato operativo corrente

- repository privata collegata localmente;
- server `10.1.2.253` non raggiunto: utente/chiave SSH mancanti;
- processo bot assente nel workspace locale; stato sul target `UNVERIFIED`;
- nessun read o write live verificato;
- write mock-only, kill switch globale e flag specifici disabilitati.

I 22 script Bash richiesti sono presenti e il gate locale ha verificato parsing `bash -n`, 29 casi
statici di contratto e 2 casi comportamentali (31 totali). Restano non eseguiti sul target Linux:
un test locale non equivale a un comando riuscito sul server.

Il gate release 0.2.0 del 15 agosto 2026 ha rieseguito tutti i 31 casi sul worktree finale. Non sono
stati eseguiti comandi sul target Debian.

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
tool. Il suo `LIVE_VERIFIED` non attesta DIC o deployment.

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

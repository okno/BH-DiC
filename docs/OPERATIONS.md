# Operazioni

## Stato operativo corrente

- repository privata installata in `/opt/bh-dic` sul target Debian 12;
- Python 3.12, virtualenv, migrazione, Chromium, ClamAV e doctor offline/online verificati;
- Groq `openai/gpt-oss-120b` verificato con probe live chiuso;
- processo bot sul target fermo;
- nessuna Function ID DIC read o write verificata live;
- autenticazione DIC bloccata dalla password TeamSystem scaduta e da vault assente;
- kill switch globale e tutte le flag write specifiche disabilitati.

I gate offline della release 0.2.2 sono registrati nell'implementation report. Sul target sono
stati eseguiti solo i controlli operativi riportati sopra; nessun test live ha eseguito una
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

Il check offline non contatta la rete:

```bash
.venv/bin/python -m bh_dic dic-auth-check
```

Dopo che un amministratore ha completato il cambio password TeamSystem e aggiornato il secret in
locale, mantenere il bot fermo e le write disabilitate, quindi eseguire il controllo live
esplicitamente autorizzato:

```bash
.venv/bin/python -m bh_dic dic-auth-check --live
```

Il comando ripristina prima un eventuale vault, prova la route applicativa fissa e richiede
l'attestazione passiva dell'azienda corrente. Se serve login, accetta redirect soltanto verso le
origini TeamSystem esatte previste. Qualunque mismatch, risposta mancante, password scaduta,
MFA/CAPTCHA o redirect inatteso fallisce chiuso. Non avviare il bot finché sessione e tenant non
risultano verificati.

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

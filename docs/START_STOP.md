# Start e stop

> Stato al 16 agosto 2026: il runtime sul target Debian è preparato e il bot è **STOPPED**.
> Doctor offline/online e Groq sono verificati; la verifica DIC headless è bloccata dalla password
> TeamSystem scaduta e dall'assenza di un vault. Non avviare ancora il servizio.

## Prerequisiti

```bash
cd /opt/bh-dic
test -x ./scripts/start.sh
./scripts/doctor.sh
.venv/bin/python -m bh_dic model-check
.venv/bin/python -m bh_dic dic-auth-check
./scripts/status.sh
```

`doctor.sh` deve terminare con exit code 0; `.env` deve essere `0600`, la configurazione valida,
il database migrato, Chromium presente e ClamAV disponibile quando richiesto. Mantenere
`ENABLE_WRITE_ACTIONS=false` e `MODEL_STORE=false`.

## Un solo gestore di processo

Scegliere **systemd** oppure gli script PID. L'unit di esempio usa
`scripts/run-foreground.sh`; in quella modalità avvio, stato, log, restart e stop si eseguono con
`systemctl` e `journalctl`. Non affiancare `start.sh`/`stop.sh` a un servizio systemd attivo.
Questa pagina descrive sotto la modalità script PID; per l'installazione systemd vedere la
[guida end-to-end](INSTALLATION.md#9-scegliere-un-solo-gestore-di-processo).

## Avvio background

```bash
./scripts/start.sh
```

Lo script accetta zero argomenti, acquisisce un lock con `flock`, rifiuta doppio avvio/PID
estraneo, esegue doctor, avvia `.venv/bin/python -m bh_dic run` con `nohup`, scrive PID atomico e
attende cinque controlli del processo. Gli errori di bootstrap vanno in
`var/log/process-errors.log`; non copiare il file senza redazione.

Dopo l'avvio autorizzato:

```bash
./scripts/status.sh
./scripts/healthcheck.sh --process-only
./scripts/logs.sh app --follow
```

Il solo processo attivo non prova connettività o correttezza funzionale.

## Foreground debug

```bash
./scripts/run-foreground.sh
```

Usarlo in una sessione controllata: rifiuta un processo già gestito e carica la configurazione
reale. `Ctrl-C` richiede la chiusura ordinata. Per un smoke senza rete preferire:

```bash
.venv/bin/python -m bh_dic run --mock --check-only
```

Quest'ultimo costruisce la slice mock, non apre il gateway Discord e mantiene le write spente.

## Status

```bash
./scripts/status.sh
```

Mostra solo metadati operatore-safe: running/stopped, PID, uptime, spazio, quick-check SQLite o DB
remoto configurato, Chromium, presenza della sessione DIC cifrata, kill switch write e ultimo
evento redatto. Non mostra token o contenuti HR.

## Restart

```bash
./scripts/restart.sh
```

Propaga le opzioni di stop:

```bash
./scripts/restart.sh --timeout 60
./scripts/restart.sh --force --timeout 60
```

`--force` invia `SIGKILL` solo dopo timeout e nuova verifica dell'identità del processo; usarlo
soltanto dopo aver indagato e preservato i log.

## Stop

```bash
./scripts/stop.sh
```

Lo script verifica PID e command line, invia `SIGTERM`, attende fino a 30 secondi e rimuove
PID/lock solo dopo l'uscita. Se non termina:

```bash
./scripts/stop.sh --timeout 60
```

Solo come ultima decisione esplicita:

```bash
./scripts/stop.sh --force --timeout 60
```

Lo stop è idempotente: se già fermo pulisce esclusivamente lifecycle file stale controllati. Non
usare `kill -9` manualmente e non eliminare PID/lock senza verificare il processo.

## Sequenza di ripresa sul target

Dopo l'aggiornamento alla release correttiva, mantenere le write disabilitate e il bot fermo:

```bash
cd /opt/bh-dic
./scripts/doctor.sh
./scripts/doctor.sh --online
.venv/bin/python -m bh_dic model-check --live
.venv/bin/python -m bh_dic dic-auth-check
```

Un amministratore deve completare fuori dal bot il cambio della password TeamSystem scaduta e
aggiornare `DIC_PASSWORD` localmente senza mostrarla. Poi:

```bash
.venv/bin/python -m bh_dic invalidate-session
.venv/bin/python -m bh_dic dic-auth-check --live
./scripts/status.sh
```

Solo se il check live attesta autenticazione e tenant, e dopo autorizzazione separata:

```bash
./scripts/register-commands.sh
./scripts/start.sh
./scripts/status.sh
./scripts/logs.sh all --follow
```

I comandi `--online`/`--live` richiedono autorizzazione esplicita a rete/costo. Il model-check live
fa una sola richiesta sintetica chiusa e non costruisce Discord, DIC o browser; deve precedere
l'avvio e non attesta il tenant DIC.

Al 16 agosto 2026 la preparazione e il provider check sono riusciti; la sequenza si ferma prima di
`dic-auth-check --live`. Nessun read/write DIC live è stato completato e il bot resta fermo.

Vedere [Operations](OPERATIONS.md) e [Troubleshooting](TROUBLESHOOTING.md).

# Start e stop

> Stato al 17 agosto 2026: il runtime sul target Debian è preparato e il bot è **STOPPED**.
> Doctor offline/online e Groq sono verificati. Un login manuale autorizzato in browser fresco ha
> accettato le credenziali con un solo submit; il check server 0.2.4 ha invece rifiutato la callback
> DIC legittima con exit 78. Distribuire la 0.2.5 e completare il gate adapter/tenant/vault prima di
> avviare il servizio.

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

L'unit systemd 0.2.4 abbina `Restart=on-failure` a `RestartPreventExitStatus=78`. Il comando `run`
usa 78 per ogni errore di autenticazione, quindi systemd non deve trasformarlo in nuovi tentativi
di login. Su Debian 12 usa inoltre `ConditionPathExists` e un `ExecCondition` con
`/usr/bin/test -f`, perché `ConditionPathIsRegularFile` non è supportata. Questi controlli non
sostituiscono `doctor.sh`, che verifica modalità `0600` di `.env` e configurazione valida.
Ricopiare e verificare l'unit realmente installata prima di abilitarla.

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

Dopo l'aggiornamento alla release 0.2.5, mantenere le write disabilitate e il bot fermo:

```bash
cd /opt/bh-dic
./scripts/doctor.sh
./scripts/doctor.sh --online
.venv/bin/python -m bh_dic model-check --live
.venv/bin/python -m bh_dic dic-auth-check
```

Il rinnovo umano della password TeamSystem e l'aggiornamento locale di `DIC_PASSWORD` sono già
stati completati. Eseguire una sola verifica live autorizzata:

```bash
.venv/bin/python -m bh_dic invalidate-session
.venv/bin/python -m bh_dic dic-auth-check --live
./scripts/status.sh
```

`encrypted_session_invalidated=false` non è un errore: indica che non esisteva un vault da
eliminare. La 0.2.3 attende l'hydration entro un budget limitato e non ritenta automaticamente le
credenziali; la 0.2.4 usa per l'e-mail DIC l'unico input nativo sotto il contenitore pubblico
`data-testid="login-email"`, invece del placeholder che corrispondeva anche al componente padre.
La 0.2.5 ammette la callback DIC esatta soltanto come transitoria bounded, senza leggere o
registrare la query, e attende il marker entro lo stesso budget mantenendo obbligatorio il tenant.
Eseguire il check live esattamente una volta e soltanto dopo il deployment 0.2.5. Se restituisce
JSON con `error_type`/`stage`, non trasformarlo in un loop e non avviare il bot.

Se `dic-auth-check --live` restituisce `DicAuthOutcomeUnknownError` con stage
`CREDENTIAL_SUBMIT`, l'exit code è 78: il submit può essere partito, mentre completamento, tenant
probe o persistenza del vault non sono dimostrabili. Fermarsi e verificare umanamente lo stato
dell'account/sessione; non ripetere il comando. Lo stesso codice impedisce il restart systemd
automatico quando l'errore di autenticazione emerge durante `run`.

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

Al 17 agosto 2026 la preparazione e il provider check sono riusciti. Il login manuale fresco è
`LIVE_AUTHENTICATED`, ma non sostituisce il check headless: la correzione 0.2.5 deve ancora
dimostrare adapter, tenant e vault server. Nessuna Function ID read/write DIC live è stata
completata e il bot resta fermo con write disabilitate.

Vedere [Operations](OPERATIONS.md) e [Troubleshooting](TROUBLESHOOTING.md).

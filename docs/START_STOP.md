# Start e stop

> Stato osservato al 17 agosto 2026: la 0.3.0 allo SHA esatto documentato ha superato il gate
> applicativo live bounded con write disabilitate. Il servizio target è `active/running`, con zero
> riavvii osservati e gateway `discord_ready`; lo smoke del trasporto Discord resta `PENDING`. Il
> gateway resta separato dal login DIC.

## Prerequisiti

```bash
cd /opt/bh-dic
test -x ./scripts/start.sh
./scripts/doctor.sh
.venv/bin/python -m bh_dic model-check
./scripts/status.sh
```

`doctor.sh` deve terminare con exit code 0; `.env` deve essere `0600`, la configurazione valida,
il database migrato, Chromium presente e ClamAV disponibile quando richiesto. Mantenere
`ENABLE_WRITE_ACTIONS=false` e `MODEL_STORE=false`.
Se esiste già un vault, `dic-auth-check` senza `--live` può validarlo localmente; su una prima
installazione o dopo invalidazione fallisce correttamente e non è un prerequisito del bootstrap.

## Un solo gestore di processo

Scegliere **systemd** oppure gli script PID. L'unit di esempio usa
`scripts/run-foreground.sh`; in quella modalità avvio, stato, log, restart e stop si eseguono con
`systemctl` e `journalctl`. Non affiancare `start.sh`/`stop.sh` a un servizio systemd attivo.
Questa pagina descrive sotto la modalità script PID; per l'installazione systemd vedere la
[guida end-to-end](INSTALLATION.md#9-scegliere-un-solo-gestore-di-processo).

L'unit systemd abbina `Restart=on-failure` a `RestartPreventExitStatus=78`. Dalla 0.2.7 il comando
`run` non invia credenziali DIC: una sessione mancante lascia il gateway online e degradato.
L'exit 78 resta una protezione per gli esiti di autenticazione esplicita non dimostrabili e non
deve mai trasformarsi in nuovi tentativi di login. Su Debian 12 l'unit usa inoltre
`ConditionPathExists` e un `ExecCondition` con
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

Il gate live 0.3.0 ha già attestato sessione e tenant: non invalidare il vault e non ripetere il
login. Mantenere le write disabilitate, verificare localmente il vault e avviare una sola istanza
per lo smoke Discord ancora pending:

```bash
cd /opt/bh-dic
./scripts/doctor.sh
.venv/bin/python -m bh_dic dic-auth-check
systemctl start bh-dic.service
systemctl is-active bh-dic.service
```

La 0.2.3 attende l'hydration entro un budget limitato e non ritenta automaticamente le
credenziali; la 0.2.4 usa per l'e-mail DIC l'unico input nativo sotto il contenitore pubblico
`data-testid="login-email"`, invece del placeholder che corrispondeva anche al componente padre.
La 0.2.5 ammette la callback DIC esatta soltanto come transitoria bounded, senza leggere o
registrare la query, e attende il marker entro lo stesso budget mantenendo obbligatorio il tenant.
La 0.2.7 accetta l'ingresso TeamSystem esatto sia su `LoginEmail` sia direttamente su
`LoginPassword` quando DIC passa il `login_hint`; non cambia lo User-Agent e non aggiunge route
generiche. Il vault conserva cifrati anche i token DIC in `sessionStorage`, così il riavvio può
ripristinare la sessione completa. Un check server 0.2.7 storico si è però fermato a
`TEAMSYSTEM_EMAIL`. La 0.2.8 riconosce anche la root e-mail TeamSystem esatta corrente e
le sole transizioni bounded `/connect/authorize`/`/connect/authorize/callback`. Se l'IdP completa
un SSO senza mostrare controlli, non viene eseguita alcuna azione credenziale e il successo
richiede comunque marker DIC e attestazione tenant esatta. Eseguire invalidazione e check live
esattamente una volta soltanto dopo una futura rotazione o compromissione; la sequenza storica è
già conclusa. Se un futuro check restituisce
JSON con `error_type`/`stage`, non trasformarlo in un loop: il servizio può comunque essere
avviato in modalità degradata per rispondere a status/health, ma nessuna funzione DIC sarà
operativa finché una sessione non viene verificata.

Se `dic-auth-check --live` restituisce `DicAuthOutcomeUnknownError` con stage
`CREDENTIAL_SUBMIT`, l'exit code è 78: il submit può essere partito, mentre completamento, tenant
probe o persistenza del vault non sono dimostrabili. Fermarsi e verificare umanamente lo stato
dell'account/sessione; non ripetere il comando. Il normale `run` non esegue questo submit.

Dopo i gate locali avviare il gateway ed eseguire lo smoke Discord autorizzato. Con systemd usare esclusivamente
`systemctl`/`journalctl`. La registrazione guild-scoped già completata non va ripetuta per una
modifica di ruoli o `.env`:

```bash
systemctl restart bh-dic.service
systemctl status bh-dic.service --no-pager
journalctl -u bh-dic.service -f -o cat
```

I comandi `--online`/`--live` richiedono autorizzazione esplicita a rete/costo. Il model-check live
fa una sola richiesta sintetica chiusa e non costruisce Discord, DIC o browser; deve precedere
l'avvio e non attesta il tenant DIC.

Al 17 agosto 2026 preparazione, provider, autenticazione/tenant e i due subset read bounded
documentati sono riusciti sullo SHA 0.3.0 verificato. Il servizio è stato avviato
`active/running`, con zero riavvii osservati e gateway `discord_ready`. Verificare ora il
round-trip slash autorizzato e poi decidere esplicitamente il lifecycle; le write restano
disabilitate.

Vedere [Operations](OPERATIONS.md) e [Troubleshooting](TROUBLESHOOTING.md).

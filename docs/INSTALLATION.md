# Installazione

Questa procedura descrive l'installazione prevista su Linux. **Non è stata eseguita sul server**:
il collegamento SSH è bloccato finché non vengono forniti `DEPLOY_SSH_USER` e una chiave o un
agent autorizzato. L'installazione non avvia il bot.

## Prerequisiti

- accesso alla repository privata;
- Linux x86_64 o ARM64 supportato dai pacchetti scelti;
- Python 3.12+, `venv`, Git e certificati CA;
- Bash, spazio disco sufficiente e, per gli upload, ClamAV;
- eventuale `sudo` limitato all'installazione dei pacchetti necessari;
- fingerprint SSH del server verificata fuori banda.

Il target preferito è `/opt/bh-dic`, eseguito dall'utente non privilegiato `bh-dic`. Senza
`sudo`, usare `$HOME/BH-DiC`. Permessi attesi: applicazione `0750`, dati `0700`, `.env` `0600`,
upload `0600`, log `0640` o più restrittivi.

## Checkout privato

Eseguire sul server con credenziali Git già configurate e senza token nell'URL:

```bash
umask 077
install -d -m 0750 /opt/bh-dic
git clone <PRIVATE_REPOSITORY_URL> /opt/bh-dic
cd /opt/bh-dic
git remote -v
```

Se `/opt/bh-dic` richiede privilegi, l'amministratore deve creare e assegnare la directory
prima del clone. Non eseguire il bot come `root`.

## Installazione automatizzata

L'interfaccia operativa prevista è:

```bash
cd /opt/bh-dic
./scripts/install.sh
```

Lo script è presente e ha superato parsing/test di contratto locali; prima dell'uso verificare che
sia eseguibile nel commit distribuito. Deve
rilevare distribuzione/architettura/Python, creare `.venv`, installare dipendenze e Chromium,
verificare ClamAV, creare `var/` con permessi stretti ed eseguire le migrazioni. Non deve creare
segreti né avviare processi persistenti.

## Installazione manuale di sviluppo

Questa è una procedura di fallback per una macchina isolata, non un'attestazione di deployment:

```bash
cd /opt/bh-dic
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --requirement requirements.lock
python -m pip install --editable .
python -m playwright install chromium
install -d -m 0700 var/db var/run var/log var/audit var/session var/uploads
python -m alembic -c migrations/alembic.ini upgrade head
```

L'installazione di librerie di sistema per Chromium o ClamAV dipende dalla distribuzione e deve
essere revisionata dall'amministratore; non usare opzioni che disabilitano TLS o verifiche delle
firme.

## Configurazione iniziale

```bash
cd /opt/bh-dic
cp -n .env.example .env
chmod 600 .env
${EDITOR:-nano} .env
./scripts/doctor.sh
```

`cp -n` evita di sovrascrivere una configurazione esistente. Inserire i segreti soltanto tramite
un canale sicuro. `OPENAI_STORE=false`, `ENABLE_WRITE_ACTIONS=false` e tutti i flag specifici di
write devono rimanere invariati. Dettagli in [Configuration](CONFIGURATION.md).

`doctor.sh` è offline per impostazione predefinita. Soltanto dopo autorizzazione e configurazione
completa:

```bash
./scripts/doctor.sh --online
```

Il controllo online non deve eseguire operazioni HR né registrare segreti.

## Verifica senza avvio

```bash
./scripts/status.sh
./scripts/audit-verify.sh
python -m pytest
```

Lo stato finale atteso per un'installazione preparata è `stopped`: nessun PID del bot, nessun
servizio systemd abilitato e nessun processo Playwright residuo. Per l'avvio autorizzato vedere
[Start/stop](START_STOP.md).

## Aggiornamento

Quando lo script è presente nel commit distribuito:

```bash
cd /opt/bh-dic
./scripts/backup.sh
./scripts/update.sh
./scripts/doctor.sh
./scripts/status.sh
```

L'update deve fermarsi se il backup o un gate fallisce e non deve avviare automaticamente il bot.

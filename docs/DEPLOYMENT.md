# Deployment

## Stato corrente

Il deployment verso `10.1.2.253:22` è **BLOCCATO**. Mancano l'utente SSH e la chiave/agent
autorizzati; nessun tentativo deve aggirare il blocco. Nessun bot è stato avviato nel workspace
locale, ma lo stato del processo sul target è `UNVERIFIED`. Non esistono letture o scritture live
verificate e tutte le write restano `TESTED_WITH_MOCK` e `DISABLED_BY_POLICY`.

Input ancora necessari:

```text
DEPLOY_HOST=10.1.2.253
DEPLOY_SSH_PORT=22
DEPLOY_SSH_USER=<FORNITO_TRAMITE_CANALE_SICURO>
DEPLOY_SSH_KEY_PATH=<PATH_LOCALE_OPZIONALE>
REMOTE_PROJECT_DIR=/opt/bh-dic
```

Non salvare questi valori, una passphrase o una chiave privata nella repository.

## Gate SSH

1. Ottenere la fingerprint host da un canale amministrativo indipendente.
2. Confrontarla con la chiave presentata dal server; non usare
   `StrictHostKeyChecking=no` e non accettare alla cieca una chiave cambiata.
3. Usare un agent o un file chiave con permessi stretti.

Comando previsto dopo la verifica della fingerprint:

```bash
ssh -p 22 -o StrictHostKeyChecking=yes -i <DEPLOY_SSH_KEY_PATH> \
  <DEPLOY_SSH_USER>@10.1.2.253
```

Se la chiave è già nell'agent, omettere `-i`. Non inserire password nella riga di comando e non
modificare `sshd_config` o firewall.

## Procedura di preparazione

Una volta sbloccato l'accesso:

```bash
ssh <USER>@10.1.2.253
cd /opt/bh-dic
cp -n .env.example .env
chmod 600 .env
nano .env
./scripts/install.sh
./scripts/doctor.sh
./scripts/status.sh
```

Se il progetto non è in `/opt/bh-dic`, sostituire il percorso in tutti i comandi. La repository
privata va clonata con una credenziale a privilegi minimi che non compaia nel remote.

La configurazione deve mantenere:

```dotenv
OPENAI_STORE=false
ENABLE_WRITE_ACTIONS=false
ENABLE_LIVE_WRITE_TESTS=false
```

Non registrare slash command e non eseguire `start.sh` durante la sola preparazione. I comandi
seguenti appartengono alla successiva attivazione, che richiede autorizzazione distinta:

```bash
./scripts/register-commands.sh
./scripts/start.sh
./scripts/status.sh
./scripts/logs.sh all --follow
./scripts/stop.sh
```

## Stato di consegna atteso

- codice e `.venv` presenti;
- Chromium e dipendenze verificati;
- database migrato e audit inizializzabile;
- `.env.example` presente; `.env` assente o protetto e valorizzato localmente;
- directory e file con i permessi documentati;
- `doctor.sh` riuscito, con risultato online separato se autorizzato;
- bot fermo, nessun PID/lock stale, nessun servizio systemd abilitato;
- nessun processo Chromium/Playwright residuo;
- report senza token, PII, cookie o contenuti HR.

Un file systemd di esempio può essere revisionato, ma non va installato né abilitato senza una
richiesta successiva.

## Rollback

Prima di ogni aggiornamento:

```bash
./scripts/stop.sh
./scripts/backup.sh
./scripts/audit-verify.sh
```

In caso di fallimento non forzare l'avvio. Ripristinare soltanto un backup verificato seguendo
[Backup/restore](BACKUP_RESTORE.md), quindi rieseguire migrazioni, doctor e test. Un esito write
incerto va riconciliato; non va ripetuto automaticamente.

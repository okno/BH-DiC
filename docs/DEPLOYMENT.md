# Deployment

## Stato corrente

La preparazione su Debian 12 in `/opt/bh-dic` è completata. Sono stati verificati Python 3.12
installato senza sostituire il Python di sistema, virtualenv e dipendenze, migrazione SQLite,
directory runtime private, Chromium Playwright, ClamAV tramite socket `0660`, audit, smoke mock,
doctor offline/online e Groq `openai/gpt-oss-120b` con `model-check --live`.

Il bot è **fermo** e deve restarlo. Un accesso manuale autorizzato, in un browser fresco e in sola
lettura, ha accettato le credenziali con un solo submit e ha raggiunto la route e il marker esatti
della lista dipendenti. Il tentativo headless 0.2.4 ha invece rifiutato la callback DIC legittima
prima di completare attestazione tenant e vault. La 0.2.5 corregge quel passaggio, ma adapter
headless, tenant e vault server restano da verificare; nessuna Function ID DIC read/write è stata
collaudata live. `ENABLE_WRITE_ACTIONS=false`, `ENABLE_LIVE_WRITE_TESTS=false` e tutte le flag
write specifiche restano `false`.

## Gate SSH

1. Ottenere la fingerprint host da un canale amministrativo indipendente.
2. Confrontarla con la chiave presentata dal server; non usare
   `StrictHostKeyChecking=no` e non accettare alla cieca una chiave cambiata.
3. Usare un agent o un file chiave con permessi stretti.

Comando previsto dopo la verifica della fingerprint:

```bash
ssh -p 22 -o StrictHostKeyChecking=yes -i <DEPLOY_SSH_KEY_PATH> \
  <DEPLOY_SSH_USER>@<DEPLOY_HOST>
```

Se la chiave è già nell'agent, omettere `-i`. Non inserire password nella riga di comando e non
modificare `sshd_config` o firewall.

## Aggiornamento dalla release precedente

Eseguire soltanto dopo la pubblicazione della release correttiva e con il bot confermato fermo:

```bash
cd /opt/bh-dic
./scripts/status.sh
./scripts/backup.sh
./scripts/update.sh
./scripts/doctor.sh
.venv/bin/python -m bh_dic validate-config
.venv/bin/python -m bh_dic model-check
```

`update.sh` richiede un worktree pulito e aggiorna solo fast-forward. Non usare `--restart` in
questa fase. Non mostrare `.env`; conservarlo con owner del servizio e modalità `0600`.
Verificare che `.venv/bin/python -m bh_dic version` riporti `0.2.5` prima del gate DIC.

La configurazione deve mantenere:

```dotenv
MODEL_STORE=false
ENABLE_WRITE_ACTIONS=false
ENABLE_LIVE_WRITE_TESTS=false
```

## Sblocco autenticazione DIC

Assicurarsi che `DIC_PASSWORD` nel secret store o nell'editor locale corrisponda alla credenziale
corrente e invalidare l'eventuale vecchia sessione. Non passare la password nella command line,
nei log o in ticket. Il login manuale fresco prova soltanto che la credenziale è stata accettata:
non attesta l'adapter headless, il tenant configurato o il vault server.

Con le write ancora disabilitate:

```bash
./scripts/doctor.sh --online
.venv/bin/python -m bh_dic model-check --live
.venv/bin/python -m bh_dic dic-auth-check
.venv/bin/python -m bh_dic dic-auth-check --live
./scripts/status.sh
```

Il controllo live deve terminare con autenticazione e tenant verificati e deve lasciare un vault
cifrato valido. Un redirect fuori dall'allowlist esatta DIC/TeamSystem, un tenant non attestabile,
MFA/CAPTCHA o un nuovo cambio password impongono stop ed escalation umana.

La 0.2.5 tratta l'esatta `/it/callback` DIC come transitoria e soltanto entro il budget residuo.
La query opaca non viene letta o registrata; fragment, porta esplicita, userinfo, host somigliante,
trailing slash e path aggiuntivi restano rifiutati. Il marker e `/data/company/id` restano
obbligatori. Eseguire `dic-auth-check --live` una sola volta dopo il deployment: un nuovo exit 78
impone stop e non autorizza un secondo tentativo.

Solo dopo questo esito, con autorizzazione distinta, proseguire:

```bash
./scripts/register-commands.sh
./scripts/start.sh
./scripts/status.sh
./scripts/logs.sh all --follow
```

`doctor --online` e `model-check --live` non attestano DIC. `dic-auth-check --live` può contattare
DIC e l'IdP TeamSystem, ma non esegue Function ID HR. Non registrare comandi e non avviare se uno
dei gate fallisce.

## Stato di consegna atteso

- codice e `.venv` presenti (verificato);
- Chromium, dipendenze e ClamAV verificati;
- database migrato e audit verificato;
- `.env.example` presente; `.env` assente o protetto e valorizzato localmente;
- directory e file con i permessi documentati;
- `doctor.sh` riuscito, con risultato online separato se autorizzato;
- bot fermo e nessun PID/lock stale;
- nessun processo Chromium/Playwright residuo;
- report senza token, PII, cookie o contenuti HR.

Se viene scelto systemd, usare esclusivamente `systemctl`/`journalctl` per il lifecycle; non
mescolare l'unit con gli script PID `start.sh`, `stop.sh` o `restart.sh`. La procedura completa
Debian, provider, Discord e prima verifica read-only è in
[Installazione e runbook](INSTALLATION.md).

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

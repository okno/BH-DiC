# Deployment

## Stato corrente

La preparazione su Debian 12 in `/opt/bh-dic` è completata. Sono stati verificati Python 3.12
installato senza sostituire il Python di sistema, virtualenv e dipendenze, migrazione SQLite,
directory runtime private, Chromium Playwright, ClamAV tramite socket `0660`, audit, smoke mock,
doctor offline/online e Groq `openai/gpt-oss-120b` con `model-check --live`.

Il bot è **fermo** e deve restarlo. La verifica DIC headless è bloccata dalla password
TeamSystem scaduta e dall'assenza di un vault autenticato; nessuna Function ID DIC read/write è
stata collaudata live. `ENABLE_WRITE_ACTIONS=false`, `ENABLE_LIVE_WRITE_TESTS=false` e tutte le
flag write specifiche restano `false`.

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

La configurazione deve mantenere:

```dotenv
MODEL_STORE=false
ENABLE_WRITE_ACTIONS=false
ENABLE_LIVE_WRITE_TESTS=false
```

## Sblocco autenticazione DIC

Un amministratore deve prima completare il cambio della password scaduta nel normale flusso
TeamSystem, poi aggiornare `DIC_PASSWORD` nel secret store o nell'editor locale e invalidare
l'eventuale vecchia sessione. Non passare la password nella command line, nei log o in ticket.

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

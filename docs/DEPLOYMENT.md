# Deployment

## Stato corrente

La preparazione su Debian 12 in `/opt/bh-dic` è completata. Sono stati verificati Python 3.12
installato senza sostituire il Python di sistema, virtualenv e dipendenze, migrazione SQLite,
directory runtime private, Chromium Playwright, ClamAV tramite socket `0660`, audit, smoke mock,
doctor offline/online e Groq `openai/gpt-oss-120b` con `model-check --live`.

Il check headless 0.2.5 ha restituito sessione `AUTHENTICATED` e tenant
`VERIFIED_BY_ADAPTER` nel processo corrente. Il comando guild-scoped è registrato e il gateway ha
risposto, ma il primo smoke è stato negato dal gate RBAC. Un riavvio successivo della release
precedente alla 0.2.7 ha perso lo stato federato non incluso nel vecchio vault e si è fermato con
exit 78 a `TEAMSYSTEM_EMAIL`, prima del gateway. La 0.2.7 corregge restore e avvio degradato, ma
deve ancora essere distribuita e verificata sul target; nessuna Function ID DIC è collaudata live.
`ENABLE_WRITE_ACTIONS=false`, `ENABLE_LIVE_WRITE_TESTS=false` e tutte le flag write specifiche
restano `false`.

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
Verificare che `.venv/bin/python -m bh_dic version` riporti `0.2.7` prima del gate DIC.

La configurazione deve mantenere:

```dotenv
MODEL_STORE=false
ENABLE_WRITE_ACTIONS=false
ENABLE_LIVE_WRITE_TESTS=false
```

## Sblocco autenticazione DIC

Assicurarsi che `DIC_PASSWORD` nel secret store o nell'editor locale corrisponda alla credenziale
corrente. Non invalidare automaticamente un vault leggibile durante l'upgrade: la 0.2.7 può
aggiornarlo al formato completo. Non passare la password nella command line,
nei log o in ticket. Il login manuale fresco prova soltanto che la credenziale è stata accettata:
non attesta l'adapter headless, il tenant configurato o il vault server.

Con le write ancora disabilitate:

```bash
./scripts/doctor.sh --online
.venv/bin/python -m bh_dic model-check --live
.venv/bin/python -m bh_dic dic-auth-check --live
```

Il check DIC senza `--live` valida soltanto un vault già esistente. Su una prima installazione o
dopo un'invalidazione intenzionale fallisce correttamente perché non esiste alcuna sessione
cifrata; non usarlo come prerequisito del bootstrap live. Un vault illeggibile blocca il check
esplicito finché l'operatore non decide se conservarlo per analisi o invalidarlo; il normale
gateway può comunque avviarsi `DEGRADED` senza sovrascriverlo.

Il controllo live deve terminare con autenticazione e tenant verificati e deve lasciare un vault
cifrato valido. Un redirect fuori dall'allowlist esatta DIC/TeamSystem, un tenant non attestabile,
MFA/CAPTCHA o un nuovo cambio password impongono stop ed escalation umana.

La 0.2.5 tratta l'esatta `/it/callback` DIC come transitoria e soltanto entro il budget residuo.
La query opaca non viene letta o registrata; fragment, porta esplicita, userinfo, host somigliante,
trailing slash e path aggiuntivi restano rifiutati. Il marker e `/data/company/id` restano
obbligatori. Eseguire `dic-auth-check --live` una sola volta dopo il deployment: un nuovo exit 78
impone stop e non autorizza un secondo tentativo. La 0.2.7 accetta inoltre soltanto
`LoginEmail`/`LoginPassword` TeamSystem esatte, vincola l'identità del form all'account configurato
prima del segreto e conserva cifrato anche `sessionStorage`; lo user agent Chromium resta nativo.

Indipendentemente dall'esito DIC, il gateway 0.2.7 può essere avviato senza inviare credenziali.
Se il check non riesce, `/bh status` deve mostrare `DEGRADED` e tutte le operazioni DIC restano
fail-closed. Gli slash command sono già registrati e non vanno sincronizzati di nuovo per cambiare
ruoli o `.env`. Proseguire con systemd:

```bash
systemctl start bh-dic.service
systemctl is-active bh-dic.service
journalctl -u bh-dic.service -n 100 --no-pager -o cat
```

`doctor --online` e `model-check --live` non attestano DIC. `dic-auth-check --live` può contattare
DIC e l'IdP TeamSystem, ma non esegue Function ID HR. Un fallimento di configurazione, DB o browser
resta bloccante; l'indisponibilità della sola sessione DIC è invece uno stato degradato esplicito.

## Stato di consegna atteso

- codice e `.venv` presenti (verificato);
- Chromium, dipendenze e ClamAV verificati;
- database migrato e audit verificato;
- `.env.example` presente; `.env` assente o protetto e valorizzato localmente;
- directory e file con i permessi documentati;
- `doctor.sh` riuscito, con risultato online separato se autorizzato;
- gestore systemd unico, servizio `active/running` dopo il deployment 0.2.7 e risposta di
  `/bh status` anche quando DIC è `DEGRADED`;
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

# Deployment

## Stato corrente

La preparazione su Debian 12 in `/opt/bh-dic` è completata. Sono stati verificati Python 3.12
installato senza sostituire il Python di sistema, virtualenv e dipendenze, migrazione SQLite,
directory runtime private, Chromium Playwright, ClamAV tramite socket `0660`, audit, smoke mock,
doctor offline/online e Groq `openai/gpt-oss-120b` con `model-check --live`.

Lo SHA `3d9283a8070aa3f73bd061adc3b608bb1440c1b5` è stato distribuito e ha superato il gate
live autorizzato in sola lettura per autenticazione/tenant ed ogni risorsa read implementata. Il
servizio è risultato `active/running`, con zero riavvii osservati, gateway `discord_ready` e
messaggio startup outbound inviato con DiC disponibile. Non confondere questi PASS con un
round-trip inbound, che deve essere iniziato da un utente Discord reale autorizzato.
Gli hotfix successivi di soli script operativi o documentazione hanno gate/deployment separati e
non cambiano retroattivamente l'evidenza DIC associata a quello SHA.
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

Eseguire soltanto dopo la pubblicazione della release correttiva. Root orchestra esclusivamente
systemd; ogni operazione sul repository, sul virtualenv e sui dati runtime viene eseguita come
utente di servizio:

```bash
sudo systemctl stop bh-dic.service &&
test "$(sudo systemctl show -p ActiveState --value bh-dic.service)" = inactive &&
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin /bin/bash -c '
  set -Eeuo pipefail
  cd /opt/bh-dic
  ./scripts/update.sh
  ./scripts/doctor.sh
  ./scripts/audit-verify.sh
  .venv/bin/python -m bh_dic validate-config
  .venv/bin/python -m bh_dic model-check
  .venv/bin/python -m alembic -c migrations/alembic.ini current
' &&
sudo systemctl start bh-dic.service &&
sudo systemctl is-active bh-dic.service
```

`update.sh` rifiuta EUID `0`, verifica due volte che l'unit sia assente oppure `loaded/inactive`,
richiede che l'intero progetto appartenga all'utente corrente e aggiorna solo fast-forward. Esegue
controlli di dipendenze e import sia prima sia dopo l'installazione. Non usare `--restart` con
systemd: l'opzione resta riservata al gestore PID. Se un passaggio fallisce dopo lo stop, il
servizio resta fermo per l'analisi; non tentare un avvio o un `chown -R` automatico. Non mostrare
`.env`; conservarlo con owner del servizio e modalità `0600`.
Verificare che `.venv/bin/python -m bh_dic version` riporti `0.3.0` e che Alembic sia alla revisione
`0002_model_usage` prima del gate DIC. La migrazione non salva prompt o dati HR: crea la sola
telemetria locale di provider/modello, stato e contatori dichiarati.

La configurazione deve mantenere:

```dotenv
MODEL_STORE=false
ENABLE_WRITE_ACTIONS=false
ENABLE_LIVE_WRITE_TESTS=false
```

## Sblocco autenticazione DIC

Assicurarsi che `DIC_PASSWORD` nel secret store o nell'editor locale corrisponda alla credenziale
corrente. Un semplice upgrade non richiede invalidazione automatica, ma una rotazione di
password/account/tenant sì: il vault corrente precede la rotazione e deve essere invalidato
deliberatamente una sola volta, con servizio fermo. Non passare la password nella command line,
nei log o in ticket. Il login manuale fresco prova soltanto che la credenziale è stata accettata:
non attesta l'adapter headless, il tenant configurato o il vault server.

Con le write ancora disabilitate, i check provider possono essere eseguiti come utente di
servizio:

```bash
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin /bin/bash -c '
  set -Eeuo pipefail
  cd /opt/bh-dic
  ./scripts/doctor.sh --online
  .venv/bin/python -m bh_dic model-check --live
'
```

Nello scenario di rotazione usare esclusivamente la [procedura canonica guarded](DIC_AUTHENTICATION.md#invalidazione-e-rotazione),
che verifica lo stop systemd ed esegue `invalidate-session` e `dic-auth-check --live` come
`bh-dic`, ciascuno esattamente una volta. Non ripetere l'invalidazione per ottenere altri tentativi.

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
La 0.2.8 ammette come schermata e-mail anche la root TeamSystem HTTPS esatta corrente e
tratta esclusivamente `/connect/authorize` e `/connect/authorize/callback` esatte come stati
pending bounded. Un SSO che salta i controlli non esegue azioni credenziali ed è accettato solo
dopo route applicativa DIC, marker autenticato e attestazione tenant. Qualunque esito
`CREDENTIAL_SUBMIT` resta non ritentabile.

Indipendentemente dall'esito DIC, il gateway dalla 0.2.7 può essere avviato senza inviare
credenziali.
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
- gestore systemd unico; nello snapshot corrente il servizio è `active/running`, con zero riavvii
  osservati e gateway `discord_ready`, in attesa del solo smoke Discord autorizzato;
- `/bh status` con provider/modello, stato API e token cumulativi locali; totale organico pubblico
  con `READ_ONLY`, scadenze individuali ephemeral con ruolo dedicato `HR_READ`;
- nessun processo Chromium/Playwright residuo;
- report senza token, PII, cookie o contenuti HR.

Se viene scelto systemd, usare esclusivamente `systemctl`/`journalctl` per il lifecycle; non
mescolare l'unit con gli script PID `start.sh`, `stop.sh` o `restart.sh`. La procedura completa
Debian, provider e smoke del trasporto Discord è in
[Installazione e runbook](INSTALLATION.md).

## Rollback

Prima di ogni aggiornamento:

```bash
sudo systemctl stop bh-dic.service &&
test "$(sudo systemctl show -p ActiveState --value bh-dic.service)" = inactive &&
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin /bin/bash -c '
  set -Eeuo pipefail
  cd /opt/bh-dic
  ./scripts/backup.sh
  ./scripts/audit-verify.sh
'
```

In caso di fallimento non forzare l'avvio. Ripristinare soltanto un backup verificato seguendo
[Backup/restore](BACKUP_RESTORE.md), quindi rieseguire migrazioni, doctor e test. Un esito write
incerto va riconciliato; non va ripetuto automaticamente.

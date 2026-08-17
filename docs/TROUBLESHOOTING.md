# Troubleshooting

Partire sempre da stato e log redatti:

```bash
./scripts/status.sh
./scripts/doctor.sh
./scripts/logs.sh all
./scripts/audit-verify.sh
```

Non abilitare write per diagnosticare. Dalla 0.2.7 Discord può restare online in modalità
degradata anche senza una sessione DIC verificata. Il
fallimento 0.2.2 è avvenuto durante l'hydration e il tentativo 0.2.3 allo stage `DIC_EMAIL`. La
0.2.4 ha inviato la password una sola volta e si è fermata con exit 78 perché la callback DIC
legittima non era ancora allowlistata. Un successivo accesso manuale autorizzato in browser fresco
ha confermato credenziali, callback, dashboard e marker della lista dipendenti; il check headless
0.2.5 ha poi attestato tenant e sessione nel processo corrente. Il riavvio successivo ha però
dimostrato che il vault pre-0.2.7 non conservava `sessionStorage`.
Un check 0.2.7 si è poi fermato prima delle azioni credenziali a `TEAMSYSTEM_EMAIL`; la 0.2.8 ha
ristretto il supporto alle route TeamSystem/OIDC esatte documentate. Il successivo gate 0.3.0 ha
attestato autenticazione/tenant e i due subset read bounded. Nello snapshot corrente il servizio è
`active/running`, con zero riavvii osservati e gateway `discord_ready`; lo smoke del trasporto
Discord resta `PENDING`.

| Sintomo | Verifica | Azione sicura |
|---|---|---|
| `bh-dic`/CLI non disponibile | `.venv/bin/python -m bh_dic --help` | reinstallare editable; non creare wrapper improvvisati |
| configurazione rifiutata | `doctor.sh`, `safe_summary()` | completare valori mancanti; non usare mock in production |
| `MODEL_STORE=true` rifiutato | `.env` locale | riportare a `false`; non cambiare il validatore |
| bot non parte | `systemctl status`, config, DB, Chromium e log | dalla 0.2.7 una sessione DIC mancante non blocca Discord; correggere l'errore di bootstrap senza avviare una seconda istanza |
| Discord mostra “L'applicazione non ha risposto” | `systemctl is-active bh-dic.service` e journal | il gateway è offline o non ha deferito entro il timeout; ripristinare prima il servizio, poi verificare permessi e RBAC |
| bot già attivo | `status.sh`, `ps` | non avviare una seconda istanza |
| slash command assenti | guild/application ID e scope | `register-commands.sh` nel solo guild |
| access denied Discord | `discord_access_denied` e `details.reason` in `discord.jsonl`; guild/channel/ruolo/thread | distribuire almeno la 0.2.6 se i log restano vuoti, poi correggere il mapping autorizzato senza allargare RBAC |
| JSONL e journal vuoti mentre il bot risponde | versione precedente alla 0.2.6; logger disabilitati dalla configurazione Alembic | aggiornare, riavviare il servizio e verificare `discord_ready`; non cambiare `LOG_LEVEL` per compensare |
| timeout/quota provider | log router, account provider, `model-check` offline/live autorizzato | verificare `MODEL_PROVIDER`, modello e chiave; retry limitato, mai bypassare intent validation |
| llama locale non raggiungibile | servizio locale, `LLAMA_BASE_URL`, modello | usare loopback e verificare il modello; non esporre la porta per aggirare il problema |
| Function ID non esposto | ruolo, scope, flag, catalogo | comportamento fail-closed previsto |
| login DIC fallisce | JSON `error_type`/`stage`, route DIC/TeamSystem, sessione, MFA/CAPTCHA | usare solo lo stage chiuso; invalidare il vault quando pertinente; mai stampare l'errore interno né usare passwordless o ampliare l'allowlist |
| `DicAuthUiChangedError` a `TEAMSYSTEM_EMAIL` | release installata, rotazione credenziale e transizione IdP | il fallimento storico è stato osservato sulla 0.2.7; dalla 0.2.8 sono ammesse la root e-mail TeamSystem esatta, la legacy `/Account/LoginEmail`, la password esatta e soltanto le transizioni bounded `/connect/authorize`/`callback`; dopo una rotazione invalidare deliberatamente il vecchio vault una volta, senza cambio User-Agent o fallback generici |
| `DicAuthOutcomeUnknownError`, exit 78 | stage `CREDENTIAL_SUBMIT`; invio forse partito ma completamento/tenant/vault non dimostrabili | non ritentare; mantenere DIC degradato e fare escalation, lasciando il gateway privo di credenziali online se necessario |
| attestazione tenant fallisce | route fissa, risposta first-party, schema/ID configurato | mantenere DIC degradato; nessun fallback su nome o DOM, patchare solo con nuova evidenza redatta |
| UI drift/selettore rotto | route/page object, trace protetto | smoke read-only e patch testata; nessuna write live |
| database locked | processi, WAL, filesystem | fermare processo concorrente; non cancellare WAL/SHM |
| migrazione fallisce | `alembic current/history` | backup, correggere schema; non marcare manualmente la revision |
| audit tamper | `audit-verify.sh` | stop, preservare evidenza, incident response; non riscrivere |
| ClamAV assente/timeout | daemon/socket/permessi | ripristinare scanner; quarantena fail-closed |
| MIME mismatch | metadata file/estensione | rifiutare; non rinominare per aggirare il controllo |
| pending scaduta | TTL/stato/audit | creare una nuova preview; non riattivare il record |
| A2 rifiutata | identità requester/A1/A2 | usare approvatore distinto e autorizzato |
| confirmation code già usato | consumed_at/audit | generare nuovo workflow; non resettare il digest |
| esito `UNKNOWN_*` | correlation/action ID, postcondizione | riconciliare; nessun retry automatico |
| disco pieno | `df -h`, `du -sh var/*` | stop sicuro, retention/backup; non cancellare audit arbitrariamente |
| SSH host key cambiata | fingerprint fuori banda | fermarsi e coinvolgere amministratore; mai disabilitare checking |
| systemd segnala `Unknown lvalue 'ConditionPathIsRegularFile'` | unit installata e release | distribuire l'unit 0.2.4, che usa `ConditionPathExists` più `ExecCondition=/usr/bin/test -f`; rieseguire `systemd-analyze verify` e `daemon-reload` a servizio fermo |

## Stage chiusi dell'autenticazione DIC

`dic-auth-check --live` non restituisce messaggi provider, URL, selettori o DOM. Gli stage hanno
soltanto questo significato operativo:

| Stage | Confine verificato | Azione sicura |
|---|---|---|
| `DIC_EMAIL`, `DIC_SUBMIT` | pagina login DIC esatta | verificare release e disponibilità del sito; dalla 0.2.4 è usato l'input nativo univoco nel contenitore pubblico, senza aggiungere click o selettori generici |
| `TEAMSYSTEM_EMAIL`, `TEAMSYSTEM_EMAIL_SUBMIT` | ingresso TeamSystem esatto: root e-mail corrente o legacy `/Account/LoginEmail`; può saltare alla password o completare SSO senza controlli | usare almeno la 0.2.8; invalidare una sola volta il vault se la credenziale è stata ruotata e fare un solo check live; verificare CAPTCHA/MFA o manutenzione IdP con procedura umana |
| `TEAMSYSTEM_CREDENTIAL`, `TEAMSYSTEM_CREDENTIAL_SUBMIT` | passaggio credenziale TeamSystem esatto e account del form uguale a `DIC_USERNAME` | non ripetere automaticamente la password; verificare stato account tramite il flusso umano |
| `CREDENTIAL_SUBMIT` | esito post-submit non dimostrabile | trattare exit 78 come stop non riavviabile; nessun nuovo login automatico o manuale senza revisione |
| `SESSION_PROBE` | verifica bounded di una sessione ripristinata già sulla route applicativa | non considerare autenticata una risposta oltre deadline; mantenere DIC degradato e verificare sessione/tenant senza nuovi submit |
| `UNCLASSIFIED` | errore non attribuibile a uno stage pubblico | fermare il bot, preservare solo log redatti ed effettuare escalation |

Il polling introdotto nella 0.2.3 è limitato dal budget di login, ricontrolla route/CAPTCHA e
rifiuta controlli visibili ambigui. La 0.2.4 seleziona il campo e-mail DIC come unico input nativo
sotto il contenitore pubblico `data-testid="login-email"`, invece del placeholder che nella 0.2.3
corrispondeva a padre e input. La 0.2.5 ammette poi soltanto l'esatta `/it/callback` DIC come stato
transitorio bounded: la query opaca non viene letta o registrata, mentre fragment, porta
esplicita, userinfo, host somigliante, trailing slash e path aggiuntivi restano rifiutati. Il marker
viene atteso entro lo stesso budget e `/data/company/id` resta obbligatorio. Non compensare un
errore aumentando indiscriminatamente i timeout o lanciando il comando in loop. Lo user agent
Chromium nativo non è risultato la causa e non va sostituito per aggirare controlli del sito.

Dalla 0.2.7 il gateway non esegue un login implicito: ripristina il vault se disponibile e resta
online anche quando DIC è `DEGRADED`. `/bh status` e `/bh health` continuano a rispondere, mentre
le funzioni DIC falliscono chiuso. Un nuovo invio di credenziali è consentito soltanto tramite un
singolo `dic-auth-check --live` esplicito con servizio fermo. Il vault include lo snapshot
`sessionStorage` bounded della sola origine DIC; una release precedente perdeva i token federati
al riavvio pur conservando cookie e `localStorage`.

La 0.2.8 mantiene il confronto esatto e aggiunge soltanto la root `/` come schermata
e-mail TeamSystem corrente e `/connect/authorize`/`/connect/authorize/callback` come transizioni
pending bounded. Un SSO che salta email/password non esegue alcuna azione credenziale ed è
considerato riuscito solo dopo route applicativa, marker DIC e attestazione tenant. Dopo una
rotazione di password/account/tenant, con servizio fermo:

```bash
.venv/bin/python -m bh_dic invalidate-session
.venv/bin/python -m bh_dic dic-auth-check --live
```

Eseguire ciascun comando una volta. Se il check restituisce `CREDENTIAL_SUBMIT`/exit 78, non
ritentare e non invalidare nuovamente per forzare un altro login.

L'unit systemd distribuita deve contenere `RestartPreventExitStatus=78`. Il normale comando `run`
non invia più credenziali; `dic-auth-check --live` usa 78 per l'esito ambiguo post-submit. Se la
direttiva manca nell'unit installata, mantenere il servizio disabled/stopped,
aggiornare l'unit dalla release approvata, verificare con `systemd-analyze verify` ed eseguire
`systemctl daemon-reload`; non avviare per provare la correzione.

Su Debian 12 l'unit dalla 0.2.4 non usa `ConditionPathIsRegularFile`, che non è supportata: verifica
l'esistenza di `.env` con `ConditionPathExists` e il tipo file regolare con
`ExecCondition=/usr/bin/test -f`. La modalità `0600` e la validità della configurazione restano
responsabilità di `doctor.sh`; non rimuovere il suo `ExecStartPre`.

## Stale PID/lock

`stop.sh` deve verificare che il PID appartenga a BH-DiC. Se `status.sh` segnala stale state,
confermare con `ps -p <PID> -o pid=,etime=,args=` e rimuovere PID/lock soltanto tramite lo script
o una procedura amministrativa revisionata. Non inviare `kill -9` automaticamente; `--force` è
l'ultima opzione esplicita.

## Escalation

Fermarsi e aprire un incidente privato quando compaiono segreti/PII nei log, audit non valido,
host key inattesa, possibile doppia write, accesso cross-tenant, malware o account compromesso.
Spegnere `ENABLE_WRITE_ACTIONS`, fermare il bot, revocare credenziali coinvolte e preservare
evidenza read-only. Vedere [Security architecture](SECURITY_ARCHITECTURE.md) e
[Debugging](DEBUGGING.md).

# Troubleshooting

Partire sempre da stato e log redatti:

```bash
./scripts/status.sh
./scripts/doctor.sh
./scripts/logs.sh all
./scripts/audit-verify.sh
```

Non avviare il bot o abilitare write per diagnosticare. Il runtime Debian è preparato e la
password TeamSystem è stata rinnovata nel flusso umano, ma il bot target resta fermo finché
autenticazione e tenant DIC non sono verificati. Il fallimento 0.2.2 è avvenuto prima
dell'autenticazione durante l'hydration dei controlli. Il tentativo 0.2.3 si è fermato allo stage
`DIC_EMAIL` perché il lookup per placeholder vedeva sia il componente padre sia l'input nativo.
Nessuno dei due esiti prova un rifiuto della nuova password; non esiste ancora una sessione
autenticata o un tenant verificato live.

| Sintomo | Verifica | Azione sicura |
|---|---|---|
| `bh-dic`/CLI non disponibile | `.venv/bin/python -m bh_dic --help` | reinstallare editable; non creare wrapper improvvisati |
| configurazione rifiutata | `doctor.sh`, `safe_summary()` | completare valori mancanti; non usare mock in production |
| `MODEL_STORE=true` rifiutato | `.env` locale | riportare a `false`; non cambiare il validatore |
| bot non parte | PID/lock, config, DB, log | rimuovere stale file solo se nessun processo BH-DiC esiste |
| bot già attivo | `status.sh`, `ps` | non avviare una seconda istanza |
| slash command assenti | guild/application ID e scope | `register-commands.sh` nel solo guild |
| access denied Discord | guild/channel/ruolo/thread | correggere mapping autorizzato, non allargare RBAC |
| timeout/quota provider | log router, account provider, `model-check` offline/live autorizzato | verificare `MODEL_PROVIDER`, modello e chiave; retry limitato, mai bypassare intent validation |
| llama locale non raggiungibile | servizio locale, `LLAMA_BASE_URL`, modello | usare loopback e verificare il modello; non esporre la porta per aggirare il problema |
| Function ID non esposto | ruolo, scope, flag, catalogo | comportamento fail-closed previsto |
| login DIC fallisce | JSON `error_type`/`stage`, route DIC/TeamSystem, sessione, MFA/CAPTCHA | usare solo lo stage chiuso; invalidare il vault quando pertinente; mai stampare l'errore interno né usare passwordless o ampliare l'allowlist |
| `DicAuthUiChangedError` durante login | stage `DIC_*` o `TEAMSYSTEM_*`, release installata | se `DIC_EMAIL` proviene dalla 0.2.3, distribuire la 0.2.4 e ripetere esattamente una verifica autorizzata soltanto dopo il deployment; zero/multipli controlli o route diversa restano fail-closed |
| `DicAuthOutcomeUnknownError`, exit 78 | stage `CREDENTIAL_SUBMIT`; invio forse partito ma completamento/tenant/vault non dimostrabili | non ritentare; mantenere bot fermo, verificare account/sessione con procedura umana e fare escalation |
| attestazione tenant fallisce | route fissa, risposta first-party, schema/ID configurato | mantenere il bot fermo; nessun fallback su nome o DOM, patchare solo con nuova evidenza redatta |
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
| `DIC_EMAIL`, `DIC_SUBMIT` | pagina login DIC esatta | verificare release 0.2.4 e disponibilità del sito; la 0.2.4 usa l'input nativo univoco nel contenitore pubblico, senza aggiungere click o selettori generici |
| `TEAMSYSTEM_EMAIL`, `TEAMSYSTEM_EMAIL_SUBMIT` | passaggio e-mail TeamSystem esatto | verificare eventuale CAPTCHA/MFA o manutenzione IdP con procedura umana |
| `TEAMSYSTEM_CREDENTIAL`, `TEAMSYSTEM_CREDENTIAL_SUBMIT` | passaggio credenziale TeamSystem esatto | non ripetere automaticamente la password; verificare stato account tramite il flusso umano |
| `CREDENTIAL_SUBMIT` | esito post-submit non dimostrabile | trattare exit 78 come stop non riavviabile; nessun nuovo login automatico o manuale senza revisione |
| `SESSION_PROBE` | verifica bounded di una sessione ripristinata già sulla route applicativa | non considerare autenticata una risposta oltre deadline; lasciare bot fermo e verificare sessione/tenant senza nuovi submit |
| `UNCLASSIFIED` | errore non attribuibile a uno stage pubblico | fermare il bot, preservare solo log redatti ed effettuare escalation |

Il polling introdotto nella 0.2.3 è limitato dal budget di login, ricontrolla route/CAPTCHA e
rifiuta controlli visibili ambigui. La 0.2.4 seleziona il campo e-mail DIC come unico input nativo
sotto il contenitore pubblico `data-testid="login-email"`, invece del placeholder che nella 0.2.3
corrispondeva a padre e input. Non compensare un errore aumentando indiscriminatamente i timeout o
lanciando il comando in loop.

L'unit systemd distribuita deve contenere `RestartPreventExitStatus=78`. Il comando `run` usa 78
per tutti gli errori di autenticazione, mentre `dic-auth-check --live` lo usa per l'esito ambiguo
post-submit. Se la direttiva manca nell'unit installata, mantenere il servizio disabled/stopped,
aggiornare l'unit dalla release approvata, verificare con `systemd-analyze verify` ed eseguire
`systemctl daemon-reload`; non avviare per provare la correzione.

Su Debian 12 l'unit 0.2.4 non usa `ConditionPathIsRegularFile`, che non è supportata: verifica
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

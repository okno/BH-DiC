# Troubleshooting

Partire sempre da stato e log redatti:

```bash
./scripts/status.sh
./scripts/doctor.sh
./scripts/logs.sh all
./scripts/audit-verify.sh
```

Non avviare il bot o abilitare write per diagnosticare. Nel workspace locale il processo bot è
assente; sul target il suo stato è `UNVERIFIED` e il deployment è `BLOCKED`.

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
| login DIC fallisce | URL, clock, sessione, MFA/CAPTCHA | invalidare sessione; escalation umana per MFA/CAPTCHA |
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

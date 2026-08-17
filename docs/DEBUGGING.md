# Debugging

Il debug deve preservare gli stessi confini di produzione. Non disabilitare TLS, host-key
checking, RBAC, redazione, ClamAV, audit o feature flag per ottenere un test verde. Usare solo
dati sintetici. Sul target sono stati verificati separatamente provider, autenticazione/tenant e
trasporto Discord, senza eseguire Function ID HR. La 0.2.7 è distribuita, ma il check DIC corrente
si è fermato a `TEAMSYSTEM_EMAIL`; la correzione candidata 0.2.8 ha gate locali verdi e verifica
live `PENDING`.

## Modalità DEBUG locale

In un ambiente isolato:

```dotenv
APP_ENV=development
MOCK_MODE=true
LOG_LEVEL=DEBUG
ENABLE_WRITE_ACTIONS=false
MODEL_STORE=false
```

Avviare in foreground soltanto quando lo script e la CLI risultano disponibili:

```bash
./scripts/run-foreground.sh
```

Non usare `DEBUG` permanente in produzione. Anche in DEBUG il logger redige i campi sensibili,
ma messaggi aggiunti manualmente possono essere pericolosi: non loggare request complete.

## Playwright headed, trace e screenshot

Trace e screenshot Playwright possono contenere credenziali, cookie e PII. Per questo motivo una
configurazione non-mock viene rifiutata se `PLAYWRIGHT_TRACE_MODE` non è `off` oppure
`SAVE_FAILURE_SCREENSHOTS` non è `false`. Non abilitarli durante `dic-auth-check --live`, sul
tenant reale o sul servizio di produzione.

Per eventuali harness di sviluppo usare esclusivamente componenti mock e dati sintetici:

```dotenv
APP_ENV=development
MOCK_MODE=true
DIC_HEADLESS=false
PLAYWRIGHT_TRACE_MODE=retain-on-failure
SAVE_FAILURE_SCREENSHOTS=true
TRACE_RETENTION_HOURS=4
ENABLE_WRITE_ACTIONS=false
```

Anche questi artefatti sintetici vanno salvati in `var/` con `0600`, non allegati automaticamente
a issue e cancellati alla scadenza. Le variabili di cattura non autorizzano né implementano una
cattura sul flusso live.

## Correlation ID

Ogni richiesta deve avere un correlation ID coerente tra log e audit. Cercarlo senza mostrare
payload:

```bash
./scripts/logs.sh all | jq 'select(.correlation_id == "<CORRELATION_ID>")'
rg -n --fixed-strings '<CORRELATION_ID>' var/log var/audit
```

Non usare employee ID reale come chiave di ricerca nei log.

## Diagnosi per componente

### Login DIC

- verificare origine HTTPS, username e tenant atteso con `doctor.sh`;
- invalidare `var/session/dic_session.enc` con la procedura operativa, senza aprirlo;
- controllare orologio e TOTP; non stampare il seed;
- MFA o CAPTCHA non devono essere aggirati: segnare il flusso `BLOCKED`.

### Token Discord e comandi mancanti

- fermare il bot e ruotare subito un token sospetto;
- verificare Application/Guild/Channel ID e scope `bot applications.commands`;
- eseguire `./scripts/register-commands.sh` nel solo guild;
- controllare che la modalità slash non richieda Message Content Intent.

### Errori del provider di modello

- verificare `MODEL_PROVIDER`, `MODEL_STORE=false`, credenziale/modello specifici, timeout e quota;
- per Groq non introdurre una base URL configurabile: deve restare
  `https://api.groq.com/openai/v1`;
- per llama verificare servizio loopback, `LLAMA_BASE_URL` e `LLAMA_MODEL` senza pubblicare
  prompt o output;
- usare prima `.venv/bin/python -m bh_dic model-check`, che è offline;
- usare `doctor.sh --online` e `model-check --live` solo se rete/costo sono autorizzati; il primo
  non autentica, il secondo fa una sola richiesta sintetica chiusa e non esegue tool;
- correlare request ID redatto senza loggare prompt o risposta completa;
- un errore provider deve fallire chiuso, non bypassare il router.

### Selettori rotti / UI drift

- acquisire route e nome page object dall'errore;
- riprodurre headed/trace soltanto in mock con fixture sintetiche;
- aggiornare il selector registry e fixture DOM sintetica/redatta in una modifica separata;
- eseguire unit test page object e smoke read-only autorizzato;
- non correggere un selettore direttamente in produzione e non provare una write.

### Database locked

```bash
./scripts/status.sh
./scripts/stop.sh
ls -l var/db
.venv/bin/python -m alembic -c migrations/alembic.ini current
```

Verificare processi concorrenti e filesystem. Non cancellare `-wal`/`-shm`, non copiare a caldo e
non modificare manualmente tabelle di approval/audit.

### ClamAV

- verificare daemon/socket e permessi con `doctor.sh`;
- un timeout o scanner assente mantiene il file in quarantena (`SCAN_ERROR`/rejected);
- non impostare `CLAMAV_REQUIRED=false` per sbloccare un upload;
- usare `./scripts/files.sh scan <UPLOAD_ID>` dopo il ripristino del servizio.

### Azione pending

```bash
./scripts/status.sh
./scripts/audit-verify.sh
```

Controllare TTL, stato, version/CAS, approvatori distinti e conferma monouso. Non recuperare o
mostrare il codice hashato e non modificare il record a mano.

### Esito incerto

`UNKNOWN_REQUIRES_RECONCILIATION` significa che un click potrebbe avere avuto effetto. Non fare
retry. Conservare correlation/action ID, lasciare il kill switch write spento e usare la
riconciliazione deterministica/postcondizione. Se lo stato esterno non è determinabile, escalation
umana e audit dell'esito sono obbligatori.

Vedere [Troubleshooting](TROUBLESHOOTING.md), [Logging](LOGGING.md) e
[Security architecture](SECURITY_ARCHITECTURE.md).

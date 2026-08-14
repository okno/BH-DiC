# Configurazione

`src/bh_dic/config.py` carica variabili d'ambiente e `.env` con Pydantic e fallisce in caso di
combinazioni non sicure. `.env.example` contiene solo nomi e default; copiarlo non rende il bot
pronto.

## Inizializzazione

```bash
umask 077
cp -n .env.example .env
chmod 600 .env
${EDITOR:-nano} .env
```

Non inviare `.env` in chat, ticket o log. In produzione sono obbligatori gli identificativi di
scope, le credenziali provider/DIC e chiavi di almeno 32 byte per audit, payload e sessione DIC.
Usare un secret manager o un canale amministrativo sicuro.

## Gruppi di variabili

| Gruppo | Variabili principali | Regola |
|---|---|---|
| Applicazione | `APP_ENV`, `APP_TIMEZONE`, `MOCK_MODE`, `DATA_DIR` | mock solo in `test`, `development` o `mock` |
| Persistenza | `DATABASE_URL`, `AUDIT_HMAC_KEY`, `ENCRYPTION_KEY` | solo `sqlite+aiosqlite` o `postgresql+asyncpg` |
| Discord | token, application/guild/channel ID, role ID | DMs false; scope guild/canale obbligatorio |
| OpenAI | API key, model, timeout, retry, output token | `OPENAI_STORE=false` è inderogabile |
| DIC | URL, user, password, TOTP opzionale, tenant, session key | origine HTTPS fissata; tenant atteso obbligatorio |
| Write | kill switch e flag specifici | tutti false; non abilitare nel rilascio corrente |
| File | limite, MIME, retention, ClamAV | upload richiede ClamAV fail-closed |
| Operazioni | PID, lock, log | path locali protetti, non condivisi via Git |

L'elenco completo e i default sono in [.env.example](../.env.example).

## Baseline sicura

```dotenv
OPENAI_STORE=false
DISCORD_ALLOW_DMS=false
ENABLE_READ_ACTIONS=true
ENABLE_WRITE_ACTIONS=false
ENABLE_LIVE_WRITE_TESTS=false
REQUIRE_TWO_PERSON_APPROVAL=true
CLAMAV_REQUIRED=true
SAVE_FAILURE_SCREENSHOTS=false
PLAYWRIGHT_TRACE_MODE=off
```

Ogni write richiede `ENABLE_WRITE_ACTIONS=true` **AND** il flag specifico **AND** policy/RBAC,
conferma, approvazioni e precondizioni runtime. Un override runtime può soltanto restringere una
baseline, mai abilitare un flag spento nella configurazione. Le azioni critiche richiedono A2
distinto dal richiedente e da A1.

`ENABLE_LIVE_WRITE_TESTS=true` è rifiutato senza kill switch globale, employee sintetico dedicato
e `DIC_TEST_TENANT_CONFIRMED=true`. Queste condizioni non costituiscono comunque autorizzazione;
nel rilascio corrente le write live sono vietate.

## Ruoli Discord

Gli ID sono interi positivi separati da virgole, senza nomi o menzioni:

```dotenv
DISCORD_HR_READ_ROLE_IDS=<ROLE_ID_1>,<ROLE_ID_2>
DISCORD_APPROVER_ROLE_IDS=<ROLE_ID_3>
```

Mappare soltanto ruoli già approvati. Il catalogo normativo RBAC è
`src/bh_dic/policies/catalog.py`; [policies.example.yaml](../config/policies.example.yaml) è un
esempio operativo restrict-only, non viene caricato automaticamente dal runtime e non può
ampliare il catalogo.

Analogamente, [redaction.example.yaml](../config/redaction.example.yaml) documenta il profilo
atteso: i controlli effettivi sono nel codice di logging/security/OpenAI. Modificare il file YAML
da solo non modifica la redazione runtime.

## OpenAI e tool exposure

Il provider riceve solo il testo minimizzato/redatto, il JSON schema e i Function ID che la
policy ha già reso visibili all'attore. Non riceve browser, file, segreti o la decisione finale.
L'output è trattato come candidato non attendibile e rivalidato. `store=false` è passato a ogni
richiesta ed `OPENAI_STORE=true` impedisce il caricamento della configurazione.

## Validazione senza stampare segreti

Con `.env` valorizzato:

```bash
./scripts/doctor.sh
.venv/bin/python -c "from bh_dic.config import AppSettings; print(AppSettings().safe_summary())"
```

La sintesi mostra solo metadati sicuri. Il controllo online è esplicito:

```bash
./scripts/doctor.sh --online
```

Non confondere la presenza dello script con un esito riuscito; conservare exit code e timestamp
del gate, non l'output contenente configurazioni.

## Modifica e rotazione

1. fermare il bot;
2. creare un backup che escluda `.env`;
3. revocare/ruotare il segreto presso il provider;
4. aggiornare `.env` localmente e ripristinare `0600`;
5. invalidare la sessione DIC se sono cambiate credenziali o chiave di sessione;
6. eseguire `doctor.sh`, audit verify e test;
7. riavviare solo se autorizzato.

Una rotazione di `AUDIT_HMAC_KEY` richiede una transizione documentata: cambiare la chiave senza
un checkpoint rende non verificabile la catena precedente. Vedere [Audit](AUDIT.md).

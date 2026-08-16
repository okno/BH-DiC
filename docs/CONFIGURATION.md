# Configurazione

`src/bh_dic/config.py` carica ambiente e `.env` con Pydantic e fallisce in caso di combinazioni
incomplete o non sicure. [.env.example](../.env.example) è un modello senza segreti: copiarlo non
rende il bot pronto.

## Inizializzazione

```bash
umask 077
cp -n .env.example .env
chmod 600 .env
${EDITOR:-nano} .env
```

Non inviare `.env` in chat, ticket o log. In produzione sono obbligatori gli identificativi di
scope, le credenziali del provider scelto e DIC e chiavi di almeno 32 byte per audit, payload e
sessione DIC. Usare un secret manager o un canale amministrativo sicuro.

## Gruppi di variabili

| Gruppo | Variabili principali | Regola |
|---|---|---|
| Applicazione | `APP_ENV`, `APP_TIMEZONE`, `MOCK_MODE`, `DATA_DIR` | mock solo in `test`, `development` o `mock` |
| Persistenza | `DATABASE_URL`, `AUDIT_HMAC_KEY`, `ENCRYPTION_KEY` | solo `sqlite+aiosqlite` o `postgresql+asyncpg` |
| Discord | token, application/guild/channel ID, role ID | DM false; guild e canale obbligatori |
| Modello | `MODEL_PROVIDER`, tuning `MODEL_*` | provider unico; storage sempre false |
| Credenziali modello | `OPENAI_*`, `GROQ_*` oppure `LLAMA_*` | valorizzare soltanto il provider selezionato |
| Persona | `BOT_LANGUAGE`, `BOT_TONE`, `BOT_ADDRESS_STYLE`, `BOT_VERBOSITY`, `BOT_EMOJI_MODE`, testi | cambia la resa, mai policy o autorizzazioni |
| DIC | URL, user, password, TOTP riservato, tenant, session key | origine HTTPS fissata; tenant numerico obbligatorio; MFA live fail-closed |
| Write | kill switch e flag specifici | tutti false; non abilitare nel rilascio corrente |
| File | limite, MIME, retention, ClamAV | upload richiede ClamAV fail-closed |
| Operazioni | PID, lock, log | path locali protetti, non condivisi via Git |

## Provider di modello

Configurazione comune canonica:

```dotenv
MODEL_PROVIDER=openai
MODEL_TIMEOUT_SECONDS=60
MODEL_MAX_RETRIES=2
MODEL_MAX_OUTPUT_TOKENS=1200
MODEL_REASONING_EFFORT=low
MODEL_STORE=false
MODEL_RESULT_RENDERING=deterministic
```

`MODEL_PROVIDER` accetta `openai`, `groq` o `llama`. Aggiungere le sole variabili specifiche:

```dotenv
# OpenAI
OPENAI_API_KEY=<SEGRETO_LOCALE>
OPENAI_MODEL=<MODELLO_APPROVATO>

# Groq
GROQ_API_KEY=<SEGRETO_LOCALE>
GROQ_MODEL=openai/gpt-oss-120b

# llama locale/OpenAI-compatible
LLAMA_BASE_URL=http://127.0.0.1:11434/v1
LLAMA_MODEL=<MODELLO_LOCALE>
# LLAMA_API_KEY=<SEGRETO_OPZIONALE>
```

Le base URL OpenAI e Groq sono fisse rispettivamente a `https://api.openai.com/v1` e
`https://api.groq.com/openai/v1` e non sono configurabili. Per `llama`, HTTP è ammesso soltanto su
loopback; il solo path `/v1` viene normalizzato e userinfo, query e fragment sono rifiutati.
`LLAMA_API_KEY` è opzionale soltanto su loopback e obbligatoria per un endpoint HTTPS remoto.
Dettagli e fonti ufficiali in [Provider di modello](OPENAI_SETUP.md).

Gli alias legacy `OPENAI_STORE`, `OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES`,
`OPENAI_MAX_OUTPUT_TOKENS`, `OPENAI_REASONING_EFFORT` e `OPENAI_RESULT_RENDERING` servono soltanto
alla migrazione verso `MODEL_*`. Non usarli in un nuovo `.env`; un valore legacy in conflitto con
quello canonico deve far fallire il caricamento.

## Persona del bot

Esempio italiano professionale:

```dotenv
BOT_LANGUAGE=it
BOT_TONE=professional
BOT_ADDRESS_STYLE=lei
BOT_VERBOSITY=standard
BOT_EMOJI_MODE=off
BOT_DISPLAY_NAME=BH-DiC
BOT_OPENING=
BOT_CLOSING=
```

Valori ammessi:

| Variabile | Valori |
|---|---|
| `BOT_LANGUAGE` | `it`, `en` (solo chiarimenti/decorazioni; output operativo italiano) |
| `BOT_TONE` | `professional`, `friendly`, `concise`, `empathetic` |
| `BOT_ADDRESS_STYLE` | `tu`, `lei`, `neutral` |
| `BOT_VERBOSITY` | `concise`, `standard`, `detailed` |
| `BOT_EMOJI_MODE` | `off`, `status` |
| `BOT_DISPLAY_NAME` | nome pubblico non sensibile |
| `BOT_OPENING`, `BOT_CLOSING` | testo opzionale, breve e non sensibile |

I default sono `it`, `professional`, `neutral`, `standard` e `off`; display name, apertura e
chiusura vuoti diventano assenti, così il profilo predefinito non modifica gli embed. Il display
name è limitato a 48 caratteri, apertura e chiusura a 120.
Testo di decorazione con mention, URL, materiale simile a token, caratteri di controllo o
istruzioni/prompt injection viene rifiutato.

La persona controlla presentazione locale e soltanto lo stile di un'eventuale domanda di
chiarimento. Display name, apertura e chiusura non vengono inviati al provider. Il profilo non
traduce dati/output operativi deterministici, che restano in italiano; non amplia il catalogo,
non cambia scope/RBAC e non rende meno esplicite preview o conferme. BH-DiC è un router HR
autorizzato, non un chatbot generalista né un sistema di moderazione Discord.

## Baseline sicura

```dotenv
MODEL_STORE=false
MODEL_RESULT_RENDERING=deterministic
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
atteso: i controlli effettivi sono nel codice di logging, security e provider. Modificare il file
YAML da solo non modifica la redazione runtime.

## Confine del provider e tool exposure

Il provider selezionato riceve solo testo minimizzato/redatto, JSON schema e Function ID che la
policy ha già reso visibili all'attore. Non riceve browser, file, segreti o la decisione finale.
L'output è un candidato non attendibile e viene rivalidato. `MODEL_STORE=true` è vietato.

## Validazione senza stampare segreti

Con `.env` valorizzato:

```bash
./scripts/doctor.sh
.venv/bin/python -c "from bh_dic.config import AppSettings; print(AppSettings().safe_summary())"
.venv/bin/python -m bh_dic model-check
```

La sintesi mostra gli enum stile e soltanto i booleani
`bot_display_name_configured`/`bot_opening_configured`/`bot_closing_configured`: non stampa i tre
testi. `model-check` mostra soltanto metadati provider sicuri ed è offline per default. I controlli
di rete/autenticazione sono espliciti e separati:

```bash
./scripts/doctor.sh --online
.venv/bin/python -m bh_dic model-check --live
```

Il doctor online verifica DNS/HTTP dell'endpoint selezionato, senza autenticazione. Il doctor non
contatta mai un database non-SQLite: per PostgreSQL il relativo stato migrazioni resta
`UNVERIFIED` e va controllato separatamente in una finestra di manutenzione autorizzata. Il
model-check live è opt-in anche per il costo e invia una sola richiesta sintetica chiusa, senza
PII né tool operativi. Non confondere la presenza dei comandi con un esito riuscito; conservare
exit code e timestamp, non configurazioni. Sul target preparato Groq e il modello selezionato
hanno esito `LIVE_VERIFIED`; OpenAI/llama, DIC e Discord restano stati separati e non vanno
promossi senza evidenza propria.

## Modifica e rotazione

1. fermare il bot con il gestore di processo scelto;
2. creare un backup che escluda `.env`;
3. revocare o ruotare il segreto presso il provider;
4. aggiornare `.env` localmente e ripristinare modo `0600`;
5. invalidare la sessione DIC se sono cambiate credenziali o chiave di sessione;
6. eseguire doctor, audit verify e test;
7. riavviare solo se autorizzato.

Una rotazione di `AUDIT_HMAC_KEY` richiede una transizione documentata: cambiare la chiave senza
un checkpoint rende non verificabile la catena precedente. Vedere [Audit](AUDIT.md).

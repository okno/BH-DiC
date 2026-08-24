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

## Persona Senior HR del bot

Esempio italiano amichevole e dettagliato, adatto a un Senior HR:

```dotenv
BOT_LANGUAGE=it
BOT_TONE=friendly
BOT_ADDRESS_STYLE=tu
BOT_VERBOSITY=detailed
BOT_EMOJI_MODE=status
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

La persona controlla il presenter Senior HR locale e soltanto lo stile di un'eventuale domanda di
chiarimento. Display name, apertura e chiusura non vengono inviati al provider. Il profilo non
inventa fatti, non amplia il catalogo, non cambia scope/RBAC e non rende meno esplicite preview o
conferme. Il tono amichevole non trasforma BH-DiC in un chatbot generalista né in un sistema di
moderazione Discord: ogni dato operativo deriva ancora dall'adapter tipizzato.

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

In ogni configurazione non-mock questi due valori sono invarianti: trace e screenshot vengono
rifiutati perché possono acquisire credenziali, cookie e PII durante il login DIC. La diagnostica
visuale è ammessa soltanto in un ambiente mock con dati sintetici.

Ogni write richiede `ENABLE_WRITE_ACTIONS=true` **AND** il flag specifico **AND** policy/RBAC,
conferma, approvazioni e precondizioni runtime. Un override runtime può soltanto restringere una
baseline, mai abilitare un flag spento nella configurazione. Le azioni critiche richiedono A2
distinto dal richiedente e da A1.

`ENABLE_LIVE_WRITE_TESTS=true` è rifiutato senza kill switch globale, employee sintetico dedicato
e `DIC_TEST_TENANT_CONFIRMED=true`. Queste condizioni non costituiscono comunque autorizzazione;
nel rilascio corrente le write live sono vietate.

`ENABLE_DIC_RECONNECT=true` abilita il solo comando amministrativo `/bh dic reconnect`. Non è una
write sui dati HR e non dipende dal kill switch globale, ma richiede un ruolo Discord mappato a
`SECURITY_ADMIN` o `SYSTEM_ADMIN`. Il comando usa esclusivamente le credenziali già presenti nel
file `.env` protetto, esegue al massimo un submit, attesta il tenant e salva il nuovo stato nel
vault cifrato. Credenziali, cookie e stato browser non transitano mai su Discord o nei log.

## Ruoli Discord

Gli ID sono interi positivi separati da virgole, senza nomi o menzioni:

```dotenv
DISCORD_HR_READ_ROLE_IDS=<ROLE_ID_1>,<ROLE_ID_2>
DISCORD_APPROVER_ROLE_IDS=<ROLE_ID_3>
```

Le autorizzazioni ai campi sensibili sono separate dal ruolo di lettura generale. Un esempio per
un ruolo HR umano approvato è:

```dotenv
DISCORD_PII_ROLE_IDS=<HR_ROLE_ID>
DISCORD_PAYROLL_ROLE_IDS=<HR_ROLE_ID>
DISCORD_DOCUMENT_METADATA_ROLE_IDS=<HR_ROLE_ID>
DISCORD_PROTECTED_DOCUMENT_ROLE_IDS=<HR_ROLE_ID>
DISCORD_BALANCE_ROLE_IDS=<HR_ROLE_ID>
```

`PII` abilita i campi identificativi previsti dal presenter; `PAYROLL` i valori di cedolino;
`DOCUMENT_METADATA` i metadati; `PROTECTED_DOCUMENT` l'eventuale consegna del file protetto;
`BALANCE` ferie/permessi. Nessuna di queste variabili concede scritture, approvazioni o privilegi
amministrativi.

Mappare soltanto ruoli già approvati. Il catalogo normativo RBAC è
`src/bh_dic/policies/catalog.py`; [policies.example.yaml](../config/policies.example.yaml) è un
esempio operativo restrict-only, non viene caricato automaticamente dal runtime e non può
ampliare il catalogo.

Essere owner del server o creatore dell'app non crea alcun bypass applicativo. Per consentire a
ogni membro del solo canale allowlistato i comandi informativi, Discord assegna a `@everyone` lo
stesso ID del guild e si può impostare `DISCORD_READONLY_ROLE_IDS=<DISCORD_GUILD_ID>`. Per dati HR
usare invece un ruolo umano dedicato in `DISCORD_HR_READ_ROLE_IDS`; non mappare `@everyone` a
write/admin. Procedura completa: [Configurazione Discord](DISCORD_SETUP.md).

`READ_ONLY` consente gli aggregati non sensibili e lo stato previsto dal catalogo; non consente
elenchi, Employee ID, contratti o scadenze individuali. Gli aggregati possono essere pubblicati
nel canale allowlistato; i risultati sensibili restano ephemeral salvo l'opt-in esplicito per il
canale HR privato descritto sotto. Per la domanda
“dipendenti con contratto a scadenza nel prossimo mese” assegnare quindi un ruolo umano dedicato
presente in `DISCORD_HR_READ_ROLE_IDS`, senza allargare `@everyone`.

Negli elenchi e nelle scadenze autorizzate `HR_READ`, il nome visualizzato può apparire in chiaro
nella risposta ephemeral o, con opt-in, nel canale HR privato. Nel modello applicativo è un
`SecretStr`: non compare in repr/dump, aggregati pubblici, provider, log, audit o telemetria.
E-mail, codice fiscale e matricola restano mascherati.

`DISCORD_INTERACTION_MODE` accetta `slash`, `mention` o `channel`. `slash` non legge i messaggi e
non richiede il Message Content Intent. `channel` considera soltanto il `DISCORD_CHANNEL_ID`,
ignora la chat non HR, usa il responder stateless per domande HR generali e invia le richieste
operative riconosciute allo stesso coordinator/RBAC di `/bh`. Richiede Message Content Intent e
Read Message History. Per rendere disponibili soltanto gli aggregati a tutti i membri usare
`DISCORD_READONLY_ROLE_IDS=<DISCORD_GUILD_ID>`; non ampliare `HR_READ` o ruoli privilegiati.

`DISCORD_PUBLISH_SENSITIVE_CHANNEL_RESPONSES=false` è il default. Impostarlo a `true` soltanto per
un canale HR privato dopo aver configurato almeno un ruolo umano dedicato in
`DISCORD_HR_READ_ROLE_IDS`; la validazione rifiuta combinazioni meno restrittive. I file generati
sono limitati da `EXPORT_MAX_MB` (default 8 MiB) e richiedono **Attach Files** sul ruolo bot.

I messaggi diretti sono disabilitati per default e non si fidano dei ruoli presenti nel payload
del messaggio. Per abilitarli il bot risolve ogni volta il membro nel guild configurato e richiede
un ruolo umano esplicito:

```dotenv
DISCORD_ALLOW_DMS=true
DISCORD_DM_AUTH_GUILD_ID=<DISCORD_GUILD_ID>
DISCORD_DM_ALLOWED_ROLE_IDS=<HR_ROLE_ID>
DISCORD_SENSITIVE_DELIVERY_MODE=dm_or_ephemeral
```

`DISCORD_MENTION_CHANNEL_IDS` limita la modalità `mention` a una allowlist di canali. I DM falliscono
in sicurezza se Discord non conferma guild, membership o ruolo; un DM chiuso non rende mai pubblico
il risultato sensibile.

Analogamente, [redaction.example.yaml](../config/redaction.example.yaml) documenta il profilo
atteso: i controlli effettivi sono nel codice di logging, security e provider. Modificare il file
YAML da solo non modifica la redazione runtime.

## Confine del provider e tool exposure

Nel percorso operativo `/bh` o `channel` il provider di routing riceve solo testo
minimizzato/redatto, JSON schema e Function ID già visibili; le operazioni chiuse quotidiane sono
risolte localmente. Per una domanda HR generale in `channel`, il responder riceve soltanto il
messaggio corrente ulteriormente redatto e nessun tool. Nessun provider riceve browser, file,
segreti, risultati DIC o la decisione finale. Ogni output è non attendibile e rivalidato/redatto.
`MODEL_STORE=true` è vietato.

La minimizzazione della 0.3.0 proietta la domanda su categorie semantiche canoniche chiuse, come
`employee_headcount`, `employment_contract` e `contract_deadline`, anziché inoltrare i vocaboli
utente grezzi. Nomi, valori di ricerca, Employee ID e termini liberi vengono rimossi o sostituiti
prima del trasporto, anche quando un nome coincide con una parola HR o un mese. Query di ricerca e
ID espliciti rimangono locali e vengono riassociati soltanto dopo il routing. Risposte DIC, DOM,
righe dipendente e analisi contrattuali non vengono mai inviate a OpenAI, Groq o llama.

## Telemetria token locale

La migrazione Alembic `0002_model_usage` crea una tabella a transizione unidirezionale per il
ciclo di vita delle chiamate modello. Conserva provider, modello, correlation key, ordinal, stato, timestamp e, solo
quando il provider li restituisce validi, `input_tokens`, `output_tokens` e `total_tokens`. Non
conserva prompt, testo utente, ID Discord, Employee ID o risultati DIC.

Gli stati sono `STARTED`, `REPORTED`, `UNAVAILABLE` e `UNKNOWN`. `REPORTED` usa esclusivamente i
contatori dichiarati dal provider; `UNAVAILABLE` indica risposta completata senza contatori;
`UNKNOWN` indica che l'esito remoto non può essere determinato. Il runtime non stima valori
mancanti. Il cumulativo è locale al database corrente, può includere gap e non equivale alla
fatturazione del provider.

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
# Copertura DIC live, rigorosamente read-only e senza identità/valori HR in output.
BH_DIC_ENABLE_LIVE_READ_COVERAGE=true ./scripts/run_live_read_coverage_gate.sh
```

Il doctor online verifica DNS/HTTP dell'endpoint selezionato, senza autenticazione. Il doctor non
contatta mai un database non-SQLite: per PostgreSQL il relativo stato migrazioni resta
`UNVERIFIED` e va controllato separatamente in una finestra di manutenzione autorizzata. Il
model-check live è opt-in anche per il costo e invia una sola richiesta sintetica chiusa, senza
PII né tool operativi. Non confondere la presenza dei comandi con un esito riuscito; conservare
exit code e timestamp, non configurazioni. Il gate DIC pagina l'elenco e prova le sole risorse di
lettura usando un identificativo esclusivamente in memoria; stampa stati e conteggi, mai nomi,
Employee ID, importi o documenti. Provider, DIC e Discord restano stati separati e non vanno
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

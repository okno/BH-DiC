# Provider di modello: OpenAI, Groq e llama locale

> Il nome del file è mantenuto per compatibilità con i link esistenti. La configurazione è ora
> multi-provider. Nessuna connessione live a OpenAI, Groq o a un endpoint llama è stata verificata
> in questa consegna.

Il modello è usato esclusivamente come router di intento. Non controlla browser o file, non riceve
credenziali e non decide autorizzazioni, approvazioni o risultati. L'applicazione espone solo i
Function ID già consentiti da scope e RBAC, tratta l'output come non attendibile e lo rivalida
localmente.

Il profilo persona passa al router soltanto opzioni chiuse per lo stile di un eventuale
chiarimento. `BOT_DISPLAY_NAME`, `BOT_OPENING` e `BOT_CLOSING` sono decorazioni locali e non vengono
inviati al provider.

## Contratto comune

Selezionare un solo provider e usare le variabili canoniche `MODEL_*`:

```dotenv
MODEL_PROVIDER=openai
MODEL_TIMEOUT_SECONDS=60
MODEL_MAX_RETRIES=2
MODEL_MAX_OUTPUT_TOKENS=1200
MODEL_REASONING_EFFORT=low
MODEL_STORE=false
MODEL_RESULT_RENDERING=deterministic
```

Valori ammessi per `MODEL_PROVIDER`: `openai`, `groq`, `llama`. `MODEL_STORE=false` e
`MODEL_RESULT_RENDERING=deterministic` sono invarianti di sicurezza; non abilitarne la modifica
per accomodare un provider. Timeout, retry e limite output valgono per tutti i provider. Un retry
del router non autorizza mai il retry di una write DIC.

Vincoli validati: timeout `1..300`, retry `0..5`, output token `64..8192`, reasoning effort
`none|low|medium|high`, storage soltanto `false` e rendering soltanto `deterministic`. Usare i
default finché test sintetici e limiti del provider non giustificano un valore più restrittivo.

Le vecchie variabili comuni con prefisso `OPENAI_` sono accettate soltanto come alias di
compatibilità durante la migrazione. Usare le forme `MODEL_*` nei nuovi deployment; valori
canonici e legacy in conflitto fanno fallire la configurazione. Le sole variabili OpenAI che
restano specifiche sono `OPENAI_API_KEY` e `OPENAI_MODEL`.

## OpenAI

Creazione della credenziale:

1. accedere alla [piattaforma API OpenAI](https://platform.openai.com/), creare o selezionare un
   progetto dedicato a BH-DiC e configurare budget/limiti prima della prova;
2. aprire [API keys](https://platform.openai.com/api-keys), creare una chiave di progetto con i
   privilegi minimi disponibili e copiarla una sola volta nel secret manager;
3. non usare una chiave personale condivisa e non salvarla in cronologia shell, Discord, ticket o
   Git; inserire il valore soltanto nel `.env` protetto del server;
4. scegliere nel progetto un modello compatibile con Responses API e function calling e impostarne
   l'identificativo esatto in `OPENAI_MODEL`.

```dotenv
MODEL_PROVIDER=openai
OPENAI_API_KEY=<SEGRETO_LOCALE>
OPENAI_MODEL=<MODELLO_APPROVATO>
MODEL_STORE=false
```

Creare una chiave dedicata al progetto BH-DiC, con budget e accesso minimi, e trasferirla al
server mediante secret manager o canale amministrativo sicuro. Scegliere un modello disponibile
nel progetto che supporti Responses API e function calling; disponibilità e policy devono essere
riconfermate prima dell'avvio. Riferimenti ufficiali: [Responses e scelta del
modello](https://developers.openai.com/api/docs/guides/latest-model) e [function
calling](https://developers.openai.com/api/docs/guides/function-calling).

La base URL OpenAI è fissata a `https://api.openai.com/v1` e non è configurabile.

`MODEL_STORE=false` viene applicato a ogni richiesta. Non inserire la chiave nella shell, nei log,
in ticket o in Git.

Se il progetto OpenAI autorizza il modello open-weight, usare l'ID indicato dalla
[scheda ufficiale `gpt-oss-120b`](https://developers.openai.com/api/docs/models/gpt-oss-120b);
l'ID prefissato `openai/gpt-oss-120b` è invece quello specifico del catalogo Groq.

## Groq

Creazione della credenziale GroqCloud:

1. accedere alla [GroqCloud Console](https://console.groq.com/), creare o selezionare un progetto
   dedicato e configurare limiti di spesa e permessi modello appropriati;
2. aprire [API Keys](https://console.groq.com/keys), creare una nuova chiave e trasferirla una sola
   volta al secret manager del server;
3. inserire il segreto soltanto come `GROQ_API_KEY` nel `.env` modo `0600`; non passarlo come
   argomento CLI e non conservarlo in output o file di documentazione;
4. lasciare `GROQ_MODEL=openai/gpt-oss-120b` per questa configurazione e verificarlo con il
   `model-check --live` chiuso prima dell'avvio del bot.

```dotenv
MODEL_PROVIDER=groq
GROQ_API_KEY=<SEGRETO_LOCALE>
GROQ_MODEL=openai/gpt-oss-120b
MODEL_STORE=false
```

La base URL è fissata nel codice a `https://api.groq.com/openai/v1`: non esiste una variabile per
sostituirla. Il modello predefinito è esattamente `openai/gpt-oss-120b`. Prima dell'uso operativo
verificare disponibilità, limiti e trattamento dati nell'account autorizzato. Riferimenti
ufficiali: [Responses API di Groq](https://console.groq.com/docs/responses-api), [compatibilità
OpenAI](https://console.groq.com/docs/openai) e [scheda del modello
`openai/gpt-oss-120b`](https://console.groq.com/docs/model/openai/gpt-oss-120b).

La compatibilità di protocollo non implica equivalenza comportamentale: schema, numero di tool
call e argomenti restano soggetti alla stessa validazione locale fail-closed. Quando il reasoning
effort è `none`, il parametro viene omesso verso Groq.

## llama su endpoint locale compatibile

```dotenv
MODEL_PROVIDER=llama
LLAMA_BASE_URL=http://127.0.0.1:11434/v1
LLAMA_MODEL=<MODELLO_LOCALE_INSTALLATO>
# LLAMA_API_KEY=<SEGRETO_OPZIONALE>
MODEL_STORE=false
```

`LLAMA_API_KEY` è opzionale soltanto per un endpoint loopback; un endpoint HTTPS remoto la
richiede. L'URL deve usare il solo path `/v1`, normalizzato senza slash finale, e non può includere
userinfo, query o fragment. L'URL predefinita corrisponde al servizio locale OpenAI-compatible di
Ollama; vedere la [documentazione ufficiale di compatibilità
OpenAI](https://docs.ollama.com/api/openai-compatibility). Un URL HTTP è accettabile soltanto su
loopback. Non esporre la porta `11434` alla LAN o a Internet senza un gateway autenticato e una
valutazione separata.

Il client llama usa la variante chat compatibile e omette parametri non interoperabili come
storage, parallel tool calls e reasoning effort; l'applicazione continua a imporre localmente una
sola tool call valida e nessuna persistenza della conversazione.

Installare e avviare il runtime locale fuori da BH-DiC, scaricare il modello autorizzato e
verificarne requisiti RAM/CPU/GPU. BH-DiC non installa, aggiorna né protegge il servizio llama. La
disponibilità del processo locale e la qualità del routing restano verifiche operative distinte.

## Minimizzazione e tool exposure

Prima della chiamata:

1. input e strutture vengono redatti e minimizzati;
2. scope Discord, tenant e ruoli determinano i Function ID visibili;
3. vengono costruiti soltanto i tool relativi a quei Function ID;
4. il provider deve restituire una sola function call conforme allo schema.

Dopo la risposta, nome tool, Function ID, schema, lunghezze, sensibilità e parametri sono
rivalidati. Un Function ID non esposto o un output ambiguo viene rifiutato. Il modello non può
eseguire una write: restituisce al massimo un candidato `PREPARE_WRITE`, poi l'applicazione applica
nuovamente policy, preview, conferma, A1/A2, idempotenza e kill switch.

Non inviare prompt completi, contenuti di documenti, payroll, codice fiscale, IBAN, e-mail,
telefono, indirizzo, cookie, TOTP, password o session state. Vedere
[Privacy/GDPR](PRIVACY_GDPR.md).

## Limiti, costo e osservabilità

- mantenere `MODEL_MAX_OUTPUT_TOKENS` al minimo utile;
- applicare rate limit Discord e timeout/retry limitati;
- configurare budget e avvisi nell'account OpenAI o Groq autorizzato;
- dimensionare e monitorare CPU/RAM/GPU per llama senza acquisire prompt;
- correlare errori soltanto tramite request ID e correlation ID redatti;
- non usare log provider o proxy come archivio conversazionale.

Il client disabilita redirect HTTP e proxy ereditati dalle variabili d'ambiente per impedire che
prompt o metadati vengano inoltrati a un'origine diversa da quella validata. Il server deve quindi
avere egress diretto verso l'endpoint scelto; un proxy applicativo non è supportato finché non
esiste una configurazione esplicita, validata e sottoposta a test. Sono inoltre rifiutate le
override ambientali dell'SDK (`OPENAI_CUSTOM_HEADERS`, `OPENAI_ADMIN_KEY`, `OPENAI_ORG_ID`,
`OPENAI_PROJECT_ID`, `OPENAI_WEBHOOK_SECRET`, `OPENAI_LOG`): non usarle nel servizio systemd.

## Verifica e promozione

Controllo locale senza rete:

```bash
./scripts/doctor.sh
.venv/bin/python -c "from bh_dic.config import AppSettings; print(AppSettings().safe_summary())"
.venv/bin/python -m bh_dic model-check
```

`model-check` senza opzioni è offline: mostra provider, modello, scope endpoint, storage e
tool-execution senza effettuare richieste e restituisce `UNVERIFIED_OFFLINE`.
`model-check --live` viene rifiutato quando `MOCK_MODE=true`.

Dopo autorizzazione esplicita a rete e costo, sempre prima di avviare il bot:

```bash
./scripts/doctor.sh --online
.venv/bin/python -m bh_dic model-check --live
```

`doctor.sh --online` seleziona l'host del provider configurato, ma verifica soltanto DNS/HTTP e non
autenticazione o disponibilità del modello. `model-check --live` effettua esattamente una richiesta
sintetica senza PII, con insieme dei Function ID ammessi vuoto: l'unico esito conforme è
`unsupported_request`. Non costruisce Discord, DIC o browser e non esegue tool. Un esito
`LIVE_VERIFIED` attesta soltanto autenticazione, disponibilità del modello e contratto chiuso del
provider in quel momento; non autorizza il bot né promuove DIC/deployment a verificati.

Prima di promuovere un provider eseguire inoltre con risorse sintetiche i casi locali allow/deny,
schema non valido, tool non esposto, timeout e rate limit. Non stampare request o response complete.

## Rotazione e revoca

1. fermare il bot con il gestore di processo scelto;
2. creare una nuova chiave nel provider autorizzato oppure ruotare il segreto del gateway locale;
3. aggiornare `.env` localmente e verificare modo `0600`;
4. eseguire doctor offline e, se autorizzato, il gate online;
5. revocare la chiave precedente;
6. controllare log redatti, errori e consumo anomalo;
7. riavviare soltanto dopo approvazione.

Per errori provider vedere [Debugging](DEBUGGING.md) e [Logging](LOGGING.md).

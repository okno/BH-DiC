# Configurazione OpenAI

OpenAI è usato esclusivamente come router di intento. Non controlla browser o file, non riceve
credenziali e non decide autorizzazioni, approvazioni o risultati. Nessuna connessione provider
live è stata verificata in questa consegna.

## Chiave e progetto

1. Nel progetto OpenAI autorizzato creare una chiave dedicata a BH-DiC con limiti di spesa e
   accesso minimi.
2. Trasferire la chiave al server tramite secret manager o canale sicuro.
3. Inserirla soltanto nel `.env` protetto, mai nella riga di comando:

```dotenv
OPENAI_API_KEY=<SECRET_LOCALE>
OPENAI_MODEL=<MODELLO_APPROVATO>
OPENAI_STORE=false
OPENAI_TIMEOUT_SECONDS=60
OPENAI_MAX_RETRIES=2
OPENAI_MAX_OUTPUT_TOKENS=1200
OPENAI_RESULT_RENDERING=deterministic
OPENAI_REASONING_EFFORT=low
```

Scegliere un modello approvato che supporti Responses API e function calling strict. Il nome del
modello non è fissato nella repository perché disponibilità e policy possono cambiare; validarlo
nel progetto del committente prima dell'avvio.

`OPENAI_STORE=false` è un'invariante doppia: la configurazione rifiuta `true` e il client passa
`store=False` a ogni chiamata. Non modificare entrambi i controlli.

## Minimizzazione e tool exposure

Prima della chiamata:

1. input e strutture vengono redatti/minimizzati;
2. scope Discord, tenant e ruolo determinano i Function ID visibili;
3. vengono costruiti solo i tool relativi a quei Function ID;
4. il provider deve restituire esattamente una function call strict.

Dopo la risposta, nome tool, Function ID, schema, lunghezze, sensibilità e parametri sono
rivalidati localmente. Un Function ID non esposto o un output ambiguo viene rifiutato. OpenAI non
può eseguire una write: restituisce al massimo un candidato `PREPARE_WRITE`, poi l'applicazione
applica nuovamente policy, preview, conferma, A1/A2, idempotenza e kill switch.

Non inviare prompt completi, contenuti di documenti, payroll, codice fiscale, IBAN, e-mail,
telefono, indirizzo, cookie, TOTP, password o session state. Vedere [Privacy/GDPR](PRIVACY_GDPR.md).

## Verifica

Controllo locale senza rete:

```bash
./scripts/doctor.sh
.venv/bin/python -c "from bh_dic.config import AppSettings; print(AppSettings().safe_summary())"
```

Dopo autorizzazione alla connettività, usare il gate esplicito:

```bash
./scripts/doctor.sh --online
```

Il gate online deve verificare solo autenticazione/connettività con un input sintetico e non deve
contattare Dipendenti in Cloud o avviare il bot. Non stampare request/response complete. Una
risposta HTTP riuscita prova soltanto la connessione, non l'intero flusso applicativo.

## Utilizzo e costi

- mantenere `OPENAI_MAX_OUTPUT_TOKENS` al minimo utile;
- usare una sola tool call, `parallel_tool_calls=false`;
- applicare rate limit Discord e timeout/retry limitati;
- monitorare consumo nel progetto OpenAI senza copiare prompt sensibili;
- impostare budget e avvisi lato progetto;
- correlare errori con request ID/correlation ID redatti.

I retry del router non autorizzano retry delle write DIC. Un esito write incerto passa a
riconciliazione manuale/deterministica.

## Rotazione e revoca

1. fermare il bot;
2. creare una nuova chiave nel progetto autorizzato;
3. aggiornare `.env` localmente e verificare `0600`;
4. eseguire doctor offline e, se autorizzato, online;
5. revocare la chiave precedente;
6. controllare log redatti e spesa anomala;
7. riavviare soltanto con autorizzazione.

Per errori del provider vedere [Debugging](DEBUGGING.md) e [Logging](LOGGING.md).

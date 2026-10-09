# Planner bridge Codex / Ollama / LM Studio

## Obiettivo e confine di fiducia

La modalità raccomandata separa il bot operativo dal modello:

```text
Discord #mng-ai
  -> BH-DiC su Ubiquiti
     RBAC, contesto utente, nomi/ID, policy, conferme, adapter browser DiC
  -> endpoint loopback creato da un tunnel SSH inverso, con mTLS applicativo
  -> bridge su Mint, in ascolto solo su loopback
  -> Codex App Server su stdio oppure Ollama/LM Studio su loopback
```

Il modello è esclusivamente un planner. Riceve una frase canonica senza nomi, Employee ID,
documenti, importi o risultati DiC e al massimo otto Function ID già filtrati dalla policy.
Restituisce un solo JSON tipizzato. Ubiquiti rivalida il JSON e soltanto il registry locale può
invocare `DicService` e l'adapter deterministico.

Codex non riceve tool DiC, shell, browser, filesystem del progetto o credenziali. Anche se il
testo utente chiede di usare shell o navigare direttamente, il bridge non può farlo. Le scritture
restano nel flusso locale preview -> conferma monouso -> approvazione -> postcondizione; il modello
non può fornire parametri di scrittura né autorizzare un'azione.

Questa scelta segue il contratto dell'App Server documentato da OpenAI: stdio usa JSONL e il
client controlla thread, schema di output, sandbox e approvazioni. Il trasporto WebSocket è
indicato come sperimentale e non supportato per produzione, quindi BH-DiC usa l'SDK ufficiale
con un App Server effimero su stdio, non una porta App Server esposta.

- [Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [Codex App Server con accesso ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)

## Backend disponibili

Il file privato del bridge usa `BRIDGE_BACKEND`:

- `codex`: SDK Python `openai-codex` bloccato alla serie dichiarata nel progetto. Ogni richiesta
  crea un thread effimero in una directory vuota, sandbox read-only, approvazioni negate e feature
  tool disabilitate. Usare un account di servizio dedicato e una sessione ChatGPT Work dedicata;
  non copiare i file IPC o i token dell'app desktop.
- `ollama`: endpoint OpenAI-compatible locale, predefinito `http://127.0.0.1:11434/v1`.
- `lmstudio`: endpoint OpenAI-compatible locale. Anche in questo caso la URL deve essere HTTP su
  loopback, senza credenziali incorporate.

Ollama e LM Studio devono supportare `response_format=json_schema`. Il bridge imposta
`tool_choice=none`, temperatura zero, accetta una sola choice con `finish_reason=stop` e rifiuta
tool call, function call, output troncati e JSON fuori schema. Un modello che non rispetta questo
contratto risulta indisponibile: non viene degradato a testo libero.

I token vengono mostrati soltanto quando il backend restituisce contatori esatti. Il bot non
stima token mancanti e non li presenta come fatturazione.

## Memoria HR condivisa e contesto per utente

`BRIDGE_HR_MEMORY_PATH` può indicare un playbook YAML/Markdown validato come descritto in
`docs/HR_SHARED_MEMORY.md`. Lo stesso rendering canonico viene usato da tutti i backend. Il file
non può contenere PII o segreti e non può concedere permessi. Nella composizione attuale viene
caricato all'avvio del bridge: dopo una sostituzione atomica del file, riavviare soltanto il
servizio bridge.

La memoria conversazionale operativa resta su Ubiquiti ed è separata per utente, guild e canale.
Conserva per 15 minuti soltanto Employee ID opachi, Function ID, parametri scalari e nonce dei
menu. Serve per risposte come `lui`, `il secondo`, cognome o click sul menu; non salva la chat, i
nomi o le buste paga e scompare al riavvio.

## Configurazione Mint

1. Creare un account non privilegiato dedicato, senza home condivisa con l'utente desktop. Le
   directory di stato e lavoro devono essere di proprietà dell'account, modo `0700`, fuori da
   home utente e checkout Git; la directory lavoro deve essere vuota all'avvio.
2. Installare il progetto e le versioni da `requirements.lock` in un virtualenv dedicato.
   In alternativa, dopo aver verificato un checkout pulito e il runtime Python 3.12, usare
   `scripts/provision-bridge-host.sh` come root con `--source-dir`, `--python-runtime` e il
   `--expected-commit` completo. Lo script crea soltanto account, directory, installazione e unità:
   non genera credenziali, non abilita/avvia servizi e non modifica SSH o rete.
3. Copiare `config/bridge.env.example` fuori dal repository, impostare modo `0600` e compilare
   soltanto i percorsi necessari.
4. Generare una CA applicativa dedicata e certificati distinti server/client. Le chiavi devono
   essere file regolari, non symlink, di proprietà del relativo account e modo `0600`. Il
   certificato server deve includere il SAN di `BRIDGE_SERVER_NAME`.
5. Per Codex, inizializzare la sessione nell'esatto `BRIDGE_CODEX_STATE_ROOT` con il runtime
   bloccato dal virtualenv e il device login ChatGPT. Esempio concettuale, da adattare all'account
   di servizio:

   ```bash
   env HOME=/nonexistent CODEX_HOME=/var/lib/bh-dic-bridge/codex \
     /opt/bh-dic-bridge/.venv/lib/python3.12/site-packages/codex_cli_bin/bin/codex \
     login --device-auth
   ```

   Verificare poi `login status` nello stesso ambiente. Non usare `--with-api-key`. La sessione
   e il suo rinnovo sono un prerequisito operativo separato; non vanno copiati dal profilo
   desktop né inseriti nel repository.
6. Avviare `bh-dic bridge-serve --env-file /percorso/assoluto/bridge.env`. Il bind non-loopback
   viene rifiutato dal validatore.

L'unità di esempio `infrastructure/systemd/bh-dic-planner-bridge.service.example` applica
`ProtectHome`, filesystem read-only salvo lo state root, nessuna capability e nessun privilegio.

## Tunnel inverso e configurazione Ubiquiti

Il bridge Mint resta su `127.0.0.1:9443`. Un servizio SSH avviato su Mint può pubblicarlo come
una porta **loopback** su Ubiquiti:

```text
Ubiquiti 127.0.0.1:9443 -> reverse SSH -> Mint 127.0.0.1:9443
```

Usare un account SSH dedicato privo di shell interattiva, chiave dedicata, host key fissata in
un `known_hosts` privato, `BatchMode=yes`, `IdentitiesOnly=yes`,
`StrictHostKeyChecking=yes`, `ExitOnForwardFailure=yes` e autorizzazione limitata al solo remote
forward richiesto. L'esempio è
`infrastructure/systemd/bh-dic-bridge-tunnel.service.example`. La predisposizione del tunnel e
delle regole SSH è un'operazione di rete/amministrazione esplicita: il bot non le modifica.

Sul bot:

```dotenv
MODEL_PROVIDER=bridge
MODEL_STORE=false
BRIDGE_HOST=127.0.0.1
BRIDGE_PORT=9443
BRIDGE_SERVER_NAME=bh-dic-planner.local
BRIDGE_CA_PATH=/percorso/privato/server-ca.crt
BRIDGE_CLIENT_CERT_PATH=/percorso/privato/client.crt
BRIDGE_CLIENT_KEY_PATH=/percorso/privato/client.key
BRIDGE_MODEL=workspace-default
```

Rimuovere dal file di produzione `OPENAI_API_KEY` e `GROQ_API_KEY`. Discord e DiC rimangono
configurati soltanto su Ubiquiti; il file Mint non deve contenerne i segreti.

## Verifiche e rollout

1. `bh-dic validate-config` su Ubiquiti e avvio isolato del bridge su Mint.
2. `bh-dic model-check --live`: esegue una sola decisione sintetica, senza PII e senza DiC.
3. Test automatici e gate statici completi.
4. Shadow/canary su richieste sintetiche; verificare fallback locale, contatori e log privi di
   prompt/PII.
5. Attivare `MODEL_PROVIDER=bridge` e riavviare esclusivamente `bh-dic.service`.

Se tunnel, mTLS o backend non sono disponibili, le rotte deterministiche locali continuano a
funzionare. Dopo tre errori consecutivi il client apre per 30 secondi un circuit breaker e poi
consente una sola prova di recupero, evitando un fan-out di connessioni durante un guasto. Il bot
non inventa una rotta e non passa automaticamente a Groq/OpenAI API. Le scritture rimangono
disabilitate dai rispettivi kill switch.

L'onboarding da immagini è separato dal planner: il canale accetta una richiesta esplicita con
1-4 JPEG/PNG, esegue antivirus e Tesseract locali e prepara una bozza privata a TTL. Non crea
automaticamente un dipendente. PDF, creazione live e stato iniziale inattivo devono restare
disabilitati finché la relativa rotta DiC e la postcondizione non sono verificate su un record
sintetico dedicato.

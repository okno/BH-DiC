# Logging

BH-DiC usa JSON Lines: un evento JSON per riga, timestamp UTC e locale, versione e metadati di
correlazione. Il logger applicativo scrive in `LOG_DIR` (default `./var/log`).

## File

| File | Contenuto |
|---|---|
| `var/log/app.jsonl` | tutti gli eventi applicativi |
| `var/log/discord.jsonl` | eventi Discord |
| `var/log/openai.jsonl` | metadati del router multi-provider; nome file legacy stabile |
| `var/log/browser.jsonl` | adapter/browser |
| `var/log/audit.jsonl` | eventi operativi audit correlati |
| `var/log/security.jsonl` | deny, rate limit e controlli security |

I file sono creati `0600` dal runtime. Il processo deve essere eseguito da un utente dedicato;
non allargare i permessi per facilitare la consultazione.

Dalla release 0.2.6 la configurazione Alembic preserva i logger `bh_dic.*` già inizializzati. Le
release precedenti potevano lasciare vuoti i JSONL e il journal dopo la migrazione iniziale, pur
continuando a rispondere su Discord. Dopo l'aggiornamento è necessario riavviare il servizio per
ricreare gli handler nel processo del gateway.

## Lettura

```bash
./scripts/logs.sh app
./scripts/logs.sh discord
./scripts/logs.sh openai
./scripts/logs.sh browser
./scripts/logs.sh audit
./scripts/logs.sh security
./scripts/logs.sh all
./scripts/logs.sh all --follow
```

Esempi `jq`:

```bash
jq 'select(.level == "ERROR")' var/log/app.jsonl
jq 'select(.event_type | startswith("security."))' var/log/security.jsonl
jq 'select(.correlation_id == "<CORRELATION_ID>")' var/log/*.jsonl
```

Se una riga non è JSON valido, preservarla come evidenza e indagare; non riscrivere la catena
audit. I log applicativi e gli audit sono flussi diversi: [Audit](AUDIT.md) è la fonte
append-only verificabile.

## Campi e redazione

Campi standard: `timestamp_utc`, `timestamp_local`, `level`, `logger`, `event_type`, `message`,
`application_version`; quando presenti: `correlation_id`, guild/channel, Function ID, outcome,
duration ed error code.

La redazione centrale copre chiavi sensibili, bearer/provider token, IBAN, codice fiscale,
contenuti binari e target non pseudonimizzati. Non loggare comunque:

- prompt o risposta completi del provider di modello;
- valori `BOT_DISPLAY_NAME`, `BOT_OPENING` o `BOT_CLOSING`;
- Authorization/Cookie, password, TOTP, confirmation code o session state;
- nomi, contatti, indirizzi, contenuti HR o documenti;
- parametri pending in chiaro;
- screenshot/trace.

La redazione è difesa in profondità, non autorizzazione a passare PII al logger.
Il `SecretStr` del display name viene aperto esclusivamente nel renderer Discord
`SENSITIVE`/ephemeral: non inserirlo in `extra`, eccezioni, correlation metadata o messaggi.

La telemetria token 0.3.0 non è un log di conversazione. La tabella
`model_usage_events` conserva soltanto correlation key, purpose/ordinal, provider, modello, stato,
timestamp e contatori esatti quando disponibili. Prompt, testo utente, Employee ID e risultati DIC
non devono apparire né nella tabella né nei JSONL. `/bh status` espone soltanto aggregati locali e
gap; non stampare righe SQL per diagnosticarli.

## Correlazione e incidenti

Usare correlation ID e action ID, mai PII, per attraversare componenti. Gli eventi di deny,
approval, kill switch, scanner, audit chain e riconciliazione devono alimentare regole Wazuh;
vedere [Wazuh](WAZUH_INTEGRATION.md).

## Rotazione e retention

Il `FileHandler` applicativo non effettua rotazione autonoma. Il deployment deve configurare
`logrotate` o un collector equivalente, con copy/truncate valutato attentamente, ownership
preservata, compressione e retention approvata. Finché tale configurazione non è installata, la
rotazione è un **gate operativo aperto**.

La retention dei log non è la stessa degli upload (`UPLOAD_RETENTION_HOURS`) o dei trace
(`TRACE_RETENTION_HOURS`). Definire una retention separata coerente con GDPR, incident response e
obblighi di audit. Non includere log nei backup se non esplicitamente richiesto e cifrato.

Monitorare spazio senza stampare contenuti:

```bash
du -sh var/log
df -h var/log
```

Per privacy e accesso vedere [Privacy/GDPR](PRIVACY_GDPR.md).

# Implementation report

Questo documento descrive la codebase pubblica, non un ambiente distribuito. Host, identificativi,
revisioni installate, risultati live, stato dei servizi e change ticket devono restare in un registro
operativo privato.

## Architettura implementata

- Discord applica allowlist di guild/canale, ruolo corrente, capability e policy prima del dispatch.
- Il planner locale risolve intenti HR, persone, periodi e passi ordinati senza inviare nomi,
  Employee ID o risultati DIC al provider AI.
- Il provider sceglie soltanto fra un massimo di tre Function ID read compatibili con la famiglia
  canonica; non riceve browser, credenziali, documenti o primitive di navigazione.
- L'adapter Playwright usa route e azioni UI registrate e osserva soltanto risposte first-party
  allowlisted con controlli di origine, schema, paginazione, dimensione e tenant.
- I piani composti verificano ordine, budget di lettura, identità dei record e completezza prima di
  presentare un risultato.
- Sessione DIC e vault sono cifrati; CAPTCHA, MFA, password scaduta e outcome incerti richiedono un
  intervento umano e non generano retry automatici delle credenziali.
- Log, audit e telemetria escludono prompt, nomi, identificatori dipendente, valori stipendiali,
  credenziali e URL firmati.

## Funzioni read

La codebase contiene percorsi tipizzati per elenco e conteggio dipendenti, ricerca locale,
riepilogo, contratti, ruoli, timbratura, maturazioni, bilanci, payroll individuale e collettivo,
documenti e notifiche. Una tabella composta organico/contratto/netto legge davvero contratti e
payroll per ciascun ID entro budget espliciti; un dato assente resta `N/D` e non viene inventato.

Il linguaggio naturale comune viene prima normalizzato localmente. Nomi ambigui producono una
selezione actor-bound; ID, cognome e ordinale sono validi soltanto nel contesto immutabile che ha
generato la scelta. Una selezione vecchia non può usare il contesto di una richiesta più recente.

Per una superficie sconosciuta non esiste un crawler arbitrario: un amministratore deve eseguire
discovery read-only, registrare route e schema, aggiungere parser/test e soltanto dopo renderla
disponibile. Questo impedisce al modello di improvvisare click o URL su dati personali.

## OCR e write

L'onboarding OCR accetta soltanto JPEG/PNG dopo quarantena, MIME e scansione antivirus, usa
Tesseract locale e crea una bozza in memoria con TTL e binding all'attore. Immagini, OCR e dati
estratti non raggiungono il provider. La bozza non crea automaticamente un dipendente.

Tutte le write restano soggette a ruolo, capability, feature flag, kill switch, preview,
conferma monouso, approvazioni, CAS/idempotenza e riconciliazione. La presenza del codice o di un
ruolo HR non costituisce evidenza live. Le operazioni non validate, irreversibili o prive di
postcondizione sicura restano rifiutate.

## Verifica richiesta prima di un rollout

Eseguire sulla revisione esatta candidata:

```bash
ruff format --check .
ruff check .
mypy src/bh_dic
pytest -q
bandit --configfile pyproject.toml --recursive src
pip-audit -r requirements.lock
git diff --check
```

La verifica di un ambiente richiede inoltre `doctor.sh`, migrazioni, browser/runtime, tenant e
sessione DIC, gate read-only e round-trip Discord avviato da un utente autorizzato. I risultati
vanno registrati privatamente insieme alla revisione distribuita; non devono essere copiati in
questo repository.

Vedere anche [Query planner](QUERY_PLANNER.md), [Feature matrix](FEATURE_MATRIX.md),
[Stato delle capability](LIVE_VERIFICATION_STATUS.md) e [Limitazioni](KNOWN_LIMITATIONS.md).

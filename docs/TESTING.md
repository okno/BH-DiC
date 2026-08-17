# Testing

Tutti i test devono usare fixture sintetiche e risorse locali. Per impostazione predefinita non
devono inviare messaggi Discord, chiamare provider di modello/DIC o eseguire write live.

## Setup

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --requirement requirements.lock
python -m pip install --editable .
```

## Gate obbligatori

Eseguire dalla root:

```bash
ruff format --check .
ruff check .
mypy src
pytest
bandit -r src
python -m pip_audit --strict --requirement requirements.lock --no-deps --progress-spinner off
gitleaks detect --source . --no-banner --redact --exit-code 1
```

Equivalenti repository quando disponibili:

```bash
make check
./scripts/run-tests.sh
./scripts/lint.sh
./scripts/security-check.sh
```

`make check` non include automaticamente `gitleaks`; verificare ogni comando singolarmente e
registrare exit code. Se un tool non è installato, il gate è `BLOCKED`, non `PASS`.

Per lo SHA 0.3.0 verificato: 834 test locali PASS; branch coverage 85% su 10.361 statement e 3.258
branch; Ruff, mypy, Bandit e `pip-audit` verdi; CI e CodeQL riusciti. Questi risultati non
sostituiscono le evidenze live bounded riportate in [Stato di verifica](LIVE_VERIFICATION_STATUS.md).

## Suite e marker

```bash
pytest tests/unit
pytest -m integration
pytest tests/security
pytest tests/integration/test_approval_sqlalchemy.py
```

I marker `integration` ed `e2e` non implicano accesso live. Un test network/live deve avere marker
esplicito, opt-in separato e tenant/employee sintetici dedicati. L'unico gate operatore live
documentato ha autorizzato soltanto autenticazione/tenant e due subset read bounded; non autorizza
nuovi test live automatici, lo smoke Discord o alcuna write.

## Copertura richiesta

- configurazione multi-provider fail-closed e `MODEL_STORE=true` rifiutato;
- schema provider strict e Function ID non esposto rifiutato;
- minimizzazione pre-provider per categorie semantiche: nomi anche collidenti con parole HR,
  Employee ID, query di ricerca e risultati DIC non attraversano OpenAI/Groq/llama;
- uso token exact-only per Responses/chat, stati `REPORTED`/`UNAVAILABLE`/`UNKNOWN`, idempotenza e
  migrazione `0002_model_usage` da database foundation;
- `model-check` offline per default e probe live simulato con zero Function ID/tool execution;
- scope Discord, RBAC, feature flag e kill switch;
- preview/conferma, A1/A2 distinti, TTL, CAS, idempotenza e reconciliation;
- catena HMAC e tamper detection;
- redazione PII/segreti e rate limit;
- upload, size/MIME/ext/hash, deduplica, antivirus fail-closed, path traversal e retention;
- session vault, ripersistenza solo dopo tenant attestato/read riuscita, page object, UI drift,
  retry read e no-retry write;
- cattura passiva della risposta elenco: origine/path/query/metodo/MIME/schema/body bounded,
  duplicati, tenant e paginazione fail-closed;
- `current_contract` per-record nei soli keyset esatti `BASE` o `EXTENDED=BASE+6`, con shape
  tecniche strette, discard-only e rifiuto di subset/superset/chiavi sconosciute;
- display name `SecretStr`: chiaro soltanto nel renderer `SENSITIVE`/ephemeral `HR_READ`, mascherato
  in repr/model dump e assente da aggregati, provider, log, audit e telemetria;
- totale non qualificato `all`, intervalli mensili con rollover anno, date ISO/italiane, scadenze
  bulk senza N+1 e risultati sensibili ephemeral;
- follow-up pubblico consentito soltanto per `PUBLIC_AGGREGATE` e ruolo `READ_ONLY`;
- script start/stop/status su processo sintetico, senza bot/provider reali.

## Coverage

```bash
coverage run --branch -m pytest
coverage report --show-missing
```

`pyproject.toml` imposta `fail_under=80`, ma la soglia si applica soltanto quando si usa il
comando coverage. Non confondere `pytest` verde con coverage superata.

## DOM e fixture

Una fixture derivata da DOM deve essere redatta e revisionata manualmente: rimuovere nomi,
contatti, codice fiscale, IBAN, indirizzi, ID, token, documenti e business data; sostituire gli
identificativi con valori sintetici; eseguire secret/PII scan prima del commit. Non committare
trace o screenshot live.

## Interpretazione dei risultati

- `PASS` prova soltanto il comportamento coperto nel contesto del test.
- `TESTED_WITH_MOCK` non significa `LIVE_*_VERIFIED`.
- una write non eseguita live resta `LIVE_WRITE_UNVERIFIED` e disabled.
- warning e test saltati vanno riportati con motivo.
- il resoconto aggiornato è in [Implementation report](IMPLEMENTATION_REPORT.md).

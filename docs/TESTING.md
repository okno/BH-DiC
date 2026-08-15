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

## Suite e marker

```bash
pytest tests/unit
pytest -m integration
pytest tests/security
pytest tests/integration/test_approval_sqlalchemy.py
```

I marker `integration` ed `e2e` non implicano accesso live. Un test network/live deve avere
marker esplicito, opt-in separato e tenant/employee sintetici dedicati. Nel rilascio corrente non
sono autorizzati test live, né read né write.

## Copertura richiesta

- configurazione multi-provider fail-closed e `MODEL_STORE=true` rifiutato;
- schema provider strict e Function ID non esposto rifiutato;
- `model-check` offline per default e probe live simulato con zero Function ID/tool execution;
- scope Discord, RBAC, feature flag e kill switch;
- preview/conferma, A1/A2 distinti, TTL, CAS, idempotenza e reconciliation;
- catena HMAC e tamper detection;
- redazione PII/segreti e rate limit;
- upload, size/MIME/ext/hash, deduplica, antivirus fail-closed, path traversal e retention;
- session vault, page object, UI drift, retry read e no-retry write;
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

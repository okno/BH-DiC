## Sintesi

Descrivere il risultato e il motivo della modifica.

## Rischio e perimetro

- [ ] Il cambiamento resta nel perimetro dichiarato.
- [ ] Non contiene credenziali, token, cookie, storage state, PII o documenti HR.
- [ ] Non introduce deploy, contatti live o navigazione browser arbitraria.
- [ ] `ENABLE_WRITE_ACTIONS` e `ENABLE_LIVE_WRITE_TESTS` restano disabilitati per i test.
- [ ] Eventuali write mantengono feature flag, RBAC, conferma/approvazione e riconciliazione.

## Verifica

- [ ] `ruff format --check .`
- [ ] `ruff check .`
- [ ] `mypy src`
- [ ] `pytest` con copertura almeno all'80%
- [ ] `bandit -r src`
- [ ] `pip-audit`
- [ ] Test nuovi o aggiornati usano soltanto mock, fixture sintetiche o HTML redatto.

Comandi eseguiti ed esiti:

```text
Inserire qui i gate eseguiti.
```

## Dati, policy e documentazione

- [ ] I Function ID derivano dal catalogo normativo unico e non sono duplicati.
- [ ] Log, audit e output applicano redazione e minimizzazione.
- [ ] Migrazioni, configurazione e documentazione sono aggiornate quando necessario.
- [ ] Limitazioni e stato di verifica live sono dichiarati senza sovrastimare l'evidenza.

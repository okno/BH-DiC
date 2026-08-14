# Implementation report

Snapshot di verità: **14 agosto 2026**, commit candidato locale. Aggiornare questo file dopo il
deployment senza sostituire `BLOCKED` o `UNVERIFIED` con inferenze.

## Repository

| Campo | Stato osservato |
|---|---|
| Owner | `okno` |
| Nome | `BH-DiC` |
| Remote | `https://github.com/okno/BH-DiC.git` |
| Visibilità | `PRIVATE` secondo il gate repository del progetto |
| Branch | `main` |
| Commit SHA | riportato nel report di consegna; non auto-referenziato nel commit stesso |
| Git | root commit candidato creato; pulizia verificata dal gate di consegna |

Il remote non contiene credenziali. La repository deve restare privata; nessuna licenza open
source è stata aggiunta.

## Server

| Campo | Stato osservato |
|---|---|
| Host | `10.1.2.253:22` |
| Utente SSH | `BLOCKED`: non fornito |
| Chiave/agent | `BLOCKED`: non forniti |
| Directory prevista | `/opt/bh-dic`; non creata/verificata |
| Python server | `UNVERIFIED` |
| `.venv` server | `UNVERIFIED` |
| Chromium server | `UNVERIFIED` |
| Database server | `UNVERIFIED` |
| Permessi/spazio | `UNVERIFIED` |
| Bot target | `UNVERIFIED`; nessun processo è stato avviato dalla sessione locale |

Il workspace locale usa Python 3.12.13 e una `.venv`; ciò non prova lo stato del server. Non sono
stati creati PID, servizi systemd o processi Playwright sul target.

## Implementazione

Completati nel codice e testati con risorse sintetiche:

- configurazione Pydantic fail-closed, logging redatto, DB async e Alembic;
- Discord gate/command group e runtime composition senza gateway live;
- Responses intent router strict con `store=false` e tool exposure filtrata;
- catalogo/policy/RBAC/flag per 32 Function ID;
- approval state machine, A1/A2, TTL, conferma hashata monouso, CAS/idempotenza, payload cifrati;
- audit append-only HMAC e verifica catena;
- adapter mock completo, page object/selector e adapter Playwright non validati live;
- quarantena, MIME/ext/hash/deduplica, ClamAV fail-closed e retention;
- CLI operatore e 22 script Bash con gate statico/contratto locale;
- documentazione sicurezza, privacy, operazioni, Wazuh, deployment e troubleshooting.

Stato funzionale:

- 13 read: `IMPLEMENTED`, `TESTED_WITH_MOCK`, `NEEDS_VALIDATION`;
- 17 write: `IMPLEMENTED`, `TESTED_WITH_MOCK`, `LIVE_WRITE_UNVERIFIED`,
  `DISABLED_BY_POLICY`; `EMP-EXPORT-001` e `EMP-DOC-003` sono invece
  `PARTIALLY_COMPLETED`, `TESTED_WITH_MOCK`, `LIVE_WRITE_UNVERIFIED`, `DISABLED_BY_POLICY`, con
  percorso live `NOT_AVAILABLE`; i 19 flag restano false per default;
- kill switch `ENABLE_WRITE_ACTIONS=false`, `ENABLE_LIVE_WRITE_TESTS=false`;
- nessun bot avviato nel workspace locale; stato target `UNVERIFIED`; nessuna modifica di
  produzione.

Dettaglio: [Feature matrix](FEATURE_MATRIX.md).

## Test e gate osservati

| Comando | Risultato |
|---|---|
| `ruff format --check .` | PASS: 173 file già formattati |
| `ruff check .` | PASS: zero issue |
| `mypy src` | PASS: 103 source file, zero issue |
| `pytest` (pytest 9.0.3) | PASS: 310 test, 1 warning esterno `discord.py/audioop` |
| `coverage run --branch -m pytest` | PASS: 310 test |
| `coverage report --show-missing` | PASS: 89%, soglia 80% |
| coverage application/OpenAI/audit/repository | PASS: 100% nella suite completa |
| `bandit -q -r src` | PASS: nessun issue riportato |
| `pip-audit --strict -r requirements.lock --no-deps` | PASS: nessuna vulnerabilità nota |
| `gitleaks detect --source . --no-banner --redact` | PASS: gitleaks 8.30.1, un commit scansionato, nessun finding |
| parsing YAML/XML docs/security | PASS |
| scansione pattern secret sui docs/config | PASS mirato; non sostituisce gitleaks repository-wide |
| 22 script `bash -n` + contratti/lifecycle ops | PASS: 31 casi; start fail-closed e status/stop sintetici inclusi |

I gate Python, coverage, lint, typing, Bandit, dependency audit e secret scan della history Git
sono verdi. Il deployment e le verifiche live non sono invece deducibili da questi risultati e
restano bloccati/non disponibili come indicato sotto.

## Operatività

```text
Configurazione  ./scripts/init-config.sh && ./scripts/doctor.sh
Avvio           ./scripts/start.sh
Foreground debug ./scripts/run-foreground.sh
Status          ./scripts/status.sh
Log             ./scripts/logs.sh all --follow
File            ./scripts/files.sh list
Audit           ./scripts/audit-verify.sh
Stop            ./scripts/stop.sh
Restart         ./scripts/restart.sh
Backup          ./scripts/backup.sh
Restore         ./scripts/restore.sh var/backups/<BACKUP>.tar.gz --confirm RESTORE
Update          ./scripts/update.sh
```

Gli script non sono stati eseguiti sul server. Backup/restore corrente supporta SQLite locale, non
PostgreSQL.

## Sicurezza

- nessun segreto o dato personale intenzionalmente inserito nei file documentali;
- OpenAI `store=false`; provider senza browser, file, segreti o decisione policy;
- scope guild/canale/tenant, RBAC e rate limit fail-closed;
- write globalmente e specificamente disabilitate;
- A2 richiede identità distinta e kill switch ricontrollato all'esecuzione;
- parametri pending cifrati; audit HMAC; file in quarantena con antivirus fail-closed;
- nessuna write/read live e nessun bot avviato.

## Problemi residui

- credenziali SSH mancanti: deployment bloccato;
- configurazione reale Discord/OpenAI/DIC non fornita e connessioni non verificate;
- selettori e route Playwright non validati live; UI drift possibile;
- form write, delete/export/download e postcondizioni non verificati live;
- MFA/CAPTCHA e funzionalità TeamSystem non documentate possono bloccare flussi;
- rotazione log e Wazuh non installati/testati sul target;
- restore drill e backup server non eseguiti;
- GitHub Advanced Security, secret/push scanning e branch protection non sono disponibili sul piano
  privato corrente: CodeQL esegue l'analisi in workflow con upload SARIF disabilitato; Bandit,
  dependency audit, gitleaks, required review tramite processo operativo e i gate CI restano i
  controlli applicabili.

Non inserire in versioni successive password, token, API key, cookie, TOTP, PII o contenuti di
documenti.

# Implementation report

Snapshot documentale: **16 agosto 2026**. Lo SHA autorevole è riportato nel report di consegna perché
un commit non può auto-referenziare il proprio hash. Aggiornare questo file dopo il deployment senza
sostituire `BLOCKED` o `UNVERIFIED` con inferenze.

## Repository

| Campo | Stato osservato |
|---|---|
| Owner | `okno` |
| Nome | `BH-DiC` |
| Remote | `https://github.com/okno/BH-DiC.git` |
| Visibilità | `PRIVATE`, verificata tramite GitHub API |
| Branch | `main` |
| Commit SHA | riportato nel report di consegna; non auto-referenziato nel commit stesso |
| Git | branch `main` pushato; worktree e tracking remoto verificati dal gate di consegna |

Il remote non contiene credenziali. La repository deve restare privata; nessuna licenza open
source è stata aggiunta.

## Server

| Campo | Stato osservato |
|---|---|
| Host | target Debian 12 autorizzato; indirizzo omesso dalla documentazione |
| Utente runtime | account di servizio dedicato verificato |
| Directory | `/opt/bh-dic`; ownership e modalità runtime verificate |
| Python server | 3.12 installato separatamente dal Python di sistema |
| `.venv` server | presente; `pip check` riuscito |
| Chromium server | installato nel profilo runtime; launch headless riuscito |
| Database server | SQLite presente e migrazione alla head verificata |
| Antivirus | ClamAV attivo; socket `0660` e scansione applicativa riuscita |
| Doctor | offline e online riusciti |
| Bot target | `STOPPED`; nessun avvio di produzione eseguito |

Il provider Groq e il modello configurato hanno superato il probe live chiuso. Il login DIC
headless si è fermato fail-closed sulla password TeamSystem scaduta e non ha creato un vault;
nessuna Function ID DIC read/write è stata eseguita.

## Implementazione

Completati nel codice e testati con risorse sintetiche:

- configurazione Pydantic fail-closed, logging redatto, DB async e Alembic;
- Discord gate/command group e runtime composition senza gateway live;
- intent router strict multi-provider: Responses per OpenAI/Groq e chat-compatible per llama,
  con storage applicativo disabilitato e tool exposure filtrata;
- persona configurabile e confinata alla presentazione, senza effetto su policy/RBAC;
- `model-check` offline per default e probe live provider sintetico/chiuso, esplicitamente opt-in;
- catalogo/policy/RBAC/flag per 32 Function ID;
- approval state machine, A1/A2, TTL, conferma hashata monouso, CAS/idempotenza, payload cifrati;
- audit append-only HMAC e verifica catena;
- adapter mock completo, Page Object e adapter Playwright; login federato con allowlist esatta,
  probe di sessione restaurata e attestazione tenant passiva first-party;
- quarantena, MIME/ext/hash/deduplica, ClamAV fail-closed e retention;
- CLI operatore e 22 script Bash con gate statico/contratto locale;
- documentazione sicurezza, privacy, operazioni, Wazuh, deployment e troubleshooting.

Stato funzionale:

- 13 read: `IMPLEMENTED`, `TESTED_WITH_MOCK`, `NEEDS_VALIDATION`;
- 13 write: `IMPLEMENTED`, `TESTED_WITH_MOCK`, `LIVE_WRITE_UNVERIFIED`,
  `DISABLED_BY_POLICY`; 6 write sono `PARTIALLY_COMPLETED`, `TESTED_WITH_MOCK`,
  `LIVE_WRITE_UNVERIFIED`, `DISABLED_BY_POLICY`: `EMP-CREATE-001` supporta live soltanto il subset
  con postcondizione verificabile, mentre `EMP-INVITE-001`, `EMP-DOC-005`, `EMP-EXPORT-001`,
  `EMP-DOC-003` ed `EMP-CONTRACT-003` hanno percorso live `NOT_AVAILABLE`; i 19 Function ID write
  sono disabilitati e i 18 gate distinti usati dal catalogo per le write restano `false` per
  default;
- kill switch `ENABLE_WRITE_ACTIONS=false`, `ENABLE_LIVE_WRITE_TESTS=false`;
- bot target fermo; nessuna modifica DIC di produzione.

Dettaglio: [Feature matrix](FEATURE_MATRIX.md).

## Test e gate — release 0.2.2

I risultati seguenti sono stati osservati il 16 agosto 2026 sul worktree candidato 0.2.2, senza
avviare bot/browser e senza chiamare Discord, DIC o provider. Provano soltanto i confini coperti da
fixture sintetiche; non trasformano alcuna integrazione in `LIVE_VERIFIED`.

| Comando | Risultato |
|---|---|
| `ruff format --check .` | PASS: 185 file già formattati |
| `ruff check .` | PASS: zero issue |
| `mypy src` | PASS: 108 source file, zero issue |
| `pytest` (pytest 9.0.3) | PASS: 518 test, 1 warning esterno `discord.py/audioop` |
| `coverage run --branch -m pytest` | PASS: 518 test |
| `coverage report --show-missing --fail-under=80` | PASS: 86% branch-aware (8.111 statement, 2.442 branch), soglia 80% |
| `bandit -q -r src` | PASS: nessun issue riportato |
| `python -m pip_audit --strict --requirement requirements.lock --no-deps --progress-spinner off` | PASS: nessuna vulnerabilità nota nel lock |
| `gitleaks git . --log-opts v0.2.1..HEAD --redact --no-banner` | PASS: gitleaks 8.30.1, 1 commit/circa 4,7 KB, nessun finding |
| parsing YAML/XML docs/security | PASS |
| scansione pattern secret sui docs/config | PASS mirato; non sostituisce gitleaks repository-wide |
| 22 script `bash -n` + contratti/lifecycle ops | PASS: 32 casi; start fail-closed e status/stop sintetici inclusi |

La scansione della cronologia del nuovo commit è riuscita; i workflow remoti restano da verificare
dopo il push. Le evidenze Debian/Groq riportate sopra derivano da controlli separati sul target;
non promuovono DIC o Discord a verificati.

## Operatività

```text
Configurazione  ./scripts/init-config.sh && ./scripts/doctor.sh
Provider offline .venv/bin/python -m bh_dic model-check
Provider live   .venv/bin/python -m bh_dic model-check --live  # solo se autorizzato
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

Installazione, doctor, audit e smoke mock sono stati eseguiti sul server. Il restore drill non è
stato eseguito. Backup/restore corrente supporta SQLite locale, non PostgreSQL.

## Sicurezza

- nessun segreto o dato personale intenzionalmente inserito nei file documentali;
- `MODEL_STORE=false`; provider senza browser, file, segreti o decisione policy;
- scope guild/canale/tenant, RBAC e rate limit fail-closed;
- write globalmente e specificamente disabilitate;
- A2 richiede identità distinta e kill switch ricontrollato all'esecuzione;
- parametri pending cifrati; audit HMAC; file in quarantena con antivirus fail-closed;
- il pending file conserva solo l'`upload_id`; path e SHA-256 non sono esposti in eventi, log,
  Discord o al provider, e lo SHA-256 è visibile soltanto all'operatore locale nei metadati file;
- nessuna Function ID DIC write/read live eseguita; processo bot target fermo.

## Problemi residui

- password TeamSystem scaduta: autenticazione DIC e creazione vault bloccate fino all'azione umana;
- Discord gateway e registrazione comandi non ancora verificati end-to-end;
- Groq/modello verificati live; OpenAI e llama restano non verificati;
- selettori e route delle funzioni HR Playwright non validati live; UI drift possibile;
- form write, delete/export/download e postcondizioni non verificati live;
- MFA/CAPTCHA e funzionalità TeamSystem non documentate possono bloccare flussi;
- rotazione log e Wazuh non installati/testati sul target;
- restore drill e backup server non eseguiti;
- i file tracciati sono privi di PII rilevata, ma i commit già pubblicati conservano l'identità
  e-mail della configurazione Git locale nei metadati Author/Committer; i nuovi commit usano
  l'identità GitHub `noreply` e la cronologia non è stata riscritta perché richiederebbe force-push;
- GitHub Advanced Security, secret/push scanning e branch protection non sono disponibili sul piano
  privato corrente: CodeQL esegue l'analisi in workflow con upload SARIF disabilitato; Bandit,
  dependency audit, gitleaks, required review tramite processo operativo e i gate CI restano i
  controlli applicabili.

Non inserire in versioni successive password, token, API key, cookie, TOTP, PII o contenuti di
documenti.

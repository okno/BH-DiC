# Manutenzione dei selettori DIC

## Fonte unica e stato

La fonte eseguibile unica è
`src/bh_dic/dic/selectors.py` (`DEFAULT_SELECTORS`). Il package richiesto dal
layout di progetto,
`src/bh_dic/dic/playwright/selectors/registry.py`, è un re-export e non contiene
una seconda copia dei selettori.

I Page Object sono implementati in `src/bh_dic/dic/pages/routes.py` e re-esportati
da `src/bh_dic/dic/playwright/pages/`. Non aggiungere selettori direttamente nei
Page Object: aggiungere o modificare una chiave nel registro centrale e usare la
chiave semantica dal Page Object.

La ricognizione live read-only ha confermato la struttura federata del login, ma
non ha validato i selettori delle funzioni HR. Le etichette e i nomi accessibili
derivano dalla baseline di Fase 1; i `data-testid`, gli attributi `data-*` e i
fallback CSS rappresentano il contratto implementativo e le fixture sintetiche,
non evidenza di stabilità sul DOM corrente.

## Route e Page Object

| Page Object | Route esatta ammessa | Namespace selettori |
| --- | --- | --- |
| `LoginPage` | `/it/login`; root e-mail TeamSystem `/`, legacy `/Account/LoginEmail`, `/Account/LoginPassword`; `/connect/authorize` e `/connect/authorize/callback` soltanto pending bounded | `auth.*` |
| `EmployeesListPage` | `/it/app/employees/list` | `employees.*`, `row.*` |
| `EmployeeSummaryPage` | `/it/app/employees/info/{employee_id}/summary` | `summary.*` |
| `EmployeeRolesPage` | `/it/app/employees/info/{employee_id}/roles` | `roles.*` |
| `TimestampEmployeesPage` | `/it/app/settings/timestamps/employees` | `timestamps.*` |
| `EmployeeContractsPage` | `/it/app/employees/info/{employee_id}/contracts` | `contracts.*`, `contract_row.*` |
| `EmployeeMaturationsPage` | `/it/app/employees/info/{employee_id}/maturations` | `maturations.*`, `maturation_row.*` |
| `EmployeeBalancePage` | `/it/app/employees/info/{employee_id}/counters` | `balance.*`, `balance_row.*` |
| `EmployeePayrollsPage` | `/it/app/employees/info/{employee_id}/payrolls` | `payrolls.*`, `payroll_row.*` |
| `EmployeeDocumentsPage` | `/it/app/employees/info/{employee_id}/documents/list` | `documents.*`, `document_row.*` |

`BaseDicPage` accetta soltanto un'origine HTTPS senza credenziali, path, query o
fragment; valida il formato di `employee_id`, costruisce una route prevista e
controlla origine e path con `open()`. L'autenticatore usa `navigate()` soltanto
per le route fisse di login/session probe e valida ogni redirect contro l'allowlist
esatta DIC/TeamSystem. La root TeamSystem corrente riusa i controlli e-mail esatti del percorso
legacy; le route OIDC pending non sono pagine su cui cercare selettori. Un SSO senza controlli
deve raggiungere marker DIC e tenant attestato senza alcun fill/click/submit credenziale. Le altre
navigazioni cross-origin o verso route diverse
generano errore. Non esiste un metodo per aprire URL o selettori arbitrari.

## Inventario completo delle chiavi

Ogni voce seguente identifica una chiave del registro e la schermata di
provenienza. I candidati ordinati (role, label, test-id, testo o CSS) sono definiti
accanto alla chiave nel file sorgente centrale.

| Schermata/provenienza | Chiavi del registro |
| --- | --- |
| Comuni a conferme e notifiche | `common.confirm`, `common.success` |
| Login/sessione federata | `auth.username`, `auth.password`, `auth.submit`, `auth.mfa`, `auth.captcha`, `auth.authenticated`, `auth.dic_email`, `auth.dic_submit`, `auth.teamsystem_email`, `auth.teamsystem_email_submit`, `auth.teamsystem_password`, `auth.teamsystem_password_submit` |
| Lista dipendenti | `employees.rows`, `employees.total`, `employees.search`, `employees.filter.active`, `employees.filter.inactive`, `employees.filter.all`, `employees.sort.name`, `employees.sort.payroll_number`, `employees.sort.status`, `employees.sort.contract`, `employees.next`, `employees.new`, `employees.create_manual`, `employees.create_payroll`, `employees.create_save` |
| Riga dipendente | `row.employee_id`, `row.name`, `row.email`, `row.tax_code`, `row.job_title`, `row.group`, `row.payroll_number`, `row.contract`, `row.contract_period`, `row.schedule`, `row.account_state`, `row.employee_state` |
| Riepilogo dipendente | `summary.first_name`, `summary.last_name`, `summary.payroll_number`, `summary.tax_code`, `summary.birth_date`, `summary.iban`, `summary.job_title`, `summary.phone`, `summary.email`, `summary.address`, `summary.workplace`, `summary.notes`, `summary.state`, `summary.save`, `summary.connect`, `summary.disconnect`, `summary.invite_again`, `summary.cancel_invite`, `summary.deactivate`, `summary.activate`, `summary.delete` |
| Permessi e ruoli | `roles.groups`, `roles.items`, `roles.time.timestamping`, `roles.time.attendance`, `roles.time.shifts`, `roles.time.expenses`, `roles.save` |
| Impostazioni timbrature | `timestamps.rows`, `timestamps.row.employee_id`, `timestamps.row.enabled` |
| Elenco/form contratti | `contracts.rows`, `contracts.new`, `contracts.edit`, `contracts.delete`, `contracts.schedule`, `contracts.flexibility`, `contracts.permanent`, `contracts.start_date`, `contracts.end_date`, `contracts.ccnl_level`, `contracts.work_regime`, `contracts.description`, `contracts.type`, `contracts.save` |
| Riga contratto | `contract_row.id`, `contract_row.schedule`, `contract_row.flexibility`, `contract_row.permanent`, `contract_row.start_date`, `contract_row.end_date`, `contract_row.ccnl_level`, `contract_row.work_regime`, `contract_row.description`, `contract_row.type`, `contract_row.status`, `contract_row.period` |
| Elenco/form maturazioni | `maturations.rows`, `maturations.new`, `maturations.category`, `maturations.valid_from`, `maturations.valid_to`, `maturations.save` |
| Riga maturazione | `maturation_row.id`, `maturation_row.category`, `maturation_row.valid_from`, `maturation_row.valid_to`, `maturation_row.status` |
| Bilancio/form correzione | `balance.year`, `balance.rows`, `balance.correct`, `balance.category`, `balance.amount`, `balance.save` |
| Riga bilancio | `balance_row.category`, `balance_row.previous_year`, `balance_row.previous_month`, `balance_row.accrued`, `balance_row.used`, `balance_row.corrections`, `balance_row.current_residual` |
| Elenco buste paga | `payrolls.rows`, `payrolls.year`, `payroll_row.id`, `payroll_row.year`, `payroll_row.month`, `payroll_row.status`, `payroll_row.published_at` |
| Elenco/form documenti | `documents.rows`, `documents.search`, `documents.uploaded`, `documents.pending`, `documents.upload`, `documents.file`, `documents.title`, `documents.category`, `documents.expiry`, `documents.save`, `documents.edit`, `documents.delete` |
| Riga documento | `document_row.id`, `document_row.title`, `document_row.category`, `document_row.expiry`, `document_row.uploaded_at`, `document_row.uploaded_by`, `document_row.state` |

## Ordine e qualità dei candidati

Il registro prova i candidati nell'ordine dichiarato. Per nuove evidenze live,
preferire:

1. `data-testid` confermato stabile;
2. role e accessible name;
3. label;
4. attributo `name` stabile;
5. testo esatto contestualizzato;
6. CSS strutturale come ultimo fallback.

Non usare coordinate, indici globali, classi generate, XPath assoluti o testo
parziale ambiguo. Un `data-testid` ipotizzato ma non presente live non va promosso
solo perché appare in una fixture. I fallback CSS `td:nth-child(...)` sono
deliberatamente l'ultima difesa e devono essere sostituiti quando si osserva un
attributo semantico stabile.

## Diagnosi di UI drift

Sintomi tipici sono `DicUiChangedError`, una route inattesa, nessun candidato per
una chiave richiesta, target non unico, ID stabile assente o postcondizione non
riconciliabile. La procedura sicura è:

1. fermare BH-DiC e confermare `ENABLE_WRITE_ACTIONS=false` e tutte le flag write
   specifiche a `false`;
2. usare un account e un tenant autorizzati, senza cambiare azienda;
3. riprodurre solo la read che fallisce;
4. verificare origine, route e controllo distintivo della pagina;
5. ispezionare role, accessible name, label e attributi stabili senza premere
   Salva, Conferma, Elimina, Esporta, Scarica o Carica;
6. associare la divergenza alla chiave del registro, non introdurre un click
   generico;
7. aggiornare fixture e test prima di riabilitare la read;
8. lasciare le write disabilitate finché read, postcondizioni e tenant guard non
   sono nuovamente verificati.

Il tenant non è un selettore manutenibile: il DOM corrente non espone un ID stabile
e non sono ammessi fallback su testo o nome azienda. Il guard osserva soltanto il
`GET` first-party same-origin a `/backend_apiV2/company/info` emesso dalla
navigazione fissa `/it/app/employees/list`, valida il contratto stretto e confronta
`/data/company/id` con il tenant configurato. Se questo contratto cambia, fermare
il bot e aggiornare parser, fixture sintetiche, test e documentazione; non
aggiungere un nuovo selettore DOM per aggirare il fallimento.

Il circuit breaker passa a stato degradato/aperto dopo errori ripetuti di UI o
trasporto. Le read idempotenti hanno retry limitati; le write non sono mai
ritentate automaticamente. Un errore dopo il dispatch richiede riconciliazione.

## Aggiornamento di una fixture redatta

Non salvare HTML autenticato grezzo. Se è necessaria una fixture DOM:

- copiare soltanto il frammento minimo che riproduce il controllo;
- sostituire nomi, e-mail, codici fiscali, IBAN, telefoni, indirizzi, matricole,
  employee/document/contract ID e nomi file con valori sintetici;
- rimuovere cookie, token, header, script, URL firmati, contenuto documentale e
  attributi non necessari;
- mantenere solo struttura accessibile e attributi coinvolti dal selettore;
- riesaminare manualmente la fixture con una ricerca di PII/secret prima del
  commit;
- non acquisire screenshot o trace live; riprodurre soltanto in mock con dati sintetici
  configurata; `SAVE_FAILURE_SCREENSHOTS=false` e `PLAYWRIGHT_TRACE_MODE=off`
  sono i default.

## Test e smoke test

Controlli locali, esclusivamente sintetici:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_dic_pages.py tests/unit/test_dic_playwright_adapter.py -q
.\.venv\Scripts\python.exe -m ruff check src/bh_dic/dic tests/unit/test_dic_pages.py tests/unit/test_dic_playwright_adapter.py
.\.venv\Scripts\python.exe -m mypy src/bh_dic/dic
```

La ricognizione della struttura login/attestazione non equivale a uno smoke delle
Function ID. Il primo smoke live read-only deve usare `ENABLE_WRITE_ACTIONS=false`,
un tenant atteso esplicito, nessun download/upload, una piccola query di lista e
al massimo l'apertura delle route censite. Registrare solo esito, Function ID,
route astratta ed errore tipizzato; mai HTML, response body, PII o credenziali.

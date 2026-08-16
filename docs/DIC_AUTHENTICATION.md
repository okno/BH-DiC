# Autenticazione Dipendenti in Cloud

## Stato e confine di sicurezza

L'origine applicativa configurabile è esclusivamente
`https://secure.dipendentincloud.it`. La configurazione rifiuta schemi diversi da
HTTPS, host diversi, credenziali nell'URL, porte diverse da `443`, path, query e
fragment. Playwright non disabilita TLS e non usa `--ignore-certificate-errors`.

Il solo flusso federato ammesso aggiunge questi target HTTPS esatti e nessun altro:

- `https://secure.dipendentincloud.it/it/login`;
- `https://identity.teamsystem.com/Account/LoginEmail`;
- `https://identity.teamsystem.com/Account/LoginPassword`.

Schema, host, porta e path sono confrontati esattamente e i fragment sono
rifiutati; gli eventuali parametri di stato dell'IdP non possono cambiare questo
confronto. Un'altra origine TeamSystem, un sottodominio somigliante o un redirect
inatteso falliscono chiuso. Il percorso passwordless non viene usato. La route
`/Account/LoginPasswordExpired` viene riconosciuta soltanto per produrre un errore
fail-closed che richiede rinnovo umano.

Una ricognizione live autorizzata in sola lettura ha osservato questa struttura e
la schermata `PasswordExpired`. Una singola submission della password è arrivata
a tale schermata, quindi il login headless finale non è stato completato e non è
stato creato un vault. Nessuna funzione HR read/write è stata eseguita. I
controlli descritti sotto sono coperti da test sintetici e devono ancora essere
verificati end-to-end con una credenziale TeamSystem valida.

Discord e il provider di modello non ricevono credenziali, cookie, `storage_state`, primitive
Playwright o una funzione di navigazione arbitraria. Il confine applicativo è il
Protocol `DipendentiInCloudAdapter`.

## Configurazione

Le variabili DIC supportate sono:

| Variabile | Scopo | Regola operativa |
| --- | --- | --- |
| `DIC_BASE_URL` | Origine DIC | Deve essere esattamente l'origine HTTPS ammessa. |
| `DIC_USERNAME` | Identità applicativa | Obbligatoria fuori da mock; non registrare nei log. |
| `DIC_PASSWORD` | Password | Secret obbligatorio fuori da mock; mai persisterlo nel vault. |
| `DIC_TOTP_SECRET` | Materiale TOTP riservato | Non viene consumato dal flusso live corrente; ogni MFA richiede intervento umano. |
| `DIC_SESSION_ENCRYPTION_KEY` | Cifratura del vault | Obbligatoria fuori da mock e lunga almeno 32 byte UTF-8. |
| `DIC_EXPECTED_TENANT_ID` | Tenant atteso | Intero positivo canonico (1-19 cifre), obbligatorio fuori da mock; confronto esatto dopo l'autenticazione. |
| `DIC_HEADLESS` | Modalità browser | `true` per impostazione predefinita. |
| `DIC_LOCALE` | Locale browser | Valore predefinito `it-IT`. |
| `DIC_TIMEZONE` | Fuso browser | Nome IANA valido; predefinito `Europe/Rome`. |
| `DIC_LOGIN_TIMEOUT_SECONDS` | Timeout login | Da 5 a 300 secondi; predefinito 60. |
| `DIC_NAVIGATION_TIMEOUT_SECONDS` | Timeout navigazione | Da 5 a 180 secondi; predefinito 30. |
| `DIC_MAX_CONCURRENT_BROWSER_OPERATIONS` | Concorrenza | Predefinito 1; intervallo validato 1-4. |
| `DIC_SESSION_STATE_PATH` | Vault cifrato | Predefinito `./var/session/dic_session.enc`; il vault richiede un path assoluto dopo la risoluzione. |
| `DIC_TEST_EMPLOYEE_ID` | Target live dedicato | Usabile solo per test write esplicitamente autorizzati. |
| `DIC_TEST_TENANT_CONFIRMED` | Conferma tenant test | Deve restare `false` salvo autorizzazione live esplicita. |

Preparazione minima su Linux:

```bash
cp .env.example .env
chmod 600 .env
```

Inserire i valori attraverso il secret manager o l'editor locale previsto. Non
mostrare il contenuto di `.env`, non passare segreti come argomenti di processo e
non commettere `.env`, chiavi, cookie o file di sessione.

## Flusso implementato

1. `status()` azzera il documento corrente su `about:blank`, attende e drena gli eventi
   precedenti; soltanto dopo installa l'osservatore tenant e naviga verso la route read-only
   fissa `/it/app/employees/list`. Questo rende verificabile anche una sessione ripristinata e
   impedisce a una risposta tardiva del documento precedente di attestare il nuovo probe.
2. Un redirect esatto same-origin a `/it/login` produce stato `UNKNOWN`, cioè
   richiesta di autenticazione. La route dipendenti, il marker autenticato e una
   attestazione tenant valida producono `AUTHENTICATED`; qualunque altra
   combinazione fallisce chiuso.
3. Se serve un nuovo login, `authenticate()` segue soltanto la sequenza
   allowlisted DIC → `LoginEmail` → `LoginPassword`, compilando i segreti
   direttamente nei controlli previsti. Non espone primitive di navigazione
   arbitrarie.
4. `PasswordExpired`, CAPTCHA o un passaggio interattivo non supportato
   interrompono il flusso con un errore tipizzato che richiede intervento umano.
5. Qualunque campo MFA visibile interrompe il flusso con `DicMfaRequiredError`; questa versione
   non compila né invia codici MFA e non usa controlli passwordless non osservati.
6. Dopo il submit, l'adapter ripete il probe sulla route fissa e richiede marker
   autenticato e corrispondenza esatta del tenant prima di persistere la sessione.

## Attestazione tenant passiva

Il DOM corrente non espone un identificatore tenant stabile. Il tenant guard non
usa quindi nome azienda, testo visibile o un selettore `auth.tenant` come fallback
autorizzativo. Durante la normale navigazione della lista dipendenti osserva
passivamente la risposta che la pagina stessa genera e accetta esclusivamente:

- metodo `GET`;
- HTTPS, host esatto `secure.dipendentincloud.it` e nessuna porta esplicita;
- path esatto `/backend_apiV2/company/info`, senza query o fragment;
- status `200` e media type `application/json`;
- body non superiore a 64 KiB, JSON UTF-8 valido e senza chiavi duplicate;
- singolo oggetto corrente con intero positivo al JSON Pointer `/data/company/id`.

`bool`, float, `null`, stringhe e contenitori sono rifiutati. Il valore viene
convertito nella rappresentazione canonica e confrontato esattamente con
`DIC_EXPECTED_TENANT_ID`; la documentazione non deve riportare il valore reale.
Timeout, risposta assente, schema/MIME/route difformi e mismatch tenant producono
un errore di autorizzazione generico senza body, PII o identificatori.

Questo endpoint first-party e lo schema minimo sono dettagli interni ricavati dal
bundle pubblico corrente, non da una risposta autenticata acquisita. Non sono
un'API pubblica supportata né un nuovo adapter dati: BH-DiC non invoca direttamente
l'endpoint e non usa la risposta per funzioni HR.

Gli errori password scaduta/CAPTCHA/MFA corrispondono operativamente a
`AUTHENTICATION_INTERACTIVE_REQUIRED`: fermare il job e completare il passaggio
solo con una procedura umana autorizzata. Non tentare bypass, automazioni CAPTCHA
o indebolimenti dei controlli browser.

`DIC_TOTP_SECRET` resta presente per compatibilità di configurazione, ma questa versione non lo
consuma e non automatizza MFA. Un challenge MFA impone lo stop e una procedura umana autorizzata,
senza registrare o persistere codici. Finché una composizione specifica non viene osservata,
implementata e verificata, MFA live è una limitazione nota.

## Vault di sessione

`FernetSessionVault` salva soltanto un modello `StoredBrowserSession`, contenente
lo `storage_state`, gli istanti di autenticazione/scadenza e un eventuale hint
redatto. Il file è cifrato con Fernet prima della scrittura.

Le proprietà implementate sono:

- directory creata con modalità richiesta `0700`;
- file temporaneo cifrato con modalità `0600`;
- `fsync`, sostituzione atomica e modalità finale `0600`;
- nessun file temporaneo in chiaro;
- rifiuto di vault illeggibili, alterati o cifrati con una chiave diversa;
- rifiuto delle sessioni scadute;
- invalidazione mediante eliminazione del solo file di sessione.

La semantica dei bit POSIX dipende dal filesystem e dall'host: sull'host Linux di
destinazione va verificata con `stat` prima di considerarla operativa.

`DicSessionManager` applica una durata predefinita di otto ore e può caricare,
salvare o invalidare lo stato. Il bootstrap non-mock collega settings, vault,
browser context e persistenza: carica lo storage state cifrato prima di creare il
context, esegue il probe fisso con attestazione passiva e lo salva nuovamente solo
dopo che l'adapter ha verificato autenticazione e tenant. Questa composizione è
testata localmente; non è ancora stata verificata con un vault live perché il
login autorizzato si è fermato sulla password scaduta.

## Comando di verifica autenticazione

Il controllo predefinito è strettamente locale e non avvia Chromium né effettua
richieste di rete:

```bash
.venv/bin/python -m bh_dic dic-auth-check
```

Valida la configurazione completa, la presenza del tenant atteso, il path non
simbolico del vault, i permessi privati su POSIX, la decifrabilità Fernet, la
scadenza e la struttura minima dello storage state. L'output contiene soltanto
stati astratti. Non mostra path, tenant ID, account hint, cookie, origin, header,
username, password, TOTP o chiavi. Un controllo locale riuscito riporta comunque
`authentication=UNVERIFIED_OFFLINE` e `tenant_binding=UNVERIFIED_OFFLINE`: cookie
presenti e non scaduti non provano che la sessione sia ancora accettata dal sito.

Solo un operatore autorizzato può richiedere esplicitamente il controllo live:

```bash
.venv/bin/python -m bh_dic dic-auth-check --live
```

`--live` costruisce il runtime browser, prova prima il ripristino mediante la
route read-only fissa, esegue il login allowlisted solo se necessario, verifica
marker autenticato e attestazione tenant, persiste il vault e chiude sempre il
runtime. Può quindi contattare DIC e TeamSystem e attivare MFA/CAPTCHA. Il tentativo
osservato si è fermato fail-closed su `PasswordExpired`: non ha prodotto vault né
eseguito Function ID DIC. In assenza del flag il codice live non viene invocato.

## Invalidazione e rotazione

Per invalidare una sessione, fermare prima il processo BH-DiC e usare il comando
operativo dedicato:

```bash
.venv/bin/python -m bh_dic invalidate-session
```

Il comando risolve il path configurato tramite `FernetSessionVault` ed elimina
soltanto quel file. Non usare glob, cancellazioni ricorsive o comandi manuali che
possano colpire altre sessioni.

La rotazione di `DIC_SESSION_ENCRYPTION_KEY` rende il vault precedente
indecifrabile. La sequenza sicura è: fermare il servizio, invalidare il vault,
ruotare la chiave nel secret manager, riavviare e autenticare nuovamente. Non
conservare copie in chiaro della chiave precedente.

Una rotazione di password, account o tenant richiede sempre l'invalidazione del
vault. Un cambio di tenant deve aggiornare anche `DIC_EXPECTED_TENANT_ID`; senza
questa corrispondenza l'adapter rifiuta la sessione.

## Diagnostica senza segreti

È sicuro registrare solo stato astratto ed errori tipizzati: browser disponibile,
sessione autenticata/non autenticata/scaduta, tenant configurato sì/no e route
attesa. Non registrare valori di username, password, TOTP, chiave Fernet, cookie,
header, HTML autenticato o `storage_state`.

In caso di errore:

1. verificare che `DIC_BASE_URL` sia l'origine esatta;
2. verificare solo la presenza, non il valore, delle variabili obbligatorie;
3. verificare proprietà e permessi del path del vault;
4. invalidare una sessione scaduta o illeggibile;
5. classificare password scaduta, CAPTCHA/MFA o redirect inatteso come azione
   umana richiesta, senza usare passwordless o allargare l'allowlist;
6. classificare attestazione tenant assente, invalida o diversa come errore di autorizzazione, senza
   tentare altre aziende.

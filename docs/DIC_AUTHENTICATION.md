# Autenticazione Dipendenti in Cloud

## Stato e confine di sicurezza

L'adapter usa esclusivamente l'origine HTTPS
`https://secure.dipendentincloud.it`. La configurazione rifiuta schemi diversi da
HTTPS, host diversi, credenziali nell'URL, porte diverse da `443`, path, query e
fragment. Playwright non disabilita TLS e non usa `--ignore-certificate-errors`.

Questa implementazione non ha eseguito login live. Le procedure e i controlli
descritti qui sono coperti da test unitari con oggetti sintetici; autenticazione,
MFA, tenant e selettori devono ancora essere verificati in una sessione
autorizzata.

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
| `DIC_TOTP_SECRET` | Materiale TOTP opzionale | La generazione automatica del codice non è collegata al runtime; vedere MFA. |
| `DIC_SESSION_ENCRYPTION_KEY` | Cifratura del vault | Obbligatoria fuori da mock e lunga almeno 32 byte UTF-8. |
| `DIC_EXPECTED_TENANT_ID` | Tenant atteso | Obbligatorio fuori da mock; confronto esatto dopo l'autenticazione. |
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

1. `session_status()` cerca un indicatore di sessione autenticata.
2. Se l'indicatore esiste, l'adapter verifica anche il tenant osservato contro
   `DIC_EXPECTED_TENANT_ID`. Indicatore tenant assente o valore diverso producono
   un errore fail-closed.
3. Se serve un nuovo login, `ensure_authenticated()` richiede credenziali
   tipizzate, apre soltanto la route di login ammessa e compila username e
   password direttamente nei controlli del form.
4. Un CAPTCHA visibile interrompe il flusso con `DicCaptchaRequiredError`.
5. Un campo MFA visibile richiede un codice monouso già fornito nelle credenziali;
   in sua assenza il flusso termina con `DicMfaRequiredError`.
6. Dopo il submit, l'adapter richiede sia l'indicatore di autenticazione sia la
   corrispondenza esatta del tenant.

Gli errori CAPTCHA/MFA corrispondono operativamente a
`AUTHENTICATION_INTERACTIVE_REQUIRED`: fermare il job e completare il passaggio
solo con una procedura umana autorizzata. Non tentare bypass, automazioni CAPTCHA
o indebolimenti dei controlli browser.

`DIC_TOTP_SECRET` è presente nella configurazione, ma questa versione non genera
automaticamente codici TOTP e non costruisce autonomamente `DicCredentials` dal
settings object. Un integratore autorizzato deve fornire il codice monouso senza
registrarlo o persisterlo. Finché questa composizione non è implementata e
verificata, MFA live è una limitazione nota.

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
context e lo salva nuovamente solo dopo che l'adapter ha verificato autenticazione
e tenant. Questa composizione è testata localmente, ma non è stata eseguita contro
il sito live.

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

`--live` costruisce il runtime browser, esegue il normale flusso adapter di
autenticazione, verifica il marker autenticato e il tenant atteso, persiste il
vault e chiude sempre il runtime. Può quindi contattare DIC e attivare MFA/CAPTCHA;
non è stato eseguito durante questa consegna. In assenza del flag il codice live
non viene invocato.

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
5. classificare CAPTCHA/MFA come interazione umana richiesta;
6. classificare tenant assente o diverso come errore di autorizzazione, senza
   tentare altre aziende.

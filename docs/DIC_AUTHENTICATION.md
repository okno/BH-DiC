# Autenticazione Dipendenti in Cloud

## Stato e confine di sicurezza

L'origine applicativa configurabile è esclusivamente
`https://secure.dipendentincloud.it`. La configurazione rifiuta schemi diversi da
HTTPS, host diversi, credenziali nell'URL, porte diverse da `443`, path, query e
fragment. Playwright non disabilita TLS e non usa `--ignore-certificate-errors`.

Il solo flusso federato ammesso aggiunge questi target HTTPS esatti e nessun altro:

- `https://secure.dipendentincloud.it/it/login`;
- `https://identity.teamsystem.com/`, esclusivamente come schermata e-mail corrente;
- `https://identity.teamsystem.com/Account/LoginEmail`;
- `https://identity.teamsystem.com/Account/LoginPassword`;
- `https://identity.teamsystem.com/connect/authorize`, esclusivamente come stato pending bounded;
- `https://identity.teamsystem.com/connect/authorize/callback`, esclusivamente come stato pending
  bounded;
- `https://secure.dipendentincloud.it/it/callback`, esclusivamente come stato transitorio
  post-submit entro lo stesso budget del login.

Schema, host, porta e path sono confrontati esattamente e i fragment sono
rifiutati; sulla callback DIC la query opaca può essere presente ma non viene letta né registrata.
Una porta esplicita, userinfo, trailing slash, path aggiuntivo, altra origine TeamSystem,
sottodominio somigliante o redirect inatteso falliscono chiuso. Gli eventuali parametri di stato
dell'IdP non possono cambiare il confronto e la query opaca, incluso l'eventuale `login_hint`, non
viene letta né registrata. Non viene mai azionato un controllo passwordless: una sessione SSO
TeamSystem già valida può invece saltare i form soltanto se il successivo marker applicativo e il
tenant DIC vengono attestati esattamente, senza compilare credenziali. La route
`/Account/LoginPasswordExpired` viene riconosciuta soltanto per produrre un errore
fail-closed che richiede rinnovo umano.

Una ricognizione live autorizzata in sola lettura ha osservato questa struttura e
la schermata `PasswordExpired`. La password TeamSystem è stata poi rinnovata nel
flusso umano e il secret locale è stato aggiornato. Il primo
`dic-auth-check --live` della release 0.2.2 si è fermato prima dell'autenticazione per una race di
hydration classificata `DicUiChangedError`: non ha creato un vault e non ha
eseguito Function ID HR. La 0.2.3 ha corretto quel percorso, ma il tentativo successivo si è
fermato fail-closed allo stage `DIC_EMAIL`: il placeholder pubblico corrispondeva sia al componente
padre sia all'input nativo. La 0.2.4 restringe il target all'unico input nativo nel contenitore
pubblico `data-testid="login-email"`. Il tentativo server 0.2.4 ha inviato la password una sola
volta, ma ha classificato come inattesa la callback DIC legittima e si è fermato con exit 78.
Una successiva verifica manuale autorizzata, in browser fresco e in sola lettura, ha accettato le
credenziali con un solo submit e ha osservato password TeamSystem → callback DIC esatta → dashboard
→ route e marker esatti della lista dipendenti. Dopo il deployment 0.2.5, un singolo check headless
ha restituito sessione `AUTHENTICATED`, tenant `VERIFIED_BY_ADAPTER` e ha creato un vault cifrato,
ma non ne ha dimostrato la durabilità al riavvio.
Il riavvio successivo ha evidenziato due limiti ora corretti: DIC passa a TeamSystem un
`login_hint`, quindi l'IdP può saltare direttamente a `LoginPassword`, e i token federati DIC
risiedono in `sessionStorage`, che il solo `storage_state` Playwright non conserva. Dalla 0.2.7
entrambe le transizioni TeamSystem esatte sono ammesse; prima del segreto il form è vincolato
all'account configurato e il vault cifrato include anche lo snapshot bounded della sola origine
DIC. Il normale gateway non invia credenziali e resta disponibile `DEGRADED` se il restore manca.
Il successivo check server della 0.2.7 si è fermato a `TEAMSYSTEM_EMAIL`: l'interfaccia pubblica
corrente espone anche la schermata e-mail sulla root TeamSystem esatta e l'OIDC attraversa le
route esatte `connect/authorize`/`connect/authorize/callback`. La 0.2.8 tratta questi
soli stati come documentato sopra. Se una sessione IdP già valida completa il SSO senza schermate
credenziali, il percorso è accettato soltanto dopo route applicativa DIC, marker autenticato e
attestazione tenant esatta, con zero fill/click/submit sui controlli e-mail/password.

La 0.3.0 conserva questi confini e aggiunge l'osservazione passiva della risposta elenco e la
ripersistenza di una sessione già attestata. Sul target Debian lo SHA esatto
`c2c1e8da8a7f2aba5cb8a9f679d1251e15cb38fe` ha poi superato un unico gate live autorizzato in sola
lettura: autenticazione e tenant sono stati attestati prima delle due letture bounded. Questo
risultato non autorizza write, non estende le route ammesse e non attesta il trasporto Discord.

Discord e il provider di modello non ricevono credenziali, cookie, `storage_state`, primitive
Playwright, righe dipendente, nomi, Employee ID, risultati DIC o una funzione di navigazione
arbitraria. Il confine applicativo è il
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
| `DIC_LOGIN_TIMEOUT_SECONDS` | Budget login | Da 5 a 300 secondi; predefinito 60. È condiviso dall'intero flusso e protetto da un limite esterno con cinque secondi di margine. |
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
3. Se serve un nuovo login, i controlli vengono cercati entro un solo budget
   limitato, ricontrollando a ogni polling la route esatta, il CAPTCHA e
   l'unicità del controllo visibile. Un cambio di origine/path, zero controlli a
   scadenza o più controlli visibili fallisce chiuso.
4. `authenticate()` segue soltanto la sequenza allowlisted DIC → root TeamSystem esatta oppure
   `/Account/LoginEmail` → `/Account/LoginPassword`; può anche entrare direttamente su
   `/Account/LoginPassword` quando TeamSystem usa il `login_hint` già verificato. Le sole
   `/connect/authorize` e `/connect/authorize/callback` esatte sono pending bounded, mai schermate
   su cui cercare controlli. Nel percorso password diretto non compila né invia nuovamente
   l'e-mail. Ogni altra route fallisce chiuso; il controllo password resta univoco e il submit
   resta singolo. I segreti sono compilati direttamente nei controlli previsti.
   Non espone primitive di navigazione arbitrarie. Il campo e-mail DIC usa l'unico
   input nativo sotto il contenitore pubblico `data-testid="login-email"`, evitando
   il placeholder che nella 0.2.3 risolveva anche il componente padre. Il submit DIC
   usa prima il `data-testid` e poi il fallback pubblico verificato
   `button`/`Accedi` esatto.
5. Una sessione TeamSystem già autenticata può saltare entrambe le schermate credenziali. Questo
   SSO silenzioso è accettato esclusivamente quando raggiunge la route applicativa DIC e il probe
   conferma sia marker autenticato sia tenant atteso; prima di tale prova non viene dichiarato
   alcun successo. Il percorso garantisce zero azioni sui controlli e-mail/password. Dopo
   l'eventuale unico submit password, la route DIC esatta `/it/callback` è ammessa soltanto come
   stato transitorio, con query opaca mai ispezionata o registrata. Il polling continua entro il
   budget residuo; fragment, porta esplicita, userinfo, host somigliante, trailing slash e path
   aggiuntivi sono rifiutati. Non esiste un secondo submit automatico.
6. Probe di sessione e autenticazione sono serializzati dalla stessa coda e
   acquisiscono lo stesso lock browser, ma vengono eseguiti una sola volta. Il
   valore predefinito usa 60 secondi per il flusso e un guard esterno di 65
   secondi; un timeout o errore di trasporto non ritenta le credenziali.
   Se il login incontra una route applicativa già ripristinata, il relativo probe
   usa soltanto il tempo residuo dello stesso budget e ricontrolla la deadline
   prima di dichiarare successo; un superamento viene classificato allo stage
   `SESSION_PROBE`.
7. `PasswordExpired`, CAPTCHA o un passaggio interattivo non supportato
   interrompono il flusso con un errore tipizzato che richiede intervento umano.
8. Qualunque campo MFA visibile interrompe il flusso con `DicMfaRequiredError`; questa versione
   non compila né invia codici MFA e non usa controlli passwordless non osservati.
9. Dopo il submit, l'adapter ripete il probe sulla route fissa e attende il marker autenticato con
   polling limitato durante la stessa cattura tenant, usando soltanto il budget residuo. Marker e
   corrispondenza esatta del tenant sono entrambi obbligatori prima di persistere la sessione. Se
   il click può essere partito ma completamento, tenant probe o persistenza del vault non sono
   dimostrabili, restituisce `DicAuthOutcomeUnknownError` allo stage `CREDENTIAL_SUBMIT`:
   l'operatore non deve ritentare automaticamente.

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

## Lettura passiva dell'elenco dipendenti

La 0.3.0 applica lo stesso principio di osservazione passiva alla lista dipendenti. Playwright
installa un listener bounded prima di una navigazione o azione UI deterministica e accetta
soltanto la risposta che la pagina emette su origine esatta
`https://secure.dipendentincloud.it`, path esatto `/backend_apiV2/employees`, metodo `GET`, status
`200` e media type JSON. BH-DiC non costruisce né invia una richiesta HTTP diretta a questo path.

Il contratto URL della risposta e quello del paginator sono distinti. Una diagnostica live
autorizzata e minimizzata ha osservato che `path`, gli URL di prima/ultima/pagina
precedente/successiva e gli URL non nulli di `links` usano la stessa origine HTTPS esatta ma il
path `/employees`. Il campo `path` resta privo di query; ogni URL pagina non nullo preserva invece
l'intera query UI validata di nove parametri e può cambiare soltanto il valore canonico di `page`.
I boundary precedente/successivo e il link attivo vengono correlati alla pagina corrente e
all'ultima pagina senza fidarsi delle label visuali. Il parser rifiuta userinfo, porta esplicita,
fragment, origin o path lookalike e anche la sostituzione reciproca dei due path. Ogni difformità
produce un errore generico fail-closed senza includere URL, query, body o PII. Questo contratto è
stato attraversato dal gate live bounded della 0.3.0; restano separate e non verificate le altre
modalità read e lo smoke del trasporto Discord.

La query catturata deve corrispondere all'azione UI appena eseguita: pagina, page size fisso,
ricerca, campi di ricerca, ordinamento e filtro `active` sono confrontati con un insieme chiuso.
Risposte precedenti al marker o non correlate all'azione vengono ignorate; overflow, timeout e
metadati inattesi fanno fallire chiuso la lettura. Il corpo massimo è 256 KiB, il JSON deve essere
UTF-8 senza chiavi duplicate e tenant, paginazione, URL correlati e conteggi devono essere
coerenti.

Lo schema root chiuso comprende soltanto `current_page`, `data`, `first_page_url`, `from`,
`last_page`, `last_page_url`, `links`, `next_page_url`, `path`, `per_page`, `prev_page_url`, `to` e
`total`. Ogni riga viene validata indipendentemente e `current_contract` ammette due soli keyset
esatti. `BASE` contiene `hours_type`, `id`, `part_time_percentage`, `permanent`, `valid_from` e
`valid_to`. `EXTENDED` contiene tutto `BASE` più `flexible_workinghours`, `hours_alert`, `note`,
`ongoing`, `workinghours` e `workinghours_list`. Nel formato esteso `flexible_workinghours`,
`hours_alert` e `ongoing` devono essere booleani JSON stretti; `note`, `workinghours` e
`workinghours_list` devono essere `null` stretti. I sei campi tecnici vengono validati e scartati:
non entrano nella proiezione, nella persistenza o nei log. Una risposta può contenere record `BASE`
e `EXTENDED` insieme; qualunque subset, superset, chiave sconosciuta o shape diversa fallisce
chiuso. `part_time_percentage` resta obbligatoria ma nullable; quando non è `null` deve essere un
intero JSON stretto compreso tra 0 e 100 inclusi (`bool`, float e stringhe sono rifiutati). Campi o
tipi inattesi, date non ISO, company ID difforme, ID duplicati o paginazione instabile producono un
errore generico senza body o PII.

All'esterno del parser esce soltanto una proiezione tipizzata. Il nome visualizzato completo è
conservato transitoriamente come `SecretStr`, escluso da `repr` e mascherato nei dump del modello;
le iniziali restano disponibili come alternativa sicura. Il presenter apre il `SecretStr` soltanto
per elenchi e scadenze classificati `SENSITIVE`, destinati a un richiedente `HR_READ` tramite
risposta ephemeral. E-mail, codice fiscale e matricola restano mascherati; stato, mansione, team,
luogo e date del contratto corrente sono i soli altri campi proiettati.

Il nome completo non entra mai in aggregati pubblici, provider di modello, log, audit, telemetria
token o persistenza applicativa. Il body originale non viene registrato, persistito o inserito
nell'audit. Un uso futuro del nome fuori dal renderer sensibile richiede una nuova review privacy.

Gli errori password scaduta/CAPTCHA/MFA corrispondono operativamente a
`AUTHENTICATION_INTERACTIVE_REQUIRED`: fermare il job e completare il passaggio
solo con una procedura umana autorizzata. Non tentare bypass, automazioni CAPTCHA
o indebolimenti dei controlli browser.

`DIC_TOTP_SECRET` resta presente per compatibilità di configurazione, ma questa versione non lo
consuma e non automatizza MFA. Un challenge MFA impone lo stop e una procedura umana autorizzata,
senza registrare o persistere codici. Finché una composizione specifica non viene osservata,
implementata e verificata, MFA live è una limitazione nota.

## Vault di sessione

`FernetSessionVault` salva soltanto un modello `StoredBrowserSession`, contenente lo
`storage_state`, lo snapshot `sessionStorage` della sola origine DIC, gli istanti di
autenticazione/scadenza e un eventuale hint redatto. Il file è cifrato con Fernet prima della
scrittura. I vault legacy privi di `sessionStorage` restano leggibili, ma possono richiedere un
nuovo login esplicito prima di diventare riutilizzabili.

Le proprietà implementate sono:

- directory creata con modalità richiesta `0700`;
- file temporaneo cifrato con modalità `0600`;
- `fsync`, sostituzione atomica e modalità finale `0600`;
- nessun file temporaneo in chiaro;
- rifiuto di vault illeggibili, alterati o cifrati con una chiave diversa;
- rifiuto delle sessioni scadute;
- invalidazione mediante eliminazione del solo file di sessione;
- massimo 64 entry `sessionStorage`, chiavi/valori e dimensione totale limitati, chiavi duplicate
  rifiutate e origine fissata esattamente a `https://secure.dipendentincloud.it`;
- ripristino one-shot in un documento bootstrap DIC vuoto, intercettato localmente senza risposta
  di rete, prima della prima navigazione applicativa; il payload è passato come argomento solo su
  quell'origine e non viene ripetuto dopo refresh token o logout.

La semantica dei bit POSIX dipende dal filesystem e dall'host: sull'host Linux di
destinazione va verificata con `stat` prima di considerarla operativa.

`DicSessionManager` applica una durata predefinita di otto ore e può caricare, salvare o
invalidare lo stato. Il bootstrap non-mock collega settings, vault e browser context: carica
cookie/localStorage e ripristina una volta lo snapshot `sessionStorage` cifrato prima degli script
applicativi. Soltanto il comando operatore esplicito `dic-auth-check --live` può inviare
credenziali e creare una nuova sessione, e lo fa solo dopo autenticazione e attestazione tenant
verificate. Il normale gateway Discord non invia mai credenziali. La 0.3.0 può però ripersistire,
con lock serializzato, lo stato già autenticato dopo una verifica tenant-attestata o una lettura
DIC riuscita: questo conserva le normali rotazioni di cookie e `sessionStorage` senza effettuare
un nuovo login. Stati non autenticati, tenant non attestati, errori e letture fallite non
sovrascrivono il vault.

Se la sessione manca, scade o il vault è illeggibile, il gateway resta online in stato `DEGRADED`,
preserva il file e le funzioni DIC falliscono chiuso. Il check esplicito continua invece a
rifiutare un vault illeggibile finché l'operatore non decide se conservarlo o invalidarlo. Questa composizione è testata
localmente. I tentativi 0.2.2 e 0.2.3 si
sono fermati prima del submit password; la 0.2.4 ha raggiunto la callback DIC dopo un singolo
submit, ma l'ha rifiutata fail-closed. Un successivo check headless ha confermato autenticazione e
tenant nel contesto corrente; la 0.2.7 corregge il ripristino completo dopo il riavvio. Il check
server 0.2.7 successivo si è fermato a `TEAMSYSTEM_EMAIL`; la 0.2.8 ha ampliato soltanto il
contratto di route esatte descritto sopra. Il gate live bounded della 0.3.0 ha successivamente
verificato autenticazione, tenant e letture riuscite sullo SHA documentato, senza abilitare write.
Un errore di persistenza dopo autenticazione verificata non viene interpretato
come logout: resta un esito `CREDENTIAL_SUBMIT` sconosciuto, senza secondo login automatico.

## Comando di verifica autenticazione

Il controllo predefinito è strettamente locale e non avvia Chromium né effettua
richieste di rete:

```bash
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin \
  /opt/bh-dic/.venv/bin/python -m bh_dic dic-auth-check
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
sudo systemctl stop bh-dic.service &&
test "$(sudo systemctl show -p ActiveState --value bh-dic.service)" = inactive &&
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin /bin/bash -c '
  set -Eeuo pipefail
  cd /opt/bh-dic
  .venv/bin/python -m bh_dic dic-auth-check --live
'
```

La sequenza non riavvia il servizio. Su un host PID-only fermare e verificare prima il processo con
`stop.sh` come owner del progetto; non mescolare quel backend con systemd.

`--live` costruisce il runtime browser, prova prima il ripristino mediante la
route read-only fissa, esegue il login allowlisted solo se necessario, verifica
marker autenticato e attestazione tenant, persiste il vault e chiude sempre il
runtime. Può quindi contattare DIC e TeamSystem e attivare MFA/CAPTCHA. Dopo il
rinnovo della password, il tentativo 0.2.2 si è fermato fail-closed prima
dell'autenticazione per hydration incompleta. Il tentativo 0.2.3 ha superato quel punto ma si è
fermato allo stage `DIC_EMAIL` per l'ambiguità padre/input del placeholder. La 0.2.4 usa il target
nativo univoco, ha effettuato un solo submit e ha poi rifiutato la callback DIC legittima con exit
78. La 0.2.5 corregge esclusivamente questo stato transitorio e l'attesa bounded del marker.
Il check autorizzato 0.2.5 è stato eseguito una sola volta con bot fermo e write disabilitate e ha
verificato autenticazione, tenant e scrittura del vault nel processo corrente; il riavvio ha poi
evidenziato il campo `sessionStorage` mancante, corretto nella 0.2.7. Un check 0.2.7 successivo si è
fermato prima delle azioni credenziali a `TEAMSYSTEM_EMAIL`; la 0.2.8 ha corretto il solo contratto
TeamSystem/OIDC. Infine, il gate live unico della 0.3.0 sullo SHA documentato ha verificato
autenticazione e tenant prima delle letture bounded, mantenendo tutte le write disabilitate. In
assenza del flag il codice live non viene invocato; per futuri rinnovi o invalidazioni resta
obbligatoria la stessa procedura singola.

## Invalidazione e rotazione

Per una rotazione di password/account/tenant, fermare prima il processo BH-DiC, invalidare una
sola volta e fare una sola verifica live come stesso utente di servizio:

```bash
sudo systemctl stop bh-dic.service &&
test "$(sudo systemctl show -p ActiveState --value bh-dic.service)" = inactive &&
sudo -u bh-dic -H env PATH=/usr/local/bin:/usr/bin:/bin /bin/bash -c '
  set -Eeuo pipefail
  cd /opt/bh-dic
  .venv/bin/python -m bh_dic invalidate-session
  .venv/bin/python -m bh_dic dic-auth-check --live
'
```

Il comando risolve il path configurato tramite `FernetSessionVault` ed elimina
soltanto quel file. Non usare glob, cancellazioni ricorsive o comandi manuali che
possano colpire altre sessioni.

In caso di sospetta compromissione senza credenziale sostitutiva, eseguire soltanto
`invalidate-session` nello stesso confine systemd/utente e lasciare il servizio fermo. Nessuna
procedura in questa sezione avvia automaticamente il bot.

La rotazione di `DIC_SESSION_ENCRYPTION_KEY` rende il vault precedente indecifrabile. La sequenza
sicura mantiene systemd fermo per tutto il cambio: invalidare il vault con la configurazione
corrente, aggiornare la chiave nel secret manager/`.env` come `bh-dic`, rieseguire doctor e un solo
check live guarded, quindi avviare separatamente il servizio soltanto dopo il successo e
l'autorizzazione operatore. Non conservare copie in chiaro della chiave precedente.

Una rotazione di password, account o tenant richiede sempre l'invalidazione deliberata del vault,
con servizio fermo. Dopo la rotazione eseguire `invalidate-session` esattamente una volta e poi un
solo `dic-auth-check --live`; non alternare invalidazioni e tentativi e non creare un loop. Un
cambio di tenant deve aggiornare anche `DIC_EXPECTED_TENANT_ID`; senza questa corrispondenza
l'adapter rifiuta la sessione. Se il check restituisce `CREDENTIAL_SUBMIT`, l'invio può essere
partito: fermarsi e non ritentare, anche se il vault è assente.

## Diagnostica senza segreti

È sicuro registrare solo stato astratto ed errori tipizzati: browser disponibile,
sessione autenticata/non autenticata/scaduta, tenant configurato sì/no e stage
chiuso. Non registrare valori di username, password, TOTP, chiave Fernet, cookie,
header, URL, selettori, HTML autenticato o `storage_state`.

Gli errori del comando sono JSON e contengono esclusivamente `error_type` e uno
stage tra `DIC_EMAIL`, `DIC_SUBMIT`, `TEAMSYSTEM_EMAIL`,
`TEAMSYSTEM_EMAIL_SUBMIT`, `TEAMSYSTEM_CREDENTIAL`,
`TEAMSYSTEM_CREDENTIAL_SUBMIT`, `CREDENTIAL_SUBMIT`, `SESSION_PROBE` e
`UNCLASSIFIED`. Non mostrare il messaggio
interno dell'eccezione per ottenere maggiori dettagli e non attivare trace o
screenshot sul tenant live senza un'autorizzazione separata.

`CREDENTIAL_SUBMIT` significa che l'invio della credenziale può avere raggiunto l'IdP, ma il
risultato finale non è dimostrabile. `dic-auth-check --live` termina allora con exit code 78.
Dalla 0.2.7 il normale comando di servizio `run` non invia credenziali DIC e può restare online
`DEGRADED`; `RestartPreventExitStatus=78` rimane una difesa aggiuntiva. Fermare il check esplicito,
non rilanciarlo in loop e verificare lo stato dell'account con una procedura umana autorizzata.
La 0.2.8 non cambia questa regola: il percorso SSO senza credenziali è accettato solo
dopo marker e tenant e non autorizza un retry quando l'esito del submit è incerto.

Il caso osservato nella 0.2.4 è stato ricondotto alla callback DIC legittima non ancora
allowlistata, non allo user agent: la 0.2.5 conserva lo user agent Chromium nativo e non introduce
spoofing. Un nuovo `CREDENTIAL_SUBMIT` dopo l'aggiornamento resta comunque un esito sconosciuto e
impone nuovamente lo stop, senza tentativi aggiuntivi.

In caso di errore:

1. verificare che `DIC_BASE_URL` sia l'origine esatta;
2. verificare solo la presenza, non il valore, delle variabili obbligatorie;
3. verificare proprietà e permessi del path del vault;
4. invalidare una sessione scaduta o illeggibile;
5. usare lo stage chiuso per individuare il solo passaggio da verificare, senza
   stampare DOM, URL o valori dei campi e senza ampliare i selettori;
6. classificare password scaduta, CAPTCHA/MFA o redirect inatteso come azione
   umana richiesta, senza usare passwordless o allargare l'allowlist;
7. classificare attestazione tenant assente, invalida o diversa come errore di autorizzazione, senza
   tentare altre aziende.

# Limitazioni note dell'adapter DIC

## Verifica live e compatibilità UI

- Oltre alla ricognizione iniziale, lo SHA 0.3.0 esatto documentato ha superato un unico gate live
  autorizzato in sola lettura per autenticazione/tenant, conteggio aggregato e scadenze bounded del
  prossimo mese di calendario. Non sono state eseguite write live.
- La baseline di Fase 1 non certifica da sola il DOM attuale. Soltanto i due subset bounded
  attraversati dal gate hanno stato `LIVE_READ_VERIFIED`; le altre read restano da validare.
- `data-testid`, attributi `data-*`, ordinamento dei fallback e controlli distintivi delle pagine
  HR richiedono validazione live autorizzata. Un cambio UI può produrre `DicUiChangedError` e
  aprire il circuit breaker.
- Non è stata identificata o adottata un'API pubblica supportata da DIC. L'attestazione tenant
  osserva passivamente una risposta first-party emessa durante una normale navigazione UI; non la
  espone come API operativa e non sostituisce i Page Object.
- I Page Object verificano origine e route esatta; la presenza di tutti i
  controlli distintivi non è stata collaudata sul sito corrente.

## Autenticazione e sessione

- `DIC_TOTP_SECRET` non è consumato dal flusso live corrente. Qualunque challenge MFA si ferma
  fail-closed e richiede una procedura umana autorizzata; nessun codice viene compilato o inviato.
- CAPTCHA richiede sempre intervento umano e non viene aggirato.
- Un check headless 0.2.5 ha autenticato sessione e tenant nel processo corrente, ma il vault
  precedente salvava soltanto cookie/localStorage. DIC conserva anche token federati in
  `sessionStorage`, perciò il riavvio successivo li ha persi e si è fermato a `TEAMSYSTEM_EMAIL`.
  La 0.2.7 cifra uno snapshot bounded della sola origine DIC e ne ripristina l'origine esatta prima
  della navigazione; il gate 0.3.0 ha successivamente verificato autenticazione e tenant sul
  server, senza rendere il formato portabile ad altri ambienti.
- DIC può usare `login_hint` e saltare `LoginEmail`. La 0.2.7 ammette soltanto le route TeamSystem
  esatte `LoginEmail`/`LoginPassword` e, prima di compilare il segreto, richiede che l'identità
  esposta dal form password coincida con quella configurata. Qualunque assenza, ambiguità o
  mismatch fallisce chiuso.
- Un check server storico della 0.2.7 si è fermato a `TEAMSYSTEM_EMAIL` prima delle azioni
  credenziali. La UI pubblica corrente espone la schermata e-mail anche sulla root TeamSystem
  esatta e il flusso OIDC attraversa `/connect/authorize` e `/connect/authorize/callback`. La
  0.2.8 gestisce soltanto questi path esatti e il legacy `/Account/LoginEmail`; può
  accettare SSO senza controlli soltanto dopo marker DIC e tenant attestato, con zero azioni
  credenziali. Il gate 0.3.0 ha poi attestato autenticazione e tenant sullo SHA verificato.
- Dopo la rotazione di password/account/tenant, il vault precedente deve essere invalidato una
  volta deliberatamente prima di un unico check live. Questa procedura non autorizza retry dopo
  `CREDENTIAL_SUBMIT` o un altro esito post-submit incerto.
- Il gateway 0.2.7 non invia credenziali DIC all'avvio. Un vault mancante, scaduto o illeggibile
  lascia Discord disponibile in stato `DEGRADED`; il check esplicito resta fail-closed e non
  sovrascrive automaticamente un vault illeggibile.
- L'identificatore aziendale non è disponibile in un marker DOM stabile. L'adapter richiede quindi
  la risposta first-party prevista durante la navigazione controllata; risposta assente, ambigua,
  malformata o difforme fallisce chiuso.

## Read e dati

- La 0.3.0 osserva soltanto la risposta UI esatta `/backend_apiV2/employees`; i metadati URL del
  paginator osservati usano il path distinto `/employees`. Non esiste un adapter API pubblico
  supportato né un fallback a chiamate HTTP dirette. I due path non sono intercambiabili; gli URL
  pagina devono ripetere l'intera query UI validata cambiando soltanto `page`. Drift di origin,
  path, query, boundary link, schema o paginazione fallisce chiuso. I due percorsi bounded descritti
  nello stato live sono verificati; altre read e lo smoke del trasporto Discord restano `PENDING`.
- `current_contract` accetta per-record soltanto il keyset esatto `BASE` oppure
  `EXTENDED=BASE+6` chiavi tecniche. L'esteso richiede booleani stretti per
  `flexible_workinghours`, `hours_alert` e `ongoing`, e `null` stretti per `note`, `workinghours` e
  `workinghours_list`; i campi tecnici vengono scartati senza proiezione. Qualunque subset,
  superset, shape diversa o chiave futura interrompe la read finché non viene riesaminata.
- Il totale non qualificato significa l'intero organico; “attivi” o “disattivati” devono essere
  espliciti. Questo elimina l'ambiguità del modello ma non risolve eventuali categorie DIC future
  fuori dal campo `active` osservato.
- L'analisi bulk delle scadenze usa esclusivamente `current_contract.valid_to` dell'elenco. Non
  effettua fetch N+1 e non include contratti storici, futuri multipli o date che lo schema chiuso
  non riesce a interpretare. I risultati individuali sono limitati a 25 campi Discord.
- I risultati del browser dipendono da label italiane, accessibilità e struttura
  della tabella; locale diverso da `it-IT` non è stato testato.
- La pagina ruoli può non essere disponibile per dipendenti non collegati.
- Lo stato timbratura osservato via pagina ruoli/settings non equivale a una
  comprensione completa del workflow di configurazione.
- Il campione payroll della baseline era vuoto: upload, eliminazione,
  pubblicazione e contenuto busta paga non sono implementati né dichiarati.
- La ricerca collettiva delle buste paga confronta soltanto anno, mese e presenza del metadato
  sulle pagine payroll registrate; non scarica né interpreta il contenuto della busta ed è bounded
  a 500 dipendenti per esecuzione. La verifica live della tabella payroll resta necessaria.
- La risposta elenco DiC corrente non espone il netto mensile. Tabelle ed export indicano `N/D`:
  il bot non ricava né stima il valore da metadati di busta paga.
- Ricerca, filtri, ordinamento e paginazione sono deterministici nel mock; effetti
  e limiti live (debounce, page size, combinazioni di filtri) non sono verificati.
- Il nome visualizzato completo è intenzionalmente disponibile come `SecretStr` transitorio per
  il solo renderer `HR_READ` sensibile/ephemeral; non è redatto in quella risposta autorizzata.
  E-mail, codice fiscale e matricola restano mascherati; gli altri campi operativi sono bounded e
  tipizzati. Correttezza, necessity del nome e copertura di nuove colonne devono essere riesaminate
  dopo la verifica live.

## Write, riconciliazione e file

- Tutte le write sono `DISABLED_BY_POLICY` e `LIVE_WRITE_UNVERIFIED`.
  `DISABLED_BY_DEFAULT` descrive soltanto l'evidenza di configurazione in `.env.example`, non uno
  stato operativo alternativo. Le write non devono essere abilitate soltanto per completare un test.
- `EMP-INVITE-001`, `EMP-DOC-005`, `EMP-DOC-003` e `EMP-CONTRACT-003` sono presenti nel
  catalogo/policy/mock, ma il dispatch Playwright è `NOT_AVAILABLE` e fallisce chiuso.
- `EMP-EXPORT-001` genera PDF, DOCX e XLSX in memoria e non persiste contenuto in chiaro. Il
  percorso è testato con dati sintetici; la completezza delle colonne provenienti dalla UI DiC è
  `NEEDS_VALIDATION` live e la consegna Discord segue la retention esterna del canale.
- `EMP-CREATE-001` è `PARTIALLY_COMPLETED`: il mock copre lo schema completo, mentre il live
  accetta solo il subset riconciliabile. `birth_date`, `iban`, `phone`, `address` e `notes` sono
  rifiutati prima della creazione del pending.
- L'upload documenti richiede un file risolto sotto la root controllata e la capability ClamAV.
  Il pending conserva soltanto l'`upload_id`: path locale e SHA-256 non entrano in eventi, log,
  Discord o al provider di modello. Lo SHA-256 è visibile esclusivamente all'operatore locale nei
  metadati file.
  Dimensioni/formati effettivi, validazioni del form, versionamento, firma, sostituzione e
  notifiche del sito restano ignoti.
- Le write create/update possono non restituire un ID stabile. Senza ID o
  postcondizione confrontabile, la riconciliazione restituisce `UNKNOWN` e vieta
  il retry automatico.
- La riconciliazione di campi sensibili redatti non può confermare in modo sicuro
  ogni modifica anagrafica. Questi casi richiedono verifica umana autorizzata.
- Eliminazioni, correzione bilancio, RBAC e scollegamento richiedono doppia
  approvazione e conferma testuale, ma non sono stati eseguiti live.
- Il mock dimostra la macchina a stati e i contratti, non semantica, permessi o
  validazioni server-side del prodotto DIC.

## Funzioni non determinate

Restano `NEEDS_DISCOVERY`: pulsante **Controlla**, link invito esplicito,
configurazione completa timbrature, gestione/upload buste paga, azioni massive,
filtri avanzati, importazione, duplicazione e archiviazione. Non esporre tool
operativi per questi workflow finché una ricognizione mirata non ne determina
semantica, permessi e postcondizioni.

## Provider di modello e persona

- Il router supporta OpenAI, Groq e llama/OpenAI-compatible. Groq con
  `openai/gpt-oss-120b` ha superato il probe live chiuso sul server; quota, latenza nel tempo e gli
  altri provider/modelli restano verifiche separate.
- Il runtime llama locale, il modello e la protezione della porta sono responsabilità
  dell'operatore e non vengono installati da BH-DiC.
- Il provider vede categorie semantiche canoniche, sole date ISO necessarie e segnaposto; ogni
  numero standalone viene redatto. Non vede vocaboli utente grezzi, nomi, Employee ID, query di
  ricerca o risultati DIC. Espressioni non mappabili
  possono diventare `[TERM_REDACTED]` e richiedere una formulazione più semplice; è un fail-closed
  di privacy, non un errore da aggirare.
- La lingua/persona riguarda presenter locale e chiarimenti. Il profilo non migliora
  l'autorizzazione, non sostituisce la validazione deterministica e non rende BH-DiC un assistente
  generalista o un bot di moderazione Discord.
- I token cumulativi includono soltanto contatori validi dichiarati dal provider nel database
  corrente. Le chiamate `UNAVAILABLE`/`UNKNOWN` sono conteggiate come gap, mai stimate; il totale
  non equivale alla fattura e può cambiare dopo restore o sostituzione del database.

## Operatività e scala

- Il runtime browser è progettato per Chromium asincrono, un account condiviso e
  concorrenza predefinita pari a uno. Firefox/WebKit, più tenant e alta
  concorrenza non sono supportati.
- Queue, lock per chiave, lock globale write, retry read limitato e circuit breaker
  sono testati localmente; comportamento sotto carico reale e recovery di
  processo/container non sono verificati.
- La riconciliazione può essere eseguita anche dopo kill switch o scadenza
  dell'approvazione, ma richiede comunque accesso read al tenant.
- Python 3.12, dipendenze, migrazione, Chromium Playwright, ClamAV, directory runtime e doctor
  offline/online sono stati verificati sul Debian target. La versione 0.3.0 allo SHA esatto
  documentato ha superato il gate applicativo live; il servizio è `active/running`, con zero
  riavvii osservati e gateway `discord_ready`. Smoke del trasporto Discord, restore drill e carico
  reale restano da verificare sul target.

## Criterio per rimuovere una limitazione

Una limitazione può essere chiusa solo con evidenza ripetibile: test automatico,
smoke test read-only autorizzato o write su tenant/record esplicitamente dedicati
con tutti i tre gate live, audit e riconciliazione. Aggiornare contestualmente
`RECONNAISSANCE_BASELINE.md`, `SELECTOR_MAINTENANCE.md` e
`LIVE_VERIFICATION_STATUS.md`; non trasformare un'osservazione manuale in una
dichiarazione di stabilità senza test.

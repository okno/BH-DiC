# Limitazioni note dell'adapter DIC

## Verifica live e compatibilità UI

- Una ricognizione autorizzata in sola lettura ha osservato la struttura corrente del login e la
  sorgente first-party dell'identità aziendale. Non ha eseguito Function ID DIC, smoke read-only
  applicativi o write live.
- La baseline di Fase 1 documenta route, etichette e controlli osservati, ma non
  certifica il DOM attuale. Nessuna read ha quindi stato `LIVE_READ_VERIFIED`.
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
  della navigazione; il nuovo formato resta da verificare sul server.
- DIC può usare `login_hint` e saltare `LoginEmail`. La 0.2.7 ammette soltanto le route TeamSystem
  esatte `LoginEmail`/`LoginPassword` e, prima di compilare il segreto, richiede che l'identità
  esposta dal form password coincida con quella configurata. Qualunque assenza, ambiguità o
  mismatch fallisce chiuso.
- Il gateway 0.2.7 non invia credenziali DIC all'avvio. Un vault mancante, scaduto o illeggibile
  lascia Discord disponibile in stato `DEGRADED`; il check esplicito resta fail-closed e non
  sovrascrive automaticamente un vault illeggibile.
- L'identificatore aziendale non è disponibile in un marker DOM stabile. L'adapter richiede quindi
  la risposta first-party prevista durante la navigazione controllata; risposta assente, ambigua,
  malformata o difforme fallisce chiuso.

## Read e dati

- I risultati del browser dipendono da label italiane, accessibilità e struttura
  della tabella; locale diverso da `it-IT` non è stato testato.
- La pagina ruoli può non essere disponibile per dipendenti non collegati.
- Lo stato timbratura osservato via pagina ruoli/settings non equivale a una
  comprensione completa del workflow di configurazione.
- Il campione payroll della baseline era vuoto: upload, eliminazione,
  pubblicazione e contenuto busta paga non sono implementati né dichiarati.
- Ricerca, filtri, ordinamento e paginazione sono deterministici nel mock; effetti
  e limiti live (debounce, page size, combinazioni di filtri) non sono verificati.
- I record letti sono redatti, ma la correttezza dei campi e la copertura di nuove
  colonne devono essere riesaminate dopo la verifica DOM.

## Write, riconciliazione e file

- Tutte le write sono `DISABLED_BY_POLICY` e `LIVE_WRITE_UNVERIFIED`.
  `DISABLED_BY_DEFAULT` descrive soltanto l'evidenza di configurazione in `.env.example`, non uno
  stato operativo alternativo. Le write non devono essere abilitate soltanto per completare un test.
- `EMP-INVITE-001`, `EMP-DOC-005`, `EMP-EXPORT-001`, `EMP-DOC-003` e
  `EMP-CONTRACT-003` sono presenti nel catalogo/policy/mock, ma il dispatch Playwright è
  `NOT_AVAILABLE` e fallisce chiuso. Per export e download manca inoltre un artifact service
  locale protetto con permessi, cifratura, audit e retention definiti.
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
- La lingua della persona riguarda chiarimenti e decorazioni; dati/output operativi restano in
  italiano. Il profilo non migliora l'autorizzazione, non sostituisce la validazione deterministica
  e non rende BH-DiC un assistente generalista o un bot di moderazione Discord.

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
  offline/online sono stati verificati sul Debian target. Login DIC/tenant e gateway Discord hanno
  avuto successi storici separati, ma l'ultimo servizio pre-0.2.7 si è fermato prima del gateway;
  il nuovo restore vault, l'avvio degradato, il restore drill e il carico reale restano da verificare.

## Criterio per rimuovere una limitazione

Una limitazione può essere chiusa solo con evidenza ripetibile: test automatico,
smoke test read-only autorizzato o write su tenant/record esplicitamente dedicati
con tutti i tre gate live, audit e riconciliazione. Aggiornare contestualmente
`RECONNAISSANCE_BASELINE.md`, `SELECTOR_MAINTENANCE.md` e
`LIVE_VERIFICATION_STATUS.md`; non trasformare un'osservazione manuale in una
dichiarazione di stabilità senza test.

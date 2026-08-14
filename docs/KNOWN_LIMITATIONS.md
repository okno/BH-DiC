# Limitazioni note dell'adapter DIC

## Verifica live e compatibilità UI

- Nessun login, probe DOM, smoke test read-only o test write live è stato eseguito
  durante la Fase 2 corrente.
- La baseline di Fase 1 documenta route, etichette e controlli osservati, ma non
  certifica il DOM attuale. Nessuna read ha quindi stato `LIVE_READ_VERIFIED`.
- `data-testid`, attributi `data-*`, ordinamento dei fallback e controlli
  distintivi richiedono validazione live autorizzata. Un cambio UI può produrre
  `DicUiChangedError` e aprire il circuit breaker.
- Non è stata identificata o adottata un'API ufficiale DIC. L'implementazione è
  un adapter UI confinato alle route censite e non usa endpoint privati inventati.
- I Page Object verificano origine e route esatta; la presenza di tutti i
  controlli distintivi non è stata collaudata sul sito corrente.

## Autenticazione e sessione

- La generazione TOTP automatica da `DIC_TOTP_SECRET` non è collegata. Il form MFA
  accetta un codice monouso già fornito; in sua assenza l'autenticazione si ferma.
- CAPTCHA richiede sempre intervento umano e non viene aggirato.
- La composizione settings → vault → browser context → persistenza è collegata
  al bootstrap non-mock e testata con boundary sintetici, ma non è stata verificata
  sul sito o sull'host Linux finale. `dic-auth-check` controlla offline vault e
  sessione senza rete; solo `dic-auth-check --live` può verificare sessione e tenant
  contro DIC, e non è stato eseguito in questa consegna.
- Permessi POSIX `0600`/`0700`, scadenza reale della sessione e rotazione devono
  essere verificati sull'host Linux finale.
- L'adapter richiede un tenant ID osservabile nel DOM. Se il sito non espone un
  attributo stabile e verificabile, l'accesso fallisce chiuso.

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

- Tutte le write sono `DISABLED_BY_DEFAULT` e `LIVE_WRITE_UNVERIFIED`. Non devono
  essere abilitate soltanto per completare un test.
- `EMP-EXPORT-001` e `EMP-DOC-003` sono implementate nel catalogo/policy/mock, ma
  il dispatch Playwright resta bloccato finché manca un artifact service locale
  protetto con permessi, cifratura, audit e retention definiti.
- L'upload documenti richiede un path risolto sotto una quarantine root e la
  capability ClamAV. Dimensioni/formati effettivi, validazioni del form,
  versionamento, firma, sostituzione e notifiche del sito restano ignoti.
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

## Operatività e scala

- Il runtime browser è progettato per Chromium asincrono, un account condiviso e
  concorrenza predefinita pari a uno. Firefox/WebKit, più tenant e alta
  concorrenza non sono supportati.
- Queue, lock per chiave, lock globale write, retry read limitato e circuit breaker
  sono testati localmente; comportamento sotto carico reale e recovery di
  processo/container non sono verificati.
- La riconciliazione può essere eseguita anche dopo kill switch o scadenza
  dell'approvazione, ma richiede comunque accesso read al tenant.
- Non è stato effettuato deploy o collaudo dell'adapter sull'host remoto. Stato
  systemd, browser installato, dipendenze native e storage persistente restano
  verifiche operative separate.

## Criterio per rimuovere una limitazione

Una limitazione può essere chiusa solo con evidenza ripetibile: test automatico,
smoke test read-only autorizzato o write su tenant/record esplicitamente dedicati
con tutti i tre gate live, audit e riconciliazione. Aggiornare contestualmente
`RECONNAISSANCE_BASELINE.md`, `SELECTOR_MAINTENANCE.md` e
`LIVE_VERIFICATION_STATUS.md`; non trasformare un'osservazione manuale in una
dichiarazione di stabilità senza test.

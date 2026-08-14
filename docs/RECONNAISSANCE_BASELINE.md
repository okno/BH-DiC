# Baseline di ricognizione Dipendenti in Cloud

## Provenienza e attendibilità

Questa baseline trascrive esclusivamente la ricognizione read-only della Fase 1
fornita nel master prompt. In questa sessione di sviluppo non è stato aperto il
sito DIC e non è stato eseguito alcun probe live, login, lettura DOM o write.

La baseline dimostra che determinate route, etichette e controlli erano stati
osservati in Fase 1; non dimostra che il DOM o i selettori siano invariati oggi.
Per questo non equivale allo stato `LIVE_READ_VERIFIED` della Fase 2. Non sono
riportati nomi, e-mail, codici fiscali, identificativi reali, documenti o conteggi
dei dipendenti.

Il perimetro ammesso è il menu **Dipendenti** e le pagine direttamente collegate.
Contabilità, configurazioni aziendali generali, altri tenant e navigazione
arbitraria sono fuori perimetro.

## Route e controlli osservati

| Schermata | Route osservata | Baseline funzionale |
| --- | --- | --- |
| Lista dipendenti | `/it/app/employees/list` | Tab Attivi, Disattivati e Tutti; Cerca; ordinamento; paginazione; Esporta; Controlla; Nuovo dipendente; apertura della scheda. |
| Riepilogo | `/it/app/employees/info/{EMPLOYEE_ID}/summary` | Campi anagrafici e lavorativi; Ripristina; Salva modifiche; azioni account, invito, stato ed eliminazione. |
| Permessi e ruoli | `/it/app/employees/info/{EMPLOYEE_ID}/roles` | Gruppi, ruoli, Timbratura, Foglio Presenze, Gestione turni, Spese e Viaggi e relativi controlli. |
| Timbrature dipendenti | `/it/app/settings/timestamps/employees` | Collegamento osservato dalla pagina ruoli; configurazione effettiva non determinata. |
| Contratti e orari | `/it/app/employees/info/{EMPLOYEE_ID}/contracts` | Cerca, Nuovo, Visualizza, Modifica, Elimina e ordinamento. |
| Ratei di maturazione | `/it/app/employees/info/{EMPLOYEE_ID}/maturations` | Tab Valide, Storico e Tutte; azione Nuovo. |
| Bilancio | `/it/app/employees/info/{EMPLOYEE_ID}/counters` | Selezione anno, Espandi/Comprimi, Esporta e Correggi. |
| Buste paga | `/it/app/employees/info/{EMPLOYEE_ID}/payrolls` | Navigazione per anno e consultazione stato/metadati. Il campione non conteneva buste paga. |
| Documenti | `/it/app/employees/info/{EMPLOYEE_ID}/documents/list` | Caricati/In attesa, ricerca, Esporta, Carica documento, filtri, ordinamento, Scarica, Modifica ed Elimina. |

## Lista dipendenti

Sono state osservate le modalità **Crea manualmente** e **Crea caricando una
busta paga**. Le informazioni visibili comprendevano nome, e-mail, codice fiscale,
mansione, gruppo/reparto, matricola, contratto, periodo contrattuale, modello
orario, stato account e stato dipendente.

Stati account osservati: `Collegato`, `Invitato`, `Non collegato`. Stati
contrattuali osservati: `Contratto determinato`, `Contratto indeterminato`,
`Nessun contratto attivo`. I conteggi devono essere letti dinamicamente e non
sono mai valori di configurazione o fixture.

## Riepilogo, ruoli e timbrature

Nel riepilogo sono stati osservati Nome, Cognome, Numero di Matricola, Codice
fiscale, Data di nascita, IBAN, Mansione, Numero di telefono, Email aziendale,
Indirizzo, Luogo di lavoro, Note, Stato e Avatar. Nome e Cognome risultavano
obbligatori.

Le azioni osservate erano Ripristina, Salva modifiche, Collega dipendente, Invita
di nuovo, Annulla invito, Scollega dipendente, Disattiva, Attiva ed Elimina. La
presenza del controllo non dimostra permesso, semantica completa o successo
dell'azione: nessuna di queste write è stata eseguita.

La pagina ruoli poteva non essere disponibile per dipendenti non collegati. Il
link alle impostazioni timbrature è stato osservato, ma la configurazione reale e
le sue validazioni restano `NEEDS_DISCOVERY`.

## Contratti, maturazioni e bilancio

Nel dettaglio contratto sono stati osservati Orario contratto, Flessibilità,
Indeterminato, Data inizio/fine, Livello CCNL, Regime orario, Descrizione, Tipo,
Stato e Periodo.

Il bilancio mostrava categorie quali Ferie, ROL, Permessi ex festività, Banca ore,
Banca ore festiva, Permessi e Flessibilità, con valori Residuo anno precedente,
Residuo mese precedente, Maturato, Goduto, Correzioni e Residuo mese corrente.
La possibilità di vedere un controllo **Correggi** non autorizza né verifica la
write corrispondente.

## Buste paga e documenti

Per le buste paga sono accertate soltanto navigazione per anno e consultazione di
stato/metadati. Non si assumono upload, eliminazione o pubblicazione.

Per i documenti erano visibili Titolo, Tipologia, Scadenza, Caricato il, Caricato
da e Azioni. Le categorie osservate includevano CV, documento d'identità, patente,
passaporto e tessera sanitaria. Non sono stati verificati limiti dimensionali,
formati, versionamento, firma, sostituzione, notifiche, antivirus o comportamento
effettivo dell'upload. Il contenuto dei documenti non è parte della baseline e non
deve essere pubblicato su Discord o inviato a OpenAI.

## Elementi ancora da scoprire

Restano `NEEDS_DISCOVERY`:

- semantica del pulsante **Controlla**;
- generazione esplicita di link invito;
- configurazione effettiva delle timbrature;
- upload e gestione delle buste paga;
- azioni massive, filtri avanzati, importazione, duplicazione e archiviazione;
- limiti e validazioni live di upload/download documenti;
- presenza e stabilità di `data-testid` e attributi `data-*` nel DOM reale.

Una futura ricognizione deve essere mirata, autorizzata e non mutativa: con il bot
fermo e tutte le feature flag write disabilitate, può validare route, struttura
DOM, controlli e selettori senza premere Salva/Conferma, caricare file o scaricare
documenti.

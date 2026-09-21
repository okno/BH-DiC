# Planner HR read-only

BH-DiC non consente al modello di pilotare Playwright, generare URL o selettori, leggere risultati
DiC o vedere identità dei dipendenti. Il comportamento “come un HR” è realizzato come un ciclo
locale, bounded e verificabile:

1. normalizzazione e risoluzione locale di date, Employee ID e nomi;
2. piano composto esclusivamente da Function ID read autorizzati;
3. risoluzione RBAC prima di ogni risorsa e di ogni target;
4. navigazione seriale sulle route tipizzate del registro DiC;
5. verifica di Employee ID, anno, tenant, cardinalità e completezza a ogni join;
6. risposta o allegato solo dopo il completamento di tutti gli step dichiarati.

`QueryPlanTracker` impone ordine, numero massimo di letture e limite record. Una risposta non è
marcata `COMPLETE` se manca uno step o se il risultato supera la sorgente verificata. Non esistono
loop infiniti: il budget termina con un codice sicuro e correlabile, senza inventare dati.

## Piani composti disponibili

- contratti in scadenza più presenza busta paga per periodo e gruppo;
- tabella completa organico/ID/contratto/netto, con periodo esplicito oppure ultima busta paga con
  netto disponibile;
- dossier HR completo del singolo dipendente sulle otto superfici per-target registrate, con
  preflight all-or-nothing di ruolo ed entitlement prima di qualunque lettura di dettaglio;
- ricerca collettiva dei dipendenti con busta paga in un mese;
- risoluzione nome/cognome/fuzzy con conferma actor-bound prima di leggere dati individuali.

Il piano tabellare legge realmente Elenco, Contratti e Buste paga per ciascun Employee ID. I
record senza contratto o netto restano `N/D`; non vengono stimati.

Formulazioni colloquiali ad alta confidenza — per esempio `Nora può timbrare?`, `quante ferie
restano a Nora?`, `documenti in scadenza per Nora`, `avvisi non letti` e `busta paga più recente
di Nora` — sono normalizzate localmente. Nome e domanda non vengono inviati al provider; i termini
ambigui come `permessi` vengono distinti fra saldo ferie/permessi e autorizzazioni del portale dal
contesto chiuso della frase.

## Routing del provider

Le richieste già riconosciute localmente non chiamano il provider. Per le restanti, il provider
riceve soltanto termini canonici senza nomi, ID o testo DiC. Il filtro di famiglia espone al
massimo tre Function ID read pertinenti (per esempio solo payroll o solo documenti), mai l'intero
catalogo. Un `tool_use_failed` Groq su una famiglia chiusa può degradare a un chiarimento locale;
non produce una write e non interrompe con un generico errore AI quando target o periodo possono
essere richiesti in sicurezza.

## Superfici DiC e discovery

Le superfici registrate sono elenco, riepilogo, ruoli, timbrature, contratti, maturazioni, saldi,
payroll, documenti e notifiche top-bar. Una superficie sconosciuta non viene esplorata liberamente
durante una richiesta utente: un crawler adattivo con sessione HR avrebbe accesso eccessivo e
potrebbe seguire azioni mutative.

La discovery ammessa è amministrativa, read-only e separata: stessa origine HTTPS, sole route
GET/lettura, nessun submit, nessun download, nessun payload o PII nei log. Una route osservata
diventa utilizzabile solo dopo ingresso nel registro tipizzato, schema/fingerprint, parser,
policy, test sintetici e verifica read-only autorizzata. Il modello non partecipa alla discovery.

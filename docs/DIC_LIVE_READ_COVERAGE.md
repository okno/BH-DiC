# DiC read capability contract

Questo documento elenca i percorsi read implementati. `LIVE_READ_VERIFIED` nella matrice indica
che il contratto ha avuto evidenza di compatibilità, non che un ambiente sia oggi connesso o che
la sua revisione sia quella corrente. Ogni rollout deve ripetere privatamente il gate sulla
revisione esatta e non deve pubblicarne tenant, conteggi o stato runtime.

| Superficie | Sorgente first-party | Garanzia applicativa |
| --- | --- | --- |
| Elenco dipendenti | pagina dipendenti ed elenco paginato | schema chiuso, totale stabile, deduplica ID e pagine bounded |
| Riepilogo | summary dipendente | risorsa singola, ID e tenant verificati |
| Ruoli | pagina ruoli | risorsa singola tipizzata |
| Timbratura | impostazioni timestamps | target tenant-bound |
| Contratti | risposta contratti | paginazione, ID dipendente e date validate |
| Maturazioni | risposta maturazioni | paginazione e riferimenti counter bounded |
| Bilanci | counters e attendance/balance | anno, mese, counter e limite righe |
| Payroll | pagina payroll dipendente | ID, anno, mese, netto e metadati allegato tipizzati |
| Documenti | pagina documenti | paginazione, categoria e metadati tipizzati |
| Notifiche | top bar DIC | schema e paginazione bounded, stato letto/non letto tipizzato |

## Completezza

Il parser rifiuta JSON con chiavi duplicate o numeri non finiti. Origine, metodo, path, query,
MIME, dimensione, paginazione e identità sono verificati per la singola superficie. Un empty state
valido non equivale a un errore; non dimostra però che ogni dipendente possieda record nel modulo.

Le interrogazioni collettive attraversano serialmente gli ID entro limiti espliciti e verificano
che ogni record appartenga al target richiesto. I piani composti dichiarano i passi prima
dell'esecuzione e producono una risposta soltanto se l'evidenza del piano è completa. Un URL PDF
firmato è temporaneo, non viene inviato al provider e non è persistito dal bot.

## Superfici condizionali

Workplace e modello orario sono campi/lookup dipendenti dal tenant. Spese, viaggi, turni e fogli
presenze non sono dichiarati disponibili finché la normale UI autorizzata non espone una sorgente
registrata e testata. L'assenza nel bot non implica l'assenza nel prodotto DIC.

## Gate privato richiesto

Il gate di un ambiente deve verificare sessione e tenant, quindi esercitare in sola lettura una
risorsa rappresentativa per ogni superficie abilitata. Il report deve contenere soltanto stati e
conteggi sanitizzati e deve rimanere nel change ticket privato. Nessuna write o download documento
è necessario per validare queste read.

# Delivery boundary

Questo file non è un verbale di produzione. La repository pubblica descrive soltanto capacità,
contratti e procedure verificabili della codebase; non pubblica host, configurazioni, revisioni
installate, conteggi del tenant, stato di servizi o risultati di gate live.

L'implementazione corrente separa:

1. autorizzazione Discord e policy applicativa;
2. pianificazione HR deterministica e actor-bound;
3. adapter DIC con route e schemi first-party allowlisted;
4. presentazione Discord e telemetria minimizzata.

Le read supportate e i relativi limiti sono in [Feature matrix](FEATURE_MATRIX.md) e
[DiC read coverage](DIC_LIVE_READ_COVERAGE.md). Il comportamento multi-step e il divieto di
navigazione arbitraria pilotata dal modello sono in [Query planner](QUERY_PLANNER.md).

Ogni rollout deve essere associato privatamente alla revisione esatta, ai gate locali e live,
all'esito del solo servizio bot e a un round-trip Discord autorizzato. Nessun documento pubblico
può attestare lo stato corrente di un ambiente.

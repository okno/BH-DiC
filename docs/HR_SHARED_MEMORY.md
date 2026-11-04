# Memoria HR condivisa

`bh_dic.ai.hr_memory` carica un playbook comune a Codex, Ollama, LM Studio e agli altri
provider. La memoria descrive regole linguistiche e operative generali; non è uno store di
conversazioni e non concede autorizzazioni, tool o accesso al browser.

## Formato

Il file configurato può essere YAML oppure Markdown e deve contenere una versione e le tre
sezioni `rules`, `capabilities` e `terminology`. Ogni sezione è una mappa `chiave: testo`; le
chiavi sono identificatori ASCII stabili. Un esempio sanificato è disponibile in
`config/hr_memory.example.yaml`.

Formato Markdown equivalente:

```markdown
# HR shared memory
Version: 1

## Rules
- evidence_first: Usa soltanto risultati restituiti dall'esecutore validato.

## Capabilities
- employee_lookup: Pianifica una ricerca, lasciando risoluzione e policy all'applicazione.

## Terminology
- riferimento: Riferimento opaco risolto localmente.
```

Il loader applica UTF-8, normalizzazione NFKC, limiti di byte/righe, schema chiuso e controlli
fail-closed per materiale che assomiglia a PII o segreti. Lo snapshot è immutabile, ordinato e
identificato dall'hash SHA-256 del contenuto semantico canonico; YAML e Markdown equivalenti
producono lo stesso hash e lo stesso testo per il modello.

## Ciclo di vita e confini

`await HrMemoryStore.refresh()` prepara il candidato fuori dal loop e lo pubblica atomicamente
solo dopo la validazione completa. Se il refresh fallisce, rimane disponibile l'ultimo snapshot
valido. Il servizio espone solamente `snapshot` e `render()`: non esiste un'API che permetta alla
chat o al modello di scrivere la memoria. Gli aggiornamenti devono essere effettuati da un
operatore sul file e distribuiti con sostituzione atomica.

Non inserire mai nomi, ID dipendente, contatti, documenti, importi individuali, cookie, token,
password, URL del tenant o istruzioni specifiche di una persona. Il contesto conversazionale per
utente resta process-local su Ubiquiti, contiene soltanto riferimenti opachi con TTL ed è separato
da questo playbook. La memoria va anteposta alla richiesta minimizzata nello stesso rendering per
tutti i provider; catalogo funzioni, RBAC, approvazioni ed esecutore deterministico restano sempre
autoritativi.

# Backup e restore

Il runtime server è preparato, ma backup e restore drill non sono ancora stati eseguiti.
Provare questa procedura con dati sintetici prima dell'uso operativo.

## Contenuto

Il backup predefinito deve includere:

- database consistente e metadati delle migrazioni;
- audit append-only e checkpoint/verifica associati;
- configurazione **non segreta**, policy ed esempi di deployment;
- manifest con versione, timestamp, hash e schema;
- log solo con opzione esplicita e retention approvata.

Deve escludere `.env`, password, token, API key, TOTP, cookie, session state DIC, upload,
quarantena, trace e screenshot. Una sessione o un file sensibile può essere incluso soltanto con
autorizzazione esplicita, cifratura e chiave conservata separatamente.

## Backup

```bash
cd /opt/bh-dic
./scripts/status.sh
./scripts/audit-verify.sh
./scripts/backup.sh
```

Prima di update o restore fermare il bot. Lo script deve usare un metodo consistente (SQLite
backup API/transaction oppure dump PostgreSQL), `umask 077`, directory `var/backups/`, nome non
ambiguo e checksum. Non copiare semplicemente un database SQLite attivo ignorando WAL/SHM.

L'implementazione corrente supporta soltanto SQLite locale. Produce un archivio `0600` in
`var/backups/`, include database, esito audit, safe summary, config/policy non segrete, manifest e
checksum. `--include-logs` è opt-in. L'archivio non è cifrato dall'applicazione: prima di copiarlo
off-host applicare cifratura approvata con chiave separata.

Se la verifica audit fallisce, lo script conserva l'archivio con warning per non perdere
l'evidenza. Tale archivio non è ripristinabile finché l'incidente non è risolto; controllare
`audit-verification.json` senza modificarlo.

Verifiche minime:

```bash
ls -ld var/backups
sha256sum var/backups/<BACKUP_FILE>
./scripts/audit-verify.sh
```

Non incollare il listing se i nomi contengono informazioni sensibili. Copiare off-host soltanto
su storage cifrato e con accesso minimo.

## Restore

Il restore è distruttivo per lo stato corrente: richiede approvazione, bot fermo e un backup
preventivo separato.

```bash
cd /opt/bh-dic
./scripts/stop.sh
./scripts/backup.sh
./scripts/restore.sh var/backups/<BACKUP_FILE>.tar.gz --confirm RESTORE
./scripts/audit-verify.sh
.venv/bin/python -m alembic -c migrations/alembic.ini upgrade head
./scripts/doctor.sh
./scripts/status.sh
```

Lo script rifiuta path fuori `var/backups/`, link/special member, traversal, file inattesi,
dimensioni e checksum invalidi; estrae prima in una directory temporanea protetta, verifica audit,
crea un backup obbligatorio del DB corrente, sostituisce il DB e applica migrazioni. Ripristina
eventuali file policy/config inclusi, ma non `.env`, log, chiavi, sessioni o upload.

Il risultato atteso è `stopped`. L'avvio è una decisione successiva e separata.

## Test periodico e retention

- eseguire restore drill su host isolato e dati sintetici;
- verificare query DB, migrazioni, catena audit e permessi;
- registrare RPO/RTO e hash senza dati personali;
- applicare retention con cancellazione controllata e auditata;
- conservare almeno una copia verificata separata dal server;
- testare il ripristino dopo ogni modifica di schema/script.

La rotazione della chiave HMAC richiede checkpoint documentato; la perdita delle chiavi di
cifratura può rendere irrecuperabili payload/sessioni. Vedere [Audit](AUDIT.md) e
[Operations](OPERATIONS.md).

# Configurazione Discord

Questa procedura prepara un'applicazione Discord limitata al guild e al canale allowlistati nella
configurazione locale. La modalità predefinita è slash-only; la modalità opzionale `channel`
legge i messaggi nel solo canale configurato, ignora quelli non HR e separa richieste operative
DIC da orientamento HR generale. I
relativi ID non sono conservati nella repository e devono essere copiati dal client Discord: non
ricavarli dai nomi e non inventarli.

> Stato al 17 agosto 2026: installazione guild-scoped e registrazione hanno evidenza storica; il
> gateway resta separato dal login DIC e può rispondere anche con DIC `DEGRADED`. Il primo smoke è
> stato negato dal gate RBAC prima del dispatch. Il gate applicativo 0.3.0 ha verificato la
> classificazione `PUBLIC`/non-ephemeral e `SENSITIVE`/ephemeral, ma non il trasporto Discord. Il
> servizio è `active/running`, con gateway `discord_ready`, e lo smoke slash resta `PENDING`.
> Correggere i ruoli senza ampliare guild o
> canale. Le istruzioni seguono la
> documentazione Discord ufficiale
> per [creare l'app e il bot](https://docs.discord.com/developers/quick-start/getting-started),
> [OAuth2](https://docs.discord.com/developers/topics/oauth2) e [application
> commands](https://docs.discord.com/developers/interactions/application-commands).

## 1. Creare applicazione e bot

1. Nel [Discord Developer Portal](https://discord.com/developers/applications) creare una nuova
   applicazione con nome riconoscibile e ownership aziendale.
2. In **General Information** copiare l'**Application ID**: diventerà
   `DISCORD_APPLICATION_ID`.
3. In **Bot** creare il bot, generare/reset il token e trasferirlo una sola volta tramite secret
   manager. Il token diventerà `DISCORD_BOT_TOKEN`; non incollarlo in Git, shell history, ticket,
   screenshot o log.
4. Se l'app deve restare interna, disabilitare **Public Bot**. Lasciare **Requires OAuth2 Code
   Grant** disabilitato: BH-DiC non implementa un authorization-code flow utente.
5. Lasciare disabilitati **Presence Intent** e **Server Members Intent**. Con
   `DISCORD_INTERACTION_MODE=slash` lasciare disabilitato anche **Message Content Intent**. Con
   `channel` o `mention` abilitarlo esplicitamente nella pagina **Bot**: senza questo flag Discord
   chiude il gateway quando l'app richiede l'intent privilegiato. Non serve un Interactions
   Endpoint URL perché le interazioni arrivano tramite gateway.

Riferimento ufficiale per intent privilegiati e abilitazione nel portal: [Gateway
Intents](https://docs.discord.com/developers/events/gateway#privileged-intents).

## 2. Copiare Guild, Channel e Role ID

Nel client Discord desktop/web:

1. aprire **User Settings → Advanced** e abilitare **Developer Mode**;
2. fare clic destro sul guild autorizzato e scegliere **Copy Server ID**; usare quel numero come
   `DISCORD_GUILD_ID`;
3. fare clic destro sul canale allowlistato e scegliere **Copy Channel ID**; usare quel numero come
   `DISCORD_CHANNEL_ID`;
4. copiare allo stesso modo gli ID dei ruoli approvati, senza usare nomi o menzioni.

Discord documenta il flusso **Developer Mode → Copy ID** nella guida [Where can I find my User,
Server or Message ID?](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID).
Per i Role ID vedere anche il tutorial Discord [Using community
invites](https://docs.discord.com/developers/tutorials/using-community-invites).

Configurazione minima, solo sul server:

```dotenv
DISCORD_BOT_TOKEN=<SEGRETO_LOCALE>
DISCORD_APPLICATION_ID=<APPLICATION_ID>
DISCORD_GUILD_ID=<DISCORD_GUILD_ID>
DISCORD_CHANNEL_ID=<DISCORD_CHANNEL_ID>
DISCORD_INTERACTION_MODE=slash
DISCORD_ALLOW_DMS=false
```

Proteggere `.env` con modo `0600`. Token, chiavi e Guild/Channel/Role ID operativi restano nella
configurazione locale e non devono essere copiati nella repository pubblica.

## 3. Install URL least privilege

In **Installation** abilitare soltanto **Guild Install**. Per l'installazione usare gli scope:

- `applications.commands`;
- `bot`.

Permessi bot minimi richiesti dall'implementazione slash attuale:

- **View Channel** (`1024`);
- **Send Messages** (`2048`);
- **Embed Links** (`16384`).

La somma è `19456`. Dopo aver sostituito Application ID e Guild ID con i valori locali, l'URL
guild-locked è:

```text
https://discord.com/oauth2/authorize?client_id=<DISCORD_APPLICATION_ID>&scope=applications.commands%20bot&permissions=19456&guild_id=<DISCORD_GUILD_ID>&disable_guild_select=true&integration_type=0
```

Verificare il riepilogo del portal prima di autorizzare. Installare esclusivamente nel guild il cui
ID coincide con `DISCORD_GUILD_ID` nella configurazione locale. Riferimento: [Discord permissions e
permission bitfields](https://docs.discord.com/developers/topics/permissions).

Per `DISCORD_INTERACTION_MODE=channel` o `mention` aggiungere **Read Message History** (`65536`),
per un totale `84992`: è necessario per rispondere al messaggio originale. Se il bot deve inviare
PDF/DOCX/XLSX aggiungere **Attach Files** (`32768`), per un totale `117760`. Non concedere
**Administrator**, Manage Guild, Manage Roles o accesso a canali non necessari. Il bot slash non
richiede Read Message History né Attach Files. Gli operatori umani che
usano `/bh upload` devono invece poter allegare file nel canale; ciò non richiede il permesso
Attach Files sul ruolo del bot. Nel canale allowlistato, gli utenti autorizzati devono poter usare
gli application commands.

## 4. Ruoli RBAC applicativi

Mappare gli ID dei ruoli Discord già approvati:

```dotenv
DISCORD_READONLY_ROLE_IDS=<ID_LIST>
DISCORD_HR_READ_ROLE_IDS=<ID_LIST>
DISCORD_BALANCE_ROLE_IDS=<ID_LIST>
DISCORD_HR_WRITE_ROLE_IDS=<ID_LIST>
DISCORD_IAM_ROLE_IDS=<ID_LIST>
DISCORD_DOCUMENT_ROLE_IDS=<ID_LIST>
DISCORD_APPROVER_ROLE_IDS=<ID_LIST>
DISCORD_SECURITY_ADMIN_ROLE_IDS=<ID_LIST>
DISCORD_SYSTEM_ADMIN_ROLE_IDS=<ID_LIST>
```

Usare interi positivi separati da virgole. La presenza nel canale non autorizza operazioni HR:
scope e ruoli vengono ricontrollati dall'applicazione. Applicare least privilege e separazione dei
compiti; il richiedente non può approvare la propria azione e A2 deve essere distinto da A1.

| Richiesta | Ruolo minimo | Visibilità risposta |
|---|---|---|
| totale organico, totale attivi/disattivati | `READ_ONLY` | aggregato finale pubblico nel canale allowlistato |
| `/bh status`, `/bh health`, aiuto | `READ_ONLY` | ephemeral, perché include stato operativo |
| elenco/ricerca dipendente | `HR_READ` | ephemeral |
| contratti o scadenze del prossimo mese | `HR_READ` | ephemeral |

Le due righe `HR_READ` possono includere il nome visualizzato in chiaro, necessario a rendere
utile l'elenco al Senior HR. Il runtime lo apre da `SecretStr` soltanto nel risultato
`SENSITIVE`/ephemeral; non compare nell'acknowledgement, negli aggregati pubblici o nei canali
tecnici.

### Consentire i comandi a tutti i membri del solo canale

Discord assegna al ruolo predefinito `@everyone` lo stesso snowflake del guild. Se l'obiettivo è
permettere a ogni membro che può vedere il canale allowlistato di usare soltanto comandi
informativi e aggregati, impostare localmente:

```dotenv
DISCORD_READONLY_ROLE_IDS=<DISCORD_GUILD_ID>
```

Il gate continua a negare DM, thread, bot, webhook, altro guild e altro canale. Questa mappa non
abilita letture HR individuali. Per `/bh ask` con funzioni HR ordinarie creare invece un ruolo
umano dedicato, per esempio `BH-DiC HR Read`, assegnarlo a tutti e soli i membri ammessi e copiarne
l'ID in `DISCORD_HR_READ_ROLE_IDS`. Aggiungere lo stesso ID a
`DISCORD_BALANCE_ROLE_IDS` soltanto se quelle persone devono vedere anche i bilanci: la mappa
balance è un entitlement aggiuntivo e da sola non supera il gate.

Non mappare `@everyone` a `HR_WRITE`, IAM, document operator, approver, security admin o system
admin. Mappare `@everyone` a `HR_READ` è tecnicamente possibile, ma estende automaticamente i dati
HR a ogni membro presente o futuro che ottenga accesso al canale ed è quindi sconsigliato.

Il risultato pubblico è limitato al numero aggregato e ai contatori tecnici associati alla
richiesta. Discord non consente di trasformare in pubblica una risposta già deferita come privata:
BH-DiC chiude quindi l'acknowledgement ephemeral e invia un follow-up pubblico soltanto quando il
runtime ha classificato esplicitamente il risultato `PUBLIC_AGGREGATE`. Nessun dettaglio HR usa
questo percorso.

Nel client Discord configurare inoltre i due livelli nativi:

1. in **Server Settings → Integrations → BH-DiC → Manage**, consentire il ruolo umano (oppure
   `@everyone` per il profilo strettamente `READ_ONLY`) e il solo canale allowlistato; negare
   **All Channels** se si vuole nascondere i comandi altrove e mantenere i sottocomandi synced;
2. in **Edit Channel → Permissions**, concedere agli utenti **View Channel** e
   **Use Application Commands**; concedere **Attach Files** soltanto a chi usa `/bh upload`;
3. al ruolo del bot concedere **View Channel**, **Send Messages** ed **Embed Links**; aggiungere
   **Read Message History** esclusivamente per `channel`/`mention` e **Attach Files** se sono
   abilitati gli export.

### Conversazione HR pubblica nel canale

Per consentire a tutti i membri che vedono il canale di parlare con il responder generale:

```dotenv
DISCORD_INTERACTION_MODE=channel
DISCORD_READONLY_ROLE_IDS=<DISCORD_GUILD_ID>
```

Il nome del canale non viene usato dal runtime: `DISCORD_CHANNEL_ID` deve essere l'ID esatto del
canale desiderato. Il responder considera soltanto il messaggio corrente, non conserva cronologia,
non ha tool e non chiama DIC. Redige riferimenti Discord, identificativi dipendente, contatti,
segreti, importi e casi personali riconoscibili; una richiesta individuale viene rinviata a
`/bh ask` soltanto se l'utente è autorizzato, altrimenti a HR. Le risposte sono pubbliche e tutti i
membri con accesso al canale possono leggerle.

Non mappare `@everyone` a `HR_READ`: gli slash command che mostrano persone, contratti, saldi o
documenti devono restare limitati a un ruolo umano dedicato. Per pubblicare tali risultati nel
canale privato configurare esplicitamente anche:

```dotenv
DISCORD_PUBLISH_SENSITIVE_CHANNEL_RESPONSES=true
DISCORD_HR_READ_ROLE_IDS=<ID_RUOLO_UMANO_HR>
```

Il ruolo deve essere assegnabile a persone e non deve essere un ruolo Discord `managed` creato
per il bot o per un'integrazione. Le richieste operative riconosciute usano il medesimo coordinator
di `/bh`; lista, ricerca, export e attiva/disattiva non bypassano mai RBAC, flag o conferme.

Ad ogni nuovo avvio del processo il bot invia nel solo canale allowlistato la notifica `BOT HR
Bitcoin Hotel Online!` con stato non sensibile di adapter, browser, tenant DIC, kill switch e
provider/modello AI. Se DIC non è disponibile, la notifica propone il ripristino controllato da
parte di un amministratore autorizzato: il gateway non invia automaticamente credenziali né tenta
un login autonomo.

Una modifica ai ruoli assegnati nel client Discord ha effetto dalla richiesta successiva. Una
modifica alle mappe nel `.env` richiede il riavvio del servizio, ma non una nuova installazione né
una nuova registrazione degli slash command.

Prima della produzione verificare con account sintetici almeno: nessun ruolo, sola lettura,
approvatore, ruolo errato, DM, altro canale, thread, webhook e bot.

## 5. Registrare i comandi nel solo guild

La configurazione completa deve essere valida, il bot deve essere fermo e le write devono restare
disabilitate:

```bash
cd /opt/bh-dic
./scripts/doctor.sh
./scripts/status.sh
./scripts/register-commands.sh
./scripts/status.sh
```

`register-commands.sh` registra il gruppo `/bh` soltanto in `DISCORD_GUILD_ID` e chiude la sessione
senza avviare il gateway. Non usare una registrazione globale. La propagazione guild-scoped è
adatta alla verifica iniziale; controllare nel canale allowlistato che `/bh` e i relativi sottocomandi
compaiano, senza eseguire una richiesta DIC live.

La superficie comprende i comandi informativi/read e le route operatore previste dal catalogo.
La visibilità di un comando non prova l'autorizzazione né l'implementazione live dell'azione.
`EMP-INVITE-001`, `EMP-DOC-005`, `EMP-DOC-003` e `EMP-CONTRACT-003` falliscono chiuso come
`NOT_AVAILABLE` nel percorso live; l'export PDF/DOCX/XLSX è locale e in memoria ma usa dati live
ancora `NEEDS_VALIDATION`; `EMP-CREATE-001` è live soltanto per il subset
verificabile documentato nella [Feature matrix](FEATURE_MATRIX.md). Tutte le write restano
`DISABLED_BY_POLICY`.

Il sottocomando guild-scoped `/bh dic reconnect` compare dopo una nuova registrazione. È
utilizzabile soltanto da un ruolo mappato in `DISCORD_SECURITY_ADMIN_ROLE_IDS` o
`DISCORD_SYSTEM_ADMIN_ROLE_IDS` e con `ENABLE_DIC_RECONNECT=true`; la risposta è sempre ephemeral.
Non chiede password, OTP o cookie su Discord: usa la configurazione server protetta.

Nel flusso `/bh upload`, il pending conserva solo l'identificatore opaco. Path locale e SHA-256
non sono mostrati al provider o su Discord e non entrano in eventi o log. Lo SHA-256 è visibile
esclusivamente all'operatore locale tramite i metadati file.

## 6. Gate prima dell'avvio

- Guild ID e Channel ID copiati dal client e presenti soltanto nella configurazione locale;
- DM disabilitati; altro guild/canale, thread, webhook e bot rifiutati;
- permessi OAuth pari al minimo revisionato, senza privilegi amministrativi;
- ruoli testati con identità sintetiche e casi deny;
- token assente da `git diff`, log e process list;
- rate limit attivo;
- `MODEL_STORE=false`;
- `ENABLE_WRITE_ACTIONS=false`, `ENABLE_LIVE_WRITE_TESTS=false` e tutti i flag specifici false;
- bot ancora `stopped` fino all'autorizzazione di avvio.

## Rotazione o compromissione

1. fermare il bot con il gestore di processo scelto;
2. rigenerare/revocare il token nel portal;
3. aggiornare `.env` e ripristinare modo `0600`;
4. controllare log e audit per uso anomalo senza esportare dati sensibili;
5. rieseguire doctor e registrazione se necessario;
6. avviare soltanto dopo approvazione.

Vedere [Configuration](CONFIGURATION.md), [Installazione e runbook](INSTALLATION.md),
[Operations](OPERATIONS.md) e [Troubleshooting](TROUBLESHOOTING.md).

# Configurazione Discord

Questa procedura prepara un'applicazione Discord slash-only limitata al guild
`1303955635984924722` e al canale `#mng-ai`. Il Channel ID di `#mng-ai` non è noto nella
repository e deve essere copiato dal client Discord: non ricavarlo dal nome e non inventarlo.

> Stato: applicazione, token, installazione nel guild e registrazione dei comandi live non sono
> stati verificati in questa consegna. Le istruzioni seguono la documentazione Discord ufficiale
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
5. Lasciare disabilitati **Presence Intent**, **Server Members Intent** e **Message Content
   Intent**. Il runtime slash usa soltanto l'intent non privilegiato Guilds. Non serve un
   Interactions Endpoint URL perché le interazioni arrivano tramite gateway.

Riferimento ufficiale per intent privilegiati e abilitazione nel portal: [Gateway
Intents](https://docs.discord.com/developers/events/gateway#privileged-intents).

## 2. Copiare Guild, Channel e Role ID

Nel client Discord desktop/web:

1. aprire **User Settings → Advanced** e abilitare **Developer Mode**;
2. fare clic destro sul guild autorizzato e scegliere **Copy Server ID**; deve risultare
   `1303955635984924722`;
3. fare clic destro sul canale **#mng-ai** e scegliere **Copy Channel ID**; usare quel numero come
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
DISCORD_GUILD_ID=1303955635984924722
DISCORD_CHANNEL_ID=<CHANNEL_ID_COPIATO_DA_MNG_AI>
DISCORD_INTERACTION_MODE=slash
DISCORD_ALLOW_DMS=false
```

Proteggere `.env` con modo `0600`. È corretto documentare il Guild ID approvato; token, chiavi e
Channel/Role ID operativi restano nella configurazione locale.

## 3. Install URL least privilege

In **Installation** abilitare soltanto **Guild Install**. Per l'installazione usare gli scope:

- `applications.commands`;
- `bot`.

Permessi bot minimi richiesti dall'implementazione slash attuale:

- **View Channel** (`1024`);
- **Send Messages** (`2048`);
- **Embed Links** (`16384`).

La somma è `19456`. Dopo aver sostituito soltanto l'Application ID, l'URL guild-locked è:

```text
https://discord.com/oauth2/authorize?client_id=<DISCORD_APPLICATION_ID>&scope=applications.commands%20bot&permissions=19456&guild_id=1303955635984924722&disable_guild_select=true&integration_type=0
```

Verificare il riepilogo del portal prima di autorizzare. Installare esclusivamente nel guild
mostrato come `1303955635984924722`. Riferimento: [Discord permissions e permission
bitfields](https://docs.discord.com/developers/topics/permissions).

Non concedere **Administrator**, Manage Guild, Manage Roles o accesso a canali non necessari.
L'attuale bot slash non richiede Read Message History né Attach Files. Gli operatori umani che
usano `/bh upload` devono invece poter allegare file nel canale; ciò non richiede il permesso
Attach Files sul ruolo del bot. Nel canale `#mng-ai`, gli utenti autorizzati devono poter usare gli
application commands.

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
adatta alla verifica iniziale; controllare in `#mng-ai` che `/bh` e i relativi sottocomandi
compaiano, senza eseguire una richiesta DIC live.

La superficie comprende i comandi informativi/read e le route operatore previste dal catalogo.
La visibilità di un comando non prova l'autorizzazione né l'implementazione live dell'azione.
`EMP-INVITE-001`, `EMP-DOC-005`, `EMP-EXPORT-001`, `EMP-DOC-003` e `EMP-CONTRACT-003` falliscono
chiuso come `NOT_AVAILABLE` nel percorso live; `EMP-CREATE-001` è live soltanto per il subset
verificabile documentato nella [Feature matrix](FEATURE_MATRIX.md). Tutte le write restano
`DISABLED_BY_POLICY`.

Nel flusso `/bh upload`, il pending conserva solo l'identificatore opaco. Path locale e SHA-256
non sono mostrati al provider o su Discord e non entrano in eventi o log. Lo SHA-256 è visibile
esclusivamente all'operatore locale tramite i metadati file.

## 6. Gate prima dell'avvio

- `DISCORD_GUILD_ID=1303955635984924722` e Channel ID copiato da `#mng-ai`;
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

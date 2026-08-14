# Configurazione Discord

Questa procedura prepara un'applicazione Discord limitata a un solo guild e canale. Il bot
BH-DiC **non è stato avviato né registrato nel guild live** in questa consegna.

## 1. Applicazione e bot

1. Nel Discord Developer Portal creare una nuova applicazione con un nome riconoscibile.
2. Nella sezione Bot creare il bot e disabilitare la pubblicazione/installazione non necessaria.
3. Generare il token una sola volta e trasferirlo con un canale segreto; non incollarlo in Git,
   shell history, ticket o log.
4. Annotare localmente Application ID, Guild ID e Channel ID abilitando Developer Mode nel
   client Discord e usando **Copy ID**.

Inserire solo in `.env` sul server:

```dotenv
DISCORD_BOT_TOKEN=<SECRET_LOCALE>
DISCORD_APPLICATION_ID=<APPLICATION_ID>
DISCORD_GUILD_ID=<GUILD_ID_AUTORIZZATO>
DISCORD_CHANNEL_ID=<CHANNEL_ID_AUTORIZZATO>
DISCORD_INTERACTION_MODE=slash
DISCORD_ALLOW_DMS=false
```

Il file deve essere `0600`. Non riportare ID reali nella documentazione o nelle fixture.

## 2. Installazione e permessi minimi

Creare un URL di installazione limitato al guild autorizzato con gli scope:

- `bot`;
- `applications.commands`.

Concedere soltanto i permessi necessari nel canale dedicato:

- View Channel;
- Send Messages;
- Embed Links;
- Read Message History;
- Attach Files solo se il flusso documenti è autorizzato.

Non concedere Administrator, Manage Guild, Manage Roles o accesso a canali non necessari. Le
operazioni HR sono autorizzate dall'RBAC applicativo, non dai soli permessi Discord.

La modalità `slash` usa soltanto l'intent Guilds. `mention` e `channel` richiedono anche Messages
e il privileged **Message Content Intent** sia nel portal sia nel bot; abilitarli solo dopo una
valutazione privacy. Le risposte sensibili non sono inviate in modalità messaggio perché non
possono essere ephemeral.

## 3. Ruoli applicativi

Mappare gli ID dei ruoli Discord già approvati alle variabili:

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

Usare liste separate da virgola. Applicare least privilege e separazione dei compiti: il
richiedente non può approvare la propria azione e A2 deve essere diverso da A1.

## 4. Registrazione slash command

Dopo aver validato configurazione e scope, ma prima di avviare il bot:

```bash
./scripts/doctor.sh
./scripts/register-commands.sh
```

Lo script deve registrare i comandi soltanto in `DISCORD_GUILD_ID`, mai globalmente. La superficie
implementata comprende `/bh ask`, `help`, `status`, `health`, `pending`, `approve`, `reject`,
`upload`, `employee`, `contracts`, `documents` e `balances`.

Verificare nel solo guild previsto che i comandi compaiano. Non eseguire una richiesta DIC live
come test di registrazione.

## 5. Gate prima dell'avvio

- guild e channel ID coincidono con quelli approvati;
- DMs disabilitati e thread/webhook/bot rifiutati;
- ruoli testati con account sintetici e casi deny;
- token non compare in `git diff`, log o process list;
- rate limit attivo;
- `ENABLE_WRITE_ACTIONS=false` e flag specifici false;
- `OPENAI_STORE=false`;
- bot ancora `stopped` fino all'autorizzazione di avvio.

## Rotazione o compromissione

1. fermare il bot;
2. rigenerare/revocare il token nel portal;
3. aggiornare `.env` e `chmod 600 .env`;
4. controllare log e audit per uso anomalo;
5. rieseguire doctor e registrazione se necessario;
6. avviare soltanto dopo approvazione.

Vedere [Configuration](CONFIGURATION.md), [Operations](OPERATIONS.md) e
[Troubleshooting](TROUBLESHOOTING.md).

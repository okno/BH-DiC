# Discord DM security model

DMs are disabled by default. Enabling them requires all of:

```dotenv
DISCORD_ALLOW_DMS=true
DISCORD_DM_AUTH_GUILD_ID=<same value as DISCORD_GUILD_ID>
DISCORD_DM_ALLOWED_ROLE_IDS=<explicit role allowlist>
DISCORD_SENSITIVE_DELIVERY_MODE=dm_or_ephemeral
```

For every DM the bot obtains the configured guild, calls `fetch_member` and derives current roles
and field entitlements from that fresh member. A missing guild/member, foreign user, bot user,
missing allowed role or empty logical-role mapping is denied before coordinator or DIC access.
The policy actor remains tenant- and HR-channel-anchored after this independent private-transport
authorization. No content is written to the denial log.

Sensitive channel results are sent by DM only after the normal policy decision. If Discord rejects
the DM, the bot posts only a non-sensitive instruction to use `/bh ask`, whose response is
ephemeral. The channel never receives the protected fields or attachment.

# Discord behavior

- Slash commands remain guild-scoped and deterministic.
- In `channel` mode every non-bot/non-webhook message in `DISCORD_CHANNEL_ID` enters the
  conversational pipeline.
- In `mention` mode only a mention or reply is accepted, and additional channels must be explicitly
  listed in `DISCORD_MENTION_CHANNEL_IDS`.
- DMs follow [the DM security model](DM_SECURITY_MODEL.md).
- Aggregate results may be public. PII, employee rows, payroll, document data and exports default
  to ephemeral or verified DM delivery.
- Message Content Intent is required for channel, mention and DM text. The bot needs View Channel,
  Send Messages, Read Message History, Embed Links and Attach Files when exports are enabled.

Administrative commands `/bh diagnostics`, `/bh coverage`, `/bh route-status` and
`/bh schema-status` require `SECURITY_ADMIN` or `SYSTEM_ADMIN`. `/bh dic reconnect` performs one
controlled credential submit only when the session is not already authenticated and the independent
feature flag is enabled.

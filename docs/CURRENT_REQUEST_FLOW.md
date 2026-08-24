# Current request flow

Baseline: `98b03932ca1bf548bff44a82a1fedd976e5603d0`.

## Slash command

1. Discord invokes a registered `/bh` subcommand.
2. The transport defers a response and builds a `DiscordActor` through
   `DiscordGate`.
3. Guild, configured channel, thread, bot/webhook and mapped-role checks run.
4. A deterministic subcommand calls the coordinator directly, or `/bh ask` calls
   `BHApplicationCoordinator.ask`.
5. `ask` normalizes the text, computes policy-visible Function IDs and first tries
   `parse_local_operational_intent`.
6. If the local parser has no match, minimized text and the visible closed tool
   catalog go to the intent provider.
7. Exactly one validated `IntentEnvelope` returns. The provider does not receive
   DiC data and does not navigate the browser.
8. The coordinator resolves a bounded employee query when applicable, evaluates
   policy and dispatches one read or prepares one write.
9. The DiC service invokes the adapter/page object. Audit and model-usage events
   are recorded without raw HR payloads.
10. The transport renders an embed, bounded text chunks and/or attachments. Slash
    responses marked sensitive are ephemeral unless explicitly configured
    otherwise.

## Configured channel mode

1. The bot receives guild messages only; bot/webhook/DM/thread events are dropped.
2. Messages outside the configured channel are dropped.
3. A message is accepted only when it mentions the bot, replies to the bot, or
   `is_general_hr_request` recognizes it. Other ordinary channel messages are
   dropped without application routing.
4. Gate and per-user/global rate limits run.
5. `is_operational_hr_request` selects one of two mutually exclusive paths:
   operational text goes to the coordinator; other text goes to a public HR model
   that cannot access DiC.
6. Coordinator results marked sensitive are refused in-channel unless the global
   sensitive-publication flag is enabled.

This transport pre-classification is the main reason a healthy DiC session can
coexist with “no operation was executed” for a natural-language request.

## Mention mode

1. Non-mention messages are dropped.
2. The mention is removed from the request.
3. The same `DiscordGate` still requires the single configured channel.
4. The coordinator runs directly; sensitive results are redirected to slash
   because a message reply is not ephemeral.

Therefore mention mode is not currently “mention in authorized channels.”

## Direct messages

DM delivery is not implemented. The bot ignores messages without a guild,
subscribes to no DM message events in interactive modes, and the gate always
returns `DM_NOT_ALLOWED` even when `allow_dms` is configured. The configuration
flag is a placeholder, not an authorization path.

## Current DiC read flow

1. The adapter uses a shared browser coordinator and authenticated context.
2. A page object navigates to a fixed DiC route and applies selectors or response
   capture.
3. Employee-list and payroll endpoints have dedicated network capture parsers.
4. Other resources are predominantly extracted from rendered page state.
5. Errors are mapped to authentication, transient, UI-contract and circuit-open
   classes, but all resources share the principal browser circuit key.
6. Only `list_all_employees` has a general page loop with stable-total,
   deduplication and exact completeness checks.

## Required target flow

The refactor will replace transport heuristics and the single envelope with:

`Discord input -> identity/delivery gate -> bounded HR query planner -> typed
read-only plan -> deterministic entity resolution -> per-step policy -> route and
schema health gate -> DiC service/page module -> completeness proof -> field-level
projection -> authorized delivery -> sanitized audit`

The provider remains limited to planning over a closed, policy-filtered schema.
Every browser action remains deterministic local code.

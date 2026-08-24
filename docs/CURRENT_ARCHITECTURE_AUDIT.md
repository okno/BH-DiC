# Current architecture audit

Baseline audited on 2026-08-24 at commit
`98b03932ca1bf548bff44a82a1fedd976e5603d0` (`main`). This document describes
the implementation as it exists before the conversational-navigation refactor.
It is evidence, not a declaration of live tenant coverage.

## Verified baseline

| Gate | Result |
| --- | --- |
| Python | 3.12.13 |
| Ruff format/check | Passed |
| mypy `src` | Passed for 118 source files |
| pytest | 982 passed, 1 skipped |
| Bandit | Passed |
| pip-audit | No known dependency vulnerabilities; local project skipped because it is not published on PyPI |
| gitleaks | Not installed on the audit workstation |

The skipped test needs a local Playwright Chromium binary. No production data,
DOM, cookie, credential, payroll value, employee identifier, or document was
captured by these gates.

## Current request path

| Layer | Current responsibility | Confirmed limitation |
| --- | --- | --- |
| Discord transport | Slash commands and guild messages; guild/channel/role gate; rate limits | DMs are always denied; mention mode is still tied to the single configured channel; channel messages are discarded unless a lexical pre-filter accepts them |
| Coordinator | Normalization, local parser, one model route, policy check, one dispatch | One `IntentEnvelope` and one Function ID per request; no query plan, multi-intent execution, context, follow-up references, or resumable clarification |
| Provider boundary | Bounded intent routing with dynamically visible functions | It is not a browser agent and cannot observe or navigate DiC; this security boundary is correct and must remain |
| Policy | Function-level role, entitlement, scope, feature-flag, confirmation and kill-switch checks | No field-level projection or delivery-channel policy for each returned resource |
| DiC service | Typed application operations and employee-list completeness guard | Complete pagination is implemented only for the employee list; payroll month discovery is a serial N+1 scan |
| Playwright adapter | Authentication, browser coordination, deterministic page calls | One shared `dic-browser` circuit can block unrelated resources; no route registry or live coverage registry |
| Page objects | Deterministic navigation and extraction | Ten page classes are concentrated in `dic/pages/routes.py` (more than 1,600 lines); the files under `dic/playwright/pages` are re-exports, not independent page modules |
| Network capture | Employee-list and payroll response capture | Other resources are primarily DOM-derived; capture schemas reject some harmless additive fields |
| Rendering | Discord embeds, messages and bounded attachments | Several resource views silently slice to 25 records; completeness is not consistently declared |

## Security properties to preserve

- The model receives only minimized natural-language input and a closed function
  catalog. It never receives a Playwright object, browser state, credentials,
  cookies, raw DOM, payroll files, or live DiC responses.
- Writes are disabled by default, require policy approval and dedicated feature
  flags, and are outside the live-read work in this refactor.
- Live Playwright traces and failure screenshots are forbidden.
- Discord bots, webhooks, threads, foreign guilds/channels and unmapped roles are
  rejected before application dispatch.
- Application outputs are typed and audit events must not contain raw HR data.

## Structural findings

1. `BHDiCBot.on_message` decides between the DiC coordinator and the public HR
   responder with `is_operational_hr_request`. A valid DiC request that is not in
   that phrase catalog never reaches the intent router.
2. `BHApplicationCoordinator.ask` accepts a local intent or exactly one provider
   `IntentEnvelope`, restores bounded identifiers, resolves at most one employee,
   evaluates one Function ID and dispatches one read or prepared write.
3. There is no persisted or expiring conversation state. Replies such as “quello
   con ID …”, “lui”, “il secondo” or “mandami il PDF” cannot reference a previous
   candidate/result set safely.
4. `DiscordGate.allow_dms` does not enable any usable path: missing guild/channel
   is unconditionally denied, and the bot does not subscribe to DM messages.
5. Ordinary mention-mode messages in another authorized guild channel still fail
   the single-channel gate.
6. Sensitive results can be made public with a global channel flag. The current
   design does not evaluate resource fields and delivery destination together.
7. `EmployeesListPage`, employee detail pages, contracts, roles, timestamps,
   maturations, balances, payrolls and documents share one large route module.
   This makes resource-specific drift isolation and ownership difficult.
8. `list_all_employees` verifies stable totals, deduplicates identifiers and fails
   closed on incomplete pagination. Equivalent contracts do not exist for every
   list resource.
9. Result rendering truncates several collections at 25 fields. Some flows attach
   a complete file, but others do not state that output is partial.
10. Runtime capabilities are derived from the static catalog, feature flags,
    role visibility and declared live support; they do not prove current route,
    schema or tenant coverage.

## Size and coupling evidence

At baseline, the main coordinator is over 2,500 lines, the page route module over
1,600 lines and the Playwright adapter over 1,200 lines. Size alone is not a
failure, but the classes mix routing, presentation, extraction, completeness and
resource recovery concerns. The refactor must split those seams without weakening
the existing policy and audit boundaries.

## Audit conclusion

The baseline is mechanically healthy and already has strong fail-closed security
primitives. It is not yet a general DiC conversational assistant: coverage is a
static set of single operations, navigation knowledge is not discoverable at
runtime, and delivery authorization is not expressed at field level. “Online” or
“authenticated” must not be treated as evidence that every DiC resource is
readable.

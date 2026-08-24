# Current failure analysis

This analysis is tied to baseline commit
`98b03932ca1bf548bff44a82a1fedd976e5603d0`. Examples are synthetic and contain
no tenant data.

| Area | Root cause | User-visible impact | Required regression proof |
| --- | --- | --- | --- |
| Channel routing | A lexical `is_operational_hr_request` pre-filter runs before the coordinator | Valid requests can be ignored or answered as generic HR guidance without any DiC navigation | Paraphrase corpus proves every authorized operational request reaches the planner |
| Intent routing | Provider/local parser returns one `IntentEnvelope` with one Function ID | Compound questions cannot search, disambiguate, navigate a second resource and aggregate results | Typed multi-step plans with dependency and ambiguity tests |
| Follow-ups | No TTL conversation store or result-set handle | “Il secondo”, “quello trovato prima”, “scarica la busta paga” lose their referent | Context isolation, expiry, restart and stale-reference tests |
| Entity resolution | Bounded name resolution exists only after a recognized single intent | Approximate names and ambiguous matches frequently become unsupported or require a fully restated command | Exact/fuzzy/ambiguous/no-match tests that never guess an employee |
| Discord DM | Gate always returns `DM_NOT_ALLOWED` for a missing guild/channel; DM event intent is disabled | “Private delivery” is not an available mechanism | Authorized DM identity mapping and unauthorized-DM denial tests |
| Mention mode | Gate accepts only the configured channel | Mentions in otherwise authorized channels are rejected | Guild/role/channel delivery matrix tests |
| Channel intake | Ordinary messages are ignored unless mention/reply/general-HR classifier matches | The configured private HR channel is not fully conversational | Test every non-bot message in the configured channel enters one bounded path |
| Sensitive delivery | One global flag can publish sensitive coordinator output to the channel | Data visibility is not decided per resource and field | Projection tests for public aggregate, protected channel and authorized private delivery |
| Authorization | Policy acts mainly on Function ID, logical role, entitlement and operation scope | An authorized function may still expose fields inappropriate for the chosen destination | Field-level allow/deny/redact matrix and denial audit tests |
| Page ownership | Ten page classes live in one route module; apparent page modules only re-export them | A page drift is harder to isolate and maintain | One module/contract/fingerprint per resource and import-boundary tests |
| Navigation | Routes are embedded in page methods; no central typed registry | No current status for route discovered/verified/degraded/disabled | Registry validation and route-status diagnostics |
| Readiness | Ready/authenticated only proves gateway/session health | Status can say online while a requested page schema is broken | Per-resource live/readiness states and health snapshots |
| Schema handling | Capture parsers use strict exact shapes at some response boundaries | Harmless additive API fields can create false drift incidents | Forward-compatible additive-field tests plus fail-closed required-field tests |
| DOM drift | Stable page fingerprints are not recorded/evaluated per resource | Failure appears late as a generic UI change | Sanitized fingerprint fixtures and explicit drift reason tests |
| Waiting | Several page flows poll browser state in short loops | Unnecessary latency and timing flakiness | Event/locator-state waits with deterministic timeout tests |
| Pagination | Only employee enumeration has a complete paginator with stable-total checks | Other list resources may be partial without proof | Shared paginator contract: total, pages, dedupe, progress, completeness or explicit partial result |
| Result limits | Several renderers use `[:25]` | Users may mistake a partial embed for a complete answer | Every truncated view declares partiality and supplies a complete bounded attachment/export when authorized |
| Payroll search | Employees are enumerated, then payroll is fetched serially for each employee | Slow response, N+1 load and elevated drift/error probability | Bounded concurrency or tenant endpoint, progress, per-resource circuit and completeness tests |
| Circuit breaker | Playwright reads share the `dic-browser` circuit key | One broken page can make unrelated resources unavailable | Independent resource circuit tests and global-auth circuit tests |
| Capability reporting | Static function catalog is reported as capability state | Catalog availability is confused with live tenant verification | Coverage matrix combines policy, implementation, route, schema and last live verification |
| Diagnostics | No `/bh diagnostics`, `/bh coverage`, `/bh route-status`, `/bh schema-status` | Operators must infer failures from generic messages/logs | Commands expose sanitized states, correlation ID and remediation without PII |
| Provider outage | A model-routing failure ends the request even where deterministic/local routes could suffice | DiC is healthy but the user sees an AI interpretation error | Local deterministic fallbacks for high-confidence operations; never guess on ambiguity |

## Representative failure chains

### Compound payroll question

Synthetic request: “Qual è il netto di Amin a luglio e puoi allegare la busta
paga?”

The present system must choose one Function ID. If it routes to payroll metadata
without an employee ID, the coordinator cannot safely finish. If name resolution
finds multiple similar employees, the returned candidate set is not stored for a
follow-up. Attachment selection is not a second plan step. The correct target
design is a typed read-only plan: search employee, pause on ambiguity, bind a
confirmed immutable ID, fetch payroll metadata for an explicit resolved period,
then authorize each output field and attachment for the delivery channel.

### Tenant-wide comparison

Synthetic request: “Quali dipendenti hanno una busta paga a luglio?”

The current implementation can execute the dedicated function, but does so as a
serial employee-by-employee scan. It has no generic progress contract and no
resource-scoped recovery. A payroll page drift contributes to the same browser
circuit used by other reads.

### Ordinary channel message

Synthetic request: “Controlla nel portale chi ha il contratto in scadenza.”

Before model routing, the channel transport may classify the text as generic HR
conversation. In that case it calls the public responder, which is intentionally
unable to access DiC. Authentication can be healthy while the request never
touches the application/adapter at all.

## Non-solutions

- Giving the language model browser access would violate the trust boundary.
- Making all responses public because the channel is private would bypass
  field-level least privilege and future membership changes.
- Retrying selectors indefinitely would conceal schema drift and increase load.
- Returning the first fuzzy employee match would risk disclosing the wrong
  person's data.
- Raising record limits without completeness checks would make partial data look
  authoritative.

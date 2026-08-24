# Conversational DiC refactoring plan

This plan is incremental. A milestone is complete only when its code, tests,
documentation and applicable live read-only evidence agree. No milestone enables
a production write.

## 0. Baseline and failure map

- Freeze commit and record all mechanical gates.
- Document actual slash/channel/mention/DM flows and root causes.
- Preserve existing audit, policy, provider-minimization and write kill switches.

Acceptance: the four baseline documents exist, contain no tenant data, and all
baseline gates remain green.

## 1. Authorized live read-only reconnaissance

- Use one authenticated Playwright context and serial navigation.
- Inventory employee-area routes, tabs, stable headings/landmarks, safe controls,
  network methods/path templates/query-key names and response key/type shapes.
- Never record values, raw DOM, screenshots, bodies, cookies, employee IDs,
  names, payroll amounts or document content.
- Store only sanitized structural artifacts and immediately stop if a control can
  mutate state.

Acceptance: route, network, selector and resource inventories identify what was
observed, when, under which build, and what remains unknown.

## 2. Navigation and data foundation

- Introduce a typed route registry with discovered/verified/degraded/disabled
  states and sanitized fingerprints.
- Split the route monolith into resource modules with explicit contracts.
- Prefer response events and locator-state waits to sleeps/polling.
- Make additive unknown response fields forward compatible while required fields,
  invalid types and contradictory totals fail closed.
- Add a shared complete paginator and explicit partial-result type.
- Isolate circuits by resource while retaining a separate authentication/session
  circuit.

Acceptance: fixture drift tests cover every resource; an isolated broken resource
does not disable unrelated reads.

## 3. Read coverage

- Implement typed extraction for employee registry, summary, contracts, roles,
  time access, maturations, balances, payroll metadata/attachments and documents.
- Add tenant-level filters/comparisons only when complete data can be proven.
- Make N+1 scans bounded, observable and cancellable; prefer observed tenant
  endpoints when available.
- Return source, freshness, completeness and unavailable-field metadata.

Acceptance: each supported resource has fixtures, parser tests, pagination tests,
route/schema state and a live read-only probe that reveals no PII in artifacts.

## 4. Conversational query engine

- Replace one envelope with a strict `HRQueryPlan` containing bounded read-only
  steps, dependencies, limits and sensitivity expectations.
- Implement multi-intent execution and deterministic local entity resolution.
- Store only opaque IDs/result handles and minimized metadata in an encrypted or
  memory-bounded TTL context; never store raw payroll/document content.
- Pause on ambiguity and require explicit user confirmation of an immutable ID.
- Resolve relative periods locally with timezone-aware dates.
- Add answer provenance and explicit incomplete/unsupported explanations.

Acceptance: at least 120 conversational cases cover paraphrases, compound
questions, ambiguity, follow-ups, relative periods, malicious prompts, provider
failure, tenant failure and schema drift.

## 5. Discord and authorization

- Treat every human message in the configured HR channel as potential input.
- Support mention/reply only in policy-authorized channels.
- Implement an actual authorized DM/private-delivery mapping rather than a
  dormant flag.
- Add field-level projections by resource, logical role, entitlement and delivery
  destination. Public aggregate, protected-channel and private fields are
  distinct classes.
- Add `/bh diagnostics`, `/bh coverage`, `/bh route-status` and
  `/bh schema-status` with sanitized operator output.
- Provide progress for long operations and bounded file delivery.

Acceptance: a delivery authorization matrix proves no sensitive field crosses to
an unauthorized channel or identity, including after role/channel changes.

## 6. Verification and deployment

- Run formatting, lint, typing, unit/integration/security/dependency/secret gates.
- Run a full read-only live coverage probe; never run live mutation tests.
- Update runbooks, privacy threat model, function catalog, architecture and
  recovery documentation.
- Deploy an exact reviewed commit, verify the audit chain, restart only the bot
  service, verify Discord readiness and DiC authentication, then execute bounded
  live smoke reads.
- Keep a commit-addressable rollback path and do not use update scripts that can
  race the service or install unreviewed dependencies.

Acceptance: production reports exact commit, bot/service state, Discord readiness,
DiC session state, resource coverage status and sanitized correlation IDs. Any
unknown or degraded resource is reported as such rather than claimed complete.

## Migration rules

- Prefer additive interfaces and adapters until new behavior has equivalent
  tests; remove old paths only after cutover evidence.
- Do not broaden write flags, confirmation rules or live mutation scope.
- Do not send DiC response data to the model. Planning input stays minimized and
  planning output stays schema-bound.
- Do not log prompts, names, identifiers, URLs, amounts, dates tied to people,
  document metadata or browser payloads.
- Every cap or timeout produces an explicit partial/degraded result, never a
  silently authoritative answer.

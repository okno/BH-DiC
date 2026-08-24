# Conversational architecture

In the configured HR channel every human message is evaluated; no keyword pre-filter may discard
it. High-confidence local parsing handles counts, lists, payroll periods, exports, status changes
and the compound contract/payroll query. Other minimized requests enter the closed model router.
Only a validated `UNSUPPORTED` outcome falls back to the stateless general-HR responder.

Conversation state is process-local, bounded and keyed by Discord user, guild and conversation.
It stores only opaque employee identifiers, a catalog Function ID and bounded scalar parameters.
Default TTL is 15 minutes; LRU size is 1,000 conversations and 100 candidate IDs per result. It is
never logged or sent to the model. Ordinal follow-ups such as “il secondo” are resolved locally.
Restart intentionally clears the state.

Implemented compound execution joins complete employee-list data with per-candidate payroll reads,
declares filters/period/totals/completeness and attaches the complete TSV. Further joins and general
result-set follow-ups remain explicitly unimplemented; they must not be described as complete.

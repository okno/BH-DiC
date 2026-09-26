# Natural-language test matrix

The executable corpus in `tests/unit/test_hr_query_plan.py` contains 120 unique Italian requests
covering counts, targeted payroll, payroll presence, compound contract/payroll joins, documents,
roles, timestamps, balances, maturations and contracts. `tests/unit/test_local_hr_intents.py` adds
realistic daily variants for short searches, profiles, salaries, contract deadlines, groups,
timestamp access, leave balances, documents, sorting, department filters and pagination. Every
request in these corpora must produce a local typed plan/Function ID: names and Employee IDs
therefore stay outside the model provider. The tests also assert local entity resolution, date
normalization, ordered dependencies, sensitivity, private delivery and a deterministic
clarification when the target employee is missing.

Additional integration scenarios verify:

| Scenario | Expected boundary |
| --- | --- |
| “netto di Nora a luglio” | local name search, payroll entitlement, private result |
| “stipendio Nora Collaudo” | exact local roster match and previous-month payroll, no model call |
| “ultima busta di Nora” | exact local roster match and latest paid payroll, no clarification |
| provider selects payroll but omits target | unique name recovered from the local roster |
| ambiguous name → “il secondo” | local TTL context; no identity sent to provider |
| contracts in 90 days without July payroll | three ordered read steps and complete attachment |
| unrelated HR/general phrasing | public HR responder only; no DIC operation |
| sensitive request without entitlement | policy denial before DIC read |
| DM member unavailable | denial before coordinator |

The corpus is not proof that every semantic combination is implemented. Unsupported joins,
corrections and result-set transformations must still request clarification or fail closed.

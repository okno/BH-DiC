# Natural-language test matrix

The executable corpus in `tests/unit/test_hr_query_plan.py` contains 120 unique Italian requests
covering counts, targeted payroll, payroll presence, compound contract/payroll joins, documents,
roles, timestamps, balances, maturations and contracts. For locally supported categories it asserts
the typed plan/Function ID, local entity placeholder, date normalization, ordered dependencies,
sensitivity and private delivery. Provider-routed paraphrases must still cross local minimization;
transport may not discard them.

Additional integration scenarios verify:

| Scenario | Expected boundary |
| --- | --- |
| “netto di Amin a luglio” | local name search, payroll entitlement, private result |
| ambiguous name → “il secondo” | local TTL context; no identity sent to provider |
| contracts in 90 days without July payroll | three ordered read steps and complete attachment |
| unrelated HR/general phrasing | coordinator first, then explicit general-HR fallback |
| sensitive request without entitlement | policy denial before DIC read |
| DM member unavailable | denial before coordinator |

The corpus is not proof that every semantic combination is implemented. Unsupported joins,
corrections and result-set transformations must still request clarification or fail closed.

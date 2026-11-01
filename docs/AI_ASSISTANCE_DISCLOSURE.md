# AI assistance and engineering ownership disclosure

## Assistance used

AI assistance was **substantial** across requirements decomposition, schema/migration drafts, deterministic generator design, synthetic edge-case modelling, forecasting and replenishment implementation, API/dashboard integration, test design, documentation, optimization and acceptance scripting. It would be misleading to describe this implementation as entirely hand-written without AI.

AI responses and complete chat transcripts are **not** reproduced in the prompt-history file. The curated user-input-only prompts and retrieval limitations appear in `AI_PROMPT_HISTORY.md`.

## Evidence of candidate-run validation available in this submission

- Candidate-reported Windows PostgreSQL migration 0005 and live smoke dataset reconciliation on isolated database `yottaflex_dual_v7`.
- Candidate-reported Windows V8 consolidated acceptance: 12 PASS, 0 FAIL, 5 WARN; 192 backend tests passed, 1 skipped; 8 Vitest tests; 7 Playwright browser tests; TypeScript/Vite build.
- Previously observed recommendation-list payload improvement from 101.28 KB to 18.63 KB; latency in the candidate's local benchmark improved from 61.40 ms to 15.62 ms. These are smoke data results, not load-test guarantees.
- Three historical positive replenishment decisions for a SKU discontinued *after* the decision date were verified from PostgreSQL records.
- Separately reproduced in an assistant-provided container: 100 SKUs, 10 stores, 90 days, CSV validator PASS, independent 72-file logical-row checksum PASS. **This is not candidate-local full-scale evidence.**

## Review and limitations

The candidate must verify every substantive claim, inspect changes, explain key equations and constraints, and record any changes personally made before certifying ownership. The assistant-provided sandbox did not have `pyarrow` or `duckdb` available and could not execute the full Parquet profile. Two historical Windows attempts failed preflight due to low available RAM; they were not successful scale runs.

Candidate-specific manual edits/reviews that cannot be independently established from the available archive are not invented here. Add a dated review note after inspecting the release before submitting.

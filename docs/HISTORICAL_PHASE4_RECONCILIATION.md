# Historical Phase 4 blocked outcomes versus V8 date-effective eligibility

**Source audit:** Uploaded source ZIP `yottaflex_phase1(2).zip` contains original P1-T04 artifact `evidence/P1-T04-20261030-231108-257/replenishment_replay/first/exceptions.csv`. This artifact independently identifies **exactly the same three recommendation IDs** as the current local PostgreSQL Step 33 inspection (user output, 2026-11-01).

| Recommendation | Store | Decision date | Original Phase 4 | Current V8 PostgreSQL |
|---|---|---|---|---|
| PO-2026-000002 | STORE-000001 | 2026-01-28 | BLOCKED / 0 | GENERATED / 240 |
| PO-2026-000014 | STORE-000002 | 2026-01-28 | BLOCKED / 0 | GENERATED / 252 |
| PO-2026-000026 | STORE-000003 | 2026-01-28 | BLOCKED / 0 | GENERATED / 324 |

All three belong to `SKU-000002`, with `launch_date=2025-09-03`, `discontinue_date=2026-01-30`, current master `status=DISCONTINUED`; their decision date **2026-01-28 precedes discontinuation**. Original P1-T04 artifact blocked them based on eventual master status, but V6+ historical rules correctly evaluate effective eligibility on the decision date. New positive decisions were recorded with `REORDER_POINT_TRIGGERED`.

**Finding:** Record identities reconcile exactly; discrepancy 19 positive + 14 zero + 3 blocked (Phase 4) vs 22 positive + 14 zero + 0 blocked (V8) corresponds to historical lifecycle defect corrected in V6. Historical evidence remains unchanged and is NOT presented as the current output. The current quantity figures originate from the user's Step 33 read-only query; this ZIP does not contain a live PostgreSQL dump.

**Boundary:** This reconciliation does not prove every other numeric formula across two algorithm versions is identical, nor permit positive recommendations *on or after* discontinuation. Existing regression tests cover date-effective behavior at unit level.

# P1-T04 — Phase 4 Replenishment Decision Engine (v1)

## Scope, ownership and validation status

AI-assisted **run-scoped** decision pipeline. It reads accepted Phase 2 public retail records and frozen Phase 3 selected forecasts. No tables, PostgreSQL migrations, Phase 2 generator, Phase 3 forecasting engine or prior evidence are modified. Run-scoped artifacts are **not persisted in PostgreSQL**. No complete system, full-scale throughput, API or UI acceptance is implied.

## Data dependencies and trust boundaries

- Phase 2 `manifest.json` and file SHA-256 values for master CSVs, selected public daily sales files and *all store inventory snapshots at the decision origin*. This ensures global store occupancy is not calculated using only selected SKU history.
- Phase 3 `forecast_manifest.json`, checksum-verified `forecast_selected.csv` (validated/published selected method only). Forecast prediction date > origin; `feature_cutoff == origin`; no test/oracle label input.
- SKU, Store, Supplier, SupplierSKU, ReorderPolicy, LeadTimeObservation are the official master entities. History and snapshots are the official daily fact entities. `Forecast` rows come from the existing Phase 3 artifacts. The new CSV uses exactly the 16 official `PurchaseRecommendation` fields, including JSON arrays/object cells.
- `restricted_ground_truth`, `negative_fixtures`, Phase 3 `oracle_evaluation.json`, and future realized sales are **not inputs**. Untrusted path traversal and source hash mismatch are rejected. No database connection is needed for this phase.
- Decision date = latest selected forecast origin per SKU/store; inventory snapshot must be dated **exactly** at that origin. No future stock, demand, supplier observation or realized promotion enters decision calculations.

## Computation and business rules

1. Resolve a real policy and SupplierSKU contract for the pair. Active policies and contracts use half-open `[effective_from, effective_to)` intervals, with empty end open-ended. Reject ambiguous overlapping active policy records. No fake FK anchors.
2. Check SKU `ACTIVE`, store `OPEN`, supplier `ACTIVE`, and SupplierSKU `ACTIVE`; dates must also be valid. Discontinued or prelaunch SKUs are blocked with zero recommended quantity, even with positive forecasts. For a missing FK policy/contract, do not output an uninsertable core entity: record a technical `unbound_exceptions.jsonl` entry.
3. Estimate supplier-specific effective lead time from eligible `RECEIVED` observations whose `actual_receipt_date <= decision_date`. Verify observed lead time and delays against actual/expected dates. A future completion is excluded. Effective lead days `max(contractual_days, ceil(mean_observed + std_observed))`; lack of enough observations is flagged.
4. Interpret Phase 3 forecast horizons (e.g. h1/h7/h14/h28) as *daily point forecasts*, not cumulative totals. Interpolate between forecasted horizons, repeat the first point for missing early horizons and extend the last level as a constant **only up to a configured max of 56 additional days**. Record `EARLY_HORIZON_BACKFILL` / `FLAT_TAIL_EXTRAPOLATION`. If outside allowed horizon, BLOCK rather than silently invent forecasts.
5. Forecast lead-time demand = sum of daily forecasts for effective supplier lead time. Protection demand = sum across lead time + review days. Historical volatility proxy is computed from **uncensored** point-in-time sales; actual zero demand is retained only when stock was available. Limited history and stockout fractions increase warnings and uncertainty.
6. Safety stock: `max(policy_floor, ceil(z(service_level)*sqrt(L*sigma_D^2 + (mean_daily^2)*sigma_L^2)))`. `sigma_D` is the greater of uncensored historical sales dispersion, prospective daily forecast dispersion and configured fallback; apply a stockout-censoring multiplier. This is a **proxy**, not a validated calibrated prediction interval. Use sample lead-time variability (and floor policy) explicitly.
7. Reorder point = `ceil(forecast_lead_demand + safety_stock)`. Inventory position = closing stock - reserved stock + *verified* outstanding supply. **Phase 2 has no purchase-order table/open PO event contract; outstanding supply is explicitly assumed zero**, never inferred by double counting observed receipts.
8. If inventory position <= ROP, order-up-to target is `ceil(protection demand + safety stock)`, and raw need is `max(0,target - inventory_position)`; otherwise zero. MOQ and pack size are jointly enforced. Capacity is checked across SupplierSKU quantity limit, ReorderPolicy quantity limit and store free storage (all SKUs in store at that date and SKU-specific storage conversion). A smaller feasible pack multiple may be issued with `CAPACITY_PARTIAL`, otherwise return `BLOCKED` with `CAPACITY_INFEASIBLE`. Zero need is `GENERATED` with quantity 0 and `REORDER_POINT_NOT_REACHED`.
9. Every decision records ID, policy version, supplier-contract version, input forecast horizons, supplier observation IDs, origin-aligned inventory ID, all calculations, rounding, capacity, warnings and reasons in `decision_trace`. Unchecked invalid/inconsistent input fails closed. `PurchaseRecommendation` ID is `PO-YYYY-NNNNNN` deterministically allocated per year **within one run**. Production database multi-run global uniqueness and locking must be handled in Phase 5 persistence integration.

## Output artifacts

| File | Contract |
|---|---|
| `purchase_recommendations.csv` | Exactly 16 official PurchaseRecommendation columns; blocked but FK-anchored rows have quantity zero |
| `exceptions.csv` | Subset of FK-anchored `BLOCKED` core recommendations |
| `unbound_exceptions.jsonl` | Technical-only issues that cannot satisfy mandatory nonnullable core FKs |
| `recommendation_traces.jsonl` | Per-decision explanatory calculation breakdown |
| `decision_audit.jsonl` | Run signature, policy, input cutoff, deterministic decision SHA |
| `technical_recommendation_lifecycle_events.jsonl` | Append-only run-local CREATED/GENERATED-or-BLOCKED export (one initial event per decision); **not PostgreSQL events** |
| `summary.json` | Counts, exceptions and operational assumptions |
| `replenishment_manifest.json` | Immutable run ID, input/source signatures, rule version, output hashes, database mutation and full-scale declarations |

Output directory must be empty/new. No overwrite, timestamps, random allocation or future oracle access. Same dataset manifest + signed selected forecasts + config yields identical seven artifact files and stable run ID. The audit verifier independently checks SHA-256 hashes, row counts, PO IDs, origin dates, fixed FK source/run fields, reason structures, blocked quantities, positive pack/MOQ adherence, store capacity and event references.

## Negative scenarios and automated acceptance

| Official case | Acceptance approach |
|---|---|
| NEG-01 stockout mistaken as zero | Phase 3 point-in-time censoring; Phase 4 uncertainty excludes stockout zeros from known-zero counts; unit tests |
| NEG-02 pack size ignored | 23 needed with pack 12 → 24 where feasible; capacity block/partial lot also tested |
| NEG-03 discontinued SKU recommended | Positive forecast and replenishment need still `BLOCKED`, quantity 0 |
| NEG-04 negative lead time | Negative contract / inconsistent observed lead time rejected before use |
| NEG-05 future sales in forecast | Phase 3 leakage checks retained; Phase 4 rejects post-origin historical rows and future supplier receipts do not alter supplier statistics |

Other automated checks: chronology, half-open effective intervals, supplier eligibility, stock occupancy, safety stock monotonicity with lead variability, signed file loading, duplicate forecast/source IDs, nonfinite numbers, nonoverwrite, input tampering, output tampering, recommendation lifecycle ledger, and same-input reproducibility.

## Known limitations / Phase 5 integration work

- **No PostgreSQL `Forecast` or `PurchaseRecommendation` bulk persistence**; not yet globally idempotent across run versions. An external durable idempotency key should be `(source_run_id, policy_id, store_id, sku_id, origin, rule_version)` and transactional server-side `PO` allocation is required before DB inserts.
- No actual `PurchaseOrder` entity (not one of mandatory ten). Cannot account for confirmed but unreceived orders until an authorized technical inbound-pipeline data contract is introduced.
- Uncertainty is an uncensored historical sales proxy, not calibrated probabilistic demand; forecast tail is explicitly extrapolated where required. High confidence and risk estimates require final calibration.
- Supplier and store capacity are modeled at their available contractual granularity; cross-SKU supplier-wide allocation is not specified by the ten entities.
- No actual inventory locking, concurrent purchasing, approval workflow, external supplier integration, or full-scale throughput evidence.
- The development sample is 12 SKU / 3 stores / 35 days. The official 2,000 × 100 × 365 run is deferred to final end-to-end acceptance.

## Usage (from existing backend directory)

```
.\verify_phase4.ps1 -DataRoot "..\examples\smoke_run" -MaxPairs 36 -SkipDependencyInstall
```

To consume already accepted Phase 3 artifacts rather than generating another small forecast:

```
.\verify_phase4.ps1 -DataRoot "..\examples\smoke_run" -ForecastRoot "..\evidence\P1-T03-20261030-222746-878\forecast_run" -MaxPairs 36 -SkipDependencyInstall
```

The script runs `pip check`, full backend pytest, validates/creates a small forecast, runs the replenishment engine, verifies its signed artifacts, regenerates twice, compares seven file hashes and writes local evidence under `evidence/P1-T04-<timestamp>/`. No PostgreSQL and no full-scale execution.

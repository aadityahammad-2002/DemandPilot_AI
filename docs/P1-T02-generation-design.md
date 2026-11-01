# P1-T02 — Deterministic Synthetic Retail Data Engine

## Scope and boundaries

This phase implements deterministic synthetic masters, vendor relationships, effective reorder policies, lead-time observations, daily demand/sales and inventory, hidden latent truth, inventory movement logs, the five **labelled fault fixtures**, CSV + SQL master seed + JSON reports, and validation/reproducibility. It **does not** implement real Forecast or PurchaseRecommendation computations, automated PostgreSQL import, backend APIs, or dashboard. These remain planned downstream tasks, not falsely completed.

## Exactly ten core business entities

`SKU`, `Store`, `SalesHistory`, `InventorySnapshot`, `Supplier`, `SupplierSKU`, `LeadTimeObservation`, `Forecast`, `ReorderPolicy`, and `PurchaseRecommendation`. Phase 2 populates eight of these ten entities, excluding Forecast and PurchaseRecommendation (count=0, explicit in manifest). Technical `tech_inventory_movement_event` outputs remain separate from these entities. Supplier/recommendation lifecycle events are added by existing PostgreSQL triggers when records later enter PostgreSQL.

## Data scale

Full profile: **2,000 SKUs × 100 stores × 365 days = 73,000,000 potential observations**. Only lifecycle-eligible days are emitted, with the `potential_sku_store_days` and `eligible_sku_store_days` reported distinctly. Promotions, intermittent zero-demand, stockouts, launches, discontinuations and delayed supplier receipts are included. Full scale has not been executed/benchmarked in the AI environment. `store_batch=2`, Parquet partitioned by month and store batch, and buffered batches of 2,048 rows bound the write-side memory footprint. The validation pass reads partitions in streaming order. Full-scale disk and time must be measured on the candidate's laptop.

## Determinism

- Config is explicit and versioned; stable SHA-256-derived RNG per `(seed, SKU, Store)` for latent daily demand.
- Stable SHA-256-derived RNG for each product, supplier contract and supplier-delay schedule.
- Immutable zero-padded public IDs exactly match v2.0: `SKU-000001`, `STORE-000001`, `SALE-STORE-000001-SKU-000001-2026-01-01`, etc.
- Partition manifests contain record counts, file SHA-256 and canonical logical-row SHA-256; stable same-seed/same-config runs must match.
- `reproduce` generates two independent fresh directories and validates them, then compares all file checksums/counts. The result is valid **within the same runtime/library/serialization environment**; Parquet physical hashes across different library versions are not a guaranteed invariant.

## Daily simulation

1. Determine date and lifecycle-eligible SKU and Store.
2. Set opening inventory to prior closing inventory; for first eligible day, derive initial stock from base rate and lead time.
3. Generate scheduled receipts from public supplier contract (14-day replenishment schedule) and the deterministic supplier delay series. This represents a **synthetic exogenous restocking assumption**, not an optimized replenishment recommendation.
4. Compute latent demand from SKU baseline, store factor, day-of-week, annual seasonal effect, known deterministic promotion schedule, and intermittent zero-demand probability.
5. Set `available = opening + receipts`; `units_sold = min(latent, available)`; `closing = available - units_sold`.
6. Persist SalesHistory and InventorySnapshot with matching canonical IDs, plus a separate restricted ground-truth record holding `latent`, `lost demand`, and diagnostic drivers.
7. Persist one append-only technical movement event for nonzero OPENING/RECEIPT/SALE; the stream reconciles with inventory snapshots.
8. All fields are generated from parent/source values. Invalid negative scenarios never appear in baseline files.

Annual sinusoid, probabilistic approximation for high Poisson rate, periodic promotions, and replenishment cycle are synthetic modeling assumptions. They are not statements about any real retailer.

## Mandatory negative fixtures (isolated, labelled)

- NEG-01: stockout recorded as observed zero despite positive hidden latent demand; required detection.
- NEG-02: 23 units ordered in packs of 12; required rounding/rejection and feasible quantity 24.
- NEG-03: positive recommendation attempted for discontinued SKU; required BLOCKED/zero.
- NEG-04: lead time -3; required database rejection.
- NEG-05: feature timestamp after forecast origin; required exclusion/rejection.

The JSONL files specify expected later algorithm/database behavior. **Presence of the fixtures is not proof that forecast/replenishment acceptance tests have executed.** PostgreSQL NEG-03/04 enforcement was separately verified in P1-T01, per candidate report.

## Data handling

- `master/*.csv`: deterministic parent/master/policy/lead-time records.
- `facts/sales_history/YYYY-MM/*.parquet` and `facts/inventory_snapshot/YYYY-MM/*.parquet` (or `.csv` smoke mode): observed data accessible to downstream pipeline.
- `technical_events/tech_inventory_movement_event/YYYY-MM/*`: append-only event export.
- `restricted_ground_truth/*`: strictly separate hidden labels. The public feature reader never joins this directory.
- `negative_fixtures/*.jsonl`: isolated invalid records; never loaded into published baseline.
- `sql/master_seed.sql`: PostgreSQL master/lead/policy seed only, **not 73M full daily fact SQL**. Review before manual use; must be loaded only into an empty/compatible target schema, not a populated assessment database.
- `manifest.json`, `generator_config.json`, `validation_summary.json`: machine-readable provenance and audit artifacts.

## Official ten data-generation steps

| DG | Implementation | Evidence/test |
|---|---|---|
| 01 full data dictionary | Unchanged `docs/data_dictionary.json` for exact 10 entities | P1-T01 dictionary + 14 tests |
| 02 parent-before-child valid records | master order and FK/interval checks | `test_complete_master_and_referential_validation` |
| 03 deterministic zero-padded IDs | existing `app.ids` + stable RNG | `test_same_seed_twice_exact_checksums` |
| 04 scale, seasonality, promos, intermittency, stockouts, supplier delays, launches | configurable full generator and daily simulator | `validation_summary.json`, scenario counters, tests |
| 05 clean baseline then inject five labelled failures | separate `negative_fixtures/*.jsonl` after facts | `test_separate_truth_and_required_negative_cases` |
| 06 derived values from sources | daily inventory and lead-time arithmetic | streaming validator + journal reconciliation |
| 07 isolated truth | `restricted_ground_truth` and whitelisted feature reader | `test_public_feature_reader_no_latent_truth_and_no_future` |
| 08 schema/temporal/arithmetic/duplicate validation | field-level P1-T01 PostgreSQL checks + file validator | validation report; P1-T01 report |
| 09 CSV/JSON/SQL exports, README, manifest, validation | master CSV; JSON reports; SQL seed; daily CSV/Parquet | `test_sql_and_manifest_exports` |
| 10 two same-seed regenerated outputs | `reproduce` command, SHA-256 + counts | reproducibility report |

## Known limitations

- Default full profile needs **pyarrow**, not installed in the assistant environment. Parquet functionality is implemented but not executed here; user must install `requirements-phase2.txt` and verify.
- Full-scale 73M potential combinations have not been generated here; memory, disk and runtime evidence on 8GB RAM remains outstanding.
- Statistical realism and aggregate seasonality are synthetic assumptions; distribution quality and business calibration require candidate review.
- Latest supplier observations are not per-store purchase orders; supplier-specific delays are mapped to a shared schedule. There is no PurchaseOrder core entity.
- The forecast feature reader prevents future rows and hidden-label columns but does not implement temporal training, feature engineering or forecasting. These belong in a later phase.
- Recommendation traces/blocked outcomes and live database journal triggers were addressed in P1-T01; Phase 2's separate JSONL fixtures are **test inputs**, not live recommendation decisions.

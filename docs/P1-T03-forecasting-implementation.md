# P1-T03 — Phase 3 Demand Forecasting Engine

## Scope and truth boundaries

This is **new add-only Phase 3 code**, separate from the verified Phase 1 and Phase 2 files. It intentionally does not connect to PostgreSQL, modify migration `0001` or `0002`, regenerate 2,000 × 100 × 365 observations, or implement replenishment or API endpoints.

- Forecast inputs: `facts/sales_history` and allowlisted public SKU/Store metadata, not `restricted_ground_truth`, `negative_fixtures`, derived `base_demand_rate`, final `status`, or future target sales.
- Training labels: historical observed `units_sold` only when not stockout-censored. An observed zero when no stock was available is **not** a legitimate known zero demand label.
- Censored historical features: imputed with only earlier uncensored observations (same weekday preferred), never from latent demand. Imputation is an estimate, not recovered ground truth.
- True zero observed sales when stock was available remain valid zero-demand examples.
- Future promotions: historical promotion flags are usable only as past features. An **optional public planned-promotion CSV** (`--promo-calendar`) supplies `store_id,sku_id,target_date,known_at,promotion_multiplier`. It is applied only if `known_at <= forecast_origin`; otherwise future promotion indicators remain unknown and are never inferred from future realized sales. Without this file, the model uses historical promotion frequencies only.
- Segment labels `regular` vs `intermittent` are derived from pre-origin observed noncensored zero frequency, not oracle truth or SKU sequence rules.

## Temporal methodology

The 365-day profile uses a fixed **245-day train / 60-day validation / 60-day test** chronological split, half-open date ranges. A short 35-day smoke profile uses **21 / 7 / 7** and is explicitly evaluation smoke, not an accurate generalization benchmark.

The global ML model is scikit-learn `HistGradientBoostingRegressor` with fixed seed, point-in-time planned promotion features where supplied, bounded reservoir training (max 40,000 uncensored training targets by default), training-only permutation importance, and no test-based tuning. Baselines: seasonal same-weekday, moving average, and Croston SBA. Model choice is based **solely on validation** WAPE (then MAE tie-break), not test or oracle.

Forecast-origin features use strictly earlier observed history. Rolling origins are limited to 12 per split by default, at 7-day increments, for requested horizons 1/7/14/28 days (only horizons inside the split are scored). Each multi-step path uses recursive **predicted** future values, not actual post-origin values. Future known calendar date/weekday is legal; future sales and future promotions not known at origin are not.

## Outputs

- `forecast_predictions.csv`: frozen run-scoped per-model predictions with official `FC-STORE-...-SKU-...-YYYY-MM` ID prefix plus deterministic version/sequence suffix where needed. **No target labels**.
- `forecast_selected.csv`: validation-selected model predictions, marked `SELECTED`, with no target labels.
- `evaluation_scoring.csv`: separate observed-sales evaluation inputs, with censoring flags and demographic-free segments. Not a model-input artifact.
- `evaluation.json`: train/validation/test method and horizon metrics: MAE, RMSE, WAPE (null when denominator zero), bias, excluded censored targets, and count.
- `explainability.json`: training-only global permutation importance, feature provenance, stockout policy and limitations (not causal effects).
- `forecast_model.joblib`: locally generated ML model (only deserialize trusted locally created artifacts).
- `forecast_manifest.json`: configuration, split dates, run signature, selected-pair count, SHA-256 checksums and DB-write flag.
- Optional `oracle_evaluation.json`: generated only by separate `oracle-evaluate` command *after prediction artifacts are frozen*. It uses restricted synthetic truth for offline evaluation; it cannot change a forecast, model, or validation selection.

## ID and database lifecycle limitations

Forecast IDs are deterministic and collision-free **within one forecast run**; the run ID is derived from the training public inputs and config, not future validation/test labels. This stage deliberately stores immutable exported artifacts, **not rows in the PostgreSQL `Forecast` table**. Any later database-import API must persist a cross-run version registry or reject duplicate IDs and enforce `uq_forecast_grain`; do not assume that two separate runs can insert the same `FC-*` IDs simultaneously. No Phase 1 schema alterations are made here.

## Scale, resource boundaries, and deferred acceptance

Default developer scope is 48 deterministic SKU-store pairs, scanned from public sales partitions, with at most 40k in-memory training feature rows. Loading selected histories costs O(selected_pairs × days) memory. The generator's potential 73M record full-scale gate remains **DEFERRED**, not passed. This Phase 3 design is suitable for bounded development batches; inference across all 200k combinations and cross-run version allocation require measured final-scale integration and throughput testing. Do not assert full-scale forecasting acceptance from a 48-pair run.

## Local Windows verification

Run from existing `yottaflex_phase1\backend` in activated Python 3.13 `.venv`:

```powershell
.\verify_phase3.ps1 -DataRoot "..\examples\smoke_run" -MaxPairs 48
```

Or supply the path to an already-verified small Phase 2 dataset. The script installs Phase 3 dependencies, checks pip, runs backend pytest, runs training/evaluation, validates artifacts, performs segregated oracle scoring, independently reproduces the same forecast outputs and writes timestamped evidence under `..\evidence\P1-T03-*`. **It never runs the Phase 2 full-scale generator or changes PostgreSQL.**

To run commands individually:

```powershell
python -m pip install -r requirements-phase3.txt
python -m app.forecasting.cli train-evaluate --data "..\examples\smoke_run" --out "..\evidence\phase3_manual_run" --max-pairs 48
python -m app.forecasting.cli verify --out "..\evidence\phase3_manual_run"
python -m app.forecasting.cli oracle-evaluate --data "..\examples\smoke_run" --out "..\evidence\phase3_manual_run"
```

Use a **new** output directory for each run. No existing artifacts are overwritten.

## Acceptance and limitations

Acceptance requires actual local `pytest` success, nonzero forecast rows, valid chronological/ID provenance, `verify` success, oracle separation, and matching independent re-run artifact hashes. Model performance must be reviewed against the baselines. The 35-day sample is too short to provide definitive evidence for a 28-day horizon. Full-scale dataset, PostgreSQL integration, high-volume inference, prediction interval calibration, scheduled future promotions, production deployment, and replenishment remain unverified or not yet implemented.

## Optional scheduled promotion input

Provide a trusted CSV file containing `store_id,sku_id,target_date,known_at,promotion_multiplier`. Run: `python -m app.forecasting.cli train-evaluate --data "..\examples\smoke_run" --out "..\evidence\promo_run" --max-pairs 48 --promo-calendar "..\my_planned_promotions.csv"`. Future realized promotion flags in `SalesHistory` are **never** used as planned future events. Phase 3 verifies the selected public sales file checksums against the Phase 2 manifest before using them.

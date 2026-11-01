# YottaFlex V8: targeted audit, corrections and acceptance record

**Scope:** Independently reviewed the latest 2026-11-01 uploaded source ZIP, not a fresh rebuild. All ten business entities, seven Operations sections, nine Developer sections, five existing Alembic migrations and V6 lifecycle/capacity safeguards remain. This is a **targeted hardening increment**, not evidence that every challenge acceptance gate is complete.

## Source baseline and verified checks

- Baseline source: `yottaflex_phase1(1).zip` supplied 2026-11-01; extracted source has `backend/app/integration/api.py`, React/Vite `frontend/src/App.tsx`, and Alembic revisions `0001`..`0005`. Full baseline file hashes are in patch `PATCH_MANIFEST.json`.
- Before changes: `python -m pytest -q`: **184 passed, 1 skipped** (real execution, SQLite/local fixtures).
- After changes: `python -m pytest -q`: **192 passed, 1 skipped** (real execution, SQLite/local fixtures). Includes eight new scoped API/metadata regression tests.
- `python -m compileall` passed. Standalone TypeScript/JSX parser returned **zero syntax diagnostics** for edited frontend source. TypeScript dependency-aware type checking was **not** run.
- `npm ci` could not finish from registry; `npm ci --offline` failed with `ENOTCACHED` for `yallist-3.1.1`. Thus post-patch Vite build, Vitest and Playwright are **not executed** here. Historical pre-patch Windows checks (8 Vitest, 6 Playwright, build passed) cannot be labeled current.
- PostgreSQL 18.4 on the user's Windows machine was **not accessed**. No PostgreSQL constraints, real workload, triggers or concurrency behavior was verified here.
- No full-scale 2,000 SKU × 100 store × 365-day generation was performed.

## Architecture and functionality preserved

- Backend: PostgreSQL SQLAlchemy domain entities and audit journals; Alembic; deterministic synthetic generator; moving average, seasonal naïve, Croston-SBA and HGB forecasting; chronological evaluation; replenishment including historical SKU lifecycle, pack/MOQ, lead time, supplier and shared store capacity; job ledger, signed artifacts, and FastAPI read/action endpoints.
- Frontend: Operations Manager (`overview`, `stores`, `risks`, `recommendations`, `suppliers`, `forecast`, `history`) and Developer (`overview`, `architecture`, `schema`, `generation`, `forecast`, `replenishment`, `validation`, `pipeline`, `evidence`).
- Preserved: local-only/no-auth intended demo, existing job idempotency and V6 business rules, PostgreSQL migrations 0001–0005, previously recorded evidence; original `yottaflex` DB and isolated `yottaflex_dual_v7` DB untouched.

## Changes made in V8

| ID | Type | Change | Evidence |
|---|---|---|---|
| V8-BE-01 | Confirmed UI/API data-scope defect | `sales?stockout_only=true` filters on SQL before pagination; Inventory Risks uses server-side count and pages, not first 500 sales records | New stockout filtering test |
| V8-BE-02 | Confirmed UI/API data-scope defect | `exceptions` now accepts SKU, store, date, source-run and purchasing-job scope; blocked table uses same filters with true pagination | New blocked scope test |
| V8-BE-03 | Confirmed API/UI limitation | New `forecast-trend` aggregates all matching forecast observations in SQL per target date, bounded to latest 180 distinct dates. Removes first-pair and first-500 chart sampling | New aggregated multi-pair/multi-run test |
| V8-BE-04 | Confirmed performance issue | `/run-catalog` batches lookup of older forecast-job parents outside the active 250-job list; reuses already-loaded parents; no N+1 | Query budget tests; isolated benchmark |
| V8-BE-05 | Confirmed selection inconsistency | `latest_forecast_run_id` now favors most recently completed persisted forecast job, not simply the run with the furthest target date; retains imported-run fallback | New chronological catalog test |
| V8-BE-06 | Verified stale code | Two PostgreSQL read-only verification scripts still demanded revision `0004` although latest schema is `0005`; updated expectations and explicit isolated-DB guard | Unit test on read-only gate (mock connection); live PG pending |
| V8-UI-01 | UX improvement | Bounded native SKU picker with debounced server-side search, ID/category matching, keyboard selection, All SKUs and selected-ID preservation | TypeScript syntax validation; browser pending |
| V8-UI-02 | UX improvement | Forecast chart no longer silently chooses one sample SKU-store; chart and table share source/filter scope. UI explicitly discloses duplicate forecast origins and latest-180-date chart bound | Aggregation API test; browser pending |
| V8-UI-03 | UX improvement | Run selectors show completion/status, counts and shortened IDs; full ID in option title | Source inspection; browser pending |
| V8-UI-04 | UX improvement | Documentation-only Developer pages hide irrelevant shared filters; removed duplicate date inputs from recommendations | Source inspection; browser pending |
| V8-UI-05 | UX improvement | Technical trace is collapsible and offers Copy JSON; evidence page shows dated uploaded baseline, not an unlabeled live result | Source inspection; browser pending |

**Important semantics:** The aggregated forecast chart sums *forecast observations*. If several forecast origins predict one SKU-store target date, those predictions are counted more than once; the UI explains this. They are **not** a single latest-origin operational demand forecast. Recorded sales during stockouts can understate latent demand. The chart displays up to 180 most recent matching target dates; the observations table is independently paginated with a true total.

## Isolated API benchmark (actually executed)

Source: `evidence/V8_run_catalog_benchmark.json`. A controlled SQLite in-memory database with **15 historical forecast-parent jobs + 251 more recent replenishment jobs**, no business rows; five `TestClient` GET requests for each version. Benchmark numbers are not PostgreSQL or production measurements.

| Version | SQL SELECT requests per response | Five-request mean latency |
|---|---:|---:|
| Original uploaded API | 259 | 49.61 ms |
| Patched API | 10 | 7.96 ms |

No latency statement has been made for other API routes without a measured baseline. At larger history sizes, queries/grouping for `run-catalog`, `forecast-trend`, dashboard aggregates, and offset pagination still require real PostgreSQL plan checks, indexing decisions and measurements.

## Known limitations and incomplete requirements

- **Critical final gate:** local PostgreSQL revision, constraints, triggers, RLS not in scope, locks, read-only query plans, recovery and concurrency still require isolated real-PostgreSQL testing. Do not alter `yottaflex_dual_v7` without approval; never target original `yottaflex`.
- **Frontend:** new UI requires local `npm ci`, `npm run build`, `npm test`, `npx playwright test`, manual focus/responsiveness/touchpad and cross-run acceptance. Unexecuted tests are not counted as passes.
- **Forecast chart:** multi-origin sum is clearly disclosed, but a latest-origin-per-pair operational forecast view is not implemented. All-SKUs/all-stores at massive scale needs scenario-based validation.
- **Large data:** 73-million-combination load, streaming/bulk import, disk sizing, query plans and memory profiling remain deferred by explicit instruction.
- **Security/deployment:** trusted localhost demo without auth. Do not expose to the public internet. Security review, dependency audit and failure recovery at DB disconnect are not established.
- **UI:** accessible dialogs/focus restoration, full responsive matrix, long JSON search/download, virtualized 2,000-SKU selection if needed, job details and category-total reconciliation still need improvement/testing.
- **Accuracy:** historic selected-model WAPE around 42.9908% is prior evidence, not a measured new live accuracy result. No accuracy uplift claimed.

## Issue register

See `docs/V8_ISSUE_REGISTER.csv` for severity, status, reproducibility, affected code, tested scope, and remaining work. **"Fixed / SQLite tested"** describes only validated test scope; it is not a PostgreSQL/browser PASS.

## Release hygiene

- Final deliverables omit `backend/.env`, virtual environments, `node_modules`, caches, historical `.yottaflex_backups`, old backups and prebuilt bundles. Never copy real local secrets to other users.
- Merge patch checks **exact uploaded baseline SHA-256 hashes**, writes backup before changing source, and ships rollback script. Patch scripts only touch listed files, not database tables or Alembic migrations. PowerShell scripts are authored and inspected, but **not executed in Windows here**.
- See `docs/V8_RUNBOOK_WINDOWS.md` and `docs/V8_POSTGRES_READONLY.sql` for manual gates.

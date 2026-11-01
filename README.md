# YottaFlex AI — SKU-Store Demand Forecasting & Replenishment

> **Enhanced integrated candidate:** start with [Final Implementation Report](FINAL_IMPLEMENTATION_REPORT.md), [Acceptance Matrix](FINAL_ACCEPTANCE_MATRIX.md), and [Windows Runbook](FINAL_WINDOWS_RUNBOOK.md). This enhanced source adds labelled adversarial overlays, causal forecast ablation and MASE, optional verified open-order inputs, a small evaluation-only business backtest and a read-only dashboard evidence endpoint. Full-scale, frontend Vitest/Vite/Playwright and live PostgreSQL acceptance remain pending; the enhanced frontend TypeScript typecheck has passed.

**72-Hour Code Challenge · Associate AI Forward Deployed Engineer / AI Forward Deployed Engineer**

A runnable **local-first, full-stack retail planning system** combining deterministic synthetic datasets, temporal demand forecasting, stockout-aware evaluation, supplier and store-capacity constraints, traceable purchase recommendations, a FastAPI backend, PostgreSQL persistence, and a React dashboard.

> **Acceptance scope:** The implementation has passed candidate-reported automated **small-development-dataset** tests and a separate **medium-scale CSV regeneration check**. **The mandatory 2,000 SKU × 100 store × 365 day full-scale Parquet test is not yet accepted or evidenced.** See [Acceptance Status](docs/FINAL_ACCEPTANCE_STATUS.md). This distinction is deliberate, not a claim of complete scale verification.

## Product overview

- **Operations dashboard:** store network, stockouts, inventory, suppliers, demand forecasts, purchase suggestions, exceptions and audit trail; clicking a recommendation shows its full recorded trace.
- **Developer dashboard:** architecture, data dictionary, deterministic generation, forecasting methodology, validation metrics, replenishment logic, negative-case fixtures and execution evidence.
- **Backend:** FastAPI and SQLAlchemy; PostgreSQL migration revisions `0001`–`0005`; idempotent pipeline jobs and persistence-mapping ledger.
- **Generation:** seeded, lifecycle-aware SKU/store matrix; promotions, intermittent/seasonal demand, supplier delays and product launches; observed stockouts kept distinct from zero latent demand. Hidden ground truth and all five negative-case fixtures are separate from valid feature inputs; the enhanced `examples/smoke_overlay_run` additionally contains five entity-schema-shaped adversarial overlays with restricted issue labels. These are NOT eligible for production DB import.
- **Forecasting:** origin-safe features and temporal holdout, candidate models including moving average, seasonal-naive, Croston-SBA and ML histogram gradient boosting.
- **Replenishment:** date-effective SKU eligibility, forecast lead-time demand, safety stock, reorder point, MOQ, pack size, supplier capacity, shared store capacity and full decision provenance. A suggested purchase is **not** a submitted purchase order. Optional explicit external `EXTPO` open-order snapshots now contribute to inventory position; without a verified snapshot, pipeline stock is explicitly assumed zero.

## Official functional requirements

The ten core business entities are **SKU**, **Store**, **SalesHistory**, **InventorySnapshot**, **Supplier**, **SupplierSKU**, **LeadTimeObservation**, **Forecast**, **ReorderPolicy** and **PurchaseRecommendation**. Primary/foreign keys, optional relationships and lifecycle rules are documented in `docs/data_dictionary.json`, models and migration scripts.

Required negative cases: (1) stockout confused with zero demand; (2) pack-size violation; (3) discontinued SKU recommended; (4) negative lead time; (5) future-sales forecasting leakage. See `examples/smoke_run/negative_fixtures/mandatory_cases.jsonl` and [Requirements Matrix](docs/REQUIREMENTS_MATRIX.md).

## Prerequisites

- Windows 10/11 or an equivalent supported environment.
- Python 3.13, Node.js/npm, and PostgreSQL 18 or a compatible PostgreSQL version.
- Free/local dependencies; Python optional **PyArrow/DuckDB** are needed for full-scale Parquet acceptance.
- A **new or explicitly approved separate PostgreSQL database**. Never run Alembic/SQL seed scripts against an existing production database.

## Setup and run (Windows PowerShell)

From repository root:

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-phase5.txt
Copy-Item .env.example .env
```

Set the database URL in `backend/.env` (or as an environment variable). **Do not commit real credentials.** The supplied candidate-specific runners require the isolated `yottaflex_dual_v7` database; configure a separate database only when explicitly approved and understand the script's safety checks.

On a **fresh, empty approved PostgreSQL database only**, execute `alembic upgrade head` from the backend directory. Demo source generation and the database import/pipeline are separate steps: consult `docs/V8_RUNBOOK_WINDOWS.md` and `docs/P1-T05-architecture.md` before performing any writes. A source-only smoke example is already bundled under `examples/smoke_run`.

Start the API:

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.integration.api:create_app --factory --host 127.0.0.1 --port 8000
```

In another terminal, start the frontend:

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`; API health is at `http://127.0.0.1:8000/health`.

### Automated tests

A non-destructive Windows verifier is provided at [`scripts/VERIFY_FINAL_WINDOWS.ps1`](scripts/VERIFY_FINAL_WINDOWS.ps1). It runs the entire 16-module backend suite as **four disjoint batches**, then attempts frontend Vitest/build (and optionally read-only PostgreSQL verification and Playwright when explicitly requested). The script never runs full-scale generation or database migrations. It emits an honest PASS/FAIL/BLOCKED/NOT_RUN status file under `evidence/final_local_*`.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\VERIFY_FINAL_WINDOWS.ps1
```


```powershell
# backend terminal, from backend/
.\.venv\Scripts\python.exe -m pytest -q --tb=short

# frontend terminal, from frontend/
npx vitest run
npm run build
npx playwright test --project=chromium --reporter=list
```

Playwright's real-API tests require a running API with approved populated small demo records. Some API actions are state-changing; do not run live pipeline actions against unapproved databases. For the candidate's previously prepared isolated DB, the [One-Shot Acceptance](YottaFlex_V8_OneShot_Acceptance_Fixed.ps1) utility performs guarded checks and compares before/after table fingerprints.

## Deterministic generation: small and medium

The generator uses a fixed default seed `20261030`. Its CLI can generate and reproduce data **without connecting to PostgreSQL**:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.generation.cli generate --profile smoke --out ..\new_smoke_output
.\.venv\Scripts\python.exe -m app.generation.cli reproduce --profile smoke --out ..\new_reproduce_output
```

Output paths must be new/nonexistent. Do not write into bundled examples. The generator writes master CSVs, partitioned facts, separate restricted ground truth, negative fixtures, JSON manifest/checksums and SQL **master/policy seed only**. The SQL seed is not 73 million INSERT statements and should **not** be executed against the populated candidate database.

### Required full-scale acceptance

**2,000 × 100 × 365 = 73,000,000 potential SKU-store-day combinations.** Lifecycle effects reduce *eligible sales* rows. Expected eligible count from the current generator configuration is **70,794,200**, subject to actual full-run verification. The full profile uses Parquet and a 2-store output batch to contain RAM usage.

Run from the repo root **only after** disk/RAM checks:

```powershell
# Install optional dependencies in the local backend virtual environment
.\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-phase2.txt

# Writes large generated outputs OUTSIDE OneDrive/project, default %LOCALAPPDATA%\YottaFlex_FullScale_Evidence
.\scripts\RUN_FULL_SCALE_WINDOWS.ps1
```

The existing `app.generation.full_gate` performs a resource-guarded Parquet pilot, full generation and file validation, and an **independent logical streaming regeneration with SHA-256 comparison**. It refuses to run with insufficient **18 GiB free disk / 2 GiB available RAM**, plus pilot-estimated additional margins, and aborts if operating reserves fall too low. **Full-scale execution may be CPU/disk intensive and has no guaranteed completion time.** An aborted or missing `full_scale_acceptance.json` with status `PASS` must be reported as NOT COMPLETE.

Generated full-scale artifacts should not be committed: keep only the acceptance JSON, reproducibility JSON and limited manifest summary after successful execution. This test **does not** prove bulk PostgreSQL import, large-data model training or dashboard load performance.

## Validation and evidence

| Verified area | Evidence |
|---|---|
| Candidate Windows smoke acceptance | 12 PASS / 0 FAIL / 5 WARN; 192 backend tests passed, 1 skipped; 8 unit tests; 7 browser E2E tests; database count/hash integrity |
| Backend API optimization | Recommendation list: 101.28 KB -> 18.63 KB (81.60% smaller), 61.40 ms -> 15.62 ms average in a local 5-request benchmark |
| Independent medium-scale generation | 100 SKUs, 10 stores, 90 days, 86,490 sales, 72 files/333,745 logical rows checked in second streaming pass, PASS |
| Full 2,000 × 100 × 365 Parquet | **NOT VERIFIED** (candidate attempts blocked by low free RAM; assistant environment missing optional Parquet dependency) |

See [`docs/FINAL_ACCEPTANCE_STATUS.md`](docs/FINAL_ACCEPTANCE_STATUS.md) and compact reports in `evidence/`. The sample data are synthetic and have no external retail customer provenance.

## AI transparency and submission

This project involved **substantial AI assistance**. Candidate prompt instructions, in a deliberately prompt-only format, are in [`docs/AI_PROMPT_HISTORY.md`](docs/AI_PROMPT_HISTORY.md); an explicit **incomplete-exact-history disclosure** is included there because prior unseen prompts cannot be reconstructed verbatim. Full prompt export from all AI tools remains the candidate's responsibility. [`docs/AI_ASSISTANCE_DISCLOSURE.md`](docs/AI_ASSISTANCE_DISCLOSURE.md) explains assistance, personal testing evidence and limitations.

The official YottaFlex FAQ asks for **a new dedicated public GitHub repository**, executable instructions, meaningful commits, **all prompts across AI tools**, transparent AI contribution, and the link sent from the **same recruitment email address**. Deadline is 72 hours after the recruiter email's recorded send time/time zone; no hosted demo or automated test suite is strictly required. See [`docs/SUBMISSION_REQUIREMENTS.md`](docs/SUBMISSION_REQUIREMENTS.md).

## Project structure

```text
backend/app/                  Schema, synthetic generator, forecasting, replenishment, API
backend/alembic/              PostgreSQL migrations
backend/tests/                Regression tests
frontend/src/                 React dashboard
frontend/e2e/                 Playwright tests (real API)
docs/data_dictionary.json     10-entity contract
scripts/RUN_FULL_SCALE_WINDOWS.ps1  Resource-guarded full data acceptance
docs/AI_PROMPT_HISTORY.md     User prompts only, with transparent export gaps
docs/AI_ASSISTANCE_DISCLOSURE.md
examples/smoke_run/          Non-sensitive synthetic sample
 evidence/                    Curated summaries only, not huge datasets
```

**Current limitations:** Not a publicly hardened production service; authentication/RBAC not implemented in the localhost challenge demo. Full-scale Parquet acceptance, live concurrent/failure recovery, large-dataset database/import performance and response load testing remain unverified. No live purchase-order execution is provided. The JavaScript bundle is relatively large (about 693 KB minified) and code splitting remains an optional improvement.

## GitHub publication

Create a **new dedicated public repository** on GitHub. From this clean project folder:

```powershell
git init
git add .
git status --short
git commit -m "Submit verified YottaFlex full-stack challenge implementation"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_NEW_REPO.git
git push -u origin main
```

Replace placeholder values; do not pretend a GitHub repo or commits already exist. Retain meaningful actual subsequent commits for changes you personally make, not fabricated retrospective history. **Check `git status`, inspect staged files, and verify no secrets before pushing.** Send the public link using the recruitment email address and follow the recruiter instructions before the recorded deadline.

**Recovering exact prompts:** A local helper, `scripts/EXTRACT_USER_PROMPTS_FROM_EXPORT.py`, can extract user-authored text messages from a ChatGPT `conversations.json` export filtered by conversation title. Review and redact the result before publication; it never publishes or sends your export. See `docs/AI_PROMPT_HISTORY.md`.

## Enhanced-source verification snapshot

On the updated repository, all backend test files were run in four **disjoint** process-isolated groups: **200 passed, 1 skipped, 0 failed**. A single-process full-suite run was attempted but exceeded the execution environment limit; no single-process PASS is asserted. Generation, five negative overlays, forecast artifact verification, replenishment verification and an evaluation-only small counterfactual passed on small data. The bounded CSV pilot (100 × 8 × 90) produced 69,192 eligible sales rows and passed validation. Refer to `FINAL_ACCEPTANCE_MATRIX.md` before any stronger acceptance claim.

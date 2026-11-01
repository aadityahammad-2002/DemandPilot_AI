# YottaFlex AI — Enhanced Release Windows Runbook

From the final extracted project root (not the old backup), use **Windows PowerShell**.

## 1. Environment

```powershell
$root = (Get-Location).Path
py -3.13 -m venv "$root\backend\.venv"
& "$root\backend\.venv\Scripts\python.exe" -m pip install -r "$root\backend\requirements-phase5.txt"
Set-Location "$root\frontend"
npm ci
Set-Location $root
```

Create `backend/.env` from `.env.example` with **your own approved isolated PostgreSQL credentials**. Never commit or share its value. To reuse the existing approved test DB, do not create a new database or re-run migrations. For a *brand-new empty* approved database only, run Alembic upgrade from `backend/` after reviewing database URL; migrations `0001–0005` must remain intact. This package adds **no migration**.

## 2. Data generation (does not write PostgreSQL)

```powershell
Set-Location "$root\backend"
$env:PYTHONPATH = "$root\backend"
& .\.venv\Scripts\python.exe -m app.generation.cli generate --profile smoke --out "$env:TEMP\yotta_overlay_smoke_new"
& .\.venv\Scripts\python.exe -m app.generation.cli validate --out "$env:TEMP\yotta_overlay_smoke_new"
```

Choose a **new** output path; generator deliberately refuses nonempty directories. The complete signed example is already shipped under `examples/smoke_overlay_run`; the original legacy `examples/smoke_run` remains unchanged for compatibility. The mandatory overlays are under `negative_overlays/`, and their detailed issue labels are under `restricted_ground_truth/`, **never** inside normal database imports.

## 3. Forecasting and replenishment smoke pipeline

```powershell
Set-Location "$root\backend"
$env:PYTHONPATH = "$root\backend"
& .\.venv\Scripts\python.exe -m app.forecasting.cli --help
& .\.venv\Scripts\python.exe -m app.replenishment.cli --help
& .\.venv\Scripts\python.exe -m app.replenishment.backtest --help
```

Use existing verified small results under `evidence/validated_final_small/forecast`, `.../replenishment` and `.../backtest.json` to inspect the executed small acceptance. For a fresh run, consult `docs/P1-T03-forecasting-implementation.md` for forecasting CLI flags, and the Phase 4 CLI below:

```powershell
& .\.venv\Scripts\python.exe -m app.replenishment.cli run --data "$root\examples\smoke_overlay_run" --forecast "<new_frozen_forecast_folder>" --out "<new_replenishment_output_folder>" --max-pairs 12
& .\.venv\Scripts\python.exe -m app.replenishment.cli verify --out "<new_replenishment_output_folder>"
& .\.venv\Scripts\python.exe -m app.replenishment.backtest --data "$root\examples\smoke_overlay_run" --replenishment "<new_replenishment_output_folder>" --out "<new_backtest_file.json>" --days 5
```

**Optional verified external purchase orders:** `--open-orders <local_file.csv>` on the Phase 4 `run` command. The exact header is:

```csv
external_order_id,store_id,sku_id,expected_receipt_date,outstanding_units,status
EXTPO-0001,STORE-000001,SKU-000001,2026-02-06,24,IN_TRANSIT
```

This is a local **input contract**; the sample line is an example, not verified real supply. Keep external references in the `EXTPO-` namespace, statuses OPEN/IN_TRANSIT/RECEIVED/CANCELLED, unique IDs, nonnegative quantities. Reconcile overdue shipments before evaluation. This is not a durable PO management system.

## 3A. Unified safe local acceptance check (new)

The release includes `scripts/VERIFY_FINAL_WINDOWS.ps1`. It **never** generates full-scale data, mutates databases, updates migrations, imports datasets, deletes old evidence or Git-pushes. It runs all backend test modules in four disjoint batches, optionally executes Vitest/build, and writes a timestamped JSON report under `evidence/final_local_*`.

```powershell
Set-Location $root
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\VERIFY_FINAL_WINDOWS.ps1
# Only when the configured PostgreSQL database is your approved isolated test database:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\VERIFY_FINAL_WINDOWS.ps1 -CheckPostgres
# Only when both API and Vite servers are already running with a populated test database:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\VERIFY_FINAL_WINDOWS.ps1 -CheckPostgres -RunBrowser
```

A `NOT_RUN` or `BLOCKED` result never means PASS. The script checks that each backend test file belongs to **exactly one batch** to avoid overstating counts. It does not silently run the resource-heavy full-scale acceptance.

## 4. Backend, API, frontend

```powershell
Set-Location "$root\backend"
& .\.venv\Scripts\python.exe -m pytest tests -q --tb=short
# Run PostgreSQL schema check against approved isolated database only:
& .\.venv\Scripts\python.exe .\verify_backend_schema_readonly.py
```

Run API in separate terminal (only against a configured, approved already-migrated PostgreSQL DB):

```powershell
Set-Location "$root\backend"
$env:PYTHONPATH="$root\backend"
$env:YOTTA_DATA_ROOT="$root\examples\smoke_overlay_run"
$env:YOTTA_ALLOW_IMPORT="false"
& .\.venv\Scripts\python.exe -m uvicorn app.integration.api:create_app --factory --host 127.0.0.1 --port 8000
```

Run frontend in separate terminal:

```powershell
Set-Location "$root\frontend"
npm ci
npm run test
npm run build
npm run dev
```

Open `http://127.0.0.1:5173`. New read-only evidence route: `http://127.0.0.1:8000/api/v1/dataset-validation`. Run Playwright once the approved populated DB and API are running:

```powershell
npx playwright test --project=chromium --reporter=list
```

## 5. Full-scale guard (not executed in this deliverable)

**Never run intermediate full-scale generation without reviewed resources.** Ensure PyArrow and DuckDB installed, disk and free RAM preflight passed, and output is outside OneDrive. The full run is time-consuming, writes many partitions, and must not be confused with small smoke evidence.

```powershell
Set-Location "$root\backend"
& .\.venv\Scripts\python.exe -m pip install -r requirements-phase2.txt
& .\.venv\Scripts\python.exe -m app.generation.full_gate --out "C:\YottaFlex_Full_Acceptance_NEW" --min-free-gib 18 --min-available-ram-gib 2
```

The full gate measures a Parquet pilot, enforces disk/RAM thresholds, generates, validates labelled overlays, and independently regenerates logical hashes. Full pipeline forecasting/replenishment over the entire large matrix remains a separate **downstream acceptance gate**, not implied by generator success.

## 6. Merge into existing clean Git worktree (optional)

Extract the ZIP to a separate new directory. Preserve the original clean worktree, `.git`, `backend/.env`, `.venv`, `frontend/node_modules` and local job artifacts. After stopping server processes, from a different PowerShell window:

```powershell
& "<extracted_release>\scripts\MERGE_FINAL_WINDOWS.ps1" -Source "<extracted_release>" -Target "<your_current_clean_worktree>"
```

This validates manifest hashes, backs up overwritten files under `%LOCALAPPDATA%`, and copies allowlisted release files **only**. It does **not** apply migrations, delete history or Git-push. Review `git status`, verify all tests, and only then commit/push.

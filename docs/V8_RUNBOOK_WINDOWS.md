# V8: safe Windows acceptance runbook

The merge is **source-only**: no database migration, import, truncation or full-scale job is triggered by applying the patch. Use one command group at a time; stop and diagnose the first failure. Do not send passwords or `.env` content into chat.

## 1 — Apply source patch to current local project

Extract `YottaFlex_V8_Merge_Patch.zip` somewhere outside the project. In PowerShell, within the extracted patch folder:

```powershell
.\apply.ps1 -ProjectRoot "$env:USERPROFILE\OneDrive\Desktop\yottaflex_phase1"
```

The script checks each modified file's SHA-256 against the exact uploaded baseline, refuses changes if local files differ, backs up originals to `.yottaflex_backups\V8_*`, copies only the listed source/docs/tests and checks new hashes. **It does not touch `.env`, PostgreSQL or any migration.** If your source has already changed since the upload, stop; do not force-apply. Roll back with `rollback.ps1` from the same extracted patch folder if needed.

## 2 — Run Python regression without DB modification

```powershell
cd "$env:USERPROFILE\OneDrive\Desktop\yottaflex_phase1\backend"
.\.venv\Scripts\python.exe -m pytest -q
```

Expected from isolated Linux/SQLite suite: **192 passed, 1 skipped**. Your environment may differ. A different result is not automatically a code defect; capture the actual traceback. No full dataset generation needed.

## 3 — Frontend build and unit tests

```powershell
cd "$env:USERPROFILE\OneDrive\Desktop\yottaflex_phase1\frontend"
npm ci --no-audit --no-fund
npm run build
npm test
```

These **must be executed on Windows** for the patched frontend. Earlier passing frontend tests predate V8. On failure, save output before retrying. Do not claim the patch's frontend PASSED until verified.

## 4 — Browser acceptance

With local API and frontend launched using the existing, database-guarded `START_PHASE5_WINDOWS.ps1` from project root (no automatic import or full-scale generation):

```powershell
cd "$env:USERPROFILE\OneDrive\Desktop\yottaflex_phase1\frontend"
npx playwright test
```

Verify Operations > Inventory Risks next/previous pages and totals; all-stores forecast chart versus selected filters; SKU search by category and ID; run selectors and browser back/forward; recommendation trace; long IDs; desktop/mobile viewport and touchpad scroll. Save actual Playwright logs and screenshots as new evidence.

## 5 — PostgreSQL read-only gate (requires existing approved isolated target)

```powershell
cd "$env:USERPROFILE\OneDrive\Desktop\yottaflex_phase1\backend"
.\.venv\Scripts\python.exe .\verify_backend_schema_readonly.py
.\.venv\Scripts\python.exe .\verify_phase5_schema.py
```

Both scripts require the configured isolated `yottaflex_dual_v7` database and revision 0005; first script reads `backend/.env` safely without overriding exported env variables. They have **not** been executed in this environment. Do not run the obsolete 0004 migration helper scripts. Read-only EXPLAIN and counts are in `docs/V8_POSTGRES_READONLY.sql`; use a **READ ONLY** transaction. No writes without an explicit backup/migration strategy and approval.

## 6 — Remaining final acceptance

PostgreSQL concurrency/failure recovery, load tests, full-chart browser journeys, complete accessibility review, index/query benchmarks, and final 2,000 × 100 × 365 data acceptance are still required. The 73-million-combination run stays deferred until those prerequisites are complete and separately approved.

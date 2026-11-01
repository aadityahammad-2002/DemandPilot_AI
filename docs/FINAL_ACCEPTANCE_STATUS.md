# Acceptance status - as verified through 2026-11-01

## Current development acceptance

| Check | Result and scope |
|---|---|
| Backend regression | Candidate Windows: 192 passed, 1 skipped (2026-11-01) |
| Frontend Vitest | Candidate Windows: 8 of 8 passing |
| Playwright | Candidate Windows: 7 of 7 passing, including full persisted trace in recommendation drawer |
| TypeScript/Vite production build | Candidate Windows: PASS; a large minified JS bundle remains (nonblocking) |
| API/PostgreSQL smoke reconciliation | 138 Forecast rows, 36 PurchaseRecommendation rows, 73 stockout observations, 8 pipeline jobs |
| PostgreSQL structure | Alembic 0005 and 10 mandatory business entities checked |
| No data mutation during one-shot | 12 table counts and SHA-256 row fingerprints unchanged |
| Recommendation optimization | Full trace retained on detail endpoint; listing 18.63 KB vs 101.28 KB prior; 5-request average 15.62 ms vs 61.40 ms prior |
| Historical discontinued SKU decisions | 3 decisions dated 2026-01-28 before discontinuation on 2026-01-30 |
| Consolidated audit | 12 PASS, 0 FAIL, 5 WARN (candidate Windows smoke dataset) |

## Separate medium-scale generator validation (2026-11-01)

- Config: seed `20261030`, 100 SKUs, 10 stores, 90 days, CSV, 2-store batches, movement journal enabled.
- Generator validator: PASS. Sales history: **86,490**; inventory snapshots: **87,130** (including 640 retired snapshots); ground-truth labels: **86,490**.
- Independent streaming regeneration: PASS, 72 files compared; 65 fact partitions; **333,745 logical rows** independently checked; zero checksum/row issues.
- Generation: 19.19 s and approximately 100 MB maximum RSS on the assistant container, **not predictive of candidate Windows/full-scale runtime**.
- Evidence: `evidence/medium_scale/{manifest.json,validation_summary.json,streaming_reproducibility.json,config.json}`. Large generated rows are intentionally excluded from the repo.

## Required full-scale profile: NOT PASSED

- 2,000 SKUs x 100 stores x 365 days = 73,000,000 potential SKU-store-days.
- Source lifecycle model predicts **70,794,200 lifecycle-eligible SKU-store-days**, not 73 million actual sales rows. Retired inventory may add separate snapshots. This is a calculation, not proof of a complete generated dataset.
- Full-scale Parquet run, independent regeneration/validation, I/O performance and disk consumption have **not** been accepted. Historical candidate attempts were rejected at RAM preflight (0.85/0.94 GiB free); the independent sandbox attempt stopped at PRECHECK because `pyarrow` was unavailable.
- Run `scripts/RUN_FULL_SCALE_WINDOWS.ps1` only after verifying free disk (>=18 GiB), available RAM (>=2 GiB) and dependencies. This writes outside the OneDrive-backed repo, does not access PostgreSQL, and may take substantial CPU/disk time.
- Full-scale synthetic fact generation does not, by itself, constitute 73-million-row PostgreSQL import, end-to-end forecast training, dashboard load test or concurrency test.

## Outstanding risk register

1. Full-scale data acceptance and reproducibility proof (mandatory target; unfinished).
2. Concurrent pipeline job execution and crash/recovery behavior under isolation (not independently proven).
3. Full-scale endpoint responsiveness and memory load (not tested).
4. Frontend bundle is about 693 KB minified / 199 KB gzip; optional code splitting pending.
5. Actual user prompt histories from every AI tool may need additional manual export; this repository does not assert it includes all unseen prompts.

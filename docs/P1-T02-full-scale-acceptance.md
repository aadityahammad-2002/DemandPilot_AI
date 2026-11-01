# P1-T02 — Full-scale acceptance gate (add-only extension)

## Current evidence

- User-reported Windows Phase 2 smoke: dependency check PASS; 29/29 backend tests PASS; 1,164 SalesHistory + InventorySnapshot + ground-truth records; 31 files matched with zero reproducibility issues.
- AI test environment for this **add-only full-scale verification patch**: 36/36 backend tests PASS (includes the existing 29 when checked in the assembled test tree); independent streaming reproducibility also PASS for a previously generated medium 100-SKU/10-store/90-day CSV run (333,105 logical row inputs across 67 manifest-listed files).
- **Not verified here:** Windows execution of the new full-scale gate, Parquet/PyArrow, actual 2,000 x 100 x 365 full-scale run, laptop memory/disk, current Windows pytest results with the new tests.

## Why `verify_phase2.ps1 -Full` is not a sufficient final acceptance gate

The existing script runs smoke pytest and reproducibility followed by one full `generate` call. **It never reproduces full-scale outputs a second time.** Therefore a PASS from that command alone must not be reported as full-scale two-run checksum verification.

## Add-only files

- `backend/app/generation/full_gate.py`: resource-guarded full-scale acceptance entry point.
- `backend/app/generation/full_reproduce.py`: independent same-seed regeneration of all master and fact records, checking logical SHA-256 and row counts per partition without writing a second full-sized dataset.
- `backend/tests/test_phase2_full_gate.py`: Python regression tests of count derivation, streaming comparison, tampering, safety guards.
- `backend/verify_phase2_full_safe.ps1`: Windows runner from existing backend virtual environment.

No changes to existing generator modules, models, migrations, or database.

## Command, from the existing backend directory

```powershell
.\verify_phase2_full_safe.ps1
```

The script expects `.venv\Scripts\python.exe`, installed `pyarrow` and `duckdb` from `requirements-phase2.txt`, an existing `..\evidence` folder, and enough **free** (not just physical) RAM and drive space. It creates a unique `..\evidence\P1-T02-FULL-<timestamp>` directory and refuses to overwrite it.

## Execution gates

1. Confirm the default full profile is exactly 2,000 SKUs, 100 stores, 365 calendar days, Parquet, 2-store batches, and inventory movement journal enabled. Check free RAM >=2 GiB and free disk >=18 GiB **before** any full run.
2. Generate and validate a real Parquet pilot (100 SKUs, 8 stores, 90 days). Estimate full logical-eligible row coverage and scaled storage from pilot output. Require estimated full storage with 1.7x conservatism **plus 8 GiB reserve**. The estimate may block conservatively and is not an empirical full-run guarantee.
3. Generate full history into a fresh directory, preserving its manifest, 10 core entity counts (including zero Forecast and PurchaseRecommendation until later), 5 negative fixtures, ground truth separation, and inventory event journals. The existing generator validates all files, logical/physical SHA-256, inventory math, and lifecycle coverage.
4. Re-derive a second full same-seed series, comparing every source master, fixture, SQL seed, and fact/event partition row count and canonical SHA-256; a second 73-million-row disk copy is **not** retained. This verifies independent computational reproducibility, but not two bitwise-identical Parquet archives across library versions.
5. `full_scale_acceptance.json` is PASS only if every gate and count checks pass; `full_reproducibility.json` captures the second-run proof. No PostgreSQL writes and no forecasting are performed.

## Resource limitations

- Full potential coverage is 73,000,000 SKU-store-day pairs; emitted facts are lifecycle-eligible rows and may be fewer due to launches/discontinuations.
- A large number of Parquet partitions, three fact families plus movement journals, repeated canonical hashing, and row-by-row generation are CPU/disk intensive. No reliable completion-time prediction can be made until locally benchmarked.
- Pilot-based estimates may be wrong for a different date/assortment mix; free RAM/disk are monitored during generation and generation stops if reserve falls below guard levels. Partially generated evidence is preserved for diagnosis, not reported PASS.
- The second regeneration is independent at the **logical row level** and intentionally disk-light; it does not create or compare a second physical Parquet file byte stream. This is a documented tradeoff in light of the 8 GB RAM and disk constraints.
- No database restore, PostgreSQL fact loading, forecasting model, or dashboard is tested by this gate.
- Data includes `sql/master_seed.sql` for **master/policy observations**, not 73m SQL INSERTs; it must **not be executed against the existing application database**.

## Human acceptance

Review `full_scale_acceptance.json`, `full_reproducibility.json`, the full dataset manifest and validator summary, the drive/RAM figures, and any failure logs before marking P1-T02 locally accepted. Capture the full prompt history and label these files as AI-generated, with local commands/tests performed by the candidate.

Only after **candidate full-scale PASS** should the Phase 3 forecasting engine be integrated into the project. Phase 3 must use origin-safe features, leakage tests, chronological train/validation/test, deterministic forecasts, candidate-reviewed model evaluation, and independent ground-truth evaluation.

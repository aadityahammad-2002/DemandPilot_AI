# Submission acceptance scope — 2026-11-01

## Evidence basis

Uploaded source ZIP `yottaflex_phase1(2).zip`, independent user-executed Windows one-shot acceptance `evidence/V8_ONE_SHOT_20261101_193239/` (**12 pass, 0 fail, 5 warn**) and supporting local testing discussion. Package creation is **source-only**, no connected PostgreSQL instance in this environment.

### Confirmed within supplied evidence

- Backend 192 passed / 1 skipped; frontend 8 Vitest passed; Playwright 7 passed; production Vite build passed.
- Original DB `yottaflex` not touched by the one-shot run. Isolated `yottaflex_dual_v7` at migration `0005` and all 12 monitored tables had matching pre/post counts & content fingerprints.
- Live API totals: forecast 138, recommendation 36, stockout 73, jobs 8; full trace loaded on detail view, lightweight list returns all 36 records.
- Full-scale data **NOT GENERATED**. Only resource-gated implementation provided; no full-scale acceptance claims.

### Source changes introduced in this submission-candidate package

- Added `frontend/vitest.config.ts` to prevent `npx vitest run` collecting `e2e/*.spec.ts` (`TEST-CONFIG-01`). Requires candidate-local execution for independent verification; existing `npm test` used scoped `vitest run src`.
- Playwright configuration fallback selects Windows `backend/.venv/Scripts/python.exe` instead of global `python` when backend server is not already available; `reuseExistingServer` behavior preserved. Requires candidate-local validation.
- Added `.gitignore` and clean, current README, with `docs/README_HISTORICAL_V8.md` retaining original historic notes, historical Phase 4 identity reconciliation, and this acceptance report. The included one-shot verifier now defaults to its own folder and verifies original-vs-current Phase 4 IDs, and checks the new Vitest config; changes to the verifier are not re-executed here. The included archival `evidence/phase5_runs/` artifacts support reviewing prior successful forecast/replenishment runs (they are not full-scale outputs).
- No changes to domain algorithms, SQLAlchemy business models, Alembic migrations, production database tables or existing smoke datasets. PERF-02 was already in uploaded baseline.

### Outstanding gates (do not overclaim)

1. Mandatory full-scale `2000 × 100 × 365` data generation, validation and twice same-seed logical reproducibility on adequately provisioned infrastructure; data-only gate does **not** establish DB large-scale throughput.
2. Real simultaneous pipeline job submissions, lock contention, crash recovery and transactional atomicity on a disposable PostgreSQL database; **never** run destructive tests on `yottaflex_dual_v7`.
3. Bundle code splitting/performance at scale (latest build warning ~693.42 KB minified); accessibility and cross-browser manual checks.
4. Re-run Vitest and Playwright on the **submission candidate** after changed configs; uploaded baseline tests do not automatically prove this packaging increment.

### Package validation

The release build script generates a SHA-256 inventory, rejects private `.env`, database dumps, old backups, virtual envs, `node_modules` and compiled frontend distributions, and preserves the latest small-data acceptance evidence. Manifest files are source/package-integrity evidence, **not** cryptographic authenticity signatures.

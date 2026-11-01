# Challenge requirements traceability

This matrix maps the **user-supplied official 72-hour problem statement** to repository paths. "Implemented" means the source path exists; it is not an assertion that every environment/scale gate passed.

| Requirement | Source | Evidence / limitation |
|---|---|---|
| 10 core relational entities, PK/FK and lifecycle constraints | `backend/app/models.py`, `backend/alembic/versions/`, `docs/data_dictionary.json` | Schema verification scripts, candidate Windows migration 0005 |
| Parent-first deterministic master data | `backend/app/generation/generator.py`, `config.py` | Small/medium generated manifests and validation |
| SKU/store matrix with seasonality, promotions, intermittent demand | `backend/app/generation/generator.py` | Medium validator PASS |
| Stockouts distinct from zero demand | `restricted_ground_truth/` format, `backend/app/generation/verify.py` | Hidden truth isolated from public features |
| MOQ, pack size, lead times, supplier and shared capacity | `backend/app/replenishment/`, `backend/tests/` | Small-dataset regressions; not full-scale acceptance |
| Temporal forecasting holdout / model comparison | `backend/app/forecasting/`, `backend/tests/` | Previous backend tests; no full-scale fit benchmark |
| Event/journal persistence | `backend/app/models.py`, migrations and API | Live smoke database schema checked |
| Exceptions and decision trace | `backend/app/integration/api.py`, `frontend/src/App.tsx` | API trace and Playwright drawer test |
| Five isolated negative scenarios | `backend/app/generation/generator.py` fixtures and `backend/tests/` | Presence tested on smoke/medium; full negative-case acceptance evidence not exhaustively re-verified here |
| IDs: SKU-..., STORE-..., SALE-..., FC-..., PO-... | `backend/app/ids.py` | Contract tests and docs |
| Data dictionary, export and source manifest | `docs/data_dictionary.json`, `backend/app/generation/{cli.py,writers.py,sql_seed.py}` | CSV/JSON/SQL master exports and CSV/Parquet partition capability; full Parquet run unverified |
| Fixed seed + regeneration | `backend/app/generation/{full_gate.py,full_reproduce.py}` | Medium independent logical checksum PASS; full missing |
| 2,000 x 100 x 365 | `GeneratorConfig()` full profile, `scripts/RUN_FULL_SCALE_WINDOWS.ps1` | **Not accepted until actual candidate full gate PASS** |
| Working FastAPI and interactive React dashboard | `backend/app/integration/api.py`, `frontend/src/App.tsx` | Candidate Windows backend, frontend and Playwright smoke tests PASS |

Synthetic records represent fictional retail activity, with no external customer or personal records. Public data is not needed for the generator, and any third-party dependency licensing remains governed by its own published terms.

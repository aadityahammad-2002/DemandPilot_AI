# Phase 5 Verification Matrix and Candidate Sign-off

| Requirement | AI-environment test / evidence | Required Windows acceptance |
|---|---|---|
| Exact 10 official core + existing 3 journals | Existing Phase 1 tests remain unchanged | 0003 read-only schema checks confirm core + journals unchanged |
| 0003 technical ledgers | Alembic `0003` PostgreSQL **offline SQL** generated | Backup, `alembic current 0003 (head)`, table existence |
| Real master, sales, inventory | `test_verified_seed_and_all_ten_tables` on SQLite | PostgreSQL `/api/v1/*` queries after explicit small import |
| Forecast persistence + versioning | `test_persist_frozen_phase3...` and job-sequence tests | Forecast job SUCCEEDED, source mappings present and IDs compliant |
| Replenishment, supplier, capacity trace | Phase 4 verified engine + source contract tests | All 36 decisions; 19 positive and 3 blocked on accepted smoke dataset |
| Database idempotency | Same-source second import test; collision unit test | Re-run opt-in sample with stable content, verify no duplicate mappings |
| PostgreSQL lifecycle and journaling | SQLite cannot execute PG triggers | PATCH `GENERATED→VALIDATED`, event appears; journal UPDATE/DELETE rejected |
| Typed backend endpoints, error states | TestClient authorization, filters, date validation, 404/pagination | Local PostgreSQL HTTP TestClient plus browser |
| React UI responsive behavior | TS/TSX syntax transpilation only | `npm run build`, `npm run test`, `npm run test:e2e` against live PostgreSQL API, plus personal responsive/device review |
| Job statuses, safe orchestration | End-to-end local sample job runner against SQLite | PostgreSQL import → forecast → replenish, all SUCCEEDED and persisted |
| Full-scale volume and performance | Not tested | **Deferred** to final end-to-end acceptance |
| AI use and final submission | Generated disclosure document | Candidate prompt export, personal review and sign-off |

## Status semantics

- `AI_TEST_PASS`: executed in assistant environment against SQLite, source-based artifacts or SQL offline rendering. Not evidence of Windows PostgreSQL acceptance.
- `LOCAL_TEST_PASS`: require actual terminal output, migration history and API/DB evidence from user's machine.
- `PENDING`: not executed or verified.
- `DEFERRED`: intentionally postponed full-scale profile, not passed.

## Candidate sign-off checklist

- [ ] Preserve Phase 1–4 source hashes and previous acceptance logs.
- [ ] Backup the *existing* PostgreSQL 18 database, verify TOC/readability and retain SHA-256.
- [ ] Apply migration 0003 without rewriting 0001/0002.
- [ ] Run consolidated backend, frontend and PostgreSQL acceptance tests; retain logs.
- [ ] Manually inspect React dashboard on desktop and narrow/mobile viewport; inspect API error states and actual jobs.
- [ ] Confirm full-scale verification remains deferred, no 2,000 × 100 × 365 run occurred.
- [ ] Review traceability, data dictionary, source ID mapping and AI assistance disclosures.
- [ ] Approve Phase 5 only with actual local evidence.

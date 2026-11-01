# Phase 5 Architecture & API Contracts

## Topology

```text
React/Vite Dashboard (port 5173)
     | HTTPS in deployment; HTTP loopback for local demo
     | Bearer token; allowed origin list; JSON typed request/response
FastAPI (port 8000, loopback-only)
     +-- /api/v1/* reads core PostgreSQL tables using SQLAlchemy
     +-- POST /api/v1/jobs/{import,forecast,replenish}
     |     | persistent jobs in tech_pipeline_run
     |     +-- one bounded in-process worker
     |          | Phase 2 signed public sales/inventory files (NEVER hidden truth)
     |          | Phase 3 forecast engine + verified frozen artifacts
     |          | Phase 4 replenishment engine + verified trace artifacts
     |          +-- SERIALIZABLE-ID allocation via PG advisory transaction lock;
     |                core data & job state committed atomically
     +-- SQLAlchemy -> PostgreSQL 18
           | all 10 official business entities (0001/0002 untouched)
           | 3 append-only technical event journals (0001/0002 untouched)
           +-- 2 new technical ledger tables (0003)
```

## Technology choices

FastAPI / Pydantic / SQLAlchemy / psycopg / Alembic, React / TypeScript / Vite / Recharts / lucide-react. Reuses Phase 2 Parquet/DuckDB generator and Phase 3 sklearn model training as-is. No Redis, Kafka, Kubernetes or paid service required for small local development acceptance.

## New technical tables (not official core entities)

`tech_pipeline_run`: job_id PK, idempotency_key UNIQUE, kind enum, status enum, timestamps, input checksum, artifact path, error code/details, result JSON. `tech_persistence_map`: source_key PK, source_hash, entity_type, database_id, job_id FK to pipeline run, unique (entity_type,database_id), created_at. These have their own SQLAlchemy `IntegrationBase` metadata, so the original `Base.metadata` remains **exactly the original 13 tables** (10 business + 3 journals).

The 0002 append-only event triggers are not replaced. Ingestion of `tech_inventory_movement_event` uses signed synthetic history events once; recommendation and supplier event journals are created by PostgreSQL triggers. No extra core business table is introduced.

## Stable identities, transaction ordering and replay protection

1. Read only the configured small source dataset (source file hashes checked, no hidden truth or negative fixtures imported).
2. Validate selected forecasts and recommendations against their original signed manifests.
3. Obtain PG transaction-level advisory lock `yottaflex_phase5_global_ids` before allocating global IDs.
4. For each immutable source key, consult `tech_persistence_map`: same key + same hash means skip, changed content for same key is rejected.
5. Reuse official forecast `FC-STORE-...-SKU-...-YYYY-MM` identities if free. On cross-run collision, use valid `-V####` and `-N######` extensions; enforce the existing 0002 ID CHECK constraints. For purchase recommendations, allocate the next available `PO-YYYY-NNNNNN` ordinal. No existing row is overwritten.
6. Persist mapped IDs, actual `Forecast` or `PurchaseRecommendation` rows, and success job state in one commit. On any database-integrity error, rollback everything in that transaction.
7. A `BLOCKED` discontinued-SKU recommendation adds the canonical `DISCONTINUED_SKU` reason required by trigger `0002` without modifying the source artifact. Original reasons remain in `decision_trace`.
8. User-controlled recommendation transitions use a row lock and PostgreSQL's 0002 state-transition and append-only event triggers.

A failed pipeline may retain *on-disk* artifacts for forensic inspection, but does not declare success or commit partially imported forecast/recommendation rows.

## HTTP routes

| Method | Route | Contract |
|---|---|---|
| GET | `/health` | Liveness (no credential or DB read) |
| GET | `/api/v1/overview` | Aggregated actual KPIs and source dates |
| GET | `/api/v1/skus`, `/stores`, `/suppliers`, `/supplier-contracts` | Paginated master data with search/filter |
| GET | `/api/v1/sales`, `/inventory` | Date-range-filtered real facts |
| GET | `/api/v1/lead-times`, `/policies` | Supplier lead evidence, policies |
| GET | `/api/v1/forecasts` | Paginated persisted selected forecasts |
| GET | `/api/v1/model-metrics` | Verified signed evaluation + explanation from latest successful forecast run |
| GET | `/api/v1/demand-trend` | Daily summed observed sales and stockout counts |
| GET | `/api/v1/recommendations` | Paginated quantity, supplier and source-based unit costs |
| GET | `/api/v1/recommendations/{id}` | Detailed quantity, constraints, rule trace, policy and lifecycle events |
| PATCH | `/api/v1/recommendations/{id}` | Allowlisted state transition, journaled by DB 0002 trigger |
| GET | `/api/v1/exceptions`, `/exceptions/unbound` | Blocked persisted decisions and unbound technical source exceptions |
| GET | `/api/v1/audit-events` | Paginated read of append-only event history |
| GET | `/api/v1/jobs`, `/jobs/{id}` | Persisted statuses and errors |
| POST | `/api/v1/jobs/{import,forecast,replenish}` | Return actual queued `job_id`, require bearer and Idempotency-Key |

`Page` response shape `{items, total, limit, offset}`. Most pages cap limit at 200 (sales/inventory/forecast allow 500). Invalid inputs produce 422; authorization errors 401; missing entities 404; state conflicts 409; data-store errors 503; internal technical details are not exposed in HTTP responses. Dates use ISO `YYYY-MM-DD`. Job submission returns HTTP 202 and status URL.

## Real UI flows

Overview KPI cards and daily chart, SKU/store/supplier catalog, actual-vs-predicted charts with explicit matching SKU/store filters, validation vs held-out WAPE and permutation importance, filtered inventory history, replenishment table with source-based supplier costs, exception drilldowns with raw/rounded quantity, safety stock, lead-time statistics and append-only lifecycle, operations center with live queued/running/succeeded/failed job statuses.

## Known scope limits

The front-end only calls the backend and displays actual responses; no fake rows or success responses. Backend remains local single-user, token-based (not RBAC). Initial data import is opt-in. The bounded in-process job executor cannot survive restarts; stalled jobs are marked failed on server restart. A live multi-instance production environment would need a durable queue and external authentication. The import endpoint is deliberately limited to a small profile. Full 73M-row persistence and performance are **not measured or claimed**. Source `Forecast` and `PurchaseRecommendation` artifacts remain immutable; generated DB rows are auditable through mappings and triggers.

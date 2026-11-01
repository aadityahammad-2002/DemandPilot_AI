# Phase 1 architecture decisions (AI-generated proposal; requires candidate review)

## Business boundary
Exactly 10 core models: SKU, Store, SalesHistory, InventorySnapshot, Supplier, SupplierSKU, LeadTimeObservation, Forecast, ReorderPolicy, PurchaseRecommendation. Separate technical append-only journals are **not** additional core entities. All daily simulation, forecasting, dashboard, and replenishment logic is intentionally excluded.

## Identifier contract
`SKU-000001`, `STORE-000001`, `SALE-STORE-000001-SKU-000001-2026-01-01`, `FC-STORE-000001-SKU-000001-2026-01`, `PO-2026-000001` are the official examples supplied. Additional forecast records use stable `-V0002` for newer versions and/or `-N000002` for additional rows within the month; the owner must assign a deterministic and consistently sorted sequence using `(forecast_origin,target_date,model_version)` before inserts. Unique business grain still enforced. Recommendation identities get globally unique per-year order ordinals; `-R0002` denotes a new recommendation revision, not a status event. Status changes of the same recommendation retain the base PO ID and add append-only lifecycle events. **Collision-freedom requires centralized deterministic assignment of ordinals/sequence; ID formatting alone does not allocate identities.** All ID ranges cap at their prescribed zero-padding widths; overflow is rejected.

## Dates and lifecycle
Business dates use `DATE`; event timestamps use UTC-aware `TIMESTAMPTZ`. Effective intervals are half-open `[start,end)`, with nullable end. Entity statuses and simple domain checks are enforced by database checks; *cross-record* lifecycle transitions and effective-interval non-overlap require orchestration/transaction validation planned in later phases. The database itself does not yet enforce transition graphs, same-date store/SKU eligibility, or every cross-record consistency rule. This is a known limitation, not a verified feature.

## Append-only journals
`tech_inventory_movement_event`: snapshot FK, timestamp, movement type, signed delta, correlation ID, JSON source payload. Grain: one event per stock movement, not a daily snapshot. Reservations and releases are tracked separately from physical closing stock.

`tech_supplier_observation_event`: observation FK, previous/new state, timestamp, correlation ID, payload (e.g. expected-date updates). `tech_recommendation_lifecycle_event`: recommendation FK, previous/new state, actor, timestamp, correlation ID, payload. Distinct event IDs are assigned externally deterministically from aggregate ID and per-aggregate event sequence. PostgreSQL `BEFORE UPDATE OR DELETE` triggers reject event mutation. PostgreSQL superusers/table owners can bypass enforcement by altering schema; protect roles in any deployed instance. No triggers are used in SQLite tests, so append-only behavior requires PostgreSQL-specific integration testing.

Event `occurred_at` supports audit sequencing, but creation order can differ under concurrency. Later phases must introduce per-aggregate sequencing/locking and transition enforcement. Event journals are foundations, not production-complete event sourcing. Inventory event reconciliation to daily snapshots remains a Phase 2 acceptance gate.

## Value semantics and assumptions
Currency defaults INR (documented assumption); `promotion_multiplier` is a known effect on demand, not a new promotion entity. `observed_available_units` means stock accessible before sale. Physical inventory closing balance excludes reservations, which must be <= closing. `adjustment_units` signed. Lead-time delay can be negative (early receipt), but observed elapsed lead time cannot be negative. Forecast ids are monthly namespace while forecast targets are daily. Current numeric computations store prediction as float and money as fixed precision. Statuses are constrained in SQL, but logical consistency of field combination remains a higher-level test concern.

## Database/layout choice
PostgreSQL 18 is target, SQLAlchemy 2 + Alembic migrations. SQLite is used only for quick isolated contract tests. Full 73m-row facts will be generated in bounded batches in a later phase, persisted as Parquet and queried via DuckDB; no generation is included here.

## Open design obligations
- Complete domain-specific allowed ranges for arbitrary strings (category/region/timezone) and full monetary/currency policy.
- Compare official problem statement's full field list against the generated dictionary; some fields here are design choices.
- Add PostgreSQL integration checks for append-only triggers, composite FK, column checks, transaction rollback and schema migration/downgrade.
- Add effective-dated non-overlap/transition checks and ensure eligibility of policies/recommendations; cannot be guaranteed by this initial migration alone.
- Decide how observational supplier receipts map to store stock movements without a purchase-order entity.
- No generated historical data, models, APIs or frontend exist in this Phase 1 artifact.

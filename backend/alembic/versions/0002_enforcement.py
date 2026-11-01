"""P1-T01 Step 7: enforce temporal, lifecycle and canonical identifiers.

Revision ID: 0002
Revises: 0001

PostgreSQL-specific and transactional. Never rewrites migration 0001 or existing
rows; invalid existing data causes an explicit migration failure/rollback.
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

# Regex matches official identities and documented deterministic extensions.
# Additional row-matching constraints prevent a syntactically valid ID from
# identifying a different store/SKU/date than the relational row itself.
ID_CHECKS = {
    'sku': {
        'ck_sku_id_format': "sku_id ~ '^SKU-[0-9]{6}$' AND sku_id <> 'SKU-000000'",
    },
    'store': {
        'ck_store_id_format': "store_id ~ '^STORE-[0-9]{6}$' AND store_id <> 'STORE-000000'",
    },
    'supplier': {
        'ck_supplier_id_format': "supplier_id ~ '^SUP-[0-9]{6}$' AND supplier_id <> 'SUP-000000'",
    },
    'supplier_sku': {
        'ck_supplier_sku_id_format': "supplier_sku_id ~ '^SSKU-[0-9]{6}$' AND supplier_sku_id <> 'SSKU-000000'",
    },
    'lead_time_observation': {
        'ck_lead_time_observation_id_format': "observation_id ~ '^LTO-[0-9]{6}$' AND observation_id <> 'LTO-000000'",
    },
    'reorder_policy': {
        'ck_reorder_policy_id_format': "policy_id ~ '^POL-[0-9]{6}$' AND policy_id <> 'POL-000000'",
    },
    'sales_history': {
        'ck_sales_history_id_format': "sale_id = 'SALE-' || store_id || '-' || sku_id || '-' || to_char(sales_date, 'YYYY-MM-DD')",
    },
    'inventory_snapshot': {
        'ck_inventory_snapshot_id_format': "inventory_id = 'INV-' || store_id || '-' || sku_id || '-' || to_char(snapshot_date, 'YYYY-MM-DD')",
    },
    'forecast': {
        'ck_forecast_id_format': """forecast_id ~ '^FC-STORE-[0-9]{6}-SKU-[0-9]{6}-[0-9]{4}-(0[1-9]|1[0-2])(-V[0-9]{4})?(-N[0-9]{6})?$'
          AND forecast_id LIKE ('FC-' || store_id || '-' || sku_id || '-' || to_char(forecast_origin, 'YYYY-MM') || '%')
          AND forecast_id !~ '(-V0000|-V0001|-N000000|-N000001)'""",
    },
    'purchase_recommendation': {
        'ck_purchase_recommendation_id_format': """recommendation_id ~ '^PO-[0-9]{4}-[0-9]{6}(-R[0-9]{4})?$'
          AND recommendation_id LIKE ('PO-' || to_char(decision_date, 'YYYY') || '-%')
          AND recommendation_id !~ '(^PO-[0-9]{4}-000000|-R0000$|-R0001$)'""",
    },
}

# Transition graphs are deliberately strict and documented. They are not
# silently inferred from the enum CHECKs in the initial migration.
STATUS_TRANSITIONS = {
    'sku': ('PLANNED:ACTIVE', 'ACTIVE:DISCONTINUED'),
    'store': ('PLANNED:OPEN', 'OPEN:CLOSED'),
    'supplier': ('ACTIVE:SUSPENDED', 'SUSPENDED:ACTIVE', 'ACTIVE:INACTIVE', 'SUSPENDED:INACTIVE'),
    'supplier_sku': ('PENDING:ACTIVE', 'PENDING:TERMINATED', 'ACTIVE:EXPIRED', 'ACTIVE:TERMINATED'),
    'lead_time_observation': ('PLACED:DELAYED', 'PLACED:RECEIVED', 'PLACED:CANCELLED', 'DELAYED:RECEIVED', 'DELAYED:CANCELLED'),
    'forecast': ('GENERATED:VALIDATED', 'GENERATED:REJECTED', 'VALIDATED:PUBLISHED', 'VALIDATED:REJECTED'),
    'reorder_policy': ('DRAFT:ACTIVE', 'DRAFT:DISABLED', 'ACTIVE:SUPERSEDED', 'ACTIVE:DISABLED'),
    'purchase_recommendation': ('GENERATED:VALIDATED', 'GENERATED:REJECTED', 'GENERATED:BLOCKED', 'VALIDATED:ACCEPTED', 'VALIDATED:REJECTED', 'VALIDATED:EXPIRED', 'VALIDATED:BLOCKED'),
}


def _assert_postgresql():
    if op.get_bind().dialect.name != 'postgresql':
        raise RuntimeError('Revision 0002 requires PostgreSQL; no SQLite substitute for lifecycle/exclusion enforcement')


def upgrade():
    _assert_postgresql()
    # Trusted extension required for GiST equality of VARCHAR keys.
    op.execute('CREATE EXTENSION IF NOT EXISTS btree_gist')
    # Declarative checks auto-validate existing data. If they fail, PostgreSQL
    # rolls the complete migration back without deleting or changing rows.
    for table, checks in ID_CHECKS.items():
        for name, definition in checks.items():
            op.create_check_constraint(op.f(name), table, definition)

    op.execute("""ALTER TABLE supplier_sku ADD CONSTRAINT ex_supplier_sku_period
        EXCLUDE USING gist (supplier_id WITH =, sku_id WITH =,
            daterange(valid_from, valid_to, '[)') WITH &&)""")
    op.execute("""ALTER TABLE reorder_policy ADD CONSTRAINT ex_reorder_policy_active_period
        EXCLUDE USING gist (sku_id WITH =, store_id WITH =,
            daterange(effective_from, effective_to, '[)') WITH &&)
        WHERE (status = 'ACTIVE')""")

    # Generic transitions: fires only when status changes; checks direct SQL
    # UPDATE as well as ORM updates. Other columns retain v0001 CHECK rules.
    op.execute("""CREATE FUNCTION yottaflex_guard_state_transition() RETURNS trigger
      LANGUAGE plpgsql AS $$
      DECLARE permitted text[]; valid_states text[];
      BEGIN
        IF NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NEW; END IF;
        CASE TG_TABLE_NAME
          WHEN 'sku' THEN permitted := ARRAY['PLANNED:ACTIVE','ACTIVE:DISCONTINUED']; valid_states := ARRAY['PLANNED','ACTIVE','DISCONTINUED'];
          WHEN 'store' THEN permitted := ARRAY['PLANNED:OPEN','OPEN:CLOSED']; valid_states := ARRAY['PLANNED','OPEN','CLOSED'];
          WHEN 'supplier' THEN permitted := ARRAY['ACTIVE:SUSPENDED','SUSPENDED:ACTIVE','ACTIVE:INACTIVE','SUSPENDED:INACTIVE']; valid_states := ARRAY['ACTIVE','SUSPENDED','INACTIVE'];
          WHEN 'supplier_sku' THEN permitted := ARRAY['PENDING:ACTIVE','PENDING:TERMINATED','ACTIVE:EXPIRED','ACTIVE:TERMINATED']; valid_states := ARRAY['PENDING','ACTIVE','EXPIRED','TERMINATED'];
          WHEN 'lead_time_observation' THEN permitted := ARRAY['PLACED:DELAYED','PLACED:RECEIVED','PLACED:CANCELLED','DELAYED:RECEIVED','DELAYED:CANCELLED']; valid_states := ARRAY['PLACED','DELAYED','RECEIVED','CANCELLED'];
          WHEN 'forecast' THEN permitted := ARRAY['GENERATED:VALIDATED','GENERATED:REJECTED','VALIDATED:PUBLISHED','VALIDATED:REJECTED']; valid_states := ARRAY['GENERATED','VALIDATED','PUBLISHED','REJECTED'];
          WHEN 'reorder_policy' THEN permitted := ARRAY['DRAFT:ACTIVE','DRAFT:DISABLED','ACTIVE:SUPERSEDED','ACTIVE:DISABLED']; valid_states := ARRAY['DRAFT','ACTIVE','SUPERSEDED','DISABLED'];
          WHEN 'purchase_recommendation' THEN permitted := ARRAY['GENERATED:VALIDATED','GENERATED:REJECTED','GENERATED:BLOCKED','VALIDATED:ACCEPTED','VALIDATED:REJECTED','VALIDATED:EXPIRED','VALIDATED:BLOCKED']; valid_states := ARRAY['GENERATED','VALIDATED','ACCEPTED','REJECTED','EXPIRED','BLOCKED'];
          ELSE RAISE EXCEPTION 'unknown transition table: %', TG_TABLE_NAME USING ERRCODE='P0001';
        END CASE;
        -- Invalid enum values are rejected by 0001 CHECK constraints (23514),
        -- preserving the existing contract-test error category.
        IF NOT (NEW.status = ANY(valid_states)) THEN RETURN NEW; END IF;
        IF NOT (OLD.status || ':' || NEW.status = ANY(permitted)) THEN
          RAISE EXCEPTION 'invalid % lifecycle transition: % -> %', TG_TABLE_NAME, OLD.status, NEW.status
             USING ERRCODE='P0001';
        END IF;
        RETURN NEW;
      END $$""")
    for table in STATUS_TRANSITIONS:
        op.execute(f"""CREATE TRIGGER trg_{table}_status_transition BEFORE UPDATE OF status
            ON {table} FOR EACH ROW EXECUTE FUNCTION yottaflex_guard_state_transition()""")

    # Primary identities are immutable after insert: create a new versioned row
    # rather than rewriting historical references. SKU has its own guard below.
    op.execute("""CREATE FUNCTION yottaflex_guard_immutable_business_id() RETURNS trigger
      LANGUAGE plpgsql AS $$
      BEGIN
        IF to_jsonb(NEW)->>TG_ARGV[0] IS DISTINCT FROM to_jsonb(OLD)->>TG_ARGV[0] THEN
          RAISE EXCEPTION 'immutable business ID changed: %.%', TG_TABLE_NAME, TG_ARGV[0]
            USING ERRCODE='P0001';
        END IF;
        RETURN NEW;
      END $$""")
    immutable_ids = {
        'store': 'store_id', 'supplier': 'supplier_id',
        'supplier_sku': 'supplier_sku_id', 'sales_history': 'sale_id',
        'inventory_snapshot': 'inventory_id', 'lead_time_observation': 'observation_id',
        'forecast': 'forecast_id', 'reorder_policy': 'policy_id',
        'purchase_recommendation': 'recommendation_id',
    }
    for table, column in immutable_ids.items():
        op.execute(f"""CREATE TRIGGER trg_{table}_immutable_id BEFORE UPDATE OF {column}
            ON {table} FOR EACH ROW EXECUTE FUNCTION yottaflex_guard_immutable_business_id('{column}')""")

    # SKU consistency and retroactive-change protection. Published positive
    # recommendations cannot silently become ineligible after changing SKU.
    op.execute("""CREATE FUNCTION yottaflex_guard_sku_eligibility() RETURNS trigger
      LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'UPDATE' AND NEW.sku_id <> OLD.sku_id THEN
          RAISE EXCEPTION 'SKU ID is immutable' USING ERRCODE='P0001';
        END IF;
        IF NEW.status = 'DISCONTINUED' AND NEW.discontinue_date IS NULL THEN
          RAISE EXCEPTION 'discontinued SKU requires discontinue_date' USING ERRCODE='P0001';
        END IF;
        IF NEW.discontinue_date IS NOT NULL AND EXISTS (
          SELECT 1 FROM purchase_recommendation pr
          WHERE pr.sku_id = NEW.sku_id AND pr.decision_date >= NEW.discontinue_date
            AND pr.recommended_quantity > 0
        ) THEN
          RAISE EXCEPTION 'SKU discontinuation conflicts with existing positive recommendation'
            USING ERRCODE='P0001';
        END IF;
        RETURN NEW;
      END $$""")
    op.execute("""CREATE TRIGGER trg_sku_eligibility BEFORE INSERT OR UPDATE
        ON sku FOR EACH ROW EXECUTE FUNCTION yottaflex_guard_sku_eligibility()""")

    # Serialize recommendation insert/update with SKU state changes. A
    # discontinued SKU can still yield a BLOCKED/zero-quantity audited result.
    # FOR SHARE conflicts with concurrent SKU UPDATE locks, avoiding races.
    op.execute("""CREATE FUNCTION yottaflex_guard_recommendation_eligibility() RETURNS trigger
      LANGUAGE plpgsql AS $$
      DECLARE product_status text; ending date;
      BEGIN
        SELECT status, discontinue_date INTO product_status, ending
          FROM sku WHERE sku_id = NEW.sku_id FOR SHARE;
        IF NOT FOUND THEN RETURN NEW; END IF; -- FK reports nonexistent SKU
        IF product_status = 'DISCONTINUED' OR
           (ending IS NOT NULL AND NEW.decision_date >= ending) THEN
          IF NEW.status <> 'BLOCKED' OR NEW.recommended_quantity <> 0 OR
             NOT (NEW.reason_codes::jsonb ? 'DISCONTINUED_SKU') THEN
            RAISE EXCEPTION 'discontinued SKU requires BLOCKED, quantity 0, DISCONTINUED_SKU reason'
              USING ERRCODE='P0001';
          END IF;
        END IF;
        RETURN NEW;
      END $$""")
    op.execute("""CREATE TRIGGER trg_recommendation_eligibility BEFORE INSERT OR UPDATE
        ON purchase_recommendation FOR EACH ROW EXECUTE FUNCTION yottaflex_guard_recommendation_eligibility()""")

    # Automatic lifecycle events for recommendation creation/state transitions.
    # Existing append-only journal guards from 0001 remain untouched.
    op.execute("""CREATE FUNCTION yottaflex_log_recommendation_lifecycle() RETURNS trigger
      LANGUAGE plpgsql AS $$
      DECLARE next_n integer; is_transition boolean; old_status text := NULL;
      BEGIN
        is_transition := TG_OP = 'INSERT';
        IF TG_OP = 'UPDATE' THEN
          is_transition := NEW.status IS DISTINCT FROM OLD.status;
          old_status := OLD.status;
        END IF;
        IF NOT is_transition THEN RETURN NEW; END IF;
        SELECT COALESCE(MAX(substring(event_id from '-S([0-9]{6})$')::integer), 0) + 1
          INTO next_n FROM tech_recommendation_lifecycle_event
          WHERE recommendation_id = NEW.recommendation_id;
        IF next_n > 999999 THEN
          RAISE EXCEPTION 'recommendation event sequence exhausted' USING ERRCODE='P0001';
        END IF;
        INSERT INTO tech_recommendation_lifecycle_event
          (event_id, recommendation_id, occurred_at, previous_status,
           new_status, actor, correlation_id, payload)
        VALUES
          ('EV-REC-' || NEW.recommendation_id || '-S' || lpad(next_n::text,6,'0'),
           NEW.recommendation_id, clock_timestamp(),
           old_status,
           NEW.status, current_user, 'PG-TRIGGER',
           json_build_object('source','yottaflex_0002','operation',TG_OP));
        RETURN NEW;
      END $$""")
    op.execute("""CREATE TRIGGER trg_recommendation_lifecycle_audit
       AFTER INSERT OR UPDATE OF status ON purchase_recommendation
       FOR EACH ROW EXECUTE FUNCTION yottaflex_log_recommendation_lifecycle()""")

    # Supplier state transitions are similarly journaled; inventory movements
    # are explicit physical events, and are not synthesized from every UPDATE.
    op.execute("""CREATE FUNCTION yottaflex_log_supplier_observation() RETURNS trigger
      LANGUAGE plpgsql AS $$
      DECLARE next_n integer; old_status text := NULL;
      BEGIN
        IF TG_OP='UPDATE' THEN
          IF NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NEW; END IF;
          old_status := OLD.status;
        END IF;
        SELECT COALESCE(MAX(substring(event_id from '-S([0-9]{6})$')::integer),0)+1
          INTO next_n FROM tech_supplier_observation_event
          WHERE observation_id=NEW.observation_id;
        IF next_n > 999999 THEN RAISE EXCEPTION 'supplier event sequence exhausted' USING ERRCODE='P0001'; END IF;
        INSERT INTO tech_supplier_observation_event
          (event_id, observation_id, occurred_at, previous_status, new_status, correlation_id, payload)
        VALUES ('EV-SUP-' || NEW.observation_id || '-S' || lpad(next_n::text,6,'0'),
          NEW.observation_id, clock_timestamp(),
          old_status,
          NEW.status, 'PG-TRIGGER',
          json_build_object('source','yottaflex_0002','operation',TG_OP));
        RETURN NEW;
      END $$""")
    op.execute("""CREATE TRIGGER trg_supplier_observation_lifecycle_audit
        AFTER INSERT OR UPDATE OF status ON lead_time_observation
        FOR EACH ROW EXECUTE FUNCTION yottaflex_log_supplier_observation()""")


def downgrade():
    _assert_postgresql()
    # Downgrade is explicit and reverses only revision 0002. No existing rows
    # are deleted; the event journal and 0001 triggers remain intact.
    op.execute('DROP TRIGGER IF EXISTS trg_supplier_observation_lifecycle_audit ON lead_time_observation')
    op.execute('DROP FUNCTION IF EXISTS yottaflex_log_supplier_observation()')
    op.execute('DROP TRIGGER IF EXISTS trg_recommendation_lifecycle_audit ON purchase_recommendation')
    op.execute('DROP FUNCTION IF EXISTS yottaflex_log_recommendation_lifecycle()')
    op.execute('DROP TRIGGER IF EXISTS trg_recommendation_eligibility ON purchase_recommendation')
    op.execute('DROP FUNCTION IF EXISTS yottaflex_guard_recommendation_eligibility()')
    for table in ('store','supplier','supplier_sku','sales_history','inventory_snapshot',
                  'lead_time_observation','forecast','reorder_policy','purchase_recommendation'):
        op.execute(f'DROP TRIGGER IF EXISTS trg_{table}_immutable_id ON {table}')
    op.execute('DROP FUNCTION IF EXISTS yottaflex_guard_immutable_business_id()')
    op.execute('DROP TRIGGER IF EXISTS trg_sku_eligibility ON sku')
    op.execute('DROP FUNCTION IF EXISTS yottaflex_guard_sku_eligibility()')
    for table in reversed(tuple(STATUS_TRANSITIONS)):
        op.execute(f'DROP TRIGGER IF EXISTS trg_{table}_status_transition ON {table}')
    op.execute('DROP FUNCTION IF EXISTS yottaflex_guard_state_transition()')
    op.execute('ALTER TABLE reorder_policy DROP CONSTRAINT IF EXISTS ex_reorder_policy_active_period')
    op.execute('ALTER TABLE supplier_sku DROP CONSTRAINT IF EXISTS ex_supplier_sku_period')
    for table, checks in ID_CHECKS.items():
        for name in checks:
            op.drop_constraint(name, table, type_='check')
    # btree_gist is intentionally left installed: other applications may use it.

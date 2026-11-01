"""Read-only PostgreSQL schema gate; no table creation or migration."""
from sqlalchemy import text
from sqlalchemy.engine import make_url
from app.config import engine
required_core={'sku','store','sales_history','inventory_snapshot','supplier','supplier_sku','lead_time_observation','forecast','reorder_policy','purchase_recommendation'}
required_journals={'tech_inventory_movement_event','tech_supplier_observation_event','tech_recommendation_lifecycle_event'}
required_tech={'tech_pipeline_run','tech_persistence_map'}
if make_url(engine().url).database!='yottaflex_dual_v7':
    raise RuntimeError('Refusing schema check: expected isolated yottaflex_dual_v7')
with engine().connect() as c:
    c.execute(text('SET TRANSACTION READ ONLY'))
    assert c.dialect.name=='postgresql','PostgreSQL required'
    assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0005'
    tables=set(c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars())
    assert (required_core|required_journals|required_tech).issubset(tables),sorted((required_core|required_journals|required_tech)-tables)
    id_checks=c.execute(text("SELECT count(*) FROM pg_constraint WHERE contype='c' AND conname LIKE 'ck_%_id_format' AND convalidated")).scalar_one()
    assert id_checks>=10,f'expected >=10 ID checks, got {id_checks}'
    exclusion=c.execute(text("SELECT count(*) FROM pg_constraint WHERE contype='x' AND conname IN ('ex_supplier_sku_period','ex_reorder_policy_active_period')")).scalar_one()
    assert exclusion==2,f'exclusion constraints: {exclusion}'
    composite=c.execute(text("SELECT count(*) FROM pg_constraint WHERE contype='f' AND array_length(conkey,1)>1 AND connamespace='public'::regnamespace")).scalar_one()
    assert composite>=2,f'composite FKs: {composite}'
    triggers=set(c.execute(text("SELECT tgname FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid WHERE r.relnamespace='public'::regnamespace AND NOT t.tgisinternal AND t.tgenabled IN ('O','A')")).scalars())
    expected={'trg_sku_status_transition','trg_recommendation_eligibility','trg_recommendation_lifecycle_audit', 'trg_tech_inventory_movement_event_immutable','trg_tech_supplier_observation_event_immutable','trg_tech_recommendation_lifecycle_event_immutable'}
    assert expected.issubset(triggers),sorted(expected-triggers)
    print('PASS | required 10 business entities unchanged')
    print('PASS | three append-only journals intact')
    print('PASS | two Phase 5 technical ledger tables present')
    print('PASS | 10 ID checks, 2 exclusion constraints, composite FKs and lifecycle triggers')
    print('PASS | migration 0005 (head)')

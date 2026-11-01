"""Static migration contracts; PostgreSQL execution must be verified on laptop."""
import importlib.util
from pathlib import Path
from app.models import Base, CORE_ENTITIES, TECH_ENTITIES
from app.lifecycle import TRANSITIONS, can_transition
from app.postgres_contracts import ID_CHECK_NAMES, EXCLUSION_NAMES, IMMUTABLE_CORE_ID_COLUMNS, JOURNALS
from app.ids import store_id, sku_id, forecast_id, sale_id
from datetime import date
import pytest

file = Path(__file__).resolve().parents[1] / 'alembic' / 'versions' / '0002_enforcement.py'
spec = importlib.util.spec_from_file_location('migration_0002', file)
rev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rev)


def test_0002_directly_follows_0001():
    assert rev.revision == '0002' and rev.down_revision == '0001'
    assert len(CORE_ENTITIES) == 10 and len(TECH_ENTITIES) == 3


def test_postgres_contract_catalog_matches_0002():
    assert {table: next(iter(rules)) for table, rules in rev.ID_CHECKS.items()} == ID_CHECK_NAMES
    assert set(EXCLUSION_NAMES) == {'supplier_sku', 'reorder_policy'}
    assert set(ID_CHECK_NAMES) == {model.__tablename__ for model in CORE_ENTITIES}
    for model in CORE_ENTITIES:
        assert IMMUTABLE_CORE_ID_COLUMNS[model.__tablename__] in model.__table__.columns
    assert set(JOURNALS) == {model.__tablename__ for model in TECH_ENTITIES}


def test_0001_metadata_not_contaminated_by_0002_constraints():
    # 0001 uses the *current* Base.metadata, so v0002 DDL must be kept outside
    # metadata until 0001 no longer depends on it.
    constraint_names = {c.name for table in Base.metadata.tables.values() for c in table.constraints}
    assert not constraint_names.intersection(ID_CHECK_NAMES.values())
    assert not constraint_names.intersection(EXCLUSION_NAMES.values())


def test_transition_mapping_matches_migration():
    assert set(TRANSITIONS) == set(rev.STATUS_TRANSITIONS)
    for table, strings in rev.STATUS_TRANSITIONS.items():
        assert TRANSITIONS[table] == frozenset(tuple(s.split(':')) for s in strings)
    assert can_transition('sku','PLANNED','ACTIVE')
    assert not can_transition('sku','ACTIVE','PLANNED')
    assert can_transition('purchase_recommendation','GENERATED','BLOCKED')
    assert not can_transition('purchase_recommendation','BLOCKED','ACCEPTED')


def test_id_helpers_reject_zero_ids_and_allow_official_extensions():
    with pytest.raises(ValueError): sale_id('STORE-000000',sku_id(1),date(2026,1,1))
    with pytest.raises(ValueError): sale_id(store_id(1),'SKU-000000',date(2026,1,1))
    assert forecast_id(store_id(1),sku_id(1),'2026-01',version=2,sequence=2).endswith('-V0002-N000002')

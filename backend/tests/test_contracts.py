import pytest
from datetime import date
from sqlalchemy import create_engine,inspect,insert
from sqlalchemy.exc import IntegrityError
from app.models import Base, CORE_ENTITIES, TECH_ENTITIES, SKU, Store, Supplier, SupplierSKU, LeadTimeObservation
from app.ids import sku_id, store_id, sale_id, forecast_id, recommendation_id, numbered
from app.dictionary import build

@pytest.fixture()
def db():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    with engine.connect() as connection: connection.exec_driver_sql('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()

def test_core_exactly_ten():
    assert len(CORE_ENTITIES)==10
    assert {x.__name__ for x in CORE_ENTITIES}=={'SKU','Store','SalesHistory','InventorySnapshot','Supplier','SupplierSKU','LeadTimeObservation','Forecast','ReorderPolicy','PurchaseRecommendation'}
    assert len(TECH_ENTITIES)==3

def test_id_contracts():
    assert sku_id(1)=='SKU-000001'
    assert store_id(1)=='STORE-000001'
    assert sale_id(store_id(1),sku_id(1),date(2026,1,1))=='SALE-STORE-000001-SKU-000001-2026-01-01'
    assert forecast_id(store_id(1),sku_id(1),'2026-01')=='FC-STORE-000001-SKU-000001-2026-01'
    assert forecast_id(store_id(1),sku_id(1),'2026-01',version=2,sequence=3).endswith('-V0002-N000003')
    assert recommendation_id(2026,1)=='PO-2026-000001'
    assert recommendation_id(2026,1,revision=2)=='PO-2026-000001-R0002'
    assert len({forecast_id(store_id(1),sku_id(1),'2026-01',version=v,sequence=s) for v in range(1,3) for s in range(1,4)})==6
    with pytest.raises(ValueError): sku_id(0)
    with pytest.raises(ValueError): numbered('SKU',1000000)

def test_dictionary_all_fields():
    d=build()
    assert d['core_entity_count']==10
    for entry in list(d['core_entities'].values())+list(d['supporting_technical_artifacts'].values()):
        for field in entry['fields']:
            assert {'field','type','required','allowed_values','units','unique','validation_rules'} <= field.keys()
            assert field['validation_rules']

def masters(conn):
    conn.execute(insert(SKU).values(sku_id=sku_id(1),category='A',unit='each',launch_date=date(2025,1,1),status='ACTIVE',base_demand_rate=1,storage_units_per_item=1))
    conn.execute(insert(Store).values(store_id=store_id(1),region='South',timezone='Asia/Kolkata',open_date=date(2025,1,1),status='OPEN',capacity_units=100))
    conn.execute(insert(Supplier).values(supplier_id='SUP-000001',supplier_name='Supplier',status='ACTIVE',effective_from=date(2025,1,1),reliability_factor=.9))

def test_fk_rejects_orphan(db):
    with pytest.raises(IntegrityError):
        with db.begin() as conn:
            conn.execute(insert(SupplierSKU).values(supplier_sku_id='SSKU-000001',supplier_id='SUP-999999',sku_id=sku_id(1),valid_from=date(2025,1,1),status='ACTIVE',base_lead_time_days=2,moq_units=5,pack_size_units=5,unit_cost=2,supplier_capacity_units=100))

def test_duplicate_primary_key(db):
    with db.begin() as conn: masters(conn)
    with pytest.raises(IntegrityError):
        with db.begin() as conn: masters(conn)

def test_invalid_enum(db):
    with pytest.raises(IntegrityError):
        with db.begin() as conn:
            conn.execute(insert(SKU).values(sku_id=sku_id(1),category='A',unit='each',launch_date=date(2025,1,1),status='NOT_REAL',base_demand_rate=1,storage_units_per_item=1))

def test_invalid_dates(db):
    with pytest.raises(IntegrityError):
        with db.begin() as conn:
            conn.execute(insert(SKU).values(sku_id=sku_id(1),category='A',unit='each',launch_date=date(2026,1,1),discontinue_date=date(2025,1,1),status='ACTIVE',base_demand_rate=1,storage_units_per_item=1))

def test_negative_lead_time(db):
    with db.begin() as conn:
        masters(conn)
        conn.execute(insert(SupplierSKU).values(supplier_sku_id='SSKU-000001',supplier_id='SUP-000001',sku_id=sku_id(1),valid_from=date(2025,1,1),status='ACTIVE',base_lead_time_days=2,moq_units=5,pack_size_units=5,unit_cost=2,supplier_capacity_units=100))
    with pytest.raises(IntegrityError):
        with db.begin() as conn:
            conn.execute(insert(LeadTimeObservation).values(observation_id='LTO-000001',supplier_sku_id='SSKU-000001',order_date=date(2026,1,8),expected_receipt_date=date(2026,1,5),status='PLACED'))

def test_tables_present(db):
    actual=set(inspect(db).get_table_names())
    assert actual=={e.__table__.name for e in CORE_ENTITIES+TECH_ENTITIES}

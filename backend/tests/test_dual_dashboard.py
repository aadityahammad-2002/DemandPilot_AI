"""Two-dashboard read contracts; SQLite intentionally not PostgreSQL acceptance."""
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.integration.api import create_app
from test_phase5_integration import database, DATA
from app.integration.persistence import import_dataset


def app_client(database, tmp_path):
    config={'database_url':'unused','cors_origins':['http://localhost:5173'],
            'data_root':DATA,'artifacts_root':tmp_path,'max_pairs':36,'allow_demo_import':False}
    return TestClient(create_app(config,database,init_on_start=False))


def test_no_bearer_token_and_schema_derived_from_real_metadata(database,tmp_path):
    with app_client(database,tmp_path) as client:
        assert client.get('/api/v1/stores').status_code==200
        schema=client.get('/api/v1/schema-metadata')
        assert schema.status_code==200
        entities={r['name']:r for r in schema.json()['entities']}
        assert len(entities)==10
        assert {'sku','forecast','purchase_recommendation','supplier_sku'} <= entities.keys()
        fks=[c for c in entities['supplier_sku']['columns'] if c['name']=='sku_id'][0]['foreign_keys']
        assert fks==['sku.sku_id']


def test_operations_summary_never_blends_unrelated_runs(database,tmp_path):
    with Session(database) as db,db.begin():
        import_dataset(db,DATA)
    # The summary should not aggregate another run and should not invent values.
    with app_client(database,tmp_path) as client:
        blank=client.get('/api/v1/operations-summary').json()
        assert blank['available'] is False and blank['recommended_units']==0
        scoped=client.get('/api/v1/operations-summary?forecast_run_id=RUN-A').json()
        assert scoped['available'] is False and scoped['total_recommendations']==0
        assert client.get('/api/v1/operations-summary?from_date=2026-12-31&to_date=2026-01-01').status_code==422


def test_health_and_inputs_still_validate_without_auth(database,tmp_path):
    with app_client(database,tmp_path) as client:
        assert client.get('/health').json()['status']=='ok'
        assert client.get('/api/v1/forecasts?limit=10000').status_code==422
        assert client.get('/api/v1/recommendations?from_date=2026-05-01&to_date=2026-01-01').status_code==422
        assert client.post('/api/v1/jobs/import',json={}).status_code==403

def test_operations_summary_aggregates_only_one_real_run(database,tmp_path):
    from datetime import date
    from sqlalchemy import select
    from app.models import ReorderPolicy,SupplierSKU,PurchaseRecommendation
    with Session(database) as db,db.begin():
        import_dataset(db,DATA)
        p=db.scalars(select(ReorderPolicy).limit(1)).first()
        c=db.scalars(select(SupplierSKU).where(SupplierSKU.sku_id==p.sku_id).limit(1)).first()
        assert c is not None
        common=dict(policy_id=p.policy_id,sku_id=p.sku_id,store_id=p.store_id,
          supplier_sku_id=c.supplier_sku_id,decision_date=date(2026,1,20),
          inventory_date=date(2026,1,20),inventory_position=5,lead_time_demand=15.0,
          raw_quantity=15,reason_codes=[],rule_version='test-read-only-summary',decision_trace={})
        db.add(PurchaseRecommendation(recommendation_id='REC-DASH-A',forecast_run_id='FORECAST-A',
          recommended_quantity=12,status='GENERATED',**common))
        db.add(PurchaseRecommendation(recommendation_id='REC-DASH-B',forecast_run_id='FORECAST-B',
          recommended_quantity=0,status='BLOCKED',**common))
    with app_client(database,tmp_path) as client:
        a=client.get('/api/v1/operations-summary?forecast_run_id=FORECAST-A').json()
        b=client.get('/api/v1/operations-summary?forecast_run_id=FORECAST-B').json()
        assert a['available'] and a['positive_recommendations']==1 and a['blocked_recommendations']==0
        assert a['recommended_units']==12 and a['total_recommendations']==1
        assert b['total_recommendations']==1 and b['blocked_recommendations']==1
        assert b['recommended_units']==0


def test_run_catalog_uses_persisted_source_ids_not_pipeline_job_ids(database,tmp_path):
    """A JOB- identifier must never be used as the FC-RUN business filter."""
    from datetime import date,datetime,timezone
    from sqlalchemy import select
    from app.models import Forecast,ReorderPolicy,SupplierSKU,PurchaseRecommendation
    from app.integration.types import PipelineRun
    with Session(database) as db,db.begin():
        import_dataset(db,DATA)
        policy=db.scalars(select(ReorderPolicy).limit(1)).first()
        contract=db.scalars(select(SupplierSKU).where(SupplierSKU.sku_id==policy.sku_id).limit(1)).first()
        db.add(Forecast(forecast_id='FC-TEST-1',sku_id=policy.sku_id,store_id=policy.store_id,
            forecast_origin=date(2026,1,20),target_date=date(2026,1,21),predicted_units=10,
            run_id='FC-RUN-CATALOG-A',model_version='moving_average',status='GENERATED'))
        db.add(PurchaseRecommendation(recommendation_id='PO-2026-900001',policy_id=policy.policy_id,
            sku_id=policy.sku_id,store_id=policy.store_id,supplier_sku_id=contract.supplier_sku_id,
            decision_date=date(2026,1,20),forecast_run_id='FC-RUN-CATALOG-A',
            inventory_date=date(2026,1,20),inventory_position=5,lead_time_demand=15,
            raw_quantity=12,recommended_quantity=12,status='GENERATED',reason_codes=[],
            rule_version='test',decision_trace={}))
        stamp=datetime(2026,1,22,tzinfo=timezone.utc)
        db.add(PipelineRun(job_id='JOB-FORECAST-A',idempotency_key='FORECAST-CATALOG-A',
             kind='FORECAST',status='SUCCEEDED',finished_at=stamp,
             result={'source_run_id':'FC-RUN-CATALOG-A','parameters':{}}))
        db.add(PipelineRun(job_id='JOB-REPLENISH-A',idempotency_key='REPLENISH-CATALOG-A',
             kind='REPLENISH',status='SUCCEEDED',finished_at=stamp,
             result={'source_run_id':'RP-RUN-CATALOG-A',
                     'parameters':{'forecast_job_id':'JOB-FORECAST-A'}}))
        from app.integration.types import PersistenceMap
        db.flush()  # Ensure parent technical job exists before FK-backed mapping.
        db.add(PersistenceMap(source_key='R:'+('a'*64),source_hash='a'*64,
              entity_type='PurchaseRecommendation',database_id='PO-2026-900001',
              job_id='JOB-REPLENISH-A'))
    with app_client(database,tmp_path) as client:
        data=client.get('/api/v1/run-catalog').json()
        assert data['forecast_runs'][0]['run_id']=='FC-RUN-CATALOG-A'
        assert data['forecast_runs'][0]['job_id']=='JOB-FORECAST-A'
        assert data['forecast_runs'][0]['has_recommendations'] is True
        assert data['replenishment_runs'][0]['job_id']=='JOB-REPLENISH-A'
        assert data['replenishment_runs'][0]['forecast_run_id']=='FC-RUN-CATALOG-A'
        assert data['latest_replenishment_forecast_run_id']=='FC-RUN-CATALOG-A'
        assert data['coverage']['recommendations']=={'from_date':'2026-01-20','to_date':'2026-01-20'}
        overview=client.get('/api/v1/operations-summary').json()
        assert overview['available'] and overview['recommended_units']==12
        assert overview['forecast_run_id']=='FC-RUN-CATALOG-A'
        assert client.get('/api/v1/recommendations?forecast_run_id=JOB-FORECAST-A').json()['total']==0
        assert client.get('/api/v1/recommendations?forecast_run_id=FC-RUN-CATALOG-A').json()['total']==1
        assert client.get('/api/v1/recommendations?outcome=positive').json()['total']==1
        assert client.get('/api/v1/recommendations?replenishment_job_id=JOB-REPLENISH-A').json()['total']==1
        assert client.get('/api/v1/recommendations?replenishment_job_id=JOB-UNKNOWN').json()['total']==0
        assert client.get('/api/v1/operations-summary?replenishment_job_id=JOB-REPLENISH-A').json()['total_recommendations']==1
        assert client.get('/api/v1/recommendations?outcome=blocked').json()['total']==0
        assert client.get('/api/v1/recommendations?outcome=zero').json()['total']==0
        assert client.get('/api/v1/recommendations?outcome=unknown').status_code==422
        assert client.get('/api/v1/forecasts?from_date=2026-01-21&to_date=2026-01-21').json()['total']==1
        assert client.get('/api/v1/forecasts?from_date=2026-01-22').json()['total']==0


def test_run_catalog_empty_data_is_explicit(database,tmp_path):
    with app_client(database,tmp_path) as client:
        data=client.get('/api/v1/run-catalog').json()
        assert data['forecast_runs']==[]
        assert data['latest_replenishment_forecast_run_id'] is None
        assert data['coverage']['forecast']=={'from_date':None,'to_date':None}


def test_backend_env_respects_exported_overrides(monkeypatch):
    import importlib
    settings_module=importlib.import_module('app.integration.settings')
    visited=[]
    monkeypatch.setattr(settings_module,'load_dotenv',lambda *a,**k:visited.append((a,k)))
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://local:secret@127.0.0.1/isolated')
    settings_module.settings.cache_clear()
    try:
        assert settings_module.settings()['database_url'].endswith('/isolated')
        assert visited and visited[0][1]['override'] is False
    finally:settings_module.settings.cache_clear()

"""Phase 5 API and persistence contract tests (SQLite in CI; PG gate on laptop)."""
from __future__ import annotations
from datetime import date
from pathlib import Path
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,event,select,func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.models import Base,SKU,Forecast,PurchaseRecommendation,RecommendationLifecycleEvent
from app.integration.types import PipelineRun,PersistenceMap,IntegrationBase
from app.integration.api import create_app
from app.integration.jobs import create_job,job_identity
from app.integration.persistence import import_dataset,import_forecasts,import_recommendations,_free_fc_id

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'examples'/'smoke_run'
# Worktree fixture paths are supplied by the test runner through environment;
# use located artifacts if already available, never generate full-scale data.
FC=Path(__import__('os').getenv('YOTTA_TEST_FORECAST_ROOT',str(ROOT/'evidence'/'test_phase5_forecast')))
RP=Path(__import__('os').getenv('YOTTA_TEST_REPL_ROOT',str(ROOT/'evidence'/'test_phase5_repl')))
TOKEN='test-token-do-not-use-in-real-setup-12345'

@pytest.fixture()
def database():
    engine=create_engine('sqlite+pysqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def fk_on(conn,record):conn.execute('PRAGMA foreign_keys=ON')
    # SQLite does not implement PostgreSQL DATE - DATE integer arithmetic.
    # Only this PostgreSQL-specific CHECK is excluded during SQLite test DDL.
    from app.models import LeadTimeObservation
    table=LeadTimeObservation.__table__
    unsupported=next(c for c in table.constraints if getattr(c,'name','').endswith('lead_derived'))
    table.constraints.discard(unsupported)
    try:Base.metadata.create_all(engine)
    finally:table.constraints.add(unsupported)
    IntegrationBase.metadata.create_all(engine)
    try:yield engine
    finally:IntegrationBase.metadata.drop_all(engine);Base.metadata.drop_all(engine);engine.dispose()

@pytest.fixture()
def client(database,tmp_path):
    settings={'database_url':'unused','api_token':TOKEN,'cors_origins':['http://localhost:5173'],
              'data_root':DATA,'artifacts_root':tmp_path,'max_pairs':36,'allow_demo_import':True}
    api=create_app(settings,database,init_on_start=False)
    with TestClient(api) as client:yield client

def headers():return {'Authorization':'Bearer '+TOKEN}

def make_job(database,kind='FORECAST'):
    key='test_'+kind+'_'+str(__import__('uuid').uuid4())
    job_id=job_identity(kind,key)
    with Session(database) as db,db.begin():
        db.add(PipelineRun(job_id=job_id,idempotency_key=key,kind=kind,status='SUCCEEDED',result={}))
    return job_id

def test_trusted_local_demo_does_not_require_auth(client):
    assert client.get('/health').status_code==200
    assert client.get('/api/v1/skus').status_code==200
    assert client.get('/api/v1/skus',headers={'Authorization':'Bearer incorrect'}).status_code==200

def test_response_contract_and_empty_dashboard(client):
    assert client.get('/api/v1/overview',headers=headers()).json()['sku_count']==0
    page=client.get('/api/v1/skus',headers=headers()).json()
    assert page=={'items':[],'total':0,'limit':30,'offset':0}
    assert client.get('/api/v1/model-metrics',headers=headers()).json()['available'] is False
    assert client.get('/api/v1/skus?limit=999999',headers=headers()).status_code==422
    assert client.get('/api/v1/sales?from_date=2026-10-01&to_date=2026-01-01',headers=headers()).status_code==422

def test_verified_seed_and_all_ten_tables(database,client):
    with Session(database) as db,db.begin():counts=import_dataset(db,DATA)
    assert counts['sku']==12 and counts['store']==3
    assert counts['sales_history']==1164 and counts['inventory_snapshot']==1164
    with Session(database) as db,db.begin():counts2=import_dataset(db,DATA)
    assert all(n==0 for n in counts2.values())
    sku=client.get('/api/v1/skus?q=SKU-000001',headers=headers()).json()
    assert sku['total']==1
    summary=client.get('/api/v1/overview',headers=headers()).json()
    assert summary['sku_count']==12 and summary['store_count']==3
    assert summary['total_units_sold']>=0
    for path in ['stores','suppliers','supplier-contracts','lead-times','policies','sales','inventory']:
        response=client.get('/api/v1/'+path,headers=headers());assert response.status_code==200,(path,response.text)
        assert response.json()['total']>0
    assert client.get('/api/v1/demand-trend',headers=headers()).status_code==200

@pytest.mark.skipif(not FC.is_dir() or not RP.is_dir(),reason='Optional frozen Phase 3/4 fixture artifacts not provided')
def test_persist_frozen_phase3_and_phase4_idempotently(database,client):
    with Session(database) as db,db.begin():import_dataset(db,DATA)
    fj=make_job(database,'FORECAST')
    rj=make_job(database,'REPLENISH')
    with Session(database) as db,db.begin():result=import_forecasts(db,FC,fj)
    assert result['inserted_forecasts']>0
    with Session(database) as db,db.begin():replay=import_forecasts(db,FC,fj)
    assert replay['inserted_forecasts']==0
    with Session(database) as db,db.begin():result=import_recommendations(db,RP,rj)
    assert result['inserted_recommendations']>0
    with Session(database) as db,db.begin():again=import_recommendations(db,RP,rj)
    assert again['inserted_recommendations']==0
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(Forecast))>0
        assert db.scalar(select(func.count()).select_from(PurchaseRecommendation))==36
        blocked=db.scalars(select(PurchaseRecommendation).where(PurchaseRecommendation.status=='BLOCKED')).all()
        assert len(blocked)==3
        for rec in blocked:
            sku=db.get(SKU,rec.sku_id)
            if sku.status=='DISCONTINUED':assert 'DISCONTINUED_SKU' in rec.reason_codes
    response=client.get('/api/v1/recommendations',headers=headers()).json()
    assert response['total']==36
    first=response['items'][0]
    detail=client.get('/api/v1/recommendations/'+first['recommendation_id'],headers=headers()).json()
    assert 'decision_trace' in detail['recommendation'] and 'contract' in detail
    assert 'estimated_cost' in first
    assert client.get('/api/v1/forecasts',headers=headers()).json()['total']>0
    assert client.get('/api/v1/exceptions',headers=headers()).json()['total']==3

def test_job_idempotency_rejects_parameter_change(database):
    cfg={'data_root':DATA,'artifacts_root':Path('/tmp'), 'allow_demo_import':True,'max_pairs':36}
    a=create_job(database,'FORECAST','some-stable-key-one',{},cfg,submit=False)
    b=create_job(database,'FORECAST','some-stable-key-one',{},cfg,submit=False)
    assert a[0]==b[0] and a[2] is True and b[2] is False
    with pytest.raises(ValueError):create_job(database,'FORECAST','some-stable-key-one',{'changed':True},cfg,submit=False)
    with pytest.raises(ValueError):create_job(database,'IMPORT','some-stable-key-one',{},cfg,submit=False)

def test_forecast_collision_allocates_valid_version(database):
    with Session(database) as db,db.begin():
        row={'forecast_id':'FC-STORE-000001-SKU-000001-2026-01','store_id':'STORE-000001','sku_id':'SKU-000001','forecast_origin':'2026-01-21'}
        assert _free_fc_id(db,row,set())==row['forecast_id']
        assert _free_fc_id(db,row,{row['forecast_id']})=='FC-STORE-000001-SKU-000001-2026-01-V0002'

def test_migration_is_add_only():
    path=Path(__file__).resolve().parents[1]/'alembic'/'versions'/'0003_phase5_integration.py'
    s=path.read_text(encoding='utf-8')
    assert "down_revision = '0002'" in s
    assert "op.create_table('tech_pipeline_run'" in s
    assert "op.create_table('tech_persistence_map'" in s
    assert 'DROP TABLE sku' not in s

def test_full_job_sequence_with_real_small_artifacts(database,tmp_path,client):
    """Runs accepted Phase 2->3->4 algorithms and persists without PostgreSQL.
    SQL triggers remain separately checked by PostgreSQL acceptance script.
    """
    from app.integration.jobs import execute_job
    settings={'database_url':'unused','api_token':TOKEN,'cors_origins':['http://localhost:5173'],
              'data_root':DATA,'artifacts_root':tmp_path,'max_pairs':36,'allow_demo_import':True}
    for kind,key,params in [('IMPORT','phase5-integration-import-0001',{}),
                            ('FORECAST','phase5-integration-forecast-0001',{}),
                            ('REPLENISH','phase5-integration-repl-0001',{'forecast_job_id':job_identity('FORECAST','phase5-integration-forecast-0001')})]:
        job,status,new=create_job(database,kind,key,params,settings,submit=False)
        execute_job(database,job,kind,params,settings)
        with Session(database) as db:
            item=db.get(PipelineRun,job)
            assert item.status=='SUCCEEDED',(kind,item.error_message)
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(Forecast))==138
        assert db.scalar(select(func.count()).select_from(PurchaseRecommendation))==36
        assert db.scalar(select(func.count()).select_from(PersistenceMap))==174


def test_local_demo_settings_and_capabilities(client):
    from app.integration.settings import validate_settings
    cfg={'database_url':'postgresql+psycopg://example:example@127.0.0.1/example',
         'cors_origins':['http://localhost:5173'],'max_pairs':36}
    validate_settings(cfg)
    assert client.get('/api/v1/capabilities').status_code==200
    assert client.get('/api/v1/capabilities').json()=={
        'allow_demo_import':True,'max_pairs':36,'full_scale_deferred':True}

def test_successful_job_replay_preserves_idempotency(database):
    from app.integration.jobs import _commit_success
    key='phase5-repeat-finished-job'
    params={'forecast_job_id':'JOB-FIXTURE'}
    cfg={'data_root':DATA,'artifacts_root':DATA,'max_pairs':36,'allow_demo_import':True}
    job,status,created=create_job(database,'REPLENISH',key,params,cfg,submit=False)
    assert created and status=='QUEUED'
    with Session(database) as db,db.begin():
        row=db.get(PipelineRun,job)
        row.status='RUNNING'
        _commit_success(db,job,{'inserted_recommendations':0},DATA/'manifest.json',DATA)
    again=create_job(database,'REPLENISH',key,params,cfg,submit=False)
    assert again==(job,'SUCCEEDED',False)
    with pytest.raises(ValueError):
        create_job(database,'REPLENISH',key,{'forecast_job_id':'CHANGED'},cfg,submit=False)

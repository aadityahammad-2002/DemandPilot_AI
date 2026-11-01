"""Regression contracts for the merge-only Phase5 backend hardening patch.

Never opens a live PostgreSQL database or runs the 2,000-SKU dataset.
"""
from __future__ import annotations


import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.integration.api import create_app
from app.integration.jobs import (_public_error, _set_state, create_job,
                                  execute_job, job_identity, recover_interrupted)
from app.integration.types import IntegrationBase, PipelineRun
from app.models import Base, LeadTimeObservation

TOKEN = 'backend-hardening-token-example-1234567'

@pytest.fixture
def engine():
    db = create_engine('sqlite+pysqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    unsupported=next(c for c in LeadTimeObservation.__table__.constraints
                     if getattr(c,'name','').endswith('lead_derived'))
    LeadTimeObservation.__table__.constraints.discard(unsupported)
    try: Base.metadata.create_all(db)
    finally: LeadTimeObservation.__table__.constraints.add(unsupported)
    IntegrationBase.metadata.create_all(db)
    yield db
    IntegrationBase.metadata.drop_all(db)
    Base.metadata.drop_all(db)
    db.dispose()


def configuration(tmp_path):
    return dict(database_url='not-a-live-database', api_token=TOKEN,
                cors_origins=['http://localhost:5173'], data_root=tmp_path / 'input',
                artifacts_root=tmp_path / 'output', max_pairs=1, allow_demo_import=True)


def test_atomic_job_claim_and_duplicate_worker_noop(engine, tmp_path):
    cfg=configuration(tmp_path)
    id_, status, created = create_job(engine, 'IMPORT', 'claim-lock-job-00001', {}, cfg, submit=False)
    assert created and status == 'QUEUED'
    assert _set_state(engine,id_,'RUNNING',expected='QUEUED',started_at=None)
    assert not _set_state(engine,id_,'RUNNING',expected='QUEUED')
    assert execute_job(engine,id_,'IMPORT',{},cfg) is False
    with Session(engine) as db:
        assert db.get(PipelineRun,id_).status == 'RUNNING'
    assert _set_state(engine,id_,'FAILED',expected='RUNNING',error_code='TEST')
    assert execute_job(engine,id_,'IMPORT',{},cfg) is False


def test_missing_input_failure_is_safe_and_idempotent(engine,tmp_path):
    cfg=configuration(tmp_path)
    key='missing-input-job-00001'
    id_,_,_=create_job(engine,'IMPORT',key,{},cfg,submit=False)
    assert execute_job(engine,id_,'IMPORT',{},cfg) is False
    with Session(engine) as db:
        row=db.get(PipelineRun,id_)
        assert row.status=='FAILED'
        assert row.error_code=='ARTIFACT_MISSING'
        assert 'input' in row.error_message.lower()
        assert str(tmp_path) not in row.error_message
        assert row.finished_at is not None
    assert create_job(engine,'IMPORT',key,{},cfg,submit=False)==(id_,'FAILED',False)
    assert execute_job(engine,id_,'IMPORT',{},cfg) is False


def test_untrusted_sql_failure_text_never_entered_into_public_errors():
    error=DataError('SQL includes password=ultra-secret', {}, Exception('password=ultra-secret'))
    code,msg=_public_error(error)
    assert code=='PERSISTENCE_ERROR'
    assert 'password' not in msg and 'secret' not in msg
    code,msg=_public_error(ValueError('/secret/home/admin/stored-manifest.json'))
    assert code=='VALIDATION_ERROR' and '/secret' not in msg


def test_recovery_fails_only_incomplete_jobs(engine,tmp_path):
    cfg=configuration(tmp_path)
    ids=[]
    for kind,key in [('IMPORT','recover-test-import-01'),('FORECAST','recover-test-forecast-01'),
                     ('REPLENISH','recover-test-replenish-01')]:
        ids.append(create_job(engine,kind,key,{},cfg,submit=False)[0])
    assert _set_state(engine,ids[0],'RUNNING',expected='QUEUED')
    assert _set_state(engine,ids[2],'SUCCEEDED',expected='QUEUED')
    recover_interrupted(engine)
    with Session(engine) as db:
        assert [db.get(PipelineRun,i).status for i in ids]==['FAILED','FAILED','SUCCEEDED']
        assert db.get(PipelineRun,ids[1]).error_code=='INTERRUPTED'
    recover_interrupted(engine)
    with Session(engine) as db:
        assert db.get(PipelineRun,ids[2]).status=='SUCCEEDED'


def test_api_rejects_extra_inputs_and_returns_consistent_contract(engine,tmp_path):
    cfg=configuration(tmp_path)
    api=create_app(cfg,engine,init_on_start=False)
    with TestClient(api) as client:
        h={'Authorization':f'Bearer {TOKEN}','Idempotency-Key':'api-good-idempotency-key-0001'}
        result=client.post('/api/v1/jobs/forecast',headers=h,json={'unexpected':True})
        assert result.status_code==422
        result=client.post('/api/v1/jobs/forecast',headers=h,json={'forecast_job_id':'JOB-'+'1'*28})
        assert result.status_code==422
        result=client.post('/api/v1/jobs/replenish',headers=h,json={'forecast_job_id':'relative/path'})
        assert result.status_code==422
        result=client.get('/api/v1/forecasts?origin_date=2026-01-01',headers=h)
        assert result.status_code==200 and result.json()['total']==0
        result=client.get('/api/v1/recommendations?forecast_run_id=FC-RUN-TEST',headers=h)
        assert result.status_code==200 and result.json()['total']==0


def test_unknown_value_error_sanitized_at_http_boundary(engine,tmp_path):
    from fastapi import APIRouter
    cfg=configuration(tmp_path)
    api=create_app(cfg,engine,init_on_start=False)
    @api.get('/_test/unsafe-error')
    def _unsafe():
        raise ValueError('postgresql+psycopg://user:password@localhost/db')
    with TestClient(api) as client:
        resp=client.get('/_test/unsafe-error')
        assert resp.status_code==422
        assert 'password' not in resp.text
        assert 'failed validation' in resp.text


def test_unsigned_fact_and_event_files_are_never_imported(engine,tmp_path):
    """Previously rglob included unsigned files despite verifying the manifest."""
    import shutil
    from app.integration.persistence import import_dataset
    from app.models import SKU
    from sqlalchemy import select,func

    source=__import__('pathlib').Path(__file__).resolve().parents[2]/'examples'/'smoke_run'
    for folder,filename in [('facts/sales_history/2026-01','extra_sales.csv'),
                            ('technical_events/tech_inventory_movement_event/2026-01','extra_event.csv')]:
        destination=tmp_path/filename
        shutil.copytree(source,destination)
        (destination/folder/filename).write_text('malicious,unlisted,data\n',encoding='utf-8')
        with pytest.raises(ValueError,match='unlisted importable source'):
            with Session(engine) as db,db.begin():
                import_dataset(db,destination)
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(SKU))==0


def test_manifest_duplicate_paths_fail_closed(engine,tmp_path):
    import shutil,json
    from pathlib import Path
    from app.integration.persistence import import_dataset
    source=Path(__file__).resolve().parents[2]/'examples'/'smoke_run'
    destination=tmp_path/'duplicate-manifest'
    shutil.copytree(source,destination)
    path=destination/'manifest.json'
    manifest=json.loads(path.read_text(encoding='utf-8'))
    manifest['files'].append(dict(manifest['files'][0]))
    path.write_text(json.dumps(manifest),encoding='utf-8')
    with Session(engine) as db,db.begin(),pytest.raises(ValueError,match='duplicate'):
        import_dataset(db,destination)


def test_row_limit_rolls_back_all_entities(engine):
    from pathlib import Path
    from app.integration.persistence import import_dataset
    from app.models import SKU
    from sqlalchemy import select,func
    source=Path(__file__).resolve().parents[2]/'examples'/'smoke_run'
    with pytest.raises(ValueError,match='small dataset row cap'):
        with Session(engine) as db,db.begin():
            import_dataset(db,source,max_fact_rows=50)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(SKU))==0


def test_nonchronological_forecast_history_fails_closed():
    from datetime import date,timedelta
    from app.forecasting.features import enforce_origin,feature_vector
    start=date(2026,1,1)
    rows=[{'date':start+timedelta(days=x),'sales':1,'stockout':False,
           'available':10,'promo':False,'multiplier':1.} for x in range(8)]
    reversed_rows=list(reversed(rows))
    with pytest.raises(ValueError,match='out-of-order'):
        enforce_origin(reversed_rows,start+timedelta(days=8))
    with pytest.raises(ValueError,match='out-of-order'):
        feature_vector(reversed_rows,start+timedelta(days=8),
                       {'launch_date':'2026-01-01','category':'GROCERY'},
                       {'region':'South'})
    with pytest.raises(ValueError,match='invalid historical sales'):
        enforce_origin([{**r,'sales':float('nan')} if i==1 else r
                        for i,r in enumerate(rows)],start+timedelta(days=8))


def test_stockout_quality_uses_recent_dates_not_input_iteration_order():
    from datetime import date,timedelta
    from app.replenishment.policy import stockout_volatility,DecisionConfig
    start=date(2026,1,1)
    records=[{'date':start+timedelta(days=i),'sales':5,'available':10,'stockout':False}
             for i in range(70)]
    for record in records[-5:]:
        record.update(sales=0,available=0,stockout=True)
    cfg=DecisionConfig()
    end=records[-1]['date']
    first=stockout_volatility(records,end,cfg)
    last=stockout_volatility(list(reversed(records)),end,cfg)
    assert first==last
    assert first['censored_count']==5
    with pytest.raises(ValueError,match='duplicate dated'):
        stockout_volatility(records+[dict(records[-1])],end,cfg)


def test_invalid_lots_and_capacity_are_rejected():
    from app.replenishment.policy import feasible_lot
    from app.replenishment.engine import decide
    from datetime import date,timedelta
    with pytest.raises(ValueError,match='integers'):
        feasible_lot(10.5,12,12,100,100,1000,1)
    with pytest.raises(ValueError,match='integers'):
        feasible_lot(True,12,12,100,100,1000,1)
    assert feasible_lot(21,12,12,24,24,500,1)['quantity']==24


def test_corrupt_model_evaluation_is_service_unavailable(engine,tmp_path):
    cfg=configuration(tmp_path)
    folder=cfg['artifacts_root']/('JOB-'+'B'*28)/'forecast'
    folder.mkdir(parents=True)
    (folder/'evaluation.json').write_text('invalid evaluation',encoding='utf-8')
    with Session(engine) as db,db.begin():
        db.add(PipelineRun(job_id='JOB-'+'B'*28,idempotency_key='bad-evaluation-12345678',
                          kind='FORECAST',status='SUCCEEDED',result={}))
    api=create_app(cfg,engine,init_on_start=False)
    with TestClient(api) as client:
        response=client.get('/api/v1/model-metrics',headers={'Authorization':'Bearer '+TOKEN})
        assert response.status_code==503
        assert 'invalid evaluation' not in response.text
        assert 'integrity' in response.text


def test_forecast_metrics_reject_nonfinite_and_negative_labels():
    from app.forecasting.evaluation import metrics
    for actual,predicted in [(float('nan'),1.),(1.,float('inf')),(-1.,1.),(1.,-1.)]:
        with pytest.raises(ValueError,match='evaluation contains invalid'):
            metrics([{'actual_units':actual,'predicted_units':predicted,'censored_target':False}])
    # An unobservable, censored zero is excluded rather than scored as demand=0.
    assert metrics([{'actual_units':0.,'predicted_units':100.,'censored_target':True}])['n']==0


def test_forecast_selection_rejects_incomparable_validation_coverage():
    from app.forecasting.evaluation import choose_method
    report={'validation':{'a':{'overall':{'n':50,'wape':0.2,'mae':1.}},
                          'b':{'overall':{'n':1,'wape':0.,'mae':0.}}}}
    with pytest.raises(ValueError,match='coverage mismatch'):
        choose_method(report)


def test_public_sales_loader_rejects_nonfinite_signed_multiplier(tmp_path):
    """The signed file can be self-consistent yet carry semantically invalid NaN."""
    import csv,hashlib,json,shutil
    from pathlib import Path
    from app.forecasting.data import load_history
    source=Path(__file__).resolve().parents[2]/'examples'/'smoke_run'
    destination=tmp_path/'bad-multiplier'
    shutil.copytree(source,destination)
    manifest_path=destination/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    entry=next(x for x in manifest['files'] if x['path'].startswith('facts/sales_history/') and x['path'].endswith('.csv'))
    csvpath=destination/entry['path']
    with csvpath.open(newline='',encoding='utf-8') as f:
        reader=csv.DictReader(f);fieldnames=reader.fieldnames;rows=list(reader)
    rows[0]['promotion_multiplier']='NaN'
    with csvpath.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fieldnames,lineterminator='\n')
        writer.writeheader();writer.writerows(rows)
    entry['bytes']=csvpath.stat().st_size
    entry['file_sha256']=hashlib.sha256(csvpath.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
    key=(rows[0]['store_id'],rows[0]['sku_id'])
    with pytest.raises(ValueError,match='invalid public promotion multiplier'):
        load_history(destination,[key])


def test_schema_audit_checks_all_business_and_technical_varchars():
    from verify_backend_schema_readonly import expected_lengths
    sizes=expected_lengths()
    assert sizes[('tech_persistence_map','entity_type')]==32
    assert sizes[('tech_pipeline_run','idempotency_key')]==128
    assert any(table=='sku' for table,_ in sizes)
    assert any(table=='forecast' for table,_ in sizes)
    assert len(sizes)>30

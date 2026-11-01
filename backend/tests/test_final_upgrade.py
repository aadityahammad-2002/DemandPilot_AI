"""Focused source-level regression for final upgrade; no full-scale generation."""
from __future__ import annotations
from datetime import date
from pathlib import Path
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.forecasting.diagnostics import validation_sample_keys,empirical_interval_diagnostics
from app.forecasting.features import infer_path
from app.replenishment.simulation import simulate, compare_policies, ScenarioCosts
from app.replenishment.policy import feasible_lot
from app.integration.api import create_app
from app.integration.jobs import create_job,execute_job
from app.integration.types import IntegrationBase,PipelineRun
from app.models import Base,LeadTimeObservation, Forecast, PurchaseRecommendation
from app.integration.persistence import import_dataset

TOKEN='final-hardening-test-token-123456789'
DATA=Path(__file__).resolve().parents[2]/'examples'/'smoke_run'

@pytest.fixture
def database():
    engine=create_engine('sqlite+pysqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def fk_on(conn,_):conn.execute('PRAGMA foreign_keys=ON')
    table=LeadTimeObservation.__table__
    unsupported=next(c for c in table.constraints if getattr(c,'name','').endswith('lead_derived'))
    table.constraints.discard(unsupported)
    try:Base.metadata.create_all(engine)
    finally:table.constraints.add(unsupported)
    IntegrationBase.metadata.create_all(engine)
    yield engine
    IntegrationBase.metadata.drop_all(engine);Base.metadata.drop_all(engine);engine.dispose()


def cfg(tmp_path):
    return {'database_url':'postgresql+psycopg://placeholder', 'api_token':TOKEN,
            'cors_origins':['http://localhost:5173'], 'data_root':DATA,
            'artifacts_root':tmp_path/'artifacts','max_pairs':36,'allow_demo_import':True}


def example(split,method,sku='SKU-1',h=1,actual=5,predicted=5):
    return dict(split=split,method=method,store_id='STORE-1',sku_id=sku,
                forecast_origin='2026-01-01',target_date='2026-01-02',horizon=h,
                actual_units=actual,predicted_units=predicted,censored_target=False)


def test_equal_counts_are_insufficient_without_identical_validation_keys():
    rows=[example('validation','a',sku='SKU-1'),example('validation','b',sku='SKU-2')]
    with pytest.raises(ValueError,match='identities differ'):
        validation_sample_keys(rows)
    rows[1]['sku_id']='SKU-1'
    assert validation_sample_keys(rows)['target_count']==1
    with pytest.raises(ValueError,match='duplicate'):
        validation_sample_keys(rows+[dict(rows[0])])


def test_intervals_are_calibrated_only_on_validation_labels():
    rows=[]
    for i in range(30):
        for split in ('validation','test'):
            r=example(split,'moving_average',sku=f'SKU-{i}',actual=10,predicted=9)
            rows.append(r)
    original=empirical_interval_diagnostics(rows,'moving_average')
    poisoned=[{**r,'actual_units':1000} if r['split']=='test' else dict(r) for r in rows]
    changed=empirical_interval_diagnostics(poisoned,'moving_average')
    a=original['per_horizon']['1'];b=changed['per_horizon']['1']
    assert a['radius_units']==b['radius_units']==1
    assert a['test_coverage']==1 and b['test_coverage']==0
    assert original['statistical_guarantee'] is False


def test_intervals_report_unavailable_when_sparse_and_ignore_censoring():
    rows=[example('validation','a',actual=0,predicted=90)]
    assert empirical_interval_diagnostics(rows,'a')['per_horizon']['1']['radius_units'] is None
    rows[0]['censored_target']=True
    assert empirical_interval_diagnostics(rows,'a')['per_horizon']=={}


def test_small_scenario_measures_fill_and_inventory_without_financial_claims():
    costs=ScenarioCosts(holding_per_unit_day=0.1,shortage_per_unit=3,ordering_per_order=2)
    report=compare_policies([5]*5,initial_on_hand=0,lead_days=1,
                            baseline_orders={},candidate_orders={0:20},costs=costs)
    assert report['scenario_only'] and report['baseline']['lost_units']==25
    assert report['candidate']['fulfilled_units']==20
    assert report['candidate']['purchased_units']==20
    assert report['delta_lost_units']==-20
    assert report['candidate']['total_simulated_cost'] == report['candidate']['holding_cost']+report['candidate']['shortage_cost']+report['candidate']['ordering_cost']
    with pytest.raises(ValueError):simulate([2,-1],initial_on_hand=0,lead_days=1,order_plan={})
    with pytest.raises(ValueError):simulate([2],initial_on_hand=0,lead_days=1,order_plan={0:1.5})
    with pytest.raises(ValueError):ScenarioCosts(shortage_per_unit=float('nan')).validate()


def test_feasible_positive_lot_satisfies_all_hard_constraints():
    for requested in (1,2,7,19,70):
        for cap in (0,5,12,18,24,60):
            result=feasible_lot(requested,6,6,cap,60,cap*2,2)
            qty=result['quantity']
            if qty:
                assert qty>=6 and qty%6==0 and qty<=cap and qty*2<=cap*2
            else:
                assert result['status']=='BLOCKED'


def test_api_filters_over_real_multiple_run_records(database,tmp_path):
    from datetime import timedelta
    with Session(database) as db,db.begin():
        import_dataset(db,DATA)
        for i in range(2):
            origin=date(2026,1,20)+timedelta(days=i)
            db.add(Forecast(forecast_id=f'F-TEST-{i}',sku_id='SKU-000001',store_id='STORE-000001',
                forecast_origin=origin,target_date=origin+timedelta(days=1),predicted_units=10+i,
                model_version='test',run_id=f'RUN-{i}',status='VALIDATED'))
            db.add(PurchaseRecommendation(recommendation_id=f'PO-TEST-{i}',
                policy_id='POL-000001',sku_id='SKU-000001',store_id='STORE-000001',
                supplier_sku_id='SSKU-000001',decision_date=origin,
                forecast_run_id=f'RUN-{i}',inventory_date=origin,inventory_position=5,
                lead_time_demand=15,raw_quantity=12,recommended_quantity=12,
                status='GENERATED',reason_codes=['TEST'],rule_version='test-v1',decision_trace={}))
    with TestClient(create_app(cfg(tmp_path),database,init_on_start=False)) as client:
        headers={'Authorization':f'Bearer {TOKEN}'}
        for i in range(2):
            origin=(date(2026,1,20)+__import__('datetime').timedelta(days=i)).isoformat()
            f=client.get('/api/v1/forecasts',params={'origin_date':origin,'run_id':f'RUN-{i}'},headers=headers)
            assert f.status_code==200 and f.json()['total']==1 and f.json()['items'][0]['run_id']==f'RUN-{i}'
            rr=client.get('/api/v1/recommendations',params={'forecast_run_id':f'RUN-{i}'},headers=headers)
            assert rr.status_code==200 and rr.json()['total']==1
            assert rr.json()['items'][0]['forecast_run_id']==f'RUN-{i}'
        assert client.get('/api/v1/recommendations?from_date=2026-02-01&to_date=2026-01-01',headers=headers).status_code==422
    count=0
    def on_query(conn,cursor,statement,parameters,context,executemany):
        nonlocal count
        if statement.lstrip().upper().startswith('SELECT'):count+=1
    event.listen(database,'before_cursor_execute',on_query)
    try:
        with TestClient(create_app(cfg(tmp_path),database,init_on_start=False)) as client:
            response=client.get('/api/v1/recommendations',headers={'Authorization':f'Bearer {TOKEN}'})
            assert response.status_code==200 and response.json()['total']==2
    finally:event.remove(database,'before_cursor_execute',on_query)
    assert count<=5, f'Unexpected N+1 queries: {count}'


def test_corrupt_optional_explainability_returns_sanitized_503(database,tmp_path,monkeypatch):
    import app.forecasting.verify as verify
    monkeypatch.setattr(verify,'verify_run',lambda path: {'status':'PASS'})
    job_id='JOB-'+'A'*28
    base=cfg(tmp_path)['artifacts_root']/job_id/'forecast'
    base.mkdir(parents=True)
    (base/'evaluation.json').write_text('{"selected_model":"moving_average"}')
    (base/'explainability.json').write_text('{"invalid":')
    with Session(database) as db,db.begin():
        db.add(PipelineRun(job_id=job_id,idempotency_key='optional-explainability-001',
            kind='FORECAST',status='SUCCEEDED',result={}))
    with TestClient(create_app(cfg(tmp_path),database,init_on_start=False)) as client:
        response=client.get('/api/v1/model-metrics',headers={'Authorization':f'Bearer {TOKEN}'})
    assert response.status_code==503
    assert 'artifact failed integrity' in response.text
    assert str(tmp_path) not in response.text


def test_failed_forecast_cleans_only_newly_created_artifacts(database,tmp_path,monkeypatch):
    import app.forecasting.engine as forecasts
    def fails(root,folder,config):
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'partial.txt').write_text('partial')
        raise ValueError('bad source')
    monkeypatch.setattr(forecasts,'forecast',fails)
    options=cfg(tmp_path)
    job,_,_=create_job(database,'FORECAST','new-artifact-failure-key-001',{},options,submit=False)
    assert not execute_job(database,job,'FORECAST',{},options)
    assert not (options['artifacts_root']/job).exists()
    old,_,_=create_job(database,'FORECAST','old-artifact-failure-key-001',{},options,submit=False)
    existing=options['artifacts_root']/old/'forecast'
    existing.mkdir(parents=True)
    (existing/'original.txt').write_text('must stay')
    assert not execute_job(database,old,'FORECAST',{},options)
    assert (existing/'original.txt').read_text()=='must stay'


def test_api_advisory_connection_is_autocommit(monkeypatch,tmp_path):
    import app.integration.api as module
    class Result:
        def scalar_one(self):return '0005'
    class Conn:
        dialect=type('Dialect',(),{'name':'postgresql'})()
        def __init__(self):self.isolation=None;self.closed=False;self.unlocked=False
        def execution_options(self,**kw):self.isolation=kw['isolation_level'];return self
        def execute(self,stmt):
            if 'pg_advisory_unlock' in str(stmt):self.unlocked=True
            return Result()
        def scalar(self,stmt):return True
        def close(self):self.closed=True
    class FakeEngine:
        def __init__(self):self.conn=Conn()
        def connect(self):return self.conn
    engine=FakeEngine()
    monkeypatch.setattr(module,'recover_interrupted',lambda engine: None)
    with TestClient(create_app(cfg(tmp_path),engine,init_on_start=True)) as client:
        assert client.get('/health').status_code==200
        assert engine.conn.isolation=='AUTOCOMMIT'
    assert engine.conn.unlocked and engine.conn.closed


def test_discontinued_sku_historical_snapshot_only_produces_blocked_zero_order():
    from app.replenishment.inputs import read_inventory_at_dates,load_signed_manifest,read_master
    from app.replenishment.engine import decide
    from app.replenishment.policy import DecisionConfig
    asof=date(2026,2,4)
    pair=('STORE-000001','SKU-000002')
    snap,_=read_inventory_at_dates(DATA,load_signed_manifest(DATA),{pair:(asof,{1:5})})
    inv=snap[(*pair,asof.isoformat())]
    assert inv.get('historical_fallback_for_block_only')
    assert inv['snapshot_date']<asof.isoformat()
    masters=read_master(DATA,load_signed_manifest(DATA))
    one=lambda name,idkey,val:next(v for v in masters[name] if v[idkey]==val)
    sku=one('sku','sku_id',pair[1]);store=one('store','store_id',pair[0]);policy=one('reorder_policy','policy_id','POL-000002')
    contract=one('supplier_sku','supplier_sku_id',policy['supplier_sku_id'])
    supplier=one('supplier','supplier_id',contract['supplier_id'])
    result=decide(key=pair,origin=asof,points={1:5,7:5},sku=sku,store=store,
                  supplier=supplier,contract=contract,policy=policy,inventory=inv,
                  store_occupancy=300,sales=[],observations=[],cfg=DecisionConfig(),
                  forecast_run_id='FC-RUN-TST',rec_id='PO-2026-000001')
    assert result['status']=='BLOCKED' and result['recommended_quantity']==0
    assert result['inventory_date']==inv['snapshot_date']
    assert 'DISCONTINUED_OR_INELIGIBLE_SKU' in result['reason_codes']
    # Forging the fallback flag does not make historical inventory valid for ACTIVE SKUs.
    active=one('sku','sku_id','SKU-000001')
    with pytest.raises(ValueError,match='active inventory must be origin-aligned'):
        decide(key=(pair[0],'SKU-000001'),origin=asof,points={1:5},sku=active,store=store,
               supplier=supplier,contract=contract,policy=policy,
               inventory={**inv,'sku_id':'SKU-000001'},store_occupancy=300,
               sales=[],observations=[],cfg=DecisionConfig(),
               forecast_run_id='FC-RUN-TST',rec_id='PO-2026-000002')


def test_selection_stability_uses_validation_only():
    from app.forecasting.diagnostics import validation_selection_stability
    rows=[example('validation','moving_average',actual=5,predicted=5),
          example('validation','seasonal_naive',actual=5,predicted=4),
          example('test','moving_average',sku='TEST',actual=999,predicted=0),
          example('test','seasonal_naive',sku='TEST',actual=999,predicted=999)]
    a=validation_selection_stability(rows,'moving_average')
    poisoned=[{**r,'actual_units':0} if r['split']=='test' else dict(r) for r in rows]
    assert validation_selection_stability(poisoned,'moving_average')==a
    assert a['validation_origins']==1 and a['selected_model_origin_wins']==1


def test_current_capacity_excludes_historical_snapshots_and_blocks_other_skus():
    """Regression: stale discontinued inventory contaminated active SKU capacity."""
    from app.replenishment.inputs import read_inventory_at_dates,load_signed_manifest,read_master
    from app.replenishment.engine import decide
    from app.replenishment.policy import DecisionConfig
    asof=date(2026,2,4)
    selected={( 'STORE-000001','SKU-000002'):(asof,{1:5.}),
              ( 'STORE-000001','SKU-000001'):(asof,{1:5.})}
    inventory,_,coverage=read_inventory_at_dates(DATA,load_signed_manifest(DATA),selected,
                                                 with_capacity_coverage=True)
    stale=inventory[('STORE-000001','SKU-000002',asof.isoformat())]
    current=inventory[('STORE-000001','SKU-000001',asof.isoformat())]
    assert stale['historical_fallback_for_block_only']
    assert 'SKU-000002' in coverage[('STORE-000001',asof.isoformat())]['missing_current_skus']
    actual_occupancy=sum(int(r['closing_stock'])*int(next(s for s in read_master(DATA,load_signed_manifest(DATA))['sku'] if s['sku_id']==sku)['storage_units_per_item'])
                         for (store,sku,day),r in inventory.items() if day==asof.isoformat()
                         and not r.get('historical_fallback_for_block_only'))
    stale_occupancy=int(stale['closing_stock'])*3
    assert stale_occupancy>0
    assert actual_occupancy+stale_occupancy>actual_occupancy
    masters=read_master(DATA,load_signed_manifest(DATA))
    one=lambda name,idkey,val:next(v for v in masters[name] if v[idkey]==val)
    product=one('sku','sku_id','SKU-000001')
    store=one('store','store_id','STORE-000001')
    policy=one('reorder_policy','policy_id','POL-000001')
    contract=one('supplier_sku','supplier_sku_id',policy['supplier_sku_id'])
    supplier=one('supplier','supplier_id',contract['supplier_id'])
    rec=decide(key=('STORE-000001','SKU-000001'),origin=asof,points={1:8.,7:9.,14:10.,28:9.},
               sku=product,store=store,supplier=supplier,contract=contract,policy=policy,
               inventory=current,store_occupancy=actual_occupancy,sales=[],observations=[],
               cfg=DecisionConfig(),forecast_run_id='FC-RUN-TEST',rec_id='PO-2026-000001',
               capacity_missing_skus=tuple(coverage[('STORE-000001',asof.isoformat())]['missing_current_skus']))
    assert rec['status']=='BLOCKED' and rec['recommended_quantity']==0
    assert 'CAPACITY_SNAPSHOT_INCOMPLETE' in rec['reason_codes']
    assert rec['decision_trace']['capacity_coverage']=='INCOMPLETE_BLOCKED'
    assert rec['decision_trace']['store_occupied_storage_units']==actual_occupancy


def test_current_capacity_is_complete_for_original_valid_decisions():
    from app.replenishment.inputs import read_inventory_at_dates,load_signed_manifest
    asof=date(2026,1,28)
    pairs={(f'STORE-{store:06d}',f'SKU-{sku:06d}'):(asof,{1:5.})
           for store in range(1,4) for sku in range(1,13)}
    inventory,_,coverage=read_inventory_at_dates(DATA,load_signed_manifest(DATA),pairs,
                                                 with_capacity_coverage=True)
    assert len(inventory)==36
    assert all(info['complete'] for info in coverage.values())


def test_capacity_detects_duplicate_prior_or_current_inventory(monkeypatch):
    """Signed facts with two observations for same identity cannot be used for occupancy."""
    from app.replenishment import inputs as mod
    asof=date(2026,1,30)
    row={'store_id':'STORE-000001','sku_id':'SKU-000001','snapshot_date':'2026-01-29',
         'opening_stock':'4','received_units':'0','sold_units':'0','adjustment_units':'0',
         'closing_stock':'4','reserved_units':'0','inventory_id':'INV-1'}
    monkeypatch.setattr(mod,'checked_path',lambda root,entry:Path('/dev/null'))
    monkeypatch.setattr(mod,'iter_rows',lambda path:iter([dict(row),dict(row)]))
    with pytest.raises(ValueError,match='duplicate inventory observation'):
        mod.read_inventory_at_dates(Path('.'),{'files':[{'path':'facts/inventory_snapshot/2026-01/inventory_snapshot_stores_001-001.csv'}]},
                                    {('STORE-000001','SKU-000001'):(asof,{1:1.})},
                                    with_capacity_coverage=True)


def test_failed_job_never_deletes_preexisting_sibling_artifacts(database,tmp_path,monkeypatch):
    import app.forecasting.engine as forecasts
    def fails(root,folder,config):
        (folder/'partial.txt').write_text('new data')
        raise RuntimeError('failure after artifact reservation')
    monkeypatch.setattr(forecasts,'forecast',fails)
    options=cfg(tmp_path)
    job,_,_=create_job(database,'FORECAST','sibling-preserve-key-2026',{},options,submit=False)
    sibling=options['artifacts_root']/job/'user-notes'
    sibling.mkdir(parents=True)
    (sibling/'saved.txt').write_text('original-content')
    assert not execute_job(database,job,'FORECAST',{},options)
    assert (sibling/'saved.txt').read_text()=='original-content'
    assert not (options['artifacts_root']/job/'forecast').exists()


def test_future_promotion_not_known_at_origin_cannot_change_predictions():
    from datetime import timedelta
    from app.forecasting.features import infer_path
    origin=date(2026,1,20)
    history=[{'date':origin-timedelta(days=n),'sales':5.,'available':10.,
              'stockout':False,'promo':False} for n in range(10,0,-1)]
    product={'sku_id':'SKU-000001','category':'GROCERY','launch_date':'2025-01-01'}
    store={'store_id':'STORE-000001','region':'North'}
    target=origin+timedelta(days=1)
    unannounced={(store['store_id'],product['sku_id'],target):(origin+timedelta(days=1),5.)}
    a=infer_path(history,origin,3,'moving_average',None,product,store)
    b=infer_path(history,origin,3,'moving_average',None,product,store,unannounced)
    assert a==b
    with pytest.raises(ValueError,match='future sales'):
        infer_path(history+[{'date':target,'sales':300,'available':20,'stockout':False,'promo':True}],
                   origin,3,'moving_average',None,product,store)


def test_unbound_exception_corrupt_artifact_is_sanitized(database,tmp_path):
    job_id='JOB-'+'B'*28
    options=cfg(tmp_path)
    location=options['artifacts_root']/job_id/'replenishment'
    location.mkdir(parents=True)
    (location/'unbound_exceptions.jsonl').write_text('{not valid json}\n')
    with Session(database) as db,db.begin():
        db.add(PipelineRun(job_id=job_id,idempotency_key='unbound-json-corrupt-2026',
            kind='REPLENISH',status='SUCCEEDED',artifact_path=str(location),result={}))
    with TestClient(create_app(options,database,init_on_start=False)) as client:
        result=client.get('/api/v1/exceptions/unbound',headers={'Authorization':f'Bearer {TOKEN}'})
    assert result.status_code==503
    assert result.json()['error']['message']=='Unbound recommendation artifact failed integrity checks'
    assert str(location) not in result.text


def test_launch_without_any_inventory_blocks_capacity_safely():
    """The current-day store SKU census, not just selected forecasts, is required."""
    from app.replenishment.engine import missing_current_capacity_skus
    from app.replenishment.inputs import read_inventory_at_dates,load_signed_manifest,read_master
    asof=date(2026,1,28)
    entries={('STORE-000001','SKU-000001'):(asof,{1:4})}
    inv,_,_=read_inventory_at_dates(DATA,load_signed_manifest(DATA),entries,
                                   with_capacity_coverage=True)
    skus={r['sku_id']:r for r in read_master(DATA,load_signed_manifest(DATA))['sku']}
    assert not missing_current_capacity_skus('STORE-000001',asof.isoformat(),inv,skus)
    missing_current=dict(inv)
    missing_current.pop(('STORE-000001','SKU-000003',asof.isoformat()))
    assert 'SKU-000003' in missing_current_capacity_skus('STORE-000001',
                                                           asof.isoformat(),missing_current,skus)


def test_zero_order_remains_no_order_when_capacity_unobserved(scenario=None):
    from app.replenishment.engine import decide
    from app.replenishment.policy import DecisionConfig
    asof=date(2026,1,31)
    product={'sku_id':'SKU-000001','launch_date':'2025-01-01','discontinue_date':'','status':'ACTIVE','storage_units_per_item':'1'}
    store={'store_id':'STORE-000001','status':'OPEN','open_date':'2025-01-01','close_date':'','capacity_units':'900'}
    supplier={'supplier_id':'SUP-000001','status':'ACTIVE','effective_from':'2025-01-01','effective_to':''}
    contract={'supplier_sku_id':'SSKU-000001','sku_id':'SKU-000001','supplier_id':'SUP-000001',
              'valid_from':'2025-01-01','valid_to':'','status':'ACTIVE','base_lead_time_days':'3',
              'moq_units':'6','pack_size_units':'6','supplier_capacity_units':'200'}
    policy={'policy_id':'POL-000001','sku_id':'SKU-000001','store_id':'STORE-000001',
            'supplier_sku_id':'SSKU-000001','status':'ACTIVE','effective_from':'2025-01-01',
            'effective_to':'','review_period_days':'7','safety_stock_units':'0',
            'capacity_limit_units':'200','service_level':'.9'}
    inv={'inventory_id':'INV-X','store_id':'STORE-000001','sku_id':'SKU-000001',
         'snapshot_date':asof.isoformat(),'closing_stock':500,'reserved_units':0}
    rec=decide(key=('STORE-000001','SKU-000001'),origin=asof,points={1:1.,7:1.,14:1.,28:1.},
               sku=product,store=store,supplier=supplier,contract=contract,policy=policy,
               inventory=inv,store_occupancy=600,sales=[],observations=[],cfg=DecisionConfig(),
               forecast_run_id='FC-TEST',rec_id='PO-2026-000001',
               capacity_missing_skus=('SKU-000002',))
    assert rec['status']=='GENERATED' and rec['recommended_quantity']==0
    assert rec['decision_trace']['capacity_coverage']=='INCOMPLETE'
    assert 'REORDER_POINT_NOT_REACHED' in rec['reason_codes']


def test_duplicate_or_unsafe_replenishment_manifest_rejected(tmp_path):
    from app.replenishment.inputs import load_signed_manifest
    (tmp_path/'manifest.json').write_text(json.dumps({'files':[{'path':'master/sku.csv'},
            {'path':'master/sku.csv'}]}))
    with pytest.raises(ValueError,match='duplicate'):
        load_signed_manifest(tmp_path)
    (tmp_path/'manifest.json').write_text(json.dumps({'files':[{'path':'../secrets.csv'}]}))
    with pytest.raises(ValueError,match='unsafe'):
        load_signed_manifest(tmp_path)


def test_runtime_environment_recorded_on_generated_forecasts(tmp_path):
    import importlib.metadata
    from app.forecasting.engine import forecast
    from app.forecasting.config import ForecastConfig
    output=tmp_path/'model-provenance'
    forecast(DATA,output,ForecastConfig(max_pairs=36))
    record=json.loads((output/'evaluation.json').read_text())
    assert record['runtime_versions']=={'numpy':importlib.metadata.version('numpy'),
                                        'scikit_learn':importlib.metadata.version('scikit-learn')}

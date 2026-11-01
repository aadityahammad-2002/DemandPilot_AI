"""Regression for V4's synthetic discontinuation/capacity contract defect."""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.generation.config import GeneratorConfig
from app.generation.generator import generate
from app.generation.verify import validate, iter_rows
from app.generation.writers import canonical
from app.integration.persistence import import_dataset
from app.models import Base, InventorySnapshot, LeadTimeObservation
from app.replenishment.inputs import load_signed_manifest, read_inventory_at_dates, read_master
from app.replenishment.engine import decide, missing_current_capacity_skus
from app.replenishment.policy import DecisionConfig


@pytest.fixture(scope='module')
def dataset(tmp_path_factory):
    root=tmp_path_factory.mktemp('v5-contract')/'data'
    cfg=GeneratorConfig(seed=4001, sku_count=12, store_count=2, days=32,
                        store_batch=1, format='csv', supplier_count=10)
    generate(cfg,root)
    assert validate(root)['status']=='PASS'
    return root


def rows(root, table):
    manifest=load_signed_manifest(root)
    return [r for e in manifest['files'] if e['path'].startswith('facts/'+table+'/')
            for r in iter_rows(root/e['path'])]


def selected():
    asof=date(2026,1,31)
    return {('STORE-000001','SKU-000001'):(asof,{1:30.,7:30.,14:30.,28:30.}),
            ('STORE-000001','SKU-000002'):(asof,{1:30.,7:30.,14:30.,28:30.})}


def test_retired_stock_carries_forward_without_fake_sales(dataset):
    masters=read_master(dataset,load_signed_manifest(dataset))
    retired=next(p for p in masters['sku'] if p['sku_id']=='SKU-000002')
    retired_day=retired['discontinue_date']
    assert retired_day
    historic=[r for r in rows(dataset,'inventory_snapshot') if r['sku_id']==retired['sku_id'] and r['store_id']=='STORE-000001']
    carried=[r for r in rows(dataset,'inventory_snapshot_retired') if r['sku_id']==retired['sku_id'] and r['store_id']=='STORE-000001']
    assert carried and min(r['snapshot_date'] for r in carried)==retired_day
    prior=next(r for r in historic if r['snapshot_date']==(date.fromisoformat(retired_day)-timedelta(days=1)).isoformat())
    for r in carried:
        assert int(r['closing_stock'])==int(prior['closing_stock'])
        assert int(r['opening_stock'])==int(prior['closing_stock'])
        assert all(int(r[k])==0 for k in ('received_units','sold_units','adjustment_units','reserved_units'))
    assert not any(r['sku_id']==retired['sku_id'] and r['sales_date']>=retired_day
                   for r in rows(dataset,'sales_history'))


def test_store_wide_complete_coverage_includes_retired_physical_stock(dataset):
    sel=selected();d=sel[next(iter(sel))][0].isoformat()
    masters=read_master(dataset,load_signed_manifest(dataset))
    inventory,_,coverage=read_inventory_at_dates(dataset,load_signed_manifest(dataset),sel,with_capacity_coverage=True)
    assert coverage[('STORE-000001',d)]['complete']
    assert coverage[('STORE-000001',d)]['missing_current_skus']==[]
    assert all(not r.get('historical_fallback_for_block_only') for r in inventory.values())
    retired=inventory[('STORE-000001','SKU-000002',d)]
    assert retired['snapshot_date']==d
    assert int(retired['closing_stock'])>=0
    all_current=[r for r in inventory.values() if r['store_id']=='STORE-000001' and r['snapshot_date']==d]
    sku_map={p['sku_id']:int(p['storage_units_per_item']) for p in masters['sku']}
    occupancy=sum(int(r['closing_stock'])*sku_map[r['sku_id']] for r in all_current)
    assert occupancy>=int(retired['closing_stock'])*sku_map['SKU-000002']


def test_complete_current_inventory_permits_positive_order(dataset):
    sel=selected();origin=sel[next(iter(sel))][0];d=origin.isoformat()
    master=read_master(dataset,load_signed_manifest(dataset))
    inventory,_,coverage=read_inventory_at_dates(dataset,load_signed_manifest(dataset),sel,with_capacity_coverage=True)
    select_one=lambda name,key,value:next(x for x in master[name] if x[key]==value)
    product=select_one('sku','sku_id','SKU-000001')
    store=select_one('store','store_id','STORE-000001')
    policy=select_one('reorder_policy','policy_id','POL-000001')
    contract=select_one('supplier_sku','supplier_sku_id',policy['supplier_sku_id'])
    supplier=select_one('supplier','supplier_id',contract['supplier_id'])
    sku_map={p['sku_id']:int(p['storage_units_per_item']) for p in master['sku']}
    occupancy=sum(int(r['closing_stock'])*sku_map[r['sku_id']] for r in inventory.values()
                  if r['store_id']==store['store_id'] and r['snapshot_date']==d)
    result=decide(key=('STORE-000001','SKU-000001'),origin=origin,points={1:40.,7:40.,14:40.,28:40.},
                  sku=product,store=store,supplier=supplier,contract=contract,policy=policy,
                  inventory=inventory[('STORE-000001','SKU-000001',d)],
                  store_occupancy=occupancy,sales=[],observations=[],cfg=DecisionConfig(),
                  forecast_run_id='FC-RUN-TEST',rec_id='PO-2026-000001',
                  capacity_missing_skus=tuple(coverage[('STORE-000001',d)]['missing_current_skus']))
    assert result['status']=='GENERATED' and result['recommended_quantity']>0
    assert result['decision_trace']['capacity_coverage']=='CURRENT_SNAPSHOTS_COMPLETE'
    assert result['recommended_quantity']%int(contract['pack_size_units'])==0


def test_removing_current_retired_observation_never_assumes_zero(dataset):
    manifest=load_signed_manifest(dataset)
    missing=[e for e in manifest['files'] if 'inventory_snapshot_retired_stores_001-001' in e['path'] and '/2026-01/' in e['path']]
    assert missing
    incomplete={**manifest,'files':[e for e in manifest['files'] if e not in missing]}
    inventory,_,coverage=read_inventory_at_dates(dataset,incomplete,selected(),with_capacity_coverage=True)
    d='2026-01-31'
    assert 'SKU-000002' in coverage[('STORE-000001',d)]['missing_current_skus']
    assert inventory[('STORE-000001','SKU-000002',d)]['historical_fallback_for_block_only']
    assert not coverage[('STORE-000001',d)]['complete']


def test_explicit_current_zero_is_not_missing():
    d='2026-01-31';store='STORE-000001';sku='SKU-000002'
    inv={(store,sku,d):{'closing_stock':0,'snapshot_date':d}}
    products={sku:{'sku_id':sku,'launch_date':'2025-01-01','discontinue_date':'2026-01-27'}}
    assert missing_current_capacity_skus(store,d,inv,products)==[]
    assert missing_current_capacity_skus(store,d,{},products,[sku])==[sku]


def test_retired_snapshot_tamper_rejected_even_if_manifest_resigned(dataset,tmp_path):
    damaged=tmp_path/'damaged';shutil.copytree(dataset,damaged)
    manifest=json.loads((damaged/'manifest.json').read_text())
    entry=next(e for e in manifest['files'] if '/inventory_snapshot_retired/' in e['path'])
    path=damaged/entry['path']
    with path.open(newline='',encoding='utf-8') as f:
        reader=csv.DictReader(f);headers=reader.fieldnames;data=list(reader)
    assert data
    data[0]['closing_stock']=str(int(data[0]['closing_stock'])+1)
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,headers,lineterminator='\n');writer.writeheader();writer.writerows(data)
    entry['bytes']=path.stat().st_size
    entry['file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    entry['logical_sha256']=hashlib.sha256(b''.join(canonical(x) for x in data)).hexdigest()
    (damaged/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
    with pytest.raises(AssertionError, match='retired inventory movement/balance mismatch'):
        validate(damaged)


def test_new_retired_partitions_import_idempotently_in_sqlite(dataset):
    engine=create_engine('sqlite+pysqlite://',poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def fk_on(conn,rec):conn.execute('PRAGMA foreign_keys=ON')
    table=LeadTimeObservation.__table__
    check=next(c for c in table.constraints if getattr(c,'name','').endswith('lead_derived'))
    table.constraints.discard(check)
    try:Base.metadata.create_all(engine)
    finally:table.constraints.add(check)
    try:
        with Session(engine) as session,session.begin():
            first=import_dataset(session,dataset)
        assert first['inventory_snapshot']>first['sales_history']
        with Session(engine) as session,session.begin():
            second=import_dataset(session,dataset)
        assert second['inventory_snapshot']==0
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(InventorySnapshot))==first['inventory_snapshot']
    finally:
        Base.metadata.drop_all(engine);engine.dispose()


@pytest.fixture()
def jobs_db():
    from app.integration.types import IntegrationBase
    engine=create_engine('sqlite+pysqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    IntegrationBase.metadata.create_all(engine)
    try:yield engine
    finally:IntegrationBase.metadata.drop_all(engine);engine.dispose()


def test_forecast_staged_until_verified_then_atomically_published(jobs_db,tmp_path,monkeypatch):
    from app.integration.jobs import create_job,execute_job
    from app.integration.types import PipelineRun
    import app.forecasting.engine as forecast_module
    import app.forecasting.verify as verify_module
    from app.integration import jobs
    root=tmp_path/'artifacts'
    settings={'artifacts_root':root,'data_root':tmp_path,'max_pairs':1,'allow_demo_import':False}
    job,_,_=create_job(jobs_db,'FORECAST','v5-atomic-publish-job-unique',{},settings,submit=False)
    checks=[]
    def producer(data,out,cfg):
        checks.append((out.name,(out.parent/'forecast').exists()))
        (out/'forecast_manifest.json').write_text('signed-test-manifest')
    def verifier(out):
        checks.append((out.name,(out.parent/'forecast').exists()))
        assert (out/'forecast_manifest.json').is_file()
        return {'status':'PASS'}
    monkeypatch.setattr(forecast_module,'forecast',producer)
    monkeypatch.setattr(verify_module,'verify_run',verifier)
    monkeypatch.setattr(jobs,'import_forecasts',lambda db,folder,job_id: {'rows_inserted':0})
    assert execute_job(jobs_db,job,'FORECAST',{},settings)
    assert checks==[('forecast.staging',False),('forecast.staging',False)]
    assert (root/job/'forecast'/'forecast_manifest.json').read_text()=='signed-test-manifest'
    assert not (root/job/'forecast.staging').exists()
    with Session(jobs_db) as db:
        saved=db.get(PipelineRun,job)
        assert saved.status=='SUCCEEDED' and saved.artifact_path==str(root/job/'forecast')


def test_failed_artifact_verification_never_publishes_or_removes_siblings(jobs_db,tmp_path,monkeypatch):
    from app.integration.jobs import create_job,execute_job
    from app.integration.types import PipelineRun
    import app.forecasting.engine as forecast_module
    import app.forecasting.verify as verify_module
    root=tmp_path/'artifacts'
    settings={'artifacts_root':root,'data_root':tmp_path,'max_pairs':1,'allow_demo_import':False}
    job,_,_=create_job(jobs_db,'FORECAST','v5-failed-staged-job-unique',{},settings,submit=False)
    sibling=root/job/'existing';sibling.mkdir(parents=True)
    (sibling/'saved.txt').write_text('immutable')
    def producer(data,out,cfg):(out/'forecast_manifest.json').write_text('incomplete')
    def verifier(out):raise ValueError('integrity validation rejected artifact')
    monkeypatch.setattr(forecast_module,'forecast',producer)
    monkeypatch.setattr(verify_module,'verify_run',verifier)
    assert not execute_job(jobs_db,job,'FORECAST',{},settings)
    assert not (root/job/'forecast').exists()
    assert not (root/job/'forecast.staging').exists()
    assert (sibling/'saved.txt').read_text()=='immutable'
    with Session(jobs_db) as db:
        saved=db.get(PipelineRun,job)
        assert saved.status=='FAILED' and saved.error_code=='VALIDATION_ERROR'


def test_preexisting_staging_artifacts_never_overwritten_or_deleted(jobs_db,tmp_path):
    from app.integration.jobs import create_job,execute_job
    root=tmp_path/'artifacts'
    settings={'artifacts_root':root,'data_root':tmp_path,'max_pairs':1,'allow_demo_import':False}
    job,_,_=create_job(jobs_db,'FORECAST','v5-stale-stage-refusal-job',{},settings,submit=False)
    previous=root/job/'forecast.staging';previous.mkdir(parents=True)
    (previous/'keep.txt').write_text('orphan from earlier interrupted attempt')
    assert not execute_job(jobs_db,job,'FORECAST',{},settings)
    assert (previous/'keep.txt').read_text()=='orphan from earlier interrupted attempt'
    assert not (root/job/'forecast').exists()


def test_legacy_v4_generator_artifact_still_verifies_and_reproduces():
    from app.generation.full_reproduce import compare_streaming
    old=Path(__file__).resolve().parents[2]/'examples'/'smoke_run'
    assert validate(old,write_report=False)['status']=='PASS'
    assert compare_streaming(old)['status']=='PASS'


def test_import_requires_retired_inventory_completeness_contract(dataset,tmp_path):
    copy=tmp_path/'uncontracted';shutil.copytree(dataset,copy)
    manifest=json.loads((copy/'manifest.json').read_text())
    del manifest['inventory_completeness_contract']
    (copy/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
    from app.integration.persistence import import_dataset
    with pytest.raises(ValueError,match='retired inventory lacks generator completeness contract'):
        with Session(create_engine('sqlite+pysqlite://')) as db:
            import_dataset(db,copy)


def test_empty_resigned_retired_partition_set_cannot_bypass_import_validation(dataset,tmp_path):
    copy=tmp_path/'all_retired_removed';shutil.copytree(dataset,copy)
    manifest=json.loads((copy/'manifest.json').read_text())
    entries=[e for e in manifest['files'] if e['path'].startswith('facts/inventory_snapshot_retired/')]
    assert entries
    for entry in entries:(copy/entry['path']).unlink()
    manifest['files']=[e for e in manifest['files'] if e not in entries]
    (copy/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
    engine=create_engine('sqlite+pysqlite://')
    try:
        with Session(engine) as db:
            with pytest.raises(AssertionError,match='retired inventory coverage mismatch'):
                import_dataset(db,copy)
    finally:engine.dispose()

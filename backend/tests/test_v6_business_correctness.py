"""V6 regressions: date-effective eligibility and joint store/date capacity."""
from __future__ import annotations
from copy import deepcopy
from datetime import date, timedelta
import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.replenishment.lifecycle import sku_eligible_asof
from app.replenishment import engine as re
from app.replenishment.policy import DecisionConfig, feasible_lot
from app.integration import persistence as persistence
from app.models import SKU, ReorderPolicy, PurchaseRecommendation
from app.integration.types import PipelineRun


@pytest.mark.parametrize('asof,expected', [
    (date(2025,12,31),False), (date(2026,1,1),True),
    (date(2026,2,20),True), (date(2026,3,9),True),
    (date(2026,3,10),False), (date(2026,3,11),False),
])
def test_retired_master_status_is_not_retroactive(asof,expected):
    row={'status':'DISCONTINUED','launch_date':'2026-01-01',
         'discontinue_date':'2026-03-10'}
    assert sku_eligible_asof(row,asof) is expected
    row['status']='ACTIVE'
    assert sku_eligible_asof(row,asof) is expected


def test_planned_and_invalid_lifecycle_fail_closed():
    planned={'status':'PLANNED','launch_date':'2026-02-01','discontinue_date':''}
    assert not sku_eligible_asof(planned,date(2026,1,1))
    with pytest.raises(ValueError,match='planned SKU'):
        sku_eligible_asof(planned,date(2026,2,1))
    for bad in ({'status':'DISCONTINUED','launch_date':'2026-01-01','discontinue_date':''},
                {'status':'DISCONTINUED','launch_date':'2026-03-10','discontinue_date':'2026-01-01'},
                {'status':'UNKNOWN','launch_date':'2026-01-01','discontinue_date':''}):
        with pytest.raises(ValueError):sku_eligible_asof(bad,date(2026,2,1))


def _fixture_for_run(monkeypatch, *, reverse=False, groups=('STORE-000001','STORE-000002'),
                    capacity=36, pack_sizes=(12,12,12), qty_onhand=0):
    origin=date(2026,1,31);day=origin.isoformat()
    skus={f'SKU-{i:06}':dict(sku_id=f'SKU-{i:06}',status='ACTIVE',
          launch_date='2025-01-01',discontinue_date='',storage_units_per_item='1')
          for i in range(1,4)}
    stores={store:dict(store_id=store,status='OPEN',open_date='2025-01-01',
           close_date='',capacity_units=str(capacity)) for store in groups}
    supplier=dict(supplier_id='SUP-000001',status='ACTIVE',effective_from='2025-01-01',effective_to='')
    contracts={};policies=[];snapshots={};coverage={};selected={};history={}
    for i,sku in enumerate(sorted(skus),start=1):
        cid=f'SSKU-{i:06}'
        contracts[cid]=dict(supplier_sku_id=cid,sku_id=sku,supplier_id='SUP-000001',
            status='ACTIVE',valid_from='2025-01-01',valid_to='',base_lead_time_days='5',
            moq_units=str(pack_sizes[i-1]),pack_size_units=str(pack_sizes[i-1]),
            supplier_capacity_units='250')
        for j,store in enumerate(groups,start=1):
            policies.append(dict(policy_id=f'POL-{(j-1)*3+i:06}',sku_id=sku,store_id=store,
                supplier_sku_id=cid,status='ACTIVE',effective_from='2025-01-01',effective_to='',
                review_period_days='7',service_level='.95',safety_stock_units='6',
                capacity_limit_units='250'))
            key=(store,sku)
            selected[key]=(origin,{1:4.,7:4.,14:4.,28:4.})
            snapshots[(store,sku,day)]=dict(inventory_id=f'INV-{store}-{sku}-{day}',
                 store_id=store,sku_id=sku,snapshot_date=day,closing_stock=qty_onhand,
                 reserved_units=0)
            history[key]=[]
    for store in groups:
        coverage[(store,day)]={'missing_current_skus':[], 'synthetic_retired_stock_skus':[]}
    masters={'sku':list(skus.values()),'store':list(stores.values()),'supplier':[supplier],
        'supplier_sku':list(contracts.values()),'reorder_policy':policies,
        'lead_time_observation':[]}
    ordered=list(selected.items())
    if reverse:ordered.reverse()
    monkeypatch.setattr(re,'load_signed_manifest',lambda root:{})
    monkeypatch.setattr(re,'latest_selected',lambda root,limit:(dict(ordered),{'run_id':'FC-RUN-TEST'}))
    monkeypatch.setattr(re,'read_master',lambda root,manifest:masters)
    monkeypatch.setattr(re,'read_inventory_at_dates',lambda *args,**kwargs:(snapshots,None,deepcopy(coverage)))
    monkeypatch.setattr(re,'load_history',lambda root,keys:(history,None))
    monkeypatch.setattr(re,'digest',lambda path:'0'*64)
    return stores,skus,origin


def _run_fakes(monkeypatch,tmp_path,**kw):
    stores,skus,origin=_fixture_for_run(monkeypatch,**kw)
    out=tmp_path/'decisions'
    result=re.run(tmp_path,tmp_path,out,DecisionConfig())
    with (out/'purchase_recommendations.csv').open(newline='') as f:
        rows=list(csv.DictReader(f))
    for r in rows:
        r['decision_trace']=json.loads(r['decision_trace'])
        r['reason_codes']=json.loads(r['reason_codes'])
        r['recommended_quantity']=int(r['recommended_quantity'])
    return result,rows,stores,skus,origin


def _capacity_invariant(rows,stores,skus):
    from collections import defaultdict
    allocated=defaultdict(int)
    for row in rows:
        group=(row['store_id'],row['decision_date'])
        allocated[group]+=row['recommended_quantity']*int(skus[row['sku_id']]['storage_units_per_item'])
        qty=row['recommended_quantity']
        if qty:
            trace=row['decision_trace']
            assert qty % int(trace['supplier_contract_version']['pack'])==0
            assert qty >= int(trace['supplier_contract_version']['moq'])
            assert row['status']=='GENERATED'
    for (store,day),consumed in allocated.items():
        assert consumed<=int(stores[store]['capacity_units'])


def test_store_capacity_allocation_is_joint_and_input_order_independent(monkeypatch,tmp_path):
    _,rows,stores,skus,_=_run_fakes(monkeypatch,tmp_path/'first',capacity=36)
    _capacity_invariant(rows,stores,skus)
    assert len(rows)==6
    for store in stores:
        group=[r for r in rows if r['store_id']==store]
        assert sum(r['recommended_quantity'] for r in group)==36
        assert group[0]['recommended_quantity']==36
        assert all(r['recommended_quantity']==0 for r in group[1:])
        assert group[1]['status']=='BLOCKED'
        assert 'CAPACITY_INFEASIBLE' in group[1]['reason_codes']
        assert group[1]['decision_trace']['capacity_prior_allocated_storage_units']==36
    # Reversing forecast-map insertion order does not change priorities/IDs/output.
    _,reversed_rows,_,_,_=_run_fakes(monkeypatch,tmp_path/'second',reverse=True,capacity=36)
    assert rows==reversed_rows


def test_mixed_packs_and_capacity_boundaries(monkeypatch,tmp_path):
    _,rows,stores,skus,_=_run_fakes(monkeypatch,tmp_path/'mixed',
                                 capacity=40,pack_sizes=(12,10,12))
    _capacity_invariant(rows,stores,skus)
    for store in stores:
        group=[r for r in rows if r['store_id']==store]
        assert [r['recommended_quantity'] for r in group]==[36,0,0]
        assert all(r['recommended_quantity'] % int(r['decision_trace']['supplier_contract_version']['pack'])==0
                   for r in group)
    # With capacity below MOQ all positive candidates are safely blocked.
    _,tiny,_,_,_=_run_fakes(monkeypatch,tmp_path/'tiny',capacity=11)
    assert all(x['recommended_quantity']==0 for x in tiny)
    assert all(x['status']=='BLOCKED' for x in tiny)


def test_historical_master_state_and_shared_capacity_simultaneously(monkeypatch,tmp_path):
    stores,skus,origin=_fixture_for_run(monkeypatch,capacity=36)
    # Retired product, still eligible at this historic decision origin.
    retired=skus['SKU-000001']
    retired['status']='DISCONTINUED';retired['discontinue_date']='2026-02-01'
    out=tmp_path/'historical'
    re.run(tmp_path,tmp_path,out,DecisionConfig())
    with (out/'purchase_recommendations.csv').open(newline='') as f:
        rows=list(csv.DictReader(f))
    assert any(r['sku_id']=='SKU-000001' and int(r['recommended_quantity'])>0 for r in rows)
    # Retirement boundary is excluded even though the master label is identical.
    assert not sku_eligible_asof(retired,date(2026,2,1))


def test_forward_only_pg_migration_replaces_end_state_check():
    path=Path(__file__).resolve().parents[1]/'alembic'/'versions'/'0005_date_effective_sku_eligibility.py'
    text=path.read_text()
    upgrade=text.split('def upgrade():',1)[1].split('def downgrade():',1)[0]
    assert "down_revision = '0004'" in text
    assert 'CREATE OR REPLACE FUNCTION yottaflex_guard_recommendation_eligibility' in upgrade
    assert 'NEW.decision_date >= ending' in upgrade
    assert 'NEW.decision_date < launching' in upgrade
    assert "product_status = 'DISCONTINUED'" not in upgrade
    assert 'CREATE TABLE' not in upgrade and 'DELETE FROM' not in upgrade
    assert 'Unsafe downgrade' in text

# SQLite checks the importer code path. PostgreSQL trigger checks remain separate.
from test_phase5_integration import database, DATA


def _write_import_fixture(root, rec, run_id):
    root.mkdir(parents=True,exist_ok=True)
    (root/'replenishment_manifest.json').write_text(json.dumps({'run_id':run_id}))
    with (root/'purchase_recommendations.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rec))
        writer.writeheader();writer.writerow(rec)


def test_persistence_import_allows_historical_positive_and_rejects_postretirement(
        database,monkeypatch,tmp_path):
    import app.replenishment.verify as verifier
    monkeypatch.setattr(verifier,'verify',lambda root:{'status':'PASS'})
    with Session(database) as db,db.begin():
        persistence.import_dataset(db,DATA)
        sku=db.get(SKU,'SKU-000001')
        sku.status='DISCONTINUED'
        sku.discontinue_date=date(2026,2,1)
        db.add(PipelineRun(job_id='JOB-V6-PERSIST',idempotency_key='v6-import',
                           kind='IMPORT',status='SUCCEEDED',result={}))
    policy='POL-000001';contract='SSKU-000001'
    record=dict(recommendation_id='PO-2026-000001',policy_id=policy,
         supplier_sku_id=contract,sku_id='SKU-000001',store_id='STORE-000001',
         decision_date='2026-01-31',forecast_run_id='FC-TEST',inventory_date='2026-01-31',
         inventory_position='0',lead_time_demand='22',raw_quantity='24',
         recommended_quantity='24',status='GENERATED',reason_codes='[]',
         rule_version='P4-REPLENISH-v3',decision_trace='{}')
    positive=tmp_path/'historical'
    _write_import_fixture(positive,record,'RUN-HISTORIC')
    with Session(database) as db,db.begin():
        result=persistence.import_recommendations(db,positive,'JOB-V6-PERSIST')
        assert result['inserted_recommendations']==1
    with Session(database) as db:
        assert db.get(PurchaseRecommendation,'PO-2026-000001').recommended_quantity==24
    after={**record,'recommendation_id':'PO-2026-000002',
           'decision_date':'2026-02-01','inventory_date':'2026-02-01'}
    invalid=tmp_path/'after'
    _write_import_fixture(invalid,after,'RUN-AFTER')
    with pytest.raises(ValueError,match='outside SKU lifecycle'):
        with Session(database) as db,db.begin():
            persistence.import_recommendations(db,invalid,'JOB-V6-PERSIST')
    after['status']='BLOCKED';after['recommended_quantity']='0'
    blocked=tmp_path/'blocked'
    _write_import_fixture(blocked,after,'RUN-BLOCKED')
    with Session(database) as db,db.begin():
        assert persistence.import_recommendations(db,blocked,'JOB-V6-PERSIST')['inserted_recommendations']==1
    with Session(database) as db:
        rec=db.get(PurchaseRecommendation,'PO-2026-000002')
        assert rec.reason_codes==['DISCONTINUED_SKU']
        assert rec.recommended_quantity==0


def test_verified_occupied_stock_counted_once(monkeypatch,tmp_path):
    _,rows,stores,skus,_=_run_fakes(monkeypatch,tmp_path,capacity=40,qty_onhand=2)
    # Three current snapshots consume six store storage units; each store has
    # only 34 free, never three independent 34-unit budgets.
    for store in stores:
        group=[r for r in rows if r['store_id']==store]
        first=group[0]
        trace=first['decision_trace']
        assert trace['store_occupied_storage_units']==6
        assert trace['capacity_remaining_before_storage_units']==34
        assert first['recommended_quantity']==24
        assert sum(r['recommended_quantity'] for r in group)<=34
        assert group[1]['decision_trace']['capacity_prior_allocated_storage_units']==24
    _capacity_invariant(rows,stores,skus)


def test_date_effective_engine_blocks_prelaunch_and_postretirement():
    # Same source SKU; no changing master end-state between historical origins.
    from test_phase4_replenishment import scenario as source_scenario
    scenario=source_scenario.__wrapped__()
    scenario['sku']['status']='DISCONTINUED'
    scenario['sku']['launch_date']='2026-01-01'
    scenario['sku']['discontinue_date']='2026-02-01'
    assert re.decide(**scenario)['recommended_quantity']>0
    before=deepcopy(scenario)
    before['origin']=date(2025,12,31)
    before['inventory']['snapshot_date']='2025-12-31'
    before['sales']=[]  # Historical origin must not see later sales.
    result=re.decide(**before)
    assert result['status']=='BLOCKED'
    assert 'DISCONTINUED_OR_INELIGIBLE_SKU' in result['reason_codes']
    after=deepcopy(scenario)
    after['origin']=date(2026,2,1)
    after['inventory']['snapshot_date']='2026-02-01'
    result=re.decide(**after)
    assert result['status']=='BLOCKED'
    assert 'DISCONTINUED_OR_INELIGIBLE_SKU' in result['reason_codes']


def test_migration_upgrade_emits_date_effective_postgresql_guard(monkeypatch):
    import importlib.util
    path=Path(__file__).resolve().parents[1]/'alembic'/'versions'/'0005_date_effective_sku_eligibility.py'
    spec=importlib.util.spec_from_file_location('yotta_v6_0005',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    collected=[]
    class FakeBind:
        dialect=type('Dialect',(),{'name':'postgresql'})()
    class FakeOp:
        def get_bind(self):return FakeBind()
        def execute(self,query):collected.append(str(query))
    monkeypatch.setattr(module,'op',FakeOp())
    module.upgrade()
    assert len(collected)==1
    sql=collected[0]
    assert 'CREATE OR REPLACE FUNCTION' in sql and 'FOR SHARE' in sql
    assert 'NEW.decision_date < launching' in sql
    assert 'NEW.decision_date >= ending' in sql
    assert "product_status = 'DISCONTINUED'" not in sql
    assert 'DROP ' not in sql and 'DELETE ' not in sql


def test_store_date_budgets_are_isolated(monkeypatch,tmp_path):
    stores,skus,origin=_fixture_for_run(monkeypatch,capacity=36)
    original_selected=re.latest_selected(tmp_path,48)[0]
    later=date(2026,2,1);later_day=later.isoformat()
    changed={key: (later,points) if key[1]=='SKU-000003' else (day,points)
             for key,(day,points) in original_selected.items()}
    monkeypatch.setattr(re,'latest_selected',lambda root,limit:(changed,{'run_id':'FC-RUN-TEST'}))
    original_snapshot_func=re.read_inventory_at_dates
    snapshots,_,coverage=original_snapshot_func(None,None,None,with_capacity_coverage=True)
    for store in stores:
        coverage[(store,later_day)]={'missing_current_skus':[], 'synthetic_retired_stock_skus':[]}
        for sku in skus:
            inv=deepcopy(snapshots[(store,sku,origin.isoformat())]);inv['snapshot_date']=later_day
            inv['inventory_id']=f'INV-{store}-{sku}-{later_day}'
            snapshots[(store,sku,later_day)]=inv
    monkeypatch.setattr(re,'read_inventory_at_dates',lambda *a,**kw:(snapshots,None,deepcopy(coverage)))
    out=tmp_path/'multi_day'
    re.run(tmp_path,tmp_path,out,DecisionConfig())
    with (out/'purchase_recommendations.csv').open(newline='') as f:
        rows=list(csv.DictReader(f))
    for row in rows:row['recommended_quantity']=int(row['recommended_quantity'])
    assert len(rows)==6
    for store in stores:
        first=sum(r['recommended_quantity'] for r in rows
                  if r['store_id']==store and r['decision_date']==origin.isoformat())
        second=sum(r['recommended_quantity'] for r in rows
                   if r['store_id']==store and r['decision_date']==later_day)
        assert first<=36 and second<=36
        assert first>0 and second>0


def test_signed_recommendations_verifier_rejects_jointly_infeasible_orders(monkeypatch,tmp_path):
    from app.replenishment.verify import verify
    from app.replenishment.inputs import digest
    _fixture_for_run(monkeypatch,capacity=36)
    out=tmp_path/'signed'
    re.run(tmp_path,tmp_path,out,DecisionConfig())
    manifest_path=out/'replenishment_manifest.json'
    manifest=json.loads(manifest_path.read_text())
    for name in manifest['files']:
        manifest['files'][name]=digest(out/name)
    manifest_path.write_text(json.dumps(manifest,sort_keys=True))
    assert verify(out)['status']=='PASS'
    path=out/'purchase_recommendations.csv'
    with path.open(newline='') as f:
        reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
    # Forge a second individually feasible 36-unit order in one store and
    # rehash the output manifest. It must still fail independent joint audit.
    victim=next(x for x in rows if x['sku_id']=='SKU-000002' and x['store_id']=='STORE-000001')
    victim['status']='GENERATED';victim['recommended_quantity']='36'
    victim['reason_codes']='["REORDER_POINT_TRIGGERED"]'
    trace=json.loads(victim['decision_trace'])
    trace['stock_capacity_available_units']=36
    trace['capacity_prior_allocated_storage_units']=0
    trace['capacity_remaining_before_storage_units']=36
    trace['capacity_remaining_after_storage_units']=0
    victim['decision_trace']=re.canonical(trace)
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');writer.writeheader();writer.writerows(rows)
    re.write_csv(out/'exceptions.csv',[row for row in rows if row['status']=='BLOCKED'],tuple(fields))
    manifest_path=out/'replenishment_manifest.json'
    manifest=json.loads(manifest_path.read_text())
    for filename in ('purchase_recommendations.csv','exceptions.csv'):
        manifest['files'][filename]=digest(out/filename)
    manifest_path.write_text(json.dumps(manifest,sort_keys=True))
    with pytest.raises(ValueError,match='prior store capacity|joint store capacity'):
        verify(out)

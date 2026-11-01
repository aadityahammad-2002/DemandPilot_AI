"""Phase 4 contracts, negative cases and immutable artifact integration."""
from __future__ import annotations
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import csv,json
import pytest

from app.replenishment.policy import (DecisionConfig,valid_interval,supplier_lead_times,
        demand_curve,stockout_volatility,safety_stock,feasible_lot)
from app.replenishment.engine import decide,run
from app.replenishment.inputs import latest_selected,read_master,load_signed_manifest,checked_path
from app.replenishment.verify import verify,compare
from app.forecasting.config import ForecastConfig
from app.forecasting.engine import forecast


@pytest.fixture
def cfg():return DecisionConfig()


@pytest.fixture
def scenario():
    origin=date(2026,1,31)
    product=dict(sku_id='SKU-000001',launch_date='2025-01-01',discontinue_date='',status='ACTIVE',storage_units_per_item='2')
    store=dict(store_id='STORE-000001',status='OPEN',open_date='2025-01-01',close_date='',capacity_units='500')
    supplier=dict(supplier_id='SUP-000001',status='ACTIVE',effective_from='2025-01-01',effective_to='')
    contract=dict(supplier_sku_id='SSKU-000001',sku_id='SKU-000001',supplier_id='SUP-000001',
        valid_from='2025-01-01',valid_to='',status='ACTIVE',base_lead_time_days='5',
        moq_units='12',pack_size_units='12',supplier_capacity_units='300')
    policy=dict(policy_id='POL-000001',sku_id='SKU-000001',store_id='STORE-000001',supplier_sku_id='SSKU-000001',
        effective_from='2025-01-01',effective_to='',status='ACTIVE',review_period_days='7',
        safety_stock_units='6',capacity_limit_units='200',service_level='.95')
    inventory=dict(inventory_id='INV-STORE-000001-SKU-000001-2026-01-31',
        store_id='STORE-000001',sku_id='SKU-000001',snapshot_date=origin.isoformat(),
        closing_stock=5,reserved_units=1)
    sales=[dict(date=origin-timedelta(days=i),sales=5,stockout=False,available=10)
           for i in range(1,30)]
    obs=[dict(observation_id='LTO-000001',supplier_sku_id='SSKU-000001',order_date='2026-01-01',
             expected_receipt_date='2026-01-06',actual_receipt_date='2026-01-08',
             observed_lead_time_days='7',delay_days='2',status='RECEIVED')]
    return dict(key=('STORE-000001','SKU-000001'),origin=origin,points={1:5.,7:6.,14:5.,28:5.},
        sku=product,store=store,supplier=supplier,contract=contract,policy=policy,
        inventory=inventory,store_occupancy=75,sales=sales,observations=obs,
        cfg=DecisionConfig(),forecast_run_id='FC-RUN-SAMPLE',rec_id='PO-2026-000001')


def test_valid_half_open_effective_date():
    assert valid_interval('2026-01-01','2026-02-01',date(2026,1,31))
    assert not valid_interval('2026-01-01','2026-02-01',date(2026,2,1))
    assert valid_interval('2026-01-01','',date(2030,1,1))


@pytest.mark.parametrize('changes',[{'max_pairs':0},{'max_extension_days':0},
    {'min_lead_observations':0},{'max_horizon_days':0},{'demand_volatility_floor':-1}])
def test_config_limits(changes):
    with pytest.raises(ValueError):DecisionConfig(**changes).validate()


def test_interpolation_and_explicit_extrapolation(cfg):
    curve,flags=demand_curve({1:3.,7:9.},10,cfg)
    assert curve[0]==3 and curve[3]==6 and curve[6]==9 and curve[-1]==9
    assert flags==['FLAT_TAIL_EXTRAPOLATION']
    with pytest.raises(ValueError, match='insufficient'):
        demand_curve({1:3.},80,cfg)


def test_nonfinite_and_negative_forecast_blocked(cfg):
    for value in (float('nan'),float('inf'),-1.):
        with pytest.raises(ValueError):demand_curve({1:value},1,cfg)


def test_future_supplier_observations_not_leaked(scenario):
    future={**scenario['observations'][0], 'observation_id':'LTO-000002',
         'order_date':'2026-01-29','expected_receipt_date':'2026-02-04',
         'actual_receipt_date':'2026-02-28','observed_lead_time_days':'30','delay_days':'24'}
    a=supplier_lead_times(scenario['contract'],scenario['observations'],scenario['origin'],scenario['cfg'])
    b=supplier_lead_times(scenario['contract'],scenario['observations']+[future],scenario['origin'],scenario['cfg'])
    assert a==b and a['observations']==1 and a['effective_days']==7


def test_negative_04_invalid_lead_time(scenario):
    contract={**scenario['contract'],'base_lead_time_days':'-3'}
    with pytest.raises(ValueError,match='NEG-04'):
        supplier_lead_times(contract,scenario['observations'],scenario['origin'],scenario['cfg'])
    bad={**scenario['observations'][0],'observed_lead_time_days':'-1'}
    with pytest.raises(ValueError,match='NEG-04'):
        supplier_lead_times(scenario['contract'],[bad],scenario['origin'],scenario['cfg'])


def test_stockouts_not_zero_labels(scenario):
    observed=scenario['sales']+[{'date':scenario['origin'],'sales':0,'available':0,'stockout':True}]
    q=stockout_volatility(observed,scenario['origin'],scenario['cfg'])
    assert q['censored_count']==1 and q['genuine_zero_count']==0
    assert q['uncensored_count']==29


def test_safety_stock_uncertainty_rises_with_lead_variability(cfg):
    quality={'sigma_sales_proxy':3,'censored_fraction':0}
    a=safety_stock([5]*14,{'effective_days':5,'observed_std_days':0},.95,4,quality,cfg)
    b=safety_stock([5]*14,{'effective_days':5,'observed_std_days':3},.95,4,quality,cfg)
    assert b['final_units']>a['final_units']>=4


def test_negative_02_pack_and_moq(cfg):
    r=feasible_lot(23,12,12,100,100,1000,1)
    assert r['quantity']==24 and r['status']=='GENERATED'
    r=feasible_lot(4,25,12,100,100,1000,1)
    assert r['quantity']==36


def test_infeasible_capacity_is_blocked(cfg):
    r=feasible_lot(23,24,12,200,200,10,1)
    assert r['status']=='BLOCKED' and r['quantity']==0 and 'CAPACITY_INFEASIBLE' in r['reasons']


def test_partial_capacity_still_respects_lot(cfg):
    r=feasible_lot(90,12,12,70,70,150,2)
    assert r['quantity']==60 and 'CAPACITY_PARTIAL' in r['reasons']


def test_decision_positive_trace(scenario):
    r=decide(**scenario)
    assert r['status']=='GENERATED' and r['recommended_quantity']>0
    assert r['recommended_quantity']%12==0
    assert r['decision_trace']['forecast_daily_curve']
    assert r['decision_trace']['pipeline_assumption']
    assert r['decision_trace']['lead_time']['observed_ids']==['LTO-000001']
    assert r['decision_trace']['reorder_point_units']>=r['lead_time_demand']


def test_negative_03_discontinued_blocks_positive(scenario):
    c=deepcopy(scenario);c['sku']['status']='DISCONTINUED';c['sku']['discontinue_date']='2026-01-30'
    r=decide(**c)
    assert r['status']=='BLOCKED' and r['recommended_quantity']==0
    assert 'DISCONTINUED_OR_INELIGIBLE_SKU' in r['reason_codes']


def test_policy_and_supplier_not_active(scenario):
    for name in ('policy','contract','supplier'):
        s=deepcopy(scenario)
        s[name]['status']='DISABLED' if name=='policy' else 'INACTIVE'
        r=decide(**s)
        assert r['status']=='BLOCKED' and r['recommended_quantity']==0


def test_future_inventory_rejected(scenario):
    s=deepcopy(scenario);s['inventory']['snapshot_date']='2026-02-01'
    with pytest.raises(ValueError,match='origin-aligned'):decide(**s)


def test_future_sales_rejected(scenario):
    s=deepcopy(scenario);s['sales'].append({'date':date(2026,2,1),'sales':900,'stockout':False,'available':999})
    with pytest.raises(ValueError,match='NEG-05'):decide(**s)


def test_same_inputs_same_decision(scenario):
    assert decide(**deepcopy(scenario))==decide(**deepcopy(scenario))


def test_reorder_point_above_position_required(scenario):
    s=deepcopy(scenario);s['inventory']['closing_stock']=200
    r=decide(**s)
    assert r['recommended_quantity']==0


@pytest.fixture(scope='module')
def integration(tmp_path_factory):
    data=Path(__file__).resolve().parents[2]/'examples'/'smoke_run'
    out=tmp_path_factory.mktemp('p4')
    f3=out/'phase3';forecast(data,f3,ForecastConfig(max_pairs=36,model_iterations=20))
    run(data,f3,out/'p4a',DecisionConfig(max_pairs=36))
    return data,f3,out


def test_integration_signed_outputs(integration):
    data,f3,out=integration
    result=verify(out/'p4a')
    assert result['status']=='PASS' and result['rows']==36
    summary=json.loads((out/'p4a'/'summary.json').read_text())
    assert summary['positive']>0 and summary['blocked']>=0
    assert summary['database_mutations'] is False
    assert summary['full_scale_generation']=='DEFERRED_NOT_EXECUTED'


def test_integration_repeat_reproducible(integration):
    data,f3,out=integration
    run(data,f3,out/'p4b',DecisionConfig(max_pairs=36))
    result=compare(out/'p4a',out/'p4b')
    assert result['status']=='PASS' and result['files_compared']==7


def test_nonoverwrite_and_scope_guard(integration):
    data,f3,out=integration
    with pytest.raises(FileExistsError):run(data,f3,out/'p4a',DecisionConfig(max_pairs=36))
    with pytest.raises(ValueError,match='bounded scope'):
        run(data,f3,out/'excess',DecisionConfig(max_pairs=5))


def test_tamper_recommendation_detected(integration):
    data,f3,out=integration
    p=out/'p4a'/'purchase_recommendations.csv';original=p.read_bytes()
    try:
        p.write_bytes(original+b'corruption')
        with pytest.raises(ValueError,match='corrupt'):verify(out/'p4a')
    finally:p.write_bytes(original)


def test_ground_truth_not_needed_for_replenishment(integration,tmp_path):
    # The engine consumes neither ground truth nor oracle evaluation artifacts.
    data,f3,out=integration
    from app.replenishment.inputs import read_master
    m=load_signed_manifest(data)
    assert 'restricted_ground_truth' not in str(read_master(data,m))
    with (out/'p4a'/'purchase_recommendations.csv').open() as f:
        assert 'latent_demand' not in f.read()


def test_model_and_policy_fks_are_exact(integration):
    data,f3,out=integration
    master=read_master(data,load_signed_manifest(data))
    policies={r['policy_id'] for r in master['reorder_policy']}
    contracts={r['supplier_sku_id'] for r in master['supplier_sku']}
    with (out/'p4a'/'purchase_recommendations.csv').open(newline='') as f:
        for row in csv.DictReader(f):
            assert row['policy_id'] in policies and row['supplier_sku_id'] in contracts


def test_over_capacity_store_blocks_corrupt_snapshot(scenario):
    # A partially corrupt occupancy snapshot must never create false free space.
    invalid=deepcopy(scenario)
    invalid['store_occupancy']=int(invalid['store']['capacity_units'])+1
    with pytest.raises(ValueError,match='occupancy/capacity'):
        decide(**invalid)
    invalid['store_occupancy']=-1
    with pytest.raises(ValueError,match='occupancy/capacity'):
        decide(**invalid)

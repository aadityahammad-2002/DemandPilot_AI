"""New isolated, deterministic VFinal regression and adversarial coverage."""
from datetime import date,timedelta
from pathlib import Path
import csv
import json
import pytest

from app.generation.config import GeneratorConfig
from app.generation.generator import generate
from app.generation.verify import validate
from app.generation.negative_overlays import SCENARIOS,verify_overlays,detect
from app.replenishment.open_orders import load_open_orders,pipeline_at
from app.replenishment.backtest import simulate,_round_lot
from app.forecasting.features import corrected_history,infer_path
from app.forecasting.engine import temporal_integrity,matched_censor_ablation
from app.forecasting.evaluation import metrics


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    root=tmp_path_factory.mktemp('final')/'sample'
    cfg=GeneratorConfig(sku_count=12,store_count=3,days=35,format='csv',store_batch=1)
    generate(cfg,root)
    return root


def test_all_five_real_overlays_and_linked_labels(generated):
    manifest=json.loads((generated/'manifest.json').read_text())
    result=verify_overlays(generated,manifest)
    assert result['status']=='PASS'
    assert result['issue_types']==list(SCENARIOS)
    assert result['classification']=={'tp':5,'fp':0,'tn':5,'fn':0,'precision':1.,'recall':1.,'f1':1.}
    for label in (json.loads(x) for x in (generated/'restricted_ground_truth'/'injected_issue_labels.jsonl').read_text().splitlines()):
        assert label['entity_id']
        assert label['baseline_value'] != label['injected_value']
        assert (generated/label['overlay_path']).is_file()
    assert validate(generated)['negative_overlay_validation']['status']=='PASS'


def test_missing_wrong_ground_truth_reference_rejected(generated,tmp_path):
    manifest=json.loads((generated/'manifest.json').read_text())
    bad=json.loads(json.dumps(manifest))
    bad['negative_overlay_contract']['ground_truth']='restricted_ground_truth/MISSING.jsonl'
    with pytest.raises(FileNotFoundError):
        verify_overlays(generated,bad)


def test_negative_detector_controls_dont_use_issue_labels(generated):
    labels=[json.loads(x) for x in (generated/'restricted_ground_truth'/'injected_issue_labels.jsonl').read_text().splitlines()]
    for label in labels:
        bad=label['injected_value'];good=label['baseline_value'];issue=label['scenario_id']
        assert detect(issue,bad,baseline=good)
        assert not detect(issue,good,baseline=good)


def test_open_order_validation_and_double_counting_guard(tmp_path):
    csv_file=tmp_path/'orders.csv'
    with csv_file.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['external_order_id','store_id','sku_id','expected_receipt_date','outstanding_units','status'])
        w.writeheader()
        w.writerow(dict(external_order_id='EXTPO-123',store_id='STORE-000001',sku_id='SKU-000001',expected_receipt_date='2026-02-05',outstanding_units=12,status='IN_TRANSIT'))
        w.writerow(dict(external_order_id='EXTPO-124',store_id='STORE-000001',sku_id='SKU-000001',expected_receipt_date='2026-02-09',outstanding_units=0,status='RECEIVED'))
    mapping=load_open_orders(csv_file)
    entries=mapping[('STORE-000001','SKU-000001')]
    assert pipeline_at(entries,date(2026,2,1),10)==(12,['EXTPO-123'])
    assert pipeline_at(entries,date(2026,2,1),2)==(0,[])
    with pytest.raises(ValueError,match='overdue'):
        pipeline_at(entries,date(2026,2,6),10)
    with csv_file.open('a',encoding='utf-8') as f:
        f.write('EXTPO-123,STORE-000001,SKU-000001,2026-03-01,3,OPEN\n')
    with pytest.raises(ValueError,match='duplicate'):
        load_open_orders(csv_file)


def test_counterfactual_same_realization_conservation_and_cost():
    demand=[3,3,3,3,3,3,3]
    receipts=[0,0,0,0,0,0,0]
    a=simulate(daily_demand=demand,receipts=receipts,on_hand=4,order_units=0,lead_days=3)
    b=simulate(daily_demand=demand,receipts=receipts,on_hand=4,order_units=24,lead_days=3)
    assert a['fulfilled_units']+a['lost_units']==b['fulfilled_units']+b['lost_units']==21
    assert b['lost_units']<a['lost_units']
    assert b['peak_closing_stock']>a['peak_closing_stock']
    with pytest.raises(ValueError):simulate(daily_demand=[-1],receipts=[0],on_hand=0,order_units=0,lead_days=1)
    assert _round_lot(13,12,12,24)==24
    assert _round_lot(25,12,12,24)==0


def test_censor_correction_is_causal_and_ablation_changes_only_input_values():
    today=date(2026,1,1)
    history=[dict(date=today+timedelta(days=i),sales=(10 if i==0 else 0),available=(20 if i==0 else 0),stockout=(i>0),promo=False) for i in range(9)]
    original=corrected_history(history)
    changed=history+[dict(date=today+timedelta(days=9),sales=999,available=1000,stockout=False,promo=False)]
    assert corrected_history(changed)[:9]==original
    assert corrected_history(history,correction_enabled=False)[1]==0
    assert original[1]>=0
    with pytest.raises(ValueError,match='future'):
        infer_path(history,today+timedelta(days=1),1,'seasonal_naive',None,{}, {})


def test_temporal_target_alignment_fails_closed():
    row={'forecast_origin':'2026-04-01','target_date':'2026-04-08','horizon':7,
         'feature_cutoff':'2026-04-01','feature_source':'public_sales_history_only'}
    assert temporal_integrity([row])['status']=='PASS'
    with pytest.raises(ValueError,match='target alignment'):
        temporal_integrity([{**row,'target_date':'2026-05-08'}])
    with pytest.raises(ValueError,match='training cutoff'):
        temporal_integrity([{**row,'feature_cutoff':'2026-04-09'}])


def test_mase_uses_past_only_denominator_and_handles_zero():
    good=metrics([dict(actual_units=4,predicted_units=6,censored_target=False,mase_scale=4)])
    assert good['mase']==0.5
    assert metrics([dict(actual_units=0,predicted_units=0,censored_target=False)])['mase'] is None

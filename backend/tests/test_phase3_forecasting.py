"""Phase 3 forecasting contracts. No PostgreSQL changes and no full-scale runs."""
from __future__ import annotations
from datetime import date, timedelta
import csv
import json
import math
from pathlib import Path
import pytest

from app.forecasting.config import ForecastConfig, chronological_split
from app.forecasting.data import load_history, master_info, select_pairs
from app.forecasting.engine import (METHODS, train_global, produce_rows,
                                    assign_forecast_ids, forecast)
from app.forecasting.features import (FEATURE_NAMES, censored, corrected_history,
                                       enforce_origin, feature_vector, baseline_predict,
                                       infer_path)
from app.forecasting.evaluation import metrics, choose_method, evaluate
from app.forecasting.verify import verify_run, compare_runs
from app.forecasting.oracle import oracle_evaluate


@pytest.fixture(scope='module')
def source():
    return Path(__file__).resolve().parents[2] / 'examples' / 'smoke_run'


@pytest.fixture(scope='module')
def context(source):
    manifest=json.loads((source/'manifest.json').read_text())
    cfg=ForecastConfig(max_pairs=36,model_iterations=35)
    pairs=select_pairs(manifest,cfg.max_pairs)
    hist,_=load_history(source,pairs)
    prod,stores=master_info(source)
    split=chronological_split(date.fromisoformat(manifest['config']['start_date']),manifest['config']['days'])
    return cfg,hist,prod,stores,split


def test_split_365_exact():
    p=chronological_split(date(2026,1,1),365)
    assert [(b-a).days for a,b in p.values()] == [245,60,60]
    assert p['train'][1]==p['validation'][0]
    assert p['validation'][1]==p['test'][0]


def test_split_small_disjoint(context):
    split=context[4]
    assert [(b-a).days for a,b in split.values()]==[21,7,7]


@pytest.mark.parametrize('days',[0,10,20])
def test_short_history_rejected(days):
    with pytest.raises(ValueError): chronological_split(date(2026,1,1),days)


def test_config_bad_inputs():
    with pytest.raises(ValueError):ForecastConfig(max_pairs=0).validate()
    with pytest.raises(ValueError):ForecastConfig(horizons=(7,7)).validate()


def test_file_reader_public_only(context):
    cfg,hist,products,stores,split=context
    assert len(hist)==36
    assert all(len(rows)>0 for rows in hist.values())
    assert all(set(r)=={'date','sales','stockout','promo','multiplier','available'}
               for rows in hist.values() for r in rows)
    assert 'latent_demand' not in str(hist)


def test_features_never_use_hidden_master_rate(context):
    _,hist,prod,stores,_=context
    key=next(k for k,v in hist.items() if len(v)>8)
    history=hist[key][:8]; target=history[-1]['date']+timedelta(days=1)
    a=feature_vector(history,target,prod[key[1]],stores[key[0]])
    product={**prod[key[1]],'base_demand_rate':'999999','status':'DISCONTINUED'}
    b=feature_vector(history,target,product,stores[key[0]])
    assert a==b and len(a)==len(FEATURE_NAMES)


def test_negative_01_zero_availability_is_censored():
    day=date(2026,1,1)
    prior=[{'date':day+timedelta(days=i),'sales':6,'available':10,
            'stockout':False,'promo':False,'multiplier':1.} for i in range(7)]
    stockout={'date':day+timedelta(days=7),'sales':0,'available':0,
              'stockout':False,'promo':False,'multiplier':1.}
    real_zero={**stockout,'date':day+timedelta(days=8),'available':10}
    assert censored(stockout) and not censored(real_zero)
    assert corrected_history(prior+[stockout])[-1]==6.
    assert corrected_history(prior+[real_zero])[-1]==0.


def test_imputation_not_below_observed_sales():
    base={'stockout':False,'promo':False,'multiplier':1.,'available':20}
    seq=[{**base,'date':date(2026,1,1),'sales':1},
         {**base,'date':date(2026,1,2),'sales':8,'stockout':True}]
    assert corrected_history(seq)[-1]>=8


def test_negative_05_future_history_rejected(context):
    _,hist,prod,stores,_=context
    key=next(iter(hist));rows=hist[key]
    origin=rows[10]['date']
    with pytest.raises(ValueError, match='NEG-05'):
        enforce_origin(rows,origin)
    with pytest.raises(ValueError):
        feature_vector(rows[:12],rows[10]['date'],prod[key[1]],stores[key[0]])


@pytest.mark.parametrize('baseline',('seasonal_naive','moving_average','croston_sba'))
def test_baselines_nonnegative_finite(context,baseline):
    _,hist,_,_,_=context
    history=next(v for v in hist.values() if len(v)>14)
    value=baseline_predict(history[:14],history[14]['date'],baseline)
    assert math.isfinite(value) and value>=0


def test_censored_metrics_excluded():
    rows=[dict(actual_units=3,predicted_units=3,censored_target=False),
          dict(actual_units=0,predicted_units=200,censored_target=True)]
    assert metrics(rows)['mae']==0
    assert metrics(rows)['n']==1


def test_all_zero_wape_undefined():
    m=metrics([dict(actual_units=0,predicted_units=2,censored_target=False)])
    assert m['wape'] is None and m['mae']==2


def test_choose_method_validation_only():
    val={'validation':{'model1':{'overall':{'n':2,'wape':.5,'mae':1}},
                       'model2':{'overall':{'n':2,'wape':.1,'mae':5}}},
         'test':{'model1':{'overall':{'n':2,'wape':0,'mae':0}}}}
    assert choose_method(val)=='model2'


def test_multistep_future_independence(context):
    cfg,hist,prod,stores,_=context
    key=next(k for k,v in hist.items() if len(v)>22)
    rows=hist[key]
    origin=rows[17]['date']
    history=[r for r in rows if r['date']<=origin]
    # Changes to future demand or promotions must not affect forecast at origin.
    copy=[{**r,'sales':999,'promo':True,'multiplier':99.} if r['date']>origin else r for r in rows]
    before=infer_path(history,origin,7,'moving_average',None,prod[key[1]],stores[key[0]])
    after=infer_path([r for r in copy if r['date']<=origin],origin,7,'moving_average',None,prod[key[1]],stores[key[0]])
    assert before==after


@pytest.fixture(scope='module')
def run_result(source,tmp_path_factory):
    target=tmp_path_factory.mktemp('p3')/'forecast_run'
    cfg=ForecastConfig(max_pairs=36,model_iterations=35)
    manifest=forecast(source,target,cfg)
    return target,manifest


def test_integrated_forecasts_and_versioning(run_result):
    target,manifest=run_result
    result=verify_run(target)
    assert result['status']=='PASS'
    assert result['forecast_rows']==manifest['rows_exported']>100
    assert len(manifest['model_versions'])==4
    assert manifest['selected_model'] in METHODS
    assert manifest['database_mutations'] is False


def test_frozen_predictions_exclude_targets(run_result):
    target,_=run_result
    with (target/'forecast_predictions.csv').open(newline='') as f:
        pred=list(csv.DictReader(f))
    assert pred and all('actual_units' not in p and 'latent_demand' not in p for p in pred)
    with (target/'evaluation_scoring.csv').open(newline='') as f:
        scoring=list(csv.DictReader(f))
    assert len(scoring)>0 and 'actual_units' in scoring[0]


def test_selected_and_versioned_formats(run_result):
    target,manifest=run_result
    with (target/'forecast_selected.csv').open(newline='') as f:
        selected=list(csv.DictReader(f))
    assert all(r['selection_status']=='SELECTED' for r in selected)
    assert len({r['forecast_id'] for r in selected})==len(selected)
    assert all(r['forecast_id'].startswith('FC-STORE-') for r in selected)
    assert all(r['forecast_origin']<r['target_date'] for r in selected)


def test_oracle_after_prediction_freeze(source,run_result):
    target,manifest=run_result
    before=(target/'forecast_predictions.csv').read_bytes()
    oracle=oracle_evaluate(source,target)
    assert oracle['distinct_truth_targets']>0
    assert oracle['model_selection_uses_oracle'] is False
    assert (target/'forecast_predictions.csv').read_bytes()==before
    with pytest.raises(FileExistsError):oracle_evaluate(source,target)


def test_integrity_tampering_detected(tmp_path,run_result):
    target,_=run_result
    # Verifier rejects a tampered file, no need to touch original artifacts.
    import shutil
    clone=tmp_path/'copy';shutil.copytree(target,clone)
    with (clone/'forecast_predictions.csv').open('a') as f:f.write('malformed\n')
    with pytest.raises(ValueError, match='corrupt'):
        verify_run(clone)


def test_same_seed_reproduction(source,tmp_path):
    cfg=ForecastConfig(max_pairs=12,model_iterations=20)
    a=tmp_path/'a';b=tmp_path/'b'
    forecast(source,a,cfg);forecast(source,b,cfg)
    assert compare_runs(a,b)['status']=='PASS'
    assert compare_runs(a,b)['files_compared']>=5


def test_origin_visible_promotion_calendar(tmp_path):
    from app.forecasting.promotions import read_calendar, known_multiplier
    p=tmp_path/'planned_promotions.csv'
    p.write_text('store_id,sku_id,target_date,known_at,promotion_multiplier\n'
                 'STORE-000001,SKU-000001,2026-02-01,2026-01-20,1.7\n',encoding='utf-8')
    schedule=read_calendar(p)
    target=date(2026,2,1)
    assert known_multiplier(schedule,'STORE-000001','SKU-000001',target,date(2026,1,19))==1.0
    assert known_multiplier(schedule,'STORE-000001','SKU-000001',target,date(2026,1,20))==1.7


def test_known_promo_changes_future_inference_only_when_announced(context):
    from app.forecasting.promotions import read_calendar
    _,hist,prod,stores,_=context
    key=next(k for k,r in hist.items() if len(r)>20)
    seq=hist[key]
    origin=seq[10]['date'];target=origin+timedelta(days=1)
    old=infer_path(seq[:11],origin,1,'moving_average',None,prod[key[1]],stores[key[0]])
    schedule={(key[0],key[1],target):(origin,1.8)}
    newer=infer_path(seq[:11],origin,1,'moving_average',None,prod[key[1]],stores[key[0]],schedule)
    assert newer[0]>=old[0]
    future_announced={(key[0],key[1],target):(origin+timedelta(days=1),1.8)}
    unseen=infer_path(seq[:11],origin,1,'moving_average',None,prod[key[1]],stores[key[0]],future_announced)
    assert unseen==old


def test_reject_invalid_promotion_calendar(tmp_path):
    from app.forecasting.promotions import read_calendar
    f=tmp_path/'promotions.csv'
    f.write_text('store_id,sku_id,target_date,known_at,promotion_multiplier\n'
                 'STORE-000001,SKU-000001,2026-02-01,2026-02-02,3\n')
    with pytest.raises(ValueError):read_calendar(f)


def test_bad_phase2_input_hash_rejected(source,tmp_path):
    import shutil
    from app.forecasting.data import load_history,select_pairs
    clone=tmp_path/'dataset'
    # Only public sales and manifest needed for the input-reader assertion.
    shutil.copytree(source,clone)
    manifest=json.loads((clone/'manifest.json').read_text())
    rel=next(x['path'] for x in manifest['files'] if x['path'].startswith('facts/sales_history/'))
    with (clone/rel).open('a',encoding='utf-8') as fp:fp.write('corrupt\n')
    with pytest.raises(ValueError,match='size mismatch|checksum mismatch'):
        load_history(clone,select_pairs(manifest,36))

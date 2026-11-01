from __future__ import annotations
import csv
from datetime import date
import json
from pathlib import Path
import pytest
from app.generation.config import GeneratorConfig
from app.generation.generator import generate,product_record,synthetic_rows, supplier_contract
from app.generation.verify import validate,compare_runs
from app.generation.sql_seed import export_sql
from app.generation.features import historical_features, validate_feature_batch
from app.generation.model_inputs import assert_model_input_allowed


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    root=tmp_path_factory.mktemp('p1t02')
    cfg=GeneratorConfig(seed=4001,sku_count=9,store_count=3,days=32,
                        start_date='2026-01-01',store_batch=1,format='csv')
    a=root/'run_a';b=root/'run_b'
    generate(cfg,a);generate(cfg,b)
    export_sql(a)
    return cfg,a,b


def test_complete_master_and_referential_validation(runs):
    cfg,a,_=runs
    result=validate(a)
    assert result['status']=='PASS'
    counts=result['entities']
    assert counts['sku']==cfg.sku_count
    assert counts['store']==cfg.store_count
    assert counts['reorder_policy']==cfg.sku_count*cfg.store_count
    assert counts['ground_truth_labels']==counts['sales_history']
    assert counts['inventory_snapshot']==counts['sales_history']+json.loads((a/'manifest.json').read_text())['summary']['retired_inventory_snapshot_days']
    assert counts['lead_time_observation']>0
    assert counts['forecast']==counts['purchase_recommendation']==0


def test_same_seed_twice_exact_checksums(runs):
    _,a,b=runs
    assert compare_runs(a,b)['status']=='PASS'


def test_different_seed_changes_demand(tmp_path):
    cfg=GeneratorConfig(seed=8001,sku_count=4,store_count=2,days=14,store_batch=1,format='csv')
    x=tmp_path/'x';y=tmp_path/'y'
    generate(cfg,x)
    generate(GeneratorConfig(**{**cfg.as_dict(),'seed':8002}),y)
    assert json.loads((x/'manifest.json').read_text())['summary']!=json.loads((y/'manifest.json').read_text())['summary']


def test_configurable_store_batch_does_not_change_pair_history():
    cfg=GeneratorConfig(seed=67,sku_count=5,store_count=3,days=22,format='csv')
    s=date(2026,1,1)
    sku=product_record(1,cfg,s);contract=supplier_contract(1,cfg,s)
    base=list(synthetic_rows(cfg,1,2,sku,contract,s))
    altered=GeneratorConfig(**{**cfg.as_dict(),'store_batch':1})
    other=list(synthetic_rows(altered,1,2,sku,contract,s))
    assert base==other


def test_lifecycle_launch_and_discontinuation(runs):
    cfg,a,_=runs
    product=list(csv.DictReader((a/'master'/'sku.csv').open(encoding='utf-8')))
    assert product[1]['status']=='DISCONTINUED'
    assert product[1]['discontinue_date']
    assert product[2]['launch_date']>cfg.start_date
    assert validate(a)['error_count']==0


def test_inventory_movement_journal(runs):
    _,a,_=runs
    manifest=json.loads((a/'manifest.json').read_text())
    assert manifest['technical_event_counts']['tech_inventory_movement_event']>0
    assert validate(a)['status']=='PASS'


def test_separate_truth_and_required_negative_cases(runs):
    _,a,_=runs
    negative=[json.loads(v) for v in (a/'negative_fixtures'/'mandatory_cases.jsonl').read_text().splitlines()]
    assert [x['scenario_id'] for x in negative]==['NEG-01','NEG-02','NEG-03','NEG-04','NEG-05']
    assert negative[0]['input']['hidden_latent_demand']>0
    assert negative[0]['input']['units_sold']==0
    assert negative[1]['input']['invalid_recommended_quantity']%negative[1]['input']['pack_size_units']!=0
    assert negative[2]['input']['expected_status']=='BLOCKED'
    assert negative[3]['input']['base_lead_time_days']<0
    assert negative[4]['input']['feature_sales_date']>negative[4]['input']['forecast_origin']
    with pytest.raises(PermissionError):
        assert_model_input_allowed(a/'restricted_ground_truth'/'hidden.csv')
    with pytest.raises(PermissionError):
        assert_model_input_allowed(a/'negative_fixtures'/'mandatory_cases.jsonl')


def test_public_feature_reader_no_latent_truth_and_no_future(runs):
    _,a,_=runs
    origin=date(2026,1,10)
    feats=list(historical_features(a,origin=origin))
    assert feats
    assert all(f['sales_date']<=origin.isoformat() and 'latent_demand' not in f for f in feats)
    validate_feature_batch(feats,origin=origin)
    with pytest.raises(ValueError,match='future sales'):
        validate_feature_batch([{'sales_date':'2026-01-11'}],origin=origin)


def test_sql_and_manifest_exports(runs):
    _,a,_=runs
    seed=a/'sql'/'master_seed.sql'
    content=seed.read_text()
    assert content.startswith('-- YottaFlex')
    assert content.rstrip().endswith('COMMIT;')
    assert 'INSERT INTO supplier_sku' in content
    assert 'INSERT INTO reorder_policy' in content
    assert (a/'generator_config.json').exists()
    assert (a/'validation_summary.json').exists()


def test_tamper_detection(runs,tmp_path):
    _,a,_=runs
    target=next((a/'facts'/'sales_history').glob('*/*.csv'))
    # Do not modify the shared fixture; test a byte-for-byte copy in a new directory.
    import shutil
    copied=tmp_path/'tampered'
    shutil.copytree(a,copied)
    dest=copied/target.relative_to(a)
    with dest.open('a',encoding='utf-8') as f: f.write('INJECTED_CORRUPTION\n')
    with pytest.raises(AssertionError,match='Validation failed'):
        validate(copied)


def test_reject_nonempty_target_and_invalid_config(runs):
    cfg,a,_=runs
    with pytest.raises(FileExistsError): generate(cfg,a)
    with pytest.raises(ValueError): GeneratorConfig(store_batch=0).validate()
    with pytest.raises(ValueError): GeneratorConfig(format='unknown').validate()
    with pytest.raises(ValueError): GeneratorConfig(days=366).validate()


def test_no_postgres_connection_required(runs,monkeypatch,tmp_path):
    cfg,_,_=runs
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://invalid-host/not-a-database')
    out=tmp_path/'isolated'
    generate(cfg,out)
    assert validate(out)['status']=='PASS'

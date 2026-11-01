from datetime import date
import json
import pytest
from app.generation.config import GeneratorConfig
from app.generation.generator import generate,product_record,supplier_contract,supplier_observations
from app.generation.verify import validate


def test_default_full_requirements_are_fixed():
    cfg=GeneratorConfig()
    cfg.validate()
    assert (cfg.sku_count,cfg.store_count,cfg.days)==(2000,100,365)
    assert cfg.sku_count*cfg.store_count*cfg.days==73_000_000
    assert cfg.format=='parquet'
    assert cfg.movement_journal


def test_generator_has_promotion_stockouts_intermittency_and_delays(tmp_path):
    cfg=GeneratorConfig(seed=20261030,sku_count=24,store_count=3,days=50,store_batch=1,format='csv')
    manifest=generate(cfg,tmp_path/'run')
    s=manifest['summary']
    assert s['promotion_days']>0
    assert s['stockout_censored_days']>0
    assert s['actual_zero_demand_days']>0
    assert s['lost_demand_units']>0
    assert s['received_units']>0
    assert s['latent_demand_units'] == s['observed_sales_units']+s['lost_demand_units']
    assert validate(tmp_path/'run')['status']=='PASS'
    obs=list(supplier_observations(cfg,date.fromisoformat(cfg.start_date)))
    assert any((int(row['delay_days'] or 0))>0 for row in obs)
    assert any(product_record(n,cfg,date(2026,1,1))['launch_date']>cfg.start_date for n in range(1,25))
    assert any(product_record(n,cfg,date(2026,1,1))['discontinue_date'] for n in range(1,25))


def test_config_rejects_invalid_file_format_and_id_bounds():
    with pytest.raises(ValueError): GeneratorConfig(sku_count=2001).validate()
    with pytest.raises(ValueError): GeneratorConfig(store_count=101).validate()
    with pytest.raises(ValueError): GeneratorConfig(sku_count=0).validate()
    with pytest.raises(ValueError): GeneratorConfig(movement_journal='yes').validate()
    with pytest.raises(FileNotFoundError): GeneratorConfig.from_json(__import__('pathlib').Path('/nonexistent/config.json'))

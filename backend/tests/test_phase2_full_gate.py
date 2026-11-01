"""Add-only tests for the full-size resource guard and disk-light reproduction.

All tests use tiny CSV data; real full Parquet remains candidate laptop gate.
"""
from __future__ import annotations
import json
from pathlib import Path
import pytest

from app.generation.config import GeneratorConfig
from app.generation.generator import generate
from app.generation.sql_seed import export_sql
from app.generation.verify import validate
from app.generation.full_gate import eligible_count, run
from app.generation.full_reproduce import compare_streaming


@pytest.fixture
def example(tmp_path):
    cfg=GeneratorConfig(seed=1105,sku_count=12,store_count=4,days=32,store_batch=2,format='csv')
    root=tmp_path/'source'
    manifest=generate(cfg,root)
    export_sql(root)
    validate(root)
    return cfg,root,manifest


def test_full_repro_comparison_matches_every_partition_without_duplicate_dataset(example):
    cfg,root,manifest=example
    out=compare_streaming(root)
    assert out['status']=='PASS'
    assert out['files_compared']==len(manifest['files'])
    assert out['no_second_full_dataset_written'] is True
    assert out['logical_rows_compared']>=3*manifest['entity_counts']['sales_history']
    assert not any(p.name.startswith('yottaflex-full-repro-') for p in root.parent.iterdir())


def test_full_repro_detects_manifest_checksum_mismatch(example):
    _,root,_=example
    p=root/'manifest.json'
    manifest=json.loads(p.read_text())
    target=next(f for f in manifest['files'] if f['path'].startswith('facts/sales_history/'))
    target['logical_sha256']='0'*64
    p.write_text(json.dumps(manifest))
    report=compare_streaming(root)
    assert report['status']=='FAIL'
    assert any('partition count/hash mismatch' in issue for issue in report['issues'])


def test_full_repro_rejects_config_divergence(example):
    _,root,_=example
    p=root/'generator_config.json'
    config=json.loads(p.read_text())
    config['seed']+=1
    p.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='Configuration differs'):
        compare_streaming(root)


def test_full_expected_eligibility_matches_real_small_generator(example):
    cfg,_,manifest=example
    assert eligible_count(cfg)==manifest['entity_counts']['sales_history']


def test_full_gate_does_not_overwrite_existing_output(tmp_path):
    out=tmp_path/'exists';out.mkdir()
    with pytest.raises(FileExistsError,match='already exists'):
        run(out)


def test_independent_repro_without_optional_movement_events(tmp_path):
    cfg=GeneratorConfig(seed=51,sku_count=4,store_count=2,days=15,
                        store_batch=2,format='csv',movement_journal=False)
    root=tmp_path/'no_journal'
    generate(cfg,root)
    export_sql(root)
    validate(root)
    result=compare_streaming(root)
    assert result['status']=='PASS'
    assert result['issue_count']==0


def test_regenerated_sql_is_included_in_comparison(example):
    _,root,_=example
    sql=root/'sql'/'master_seed.sql'
    sql.write_text(sql.read_text()+'\n-- tamper\n',encoding='utf-8')
    result=compare_streaming(root)
    assert result['status']=='FAIL'
    assert 'master SQL seed checksum mismatch' in result['issues']

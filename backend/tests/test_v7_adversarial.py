"""Adversarial V7 contracts: signed forecast masters and ambiguous commit outcomes."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.forecasting.data import master_info
from app.integration.jobs import create_job, execute_job
from app.integration.types import IntegrationBase, PipelineRun
from app.replenishment.lifecycle import sku_eligible_asof
from app.replenishment.policy import feasible_lot

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'smoke_run'


def test_forecast_master_fails_closed_when_signed_sku_file_is_modified(tmp_path):
    root = tmp_path / 'source'
    shutil.copytree(EXAMPLE, root)
    products_before, _ = master_info(root)
    target = root / 'master' / 'sku.csv'
    contents = target.read_text(encoding='utf-8')
    assert 'GROCERY' in contents
    target.write_text(contents.replace('GROCERY', 'HOME', 1), encoding='utf-8')
    with pytest.raises(ValueError, match='manifest|hash|digest|mismatch|signed'):
        master_info(root)
    assert products_before


def test_forecast_master_rejects_duplicate_identity_even_with_matching_manifest(tmp_path):
    root = tmp_path / 'source'
    shutil.copytree(EXAMPLE, root)
    target = root / 'master' / 'sku.csv'
    with target.open(newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    with target.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows([*rows, rows[0]])
    import hashlib
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest['files']:
        if entry['path'] == 'master/sku.csv':
            entry['bytes'] = target.stat().st_size
            entry['file_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
            break
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='duplicate'):
        master_info(root)


def test_published_verified_artifact_not_deleted_when_db_commit_is_ambiguous(tmp_path, monkeypatch):
    import app.forecasting.engine as forecasting_engine
    import app.forecasting.verify as forecasting_verify
    import app.integration.jobs as jobs
    engine = create_engine('sqlite+pysqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    IntegrationBase.metadata.create_all(engine)
    try:
        settings = {'artifacts_root':tmp_path/'artifacts','data_root':tmp_path/'input',
                    'max_pairs':1,'allow_demo_import':False}
        job_id,_,_ = create_job(engine,'FORECAST','ambiguous-commit-preserve-00001',{},settings,submit=False)
        def produce(_data, stage, _cfg):
            (stage/'forecast_manifest.json').write_text('complete verified contents')
        monkeypatch.setattr(forecasting_engine, 'forecast', produce)
        monkeypatch.setattr(forecasting_verify, 'verify_run', lambda path: {'status':'PASS'})
        def failed_commit(_db, _folder, _job_id):
            # Simulates a lost commit acknowledgement, where the application
            # cannot tell whether the database consumed the published artifact.
            raise RuntimeError('commit result ambiguous')
        monkeypatch.setattr(jobs, 'import_forecasts', failed_commit)
        assert execute_job(engine, job_id, 'FORECAST', {}, settings) is False
        published = settings['artifacts_root']/job_id/'forecast'/'forecast_manifest.json'
        assert published.read_text() == 'complete verified contents'
        assert not (settings['artifacts_root']/job_id/'forecast.staging').exists()
        with Session(engine) as db:
            assert db.get(PipelineRun,job_id).status=='FAILED'
        # Terminal idempotency ensures this path is not reused destructively.
        assert execute_job(engine,job_id,'FORECAST',{},settings) is False
        assert published.is_file()
    finally:
        IntegrationBase.metadata.drop_all(engine)
        engine.dispose()


def test_lifecycle_exhaustive_date_boundaries():
    launch, end = date(2026,2,9),date(2026,3,7)
    for current_status in ('ACTIVE','DISCONTINUED'):
        row={'launch_date':launch.isoformat(),'discontinue_date':end.isoformat(),'status':current_status}
        for delta in range(-80,120):
            day=launch+timedelta(days=delta)
            assert sku_eligible_asof(row,day) == (launch<=day<end)


def test_lot_invariants_exhaustive_small_integer_domain():
    # Independently calculate feasible lot maxima across 3,600 combinations.
    for raw in range(0,10):
        for moq in range(1,5):
            for pack in range(1,5):
                for free in range(0,24,3):
                    out=feasible_lot(raw,moq,pack,12,12,free,1)
                    quantity=out['quantity']
                    if quantity:
                        assert quantity>=moq and quantity%pack==0
                        assert quantity<=min(12,free)
                    else:
                        assert out['status'] in ('NO_ORDER','BLOCKED')
                    if raw==0:
                        assert quantity==0


def test_nan_model_prediction_is_rejected_not_silently_clipped_to_zero():
    from app.forecasting.features import infer_path
    class BrokenModel:
        def predict(self, _x):
            return [float('nan')]
    history=[{'date':date(2026,1,1)+timedelta(days=i),'sales':2,
              'stockout':False,'available':10,'promo':False,'multiplier':1.0}
             for i in range(8)]
    product={'sku_id':'SKU-000001','launch_date':'2025-01-01','category':'GROCERY'}
    store={'store_id':'STORE-000001','region':'North'}
    with pytest.raises(ValueError,match='nonfinite|invalid'):
        infer_path(history,date(2026,1,8),1,'ml_hgb',BrokenModel(),product,store)


def test_nonfinite_observed_availability_and_planned_multiplier_rejected():
    from app.forecasting.features import feature_vector, enforce_origin
    history=[{'date':date(2026,1,1),'sales':3,'available':float('nan'),
              'stockout':False,'promo':False,'multiplier':1.}]
    product={'sku_id':'SKU-000001','launch_date':'2025-01-01','category':'GROCERY'}
    store={'store_id':'STORE-000001','region':'North'}
    with pytest.raises(ValueError,match='invalid'):
        enforce_origin(history,date(2026,1,2))
    history[0]['available']=10
    with pytest.raises(ValueError,match='invalid'):
        feature_vector(history,date(2026,1,2),product,store,float('nan'))

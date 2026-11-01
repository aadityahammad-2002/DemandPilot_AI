"""Read-only artifact contract and independent same-seed comparison."""
from __future__ import annotations
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from app.ids import forecast_id


def file_hash(path: Path) -> str:
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def verify_run(out: Path) -> dict:
    manifest=json.loads((out/'forecast_manifest.json').read_text(encoding='utf-8'))
    errors=[]
    for name,digest in manifest['files'].items():
        p=(out/name).resolve()
        if not p.is_relative_to(out.resolve()) or not p.is_file() or file_hash(p)!=digest:
            errors.append(f'corrupt or missing: {name}')
    if errors:
        raise ValueError('; '.join(errors))
    seen=set();count=0;cutoff_fail=0;id_fail=0
    with (out/'forecast_predictions.csv').open(newline='',encoding='utf-8') as f:
        for r in csv.DictReader(f):
            count+=1
            if r['forecast_id'] in seen: errors.append('duplicate forecast ID')
            seen.add(r['forecast_id'])
            if not (r['forecast_origin'] < r['target_date'] and r['forecast_origin']==r['feature_cutoff']):
                cutoff_fail+=1
            if date.fromisoformat(r['target_date'])-date.fromisoformat(r['forecast_origin']) != __import__('datetime').timedelta(days=int(r['horizon'])):
                cutoff_fail+=1
            v=float(r['predicted_units'])
            if not math.isfinite(v) or v<0: errors.append('negative/nonfinite forecast')
            if r['run_id'] != manifest['run_id']: errors.append('run_id mismatch')
            if 'actual_units' in r or 'latent_demand' in r or 'stockout_censored' in r:
                errors.append('labels leaked into prediction artifact')
            if not r['forecast_id'].startswith('FC-'+r['store_id']+'-'+r['sku_id']+'-'+r['forecast_origin'][:7]):
                id_fail+=1
    if cutoff_fail:errors.append(f'bad feature date/horizon: {cutoff_fail}')
    if id_fail:errors.append(f'bad ID prefix: {id_fail}')
    if count!=manifest['rows_exported']:errors.append('row count mismatch')
    with (out/'forecast_selected.csv').open(newline='',encoding='utf-8') as f:
        chosen=list(csv.DictReader(f))
    if not chosen: errors.append('no selected predictions')
    if any(r['forecast_id'] not in seen or r['selection_status']!='SELECTED' for r in chosen):
        errors.append('selected predictions mismatch')
    evaluate=json.loads((out/'evaluation.json').read_text(encoding='utf-8'))
    if evaluate['selected_model']!=manifest['selected_model']:
        errors.append('selected model mismatch')
    if evaluate['oracle_truth_used']:
        errors.append('oracle truth must be excluded from core evaluation')
    result={'status':'PASS' if not errors else 'FAIL','forecast_rows':count,
            'unique_ids':len(seen),'selected_model':manifest['selected_model'],'errors':errors}
    if errors: raise ValueError(result)
    return result


def compare_runs(first: Path, second: Path) -> dict:
    a=json.loads((first/'forecast_manifest.json').read_text(encoding='utf-8'))
    b=json.loads((second/'forecast_manifest.json').read_text(encoding='utf-8'))
    differences=[name for name in sorted(set(a['files'])|set(b['files']))
                 if a['files'].get(name)!=b['files'].get(name)]
    for name in ('run_id','input_signature','rows_exported','selected_model','train_validation_test'):
        if a.get(name)!=b.get(name):differences.append(name)
    return {'status':'PASS' if not differences else 'FAIL',
            'files_compared':len(set(a['files'])|set(b['files'])),
            'differences':differences,'run_id':a['run_id']}

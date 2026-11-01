"""Separate POST-FREEZE oracle evaluator. This module is never imported by trainer.

Restricted ground truth cannot influence feature construction, forecasts,
model fitting, validation-model selection, or official prediction artifacts.
"""
from __future__ import annotations
from collections import defaultdict
import csv
import json
from pathlib import Path
from app.generation.verify import iter_rows
from app.ids import sale_id
from app.forecasting.evaluation import metrics
from app.forecasting.verify import verify_run


def oracle_evaluate(data_root: Path, forecast_run: Path) -> dict:
    verify_run(forecast_run)
    manifest=json.loads((data_root/'manifest.json').read_text(encoding='utf-8'))
    with (forecast_run/'forecast_predictions.csv').open(newline='',encoding='utf-8') as fp:
        rows=list(csv.DictReader(fp))
    targets=defaultdict(list)
    for r in rows:
        target_id=sale_id(r['store_id'],r['sku_id'],__import__('datetime').date.fromisoformat(r['target_date']))
        targets[target_id].append(r)
    observed={}; matched=set()
    truth_entries=[e for e in manifest['files'] if e['path'].startswith('restricted_ground_truth/ground_truth/')]
    for entry in truth_entries:
        path=(data_root/entry['path']).resolve()
        if not path.is_relative_to(data_root.resolve()) or not path.is_file():
            raise ValueError('invalid restricted ground truth artifact')
        for tr in iter_rows(path):
            key=tr['sale_id']
            if key not in targets: continue
            if key in matched:raise ValueError('duplicate oracle label')
            matched.add(key)
            demand=int(tr['latent_demand'])
            if demand<0: raise ValueError('invalid oracle demand')
            for p in targets[key]:
                b=(p['split'],p['model_version'],int(p['horizon']))
                observed.setdefault(b,[]).append({
                    'actual_units':demand,'predicted_units':float(p['predicted_units']),
                    'censored_target':False})
    if not matched:
        raise ValueError('no matching oracle labels found')
    out={'role':'POST_FREEZE_ORACLE_EVALUATION_ONLY',
         'prediction_rows':len(rows),'distinct_truth_targets':len(matched),
         'model_selection_uses_oracle':False,
         'metrics':{f'{k[0]}|{k[1]}|h{k[2]}':metrics(v)
                    for k,v in sorted(observed.items())}}
    path=forecast_run/'oracle_evaluation.json'
    if path.exists():raise FileExistsError('oracle report already exists; refusing overwrite')
    path.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return out

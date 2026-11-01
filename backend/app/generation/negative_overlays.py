"""Deterministic, schema-shaped, non-importable adversarial partitions.

Operational tables stay valid. Each overlay is a complete row of the underlying
entity schema; sidecar restricted labels describe the deliberate corruption.
No overlay is eligible for normal import (import uses a manifest allowlist).
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Iterator

from app.generation.writers import PartitionWriter
from app.ids import forecast_id, recommendation_id
from app.generation.verify import iter_rows

SCENARIOS = ('NEG-01', 'NEG-02', 'NEG-03', 'NEG-04', 'NEG-05')
PO_FIELDS = ('recommendation_id','policy_id','sku_id','store_id','supplier_sku_id',
    'decision_date','forecast_run_id','inventory_date','inventory_position',
    'lead_time_demand','raw_quantity','recommended_quantity','status',
    'reason_codes','rule_version','decision_trace')
FORECAST_FIELDS = ('forecast_id','run_id','sku_id','store_id','forecast_origin',
    'target_date','horizon','predicted_units','model_version','status','split',
    'feature_cutoff','feature_source','selection_status')


def first_fact(root: Path, manifest: dict, entity: str, *, prefer_stockout=False) -> dict:
    files = sorted((x for x in manifest['files'] if x['path'].startswith(f'facts/{entity}/')),
                   key=lambda entry: entry['path'])
    fallback = None
    for entry in files:
        for row in iter_rows(root/entry['path']):
            if fallback is None:
                fallback = dict(row)
            if not prefer_stockout or str(row.get('stockout_flag', '')).lower() in ('true','1'):
                return dict(row)
    if fallback is None:
        raise ValueError(f'Cannot prepare negative overlay without {entity} baseline rows')
    return fallback


def detect(issue: str, row: dict, *, baseline: dict | None = None) -> bool:
    """Evaluate the record without consulting its hidden ground-truth issue label."""
    if issue == 'NEG-01':
        # Known production record with a stockout has been re-labelled as a non-stockout;
        # the mismatch is derived from independently signed baseline evidence.
        return (baseline is not None and
                (str(baseline.get('stockout_flag','')).lower() in ('true','1') or
                 int(baseline.get('units_sold',0)) != int(row['units_sold'])) and
                (str(row.get('stockout_flag','')).lower() not in ('true','1') or
                 int(row['units_sold']) != int(baseline['units_sold'])))
    if issue == 'NEG-02':
        trace = json.loads(row['decision_trace']) if isinstance(row['decision_trace'], str) else row['decision_trace']
        pack = int(trace['pack_size_units']); moq = int(trace['moq_units'])
        qty = int(row['recommended_quantity'])
        return qty > 0 and (qty % pack != 0 or qty < moq)
    if issue == 'NEG-03':
        trace = json.loads(row['decision_trace']) if isinstance(row['decision_trace'], str) else row['decision_trace']
        return (int(row['recommended_quantity']) > 0 and
                date.fromisoformat(row['decision_date']) >= date.fromisoformat(trace['discontinue_date']))
    if issue == 'NEG-04':
        return int(row['observed_lead_time_days']) < 0
    if issue == 'NEG-05':
        cutoff = date.fromisoformat(row['feature_cutoff'])
        source = row['feature_source']
        if source.startswith('public_sales_history:'):
            used_day = date.fromisoformat(source.split(':',1)[1])
            return used_day > cutoff
        return False
    raise ValueError('unknown issue type')


def _write(root: Path, manifest: dict, relative: str, fields: tuple | list, rows: list[dict]):
    path = root/relative
    writer = PartitionWriter(path, fields, 'csv')
    for row in rows:
        writer.add(row)
    entry = writer.close()
    entry['path'] = relative
    entry['entity'] = 'negative_overlay' if relative.startswith('negative_overlays/') else 'restricted_issue_labels'
    manifest['files'].append(entry)


def generate_overlays(root: Path, manifest: dict, cfg, start: date, *, sale_override: dict | None = None) -> None:
    """Produce a genuine row per prescribed case with an independently linked label.

    This small controlled overlay is independent of cardinality and memory use of
    the full 73-million-potential-row baseline. Baseline tables are never altered.
    """
    from app.generation.generator import SALES_FIELDS, MASTER_COLUMNS
    from app.ids import sku_id

    sale = dict(sale_override) if sale_override is not None else first_fact(root, manifest, 'sales_history', prefer_stockout=True)
    baseline_sale = dict(sale)
    # If this bounded profile has no real stockout, deliberately introduce a
    # zero-demand misclassification on the existing public record; the baseline
    # reference still makes the corruption detectable without using issue labels.
    corrupted_sale = {**sale, 'stockout_flag': False,
                      'units_sold': 0, 'observed_available_units': 0}
    contracts = list(iter_rows(root/'master'/'supplier_sku.csv'))
    policy_path = root/'master'/'reorder_policy.csv'
    products = list(iter_rows(root/'master'/'sku.csv'))
    contract = dict(contracts[0]); bad_lead = {**contract, 'base_lead_time_days': -3}
    obs = dict(next(iter_rows(root/'master'/'lead_time_observation.csv')))
    bad_obs = {**obs, 'observed_lead_time_days': -3}
    sku = next((p for p in products if p['discontinue_date']), None)
    if sku is None:
        # Short profiles can have no lifecycle retirement. Anchor the test to a
        # real SKU and explicitly bound it to a synthetic effective-date overlay.
        sku = dict(products[0]); sku['discontinue_date'] = start.isoformat()
    policy = next((p for p in iter_rows(policy_path) if p['sku_id'] == sku['sku_id']), None)
    if policy is None:policy=next(iter_rows(policy_path))
    ct = next((c for c in contracts if c['supplier_sku_id'] == policy['supplier_sku_id']), contracts[0])
    order_day = max(start, date.fromisoformat(sku['discontinue_date']))
    common = {'policy_id': policy['policy_id'], 'sku_id': sku['sku_id'],
              'store_id': policy['store_id'], 'supplier_sku_id': ct['supplier_sku_id'],
              'decision_date': order_day.isoformat(), 'forecast_run_id': 'FC-RUN-NEGATIVE-OVERLAY',
              'inventory_date': order_day.isoformat(), 'inventory_position': 0,
              'lead_time_demand': 30, 'raw_quantity': 23, 'recommended_quantity': 0,
              'status': 'BLOCKED', 'reason_codes': '["NEGATIVE_OVERLAY"]',
              'rule_version': 'ADVERSARIAL-OVERLAY', 'decision_trace': '{}'}
    packing = {**common, 'recommendation_id': recommendation_id(order_day.year, 999998),
        'recommended_quantity': 23, 'status': 'GENERATED',
        'decision_trace': json.dumps({'pack_size_units': 12, 'moq_units': 24}, sort_keys=True)}
    retired = {**common, 'recommendation_id': recommendation_id(order_day.year, 999999),
        'recommended_quantity': 24, 'status': 'GENERATED',
        'decision_trace': json.dumps({'discontinue_date': sku['discontinue_date']}, sort_keys=True)}
    day = date.fromisoformat(sale['sales_date'])
    future = day + timedelta(days=1)
    fc = dict(zip(FORECAST_FIELDS, (
        forecast_id(sale['store_id'],sale['sku_id'], day.strftime('%Y-%m')),
        'FC-RUN-NEGATIVE-OVERLAY',sale['sku_id'],sale['store_id'],day.isoformat(),
        future.isoformat(),1,4.0,'negative-overlay-v1','VALIDATED','test',
        day.isoformat(),f'public_sales_history:{future.isoformat()}','CANDIDATE')))
    specs = [
      ('NEG-01','SalesHistory',corrupted_sale,baseline_sale,SALES_FIELDS),
      ('NEG-02','PurchaseRecommendation',packing,{**packing,'recommended_quantity':24},PO_FIELDS),
      ('NEG-03','PurchaseRecommendation',retired,{**retired,'recommended_quantity':0,'status':'BLOCKED'},PO_FIELDS),
      ('NEG-04','LeadTimeObservation',bad_obs,obs,MASTER_COLUMNS['lead_time_observation']),
      ('NEG-05','Forecast',fc,{**fc,'feature_source':f'public_sales_history:{day.isoformat()}'},FORECAST_FIELDS),
    ]
    labels=[];controls=[];detected=[]
    primary = {'SalesHistory':'sale_id','PurchaseRecommendation':'recommendation_id',
               'LeadTimeObservation':'observation_id','Forecast':'forecast_id'}
    for sid,entity,bad,good,fields in specs:
        identifier=bad[primary[entity]]
        _write(root,manifest,f'negative_overlays/{sid}/{entity.lower()}.csv',fields,[bad])
        truth={'scenario_id':sid,'issue_type':sid,'entity_type':entity,'entity_id':identifier,
               'overlay_path':f'negative_overlays/{sid}/{entity.lower()}.csv',
               'baseline_primary_key':good[primary[entity]],
               'baseline_value':good,'injected_value':bad,
               'expected_detection':True,'description':f'Deliberately corrupted {entity}; do not import'}
        labels.append(truth)
        actual=detect(sid,bad,baseline=good)
        control=detect(sid,good,baseline=good)
        controls.append((sid,control))
        detected.append((sid,actual))
    # Label names never enter the detector inputs. Ground truth is restricted.
    label_path='restricted_ground_truth/injected_issue_labels.jsonl'
    p=root/label_path;p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',encoding='utf-8',newline='\n') as handle:
        for row in labels:
            handle.write(json.dumps(row,sort_keys=True,separators=(',',':'),default=str)+'\n')
    manifest['files'].append({'path':label_path,'rows':len(labels),'bytes':p.stat().st_size,
        'file_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'entity':'restricted_issue_labels'})
    tp=sum(value for _,value in detected);fp=sum(value for _,value in controls)
    fn=len(detected)-tp;tn=len(controls)-fp
    precision=tp/(tp+fp) if tp+fp else None
    recall=tp/(tp+fn) if tp+fn else None
    f1=2*precision*recall/(precision+recall) if precision is not None and recall is not None and precision+recall else None
    manifest['negative_overlay_contract']={'version':1,'location':'negative_overlays/',
        'ground_truth':label_path,'baseline_tables_modified':False,
        'database_import_allowed':False,'issue_types':list(SCENARIOS),
        'row_count':len(labels),'control_count':len(controls),
        'detector_results':dict(detected),'control_false_positives':dict(controls),
        'classification':{'tp':tp,'fp':fp,'tn':tn,'fn':fn,'precision':precision,'recall':recall,'f1':f1},
        'note':'five targeted schema-shaped injected overlays + five untouched controls; NOT a population-level statistical detector benchmark'}


def verify_overlays(root: Path, manifest: dict) -> dict:
    contract=manifest['negative_overlay_contract']
    from app.generation.verify import iter_rows
    errors=[]
    labels_path=root/contract['ground_truth']
    labels=[json.loads(line) for line in labels_path.read_text(encoding='utf-8').splitlines()]
    if [x['scenario_id'] for x in labels]!=list(SCENARIOS):
        errors.append('Missing/duplicate/reordered mandatory negative labels')
    expected_fields={'SalesHistory':set(__import__('app.generation.generator',fromlist=['SALES_FIELDS']).SALES_FIELDS),
                     'PurchaseRecommendation':set(PO_FIELDS),
                     'LeadTimeObservation':set(__import__('app.generation.generator',fromlist=['MASTER_COLUMNS']).MASTER_COLUMNS['lead_time_observation']),
                     'Forecast':set(FORECAST_FIELDS)}
    for label in labels:
        rel=label['overlay_path']
        if not rel.startswith('negative_overlays/') or '..' in Path(rel).parts:
            errors.append('unsafe overlay filename');continue
        entry=next((e for e in manifest['files'] if e['path']==rel),None)
        if not entry or entry.get('rows')!=1:
            errors.append(f'missing signed overlay row: {rel}');continue
        records=list(iter_rows(root/rel))
        if len(records)!=1 or set(records[0])!=expected_fields[label['entity_type']]:
            errors.append(f'overlay schema invalid: {rel}');continue
        record=records[0]
        if record!= {k:str(v) for k,v in label['injected_value'].items()}:
            # CSV booleans normalize to True/False, JSON fields are strings.
            errors.append(f'overlay content/label mismatch: {rel}')
        key={'SalesHistory':'sale_id','PurchaseRecommendation':'recommendation_id',
             'LeadTimeObservation':'observation_id','Forecast':'forecast_id'}[label['entity_type']]
        if record[key]!=label['entity_id']:
            errors.append(f'overlay entity identity mismatch: {rel}')
        if not detect(label['scenario_id'],record,baseline=label['baseline_value']):
            errors.append(f'undetected issue: {rel}')
        if detect(label['scenario_id'],label['baseline_value'],baseline=label['baseline_value']):
            errors.append(f'false positive on baseline control: {rel}')
    return {'status':'PASS' if not errors else 'FAIL','errors':errors,
            'classification':contract['classification'],'issue_types':list(SCENARIOS),
            'overlay_rows':len(labels)}

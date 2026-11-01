"""Read only signed public Phase2/Phase3 artifacts; no truth files or DB access."""
from __future__ import annotations
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable
import csv, hashlib, json, re
from app.generation.verify import iter_rows
from app.generation.model_inputs import assert_model_input_allowed
from app.forecasting.verify import verify_run
from app.forecasting.data import load_history

MASTER = ('sku','store','supplier','supplier_sku','reorder_policy','lead_time_observation')
FORECAST_COLS = {'forecast_id','run_id','sku_id','store_id','forecast_origin',
                 'target_date','horizon','predicted_units','model_version',
                 'status','split','feature_cutoff','feature_source','selection_status'}


def digest(path:Path)->str:
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def load_signed_manifest(root:Path)->dict:
    p=root/'manifest.json'
    manifest=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(manifest,dict) or not isinstance(manifest.get('files'),list) or not manifest['files']:
        raise ValueError('empty or malformed generation manifest')
    paths=[]
    for entry in manifest['files']:
        if not isinstance(entry,dict) or not isinstance(entry.get('path'),str):
            raise ValueError('invalid manifest file entry')
        path=Path(entry['path'])
        if path.is_absolute() or '..' in path.parts or entry['path'].startswith('\\'):
            raise ValueError('unsafe manifest path')
        paths.append(entry['path'])
    if len(set(paths))!=len(paths):
        raise ValueError('duplicate generation manifest paths')
    return manifest


def checked_path(root:Path, entry:dict)->Path:
    path=(root/entry['path']).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('missing or unsafe phase2 input file')
    assert_model_input_allowed(path)
    if path.stat().st_size!=int(entry['bytes']) or digest(path)!=entry['file_sha256']:
        raise ValueError('Phase 2 input manifest mismatch: '+entry['path'])
    return path


def read_master(root:Path, manifest:dict)->dict:
    entries={x['path']:x for x in manifest['files']}
    result={}
    for name in MASTER:
        rel=f'master/{name}.csv'
        if rel not in entries: raise ValueError('missing manifest master '+name)
        rows=list(iter_rows(checked_path(root,entries[rel])))
        key = {'sku':'sku_id','store':'store_id','supplier':'supplier_id',
               'supplier_sku':'supplier_sku_id','reorder_policy':'policy_id',
               'lead_time_observation':'observation_id'}[name]
        if len({x[key] for x in rows})!=len(rows): raise ValueError('duplicate master identifier')
        result[name]=rows
    return result


def latest_selected(forecast_root:Path, max_pairs:int)->tuple[dict,dict]:
    # verify_run hashes every required artifact; no evaluation/oracle data is loaded by this method.
    verify_run(forecast_root)
    manifest=json.loads((forecast_root/'forecast_manifest.json').read_text(encoding='utf-8'))
    if 'forecast_selected.csv' not in manifest['files']: raise ValueError('selected forecast not signed')
    rows=defaultdict(lambda:defaultdict(dict))
    with (forecast_root/'forecast_selected.csv').open(newline='',encoding='utf-8') as fp:
        for x in csv.DictReader(fp):
            if set(x)!=FORECAST_COLS:raise ValueError('unknown or missing forecast columns')
            if x['selection_status']!='SELECTED' or x['status'] not in ('VALIDATED','PUBLISHED'):
                raise ValueError('unvalidated forecast selected')
            if x['run_id']!=manifest['run_id'] or not x['model_version'].startswith(f"phase3-{manifest['selected_model']}-"):
                raise ValueError('forecast run/model mismatch')
            origin=date.fromisoformat(x['forecast_origin']);target=date.fromisoformat(x['target_date'])
            h=int(x['horizon']); v=float(x['predicted_units'])
            import math
            if h<1 or (target-origin).days!=h or x['feature_cutoff']!=x['forecast_origin'] or not math.isfinite(v) or v<0:
                raise ValueError('NEG-05: invalid feature time, horizon or forecast value')
            if x['forecast_id'] != __import__('app.ids',fromlist=['forecast_id']).forecast_id(x['store_id'],x['sku_id'],x['forecast_origin'][:7],version={'seasonal_naive':1,'moving_average':2,'croston_sba':3,'ml_hgb':4}[manifest['selected_model']],sequence=_sequence_from_id(x['forecast_id'])):
                raise ValueError('forecast ID integrity failure')
            key=(x['store_id'],x['sku_id']); date_key=x['forecast_origin']
            if h in rows[key][date_key]: raise ValueError('duplicate horizon for pair/origin')
            rows[key][date_key][h] = v
    if not rows: raise ValueError('no eligible selected forecasts')
    if len(rows)>max_pairs:raise ValueError('selected pairs exceed configured bounded scope; rerun Phase 3 with fewer pairs')
    latest={key:(date.fromisoformat(max(variants)),variants[max(variants)]) for key,variants in sorted(rows.items())}
    return latest, manifest


def _sequence_from_id(value:str)->int:
    m=re.search(r'-N(\d{6})$',value)
    return int(m.group(1)) if m else 1


def read_inventory_at_dates(root:Path, manifest:dict, selected:dict, *, with_capacity_coverage:bool=False):
    """Read exact-day store occupancy, with verified historical fallback metadata.

    A historical fallback may ONLY be used by the decision engine for an already
    ineligible SKU, producing a blocked (zero-order) decision. An active SKU
    still requires an exact-origin inventory snapshot, without exceptions.
    """
    requested=defaultdict(set)
    selected_asof={}
    for store,sku in selected:
        day=selected[(store,sku)][0].isoformat()
        requested[store].add(day)
        selected_asof[(store,sku)]=day
    entries=[x for x in manifest['files'] if x['path'].startswith(('facts/inventory_snapshot/', 'facts/inventory_snapshot_retired/'))]
    inventory={}; dates=defaultdict(set); prior={}
    # Record all historically stocked SKU/store pairs, including those not in
    # the selected forecast subset. Absence from a current snapshot is UNKNOWN,
    # not a valid assumption of zero occupied storage.
    first_seen={}  # one date per SKU/store; bounded by pairs, not all historical days
    last_seen={}
    for e in sorted(entries,key=lambda x:x['path']):
        rel=e['path']
        m=re.search(r'inventory_snapshot(?:_retired)?_stores_(\d+)-(\d+)\.(csv|parquet)$',rel)
        if m and not any(int(m.group(1))<=int(st.split('-')[-1])<=int(m.group(2)) for st in requested):continue
        for r in iter_rows(checked_path(root,e)):
            store=r['store_id'];sku=r['sku_id'];day=str(r['snapshot_date'])
            prior_limit=selected_asof.get((store,sku))
            needed_day=day in requested.get(store,set())
            useful_prior=prior_limit is not None and day < prior_limit
            if store in requested and any(day<=asof for asof in requested[store]):
                pair=(store,sku)
                if pair in last_seen and day<=last_seen[pair]:
                    raise ValueError('duplicate inventory observation or out-of-order history')
                first_seen.setdefault(pair,day)
                last_seen[pair]=day
            if not needed_day and not useful_prior:continue
            inv={k:(int(v) if k in ('opening_stock','received_units','sold_units','adjustment_units','closing_stock','reserved_units') else v) for k,v in r.items()}
            if rel.startswith('facts/inventory_snapshot_retired/'):
                inv['synthetic_retired_carry_forward']=True
            if min(inv[k] for k in ('opening_stock','received_units','sold_units','closing_stock','reserved_units'))<0:
                raise ValueError('negative stock in signed snapshot')
            if inv['opening_stock']+inv['received_units']-inv['sold_units']+inv['adjustment_units'] != inv['closing_stock']:
                raise ValueError('inventory reconciliation failed')
            if inv['reserved_units']>inv['closing_stock']: raise ValueError('invalid reserved stock')
            if needed_day:
                key=(store,sku,day)
                if key in inventory:raise ValueError('duplicate inventory snapshot')
                inventory[key]=inv
                dates[store].add(day)
            if useful_prior:
                key=(store,sku)
                if key not in prior or day>prior[key]['snapshot_date']:
                    prior[key]=inv
    for store,sku in selected:
        d=selected_asof[(store,sku)]; key=(store,sku,d)
        if key not in inventory:
            old=prior.get((store,sku))
            if old is None:
                raise ValueError(f'no point-in-time inventory for {store}/{sku} at {d}')
            inventory[key]={**old,'historical_fallback_for_block_only':True}
    if with_capacity_coverage:
        # An active SKU with no current snapshot, or a previously stocked
        # discontinued SKU with unknown residual stock, makes store capacity
        # unprovable. Never count its stale quantity as current occupancy.
        coverage={}
        for store,days in requested.items():
            for day in days:
                missing=sorted(sku for (st,sku),first_day in first_seen.items()
                               if st==store and first_day<=day
                               and ((store,sku,day) not in inventory or
                                    inventory[(store,sku,day)].get('historical_fallback_for_block_only')))
                coverage[(store,day)]={'complete':not missing,
                    'missing_current_skus':missing,
                    'current_snapshot_count':sum(1 for (st,sku,d),row in inventory.items() if st==store and d==day
                                                 and not row.get('historical_fallback_for_block_only')),
                    'synthetic_retired_stock_skus':sorted(sku for (st,sku,d),row in inventory.items()
                        if st==store and d==day and row.get('synthetic_retired_carry_forward'))}
        return inventory,dates,coverage
    return inventory,dates

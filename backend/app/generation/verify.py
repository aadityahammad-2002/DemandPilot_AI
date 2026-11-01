"""Streaming validator and independent two-run logical/physical checksum comparison."""
from __future__ import annotations
import csv
import hashlib
import itertools
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

from app.ids import sale_id, inventory_id
from app.generation.writers import canonical


def iter_rows(path: Path) -> Iterator[dict]:
    if path.suffix == '.csv':
        with path.open(newline='',encoding='utf-8') as f:
            yield from csv.DictReader(f)
    elif path.suffix == '.parquet':
        try: import pyarrow.parquet as pq
        except ImportError as e: raise RuntimeError('Install pyarrow for Parquet validation') from e
        pf=pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=4096):
            yield from batch.to_pylist()
    else:
        raise ValueError(f'Unsupported fact path: {path}')


def _boolean(v: object) -> bool:
    if isinstance(v,bool): return v
    if v in ('True','true','1'): return True
    if v in ('False','false','0'): return False
    raise ValueError(f'Invalid boolean: {v}')


def verify_file(root: Path, entry: dict) -> None:
    p = (root / entry['path']).resolve()
    if not p.is_relative_to(root.resolve()): raise ValueError('manifest path traversal')
    if not p.is_file(): raise ValueError(f'missing file {entry["path"]}')
    with p.open('rb') as f:
        h=hashlib.file_digest(f,'sha256').hexdigest()
    if h != entry['file_sha256'] or p.stat().st_size != entry['bytes']:
        raise ValueError(f'file checksum/size mismatch: {entry["path"]}')
    if 'logical_sha256' in entry:
        digest=hashlib.sha256()
        counter=0
        for row in iter_rows(p):
            digest.update(canonical(row))
            counter+=1
        if counter != entry['rows'] or digest.hexdigest() != entry['logical_sha256']:
            raise ValueError(f'logical checksum or row-count mismatch: {entry["path"]}')


def validate(root: Path, *, write_report: bool = True) -> dict:
    root=root.resolve()
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    errors=[]
    # File hashes and logical row checks are mandatory, not just summary counts.
    for entry in manifest['files']:
        try: verify_file(root,entry)
        except (ValueError, OSError) as exc: errors.append(str(exc))
    expected=manifest['entity_counts']
    for entity in ['sku','store','supplier','supplier_sku','reorder_policy','lead_time_observation',
                   'sales_history','inventory_snapshot','ground_truth_labels']:
        if entity not in expected: errors.append(f'missing entity count {entity}')
    def read_master(name):
        return iter_rows(root/'master'/f'{name}.csv')
    products={r['sku_id']:r for r in read_master('sku')}
    stores={r['store_id']:r for r in read_master('store')}
    suppliers={r['supplier_id']:r for r in read_master('supplier')}
    contracts={r['supplier_sku_id']:r for r in read_master('supplier_sku')}
    if len(products)!=expected['sku']: errors.append('duplicate SKU IDs')
    if len(stores)!=expected['store']: errors.append('duplicate Store IDs')
    if len(suppliers)!=expected['supplier']: errors.append('duplicate Supplier IDs')
    if len(contracts)!=expected['supplier_sku']: errors.append('duplicate SupplierSKU IDs')
    for item in contracts.values():
        if item['sku_id'] not in products or item['supplier_id'] not in suppliers:
            errors.append('supplier/sku parent FK violation')
        if int(item['base_lead_time_days']) <= 0 or int(item['pack_size_units']) <= 0:
            errors.append('invalid contract lead time / pack size')
    expected_policies = expected['reorder_policy']
    policies_seen=set()
    for r in read_master('reorder_policy'):
        key=(r['sku_id'],r['store_id'])
        if key in policies_seen: errors.append(f'duplicate active policy {key}')
        policies_seen.add(key)
        if r['sku_id'] not in products or r['store_id'] not in stores or r['supplier_sku_id'] not in contracts:
            errors.append('reorder-policy FK missing')
        elif r['sku_id'] != contracts[r['supplier_sku_id']]['sku_id']:
            errors.append('reorder-policy composite contract mismatch')
        if r['effective_to'] and r['effective_to'] <= r['effective_from']:
            errors.append('invalid policy effective interval')
    if len(policies_seen)!=expected_policies:
        errors.append('policy count/uniqueness mismatch')
    count_leads=0
    for obs in read_master('lead_time_observation'):
        count_leads+=1
        if obs['supplier_sku_id'] not in contracts: errors.append('lead-time observation FK missing')
        if obs['actual_receipt_date']:
            observed=(date.fromisoformat(obs['actual_receipt_date'])-date.fromisoformat(obs['order_date'])).days
            if str(observed) != obs['observed_lead_time_days'] or observed < 0:
                errors.append('lead time derived arithmetic mismatch')
            delay=(date.fromisoformat(obs['actual_receipt_date'])-date.fromisoformat(obs['expected_receipt_date'])).days
            if str(delay) != obs['delay_days']: errors.append('delay-day arithmetic mismatch')
        elif obs['observed_lead_time_days'] or obs['delay_days']:
            errors.append('incomplete observation has derived values')
    if count_leads != expected['lead_time_observation']: errors.append('lead-time row count mismatch')

    sales_entries=[x for x in manifest['files'] if x['path'].startswith('facts/sales_history/')]
    last_state={}
    stats=Counter()
    for entry in sorted(sales_entries,key=lambda x:x['path']):
        event_path=root/entry['path'].replace('facts/sales_history/','technical_events/tech_inventory_movement_event/').replace('sales_history_stores_','tech_inventory_movement_event_stores_')
        events_by_inventory={}
        if manifest['config'].get('movement_journal',False) and event_path.exists():
            for ev in iter_rows(event_path):
                events_by_inventory.setdefault(ev['inventory_id'],[]).append(ev)
        elif manifest['config'].get('movement_journal',False):
            # No movement rows may be legitimate for a sparse slice, but all partitions
            # containing a first inventory snapshot have at least one OPENING event.
            pass
        sales_path=root/entry['path']
        inv_path=root/entry['path'].replace('facts/sales_history/','facts/inventory_snapshot/').replace('sales_history_stores_','inventory_snapshot_stores_')
        truth_path=root/entry['path'].replace('facts/sales_history/','restricted_ground_truth/ground_truth/').replace('sales_history_stores_','ground_truth_stores_')
        for sale,inv,truth in itertools.zip_longest(iter_rows(sales_path),iter_rows(inv_path),iter_rows(truth_path)):
            if None in (sale,inv,truth):
                errors.append(f'unaligned sales, inventory, truth partitions: {entry["path"]}')
                break
            sid=sale['sku_id']; stid=sale['store_id']; day=sale['sales_date']
            if sid not in products or stid not in stores:
                errors.append('sales FK missing')
                continue
            if sale['sale_id'] != sale_id(stid,sid,date.fromisoformat(day)):
                errors.append('invalid canonical sale ID')
            if inv['inventory_id'] != inventory_id(stid,sid,date.fromisoformat(day)):
                errors.append('invalid canonical inventory ID')
            if sale['sale_id'] != truth['sale_id']:
                errors.append('ground-truth record mismatch')
            if not products[sid]['launch_date'] <= day or (
                products[sid]['discontinue_date'] and day >= products[sid]['discontinue_date']):
                errors.append('sale outside product lifecycle')
            opening=int(inv['opening_stock']); receipt=int(inv['received_units']); sold=int(sale['units_sold']);
            closing=int(inv['closing_stock']); available=int(sale['observed_available_units'])
            latent=int(truth['latent_demand']); lost=int(truth['lost_demand']);
            if closing != opening+receipt-sold+int(inv['adjustment_units']): errors.append('inventory arithmetic mismatch')
            if available != opening+receipt or sold>available or sold!=int(inv['sold_units']): errors.append('sales availability mismatch')
            if latent != sold+lost or _boolean(sale['stockout_flag'])!=(lost>0): errors.append('latent demand reconciliation mismatch')
            if _boolean(truth['actual_zero_demand']) != (latent==0): errors.append('zero-demand label mismatch')
            key=(sid,stid)
            if manifest['config'].get('movement_journal',False):
                actual_events=events_by_inventory.pop(inv['inventory_id'],[])
                expected_movements=[]
                if key not in last_state: expected_movements.append(('OPENING',opening))
                if receipt>0: expected_movements.append(('RECEIPT',receipt))
                if sold>0: expected_movements.append(('SALE',-sold))
                observed_movements=sorted((e['movement_type'],int(e['quantity_delta'])) for e in actual_events)
                if observed_movements != sorted(expected_movements):
                    errors.append('append-only inventory event reconciliation mismatch')
                for e in actual_events:
                    if e['correlation_id']!=sale['sale_id']:
                        errors.append('movement correlation mismatch')
            if key in last_state:
                prev_date,prev_close=last_state[key]
                if (date.fromisoformat(day)-date.fromisoformat(prev_date)).days!=1 or prev_close!=opening:
                    errors.append('daily inventory continuity violation')
            last_state[key]=(day,closing)
            stats['eligible_sku_store_days']+=1
            stats['latent_demand_units']+=latent
            stats['observed_sales_units']+=sold
            stats['lost_demand_units']+=lost
            stats['received_units']+=receipt
            stats['stockout_censored_days']+=int(_boolean(sale['stockout_flag']))
            stats['actual_zero_demand_days']+=int(_boolean(truth['actual_zero_demand']))
            stats['promotion_days']+=int(_boolean(sale['promotion_flag']))
        if events_by_inventory:
            errors.append('orphan movement events')
    # Post-retirement snapshots are independent of sales/ground truth.
    # Validate all carry-forward values against the final sale-day stock and
    # require every post-discontinuation day (not just a signed subset).
    retired_entries=[x for x in manifest['files'] if x['path'].startswith('facts/inventory_snapshot_retired/')]
    retired_count=0
    for entry in sorted(retired_entries,key=lambda x:x['path']):
        for inv in iter_rows(root/entry['path']):
            sid=inv['sku_id']; stid=inv['store_id']; day=inv['snapshot_date']
            key=(sid,stid)
            retired_count+=1
            if sid not in products or stid not in stores:
                errors.append('retired inventory parent missing');continue
            discontinued=products[sid]['discontinue_date']
            if not discontinued or day < discontinued:
                errors.append('retired snapshot not after SKU discontinuation')
            if inv['inventory_id']!=inventory_id(stid,sid,date.fromisoformat(day)):
                errors.append('retired inventory ID mismatch')
            if key not in last_state or (date.fromisoformat(day)-date.fromisoformat(last_state[key][0])).days!=1:
                errors.append('retired inventory date continuity failure')
            if (int(inv['opening_stock'])!=int(last_state[key][1]) or
                int(inv['closing_stock'])!=int(inv['opening_stock']) or
                any(int(inv[k]) for k in ('received_units','sold_units','adjustment_units','reserved_units'))):
                errors.append('retired inventory movement/balance mismatch')
            last_state[key]=(day,int(inv['closing_stock']))
    if manifest.get('inventory_completeness_contract'):
        start=date.fromisoformat(manifest['config']['start_date'])
        end=start+timedelta(days=int(manifest['config']['days']))
        expected_retired_count=0
        for sku,product in products.items():
            if not product['discontinue_date']:continue
            retire_day=date.fromisoformat(product['discontinue_date'])
            expected_retired_count+=max(0,(end-retire_day).days)*len(stores)
            for store in stores:
                # Per-pair streaming continuity above detects missing or duplicate
                # interior dates. The end-state check detects missing tail dates.
                if last_state.get((sku,store),(None,))[0]!=(end-timedelta(days=1)).isoformat():
                    errors.append('retired inventory final-date coverage mismatch')
        if expected_retired_count!=retired_count:
            errors.append('retired inventory coverage mismatch')
    elif retired_count:
        errors.append('uncontracted retired inventory files')
    if retired_count!=int(manifest.get('summary',{}).get('retired_inventory_snapshot_days',0)):
        errors.append('retired inventory count mismatch')
    stats['retired_inventory_snapshot_days']=retired_count
    for k,v in manifest['summary'].items():
        if stats[k] != v: errors.append(f'manifest summary mismatch for {k}: {stats[k]} vs {v}')
    if expected['sales_history'] != stats['eligible_sku_store_days'] or expected['inventory_snapshot']!=stats['eligible_sku_store_days']+retired_count:
        errors.append('sales/inventory row coverage mismatch')
    scenarios=root/'negative_fixtures'/'mandatory_cases.jsonl'
    if scenarios.is_file():
        ids=[json.loads(line)['scenario_id'] for line in scenarios.read_text(encoding='utf-8').splitlines()]
        if ids!=['NEG-01','NEG-02','NEG-03','NEG-04','NEG-05']:
            errors.append('five prescribed scenarios missing or duplicated')
    else: errors.append('negative scenarios missing')
    # Enforce signed adversarial overlay integrity and independently replay
    # the record-level detectors on each overlay and its baseline control.
    overlay_report = None
    if 'negative_overlay_contract' in manifest:
        try:
            from app.generation.negative_overlays import verify_overlays
            overlay_report = verify_overlays(root,manifest)
            errors.extend(overlay_report['errors'])
        except (KeyError,ValueError,IOError) as exc:
            errors.append(f'negative overlay contract invalid: {exc}')
    report={'status':'PASS' if not errors else 'FAIL' ,'errors':errors[:25],
            'error_count':len(errors),'file_count':len(manifest['files']),
            'entities':expected,'reconciled':dict(stats),
            'negative_scenarios':manifest.get('negative_scenarios',[]),
            'negative_overlay_validation':overlay_report,
            'note':'validated independent full-stream scan, not PostgreSQL import or forecast/replenishment behavior'}
    if write_report:
        (root/'validation_summary.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    if errors: raise AssertionError(f'Validation failed ({len(errors)}): {errors[:5]}')
    return report


def compare_runs(a: Path,b: Path) -> dict:
    ma=json.loads((a/'manifest.json').read_text())
    mb=json.loads((b/'manifest.json').read_text())
    keys=['config','entity_counts','potential_sku_store_days','summary','negative_scenarios','source_manifest','technical_event_counts','negative_overlay_contract']
    issues=[k for k in keys if ma[k]!=mb[k]]
    f1={f['path']:(f['rows'],f.get('logical_sha256'),f['file_sha256']) for f in ma['files']}
    f2={f['path']:(f['rows'],f.get('logical_sha256'),f['file_sha256']) for f in mb['files']}
    if f1 != f2: issues.append('file names, row counts, logical or physical checksums differ')
    return {'status':'PASS' if not issues else 'FAIL','issues':issues,'file_count':len(f1),
            'counts':ma['entity_counts'],'same_config':ma['config']==mb['config']}

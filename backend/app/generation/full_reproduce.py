"""Full-scale deterministic regeneration without a second full-data directory.

An independent pass re-derives records from the configuration and verifies
per-partition logical SHA-256 and row counts against the validated baseline.
Original datasets are read-only. Does not connect to PostgreSQL.
"""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from app.generation.config import GeneratorConfig
from app.ids import sku_id, store_id, inventory_id
from app.generation.generator import (
    master_generation, read_master, synthetic_rows, write_negative_fixtures,
)
from app.generation.sql_seed import export_sql
from app.generation.writers import canonical
from app.generation.negative_overlays import generate_overlays


def _first_stockout_from_seed(cfg, start, products, contracts):
    """Rebuild the first public stockout in partition filename ordering."""
    fallback = None
    months = sorted({(start+timedelta(days=i)).strftime('%Y-%m') for i in range(cfg.days)})
    for month in months:
        for first in range(1,cfg.store_count+1,cfg.store_batch):
            last=min(cfg.store_count,first+cfg.store_batch-1)
            for store in range(first,last+1):
                for sku in range(1,cfg.sku_count+1):
                    for _, sale, _, _ in synthetic_rows(cfg,sku,store,products[sku],contracts[sku],start):
                        if sale['sales_date'][:7]!=month:
                            continue
                        if fallback is None:fallback=sale
                        if sale['stockout_flag']:
                            return sale
    if fallback is None:raise ValueError('No eligible sales records')
    return fallback

FACT_ENTITIES = ('sales_history', 'inventory_snapshot', 'inventory_snapshot_retired', 'ground_truth',
                 'tech_inventory_movement_event')


def _key(entity: str, month: str, first: int, last: int, fmt: str) -> str:
    base = ('restricted_ground_truth' if entity == 'ground_truth' else
            'technical_events' if entity == 'tech_inventory_movement_event' else 'facts')
    return (f'{base}/{entity}/{month}/'
            f'{entity}_stores_{first:03d}-{last:03d}.{fmt}')


def _sha(path: Path) -> str:
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def compare_streaming(root: Path, *, progress=None) -> dict:
    """Return PASS only if *every* original output matches a separate regeneration.

    No original data modified; second pass retains only hash objects in RAM.
    """
    root = root.resolve()
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    cfg = GeneratorConfig.from_json(root/'generator_config.json')
    if cfg.as_dict() != manifest['config']:
        raise ValueError('Configuration differs from generation manifest')
    original = {entry['path']: entry for entry in manifest['files']}
    regenerated_retired = bool(manifest.get('inventory_completeness_contract'))
    issues = []
    rows_compared = 0
    partitions_compared = 0
    seen = set()

    with tempfile.TemporaryDirectory(prefix='yottaflex-full-repro-') as scratch:
        scratch = Path(scratch)
        regen = {'files': [], 'entity_counts': {}}
        start = date.fromisoformat(cfg.start_date)
        master_generation(cfg, scratch, regen, start)
        write_negative_fixtures(cfg, scratch, regen, start)
        seed_products=[None]+list(read_master(scratch,'sku'))
        seed_contracts=[None]+list(read_master(scratch,'supplier_sku'))
        if 'negative_overlay_contract' in manifest:
            overlay_sample=_first_stockout_from_seed(cfg,start,seed_products,seed_contracts)
            generate_overlays(scratch,regen,cfg,start,sale_override={k:str(v) for k,v in overlay_sample.items()})
        for entry in regen['files']:
            rel = entry['path']
            seen.add(rel)
            expected = original.get(rel)
            if not expected or expected['rows'] != entry['rows'] or expected['file_sha256'] != entry['file_sha256']:
                issues.append(f'master/negative mismatch: {rel}')
        export_sql(scratch)
        if _sha(scratch/'sql'/'master_seed.sql') != _sha(root/'sql'/'master_seed.sql'):
            issues.append('master SQL seed checksum mismatch')
        # Reference inputs must be regenerated from seed, never loaded from
        # potentially altered original master data.
        products = [None] + list(read_master(scratch,'sku'))
        contracts = [None] + list(read_master(scratch,'supplier_sku'))
        months = sorted({(start + timedelta(days=i)).strftime('%Y-%m') for i in range(cfg.days)})
        for first in range(1, cfg.store_count+1, cfg.store_batch):
            last = min(cfg.store_count, first+cfg.store_batch-1)
            entities = FACT_ENTITIES if cfg.movement_journal else FACT_ENTITIES[:-1]
            if not regenerated_retired:
                entities=tuple(e for e in entities if e!='inventory_snapshot_retired')
            hashes = {_key(e,m,first,last,cfg.format): hashlib.sha256()
                      for e in entities for m in months}
            counts = defaultdict(int)
            for store_n in range(first,last+1):
                for sku_n in range(1,cfg.sku_count+1):
                    sku = products[sku_n]
                    contract = contracts[sku_n]
                    last_inv=None
                    for _, sale, inv, truth in synthetic_rows(cfg, sku_n, store_n, sku, contract, start):
                        last_inv=inv
                        month = sale['sales_date'][:7]
                        for entity, row in (('sales_history',sale),
                                            ('inventory_snapshot',inv),('ground_truth',truth)):
                            key = _key(entity,month,first,last,cfg.format)
                            hashes[key].update(canonical(row))
                            counts[key] += 1
                        if cfg.movement_journal:
                            moves=[]
                            if inv['snapshot_date'] == sku['launch_date'] or (sale['sales_date'] == cfg.start_date and sku['launch_date'] < cfg.start_date):
                                moves.append(('OPENING',inv['opening_stock']))
                            if inv['received_units'] > 0: moves.append(('RECEIPT',inv['received_units']))
                            if sale['units_sold'] > 0: moves.append(('SALE',-sale['units_sold']))
                            key=_key('tech_inventory_movement_event',month,first,last,cfg.format)
                            for kind,delta in moves:
                                event={'event_id': 'EV-INV-'+inv['inventory_id']+'-'+kind,
                                       'inventory_id':inv['inventory_id'],
                                       'occurred_at': sale['sales_date']+'T23:59:00+00:00',
                                       'movement_type':kind, 'quantity_delta':delta,
                                       'correlation_id':sale['sale_id'],
                                       'payload':json.dumps({'generator':'P1-T02-v1','source':'synthetic_history'},sort_keys=True,separators=(',',':'))}
                                hashes[key].update(canonical(event))
                                counts[key]+=1
                    if regenerated_retired and sku['discontinue_date'] and last_inv is not None:
                        d=date.fromisoformat(sku['discontinue_date'])
                        closing=int(last_inv['closing_stock'])
                        while d < start+timedelta(days=cfg.days):
                            row={'inventory_id':inventory_id(store_id(store_n),sku_id(sku_n),d),
                                 'sku_id':sku_id(sku_n),'store_id':store_id(store_n),
                                 'snapshot_date':d.isoformat(),'opening_stock':closing,
                                 'received_units':0,'sold_units':0,'adjustment_units':0,
                                 'closing_stock':closing,'reserved_units':0}
                            key=_key('inventory_snapshot_retired',d.strftime('%Y-%m'),first,last,cfg.format)
                            hashes[key].update(canonical(row))
                            counts[key]+=1
                            d+=timedelta(days=1)
            for key, digest in hashes.items():
                count = counts[key]
                if count == 0:
                    if key in original:
                        issues.append(f'unexpected empty partition in second run: {key}')
                    continue
                seen.add(key)
                expected=original.get(key)
                if expected is None or expected['rows'] != count or expected.get('logical_sha256') != digest.hexdigest():
                    issues.append(f'partition count/hash mismatch: {key}')
                partitions_compared+=1
                rows_compared+=count
            if progress:
                progress({'stores_completed':last,'store_count':cfg.store_count,
                          'partitions_compared':partitions_compared,
                          'logical_rows_compared':rows_compared})
    missing=set(original)-seen
    if missing: issues.append(f'baseline contains unregenerated files ({len(missing)}), example: {sorted(missing)[0]}')
    result={'status':'PASS' if not issues else 'FAIL', 'method':'independent streaming regeneration',
            'seed':cfg.seed, 'config':cfg.as_dict(), 'issues':issues[:30],
            'issue_count':len(issues), 'files_compared':len(seen),
            'fact_partitions_compared':partitions_compared,
            'logical_rows_compared':rows_compared,
            'no_second_full_dataset_written':True,
            'checksums':'SHA-256 logical per partition; physical output validated separately'}
    return result

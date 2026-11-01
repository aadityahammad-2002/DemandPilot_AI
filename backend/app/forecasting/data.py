"""Read allowlisted Phase 2 facts without touching oracle/negative fixtures."""
from __future__ import annotations
from collections import defaultdict
from datetime import date
import json
import math
import hashlib
from pathlib import Path
import re
from app.generation.model_inputs import assert_model_input_allowed
from app.generation.verify import iter_rows, _boolean

ALLOWED = frozenset(('sku_id', 'store_id', 'sales_date', 'units_sold',
                     'promotion_flag', 'promotion_multiplier', 'stockout_flag',
                     'observed_available_units', 'selling_price', 'sale_id'))


def _date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def select_pairs(manifest: dict, max_pairs: int) -> list[tuple[str, str]]:
    """Deterministic bounded selection, includes intermittent and launch SKU archetypes."""
    n_sku = int(manifest['config']['sku_count'])
    n_store = int(manifest['config']['store_count'])
    preferred = [1, 2, 3, 8, 11, 16, 22, 24, 29, 33, 44, 56]
    preferred += list(range(1, n_sku + 1))
    skus = list(dict.fromkeys(x for x in preferred if x <= n_sku))
    # Cap store regions scanned during developer runs (can be expanded in config).
    stores = range(1, n_store + 1)
    pairs = []
    for sn in stores:
        for kn in skus:
            pairs.append((f'STORE-{sn:06d}', f'SKU-{kn:06d}'))
            if len(pairs) >= max_pairs:
                return pairs
    return pairs


def load_history(root: Path, pairs: list[tuple[str, str]]) -> tuple[dict, dict]:
    """Keep only explicitly selected store/SKU histories, never hidden truth.

    Scans only public sales partitions with selected store batches. Memory is bounded
    by `len(pairs) * days`, not by the full SKU-store-day fact panel.
    """
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    selected = set(pairs)
    stores = {int(store.split('-')[-1]) for store, _ in selected}
    history = defaultdict(list)
    entries = [e for e in manifest['files'] if e['path'].startswith('facts/sales_history/')]
    if not entries:
        raise ValueError('manifest has no public sales_history files')
    for entry in sorted(entries, key=lambda e: e['path']):
        rel = entry['path']
        m = re.search(r'sales_history_stores_(\d+)-(\d+)\.(csv|parquet)$', rel)
        if m and not any(int(m.group(1)) <= x <= int(m.group(2)) for x in stores):
            continue
        path = (root / rel).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f'invalid or missing public fact path: {rel}')
        assert_model_input_allowed(path)
        if path.stat().st_size != int(entry['bytes']):
            raise ValueError(f'Phase 2 input size mismatch: {rel}')
        with path.open('rb') as fh:
            if hashlib.file_digest(fh,'sha256').hexdigest() != entry['file_sha256']:
                raise ValueError(f'Phase 2 input checksum mismatch: {rel}')
        for raw in iter_rows(path):
            if set(raw) - ALLOWED:
                raise ValueError(f'unknown public sales columns: {set(raw)-ALLOWED}')
            key = (str(raw['store_id']), str(raw['sku_id']))
            if key not in selected:
                continue
            sold = int(raw['units_sold'])
            available = int(raw['observed_available_units'])
            if min(sold, available) < 0 or sold > available:
                raise ValueError('invalid observed sales/availability')
            multiplier=float(raw['promotion_multiplier'])
            if not math.isfinite(multiplier) or multiplier<=0:
                raise ValueError('invalid public promotion multiplier')
            history[key].append({
                'date': _date(raw['sales_date']), 'sales': sold,
                'stockout': _boolean(raw['stockout_flag']),
                'promo': _boolean(raw['promotion_flag']),
                'multiplier': multiplier,
                'available': available,
            })
    for key, rows in history.items():
        rows.sort(key=lambda r:r['date'])
        days = [r['date'] for r in rows]
        if len(days) != len(set(days)):
            raise ValueError(f'duplicate daily observations: {key}')
    missing = selected.difference(history)
    if missing:
        raise ValueError(f'selected pairs absent from dataset: {len(missing)}')
    return dict(history), manifest


def master_info(root: Path) -> tuple[dict, dict]:
    """Read *manifest-verified* SKU/store features, not mutable master CSVs.

    Forecast features include category, region and launch date. Validating only
    sales facts allowed a modified or duplicated master to change predictions
    while every sales partition still passed its digest check.
    """
    root=root.resolve()
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    entries=manifest.get('files',[])
    if not isinstance(entries,list):
        raise ValueError('invalid master manifest')
    result=[]
    for name,identity in (('sku','sku_id'),('store','store_id')):
        rel=f'master/{name}.csv'
        matches=[entry for entry in entries if isinstance(entry,dict) and entry.get('path')==rel]
        if len(matches)!=1:
            raise ValueError('missing or duplicate signed master manifest entry: '+rel)
        path=(root/rel).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('unsafe or missing signed master: '+rel)
        assert_model_input_allowed(path)
        entry=matches[0]
        if path.stat().st_size!=int(entry['bytes']):
            raise ValueError('signed master manifest size mismatch: '+rel)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=entry['file_sha256']:
                raise ValueError('signed master manifest digest mismatch: '+rel)
        rows=list(iter_rows(path))
        if any(identity not in row for row in rows):
            raise ValueError('missing signed master identifier: '+rel)
        if len({row[identity] for row in rows})!=len(rows):
            raise ValueError('duplicate signed master identifier: '+rel)
        result.append({row[identity]:row for row in rows})
    return result[0],result[1]

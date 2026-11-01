"""Stable identifiers. Public compatibility prefixes preserve prescribed examples."""
from datetime import date
import re


def numbered(prefix: str, number: int, width: int = 6) -> str:
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0 or number >= 10 ** width:
        raise ValueError('number out of supported range')
    if not re.fullmatch(r'[A-Z][A-Z0-9]*', prefix):
        raise ValueError('invalid prefix')
    return f'{prefix}-{number:0{width}d}'

def sku_id(n: int) -> str: return numbered('SKU', n)
def store_id(n: int) -> str: return numbered('STORE', n)
def supplier_id(n: int) -> str: return numbered('SUP', n)
def supplier_sku_id(n: int) -> str: return numbered('SSKU', n)
def policy_id(n: int) -> str: return numbered('POL', n)
def observation_id(n: int) -> str: return numbered('LTO', n)

def sale_id(store: str, sku: str, day: date) -> str:
    _validate_base(store, sku)
    return f'SALE-{store}-{sku}-{day.isoformat()}'

def inventory_id(store: str, sku: str, day: date) -> str:
    _validate_base(store, sku)
    return f'INV-{store}-{sku}-{day.isoformat()}'

def _validate_base(store: str, sku: str) -> None:
    if (not re.fullmatch(r'STORE-\d{6}', store) or not re.fullmatch(r'SKU-\d{6}', sku)
            or store.endswith('-000000') or sku.endswith('-000000')):
        raise ValueError('invalid store or sku format')

def forecast_id(store: str, sku: str, month: str, *, version: int = 1, sequence: int = 1) -> str:
    """First forecast exactly FC-STORE-000001-SKU-000001-2026-01;
    further revisions get -V0002, and multiple rows in a version get -N0002.
    Model key additionally includes origin and target date; these IDs are stable only
    if version+sequence are assigned deterministically per store/sku/month.
    """
    _validate_base(store, sku)
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month): raise ValueError('invalid month')
    if version < 1 or sequence < 1 or version > 9999 or sequence > 999999: raise ValueError('invalid version/sequence')
    stem = f'FC-{store}-{sku}-{month}'
    return stem + (f'-V{version:04d}' if version != 1 else '') + (f'-N{sequence:06d}' if sequence != 1 else '')

def recommendation_id(year: int, ordinal: int, *, revision: int = 1) -> str:
    """The base ID matches PO-2026-000001; only lifecycle event IDs carry revisions.
    Distinct recommendations must have distinct monotonic ordinals per year.
    """
    if not 2000 <= year <= 9999 or not 1 <= revision <= 9999: raise ValueError('invalid year/revision')
    stem = f'PO-{year}-{ordinal:06d}' if 1 <= ordinal <= 999999 else None
    if stem is None: raise ValueError('invalid ordinal')
    return stem if revision == 1 else f'{stem}-R{revision:04d}'

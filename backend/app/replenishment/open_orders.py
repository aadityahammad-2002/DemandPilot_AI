"""Verified external open-order snapshot (not inferred from historical receipts).

Explicit opt-in CSV. No source -> zero pipeline with an uncertainty disclosure.
Rows represent *remaining* outstanding units; closed/received orders never count.
All active rows must have a future due date at the decision time, preventing
an overdue shipment being treated as guaranteed future supply.
"""
from __future__ import annotations
import csv
from datetime import date
from pathlib import Path
import re

FIELDS = ('external_order_id','store_id','sku_id','expected_receipt_date','outstanding_units','status')
ACTIVE = frozenset(('OPEN','IN_TRANSIT'))
CLOSED = frozenset(('RECEIVED','CANCELLED'))


def load_open_orders(path: Path) -> dict[tuple[str,str],list[dict]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    records = {}
    by_pair = {}
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or set(reader.fieldnames) != set(FIELDS):
            raise ValueError('open-order CSV field contract mismatch')
        for row in reader:
            identifier = row['external_order_id']
            if not re.fullmatch(r'EXTPO-[A-Za-z0-9][A-Za-z0-9-]{0,70}',identifier):
                raise ValueError('external order reference must be in EXTPO namespace')
            if identifier in records:
                raise ValueError('duplicate external open-order reference')
            if not re.fullmatch(r'STORE-[0-9]{6}',row['store_id']) or not re.fullmatch(r'SKU-[0-9]{6}',row['sku_id']):
                raise ValueError('invalid open-order SKU/store identifier')
            due = date.fromisoformat(row['expected_receipt_date'])
            amount = int(row['outstanding_units'])
            if amount < 0:
                raise ValueError('negative outstanding order units')
            if row['status'] not in ACTIVE | CLOSED:
                raise ValueError('invalid outstanding order status')
            if row['status'] in CLOSED and amount != 0:
                raise ValueError('received/cancelled order cannot remain outstanding')
            record={**row,'expected_receipt_date':due,'outstanding_units':amount}
            records[identifier]=record
            by_pair.setdefault((row['store_id'],row['sku_id']),[]).append(record)
    for entries in by_pair.values():
        entries.sort(key=lambda r:(r['expected_receipt_date'],r['external_order_id']))
    return by_pair


def pipeline_at(rows: list[dict], asof: date, horizon_days: int) -> tuple[int,list[str]]:
    from datetime import timedelta
    if horizon_days < 0:
        raise ValueError('invalid forecast horizon')
    total=0;references=[]
    for row in rows:
        if row['status'] not in ACTIVE:
            continue
        due=row['expected_receipt_date']
        if isinstance(due,str):due=date.fromisoformat(due)
        if due<=asof:
            raise ValueError('outstanding open-order snapshot contains overdue/received-unreconciled order')
        if due>asof+timedelta(days=horizon_days):
            continue
        units=int(row['outstanding_units'])
        if units<0:raise ValueError('negative outstanding order units')
        total+=units
        references.append(row['external_order_id'])
    return total,references

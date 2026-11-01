"""Optional forward-known promotion calendar, with strict point-in-time availability.

CSV columns: store_id,sku_id,target_date,known_at,promotion_multiplier.
This is NOT future realized sales or a generated hidden-truth artifact.
"""
from __future__ import annotations
import csv
from datetime import date
import math
from pathlib import Path
from app.generation.model_inputs import assert_model_input_allowed


def read_calendar(path: Path | None) -> dict:
    if path is None:
        return {}
    path=path.resolve()
    assert_model_input_allowed(path)
    if not path.is_file() or path.suffix.lower() != '.csv':
        raise ValueError('promotion calendar must be a public CSV file')
    schedule={}
    with path.open(newline='',encoding='utf-8') as fp:
        reader=csv.DictReader(fp)
        expected={'store_id','sku_id','target_date','known_at','promotion_multiplier'}
        if set(reader.fieldnames or []) != expected:
            raise ValueError('invalid promotion calendar columns')
        for row in reader:
            target=date.fromisoformat(row['target_date'])
            known_at=date.fromisoformat(row['known_at'])
            mult=float(row['promotion_multiplier'])
            if not math.isfinite(mult) or not (0.1<=mult<=10.0):
                raise ValueError('invalid promotion multiplier')
            if known_at>target:
                raise ValueError('promotion cannot become known after target date')
            key=(row['store_id'],row['sku_id'],target)
            if key in schedule:
                raise ValueError('duplicate scheduled promotion record')
            schedule[key]=(known_at,mult)
    return schedule


def known_multiplier(calendar: dict, store_id: str, sku_id: str,
                     target: date, origin: date) -> float:
    info=calendar.get((store_id,sku_id,target))
    return info[1] if info and info[0]<=origin else 1.0

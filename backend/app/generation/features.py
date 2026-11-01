"""Minimal safe public historical feature reader, not a forecasting implementation."""
from __future__ import annotations
from datetime import date
from pathlib import Path
from app.generation.verify import iter_rows
from app.generation.model_inputs import assert_model_input_allowed

PUBLIC_FEATURES=('sku_id','store_id','sales_date','units_sold','promotion_flag',
                 'promotion_multiplier','stockout_flag','observed_available_units')


def historical_features(root: Path, *, origin: date):
    """Yield only sales observations available before/at origin. Hidden truth excluded.

    NOTE: The later feature pipeline must use training-origin-specific lagging and
    exclude contemporaneous target values. This reader only provides chronological
    raw history with a strict timestamp guard.
    """
    root=root.resolve()
    for p in sorted((root/'facts'/'sales_history').glob('*/*')):
        assert_model_input_allowed(p)
        for r in iter_rows(p):
            day=r['sales_date']
            if isinstance(day,date): day=day.isoformat()
            if day>origin.isoformat():
                continue
            yield {key:r[key] for key in PUBLIC_FEATURES}


def validate_feature_batch(features, *, origin: date):
    """Reject (rather than silently mask) future-loaded features and truth columns."""
    for r in features:
        if set(r)-set(PUBLIC_FEATURES):
            raise ValueError(f'forbidden feature columns: {set(r)-set(PUBLIC_FEATURES)}')
        d=r['sales_date']
        if (d.isoformat() if isinstance(d,date) else d) > origin.isoformat():
            raise ValueError('forecast would use future sales')

"""Date-effective SKU lifecycle contract for historical decisions and imports.

Master.status is the latest/end-state label in synthetic data. Dates are the
source of truth for historical eligibility; [launch, discontinue) is active.
"""
from __future__ import annotations
from datetime import date


def sku_eligible_asof(sku, asof: date) -> bool:
    launch = sku['launch_date'] if isinstance(sku, dict) else sku.launch_date
    end = sku['discontinue_date'] if isinstance(sku, dict) else sku.discontinue_date
    state = sku['status'] if isinstance(sku, dict) else sku.status
    launch = date.fromisoformat(launch) if isinstance(launch, str) else launch
    end = date.fromisoformat(end) if isinstance(end, str) and end else end or None
    if state not in ('PLANNED', 'ACTIVE', 'DISCONTINUED') or not isinstance(launch, date):
        raise ValueError('invalid SKU lifecycle state')
    if end is not None and (not isinstance(end, date) or end < launch):
        raise ValueError('inconsistent SKU lifecycle dates')
    if state == 'DISCONTINUED' and end is None:
        raise ValueError('discontinued SKU missing effective end date')
    if state == 'PLANNED' and launch <= asof:
        # This is an inconsistent source state, not evidence of availability.
        raise ValueError('planned SKU already reached launch date')
    return state != 'PLANNED' and launch <= asof and (end is None or asof < end)

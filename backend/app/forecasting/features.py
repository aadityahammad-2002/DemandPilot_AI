"""Point-in-time features and censored-demand treatment.

No restricted labels, final SKU demand parameters, future sales, or future
promotion indicators are admissible here. A zero with no availability is
censored, NOT a known zero-demand example.
"""
from __future__ import annotations
from datetime import date
import math
import statistics

FEATURE_NAMES = (
    'lag1', 'lag7', 'mean7', 'mean28', 'std28', 'positive_rate28',
    'true_zero_rate28', 'censored_rate28', 'promo_rate28',
    'weekday_sin', 'weekday_cos', 'year_sin', 'year_cos',
    'category_code', 'region_code', 'age_days',
    'scheduled_promo_flag', 'scheduled_promo_multiplier',
)
CATEGORIES = ('GROCERY','FRESH','ELECTRONICS','PERSONAL_CARE','HOME','SEASONAL')
REGIONS = ('North','South','East','West','Central')


def censored(row: dict) -> bool:
    return bool(row['stockout']) or row['available'] <= 0


def enforce_origin(history: list[dict], origin: date) -> None:
    """Reject unsafe supplied batches; cannot silently omit future sales."""
    if any(r['date'] > origin for r in history):
        raise ValueError('NEG-05: forecast uses future sales')
    dates=[r['date'] for r in history]
    if dates!=sorted(dates):
        raise ValueError('out-of-order history would leak into lag features')
    if len(set(dates)) != len(history):
        raise ValueError('duplicate dates in feature history')
    if any(not math.isfinite(float(r['sales'])) or float(r['sales'])<0 or
           not math.isfinite(float(r['available'])) or float(r['available'])<0
           for r in history):
        raise ValueError('invalid historical sales/availability')


def corrected_history(history: list[dict], *, correction_enabled: bool = True) -> list[float]:
    """Past-only imputation for censored points; never use hidden latent truth."""
    values=[]
    valid=[]
    for r in history:
        if censored(r):
            if not correction_enabled:
                values.append(float(r['sales']))
                continue
            same_weekday = [v for d,v in valid[-56:] if d.weekday() == r['date'].weekday()]
            recent = [v for _, v in valid[-28:]]
            replacement = statistics.mean(same_weekday or recent) if (same_weekday or recent) else 0.
            values.append(max(float(r['sales']), float(replacement)))
        else:
            number = float(r['sales'])
            values.append(number)
            valid.append((r['date'], number))
    return values


def feature_vector(history: list[dict], target: date, product: dict, store: dict,
                   planned_multiplier: float = 1.0, *, correction_enabled: bool = True) -> list[float]:
    # Caller must check origin explicitly; for training, all provided rows are <target.
    enforce_origin(history,target)
    if not math.isfinite(planned_multiplier) or planned_multiplier<=0:
        raise ValueError('invalid planned promotion multiplier')
    if not history or history[-1]['date'] >= target:
        raise ValueError('features require history strictly earlier than target')
    if any(r['date'] >= target for r in history):
        raise ValueError('NEG-05: future or contemporaneous sales feature')
    values = corrected_history(history, correction_enabled=correction_enabled)
    recent7 = values[-7:]
    recent28 = values[-28:]
    tail = history[-28:]
    mean28 = statistics.mean(recent28)
    age = max(0, (target - date.fromisoformat(product['launch_date'])).days)
    doy = target.timetuple().tm_yday
    wd = target.weekday()
    return [
        values[-1], values[-7] if len(values) >= 7 else mean28,
        statistics.mean(recent7), mean28,
        statistics.pstdev(recent28) if len(recent28)>1 else 0.,
        sum(v>0 for v in recent28)/len(recent28),
        sum((not censored(r)) and r['sales']==0 for r in tail)/len(tail),
        sum(censored(r) for r in tail)/len(tail),
        sum(r['promo'] for r in tail)/len(tail),
        math.sin(2*math.pi*wd/7), math.cos(2*math.pi*wd/7),
        math.sin(2*math.pi*doy/365.25), math.cos(2*math.pi*doy/365.25),
        float(CATEGORIES.index(product['category'])),
        float(REGIONS.index(store['region'])), float(age),
        float(planned_multiplier != 1.0), float(planned_multiplier),
    ]


def baseline_predict(history: list[dict], target: date, kind: str, *, correction_enabled: bool = True) -> float:
    vals = corrected_history(history, correction_enabled=correction_enabled)
    if not vals: return 0.
    if kind == 'moving_average':
        return max(0., statistics.mean(vals[-28:]))
    if kind == 'seasonal_naive':
        # Same-weekday observations avoid blindly reusing an unrelated day.
        matches = [v for r,v in zip(history, vals) if r['date'].weekday()==target.weekday()]
        return max(0., statistics.mean(matches[-4:]) if matches else statistics.mean(vals[-7:]))
    if kind == 'croston_sba':
        # Bias-corrected Croston intermittent-demand baseline, chronological.
        positives = [(i, v) for i,v in enumerate(vals) if v > 0]
        if not positives: return 0.
        z = positives[0][1]
        p = float(positives[0][0]+1)
        last = positives[0][0]
        alpha = .2
        for idx, amount in positives[1:]:
            interval = idx-last
            z = alpha*amount+(1-alpha)*z
            p = alpha*interval+(1-alpha)*p
            last = idx
        return max(0., (.9*z)/max(p,1.))
    raise ValueError(f'unknown baseline: {kind}')


def infer_path(history: list[dict], origin: date, horizon: int, kind: str,
               model, product: dict, store: dict,
               promo_schedule: dict | None = None, *, correction_enabled: bool = True) -> list[float]:
    """Recursive multi-step prediction; never uses actual post-origin sales."""
    enforce_origin(history, origin)
    from app.forecasting.promotions import known_multiplier
    work = list(history)
    predicted=[]
    for delta in range(1,horizon+1):
        target = origin.fromordinal(origin.toordinal()+delta)
        promo_mult=known_multiplier(promo_schedule or {}, store['store_id'],
                                    product['sku_id'], target, origin)
        if kind == 'ml_hgb':
            import numpy as np
            features = feature_vector(work, target, product, store, promo_mult, correction_enabled=correction_enabled)
            y = float(model.predict(np.asarray([features], dtype='float64'))[0])
        else:
            y = baseline_predict(work, target, kind, correction_enabled=correction_enabled)*promo_mult
        # Validate BEFORE clipping: max(0, NaN) silently yields zero in Python.
        # Invalid model output must never masquerade as genuine zero demand.
        if not math.isfinite(y): raise ValueError('nonfinite forecast')
        y = max(0., round(y,6))
        predicted.append(y)
        # Recursive synthetic predictions must be uncensored, but availability
        # remains finite and never pretends to be observed physical inventory.
        work.append({'date':target,'sales':y,'stockout':False,
                     'promo':promo_mult != 1.,
                     'multiplier':promo_mult,'available':max(1.0,y)})
    return predicted

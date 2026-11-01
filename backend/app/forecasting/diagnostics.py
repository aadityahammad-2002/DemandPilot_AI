"""Independent, validation-only forecast comparability and uncertainty diagnostics.

The diagnostic interval is a symmetric empirical absolute-error band. It is NOT a
formal coverage guarantee and is never fed back into forecasts or decisions.
"""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
import math


def validation_sample_keys(rows: list[dict]) -> dict:
    groups=defaultdict(set)
    counts=defaultdict(int)
    for r in rows:
        if r['split']!='validation' or r['censored_target']:
            continue
        key=(r['store_id'],r['sku_id'],r['forecast_origin'],r['target_date'],int(r['horizon']))
        method=r['method']
        counts[method]+=1
        if key in groups[method]:
            raise ValueError('duplicate validation target identity')
        groups[method].add(key)
    if not groups or any(not keys for keys in groups.values()):
        raise ValueError('no eligible validation target identities')
    reference=next(iter(groups.values()))
    if any(keys!=reference for keys in groups.values()):
        raise ValueError('validation target identities differ between methods')
    signature=hashlib.sha256(json.dumps(sorted(reference),separators=(',',':')).encode()).hexdigest()
    return {'target_count':len(reference),'method_count':len(groups),'target_keys_sha256':signature}


def empirical_interval_diagnostics(rows: list[dict], winner: str, *,
                                   nominal_coverage: float = 0.8,
                                   min_horizon_samples: int = 20,
                                   min_global_samples: int = 10) -> dict:
    """Fit absolute-error radii ONLY on validation; evaluate on untouched test.

    Optional per-horizon bands fall back to a pooled validation band, or become
    unavailable when calibration is too sparse. No test label sets a radius.
    """
    if not 0 < nominal_coverage < 1 or min(min_horizon_samples,min_global_samples)<2:
        raise ValueError('invalid calibration settings')
    selected=[r for r in rows if r['method']==winner and not r['censored_target']]
    validation=[r for r in selected if r['split']=='validation']
    test=[r for r in selected if r['split']=='test']
    def residual(r):
        a=float(r['actual_units']);p=float(r['predicted_units'])
        if not math.isfinite(a) or not math.isfinite(p) or a<0 or p<0:
            raise ValueError('invalid calibration target')
        return abs(a-p)
    # Test is accessed only below after all calibration widths are frozen.
    global_err=sorted(residual(r) for r in validation)
    by_horizon=defaultdict(list)
    for r in validation:by_horizon[int(r['horizon'])].append(residual(r))
    def quantile(data):
        # Finite-sample conservative order statistic; insufficient n => unavailable.
        rank=math.ceil((len(data)+1)*nominal_coverage)
        return data[rank-1] if 1<=rank<=len(data) else None
    global_radius=quantile(global_err) if len(global_err)>=min_global_samples else None
    radii={}
    for horizon in sorted({int(r['horizon']) for r in selected}):
        local=sorted(by_horizon.get(horizon,[]))
        if len(local)>=min_horizon_samples and quantile(local) is not None:
            radii[horizon]=(quantile(local),'horizon',len(local))
        elif global_radius is not None:
            radii[horizon]=(global_radius,'pooled',len(global_err))
        else:
            radii[horizon]=(None,'unavailable',len(global_err))
    coverage={}
    for horizon,(radius,source,n) in sorted(radii.items()):
        holdout=[r for r in test if int(r['horizon'])==horizon]
        covered=(sum(residual(r)<=radius for r in holdout) if radius is not None else None)
        coverage[str(horizon)]={'radius_units':round(radius,6) if radius is not None else None,
            'calibration_source':source,'calibration_count':n,'test_count':len(holdout),
            'test_covered':covered,'test_coverage':round(covered/len(holdout),6)
                if covered is not None and holdout else None}
    return {'type':'symmetric_absolute_error_empirical_diagnostic',
            'statistical_guarantee':False,'nominal_coverage':nominal_coverage,
            'calibration_split':'validation','evaluation_split':'test',
            'selected_model':winner,'per_horizon':coverage}


def validation_selection_stability(rows: list[dict], winner: str) -> dict:
    """Diagnostic win frequency across validation origins; never consults test."""
    origins=defaultdict(lambda:defaultdict(list))
    for row in rows:
        if row['split']=='validation' and not row['censored_target']:
            origins[row['forecast_origin']][row['method']].append(row)
    decisions=[]
    for origin,candidates in sorted(origins.items()):
        # Check identity equality, not just sample count.
        sample_sets=[]
        for records in candidates.values():
            sample_sets.append({(r['store_id'],r['sku_id'],r['target_date'],int(r['horizon']))
                                for r in records})
        if any(x!=sample_sets[0] for x in sample_sets[1:]):
            raise ValueError('validation origin methods have mismatched target identities')
        ranked=[]
        for method,records in candidates.items():
            total_actual=sum(float(r['actual_units']) for r in records)
            abs_error=sum(abs(float(r['predicted_units'])-float(r['actual_units'])) for r in records)
            # WAPE ranking when defined, otherwise MAE surrogate.
            ranked.append((abs_error/total_actual if total_actual else
                           abs_error/len(records),method))
        decisions.append({'origin':origin,'winner':min(ranked)[1],
                          'eligible_target_count':len(sample_sets[0])})
    return {'validation_origins':len(decisions),
            'selected_model':winner,
            'selected_model_origin_wins':sum(x['winner']==winner for x in decisions),
            'fraction_of_origins_won':round(sum(x['winner']==winner for x in decisions)/len(decisions),6)
                if decisions else None,
            'origin_winners':decisions,
            'uses_test_labels':False,'used_to_change_selection':False}

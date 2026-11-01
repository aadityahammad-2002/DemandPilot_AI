"""Leakage-free evaluation scoring and explanatory summaries."""
from collections import defaultdict
import math


def metrics(rows: list[dict]) -> dict:
    """Only observations known uncensored are scored against observed sales."""
    eligible = [r for r in rows if not r['censored_target']]
    for r in eligible:
        predicted=float(r['predicted_units'])
        actual=float(r['actual_units'])
        if not math.isfinite(predicted) or not math.isfinite(actual) or min(predicted,actual)<0:
            raise ValueError('evaluation contains invalid observed or predicted demand')
    n = len(eligible)
    if not n:
        return {'n':0,'mae':None,'rmse':None,'wape':None,'bias':None,'mase':None}
    errors = [r['predicted_units']-r['actual_units'] for r in eligible]
    denom = sum(abs(r['actual_units']) for r in eligible)
    valid_scale=[float(r['mase_scale']) for r in eligible
                 if r.get('mase_scale') is not None and math.isfinite(float(r['mase_scale']))
                 and float(r['mase_scale'])>0]
    mase=(sum(abs(e) for e in errors)/n)/(sum(valid_scale)/len(valid_scale)) if len(valid_scale)==n else None
    return {'n':n,'mae':round(sum(abs(e) for e in errors)/n,6),
            'rmse':round(math.sqrt(sum(e*e for e in errors)/n),6),
            'wape':round(sum(abs(e) for e in errors)/denom,6) if denom else None,
            'bias':round(sum(errors)/n,6),
            'mase':round(mase,6) if mase is not None else None}


def evaluate(rows: list[dict]) -> dict:
    bucket=defaultdict(list)
    for row in rows:
        bucket[(row['split'],row['method'],'overall')].append(row)
        bucket[(row['split'],row['method'],f'h{row["horizon"]}')].append(row)
        bucket[(row['split'],row['method'],row['segment'])].append(row)
    out={}
    for (split,method,group), values in sorted(bucket.items()):
        out.setdefault(split,{}).setdefault(method,{})[group] = {
            **metrics(values),
            'censored_skipped':sum(x['censored_target'] for x in values),
            'forecasts':len(values),
        }
    return out


def choose_method(evaluation: dict) -> str:
    """Select using validation only; test is never consulted."""
    options=evaluation.get('validation',{})
    if not options: raise ValueError('no validation forecasts')
    def objective(name):
        m=options[name]['overall']
        return (m['wape'] if m['wape'] is not None else float('inf'),
                m['mae'] if m['mae'] is not None else float('inf'),name)
    eligible=[name for name in options if options[name]['overall']['n']]
    if not eligible: raise ValueError('no uncensored validation target rows')
    if len({options[name]['overall']['n'] for name in eligible})!=1:
        raise ValueError('validation coverage mismatch between candidate methods')
    return min(eligible,key=objective)

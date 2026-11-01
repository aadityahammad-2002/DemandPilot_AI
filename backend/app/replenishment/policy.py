"""Pure, deterministic decision math; no database, clock or random side effects."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from statistics import NormalDist, mean, pstdev
import math


@dataclass(frozen=True)
class DecisionConfig:
    max_pairs: int = 48
    max_extension_days: int = 56
    demand_volatility_floor: float = .25
    stockout_uncertainty_uplift: float = .50
    lead_observation_window_days: int = 180
    min_lead_observations: int = 2
    max_horizon_days: int = 90

    def validate(self):
        if not 1 <= self.max_pairs <= 200000: raise ValueError('invalid max_pairs')
        if not 1 <= self.max_extension_days <= 365: raise ValueError('invalid max_extension_days')
        if not 0 <= self.demand_volatility_floor <= 5: raise ValueError('invalid demand_volatility_floor')
        if not 0 <= self.stockout_uncertainty_uplift <= 5: raise ValueError('invalid stockout_uncertainty_uplift')
        if not 1 <= self.lead_observation_window_days <= 3650: raise ValueError('invalid lead_observation_window_days')
        if not 1 <= self.min_lead_observations <= 10000: raise ValueError('invalid min_lead_observations')
        if not 1 <= self.max_horizon_days <= 365: raise ValueError('invalid max_horizon_days')


def valid_interval(start: str, end: str | None, asof: date) -> bool:
    return date.fromisoformat(start) <= asof and (not end or asof < date.fromisoformat(end))


def supplier_lead_times(contract: dict, observations: list[dict], asof: date, cfg: DecisionConfig) -> dict:
    """Only completed receipts observable by decision date are admissible."""
    base = int(contract['base_lead_time_days'])
    if base <= 0: raise ValueError('NEG-04: negative/zero contract lead time')
    observed = []
    used = []
    for obs in observations:
        if obs['supplier_sku_id'] != contract['supplier_sku_id']: continue
        order = date.fromisoformat(obs['order_date'])
        expected = date.fromisoformat(obs['expected_receipt_date'])
        if expected < order: raise ValueError('invalid expected lead time')
        # Future planned receipts are NEVER used in historical lead-time statistics.
        if order > asof or obs['status'] != 'RECEIVED': continue
        receipt = date.fromisoformat(obs['actual_receipt_date'])
        if receipt > asof or (asof - receipt).days > cfg.lead_observation_window_days: continue
        calculated = (receipt - order).days
        if calculated < 0 or int(obs['observed_lead_time_days']) != calculated:
            raise ValueError('NEG-04: negative/inconsistent observed lead time')
        if int(obs['delay_days']) != (receipt - expected).days:
            raise ValueError('incorrect derived supplier delay')
        used.append(obs['observation_id'])
        observed.append(calculated)
    n=len(observed)
    m=mean(observed) if n else float(base)
    sd=pstdev(observed) if n > 1 else 0.0
    # Projected lead >= contractual minimum. Conservative +1 std for known variability.
    effective=max(base,math.ceil(m+sd))
    return {'base_days':base,'observed_mean_days':round(m,6),
            'observed_std_days':round(sd,6),'observations':n,
            'observed_ids':used,'effective_days':effective,
            'low_confidence':n < cfg.min_lead_observations}


def demand_curve(points: dict[int,float], days: int, cfg: DecisionConfig) -> tuple[list[float],list[str]]:
    """Dated horizon forecasts are *daily point* predictions, not cumulative demand.
    Interpolate between observed horizons and use an explicitly disclosed flat tail.
    """
    if days < 0 or days > cfg.max_horizon_days: raise ValueError('requested horizon out of safety limits')
    if not points: raise ValueError('missing forecast horizons')
    keys=sorted(points)
    if any(not isinstance(h,int) or h<1 for h in keys): raise ValueError('invalid forecast horizon')
    if any(not isinstance(x,(int,float)) or isinstance(x,bool) or
           not math.isfinite(x) or x<0 for x in points.values()):
        raise ValueError('invalid forecast values')
    if days>keys[-1]+cfg.max_extension_days:
        raise ValueError('insufficient forecast horizon for lead time + review period')
    flags=[]
    if days>keys[-1]: flags.append('FLAT_TAIL_EXTRAPOLATION')
    if keys[0]>1: flags.append('EARLY_HORIZON_BACKFILL')
    series=[]
    for day in range(1,days+1):
        if day<=keys[0]: y=points[keys[0]]
        elif day>=keys[-1]: y=points[keys[-1]]
        elif day in points: y=points[day]
        else:
            left=max(x for x in keys if x<day)
            right=min(x for x in keys if x>day)
            y=points[left]+(points[right]-points[left])*(day-left)/(right-left)
        series.append(round(max(0.,float(y)),6))
    return series, flags


def stockout_volatility(observed: list[dict], asof: date, cfg: DecisionConfig) -> dict:
    """Use only origin-visible uncensored observations; zeros with stockout are unknown."""
    # Incoming fact partitions are chronological, but pure helper callers may not be.
    # Sort before selecting the trailing 56 days; duplicates indicate invalid history.
    records=sorted((x for x in observed if x['date']<=asof),key=lambda x:x['date'])
    if len({x['date'] for x in records})!=len(records):
        raise ValueError('duplicate dated sales observations')
    records=records[-56:]
    uncensored=[float(x['sales']) for x in records if not x['stockout'] and x['available']>0]
    zeros=sum(x==0 for x in uncensored)
    fraction=(len(records)-len(uncensored))/len(records) if records else 0.
    # Historical std is an uncertainty proxy, not an unbiased model residual estimate.
    sigma=pstdev(uncensored) if len(uncensored)>1 else 0.
    return {'history_count':len(records),'uncensored_count':len(uncensored),
            'censored_count':len(records)-len(uncensored),'censored_fraction':round(fraction,6),
            'genuine_zero_count':zeros,'sigma_sales_proxy':round(sigma,6)}


def safety_stock(curve:list[float], lead:dict, service_level:float,
                 policy_floor:int, past:dict, cfg:DecisionConfig) -> dict:
    if not 0 < service_level < 1: raise ValueError('service level invalid')
    if policy_floor<0: raise ValueError('safety floor negative')
    mean_daily=mean(curve) if curve else 0.
    std_forecast=pstdev(curve) if len(curve)>1 else 0.
    sigma=max(float(past['sigma_sales_proxy']),std_forecast,
              cfg.demand_volatility_floor*mean_daily)
    sigma*=1+cfg.stockout_uncertainty_uplift*float(past['censored_fraction'])
    lead_days=int(lead['effective_days'])
    lead_std=float(lead['observed_std_days'])
    uncertainty=math.sqrt(lead_days*sigma**2 + mean_daily**2*lead_std**2)
    z=NormalDist().inv_cdf(service_level)
    calculated=math.ceil(max(0,z*uncertainty))
    return {'z':round(z,6),'sigma_daily_units':round(sigma,6),
            'lead_std_days':round(lead_std,6),'uncertainty_units':round(uncertainty,6),
            'calculated_units':calculated,'policy_floor_units':policy_floor,
            'final_units':max(calculated,policy_floor)}


def feasible_lot(raw:int, moq:int, pack:int, supplier_cap:int, sku_cap:int,
                 store_free_units:int, storage_units:int) -> dict:
    """Choose feasible rounded lot; partial fills still satisfy MOQ and pack size."""
    quantities=(raw,moq,pack,supplier_cap,sku_cap,store_free_units,storage_units)
    if any(not isinstance(x,int) or isinstance(x,bool) for x in quantities):
        raise ValueError('lot quantities and capacities must be integers')
    if min(raw,moq,pack,supplier_cap,sku_cap,store_free_units)<0 or storage_units<=0:
        raise ValueError('invalid quantity/capacity')
    if moq==0 or pack==0:raise ValueError('MOQ and pack size must be positive')
    if raw==0:return {'quantity':0,'status':'NO_ORDER','reasons':['REORDER_POINT_NOT_REACHED']}
    rounded=math.ceil(max(raw,moq)/pack)*pack
    max_units=min(supplier_cap,sku_cap,store_free_units//storage_units)
    if rounded<=max_units: return {'quantity':rounded,'status':'GENERATED','reasons':[]}
    reduced=(max_units//pack)*pack
    if reduced>=moq and reduced>0:
        return {'quantity':reduced,'status':'GENERATED','reasons':['CAPACITY_PARTIAL']}
    return {'quantity':0,'status':'BLOCKED','reasons':['CAPACITY_INFEASIBLE']}

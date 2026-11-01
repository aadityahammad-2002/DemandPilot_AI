"""Leakage-free *evaluation-only* 28-day counterfactual policy backtest.

This module is deliberately not an input to forecasting or replenishment.
Hidden latent demand is read ONLY at evaluation time. Both policies receive
identical future demand and exogenous receipts. This is a simulation, not a
claim about actual future sales or observed supplier service performance.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math

from app.generation.verify import iter_rows, verify_file
from app.forecasting.data import load_history
from app.replenishment.inputs import load_signed_manifest, read_master


def simulate(*, daily_demand: list[int], receipts: list[int], on_hand:int,
             order_units:int, lead_days:int, holding_cost_per_unit_day:float=0.0)->dict:
    if len(daily_demand)!=len(receipts) or lead_days<1 or on_hand<0 or order_units<0:
        raise ValueError('invalid backtest input lengths or quantities')
    if holding_cost_per_unit_day<0 or not math.isfinite(holding_cost_per_unit_day):
        raise ValueError('invalid holding cost')
    stock=on_hand;fulfilled=lost=stockout_days=0;stocks=[]
    for day,(demand,received) in enumerate(zip(daily_demand,receipts),start=1):
        if any(not isinstance(x,int) or x<0 for x in (demand,received)):
            raise ValueError('invalid backtest demand or receipt')
        # Incoming order arrives after lead time; same timing for all policies.
        stock+=received + (order_units if day==lead_days else 0)
        sold=min(stock,demand)
        fulfilled+=sold;lost+=demand-sold
        stock-=sold
        stockout_days+=int(demand>sold)
        if stock<0:raise AssertionError('inventory conservation failure')
        stocks.append(stock)
    total=fulfilled+lost
    return {'requested_units':total,'fulfilled_units':fulfilled,'lost_units':lost,
            'fill_rate':round(fulfilled/total,6) if total else None,
            'stockout_days':stockout_days,'stockout_day_share':round(stockout_days/len(stocks),6) if stocks else None,
            'average_closing_stock':round(sum(stocks)/len(stocks),4) if stocks else None,
            'peak_closing_stock':max(stocks,default=0),
            'holding_cost_estimate':round(sum(stocks)*holding_cost_per_unit_day,4),
            'ordered_units':order_units,'order_count':int(order_units>0),
            'days':len(stocks)}


def _round_lot(raw:int,moq:int,pack:int,capacity:int)->int:
    if raw<=0:return 0
    if min(moq,pack,capacity)<=0:raise ValueError('invalid baseline supplier constraint')
    quantity=math.ceil(max(raw,moq)/pack)*pack
    return quantity if quantity<=capacity else 0


def run_backtest(data_root:Path,replenishment_root:Path,out:Path, *, days:int=28,
                 max_pairs:int=100,holding_cost_per_unit_day:float=0.01)->dict:
    if not 1<=days<=90 or not 1<=max_pairs<=200000:
        raise ValueError('invalid backtest scope')
    data_root=data_root.resolve();replenishment_root=replenishment_root.resolve();out=out.resolve()
    if out.exists():raise FileExistsError('backtest refuses existing output file')
    manifest=load_signed_manifest(data_root)
    source_manifest=json.loads((replenishment_root/'replenishment_manifest.json').read_text())
    if source_manifest['source_manifest_sha256']!=hashlib.sha256((data_root/'manifest.json').read_bytes()).hexdigest():
        raise ValueError('replenishment source manifest mismatch')
    rec_path=replenishment_root/'purchase_recommendations.csv'
    if hashlib.sha256(rec_path.read_bytes()).hexdigest()!=source_manifest['files']['purchase_recommendations.csv']:
        raise ValueError('modified recommendations')
    with rec_path.open(newline='',encoding='utf-8') as f:
        recommendations=list(csv.DictReader(f))[:max_pairs]
    if not recommendations:raise ValueError('no recommendations available')
    keys={(r['store_id'],r['sku_id']) for r in recommendations}
    history,_=load_history(data_root,sorted(keys))
    masters=read_master(data_root,manifest)
    contracts={x['supplier_sku_id']:x for x in masters['supplier_sku']}
    skus={x['sku_id']:x for x in masters['sku']}
    # Signed oracle is evaluation-only. Load only selected pairs; no oracle fields
    # are ever passed to the forecasting model or into a recommendation decision.
    hidden=defaultdict(dict);received=defaultdict(dict)
    entries=[x for x in manifest['files'] if x['path'].startswith('restricted_ground_truth/ground_truth/')]
    for entry in entries:
        verify_file(data_root,entry)
        for row in iter_rows(data_root/entry['path']):
            # SALE-STORE-000001-SKU-000001-YYYY-MM-DD
            sid=row['sale_id'];split=sid.split('-');pair=('STORE-'+split[2],'SKU-'+split[4]);day='-'.join(split[5:])
            if pair in keys:hidden[pair][day]=int(row['latent_demand'])
    entries=[x for x in manifest['files'] if x['path'].startswith('facts/inventory_snapshot/')]
    for entry in entries:
        verify_file(data_root,entry)
        for row in iter_rows(data_root/entry['path']):
            pair=(row['store_id'],row['sku_id'])
            if pair in keys:received[pair][row['snapshot_date']]=int(row['received_units'])
    records=[]
    for rec in recommendations:
        pair=(rec['store_id'],rec['sku_id']);origin=date.fromisoformat(rec['decision_date'])
        if rec['status']=='BLOCKED':continue
        contract=contracts[rec['supplier_sku_id']];lead=int(contract['base_lead_time_days'])
        if lead>days:continue
        horizon=[(origin+timedelta(days=i)).isoformat() for i in range(1,days+1)]
        if any(day not in hidden[pair] for day in horizon):continue
        demand=[hidden[pair][day] for day in horizon]
        exogenous_receipts=[received[pair].get(day,0) for day in horizon]
        trace=json.loads(rec['decision_trace'])
        on_hand=int(trace['closing_stock'])-int(trace['reserved_units'])
        if on_hand<0:raise ValueError('invalid recorded on-hand inventory')
        prior=[x['sales'] for x in history[pair] if x['date']<=origin and not x['stockout'] and x['available']>0]
        naive=sum(prior[-28:])/len(prior[-28:]) if prior else 0.0
        baseline_raw=max(0,math.ceil(naive*(lead+14)-on_hand))
        baseline_qty=_round_lot(baseline_raw,int(contract['moq_units']),
                                int(contract['pack_size_units']),int(contract['supplier_capacity_units']))
        experimental_qty=int(rec['recommended_quantity'])
        if experimental_qty<0:raise ValueError('negative recommendation quantity')
        baseline=simulate(daily_demand=demand,receipts=exogenous_receipts,on_hand=on_hand,
                         order_units=baseline_qty,lead_days=lead,
                         holding_cost_per_unit_day=holding_cost_per_unit_day)
        experimental=simulate(daily_demand=demand,receipts=exogenous_receipts,on_hand=on_hand,
                              order_units=experimental_qty,lead_days=lead,
                              holding_cost_per_unit_day=holding_cost_per_unit_day)
        records.append({'store_id':pair[0],'sku_id':pair[1],
                        'recommendation_id':rec['recommendation_id'],
                        'decision_date':origin.isoformat(),'baseline':baseline,'engine':experimental,
                        'service_target':0.95})
    def aggregate(policy):
        total=sum(r[policy]['requested_units'] for r in records)
        served=sum(r[policy]['fulfilled_units'] for r in records)
        return {'requested_units':total,'fulfilled_units':served,
                'fill_rate':round(served/total,6) if total else None,
                'lost_units':total-served,
                'stockout_days':sum(r[policy]['stockout_days'] for r in records),
                'holding_cost_estimate':round(sum(r[policy]['holding_cost_estimate'] for r in records),2),
                'ordered_units':sum(r[policy]['ordered_units'] for r in records),
                'service_target_met_pairs':sum((r[policy]['fill_rate'] or 0)>=r['service_target'] for r in records)}
    result={'status':'PASS' if records else 'NO_OVERLAPPING_HOLDOUT',
            'mode':'SIMULATED_EVALUATION_ONLY','pairs_evaluated':len(records),
            'requested_pairs':len(recommendations),'horizon_days':days,
            'baseline_policy':'trailing uncensored 28-day mean + MOQ/pack + contract capacity',
            'engine_policy':'signed Phase 4 recommendation quantities',
            'same_realization_for_both':True,'training_uses_ground_truth':False,
            'future_demand':'synthetic hidden latent demand, evaluation only',
            'exogenous_receipts':'fixed signed synthetic future receipt schedule for both policies',
            'supplier_capacity_after_order':'not simulated (pre-order constraint only)',
            'shared_store_capacity_after_order':'not modeled in counterfactual receipts',
            'holding_cost_per_unit_day':holding_cost_per_unit_day,
            'source_replenishment_sha256':hashlib.sha256(rec_path.read_bytes()).hexdigest(),
            'baseline':aggregate('baseline'),'engine':aggregate('engine'),'pairs':records}
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    return result


def main():
    p=argparse.ArgumentParser(description='Evaluation-only baseline versus replenishment backtest')
    p.add_argument('--data',required=True,type=Path)
    p.add_argument('--replenishment',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--days',type=int,default=28)
    opts=p.parse_args()
    result=run_backtest(opts.data,opts.replenishment,opts.out,days=opts.days)
    print(json.dumps({k:v for k,v in result.items() if k!='pairs'},sort_keys=True))
    if result['status']!='PASS':raise SystemExit(2)

if __name__=='__main__':main()

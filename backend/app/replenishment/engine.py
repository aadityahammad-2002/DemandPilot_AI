"""Auditable Phase4 stock decisions. Pure run-scoped files; no PostgreSQL mutations."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path
import csv, hashlib, json, math
from app.ids import recommendation_id
from app.forecasting.data import load_history
from app.replenishment import RULE_VERSION
from app.replenishment.lifecycle import sku_eligible_asof
from app.replenishment.open_orders import load_open_orders, pipeline_at
from app.replenishment.inputs import (digest,load_signed_manifest,read_master,
                                        read_inventory_at_dates,latest_selected)
from app.replenishment.policy import (DecisionConfig,valid_interval,supplier_lead_times,
                    demand_curve,stockout_volatility,safety_stock,feasible_lot)

RECOMMENDATION_FIELDS = ('recommendation_id','policy_id','sku_id','store_id',
    'supplier_sku_id','decision_date','forecast_run_id','inventory_date',
    'inventory_position','lead_time_demand','raw_quantity','recommended_quantity',
    'status','reason_codes','rule_version','decision_trace')


def canonical(data):
    return json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False,default=str)


def _active(row:dict,asof:date, from_key:str,to_key:str, status:str)->bool:
    return row['status']==status and valid_interval(str(row[from_key]),str(row[to_key] or ''),asof)


def decide(*, key:tuple[str,str], origin:date, points:dict[int,float],
           sku:dict, store:dict, supplier:dict|None, contract:dict|None,
           policy:dict|None, inventory:dict, store_occupancy:int,
           sales:list[dict], observations:list[dict], cfg:DecisionConfig,
           forecast_run_id:str, rec_id:str,
           capacity_missing_skus:tuple[str,...]=(),
           capacity_synthetic_retired_skus:tuple[str,...]=(),
           allocated_storage_units:int=0,
           open_orders:tuple[dict,...]=()) -> dict:
    """One deterministic decision. Ineligible sources are BLOCKED, never bought.

    Positive recommendations require dates and consistency. Invalid lead time is a
    data-integrity error (fail closed), not converted into a plausible recommendation.
    """
    store_id,sku_id=key
    reasons=[];warnings=[];trace={'origin':origin.isoformat(),'forecast_horizons':points,
         'unit':'each','storage_unit':'SKU.storage_units_per_item',
         'pipeline_units':0,'pipeline_assumption':'NO_VERIFIED_OPEN_PO_SOURCE_IN_PHASE2',
         'stockout_policy':'censored sales never inferred as actual zero demand',
         'lead_time_receipts_rule':'actual_receipt_date<=decision_date',
         'capacity_synthetic_retired_carry_forward_skus':list(capacity_synthetic_retired_skus),
         'capacity_synthetic_assumption':('NO_POST_RETIREMENT_MOVEMENTS_IN_GENERATOR'
             if capacity_synthetic_retired_skus else None)}
    if sku['sku_id']!=sku_id or store['store_id']!=store_id:raise ValueError('pair/master mismatch')
    if not sku_eligible_asof(sku,origin):
        reasons.append('DISCONTINUED_OR_INELIGIBLE_SKU')
    if not _active(store,origin,'open_date','close_date','OPEN'):
        reasons.append('STORE_NOT_OPEN')
    if not policy or policy['sku_id']!=sku_id or policy['store_id']!=store_id or policy['status']!='ACTIVE' or not valid_interval(policy['effective_from'],policy['effective_to'],origin):
        reasons.append('NO_ACTIVE_REORDER_POLICY')
    if not contract or contract['sku_id']!=sku_id or not _active(contract,origin,'valid_from','valid_to','ACTIVE'):
        reasons.append('NO_ACTIVE_SUPPLIER_SKU')
    if not supplier or not _active(supplier,origin,'effective_from','effective_to','ACTIVE'):
        reasons.append('NO_ACTIVE_SUPPLIER')
    if contract and supplier and supplier['supplier_id']!=contract['supplier_id']:
        raise ValueError('supplier contract cross-reference mismatch')
    if policy and contract and policy['supplier_sku_id']!=contract['supplier_sku_id']:
        raise ValueError('policy contract mismatch')
    if inventory['store_id']!=store_id or inventory['sku_id']!=sku_id:
        raise ValueError('inventory pair mismatch')
    stale=inventory['snapshot_date']!=origin.isoformat()
    if stale:
        # Historical inventory is never treated as current for purchasing.
        # A discontinued/ineligible SKU is already BLOCKED, so it may carry
        # its last verified snapshot for FK provenance and audit context.
        if not (inventory.get('historical_fallback_for_block_only') and
                inventory['snapshot_date'] < origin.isoformat() and
                'DISCONTINUED_OR_INELIGIBLE_SKU' in reasons):
            raise ValueError('active inventory must be origin-aligned')
        warnings.append('HISTORICAL_INVENTORY_FOR_BLOCKED_SKU_ONLY')
    closing=int(inventory['closing_stock']);reserved=int(inventory['reserved_units'])
    if closing<0 or reserved<0 or reserved>closing:
        raise ValueError('invalid on-hand or reserved inventory')
    inventory_position=closing-reserved
    if inventory_position<0: raise ValueError('negative inventory position')
    if store_occupancy<0 or store_occupancy>int(store['capacity_units']):
        raise ValueError('inconsistent store occupancy/capacity')
    if not isinstance(allocated_storage_units,int) or isinstance(allocated_storage_units,bool) or allocated_storage_units<0:
        raise ValueError('invalid prior allocated capacity')
    if allocated_storage_units>int(store['capacity_units'])-store_occupancy:
        raise ValueError('prior allocations exceed verified store capacity')
    if any(r['date']>origin for r in sales):raise ValueError('NEG-05: sales after decision origin')
    trace.update({'inventory_id':inventory['inventory_id'],
                  'inventory_snapshot_date':inventory['snapshot_date'],
                  'inventory_snapshot_stale_for_block_only':stale,
                  'closing_stock':int(inventory['closing_stock']),
                  'reserved_units':int(inventory['reserved_units']),
                  'inventory_position':inventory_position,
                  'store_occupied_storage_units':store_occupancy,
                  'store_capacity_units':int(store['capacity_units']),
                  'storage_units_per_item':int(sku['storage_units_per_item']),
                  'capacity_prior_allocated_storage_units':allocated_storage_units,
                  'capacity_allocation_priority':'STORE_ID_DATE_SKU_ID_ASC',
                  'capacity_remaining_before_storage_units':
                      int(store['capacity_units'])-store_occupancy-allocated_storage_units})
    if capacity_missing_skus:
        warnings.append('CAPACITY_SNAPSHOT_INCOMPLETE')
        trace['missing_current_capacity_skus']=list(capacity_missing_skus)
        trace['capacity_coverage']='INCOMPLETE'
    else:
        trace['capacity_coverage']='CURRENT_SNAPSHOTS_COMPLETE'
    if reasons:
        return _result(rec_id,policy,contract,key,origin,forecast_run_id,
                       inventory_position,0,0,0,'BLOCKED',reasons,trace)
    assert contract and policy and supplier
    lead=supplier_lead_times(contract,observations,origin,cfg)
    trace['lead_time']=lead
    review=int(policy['review_period_days'])
    if review<=0:raise ValueError('invalid review interval')
    total_horizon=lead['effective_days']+review
    if total_horizon>cfg.max_horizon_days:
        reasons.append('HORIZON_LIMIT_EXCEEDED')
        return _result(rec_id,policy,contract,key,origin,forecast_run_id,
                       inventory_position,0,0,0,'BLOCKED',reasons,trace)
    try: curve,flags=demand_curve(points,total_horizon,cfg)
    except ValueError:
        reasons.append('INSUFFICIENT_FORECAST_HORIZON')
        return _result(rec_id,policy,contract,key,origin,forecast_run_id,
                       inventory_position,0,0,0,'BLOCKED',reasons,trace)
    warnings+=flags
    hist=stockout_volatility(sales,origin,cfg)
    trace['historical_demand_quality']=hist
    if hist['censored_count']:
        warnings.append('STOCKOUT_CENSORED_HISTORY')
    if hist['uncensored_count']<7:
        warnings.append('LOW_UNCENSORED_HISTORY')
    if lead['low_confidence']:
        warnings.append('LOW_LEAD_OBSERVATION_COUNT')
    ss=safety_stock(curve,lead,float(policy['service_level']),int(policy['safety_stock_units']),hist,cfg)
    lead_demand=sum(curve[:lead['effective_days']]); coverage=sum(curve)
    pipeline_units,open_ids=pipeline_at(list(open_orders),origin,total_horizon)
    inventory_position += pipeline_units
    trace.update({'pipeline_units':pipeline_units, 'external_open_order_refs':open_ids,
                  'inventory_position':inventory_position,
                  'pipeline_accounting':'on_hand_minus_reserved_plus_future_outstanding_once',
                  'open_order_horizon_days':total_horizon})
    reorder_point=math.ceil(lead_demand+ss['final_units'])
    target_stock=math.ceil(coverage+ss['final_units'])
    # Continuous-review trigger with order-up-to protection horizon.
    raw=max(0,target_stock-inventory_position) if inventory_position<=reorder_point else 0
    if capacity_missing_skus and raw>0:
        # A no-order decision needs no additional physical capacity. But no
        # positive PO may be authorized without complete current-day coverage.
        reasons.append('CAPACITY_SNAPSHOT_INCOMPLETE')
        trace.update({'warnings':warnings,'raw_qty':raw,
                      'lead_time_demand':round(lead_demand,6),
                      'reorder_point_units':reorder_point,'target_stock_units':target_stock,
                      'capacity_coverage':'INCOMPLETE_BLOCKED',
                      'capacity_order_rule':'NO_POSITIVE_ORDER_WITH_UNKNOWN_OCCUPANCY'})
        return _result(rec_id,policy,contract,key,origin,forecast_run_id,
                       inventory_position,round(lead_demand,6),raw,0,'BLOCKED',reasons,trace)
    free=(max(0,int(store['capacity_units'])-store_occupancy-allocated_storage_units)
          if not capacity_missing_skus else 0)
    lot=feasible_lot(raw,int(contract['moq_units']),int(contract['pack_size_units']),
            int(contract['supplier_capacity_units']),int(policy['capacity_limit_units']),
            free,int(sku['storage_units_per_item']))
    reasons+=lot['reasons']
    if lot['status']=='GENERATED' and 'CAPACITY_PARTIAL' not in reasons:
        reasons.append('REORDER_POINT_TRIGGERED')
    trace.update({'warnings':warnings,'lead_time_demand':round(lead_demand,6),
                  'protection_period_days':total_horizon,'protection_demand_units':round(coverage,6),
                  'reorder_point_units':reorder_point,'target_stock_units':target_stock,
                  'safety_stock':ss,'policy_version':{'policy_id':policy['policy_id'],
                        'effective_from':policy['effective_from'],'effective_to':policy['effective_to'],
                        'service_level':policy['service_level'],'review_days':review,
                        'capacity_limit_units':int(policy['capacity_limit_units'])},
                  'supplier_contract_version':{'supplier_sku_id':contract['supplier_sku_id'],
                        'valid_from':contract['valid_from'],'valid_to':contract['valid_to'],
                        'moq':contract['moq_units'],'pack':contract['pack_size_units'],
                        'supplier_capacity_units':int(contract['supplier_capacity_units'])},
                  'stock_capacity_available_units':free,
                  'capacity_remaining_after_storage_units':free-lot['quantity']*int(sku['storage_units_per_item']),
                  'raw_qty':raw,'rounded_qty':lot['quantity'],
                  'forecast_daily_curve':curve,'constraint_outcome':lot['status']})
    status='GENERATED' if lot['status']=='GENERATED' else ('BLOCKED' if lot['status']=='BLOCKED' else 'GENERATED')
    return _result(rec_id,policy,contract,key,origin,forecast_run_id,inventory_position,
                   round(lead_demand,6),raw,lot['quantity'],status,reasons,trace)


def _result(rec_id,policy,contract,key,origin,run,position,lead,raw,qty,status,reasons,trace):
    if status=='BLOCKED' and qty!=0:raise AssertionError('blocked quantity must be zero')
    return {'recommendation_id':rec_id,'policy_id':policy['policy_id'] if policy else None,
            'sku_id':key[1],'store_id':key[0],
            'supplier_sku_id':contract['supplier_sku_id'] if contract else None,
            'decision_date':origin.isoformat(),'forecast_run_id':run,
            'inventory_date':trace.get('inventory_snapshot_date',origin.isoformat()),
            'inventory_position':position,
            'lead_time_demand':lead,'raw_quantity':raw,'recommended_quantity':qty,
            'status':status,'reason_codes':reasons,'rule_version':RULE_VERSION,
            'decision_trace':trace}


def missing_current_capacity_skus(store:str, day:str, snapshots:dict,
                                  products:dict, historical_missing=()) -> list[str]:
    """Refuse capacity authorization if any expected physical stock is unknown.

    A launched SKU must have a current-day observation. A previously stocked
    retired SKU lacking current observations also makes capacity uncertain:
    retirement does not prove that the physical product left the store.
    """
    missing=set(historical_missing)
    for product in products.values():
        sku=product['sku_id']
        if (product['launch_date']<=day and
            (not product['discontinue_date'] or day<product['discontinue_date']) and
            ((store,sku,day) not in snapshots or
             snapshots[(store,sku,day)].get('historical_fallback_for_block_only'))):
            missing.add(sku)
    return sorted(missing)


def write_csv(path:Path,rows:list[dict],fields:tuple):
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({k:canonical(row[k]) if isinstance(row[k],(list,dict)) else row[k]
                             for k in fields})


def run(data_root:Path,forecast_root:Path,out:Path,cfg:DecisionConfig, *,
        open_orders_file:Path|None=None)->dict:
    cfg.validate();data_root=data_root.resolve();forecast_root=forecast_root.resolve();out=out.resolve()
    if out.exists() and any(out.iterdir()):raise FileExistsError('refuse overwrite of existing output')
    dataset=load_signed_manifest(data_root)
    selected,fc_manifest=latest_selected(forecast_root,cfg.max_pairs)
    masters=read_master(data_root,dataset)
    def keyed(name,key):return {r[key]:r for r in masters[name]}
    skus=keyed('sku','sku_id');stores=keyed('store','store_id');suppliers=keyed('supplier','supplier_id')
    contracts=keyed('supplier_sku','supplier_sku_id')
    contracts_by_sku=defaultdict(list)
    for c in contracts.values(): contracts_by_sku[c['sku_id']].append(c)
    policies_by_pair=defaultdict(list)
    for p in masters['reorder_policy']:
        policies_by_pair[(p['store_id'],p['sku_id'])].append(p)
    obs_by_contract=defaultdict(list)
    for o in masters['lead_time_observation']:
        obs_by_contract[o['supplier_sku_id']].append(o)
    snapshots,_,capacity_coverage=read_inventory_at_dates(
        data_root,dataset,selected,with_capacity_coverage=True)
    # Also detect a missing current observation for a SKU that should have
    # launched but has *never* appeared in historical inventory. The signed
    # generator models one daily snapshot for each launched, undiscontinued
    # SKU in every open store. End-state SKU.status is not a historic status.
    for (store,day),detail in capacity_coverage.items():
        missing=missing_current_capacity_skus(store,day,snapshots,skus,
                                              detail['missing_current_skus'])
        detail['missing_current_skus']=missing
        detail['complete']=not missing
    # Reconstruct global occupancy for the whole store, using only snapshots at relevant as-of dates.
    occupancy=defaultdict(int)
    for (store,sku,day),inv in snapshots.items():
        if sku not in skus:raise ValueError('inventory references absent master SKU')
        if inv.get('historical_fallback_for_block_only'):
            continue  # Provenance only; never current physical occupancy.
        occupancy[(store,day)]+=inv['closing_stock']*int(skus[sku]['storage_units_per_item'])
    history,_=load_history(data_root,list(selected))
    # Optional, immutable, validated external PO snapshot. It is not derived from
    # lead-time observations or invented out of recommendation history.
    orders=load_open_orders(open_orders_file) if open_orders_file is not None else {}
    for store_id, sku_id in orders:
        if store_id not in stores or sku_id not in skus:
            raise ValueError('external open-order reference has unknown store/SKU parent')
    order_file_hash=digest(open_orders_file) if open_orders_file is not None else None
    # Dataset identities are bound into the run hash; replay is deterministic, independent of wall clock.
    input_digest=hashlib.sha256(canonical({
        'dataset_manifest_sha256':digest(data_root/'manifest.json'),
        'forecast_manifest_sha256':digest(forecast_root/'forecast_manifest.json'),
        'forecast_selected_sha256':digest(forecast_root/'forecast_selected.csv'),
        'config':asdict(cfg),'rule_version':RULE_VERSION,
        'external_open_orders_sha256':order_file_hash}).encode()).hexdigest()
    run_id='RP-RUN-'+input_digest[:20].upper()
    recs=[];audit=[];unbound=[]
    counters=defaultdict(int)
    # Stable priority: store, decision date, then SKU ID ascending. One budget
    # per store/date; unrelated dates and stores cannot consume each other's stock space.
    committed_storage=defaultdict(int)
    for key,(origin,points) in sorted(selected.items(),
            key=lambda item:(item[0][0],item[1][0],item[0][1])):
        store,sku=key;year=origin.year
        counters[year]+=1
        rec_id=recommendation_id(year,counters[year])
        product=skus[sku];shop=stores[store]
        pols=[p for p in policies_by_pair[key] if p['status']=='ACTIVE' and
              valid_interval(p['effective_from'],p['effective_to'],origin)]
        if len(pols)>1:raise ValueError('ambiguous overlapping active reorder policies')
        # Retain a real historical policy as FK provenance for a blocked outcome.
        # Never fabricate a policy ID merely to satisfy the mandatory schema.
        historical=sorted(policies_by_pair[key],key=lambda p:(p['effective_from'],p['policy_id']))
        policy=pols[0] if pols else (historical[-1] if historical else None)
        candidate=contracts.get(policy['supplier_sku_id']) if policy else None
        if candidate is None:
            matches=[c for c in contracts_by_sku[sku] if c['status']=='ACTIVE' and
                     valid_interval(c['valid_from'],c['valid_to'],origin)]
            if len(matches)>1:raise ValueError('ambiguous supplier contract without active policy')
            candidate=matches[0] if matches else None
        supplier=suppliers.get(candidate['supplier_id']) if candidate else None
        inv=snapshots[(store,sku,origin.isoformat())]
        result=decide(key=key,origin=origin,points=points,sku=product,store=shop,
                      supplier=supplier,contract=candidate,policy=policy,inventory=inv,
                      store_occupancy=occupancy[(store,origin.isoformat())],
                      allocated_storage_units=committed_storage[(store,origin.isoformat())],
                      sales=[r for r in history[key] if r['date']<=origin],
                      observations=obs_by_contract.get(candidate['supplier_sku_id'],[]) if candidate else [],
                      cfg=cfg,forecast_run_id=fc_manifest['run_id'],rec_id=rec_id,
                      open_orders=tuple(orders.get(key,())),
                      capacity_missing_skus=tuple(capacity_coverage[(store,origin.isoformat())]['missing_current_skus']),
                      capacity_synthetic_retired_skus=tuple(capacity_coverage[(store,origin.isoformat())]['synthetic_retired_stock_skus']))
        if result['policy_id'] is None or result['supplier_sku_id'] is None:
            # No valid core FK anchor; retain as a separate technical exception,
            # not as an uninsertable PurchaseRecommendation row.
            unbound.append({'attempted_recommendation_id':rec_id,'pair':list(key),
                            'decision_date':origin.isoformat(),
                            'reasons':result['reason_codes'],
                            'trace':result['decision_trace'],
                            'technical_only':'MISSING_CORE_POLICY_OR_CONTRACT_FK'})
            continue
        if result['recommended_quantity']>0:
            committed_storage[(store,origin.isoformat())] += (
                result['recommended_quantity']*int(product['storage_units_per_item']))
            if committed_storage[(store,origin.isoformat())] + occupancy[(store,origin.isoformat())] > int(shop['capacity_units']):
                raise AssertionError('joint store capacity invariant violated')
        recs.append(result)
        audit.append({'recommendation_id':rec_id,'run_id':run_id,'rule_version':RULE_VERSION,
                      'decision_date':origin.isoformat(), 'forecast_origin':origin.isoformat(),
                      'forecast_run_id':fc_manifest['run_id'],
                      'forecast_horizons':sorted(points),
                      'policy_id':policy['policy_id'] if policy else None,
                      'inventory_id':inv['inventory_id'],
                      'source_digest':input_digest,
                      'decision_digest':hashlib.sha256(canonical(result).encode()).hexdigest()})
    if not recs:raise ValueError('no FK-anchored recommendations; inspect source policy/contract inputs')
    out.mkdir(parents=True,exist_ok=True)
    write_csv(out/'purchase_recommendations.csv',recs,RECOMMENDATION_FIELDS)
    write_csv(out/'exceptions.csv',[r for r in recs if r['status']=='BLOCKED'],RECOMMENDATION_FIELDS)
    with (out/'recommendation_traces.jsonl').open('w',encoding='utf-8') as f:
        for r in recs:f.write(canonical({'recommendation_id':r['recommendation_id'],'decision_trace':r['decision_trace']})+'\n')
    with (out/'technical_recommendation_lifecycle_events.jsonl').open('w',encoding='utf-8') as f:
        for r in recs:
            f.write(canonical({'event_id':'EVT-'+r['recommendation_id']+'-000001',
                'recommendation_id':r['recommendation_id'],'previous_status':None,
                'new_status':r['status'],'actor':'PHASE4_DECISION_ENGINE',
                'correlation_id':run_id,'event_seq':1,
                'note':'Run-scoped technical append-only export; PostgreSQL not mutated'})+'\n')
    with (out/'unbound_exceptions.jsonl').open('w',encoding='utf-8') as f:
        for r in unbound:f.write(canonical(r)+'\n')
    with (out/'decision_audit.jsonl').open('w',encoding='utf-8') as f:
        for a in audit:f.write(canonical(a)+'\n')
    summary={'run_id':run_id,'decisions':len(recs),'generated':sum(r['status']=='GENERATED' for r in recs),
             'blocked':sum(r['status']=='BLOCKED' for r in recs),
             'unbound_exceptions':len(unbound),
             'positive':sum(r['recommended_quantity']>0 for r in recs),
             'reasons':dict(sorted((name,sum(name in r['reason_codes'] for r in recs))
                     for name in {name for r in recs for name in r['reason_codes']})),
             'source_pairs':len(selected),'database_mutations':False,
             'run_scoped_ids':'PO IDs require collision-safe allocator for multi-run PostgreSQL integration',
             'full_scale_generation':'DEFERRED_NOT_EXECUTED',
             'pipeline_stock':'EXTERNAL_OPEN_ORDER_SNAPSHOT' if open_orders_file else 'ZERO_NO_VERIFIED_SOURCE',
             'external_open_orders_sha256':order_file_hash,
             'assumptions':['forecast horizon interpolation and explicit bounded flat extension',
                            'uncertainty uses past uncensored sales variability proxy, not calibrated forecast intervals',
                            'current-day store occupancy only; missing previously stocked SKU snapshots block orders']}
    (out/'summary.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    filenames=('purchase_recommendations.csv','exceptions.csv','recommendation_traces.jsonl',
               'technical_recommendation_lifecycle_events.jsonl','decision_audit.jsonl',
               'unbound_exceptions.jsonl','summary.json')
    manifest={'schema_version':'P4-v1','run_id':run_id,'rule_version':RULE_VERSION,
              'forecast_run_id':fc_manifest['run_id'],
              'source_manifest_sha256':digest(data_root/'manifest.json'),
              'forecast_manifest_sha256':digest(forecast_root/'forecast_manifest.json'),
              'input_digest':input_digest,'config':asdict(cfg),
              'external_open_orders_sha256':order_file_hash,
              'database_mutations':False,'full_scale_execution':'DEFERRED',
              'rows':len(recs),'files':{name:digest(out/name) for name in filenames}}
    (out/'replenishment_manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    return {'status':'PASS','run_id':run_id,'decisions':len(recs),'generated':summary['generated'],
            'blocked':summary['blocked'],'positive':summary['positive'],
            'database_mutations':False,'files':manifest['files']}

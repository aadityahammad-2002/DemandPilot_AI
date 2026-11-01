"""Read-only, independent signed-output verification and reproducibility comparison."""
from __future__ import annotations
import csv, hashlib, json, math
from pathlib import Path
from app.ids import recommendation_id
from app.replenishment.inputs import digest
from app.replenishment.engine import RECOMMENDATION_FIELDS,canonical


def verify(root:Path)->dict:
    root=root.resolve()
    m=json.loads((root/'replenishment_manifest.json').read_text(encoding='utf-8'))
    for name,sha in m['files'].items():
        p=(root/name).resolve()
        if not p.is_relative_to(root) or not p.is_file() or digest(p)!=sha:
            raise ValueError('missing/corrupt output '+name)
    with (root/'purchase_recommendations.csv').open(newline='',encoding='utf-8') as f:
        r=csv.DictReader(f)
        if tuple(r.fieldnames)!=RECOMMENDATION_FIELDS:raise ValueError('wrong PurchaseRecommendation schema')
        decisions=list(r)
    if len(decisions)!=m['rows'] or not decisions:raise ValueError('decision count invalid')
    seen=set();blocked=[]
    joint_capacity={}  # Independent store/date cumulative allocation audit (V6).
    for r in decisions:
        rid=r['recommendation_id']
        if rid in seen:raise ValueError('duplicate recommendation ID')
        seen.add(rid)
        if not rid.startswith('PO-'+r['decision_date'][:4]+'-'):
            raise ValueError('wrong PO identifier')
        year=int(r['decision_date'][:4]);ordinal=int(rid.split('-')[2]);
        if recommendation_id(year,ordinal)!=rid:raise ValueError('noncanonical PO identifier')
        reasons=json.loads(r['reason_codes']);trace=json.loads(r['decision_trace'])
        quantity=int(r['recommended_quantity']);raw=int(r['raw_quantity'])
        if min(quantity,raw,int(r['inventory_position']))<0:raise ValueError('negative quantities')
        if not math.isfinite(float(r['lead_time_demand'])) or float(r['lead_time_demand'])<0:raise ValueError('invalid lead demand')
        if r['forecast_run_id']!=m['forecast_run_id'] or r['rule_version']!=m['rule_version']:
            raise ValueError('run/rule mismatch')
        if trace['origin']!=r['decision_date']:
            raise ValueError('point-in-time inventory mismatch')
        if r['inventory_date'] != r['decision_date']:
            # A *zero-order* discontinued-SKU exception may refer to its last
            # real historical inventory observation; it cannot authorize a PO.
            if not (r['status']=='BLOCKED' and quantity==0 and
                    'DISCONTINUED_OR_INELIGIBLE_SKU' in reasons and
                    r['inventory_date'] < r['decision_date'] and
                    trace.get('inventory_snapshot_stale_for_block_only') is True and
                    trace.get('inventory_snapshot_date')==r['inventory_date']):
                raise ValueError('point-in-time inventory mismatch')
        if r['status']=='BLOCKED':
            if quantity!=0 or not reasons:raise ValueError('blocked must have zero quantity and reasons')
            blocked.append(rid)
        elif r['status']=='GENERATED':
            # Backward-compatible: older signed artifacts lack coverage metadata.
            # New artifacts must never authorize purchases while explicitly
            # declaring that current physical capacity is unknown.
            if quantity>0 and trace.get('capacity_coverage') not in (None,'CURRENT_SNAPSHOTS_COMPLETE'):
                raise ValueError('positive order with incomplete capacity evidence')
            if quantity>0:
                contract=trace['supplier_contract_version']
                if quantity%int(contract['pack']) or quantity<int(contract['moq']):
                    raise ValueError('NEG-02: rounded order violates pack/MOQ')
                if quantity*int(trace.get('storage_units_per_item',1)) > trace['stock_capacity_available_units']:
                    raise ValueError('store capacity exceeded')
                if m['rule_version']=='P4-REPLENISH-v3':
                    if quantity>int(contract['supplier_capacity_units']) or quantity>int(trace['policy_version']['capacity_limit_units']):
                        raise ValueError('supplier/policy capacity exceeded')
        else:raise ValueError('unsupported recommendation state')
        if m['rule_version']=='P4-REPLENISH-v3':
            # Detect independently valid lots whose *combined* orders exceed
            # store physical capacity. Also verify recorded deterministic priority.
            group=(r['store_id'],r['decision_date'])
            units=int(trace['storage_units_per_item'])
            capacity=int(trace['store_capacity_units']);occupied=int(trace['store_occupied_storage_units'])
            prior=joint_capacity.setdefault(group,{'capacity':capacity,'occupied':occupied,
                  'allocated':0,'last_sku':None})
            if prior['capacity']!=capacity or prior['occupied']!=occupied:
                raise ValueError('conflicting store capacity evidence')
            if prior['last_sku'] is not None and r['sku_id']<=prior['last_sku']:
                raise ValueError('noncanonical store capacity allocation order')
            if int(trace['capacity_prior_allocated_storage_units'])!=prior['allocated']:
                raise ValueError('inconsistent prior store capacity reservation')
            if int(trace['capacity_remaining_before_storage_units'])!=capacity-occupied-prior['allocated']:
                raise ValueError('incorrect store capacity remaining before order')
            if quantity*units>capacity-occupied-prior['allocated']:
                raise ValueError('joint store capacity exceeded')
            prior['allocated']+=quantity*units
            prior['last_sku']=r['sku_id']
            if 'capacity_remaining_after_storage_units' in trace and int(trace['capacity_remaining_after_storage_units'])!=capacity-occupied-prior['allocated']:
                raise ValueError('incorrect remaining store capacity')
    unbound=[json.loads(s) for s in (root/'unbound_exceptions.jsonl').read_text(encoding='utf-8').splitlines()]
    if any(x['technical_only']!='MISSING_CORE_POLICY_OR_CONTRACT_FK' for x in unbound):
        raise ValueError('invalid unbound exception type')
    with (root/'exceptions.csv').open(newline='',encoding='utf-8') as f:
        ex=list(csv.DictReader(f))
    if {x['recommendation_id'] for x in ex}!=set(blocked):raise ValueError('exception export mismatch')
    for name in ('recommendation_traces.jsonl','technical_recommendation_lifecycle_events.jsonl','decision_audit.jsonl'):
        lines=[json.loads(line) for line in (root/name).read_text(encoding='utf-8').splitlines()]
        if len(lines)!=len(decisions):raise ValueError(name+' row count mismatch')
        if len({r['recommendation_id'] for r in lines})!=len(decisions):raise ValueError(name+' missing recommendation')
        if name.startswith('technical'):
            if any(r['new_status'] not in ('GENERATED','BLOCKED') or r['event_seq']!=1 for r in lines):
                raise ValueError('invalid lifecycle event')
    return {'status':'PASS','rows':len(decisions),'blocked':len(blocked),
            'unbound_exceptions':len(unbound),
            'file_hashes_verified':len(m['files']), 'database_mutations':False}


def compare(a:Path,b:Path)->dict:
    first=verify(a);second=verify(b)
    x=json.loads((a/'replenishment_manifest.json').read_text())
    y=json.loads((b/'replenishment_manifest.json').read_text())
    differences=[key for key in ('run_id','input_digest','forecast_run_id','rows','files') if x[key]!=y[key]]
    return {'status':'FAIL' if differences else 'PASS','files_compared':len(x['files']),
            'differences':differences,'run_id':x['run_id']}

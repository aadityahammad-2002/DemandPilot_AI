"""Bounded-memory, deterministic retail generator. No forecasts or purchase decisions are fabricated.

Every natural SKU-store pair uses its own SHA-256-derived random stream. Chunks
change output partitioning but not the simulated latent demand for that pair.
"""
from __future__ import annotations
import csv
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Iterator

from app.ids import sku_id, store_id, supplier_id, supplier_sku_id, policy_id, observation_id, sale_id, inventory_id
from app.generation.config import GeneratorConfig
from app.generation.writers import PartitionWriter, write_csv

MASTER_COLUMNS = {
    'sku': ['sku_id','category','unit','launch_date','discontinue_date','status','base_demand_rate','storage_units_per_item'],
    'store': ['store_id','region','timezone','open_date','close_date','status','capacity_units'],
    'supplier': ['supplier_id','supplier_name','status','effective_from','effective_to','reliability_factor'],
    'supplier_sku': ['supplier_sku_id','supplier_id','sku_id','valid_from','valid_to','status','base_lead_time_days','moq_units','pack_size_units','unit_cost','supplier_capacity_units'],
    'reorder_policy': ['policy_id','sku_id','store_id','supplier_sku_id','effective_from','effective_to','review_period_days','service_level','safety_stock_units','capacity_limit_units','status'],
    'lead_time_observation': ['observation_id','supplier_sku_id','order_date','expected_receipt_date','actual_receipt_date','observed_lead_time_days','delay_days','status'],
}
SALES_FIELDS = ['sale_id','sku_id','store_id','sales_date','units_sold','selling_price','promotion_flag','promotion_multiplier','stockout_flag','observed_available_units']
INV_FIELDS = ['inventory_id','sku_id','store_id','snapshot_date','opening_stock','received_units','sold_units','adjustment_units','closing_stock','reserved_units']
MOVEMENT_FIELDS = ['event_id','inventory_id','occurred_at','movement_type','quantity_delta','correlation_id','payload']
TRUTH_FIELDS = ['sale_id','latent_demand','lost_demand','actual_zero_demand','stockout_censored','promotion_lift','seasonality_factor','inventory_available']
CATEGORIES = ('GROCERY','FRESH','ELECTRONICS','PERSONAL_CARE','HOME','SEASONAL')
REGIONS = ('North','South','East','West','Central')


def rng_for(seed: int, *keys: object) -> random.Random:
    data = '|'.join(map(str, (seed, *keys))).encode('utf-8')
    return random.Random(int.from_bytes(hashlib.sha256(data).digest()[:16], 'big'))


def qty_poisson(lam: float, rng: random.Random) -> int:
    if lam <= 0: return 0
    if lam > 18:
        return max(0, int(math.floor(rng.gauss(lam, math.sqrt(lam)) + 0.5)))
    threshold = math.exp(-lam)
    prod, i = 1.0, 0
    while prod > threshold:
        prod *= rng.random()
        i += 1
    return i - 1


def days_since(start: date, day: date) -> int:
    return (day-start).days


def as_iso(d: date | None) -> str:
    return '' if d is None else d.isoformat()


def product_record(n: int, cfg: GeneratorConfig, start: date) -> dict:
    r = rng_for(cfg.seed,'sku',n)
    # Deterministic coverage even in tiny smoke profiles.
    launching = (n == 3 or n % 11 == 0) and cfg.days >= 8
    discontinued = (n == 2 or n % 29 == 0) and cfg.days >= 10
    launch_offset = (min(cfg.days-5, max(2, cfg.days // 3)) if n == 3 else
                     r.randint(3, max(3, cfg.days // 2))) if launching else -120
    launch = start + timedelta(days=launch_offset)
    discontinue_offset = max(launch_offset + 3, int(cfg.days * .83))
    discontinuation = start + timedelta(days=discontinue_offset) if discontinued else None
    return dict(sku_id=sku_id(n), category=CATEGORIES[(n-1)%len(CATEGORIES)], unit='each',
                launch_date=as_iso(launch), discontinue_date=as_iso(discontinuation),
                status='DISCONTINUED' if discontinued else 'ACTIVE',
                base_demand_rate=round(r.uniform(.3, 16.0),4) if n%8 else round(r.uniform(.1,2.2),4),
                storage_units_per_item=1 + n % 3)


def supplier_contract(n: int, cfg: GeneratorConfig, start: date) -> dict:
    r = rng_for(cfg.seed,'contract',n)
    pack = (1,6,12,24)[n % 4]
    return dict(supplier_sku_id=supplier_sku_id(n),supplier_id=supplier_id((n-1)%cfg.supplier_count + 1),
                sku_id=sku_id(n), valid_from=as_iso(start-timedelta(days=365)), valid_to='',
                status='ACTIVE',base_lead_time_days=r.randint(2,11),
                moq_units=pack*r.randint(1,3),pack_size_units=pack,
                unit_cost=round(r.uniform(2,150),2),supplier_capacity_units=50000)


def supplier_delay(seed: int, sku_number: int, observation_number: int) -> int:
    r = rng_for(seed,'delay',sku_number,observation_number)
    return r.choice((0,0,0,0,1,1,2,4,7))


def supplier_observations(cfg: GeneratorConfig, start: date) -> Iterator[dict]:
    last = start + timedelta(days=cfg.days - 1)
    for n in range(1,cfg.sku_count+1):
        contract = supplier_contract(n,cfg,start)
        for m, offset in enumerate(range(0,cfg.days,28)):
            order_date = start + timedelta(days=offset)
            expected = order_date + timedelta(days=contract['base_lead_time_days'])
            delay = supplier_delay(cfg.seed,n,m)
            actual = expected + timedelta(days=delay)
            received = actual <= last
            # 365*2000 / 28 ~ 27,000 observation IDs, within six digits.
            seq = (n-1)*math.ceil(cfg.days/28) + m + 1
            yield dict(observation_id=observation_id(seq),supplier_sku_id=contract['supplier_sku_id'],
                       order_date=as_iso(order_date),expected_receipt_date=as_iso(expected),
                       actual_receipt_date=as_iso(actual) if received else '',
                       observed_lead_time_days=(actual-order_date).days if received else '',
                       delay_days=delay if received else '',status='RECEIVED' if received else 'DELAYED')


def write_master(out: Path, name: str, rows: Iterator[dict], manifest: dict) -> None:
    path = out / 'master' / f'{name}.csv'
    data = write_csv(path, MASTER_COLUMNS[name], rows)
    data['path'] = path.relative_to(out).as_posix()
    manifest['files'].append(data)
    manifest['entity_counts'][name] = data['rows']


def read_master(out: Path, name: str) -> Iterator[dict]:
    with (out / 'master' / f'{name}.csv').open(newline='', encoding='utf-8') as f:
        yield from csv.DictReader(f)


def master_generation(cfg: GeneratorConfig, out: Path, manifest: dict, start: date) -> None:
    def skus():
        for n in range(1,cfg.sku_count+1): yield product_record(n,cfg,start)
    def stores():
        for n in range(1,cfg.store_count+1):
            yield dict(store_id=store_id(n),region=REGIONS[(n-1)%5],timezone='Asia/Kolkata',
                       open_date=as_iso(start-timedelta(days=730)),close_date='',
                       status='OPEN',capacity_units=50000)
    def suppliers():
        for n in range(1,cfg.supplier_count+1):
            r=rng_for(cfg.seed,'supplier',n)
            yield dict(supplier_id=supplier_id(n), supplier_name=f'Synthetic Supplier {n:04d}',
                       status='ACTIVE', effective_from=as_iso(start-timedelta(days=730)),
                       effective_to='', reliability_factor=round(r.uniform(.75,.99),3))
    def contracts():
        for n in range(1,cfg.sku_count+1): yield supplier_contract(n,cfg,start)
    def policies():
        for store_n in range(1,cfg.store_count+1):
            for sku_n in range(1,cfg.sku_count+1):
                product=product_record(sku_n,cfg,start)
                supply=supplier_contract(sku_n,cfg,start)
                yield dict(policy_id=policy_id((store_n-1)*cfg.sku_count+sku_n),
                           sku_id=sku_id(sku_n),store_id=store_id(store_n),
                           supplier_sku_id=supplier_sku_id(sku_n),
                           effective_from=product['launch_date'],effective_to=product['discontinue_date'],
                           review_period_days=14,service_level=.95,
                           safety_stock_units=math.ceil(float(product['base_demand_rate'])*3),
                           capacity_limit_units=10000,status='ACTIVE')
    for name,fn in [('sku',skus),('store',stores),('supplier',suppliers),
                    ('supplier_sku',contracts),('reorder_policy',policies)]:
        write_master(out,name,fn(),manifest)
    write_master(out,'lead_time_observation',supplier_observations(cfg,start),manifest)
    manifest['entity_counts']['forecast'] = 0
    manifest['entity_counts']['purchase_recommendation'] = 0
    manifest['deferred_core_entities'] = {
        'forecast':'Produced by forecasting engine in later phase; never invented by data generator',
        'purchase_recommendation':'Produced by replenishment engine in later phase; never invented by data generator'}


def pair_receipts(cfg: GeneratorConfig, sku_n: int, store_n: int,
                  start_idx: int, last_idx: int, base: float, contract: dict) -> dict[int,int]:
    """Scheduled supply is derived from public contract and the deterministic delay series."""
    receipts={}
    lead=int(contract['base_lead_time_days'])
    pack=int(contract['pack_size_units'])
    moq=int(contract['moq_units'])
    store_factor = .65 + (store_n%17)*.035
    target = max(moq, math.ceil(base*store_factor*14*1.05))
    qty = max(pack, math.ceil(target/pack)*pack)
    for order_idx in range(start_idx,last_idx,14):
        # Supplier observations are shared across the stores sourcing each SKU.
        delay = supplier_delay(cfg.seed,sku_n,order_idx//28)
        receipt_idx=order_idx+lead+delay
        if receipt_idx < last_idx:
            receipts[receipt_idx]=receipts.get(receipt_idx,0)+qty
    return receipts


def synthetic_rows(cfg: GeneratorConfig, sku_n: int, store_n: int,
                   product: dict, contract: dict, start: date):
    """Yield daily (sale, inventory, hidden_truth) triplets for eligible dates."""
    rng=rng_for(cfg.seed,'sku-store-history',sku_n,store_n)
    begin=max(0,days_since(start,date.fromisoformat(product['launch_date'])))
    finish=min(cfg.days,days_since(start,date.fromisoformat(product['discontinue_date']))
               if product['discontinue_date'] else cfg.days)
    if begin >= finish: return
    base=float(product['base_demand_rate'])
    store_factor=.65 + (store_n%17)*.035
    contract_lead = int(contract['base_lead_time_days'])
    pack=int(contract['pack_size_units'])
    receipts=pair_receipts(cfg,sku_n,store_n,begin,finish,base,contract)
    opening=max(1,math.ceil(base*store_factor*(contract_lead+2)*(.65 if sku_n%7==0 else 1)))
    for t in range(begin,finish):
        current=start+timedelta(days=t)
        week=1.23 if current.weekday() in (4,5) else .90
        annual = 1.0 + .25*math.sin(2*math.pi*t/365+(sku_n%7)*.5)
        promo = (t + sku_n*3 + store_n%5*2)%61 < 7
        promotion_lift=1.5 if promo else 1.0
        lam = base*store_factor*week*annual*promotion_lift
        intermittent = (sku_n%8==0 or sku_n%11==0)
        latent = 0 if intermittent and rng.random() < .58 else qty_poisson(lam,rng)
        received=receipts.get(t,0)
        available=opening+received
        sold=min(latent,available)
        closing=available-sold
        is_stockout=latent>sold
        sid=sale_id(store_id(store_n),sku_id(sku_n),current)
        sale=dict(sale_id=sid,sku_id=sku_id(sku_n),store_id=store_id(store_n),
                  sales_date=as_iso(current),units_sold=sold,selling_price=round(float(contract['unit_cost'])*1.35/(promotion_lift if promo else 1),2),
                  promotion_flag=promo,promotion_multiplier=promotion_lift,
                  stockout_flag=is_stockout,observed_available_units=available)
        inv=dict(inventory_id=inventory_id(store_id(store_n),sku_id(sku_n),current),
                 sku_id=sku_id(sku_n),store_id=store_id(store_n),snapshot_date=as_iso(current),
                 opening_stock=opening,received_units=received,sold_units=sold,
                 adjustment_units=0,closing_stock=closing,reserved_units=0)
        truth=dict(sale_id=sid,latent_demand=latent,lost_demand=latent-sold,
                   actual_zero_demand=latent==0,stockout_censored=is_stockout,
                   promotion_lift=promotion_lift,seasonality_factor=round(week*annual,6),
                   inventory_available=available)
        yield (t, sale, inv, truth)
        opening=closing


def write_facts(cfg: GeneratorConfig, out: Path, manifest: dict, start: date) -> None:
    products=[None]+list(read_master(out,'sku'))
    contracts=[None]+list(read_master(out,'supplier_sku'))
    period_names=[]
    for t in range(cfg.days):
        key=(start+timedelta(days=t)).strftime('%Y-%m')
        if key not in period_names:
            period_names.append(key)
    stats={'latent_demand_units':0,'observed_sales_units':0,'lost_demand_units':0,
           'stockout_censored_days':0,'actual_zero_demand_days':0,'promotion_days':0,
           'received_units':0,'eligible_sku_store_days':0}
    for first_store in range(1,cfg.store_count+1,cfg.store_batch):
        end_store=min(cfg.store_count,first_store+cfg.store_batch-1)
        writers={}
        for p in period_names:
            for entity,fields in [('sales_history',SALES_FIELDS),('inventory_snapshot',INV_FIELDS),
                                  ('ground_truth',TRUTH_FIELDS)] + ([('tech_inventory_movement_event',MOVEMENT_FIELDS)] if cfg.movement_journal else []):
                sub=('restricted_ground_truth' if entity=='ground_truth' else
                     'technical_events' if entity=='tech_inventory_movement_event' else 'facts')
                filename=f'{entity}_stores_{first_store:03d}-{end_store:03d}.{cfg.format}'
                path=out/sub/entity/p/filename
                writers[(entity,p)]=PartitionWriter(path,fields,cfg.format)
        try:
            for store_n in range(first_store,end_store+1):
                for sku_n in range(1,cfg.sku_count+1):
                    for _,sale,inv,truth in synthetic_rows(cfg,sku_n,store_n,products[sku_n],contracts[sku_n],start):
                        p=sale['sales_date'][:7]
                        writers[('sales_history',p)].add(sale)
                        writers[('inventory_snapshot',p)].add(inv)
                        writers[('ground_truth',p)].add(truth)
                        if cfg.movement_journal:
                            journal = writers[('tech_inventory_movement_event',p)]
                            moves=[]
                            if inv['snapshot_date']==products[sku_n]['launch_date'] or (sale['sales_date']==cfg.start_date and products[sku_n]['launch_date']<cfg.start_date):
                                moves.append(('OPENING',inv['opening_stock']))
                            if inv['received_units']>0: moves.append(('RECEIPT',inv['received_units']))
                            if sale['units_sold']>0: moves.append(('SALE',-sale['units_sold']))
                            for mtype,delta in moves:
                                journal.add(dict(event_id='EV-INV-'+inv['inventory_id']+'-'+mtype,
                                    inventory_id=inv['inventory_id'],
                                    occurred_at=sale['sales_date']+'T23:59:00+00:00',
                                    movement_type=mtype,quantity_delta=delta,correlation_id=sale['sale_id'],
                                    payload=json.dumps({'generator':'P1-T02-v1','source':'synthetic_history'},
                                                       sort_keys=True,separators=(',',':'))))
                        stats['latent_demand_units']+=truth['latent_demand']
                        stats['observed_sales_units']+=sale['units_sold']
                        stats['lost_demand_units']+=truth['lost_demand']
                        stats['stockout_censored_days']+=int(sale['stockout_flag'])
                        stats['actual_zero_demand_days']+=int(truth['actual_zero_demand'])
                        stats['promotion_days']+=int(sale['promotion_flag'])
                        stats['received_units']+=inv['received_units']
                        stats['eligible_sku_store_days']+=1
        finally:
            for (entity,p),w in writers.items():
                # A month may contain no eligible dates in a short configuration.
                if w.count == 0:
                    if w.output: w.output.close()
                    w.path.unlink(missing_ok=True)
                    continue
                entry=w.close()
                entry['path']=w.path.relative_to(out).as_posix()
                entry['entity']=entity
                manifest['files'].append(entry)
                if entity == 'tech_inventory_movement_event':
                    manifest.setdefault('technical_event_counts',{})[entity] = manifest.get('technical_event_counts',{}).get(entity,0)+entry['rows']
                elif entity != 'ground_truth':
                    manifest['entity_counts'][entity] = manifest['entity_counts'].get(entity,0)+entry['rows']
    manifest['summary'] = stats
    manifest['entity_counts']['ground_truth_labels']=stats['eligible_sku_store_days']



def write_retired_inventory(cfg: GeneratorConfig, out: Path, manifest: dict, start: date) -> None:
    """Post-retirement, physical stock remains in the synthetic store.

    The simulator has no disposal/transfer/after-discontinuation sale action.
    Its last simulated closing stock therefore carries forward with zero movements.
    These are *new simulated daily observations*, never inferred from real data
    or loaded as sales/hidden demand. Keep separate from sale-aligned partitions.
    """
    end = start + timedelta(days=cfg.days)
    retired = [(n, product_record(n,cfg,start)) for n in range(1,cfg.sku_count+1)]
    retired = [(n,p) for n,p in retired if p['discontinue_date']]
    count=0
    for first_store in range(1,cfg.store_count+1,cfg.store_batch):
        last_store=min(cfg.store_count,first_store+cfg.store_batch-1)
        writers={}
        try:
            for store_n in range(first_store,last_store+1):
                for sku_n,product in retired:
                    contract=supplier_contract(sku_n,cfg,start)
                    history=synthetic_rows(cfg,sku_n,store_n,product,contract,start)
                    last_inventory=None
                    for _,_,inv,_ in history:
                        last_inventory=inv
                    if last_inventory is None:
                        continue
                    last_day=date.fromisoformat(last_inventory['snapshot_date'])
                    retire_day=date.fromisoformat(product['discontinue_date'])
                    if retire_day != last_day+timedelta(days=1):
                        raise AssertionError('retirement inventory continuity unavailable')
                    closing=last_inventory['closing_stock']
                    while retire_day<end:
                        period=retire_day.strftime('%Y-%m')
                        if period not in writers:
                            path=(out/'facts'/'inventory_snapshot_retired'/period/
                                  f'inventory_snapshot_retired_stores_{first_store:03d}-{last_store:03d}.{cfg.format}')
                            writers[period]=PartitionWriter(path,INV_FIELDS,cfg.format)
                        writers[period].add(dict(
                            inventory_id=inventory_id(store_id(store_n),sku_id(sku_n),retire_day),
                            sku_id=sku_id(sku_n),store_id=store_id(store_n),
                            snapshot_date=as_iso(retire_day),opening_stock=closing,
                            received_units=0,sold_units=0,adjustment_units=0,
                            closing_stock=closing,reserved_units=0))
                        count+=1
                        retire_day+=timedelta(days=1)
        finally:
            for writer in writers.values():
                if not writer.count:
                    if writer.output:writer.output.close()
                    writer.path.unlink(missing_ok=True)
                    continue
                entry=writer.close()
                entry['path']=writer.path.relative_to(out).as_posix()
                entry['entity']='inventory_snapshot'
                manifest['files'].append(entry)
    manifest['entity_counts']['inventory_snapshot']+=count
    manifest['summary']['retired_inventory_snapshot_days']=count
    manifest['inventory_completeness_contract']={
        'version':1,'scope':'SYNTHETIC_ONLY',
        'post_discontinuation_assumption':'NO_FURTHER_SALES_RECEIPTS_TRANSFERS_OR_DISPOSALS',
        'carry_forward':'LAST_VERIFIED_SIMULATED_CLOSING_STOCK',
        'real_data_missing_snapshots':'UNKNOWN_NEVER_ASSUME_ZERO',
        'retired_inventory_snapshot_days':count}

def write_negative_fixtures(cfg: GeneratorConfig, out: Path, manifest: dict, start: date):
    """Faults are deliberately NOT injected into published valid baseline facts."""
    samples=[
       ('NEG-01','Stockout mistaken for zero demand','DETECT_CENSORING',
        {'sales_date':as_iso(start),'units_sold':0,'observed_available_units':0,
         'stockout_flag':True,'hidden_latent_demand':7,'incorrect_training_target':0}),
       ('NEG-02','Reorder quantity ignores pack size','REJECT_UNROUNDED_QUANTITY',
        {'raw_quantity':23,'pack_size_units':12,'moq_units':12,'invalid_recommended_quantity':23,'feasible_quantity':24}),
       ('NEG-03','Discontinued SKU recommended','BLOCK_DISCONTINUED_SKU',
        {'sku_id':sku_id(2 if cfg.sku_count >=2 else 1),
         'sku_status':'DISCONTINUED','requested_quantity':12,'expected_recommended_quantity':0,
         'expected_status':'BLOCKED','expected_reason':'DISCONTINUED_SKU'}),
       ('NEG-04','Negative lead time','REJECT_INVALID_LEAD_TIME',
        {'supplier_sku_id':supplier_sku_id(1),'base_lead_time_days':-3,'expected_sqlstate':'23514'}),
       ('NEG-05','Forecast uses future sales','REJECT_FUTURE_FEATURE',
        {'forecast_origin':as_iso(start+timedelta(days=5)),'feature_sales_date':as_iso(start+timedelta(days=6)),
         'expected':'feature timestamp must be <= forecast origin'})]
    path=out/'negative_fixtures'/'mandatory_cases.jsonl'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='\n') as f:
        for scenario_id,name,expect,fixture in samples:
            f.write(json.dumps(dict(scenario_id=scenario_id,name=name,expected_behavior=expect,
                                    input=fixture, injected_only=True, source='baseline_then_fault'),
                               sort_keys=True,separators=(',',':'))+'\n')
    with path.open('rb') as f: checksum=hashlib.file_digest(f,'sha256').hexdigest()
    manifest['files'].append(dict(path=path.relative_to(out).as_posix(),rows=5,
                                  file_sha256=checksum,bytes=path.stat().st_size))
    manifest['negative_scenarios']=[x[0] for x in samples]


def generate(cfg: GeneratorConfig, out: Path, *, no_overwrite: bool=True) -> dict:
    cfg.validate()
    out=out.resolve()
    if out.exists() and any(out.iterdir()) and no_overwrite:
        raise FileExistsError(f'Refusing non-empty output directory: {out}')
    out.mkdir(parents=True,exist_ok=True)
    start=date.fromisoformat(cfg.start_date)
    backend_root=Path(__file__).resolve().parents[2]
    source_names=['app/ids.py','app/models.py','app/generation/config.py',
                  'app/generation/generator.py','app/generation/verify.py',
                  'app/generation/writers.py','app/generation/negative_overlays.py','alembic/versions/0001_initial.py',
                  'alembic/versions/0002_enforcement.py']
    source_manifest={}
    for name in source_names:
        with (backend_root/name).open('rb') as fh:
            source_manifest[name]=hashlib.file_digest(fh,'sha256').hexdigest()
    manifest={'generator_version':'P1-T02-v2','schema_migration':'0002',
              'config':cfg.as_dict(),'potential_sku_store_days':cfg.sku_count*cfg.store_count*cfg.days,
              'files':[],'entity_counts':{}, 'truth_isolation': 'restricted_ground_truth is never exposed through model input loader',
              'database_mutations': False,'source_manifest':source_manifest}
    master_generation(cfg,out,manifest,start)
    write_facts(cfg,out,manifest,start)
    write_retired_inventory(cfg,out,manifest,start)
    write_negative_fixtures(cfg,out,manifest,start)
    # Entity-shaped adversarial exports, never inserted into canonical baseline.
    from app.generation.negative_overlays import generate_overlays
    generate_overlays(out,manifest,cfg,start)
    manifest['files'].sort(key=lambda x:x['path'])
    config_path=out/'generator_config.json'
    config_path.write_text(json.dumps(cfg.as_dict(),indent=2,sort_keys=True)+'\n',encoding='utf-8')
    path=out/'manifest.json'
    path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return manifest

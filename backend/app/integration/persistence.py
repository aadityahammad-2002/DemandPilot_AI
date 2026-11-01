"""Validated, idempotent ingestion to the official ten entity tables.

Source artifacts remain immutable. The three Phase 1 append-only journals remain
owned by their database triggers and explicit inventory-movement ingestion.
"""
from __future__ import annotations
import csv, hashlib, json, re
from datetime import date
from pathlib import Path
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app import models as m
from app.ids import forecast_id, recommendation_id
from app.replenishment.lifecycle import sku_eligible_asof
from app.integration.types import PersistenceMap, PipelineRun

MASTER = [('sku', m.SKU),('store',m.Store),('supplier',m.Supplier),
          ('supplier_sku',m.SupplierSKU),('reorder_policy',m.ReorderPolicy),
          ('lead_time_observation',m.LeadTimeObservation)]
FACTS = [('sales_history',m.SalesHistory),('inventory_snapshot',m.InventorySnapshot)]

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False,default=str)

def sha(obj):return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()

def convert(model, row):
    from sqlalchemy import Date, DateTime, Integer, Float, Numeric, Boolean, JSON
    result={}
    for col in model.__table__.columns:
        if col.name not in row:continue
        value=row[col.name]
        if value is None or value=='':
            result[col.name]=None
        elif isinstance(col.type,DateTime):
            from datetime import datetime
            result[col.name]=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        elif isinstance(col.type,Date):result[col.name]=date.fromisoformat(str(value))
        elif isinstance(col.type,Boolean):
            if str(value).lower() not in ('true','false','1','0'):raise ValueError(f'invalid boolean {col.name}')
            result[col.name]=str(value).lower() in ('true','1')
        elif isinstance(col.type,Integer):result[col.name]=int(value)
        elif isinstance(col.type,(Float,Numeric)):
            from decimal import Decimal
            result[col.name]=float(value) if isinstance(col.type,Float) else Decimal(str(value))
        elif isinstance(col.type,JSON):result[col.name]=json.loads(value) if isinstance(value,str) else value
        else: result[col.name]=str(value)
    return result

def iter_fact_files(data_root:Path, name:str, allowed_paths=None):
    """Import only manifest-verified partitions, never arbitrary directory contents."""
    if allowed_paths is None:
        raise ValueError('fact import requires verified manifest allowlist')
    prefixes=(f'facts/{name}/',) + (('facts/inventory_snapshot_retired/',) if name=='inventory_snapshot' else ())
    files=sorted(x for x in allowed_paths if x.startswith(prefixes) and x.endswith(('.csv','.parquet')))
    if not files:raise ValueError(f'missing {name} fact partitions')
    for relative in files:
        path=(data_root/relative).resolve()
        if not path.is_relative_to(data_root.resolve()):raise ValueError('fact path escapes data directory')
        if path.suffix=='.csv':
            with path.open(encoding='utf-8',newline='') as f:
                yield from csv.DictReader(f)
        elif path.suffix=='.parquet':
            try:import pyarrow.parquet as pq
            except ImportError as exc:raise RuntimeError('Parquet import requires pyarrow') from exc
            reader=pq.ParquetFile(path)
            for batch in reader.iter_batches(batch_size=512):
                for record in batch.to_pylist():yield record


def _verified_file_allowlist(data_root:Path, manifest:dict)->set[str]:
    """Verify manifest digests and refuse unlisted importable files.

    Files in oracle/negative-fixture directories are verified but never ingested.
    The manifest is a local integrity contract, not a cryptographic signature.
    """
    root=data_root.resolve()
    approved={}
    for entry in manifest['files']:
        relative=entry['path']
        if not isinstance(relative,str) or relative in approved:
            raise ValueError('duplicate or invalid manifest file path')
        artifact=(root/relative).resolve()
        if not artifact.is_relative_to(root) or not artifact.is_file():
            raise ValueError('source manifest path invalid')
        if 'bytes' in entry and artifact.stat().st_size!=int(entry['bytes']):
            raise ValueError('source manifest size mismatch')
        with artifact.open('rb') as source:
            if hashlib.file_digest(source,'sha256').hexdigest()!=entry['file_sha256']:
                raise ValueError('source manifest content hash mismatch')
        approved[relative]=artifact
    ingest_dirs=['master','facts/sales_history','facts/inventory_snapshot','facts/inventory_snapshot_retired',
                 'technical_events/tech_inventory_movement_event']
    for folder in ingest_dirs:
        directory=root/folder
        if not directory.is_dir():continue
        for path in directory.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in ('.csv','.parquet'):continue
            if path.relative_to(root).as_posix() not in approved:
                raise ValueError('unlisted importable source file (manifest mismatch)')
    return set(approved)

def _insert_clean(session:Session, model, data:dict):
    """Idempotent but fail closed on conflicting source values (never overwrite)."""
    pk=model.__mapper__.primary_key[0].name
    exists=session.get(model,data[pk])
    if exists is not None:
        for k,v in data.items():
            existing=getattr(exists,k)
            if isinstance(v,(float,int)) and not isinstance(v,bool):
                same=abs(float(existing)-float(v)) <= 1e-8 * max(1,abs(float(v)))
            elif hasattr(v,'tzinfo') and v.tzinfo is not None and hasattr(existing,'tzinfo') and existing.tzinfo is None:
                from datetime import timezone
                same=existing.replace(tzinfo=timezone.utc)==v.astimezone(timezone.utc)
            else: same=existing==v
            if not same:
                raise ValueError(f'conflicting {model.__tablename__} {data[pk]} field {k}')
        return False
    session.add(model(**data))
    # Flush ensures FK/lifecycle constraints are checked before proceeding.
    session.flush()
    return True

def import_dataset(session:Session, data_root:Path, *, max_fact_rows:int=250000):
    """Explicit opted-in small-sample import; no hidden truth or negative fixtures loaded."""
    # Read-only verification: never rewrite Phase 2 validation_summary.json.
    manifest=json.loads((data_root/'manifest.json').read_text(encoding='utf-8'))
    verified=_verified_file_allowlist(data_root,manifest)
    # Retired-stock carry-forward is a simulator-specific source contract.
    # Check continuity and exact coverage without modifying the signed source.
    # A file checksum alone cannot prove stock continuity across lifecycle days.
    has_retired_files=any(path.startswith('facts/inventory_snapshot_retired/') for path in verified)
    contracted=bool(manifest.get('inventory_completeness_contract'))
    if has_retired_files and not contracted:
        raise ValueError('retired inventory lacks generator completeness contract')
    if contracted:
        # Also catch an entirely missing set of retired partitions; a manifest
        # signed after omission must not bypass this completeness check.
        from app.generation.verify import validate
        validate(data_root,write_report=False)
    counts={}
    for name,model in MASTER:
        p=data_root/'master'/f'{name}.csv'
        if f'master/{name}.csv' not in verified or not p.is_file():
            raise ValueError(f'missing verified master source: {name}')
        n=0
        with p.open(encoding='utf-8',newline='') as f:
            for row in csv.DictReader(f):
                n+=int(_insert_clean(session,model,convert(model,row)))
        counts[name]=n
    total=0
    for name,model in FACTS:
        n=0
        for row in iter_fact_files(data_root,name,verified):
            total+=1
            if total>max_fact_rows:raise ValueError('small dataset row cap exceeded (full scale deferred)')
            n+=int(_insert_clean(session,model,convert(model,row)))
        counts[name]=n
    # Technical inventory events must also be manifest-verified and bounded.
    prefix='technical_events/tech_inventory_movement_event/'
    events=sorted(x for x in verified if x.startswith(prefix) and x.endswith('.csv'))
    n=0
    for relative in events:
        with (data_root/relative).open(newline='',encoding='utf-8') as f:
            for row in csv.DictReader(f):
                total+=1
                if total>max_fact_rows:
                    raise ValueError('small dataset row cap exceeded (full scale deferred)')
                n+=int(_insert_clean(session,m.InventoryMovementEvent,
                                     convert(m.InventoryMovementEvent,row)))
    counts['tech_inventory_movement_event']=n
    session.flush()
    return counts

def _register(session,source_key,source_hash,entity_type,new_id,job_id):
    old=session.get(PersistenceMap,source_key)
    if old:
        if old.source_hash!=source_hash or old.entity_type!=entity_type:
            raise ValueError('immutable artifact changed for same source identity')
        return old.database_id,False
    session.add(PersistenceMap(source_key=source_key,source_hash=source_hash,
          entity_type=entity_type,database_id=new_id,job_id=job_id))
    return new_id,True

def _lock_ids(session):
    if session.bind.dialect.name=='postgresql':
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('yottaflex_phase5_global_ids'))"))

def _free_fc_id(session,row,used):
    existing=row['forecast_id']
    if existing not in used and session.get(m.Forecast,existing) is None:return existing
    month=row['forecast_origin'][:7]
    for version in range(2,10000):
        # Use one version slot across the month; sequence based on stable source ordinal.
        for seq in range(1,1000000):
            candidate=forecast_id(row['store_id'],row['sku_id'],month,version=version,sequence=seq)
            if candidate not in used and session.get(m.Forecast,candidate) is None:
                return candidate
    raise ValueError('forecast ID namespace exhausted')

def import_forecasts(session:Session, root:Path, job_id:str):
    from app.forecasting.verify import verify_run
    verified=verify_run(root)
    if verified.get('status')!='PASS':raise ValueError('forecast artifact validation failed')
    manifest=json.loads((root/'forecast_manifest.json').read_text(encoding='utf-8'))
    rows=list(csv.DictReader((root/'forecast_selected.csv').open(encoding='utf-8',newline='')))
    inserted=0; used=set()
    _lock_ids(session)
    for row in sorted(rows,key=lambda r:(r['store_id'],r['sku_id'],r['forecast_origin'],r['target_date'],r['forecast_id'])):
        source_key='F:'+hashlib.sha256((row['run_id']+'|'+row['forecast_id']).encode()).hexdigest()
        source_hash=sha(row)
        prior=session.get(PersistenceMap,source_key)
        if prior:
            if prior.source_hash!=source_hash:raise ValueError('forecast artifact modified after import')
            if session.get(m.Forecast,prior.database_id) is None:raise ValueError('broken forecast mapping')
            continue
        new_id=_free_fc_id(session,row,used)
        used.add(new_id)
        data=convert(m.Forecast,{**row,'forecast_id':new_id})
        _register(session,source_key,source_hash,'Forecast',new_id,job_id)
        session.add(m.Forecast(**data));session.flush();inserted+=1
    return {'inserted_forecasts':inserted,'selected_model':manifest.get('selected_model'),'source_run_id':manifest['run_id']}

def _next_po(session, year:int):
    from sqlalchemy import func
    max_id=session.execute(select(func.max(m.PurchaseRecommendation.recommendation_id)).where(
        m.PurchaseRecommendation.recommendation_id.like(f'PO-{year}-%'))).scalar_one_or_none()
    # The 0002 rule accepts optional -R suffix. We allocate new base ordinals only.
    candidate=int(max_id.split('-')[2]) if max_id else 0
    while candidate<999999:
        candidate+=1
        proposed=recommendation_id(year,candidate)
        if session.get(m.PurchaseRecommendation,proposed) is None:return proposed
    raise ValueError('annual PO identifier range exhausted')

def import_recommendations(session:Session,root:Path,job_id:str):
    from app.replenishment.verify import verify
    result=verify(root)
    if result.get('status')!='PASS':raise ValueError('recommendation artifact invalid')
    rows=list(csv.DictReader((root/'purchase_recommendations.csv').open(encoding='utf-8',newline='')))
    inserted=0;used=set();_lock_ids(session)
    for row in sorted(rows,key=lambda r:r['recommendation_id']):
        # Bind to immutable contents and the actual Phase 4 input digest.
        manifest=json.loads((root/'replenishment_manifest.json').read_text(encoding='utf-8'))
        source_key='R:'+hashlib.sha256((manifest['run_id']+'|'+row['recommendation_id']).encode()).hexdigest()
        source_hash=sha(row)
        prior=session.get(PersistenceMap,source_key)
        if prior:
            if prior.source_hash!=source_hash:raise ValueError('recommendation artifact modified after import')
            if session.get(m.PurchaseRecommendation,prior.database_id) is None:raise ValueError('broken recommendation mapping')
            continue
        original=row['recommendation_id']
        new_id=original if original not in used and session.get(m.PurchaseRecommendation,original) is None else _next_po(session,int(row['decision_date'][:4]))
        used.add(new_id)
        data=convert(m.PurchaseRecommendation,{**row,'recommendation_id':new_id})
        # 0002 requires an additional standardized reason for post-discontinuation blocks.
        sku=session.get(m.SKU,row['sku_id'])
        if sku and not sku_eligible_asof(sku,data['decision_date']):
            if data['recommended_quantity']!=0 or data['status']!='BLOCKED':
                raise ValueError('positive recommendation outside SKU lifecycle')
        if sku and sku.discontinue_date and data['decision_date']>=sku.discontinue_date:
            if 'DISCONTINUED_SKU' not in data['reason_codes']:
                data['reason_codes']=[*data['reason_codes'],'DISCONTINUED_SKU']
                data['decision_trace']={**data['decision_trace'],
                     'integration_reason_normalization':'DISCONTINUED_SKU (DB enforcement code)',
                     'original_reason_codes':json.loads(row['reason_codes'])}
        _register(session,source_key,source_hash,'PurchaseRecommendation',new_id,job_id)
        session.add(m.PurchaseRecommendation(**data));session.flush();inserted+=1
    return {'inserted_recommendations':inserted,'source_run_id':manifest['run_id']}

"""PostgreSQL-backed typed FastAPI surface for all ten mandatory entities."""
from __future__ import annotations
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date
import json
from pathlib import Path
from typing import Literal
from fastapi import FastAPI,APIRouter,Depends,Header,HTTPException,Query,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import create_engine,func,select,and_,or_,desc
from sqlalchemy.exc import IntegrityError,SQLAlchemyError
from sqlalchemy.orm import Session
from app import models as m
from app.integration.settings import settings,validate_settings
from app.integration.types import PipelineRun,PersistenceMap
from app.integration.jobs import create_job,recover_interrupted

class JobRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    forecast_job_id:str|None=Field(default=None,pattern=r'^JOB-[0-9A-F]{28}$')

class TransitionRequest(BaseModel):
    status:Literal['VALIDATED','ACCEPTED','REJECTED','EXPIRED','BLOCKED']

class Page(BaseModel):
    items:list[dict]
    total:int=Field(ge=0)
    limit:int=Field(ge=1)
    offset:int=Field(ge=0)

def as_dict(obj):
    if obj is None:return None
    return {c.name:getattr(obj,c.name) for c in obj.__table__.columns}

def create_app(config=None, engine=None, *, init_on_start=True):
    cfg=config or settings()
    if engine is None:
        # Validate on startup so importing the module never connects or mutates state.
        from app.config import database_url as configured_url
        engine=create_engine(cfg['database_url'] or configured_url(),pool_pre_ping=True)
    @asynccontextmanager
    async def lifespan(app):
        if init_on_start:
            validate_settings(cfg)
            # Fails fast without silently creating tables: user applies Alembic 0003.
            from sqlalchemy import text
            # PostgreSQL session-level lock: a second API process must never mark
            # the live first process's QUEUED/RUNNING jobs as INTERRUPTED.
            # PostgreSQL session-level advisory locks must not hold an idle
            # transaction open for the entire API lifetime. The connection is
            # dedicated to the lock, retained until shutdown, in AUTOCOMMIT mode.
            conn=engine.connect().execution_options(isolation_level='AUTOCOMMIT')
            lock_acquired=False
            try:
                if conn.dialect.name!='postgresql':raise RuntimeError('production API requires PostgreSQL')
                revision=conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
                if revision!='0005':raise RuntimeError('Apply alembic upgrade head (0005) before starting API')
                lock_acquired=bool(conn.scalar(text("SELECT pg_try_advisory_lock(hashtext('yottaflex_phase5_api_worker'))")))
                if not lock_acquired:
                    raise RuntimeError('Another YottaFlex API worker is running; use one worker only')
                recover_interrupted(engine)
                yield
            finally:
                if lock_acquired:
                    conn.execute(text("SELECT pg_advisory_unlock(hashtext('yottaflex_phase5_api_worker'))"))
                conn.close()
        else:
            yield
    api=FastAPI(title='YottaFlex Retail Intelligence',version='1.0.0',lifespan=lifespan)
    api.add_middleware(CORSMiddleware,allow_origins=cfg['cors_origins'],allow_methods=['GET','POST','PATCH'],allow_headers=['Content-Type','Idempotency-Key'])
    @api.exception_handler(ValueError)
    async def value_error(request,exc):
        # Domain code can include local paths, input rows and untrusted descriptions.
        # Only explicitly known request errors are returned verbatim.
        allowed={'idempotency key already belongs to a different request',
                 'Idempotency-Key header must be between 8 and 128 characters',
                 'unsupported job kind'}
        message=str(exc) if str(exc) in allowed else 'Request or stored artifact failed validation'
        return JSONResponse(status_code=422,content={'error':{'code':'INVALID_ARGUMENT','message':message}})

    @api.exception_handler(IntegrityError)
    async def integrity_error(request,exc):
        return JSONResponse(status_code=409,content={'error':{'code':'DATABASE_CONSTRAINT','message':'Database constraint rejected the operation; no changes committed'}})
    @api.exception_handler(SQLAlchemyError)
    async def db_error(request,exc):
        return JSONResponse(status_code=503,content={'error':{'code':'DATABASE_ERROR','message':'Database operation failed; inspect local server logs'}})
    @api.exception_handler(HTTPException)
    async def http_error(request,exc):return JSONResponse(status_code=exc.status_code,content={'error':{'code':'HTTP_ERROR','message':exc.detail}})

    def db():
        with Session(engine) as session:yield session

    router=APIRouter(prefix='/api/v1')
    @api.get('/health')
    def health():
        return {'status':'ok','service':'yottaflex-api','version':'1.0.0'}

    def query_page(db,model,conditions,limit,offset,order_col):
        where=and_(*conditions) if conditions else True
        total=db.scalar(select(func.count()).select_from(model).where(where)) or 0
        rows=db.execute(select(model).where(where).order_by(order_col,*[k.asc() for k in model.__mapper__.primary_key]).limit(limit).offset(offset)).scalars().all()
        return {'items':[as_dict(r) for r in rows],'total':total,'limit':limit,'offset':offset}

    def filter_date(column,from_date,to_date):
        if from_date and to_date and from_date>to_date:raise HTTPException(422,'from_date must be <= to_date')
        return ([column>=from_date] if from_date else [])+([column<=to_date] if to_date else [])

    @router.get('/dataset-validation')
    def dataset_validation():
        """Read-only generation evidence; never expose restricted issue values."""
        root=cfg['data_root']
        source=root/'manifest.json'
        if not source.is_file():
            return {'available':False,'reason':'No generated dataset configured'}
        try:
            record=json.loads(source.read_text(encoding='utf-8'))
            contract=record.get('negative_overlay_contract')
            if contract is None:
                return {'available':False,'reason':'Dataset predates labelled overlay support',
                        'generator_version':record.get('generator_version')}
            counts=contract['classification']
            return {'available':True,'evidence_kind':'RECORDED_GENERATOR_MANIFEST',
                    'not_live_verification':True,
                    'scenario_ids':contract['issue_types'],
                    'overlay_rows':contract['row_count'],
                    'controls':contract['control_count'],
                    'classification':counts,
                    'baseline_tables_modified':contract['baseline_tables_modified'],
                    'database_import_allowed':contract['database_import_allowed']}
        except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError):
            return {'available':False,'reason':'Validation manifest is unavailable or invalid'}

    @router.get('/skus',response_model=Page)
    def skus(q:str|None=Query(None,max_length=80),category:str|None=None,status:str|None=None,limit:int=Query(30,ge=1,le=200),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        conditions=([or_(m.SKU.sku_id.ilike(f'%{q}%'),m.SKU.category.ilike(f'%{q}%'))] if q else [])
        if category:conditions.append(m.SKU.category==category)
        if status:conditions.append(m.SKU.status==status)
        return query_page(db,m.SKU,conditions,limit,offset,m.SKU.sku_id)

    @router.get('/stores',response_model=Page)
    def stores(q:str|None=Query(None,max_length=80),region:str|None=None,limit:int=Query(30,ge=1,le=200),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        conditions=([m.Store.store_id.ilike(f'%{q}%')] if q else [])
        if region:conditions.append(m.Store.region==region)
        return query_page(db,m.Store,conditions,limit,offset,m.Store.store_id)

    @router.get('/suppliers',response_model=Page)
    def suppliers(status:str|None=None,limit:int=Query(30,ge=1,le=200),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        return query_page(db,m.Supplier,[m.Supplier.status==status] if status else [],limit,offset,m.Supplier.supplier_id)

    @router.get('/supplier-contracts',response_model=Page)
    def contracts(sku_id:str|None=None,supplier_id:str|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        conditions=[]
        if sku_id:conditions.append(m.SupplierSKU.sku_id==sku_id)
        if supplier_id:conditions.append(m.SupplierSKU.supplier_id==supplier_id)
        return query_page(db,m.SupplierSKU,conditions,limit,offset,m.SupplierSKU.supplier_sku_id)

    @router.get('/lead-times',response_model=Page)
    def leads(supplier_sku_id:str|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        return query_page(db,m.LeadTimeObservation,[m.LeadTimeObservation.supplier_sku_id==supplier_sku_id] if supplier_sku_id else [],limit,offset,m.LeadTimeObservation.order_date.desc())

    @router.get('/policies',response_model=Page)
    def policies(sku_id:str|None=None,store_id:str|None=None,status:str|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        conditions=[]
        for name,value in [('sku_id',sku_id),('store_id',store_id),('status',status)]:
            if value:conditions.append(getattr(m.ReorderPolicy,name)==value)
        return query_page(db,m.ReorderPolicy,conditions,limit,offset,m.ReorderPolicy.policy_id)

    @router.get('/sales',response_model=Page)
    def sales(sku_id:str|None=None,store_id:str|None=None,from_date:date|None=None,to_date:date|None=None,stockout_only:bool=False,limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        conditions=filter_date(m.SalesHistory.sales_date,from_date,to_date)
        if stockout_only:conditions.append(m.SalesHistory.stockout_flag.is_(True))
        if sku_id:conditions.append(m.SalesHistory.sku_id==sku_id)
        if store_id:conditions.append(m.SalesHistory.store_id==store_id)
        return query_page(db,m.SalesHistory,conditions,limit,offset,m.SalesHistory.sales_date.desc())

    @router.get('/inventory',response_model=Page)
    def inventory(sku_id:str|None=None,store_id:str|None=None,from_date:date|None=None,to_date:date|None=None,limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        conditions=filter_date(m.InventorySnapshot.snapshot_date,from_date,to_date)
        if sku_id:conditions.append(m.InventorySnapshot.sku_id==sku_id)
        if store_id:conditions.append(m.InventorySnapshot.store_id==store_id)
        return query_page(db,m.InventorySnapshot,conditions,limit,offset,m.InventorySnapshot.snapshot_date.desc())

    @router.get('/forecasts',response_model=Page)
    def forecasts(sku_id:str|None=None,store_id:str|None=None,run_id:str|None=None,model_version:str|None=None,origin_date:date|None=None,from_date:date|None=None,to_date:date|None=None,limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        conditions=filter_date(m.Forecast.target_date,from_date,to_date)
        if origin_date:conditions.append(m.Forecast.forecast_origin==origin_date)
        for name,value in [('sku_id',sku_id),('store_id',store_id),('run_id',run_id),('model_version',model_version)]:
            if value:conditions.append(getattr(m.Forecast,name)==value)
        return query_page(db,m.Forecast,conditions,limit,offset,m.Forecast.target_date.desc())

    @router.get('/recommendations',response_model=Page)
    def recommendations(sku_id:str|None=None,store_id:str|None=None,status:str|None=None,outcome:Literal['positive','zero','blocked']|None=None,positive:bool|None=None,forecast_run_id:str|None=None,replenishment_job_id:str|None=None,from_date:date|None=None,to_date:date|None=None,limit:int=Query(100,ge=1,le=200),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        conditions=filter_date(m.PurchaseRecommendation.decision_date,from_date,to_date)
        if forecast_run_id:conditions.append(m.PurchaseRecommendation.forecast_run_id==forecast_run_id)
        if replenishment_job_id:
            conditions.append(m.PurchaseRecommendation.recommendation_id.in_(select(PersistenceMap.database_id).where(
                PersistenceMap.entity_type=='PurchaseRecommendation',PersistenceMap.job_id==replenishment_job_id)))
        for name,value in [('sku_id',sku_id),('store_id',store_id),('status',status)]:
            if value:conditions.append(getattr(m.PurchaseRecommendation,name)==value)
        if positive is not None:conditions.append(m.PurchaseRecommendation.recommended_quantity > 0 if positive else m.PurchaseRecommendation.recommended_quantity==0)
        if outcome=='positive':conditions.extend([m.PurchaseRecommendation.recommended_quantity>0,m.PurchaseRecommendation.status!='BLOCKED'])
        elif outcome=='zero':conditions.extend([m.PurchaseRecommendation.recommended_quantity==0,m.PurchaseRecommendation.status!='BLOCKED'])
        elif outcome=='blocked':conditions.append(m.PurchaseRecommendation.status=='BLOCKED')
        where=and_(*conditions) if conditions else True
        total=db.scalar(
            select(func.count())
            .select_from(m.PurchaseRecommendation)
            .where(where)
        ) or 0

        columns=[
            c for c in m.PurchaseRecommendation.__table__.columns
            if c.name!='decision_trace'
        ]

        rows=db.execute(
            select(*columns)
            .where(where)
            .order_by(
                m.PurchaseRecommendation.decision_date.desc(),
                m.PurchaseRecommendation.recommendation_id.asc()
            )
            .limit(limit)
            .offset(offset)
        ).mappings().all()

        page={
            'items':[dict(row) for row in rows],
            'total':total,
            'limit':limit,
            'offset':offset
        }
        ids={row['supplier_sku_id'] for row in page['items'] if row['supplier_sku_id']}
        contracts={c.supplier_sku_id:c for c in db.scalars(
            select(m.SupplierSKU).where(m.SupplierSKU.supplier_sku_id.in_(ids))).all()} if ids else {}
        supplier_ids={c.supplier_id for c in contracts.values()}
        suppliers={s.supplier_id:s for s in db.scalars(
            select(m.Supplier).where(m.Supplier.supplier_id.in_(supplier_ids))).all()} if supplier_ids else {}
        for row in page['items']:
            contract=contracts.get(row['supplier_sku_id'])
            supplier=suppliers.get(contract.supplier_id) if contract else None
            row['supplier_name']=supplier.supplier_name if supplier else None
            row['unit_cost']=float(contract.unit_cost) if contract else None
            row['estimated_cost']=round(row['recommended_quantity']*float(contract.unit_cost),2) if contract else None
        return page

    @router.get('/recommendations/{rec_id}')
    def recommendation_detail(rec_id:str,db:Session=Depends(db)):
        row=db.get(m.PurchaseRecommendation,rec_id)
        if not row:raise HTTPException(404,'Recommendation not found')
        contract=db.get(m.SupplierSKU,row.supplier_sku_id)
        supplier=db.get(m.Supplier,contract.supplier_id) if contract else None
        pol=db.get(m.ReorderPolicy,row.policy_id)
        journal=db.execute(select(m.RecommendationLifecycleEvent).where(m.RecommendationLifecycleEvent.recommendation_id==rec_id).order_by(m.RecommendationLifecycleEvent.occurred_at)).scalars().all()
        return {'recommendation':as_dict(row),'supplier':as_dict(supplier), 'contract':as_dict(contract), 'policy':as_dict(pol),'events':[as_dict(x) for x in journal]}

    @router.patch('/recommendations/{rec_id}')
    def transition(rec_id:str,payload:TransitionRequest,db:Session=Depends(db)):
        with db.begin():
            row=db.get(m.PurchaseRecommendation,rec_id,with_for_update=True)
            if not row:raise HTTPException(404,'Recommendation not found')
            if row.status==payload.status:return as_dict(row)
            # DB 0002 transition trigger is authoritative; reject invalid in API first.
            allowed={'GENERATED':{'VALIDATED','REJECTED','BLOCKED'},'VALIDATED':{'ACCEPTED','REJECTED','EXPIRED','BLOCKED'}}
            if payload.status not in allowed.get(row.status,set()):raise HTTPException(409,'Invalid lifecycle transition')
            row.status=payload.status
            db.flush()
            return as_dict(row)

    @router.get('/exceptions',response_model=Page)
    def exceptions(sku_id:str|None=None,store_id:str|None=None,forecast_run_id:str|None=None,
                   replenishment_job_id:str|None=None,from_date:date|None=None,to_date:date|None=None,
                   limit:int=Query(100,ge=1,le=200),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        conditions=[m.PurchaseRecommendation.status=='BLOCKED']
        conditions.extend(filter_date(m.PurchaseRecommendation.decision_date,from_date,to_date))
        if sku_id:conditions.append(m.PurchaseRecommendation.sku_id==sku_id)
        if store_id:conditions.append(m.PurchaseRecommendation.store_id==store_id)
        if forecast_run_id:conditions.append(m.PurchaseRecommendation.forecast_run_id==forecast_run_id)
        if replenishment_job_id:
            conditions.append(m.PurchaseRecommendation.recommendation_id.in_(select(PersistenceMap.database_id).where(
                PersistenceMap.entity_type=='PurchaseRecommendation',PersistenceMap.job_id==replenishment_job_id)))
        return query_page(db,m.PurchaseRecommendation,conditions,limit,offset,m.PurchaseRecommendation.decision_date.desc())

    @router.get('/capabilities')
    def capabilities():
        return {'allow_demo_import':cfg['allow_demo_import'], 'max_pairs':cfg['max_pairs'], 'full_scale_deferred':True}

    @router.get('/overview')
    def overview(db:Session=Depends(db)):
        sales_total=db.scalar(select(func.sum(m.SalesHistory.units_sold))) or 0
        sku_count=db.scalar(select(func.count()).select_from(m.SKU)) or 0
        stores=db.scalar(select(func.count()).select_from(m.Store)) or 0
        stockouts=db.scalar(select(func.count()).select_from(m.SalesHistory).where(m.SalesHistory.stockout_flag==True)) or 0
        positive=db.scalar(select(func.count()).select_from(m.PurchaseRecommendation).where(m.PurchaseRecommendation.recommended_quantity>0)) or 0
        blocked=db.scalar(select(func.count()).select_from(m.PurchaseRecommendation).where(m.PurchaseRecommendation.status=='BLOCKED')) or 0
        fc=db.scalar(select(func.count()).select_from(m.Forecast)) or 0
        last_date=db.scalar(select(func.max(m.InventorySnapshot.snapshot_date)))
        stock=db.scalar(select(func.sum(m.InventorySnapshot.closing_stock)).where(m.InventorySnapshot.snapshot_date==last_date)) or 0 if last_date else 0
        latest=db.execute(select(PipelineRun).where(PipelineRun.status=='SUCCEEDED').order_by(PipelineRun.finished_at.desc()).limit(1)).scalar_one_or_none()
        return {'sku_count':sku_count,'store_count':stores,'total_units_sold':sales_total,'latest_inventory_units':stock,
                'inventory_as_of':last_date,'stockout_observations':stockouts,'forecast_records':fc,
                'positive_recommendations':positive,'blocked_recommendations':blocked,
                'last_successful_run':as_dict(latest) if latest else None}

    @router.get('/operations-summary')
    def operations_summary(forecast_run_id:str|None=None,replenishment_job_id:str|None=None,from_date:date|None=None,to_date:date|None=None,db:Session=Depends(db)):
        """Bounded store-level recommendation aggregation for one selected forecast run.

        If no run is selected, use latest successful replenishment job's linked
        forecast run; never blend recommendations from unrelated historical runs.
        """
        from app.integration.types import PipelineRun
        if from_date and to_date and from_date > to_date:
            raise HTTPException(422, 'from_date must be <= to_date')
        if not forecast_run_id:
            latest=db.execute(select(PipelineRun).where(PipelineRun.kind=='REPLENISH',
                PipelineRun.status=='SUCCEEDED').order_by(PipelineRun.finished_at.desc(),PipelineRun.job_id.desc()).limit(1)).scalar_one_or_none()
            if latest:
                forecast_job_id=(latest.result or {}).get('parameters',{}).get('forecast_job_id')
                if forecast_job_id:
                    forecast_job=db.get(PipelineRun,forecast_job_id)
                    if forecast_job and forecast_job.status=='SUCCEEDED' and forecast_job.kind=='FORECAST':
                        forecast_run_id=(forecast_job.result or {}).get('source_run_id')
        if not forecast_run_id:
            return {'available':False,'forecast_run_id':None,'stores':[],'total_recommendations':0,
                    'positive_recommendations':0,'blocked_recommendations':0,'recommended_units':0}
        filters=[m.PurchaseRecommendation.forecast_run_id==forecast_run_id]
        if replenishment_job_id:
            filters.append(m.PurchaseRecommendation.recommendation_id.in_(select(PersistenceMap.database_id).where(
                PersistenceMap.entity_type=='PurchaseRecommendation',PersistenceMap.job_id==replenishment_job_id)))
        filters.extend(filter_date(m.PurchaseRecommendation.decision_date,from_date,to_date))
        counts=db.execute(select(m.PurchaseRecommendation.store_id,
          func.count(),
          func.sum(__import__('sqlalchemy').case((m.PurchaseRecommendation.recommended_quantity>0,1),else_=0)),
          func.sum(__import__('sqlalchemy').case((m.PurchaseRecommendation.status=='BLOCKED',1),else_=0)),
          func.sum(m.PurchaseRecommendation.recommended_quantity))
          .where(*filters).group_by(m.PurchaseRecommendation.store_id)
          .order_by(m.PurchaseRecommendation.store_id)).all()
        stores=[{'store_id':r[0],'decisions':int(r[1]),'positive':int(r[2] or 0),
                 'blocked':int(r[3] or 0),'units':int(r[4] or 0)} for r in counts]
        return {'available':bool(stores),'forecast_run_id':forecast_run_id,'stores':stores,
                'total_recommendations':sum(r['decisions'] for r in stores),
                'positive_recommendations':sum(r['positive'] for r in stores),
                'blocked_recommendations':sum(r['blocked'] for r in stores),
                'recommended_units':sum(r['units'] for r in stores)}

    @router.get('/run-catalog')
    def run_catalog(db:Session=Depends(db)):
        """Resolve technical job identifiers to persisted business run identifiers.

        Source run IDs from the Forecast table are authoritative; a JOB- ID is
        never silently used as a FC-RUN- filter.  Only imported rows are shown.
        """
        completed=db.scalars(select(PipelineRun).where(PipelineRun.status=='SUCCEEDED')
            .order_by(PipelineRun.finished_at.desc(),PipelineRun.job_id.desc()).limit(250)).all()
        source_jobs={}
        for job in completed:
            if job.kind=='FORECAST':
                source=(job.result or {}).get('source_run_id')
                if source and source not in source_jobs:source_jobs[source]=job.job_id
        # Keep job metadata lookups bounded: retrieving a parent for each completed
        # replenishment job used to produce N+1 SELECTs on /run-catalog.
        parent_ids={((j.result or {}).get('parameters') or {}).get('forecast_job_id')
                    for j in completed if j.kind=='REPLENISH'}
        parent_ids.discard(None)
        # Most parents are already in the identity map from the completed list;
        # only fetch out-of-window forecast parents, in one query.
        parents={j.job_id:j for j in completed if j.job_id in parent_ids}
        missing_parent_ids=parent_ids-parents.keys()
        if missing_parent_ids:
            parents.update({j.job_id:j for j in db.scalars(select(PipelineRun).where(
                PipelineRun.job_id.in_(missing_parent_ids))).all()})
        rec_runs=set(db.scalars(select(m.PurchaseRecommendation.forecast_run_id).distinct()).all())
        forecasts=db.execute(select(m.Forecast.run_id,func.min(m.Forecast.target_date),
                 func.max(m.Forecast.target_date),func.count()).group_by(m.Forecast.run_id)
                 .order_by(func.max(m.Forecast.target_date).desc(),m.Forecast.run_id.desc()).limit(250)).all()
        forecast_jobs={j.job_id:j for j in completed if j.kind=='FORECAST'}
        runs=[{'run_id':r[0],'job_id':source_jobs.get(r[0]),
               'completed_at':forecast_jobs[source_jobs[r[0]]].finished_at if r[0] in source_jobs else None,
               'status':'SUCCEEDED' if r[0] in source_jobs else 'IMPORTED',
               'from_date':r[1], 'to_date':r[2],
               'forecast_count':r[3],'has_recommendations':r[0] in rec_runs}
              for r in forecasts]
        # The newest completed forecast job and newest target *date* are not
        # interchangeable. Prefer execution chronology when backed by a job.
        source_ids={r['run_id'] for r in runs}
        latest_source_run=next((j.result.get('source_run_id')
            for j in completed if j.kind=='FORECAST' and (j.result or {}).get('source_run_id') in source_ids),
            runs[0]['run_id'] if runs else None)
        latest_replenishment_run=None
        for job in completed:
            if job.kind!='REPLENISH':continue
            parent_id=(job.result or {}).get('parameters',{}).get('forecast_job_id')
            parent=parents.get(parent_id)
            source=(parent.result or {}).get('source_run_id') if parent and parent.status=='SUCCEEDED' else None
            if source in rec_runs:
                latest_replenishment_run=source
                break
        if not latest_replenishment_run:
            # Older valid imported records may predate a retained job ledger.
            # Do not infer an unrelated forecast job; use the recorded source run.
            recent=db.scalar(select(m.PurchaseRecommendation.forecast_run_id)
                .order_by(m.PurchaseRecommendation.decision_date.desc(),
                          m.PurchaseRecommendation.recommendation_id.desc()).limit(1))
            latest_replenishment_run=recent
        def bounds(model,column):
            lo,hi=db.execute(select(func.min(column),func.max(column))).one()
            return {'from_date':lo,'to_date':hi}
        persisted=db.execute(select(PersistenceMap.job_id,func.count()).where(
            PersistenceMap.entity_type=='PurchaseRecommendation').group_by(PersistenceMap.job_id)).all()
        persisted_counts=dict(persisted)
        replenishment_runs=[]
        for job in completed:
            if job.kind!='REPLENISH' or job.job_id not in persisted_counts:continue
            forecast_job_id=(job.result or {}).get('parameters',{}).get('forecast_job_id')
            parent=parents.get(forecast_job_id)
            source=(parent.result or {}).get('source_run_id') if parent else None
            if not source:continue
            replenishment_runs.append({'job_id':job.job_id,
                'run_id':(job.result or {}).get('source_run_id'),
                'forecast_run_id':source,'recommendation_count':persisted_counts[job.job_id],
                'status':job.status,'completed_at':job.finished_at})
        return {'forecast_runs':runs,'replenishment_runs':replenishment_runs,
                'latest_forecast_run_id':latest_source_run,
                'latest_replenishment_forecast_run_id':latest_replenishment_run,
                'coverage':{'sales':bounds(m.SalesHistory,m.SalesHistory.sales_date),
                            'inventory':bounds(m.InventorySnapshot,m.InventorySnapshot.snapshot_date),
                            'forecast':bounds(m.Forecast,m.Forecast.target_date),
                            'recommendations':bounds(m.PurchaseRecommendation,m.PurchaseRecommendation.decision_date)}}

    @router.get('/schema-metadata')
    def schema_metadata():
        """Describe actual SQLAlchemy schema, never invented column descriptions."""
        names=['sku','store','sales_history','inventory_snapshot','supplier',
               'supplier_sku','lead_time_observation','forecast','reorder_policy','purchase_recommendation']
        return {'entities':[{'name':name,'columns':[{'name':c.name,'type':str(c.type),
                 'required':not c.nullable,'primary_key':c.primary_key,
                 'foreign_keys':[str(k.target_fullname) for k in c.foreign_keys]}
                 for c in m.Base.metadata.tables[name].columns]}
                 for name in names]}

    @router.get('/demand-trend')
    def trend(sku_id:str|None=None,store_id:str|None=None,from_date:date|None=None,to_date:date|None=None,db:Session=Depends(db)):
        clauses=filter_date(m.SalesHistory.sales_date,from_date,to_date)
        if sku_id:clauses.append(m.SalesHistory.sku_id==sku_id)
        if store_id:clauses.append(m.SalesHistory.store_id==store_id)
        stmt=select(m.SalesHistory.sales_date,func.sum(m.SalesHistory.units_sold),func.sum(m.SalesHistory.stockout_flag.cast(__import__('sqlalchemy').Integer))).where(*clauses).group_by(m.SalesHistory.sales_date).order_by(m.SalesHistory.sales_date.desc()).limit(180)
        return [{'date':d.isoformat(),'units':int(s),'stockouts':int(o)} for d,s,o in reversed(db.execute(stmt).all())]

    @router.get('/forecast-trend')
    def forecast_trend(run_id:str|None=None,sku_id:str|None=None,store_id:str|None=None,
                       from_date:date|None=None,to_date:date|None=None,db:Session=Depends(db)):
        """Server-side daily totals; no hidden 500-observation chart sampling.

        Values are sums of forecast *observations*, not a single consolidated
        SKU-store demand curve when multiple forecast origins cover a target day.
        Every included date represents the same run/store/SKU/date scope as the table.
        """
        conditions=filter_date(m.Forecast.target_date,from_date,to_date)
        if run_id:conditions.append(m.Forecast.run_id==run_id)
        if sku_id:conditions.append(m.Forecast.sku_id==sku_id)
        if store_id:conditions.append(m.Forecast.store_id==store_id)
        # Date bound ensures response size is constant even with millions of pairs.
        forecast_rows=db.execute(select(m.Forecast.target_date,
            func.sum(m.Forecast.predicted_units),func.count())
            .where(*conditions).group_by(m.Forecast.target_date)
            .order_by(m.Forecast.target_date.desc()).limit(180)).all()
        dates=[r[0] for r in forecast_rows]
        if not dates:return {'items':[],'total_dates':0,'date_limit':180}
        sales_conditions=[m.SalesHistory.sales_date.in_(dates)]
        if sku_id:sales_conditions.append(m.SalesHistory.sku_id==sku_id)
        if store_id:sales_conditions.append(m.SalesHistory.store_id==store_id)
        observed={r[0]:(int(r[1] or 0),int(r[2] or 0)) for r in db.execute(
            select(m.SalesHistory.sales_date,func.sum(m.SalesHistory.units_sold),
                   func.sum(m.SalesHistory.stockout_flag.cast(__import__('sqlalchemy').Integer)))
            .where(*sales_conditions).group_by(m.SalesHistory.sales_date)).all()}
        return {'items':[{'date':d,'predicted':float(predicted or 0),
                 'forecast_observations':int(n),
                 'actual':observed[d][0] if d in observed else None,
                 'stockout_observations':observed[d][1] if d in observed else None}
                for d,predicted,n in reversed(forecast_rows)],
                'total_dates':len(dates),'date_limit':180}

    @router.get('/model-metrics')
    def metrics(job_id:str|None=None,db:Session=Depends(db)):
        query=select(PipelineRun).where(PipelineRun.kind=='FORECAST',PipelineRun.status=='SUCCEEDED')
        if job_id:query=query.where(PipelineRun.job_id==job_id)
        row=db.execute(query.order_by(PipelineRun.finished_at.desc()).limit(1)).scalar_one_or_none()
        if not row:return {'available':False,'reason':'No successful forecast job with persisted artifacts'}
        path=(cfg['artifacts_root']/row.job_id/'forecast'/'evaluation.json').resolve()
        if not path.is_relative_to(cfg['artifacts_root']) or not path.is_file():
            raise HTTPException(503,'Model evaluation artifact missing')
        from app.forecasting.verify import verify_run
        try:
            verify_run(path.parent)
            evaluation=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise HTTPException(503,'Model evaluation artifact failed integrity checks') from exc
        explanation=(cfg['artifacts_root']/row.job_id/'forecast'/'explainability.json').resolve()
        try:
            if not explanation.is_relative_to(path.parent):
                raise ValueError('unsafe optional artifact')
            details=json.loads(explanation.read_text(encoding='utf-8')) if explanation.exists() else None
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise HTTPException(503,'Model explainability artifact failed integrity checks') from exc
        return {'available':True,'job_id':row.job_id,'evaluation':evaluation,
                'explainability':details}

    @router.get('/audit-events',response_model=Page)
    def audit_events(kind:Literal['recommendation','supplier','inventory']='recommendation',entity_id:str|None=None,
                     limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        models={'recommendation':(m.RecommendationLifecycleEvent,'recommendation_id'),
                'supplier':(m.SupplierObservationEvent,'observation_id'),
                'inventory':(m.InventoryMovementEvent,'inventory_id')}
        model,col=models[kind]
        cond=[getattr(model,col)==entity_id] if entity_id else []
        return query_page(db,model,cond,limit,offset,model.occurred_at.desc())

    @router.get('/exceptions/unbound')
    def unbound_exceptions(db:Session=Depends(db)):
        jobs=db.execute(select(PipelineRun).where(PipelineRun.kind=='REPLENISH',PipelineRun.status=='SUCCEEDED')
                        .order_by(PipelineRun.finished_at.desc()).limit(20)).scalars().all()
        records=[]
        for job in jobs:
            if not job.artifact_path:continue
            path=(Path(job.artifact_path)/'unbound_exceptions.jsonl').resolve()
            if not path.is_relative_to(cfg['artifacts_root']) or not path.is_file():continue
            try:
                # Bound I/O and reject corrupt records instead of returning an
                # uncontrolled 500. The path has already been scope-checked.
                with path.open(encoding='utf-8') as source:
                    for i,line in enumerate(source):
                        if i>=100:break
                        item=json.loads(line)
                        if not isinstance(item,dict):raise ValueError('invalid exception event')
                        records.append({**item,'job_id':job.job_id})
            except (OSError,UnicodeError,ValueError,TypeError) as exc:
                raise HTTPException(503,'Unbound recommendation artifact failed integrity checks') from exc
        return {'items':records[:200],'total':len(records[:200])}

    @router.get('/jobs',response_model=Page)
    def jobs(limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0),db:Session=Depends(db)):
        return query_page(db,PipelineRun,[],limit,offset,PipelineRun.submitted_at.desc())

    @router.get('/jobs/{job_id}')
    def job_status(job_id:str,db:Session=Depends(db)):
        obj=db.get(PipelineRun,job_id)
        if obj is None:raise HTTPException(404,'Job not found')
        return as_dict(obj)

    @router.post('/jobs/{kind}',status_code=202)
    def submit(kind:Literal['import','forecast','replenish'],payload:JobRequest,
               idempotency_key:str|None=Header(None,alias='Idempotency-Key'), db:Session=Depends(db)):
        if kind=='import' and not cfg['allow_demo_import']:raise HTTPException(403,'Import disabled')
        if kind=='replenish' and not payload.forecast_job_id:raise HTTPException(422,'forecast_job_id required')
        if kind!='replenish' and payload.forecast_job_id is not None:raise HTTPException(422,'forecast_job_id is only valid for replenishment')
        if not idempotency_key:raise HTTPException(422,'Idempotency-Key header required')
        job_id,status,new=create_job(engine,kind.upper() if kind!='replenish' else 'REPLENISH',idempotency_key,payload.model_dump(),cfg)
        return {'job_id':job_id,'status':status,'created':new,'status_url':f'/api/v1/jobs/{job_id}'}

    api.include_router(router)
    return api


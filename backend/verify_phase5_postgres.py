"""Phase 5 trusted-local HTTP + PostgreSQL acceptance, SMALL dataset only.

Must be run only after explicit opted-in pg_dump and Alembic 0003. Creates
real technical job records and core fact/forecast/recommendation rows. No
full-scale generation, no schema drops, no hidden truth imports.
"""
from __future__ import annotations
import json,os,time,uuid
from pathlib import Path
from sqlalchemy import create_engine,text,select,func
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from app.integration.api import create_app
from app.integration.settings import settings,validate_settings
from app.integration.types import PipelineRun,PersistenceMap
from app.models import SKU,Forecast,PurchaseRecommendation,RecommendationLifecycleEvent,InventoryMovementEvent,SupplierSKU

def require(cond,msg):
    if not cond:raise AssertionError(msg)

def run():
    cfg=settings();validate_settings(cfg)
    require(cfg['allow_demo_import'],'YOTTA_ALLOW_IMPORT must be true for explicitly approved small dataset acceptance')
    require(cfg['data_root'].joinpath('manifest.json').is_file(),'validated small source dataset missing')
    from app.generation.config import GeneratorConfig
    c=GeneratorConfig.from_json(cfg['data_root']/'generator_config.json')
    require(c.sku_count <= 100 and c.store_count <= 10 and c.days <= 90,
            'REFUSE full-scale dataset in Phase 5 intermediate acceptance')
    engine=create_engine(cfg['database_url'],pool_pre_ping=True)
    with engine.connect() as conn:
        require(conn.dialect.name=='postgresql','PostgreSQL required')
        require(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0005','expected Alembic 0005')
        require(conn.execute(text('SELECT current_database()')).scalar_one()=='yottaflex','unexpected database')
    result={'environment':'candidate-local-postgresql','stage':'PHASE5','status':'FAIL',
            'full_scale_generation':'DEFERRED_NOT_EXECUTED','screenshots':'NOT_VERIFIED',
            'checks':[]}
    def ok(label,condition):
        require(condition,label)
        result['checks'].append({'check':label,'status':'PASS'})
        print('PASS |',label,flush=True)
    api=create_app(cfg,engine,init_on_start=False)
    headers={}
    with TestClient(api) as client:
        ok('local demo access without bearer',client.get('/api/v1/skus').status_code==200)
        ok('health',client.get('/health').status_code==200)
        def get(path):
            r=client.get('/api/v1/'+path,headers=headers)
            require(r.status_code==200,f'{path}: {r.status_code} {r.text[:150]}')
            return r.json()
        def job(kind,data):
            idem=f'phase5-{kind}-{uuid.uuid4()}'
            r=client.post('/api/v1/jobs/'+kind,json=data,headers={**headers,'Idempotency-Key':idem})
            require(r.status_code==202,f'{kind} submit failed: {r.status_code} {r.text[:200]}')
            job_id=r.json()['job_id']
            until=time.monotonic()+180
            while time.monotonic()<until:
                obj=get('jobs/'+job_id)
                if obj['status']=='SUCCEEDED':return obj
                if obj['status']=='FAILED':raise AssertionError(f'{kind} failed: {obj["error_code"]}: {obj["error_message"]}')
                time.sleep(0.4)
            raise AssertionError(f'{kind} timed out; job remains persisted for inspection')
        imported=job('import',{})
        ok('signed small dataset imported',get('overview')['sku_count']==c.sku_count)
        ok('all master and fact APIs',all(get(path)['total']>0 for path in
           ['skus','stores','suppliers','supplier-contracts','lead-times','policies','sales','inventory']))
        ok('date range paging',get('sales?from_date=2026-01-01&to_date=2026-01-05&limit=10')['limit']==10)
        ok('demand trend',bool(get('demand-trend')))
        fc=job('forecast',{})
        ok('forecast persistence',get('forecasts')['total']>0)
        metrics=get('model-metrics')
        ok('model evaluation and explainability',metrics.get('available') and bool(metrics.get('evaluation')) and bool(metrics.get('explainability')))
        rp=job('replenish',{'forecast_job_id':fc['job_id']})
        with Session(engine) as session:
            rec_ids=session.execute(select(PersistenceMap.database_id).where(
                PersistenceMap.job_id==rp['job_id'],PersistenceMap.entity_type=='PurchaseRecommendation')).scalars().all()
            # Even on a previous same-source import, source mappings are valid and
            # DB decisions must be retrievable; do not infer from inserted count.
            if not rec_ids:
                rec_ids=[x[0] for x in session.execute(select(PersistenceMap.database_id).where(PersistenceMap.entity_type=='PurchaseRecommendation'))]
            decs=[session.get(PurchaseRecommendation,rid) for rid in rec_ids]
            decs=[r for r in decs if r]
            ok('all 36 source decisions persisted',len(decs)==36)
            ok('positive 19, blocked 3',sum(r.recommended_quantity>0 for r in decs)==19 and sum(r.status=='BLOCKED' for r in decs)==3)
            ok('source-to-database idempotency map',session.scalar(select(func.count()).select_from(PersistenceMap))>=174)
            ok('append-only lifecycle journal',all(session.scalar(select(func.count()).select_from(RecommendationLifecycleEvent).where(
               RecommendationLifecycleEvent.recommendation_id==r.recommendation_id))>=1 for r in decs))
            ok('inventory movement audit records',session.scalar(select(func.count()).select_from(InventoryMovementEvent))>0)
            ok('blocked discontinued never positive',all(r.recommended_quantity==0 for r in decs if r.status=='BLOCKED'))
        page=get('recommendations?limit=50')
        ok('recommendations API plus supplier cost',page['total']>=36 and page['items'] and 'estimated_cost' in page['items'][0])
        detail=get('recommendations/'+decs[0].recommendation_id)
        ok('reason trace and supplier contract',detail['recommendation']['decision_trace'] is not None and detail['contract'] is not None)
        ok('exceptions API',get('exceptions')['total']>=3)
        ok('jobs API',get('jobs')['total']>=3)
        # Confirm a genuine database lifecycle event appended by the 0002 trigger.
        original=next((r for r in decs if r.status=='GENERATED'),None)
        require(original is not None,'missing generated decision')
        before=get('recommendations/'+original.recommendation_id)['events']
        patch=client.patch('/api/v1/recommendations/'+original.recommendation_id,
                           headers=headers,json={'status':'VALIDATED'})
        require(patch.status_code==200,patch.text)
        after=get('recommendations/'+original.recommendation_id)['events']
        ok('DB lifecycle transition appended event',len(after)==len(before)+1 and after[-1]['new_status']=='VALIDATED')
        invalid=client.patch('/api/v1/recommendations/'+original.recommendation_id,
                             headers=headers,json={'status':'VALIDATED'})
        ok('same-status retry idempotent',invalid.status_code==200 and len(get('recommendations/'+original.recommendation_id)['events'])==len(after))
        # PG UPDATE and DELETE on append-only journal must fail in isolated rollback.
        event_id=after[-1]['event_id']
        for verb in ('UPDATE','DELETE'):
            with engine.connect() as conn:
                trans=conn.begin()
                try:
                    if verb=='UPDATE':
                        conn.execute(text('UPDATE tech_recommendation_lifecycle_event SET actor=actor WHERE event_id=:id'),{'id':event_id})
                    else:
                        conn.execute(text('DELETE FROM tech_recommendation_lifecycle_event WHERE event_id=:id'),{'id':event_id})
                    raise AssertionError(f'{verb} append-only event incorrectly allowed')
                except DBAPIError as exc:
                    state=getattr(exc.orig,'sqlstate',None)
                    ok(f'append-only {verb} rejected',state in ('P0001','23514'))
                finally:trans.rollback()
    result.update(status='PASS',checks_passed=len(result['checks']),
                  run_ids={'import':imported['job_id'],'forecast':fc['job_id'],'replenish':rp['job_id']},
                  personal_ui_review='PENDING')
    return result

if __name__=='__main__':
    output=Path(os.getenv('YOTTA_PHASE5_REPORT','phase5_postgres_acceptance.json'))
    try:report=run()
    except Exception as exc:
        print('FAIL |',type(exc).__name__,str(exc)[:300],flush=True)
        report={'status':'FAIL','error':str(exc)[:300],'full_scale_generation':'DEFERRED_NOT_EXECUTED'}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print('OVERALL |',report['status'],flush=True)
    raise SystemExit(0 if report['status']=='PASS' else 2)

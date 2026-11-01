"""V8 read-scope regression; SQLite fixtures, never an actual PostgreSQL write.

Verifies SQL-side filters/pagination and chart aggregation rather than assuming
that a limited client page contains the full dataset.
"""
from __future__ import annotations
from datetime import date, datetime, timezone, timedelta
from sqlalchemy import select,func,event
from sqlalchemy.orm import Session
from app.models import SKU,SalesHistory,Forecast,PurchaseRecommendation,ReorderPolicy,SupplierSKU
from app.integration.types import PipelineRun,PersistenceMap
from app.integration.persistence import import_dataset
from test_phase5_integration import database,client,DATA


def test_stockout_filter_applies_before_limit_offset_and_count(client,database):
    with Session(database) as db,db.begin():
        import_dataset(db, DATA)
    with Session(database) as db:
        total=db.scalar(select(func.count()).select_from(SalesHistory).where(SalesHistory.stockout_flag.is_(True)))
        assert total>0
        first=db.execute(select(SalesHistory.sale_id).where(SalesHistory.stockout_flag.is_(True))
                         .order_by(SalesHistory.sales_date.desc(),SalesHistory.sale_id).limit(7)).scalars().all()
    response=client.get('/api/v1/sales',params={'stockout_only':'true','limit':7})
    assert response.status_code==200
    payload=response.json()
    assert payload['total']==total
    assert len(payload['items'])==7
    assert all(x['stockout_flag'] for x in payload['items'])
    assert [r['sale_id'] for r in payload['items']]==first
    page_2=client.get('/api/v1/sales',params={'stockout_only':True,'limit':7,'offset':7}).json()
    assert page_2['total']==total
    assert not set(x['sale_id'] for x in payload['items']) & set(x['sale_id'] for x in page_2['items'])
    assert client.get('/api/v1/sales',params={'stockout_only':True,'from_date':'2026-03-01','to_date':'2026-01-01'}).status_code==422


def test_blocked_exceptions_are_run_store_sku_date_scoped(client,database):
    with Session(database) as db,db.begin():
        import_dataset(db,DATA)
        p=db.scalars(select(ReorderPolicy).limit(1)).first()
        c=db.scalars(select(SupplierSKU).where(SupplierSKU.sku_id==p.sku_id).limit(1)).first()
        assert c
        common=dict(policy_id=p.policy_id,sku_id=p.sku_id,store_id=p.store_id,
                    supplier_sku_id=c.supplier_sku_id,inventory_date=date(2026,1,20),
                    inventory_position=2,lead_time_demand=6,raw_quantity=0,
                    recommended_quantity=0,status='BLOCKED',reason_codes=['TEST'],
                    rule_version='test',decision_trace={})
        selected_store,selected_sku=p.store_id,p.sku_id
        db.add_all([
            PurchaseRecommendation(recommendation_id='TEST-BLOCK-A',decision_date=date(2026,1,20),forecast_run_id='RUN-A',**common),
            PurchaseRecommendation(recommendation_id='TEST-BLOCK-B',decision_date=date(2026,1,21),forecast_run_id='RUN-B',**common)
        ])
    base='/api/v1/exceptions'
    assert client.get(base).json()['total']==2
    a=client.get(base,params={'forecast_run_id':'RUN-A','store_id':selected_store,'sku_id':selected_sku}).json()
    assert a['total']==1 and a['items'][0]['recommendation_id']=='TEST-BLOCK-A'
    assert client.get(base,params={'forecast_run_id':'RUN-B','to_date':'2026-01-20'}).json()['total']==0
    assert client.get(base,params={'replenishment_job_id':'JOB-NONEXISTENT'}).json()['total']==0
    assert client.get(base,params={'from_date':'2026-02-01','to_date':'2026-01-01'}).status_code==422


def test_forecast_trend_aggregates_all_matching_rows_not_first_page(client,database):
    with Session(database) as db,db.begin():
        import_dataset(db,DATA)
        # 60 forecasts on one day, across 12 SKU * 3 stores with two origins;
        # the endpoint must not silently collapse them to one SKU-store.
        pairs=db.execute(select(SalesHistory.sku_id,SalesHistory.store_id).distinct().limit(36)).all()
        for i in range(60):
            sku,store=pairs[i%len(pairs)]
            db.add(Forecast(forecast_id=f'TEST-TREND-{i}',sku_id=sku,store_id=store,
                forecast_origin=date(2026,1,15)+timedelta(days=i//36),
                target_date=date(2026,1,28),predicted_units=2,
                model_version='test',run_id='FC-RUN-TREND-A',status='VALIDATED'))
        sku_0=pairs[0][0]
        db.add(Forecast(forecast_id='TEST-TREND-OTHER',sku_id=pairs[0][0],store_id=pairs[0][1],
            forecast_origin=date(2026,1,15),target_date=date(2026,1,28),predicted_units=900,
            model_version='test',run_id='FC-RUN-TREND-B',status='VALIDATED'))
    res=client.get('/api/v1/forecast-trend',params={'run_id':'FC-RUN-TREND-A'}).json()
    assert res['total_dates']==1
    assert res['items'][0]['predicted']==120
    assert res['items'][0]['forecast_observations']==60
    assert res['items'][0]['date']=='2026-01-28'
    distinct=client.get('/api/v1/forecast-trend',params={'run_id':'FC-RUN-TREND-B'}).json()
    assert distinct['items'][0]['predicted']==900
    assert distinct['items'][0]['forecast_observations']==1
    filtered=client.get('/api/v1/forecast-trend',params={'run_id':'FC-RUN-TREND-A','sku_id':sku_0}).json()
    assert 0<filtered['items'][0]['forecast_observations']<60
    assert client.get('/api/v1/forecast-trend',params={'from_date':'2026-05-01','to_date':'2026-01-01'}).status_code==422


def test_sku_search_supports_category_and_stays_bounded(client,database):
    with Session(database) as db,db.begin():import_dataset(db,DATA)
    with Session(database) as db:
        sku=db.scalar(select(SKU).limit(1))
        assert sku and sku.category
    result=client.get('/api/v1/skus',params={'q':sku.category,'limit':5}).json()
    assert result['total']>=1
    assert any(row['sku_id']==sku.sku_id for row in result['items']) or result['total']>5
    assert client.get('/api/v1/skus',params={'q':'x'*81}).status_code==422


def test_run_catalog_parent_lookup_does_not_grow_linearly(client,database):
    with Session(database) as db,db.begin():
        for i in range(20):
            f=f'JOB-FORECAST-BATCH-{i:02d}'
            r=f'JOB-REPLENISH-BATCH-{i:02d}'
            db.add_all([
                PipelineRun(job_id=f,idempotency_key=f'forecast-batch-{i}',kind='FORECAST',status='SUCCEEDED',
                    finished_at=datetime(2026,1,1,tzinfo=timezone.utc),result={'source_run_id':f'FC-RUN-{i}','parameters':{}}),
                PipelineRun(job_id=r,idempotency_key=f'replenish-batch-{i}',kind='REPLENISH',status='SUCCEEDED',
                    finished_at=datetime(2026,1,1,tzinfo=timezone.utc),
                    result={'source_run_id':f'RP-RUN-{i}','parameters':{'forecast_job_id':f}})
            ])
    queries=[]
    def count_select(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
    event.listen(database,'before_cursor_execute',count_select)
    try:res=client.get('/api/v1/run-catalog')
    finally:event.remove(database,'before_cursor_execute',count_select)
    assert res.status_code==200
    assert len(queries)<=16, f'/run-catalog made {len(queries)} SELECTs for 20 parent runs'


def test_latest_forecast_selects_latest_completed_job_not_largest_target_date(client,database):
    with Session(database) as db,db.begin():
        import_dataset(db,DATA)
        old_target=date(2026,2,1)
        new_target=date(2026,1,28)
        for i,(run,target) in enumerate([('FC-RUN-OLD-JOB',old_target),('FC-RUN-NEW-JOB',new_target)]):
            db.add(Forecast(forecast_id=f'FC-LATEST-TEST-{i}',sku_id='SKU-000001',
                store_id='STORE-000001',forecast_origin=date(2026,1,20),
                target_date=target,predicted_units=5,model_version='test',
                run_id=run,status='VALIDATED'))
            db.add(PipelineRun(job_id=f'JOB-LATEST-TEST-{i}',idempotency_key=f'latest-run-key-{i}',
                kind='FORECAST',status='SUCCEEDED',
                finished_at=datetime(2026,2,i+1,tzinfo=timezone.utc),
                result={'source_run_id':run,'parameters':{}}))
    data=client.get('/api/v1/run-catalog').json()
    assert data['forecast_runs'][0]['run_id']=='FC-RUN-OLD-JOB'  # broader target coverage
    assert data['latest_forecast_run_id']=='FC-RUN-NEW-JOB'  # actual completion chronology
    assert data['forecast_runs'][0]['status']=='SUCCEEDED'
    assert data['forecast_runs'][1]['completed_at']


def test_catalog_parent_lookup_batches_missing_history_window(client,database):
    with Session(database) as db,db.begin():
        # Forecast parents are older than the 250 latest replenishment jobs.
        for i in range(15):
            db.add(PipelineRun(job_id=f'JOB-OLD-PARENT-{i}',idempotency_key=f'old-parent-{i}',
                kind='FORECAST',status='SUCCEEDED',
                finished_at=datetime(2026,1,1,tzinfo=timezone.utc),
                result={'source_run_id':f'FC-RUN-PARENT-{i}','parameters':{}}))
        for i in range(251):
            db.add(PipelineRun(job_id=f'JOB-NEW-REPLENISH-{i:03d}',idempotency_key=f'new-replenish-{i}',
                kind='REPLENISH',status='SUCCEEDED',
                finished_at=datetime(2026,2,1,tzinfo=timezone.utc),
                result={'source_run_id':f'RP-RUN-NEW-{i}',
                    'parameters':{'forecast_job_id':f'JOB-OLD-PARENT-{i%15}'}}))
    queries=[]
    def count_select(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
    event.listen(database,'before_cursor_execute',count_select)
    try:response=client.get('/api/v1/run-catalog')
    finally:event.remove(database,'before_cursor_execute',count_select)
    assert response.status_code==200
    assert len(queries)<=16,f'Batching regressed: {len(queries)} queries'


def test_readonly_postgres_schema_gate_checks_current_migration_without_writes():
    from verify_backend_schema_readonly import expected_lengths,verify
    class Result:
        def __init__(self,items):self.items=items
        def all(self):return self.items
    class Connection:
        dialect=type('Dialect',(),{'name':'postgresql'})()
        def __init__(self):self.queries=[]
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def scalar(self,sql):self.queries.append(str(sql));return '0005'
        def execute(self,sql):
            self.queries.append(str(sql))
            return Result([(table,column,length) for (table,column),length in expected_lengths().items()])
    class Engine:
        def __init__(self):self.connection=Connection()
        def connect(self):return self.connection
    engine=Engine()
    verify(engine)
    assert engine.connection.queries[0]=='SET TRANSACTION READ ONLY'
    assert all(q.startswith('SET TRANSACTION READ ONLY') or q.startswith('SELECT') or q.strip().startswith('SELECT')
               for q in engine.connection.queries)

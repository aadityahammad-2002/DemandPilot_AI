"""Bounded local job execution. States persisted; requests never fake success."""
from __future__ import annotations
import hashlib,json,threading,logging,re,shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from sqlalchemy import select,update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from app.integration.types import PipelineRun
from app.integration.persistence import import_dataset,import_forecasts,import_recommendations

_EXECUTOR=ThreadPoolExecutor(max_workers=1,thread_name_prefix='yotta-phase5')
_SUBMIT_LOCK=threading.Lock()
_LOG=logging.getLogger(__name__)

def utc():return datetime.now(timezone.utc)

def job_identity(kind,key):
    return 'JOB-'+hashlib.sha256((kind+':'+key).encode()).hexdigest()[:28].upper()

def _existing_request(engine, key, job_id, kind, parameters):
    with Session(engine) as db:
        existing=db.execute(select(PipelineRun).where(PipelineRun.idempotency_key==key)).scalar_one_or_none()
        if existing is None:
            return None
        if (existing.job_id != job_id or existing.kind != kind
                or (existing.result or {}).get('parameters') != parameters):
            raise ValueError('idempotency key already belongs to a different request')
        return existing.job_id, existing.status, False


def create_job(engine,kind,key,parameters,settings,submit=True):
    if kind not in ('IMPORT','FORECAST','REPLENISH'):raise ValueError('unsupported job kind')
    if not isinstance(key,str) or not 8<=len(key)<=128:
        raise ValueError('Idempotency-Key header must be between 8 and 128 characters')
    job_id=job_identity(kind,key)
    with _SUBMIT_LOCK:
        previous=_existing_request(engine,key,job_id,kind,parameters)
        if previous is not None:
            return previous
        try:
            with Session(engine) as db, db.begin():
                db.add(PipelineRun(job_id=job_id,idempotency_key=key,kind=kind,status='QUEUED',
                                   result={'parameters':parameters}))
        except IntegrityError:
            # The UNIQUE constraint is authoritative across independent API processes.
            # Never schedule another run if another process won the race.
            prior=_existing_request(engine,key,job_id,kind,parameters)
            if prior is not None:return prior
            raise
    if submit:
        try:
            _EXECUTOR.submit(execute_job,engine,job_id,kind,parameters,settings)
        except RuntimeError:
            _set_state(engine,job_id,'FAILED',expected='QUEUED',finished_at=utc(),
                       error_code='WORKER_UNAVAILABLE',error_message='Job worker unavailable; submit with a new key')
            return job_id,'FAILED',True
    return job_id,'QUEUED',True


def _set_state(engine,job_id,status,*,expected=None,**changes):
    # Atomic claim and compare-and-swap protect against duplicate worker execution,
    # including requests arriving through different Python processes.
    where=[PipelineRun.job_id==job_id]
    if expected is not None:where.append(PipelineRun.status==expected)
    with Session(engine) as db,db.begin():
        changed=db.execute(update(PipelineRun).where(*where).values(status=status,**changes))
        return changed.rowcount==1


def _public_error(exc):
    """Only known-safe descriptions may enter the employee-visible job ledger."""
    if isinstance(exc,SQLAlchemyError):
        # SQLSTATE is a public standardized diagnostic, never a SQL statement,
        # bound parameter or DSN. 22001 means string data right truncation.
        state=getattr(getattr(exc,'orig',None),'sqlstate',None)
        code='PERSISTENCE_'+state if isinstance(state,str) and re.fullmatch(r'[A-Z0-9]{5}',state) else 'PERSISTENCE_ERROR'
        return code,'Database write failed; no partial job results committed'
    if isinstance(exc,FileNotFoundError):
        return 'ARTIFACT_MISSING','Required input or output artifact was not found'
    if isinstance(exc,PermissionError):
        return 'ARTIFACT_ACCESS','Job could not access an input or artifact'
    if isinstance(exc,ValueError):
        # Structured validation errors may contain input rows or local paths.
        # Surface a stable category rather than arbitrary exception text.
        return 'VALIDATION_ERROR','Job input, artifact or business constraint validation failed'
    return 'EXECUTION_ERROR','Execution failed; inspect local server logs'


def _commit_success(db,job_id,result,source,folder):
    # Atomic with core-row inserts and persistence mappings, so a crash cannot
    # claim FAILED while keeping an uncommitted/successfully imported batch.
    with open(source,'rb') as f:source_digest=hashlib.file_digest(f,'sha256').hexdigest()
    item=db.get(PipelineRun,job_id,with_for_update=True)
    if item is None or item.status!='RUNNING':raise RuntimeError('job is no longer RUNNING')
    item.status='SUCCEEDED';item.finished_at=utc();item.source_digest=source_digest
    item.artifact_path=str(folder);item.result={'parameters':(item.result or {}).get('parameters',{}),**result}


def execute_job(engine,job_id,kind,parameters,settings):
    owned_artifact_dir=None
    try:
        if not _set_state(engine,job_id,'RUNNING',expected='QUEUED',started_at=utc()):
            return False
        data=settings['data_root']
        artifacts=settings['artifacts_root']
        artifacts.mkdir(parents=True,exist_ok=True)
        if kind=='IMPORT':
            if not settings['allow_demo_import']:
                raise ValueError('initial data import disabled by YOTTA_ALLOW_IMPORT')
            source=data/'manifest.json'
            with Session(engine) as db,db.begin():
                result=import_dataset(db,data)
                _commit_success(db,job_id,result,source,data)
        elif kind=='FORECAST':
            from app.forecasting.config import ForecastConfig
            from app.forecasting.engine import forecast
            from app.forecasting.verify import verify_run
            folder=artifacts/job_id/'forecast'
            staging=artifacts/job_id/'forecast.staging'
            if folder.exists() or staging.exists():
                raise ValueError('artifact destination/staging exists; refusing overwrite')
            # Keep incomplete output in an attempt-owned staging leaf. The final
            # path appears only after the artifact verifier accepts all files.
            staging.mkdir(parents=True,exist_ok=False)
            owned_artifact_dir=staging
            cfg=ForecastConfig(max_pairs=settings['max_pairs'])
            forecast(data,staging,cfg)
            verify_run(staging)
            staging.rename(folder)  # Same-parent atomic directory publication.
            owned_artifact_dir=None  # Preserve published artifact even when DB commit acknowledgement is lost.
            source=folder/'forecast_manifest.json'
            with Session(engine) as db,db.begin():
                result=import_forecasts(db,folder,job_id)
                _commit_success(db,job_id,result,source,folder)
        else:
            from app.replenishment.engine import run
            from app.replenishment.policy import DecisionConfig
            from app.replenishment.verify import verify
            fc_job=parameters.get('forecast_job_id','')
            with Session(engine) as db:
                old=db.get(PipelineRun,fc_job)
                if old is None or old.kind!='FORECAST' or old.status!='SUCCEEDED':
                    raise ValueError('replenishment requires a SUCCEEDED forecast job')
            forecast_folder=artifacts/fc_job/'forecast'
            if not (forecast_folder/'forecast_manifest.json').is_file():
                raise ValueError('forecast artifacts missing')
            folder=artifacts/job_id/'replenishment'
            staging=artifacts/job_id/'replenishment.staging'
            if folder.exists() or staging.exists():
                raise ValueError('artifact destination/staging exists; refusing overwrite')
            staging.mkdir(parents=True,exist_ok=False)
            owned_artifact_dir=staging
            cfg=DecisionConfig(max_pairs=settings['max_pairs'])
            run(data,forecast_folder,staging,cfg)
            verify(staging)
            staging.rename(folder)
            owned_artifact_dir=None  # Never delete published verified output on DB failure.
            source=folder/'replenishment_manifest.json'
            with Session(engine) as db,db.begin():
                result=import_recommendations(db,folder,job_id)
                _commit_success(db,job_id,result,source,folder)
    except Exception as exc:
        # A failed job can leave a partially written filesystem artifact even
        # if DB inserts roll back. Never reuse it or mistake it for success.
        if owned_artifact_dir is not None:
            try:
                root=settings['artifacts_root'].resolve()
                owned=owned_artifact_dir.resolve()
                if owned.parent.parent==root and owned.is_dir() and not owned.is_symlink():
                    shutil.rmtree(owned)
                    # Remove the now-empty job directory only; never siblings.
                    try: owned.parent.rmdir()
                    except OSError: pass
            except (OSError,ValueError):
                _LOG.error('Job artifact cleanup failed: job_id=%s',job_id)
        code,message=_public_error(exc)
        # Server logs deliberately exclude untrusted input, DSNs and SQL parameters.
        _LOG.error('Pipeline execution failed: job_id=%s category=%s exception=%s',
                   job_id,code,type(exc).__name__)
        try:
            _set_state(engine,job_id,'FAILED',expected='RUNNING',finished_at=utc(),
                       error_code=code,error_message=message)
        except SQLAlchemyError:
            # A database outage may prevent recording failure. Reconciliation
            # after a guarded restart remains the recovery mechanism.
            _LOG.error('Job failure state could not be persisted: job_id=%s',job_id)
        return False
    return True

def recover_interrupted(engine):
    # Called ONLY while holding the exclusive PostgreSQL API instance lock.
    # If the server died, its in-memory worker pool no longer exists.
    with Session(engine) as db,db.begin():
        db.execute(update(PipelineRun).where(PipelineRun.status.in_(['RUNNING','QUEUED']))
                   .values(status='FAILED',finished_at=utc(),error_code='INTERRUPTED',
                           error_message='Server restarted before job completion; submit with a new key'))

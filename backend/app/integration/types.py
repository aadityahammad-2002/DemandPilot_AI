"""Technical database tables separate from exactly ten official business entities."""
from sqlalchemy import String, DateTime, Text, JSON, CheckConstraint, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from datetime import datetime
from sqlalchemy.orm import DeclarativeBase

class IntegrationBase(DeclarativeBase):
    """Separate technical metadata; preserve Phase 1 Base with exactly 13 tables."""
    pass

class PipelineRun(IntegrationBase):
    __tablename__='tech_pipeline_run'
    job_id:Mapped[str]=mapped_column(String(64),primary_key=True)
    idempotency_key:Mapped[str]=mapped_column(String(128),unique=True,nullable=False)
    kind:Mapped[str]=mapped_column(String(24),nullable=False)
    status:Mapped[str]=mapped_column(String(16),nullable=False)
    submitted_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.current_timestamp(),nullable=False)
    started_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    finished_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    source_digest:Mapped[str|None]=mapped_column(String(64))
    artifact_path:Mapped[str|None]=mapped_column(Text)
    error_code:Mapped[str|None]=mapped_column(String(60))
    error_message:Mapped[str|None]=mapped_column(Text)
    result:Mapped[dict|None]=mapped_column(JSON)
    __table_args__=(CheckConstraint("kind IN ('FORECAST','REPLENISH','IMPORT')",name='tech_pipeline_run_kind'),CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",name='tech_pipeline_run_status'),Index('idx_pipeline_runs_time','submitted_at'))

class PersistenceMap(IntegrationBase):
    __tablename__='tech_persistence_map'
    source_key:Mapped[str]=mapped_column(String(200),primary_key=True)
    source_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    entity_type:Mapped[str]=mapped_column(String(32),nullable=False)
    database_id:Mapped[str]=mapped_column(String(110),nullable=False)
    job_id:Mapped[str]=mapped_column(ForeignKey('tech_pipeline_run.job_id'),nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.current_timestamp(),nullable=False)
    __table_args__=(UniqueConstraint('entity_type','database_id',name='uq_tech_persistence_map_entity_id'),CheckConstraint("entity_type IN ('Forecast','PurchaseRecommendation')",name='tech_persistence_map_entity'),Index('idx_persistence_map_job','job_id'))

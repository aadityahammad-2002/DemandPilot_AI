"""Phase 5 supporting run/idempotency ledger. Exactly ten core entities remain unchanged.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('tech_pipeline_run',
        sa.Column('job_id',sa.String(64),primary_key=True),
        sa.Column('idempotency_key',sa.String(128),nullable=False,unique=True),
        sa.Column('kind',sa.String(24),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('submitted_at',sa.DateTime(timezone=True),server_default=sa.text('CURRENT_TIMESTAMP'),nullable=False),
        sa.Column('started_at',sa.DateTime(timezone=True)),
        sa.Column('finished_at',sa.DateTime(timezone=True)),
        sa.Column('source_digest',sa.String(64)),
        sa.Column('artifact_path',sa.Text()),
        sa.Column('error_code',sa.String(60)),
        sa.Column('error_message',sa.Text()),
        sa.Column('result',sa.JSON()),
        sa.CheckConstraint("kind IN ('FORECAST','REPLENISH','IMPORT')",name='ck_tech_pipeline_run_kind'),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",name='ck_tech_pipeline_run_status'))
    op.create_index('idx_pipeline_runs_time','tech_pipeline_run',['submitted_at'])
    op.create_table('tech_persistence_map',
        sa.Column('source_key',sa.String(200),primary_key=True),
        sa.Column('source_hash',sa.String(64),nullable=False),
        sa.Column('entity_type',sa.String(20),nullable=False),
        sa.Column('database_id',sa.String(110),nullable=False),
        sa.Column('job_id',sa.String(64),sa.ForeignKey('tech_pipeline_run.job_id'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.text('CURRENT_TIMESTAMP'),nullable=False),
        sa.UniqueConstraint('entity_type','database_id',name='uq_tech_persistence_map_entity_id'),
        sa.CheckConstraint("entity_type IN ('Forecast','PurchaseRecommendation')",name='ck_tech_persistence_map_entity'))
    op.create_index('idx_persistence_map_job','tech_persistence_map',['job_id'])

def downgrade():
    # Downgrade is deliberately non-destructive if audit mappings exist.
    bind=op.get_bind()
    for name in ('tech_persistence_map','tech_pipeline_run'):
        n=bind.execute(sa.text('SELECT COUNT(*) FROM '+name)).scalar_one()
        if n: raise RuntimeError('Cannot downgrade Phase 5 while audit ledger has data; export and obtain approval')
    op.drop_index('idx_persistence_map_job',table_name='tech_persistence_map')
    op.drop_table('tech_persistence_map')
    op.drop_index('idx_pipeline_runs_time',table_name='tech_pipeline_run')
    op.drop_table('tech_pipeline_run')

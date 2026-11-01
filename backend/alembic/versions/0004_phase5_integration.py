"""Expand Phase 5 entity discriminator length.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column(
        "tech_persistence_map",
        "entity_type",
        existing_type=sa.String(20),
        type_=sa.String(32),
        existing_nullable=False,
    )

def downgrade():
    bind = op.get_bind()
    n = bind.execute(sa.text(
        "SELECT count(*) FROM tech_persistence_map "
        "WHERE length(entity_type) > 20"
    )).scalar_one()
    if n:
        raise RuntimeError("Existing values prevent safe downgrade")
    op.alter_column(
        "tech_persistence_map",
        "entity_type",
        existing_type=sa.String(32),
        type_=sa.String(20),
        existing_nullable=False,
    )

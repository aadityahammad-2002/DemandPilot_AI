"""Initial 10 business entities and 3 supporting append-only journals.
Revision ID: 0001
Revises:
"""
from alembic import op
from app.models import Base
revision='0001'
down_revision=None
branch_labels=None
depends_on=None

EVENT_TABLES=['tech_inventory_movement_event','tech_supplier_observation_event','tech_recommendation_lifecycle_event']

def upgrade():
    bind=op.get_bind()
    # metadata sorted by FK dependencies, including support tables
    for table in Base.metadata.sorted_tables:
        table.create(bind, checkfirst=False)
    if bind.dialect.name == 'postgresql':
        op.execute('''CREATE FUNCTION yottaflex_reject_event_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append-only event history: % forbidden', TG_OP; END; $$''')
        for table in EVENT_TABLES:
            op.execute(f'''CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table}
                         FOR EACH ROW EXECUTE FUNCTION yottaflex_reject_event_mutation()''')

def downgrade():
    bind=op.get_bind()
    for table in reversed(Base.metadata.sorted_tables): table.drop(bind,checkfirst=True)
    if bind.dialect.name=='postgresql': op.execute('DROP FUNCTION IF EXISTS yottaflex_reject_event_mutation()')

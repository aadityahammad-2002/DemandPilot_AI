"""Permit date-effective historical SKU decisions without accepting retired orders.

Revision ID: 0005
Revises: 0004

No table or historical record is modified. PostgreSQL trigger implementation
replaces only the 0002 recommendation-eligibility function. The SKU mutation
trigger from 0002 already enforces discontinue-date conflicts and is retained.
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def _postgres_only():
    if op.get_bind().dialect.name != 'postgresql':
        raise RuntimeError('SKU eligibility trigger migration requires PostgreSQL')


def upgrade():
    _postgres_only()
    op.execute("""CREATE OR REPLACE FUNCTION yottaflex_guard_recommendation_eligibility() RETURNS trigger
      LANGUAGE plpgsql AS $$
      DECLARE product_status text; launching date; ending date;
      BEGIN
        SELECT status, launch_date, discontinue_date
          INTO product_status, launching, ending
          FROM sku WHERE sku_id = NEW.sku_id FOR SHARE;
        IF NOT FOUND THEN RETURN NEW; END IF; -- Existing foreign key handles unknown SKU.
        -- The master status can be the eventual state for historic imports;
        -- the half-open [launch, discontinue) dates govern historic eligibility.
        IF NEW.decision_date < launching OR product_status = 'PLANNED' THEN
          IF NEW.recommended_quantity <> 0 OR NEW.status <> 'BLOCKED' THEN
            RAISE EXCEPTION 'SKU outside launch eligibility requires BLOCKED quantity 0'
              USING ERRCODE='P0001';
          END IF;
        END IF;
        IF ending IS NOT NULL AND NEW.decision_date >= ending THEN
          IF NEW.status <> 'BLOCKED' OR NEW.recommended_quantity <> 0 OR
             NOT (NEW.reason_codes::jsonb ? 'DISCONTINUED_SKU') THEN
            RAISE EXCEPTION 'discontinued SKU requires BLOCKED, quantity 0, DISCONTINUED_SKU reason'
              USING ERRCODE='P0001';
          END IF;
        END IF;
        RETURN NEW;
      END $$""")


def downgrade():
    _postgres_only()
    # Downgrading would re-introduce the historical-status defect; refuse if
    # legitimate positive historical decisions would become unmodifiable.
    count = op.get_bind().execute(sa.text("""
      SELECT count(*) FROM purchase_recommendation pr
      JOIN sku s ON s.sku_id=pr.sku_id
      WHERE s.status='DISCONTINUED' AND pr.recommended_quantity>0
        AND pr.decision_date < s.discontinue_date
    """)).scalar_one()
    if count:
        raise RuntimeError('Unsafe downgrade: positive historical recommendations exist for retired SKUs')
    op.execute("""CREATE OR REPLACE FUNCTION yottaflex_guard_recommendation_eligibility() RETURNS trigger
      LANGUAGE plpgsql AS $$
      DECLARE product_status text; ending date;
      BEGIN
        SELECT status, discontinue_date INTO product_status, ending
          FROM sku WHERE sku_id=NEW.sku_id FOR SHARE;
        IF NOT FOUND THEN RETURN NEW; END IF;
        IF product_status='DISCONTINUED' OR
           (ending IS NOT NULL AND NEW.decision_date >= ending) THEN
          IF NEW.status <> 'BLOCKED' OR NEW.recommended_quantity <> 0 OR
             NOT (NEW.reason_codes::jsonb ? 'DISCONTINUED_SKU') THEN
            RAISE EXCEPTION 'discontinued SKU requires BLOCKED, quantity 0, DISCONTINUED_SKU reason'
              USING ERRCODE='P0001';
          END IF;
        END IF;
        RETURN NEW;
      END $$""")

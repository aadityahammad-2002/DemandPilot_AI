"""Exactly ten core business models. Three append-only technical event tables are separate."""
from datetime import date, datetime
from sqlalchemy import (MetaData, String, Integer, BigInteger, Date, DateTime, Numeric, Float,
  ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index, Boolean, JSON, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

CONVENTION = {'ix':'ix_%(column_0_label)s', 'uq':'uq_%(table_name)s_%(column_0_name)s',
              'ck':'ck_%(table_name)s_%(constraint_name)s','fk':'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
              'pk':'pk_%(table_name)s'}
class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENTION)

class SKU(Base):
    __tablename__='sku'
    sku_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    unit: Mapped[str] = mapped_column(String(12), nullable=False, default='each')
    launch_date: Mapped[date] = mapped_column(Date, nullable=False)
    discontinue_date: Mapped[date|None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    base_demand_rate: Mapped[float] = mapped_column(Float, nullable=False)
    storage_units_per_item: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __table_args__=(CheckConstraint("status IN ('PLANNED','ACTIVE','DISCONTINUED')",name='sku_status'),
                    CheckConstraint("unit IN ('each','case','kg','litre')",name='sku_unit'),
                    CheckConstraint('base_demand_rate >= 0',name='sku_demand'),
                    CheckConstraint('storage_units_per_item > 0',name='sku_storage'),
                    CheckConstraint('discontinue_date IS NULL OR discontinue_date >= launch_date',name='sku_dates'))
class Store(Base):
    __tablename__='store'
    store_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    region: Mapped[str] = mapped_column(String(60),nullable=False)
    timezone: Mapped[str] = mapped_column(String(64),nullable=False,default='Asia/Kolkata')
    open_date: Mapped[date] = mapped_column(Date,nullable=False)
    close_date: Mapped[date|None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16),nullable=False)
    capacity_units: Mapped[int] = mapped_column(Integer,nullable=False)
    __table_args__=(CheckConstraint("status IN ('PLANNED','OPEN','CLOSED')",name='store_status'),
                    CheckConstraint('capacity_units >= 0',name='store_capacity'),
                    CheckConstraint('close_date IS NULL OR close_date >= open_date',name='store_dates'))
class Supplier(Base):
    __tablename__='supplier'
    supplier_id: Mapped[str] = mapped_column(String(10),primary_key=True)
    supplier_name: Mapped[str] = mapped_column(String(120),nullable=False)
    status: Mapped[str] = mapped_column(String(16),nullable=False)
    effective_from: Mapped[date] = mapped_column(Date,nullable=False)
    effective_to: Mapped[date|None] = mapped_column(Date)
    reliability_factor: Mapped[float] = mapped_column(Float,nullable=False)
    __table_args__=(CheckConstraint("status IN ('ACTIVE','SUSPENDED','INACTIVE')",name='supplier_status'),
                    CheckConstraint('reliability_factor >= 0 AND reliability_factor <= 1',name='supplier_reliability'),
                    CheckConstraint('effective_to IS NULL OR effective_to > effective_from',name='supplier_dates'))
class SupplierSKU(Base):
    __tablename__='supplier_sku'
    supplier_sku_id: Mapped[str] = mapped_column(String(12),primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey('supplier.supplier_id'),nullable=False)
    sku_id: Mapped[str] = mapped_column(ForeignKey('sku.sku_id'),nullable=False)
    valid_from: Mapped[date] = mapped_column(Date,nullable=False)
    valid_to: Mapped[date|None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16),nullable=False)
    base_lead_time_days: Mapped[int] = mapped_column(Integer,nullable=False)
    moq_units: Mapped[int] = mapped_column(Integer,nullable=False)
    pack_size_units: Mapped[int] = mapped_column(Integer,nullable=False)
    unit_cost: Mapped[float] = mapped_column(Numeric(14,2),nullable=False)
    supplier_capacity_units: Mapped[int] = mapped_column(Integer,nullable=False)
    __table_args__=(UniqueConstraint('supplier_sku_id','sku_id',name='uq_contract_sku'),
                    UniqueConstraint('supplier_id','sku_id','valid_from',name='uq_contract_effective'),
                    CheckConstraint("status IN ('PENDING','ACTIVE','EXPIRED','TERMINATED')",name='contract_status'),
                    CheckConstraint('valid_to IS NULL OR valid_to > valid_from',name='contract_dates'),
                    CheckConstraint('base_lead_time_days > 0',name='contract_lead'),
                    CheckConstraint('moq_units > 0 AND pack_size_units > 0',name='contract_pack_moq'),
                    CheckConstraint('unit_cost >= 0 AND supplier_capacity_units >= 0',name='contract_cost_capacity'))
class SalesHistory(Base):
    __tablename__='sales_history'
    sale_id: Mapped[str] = mapped_column(String(64),primary_key=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey('sku.sku_id'),nullable=False)
    store_id: Mapped[str] = mapped_column(ForeignKey('store.store_id'),nullable=False)
    sales_date: Mapped[date] = mapped_column(Date,nullable=False)
    units_sold: Mapped[int] = mapped_column(Integer,nullable=False)
    selling_price: Mapped[float] = mapped_column(Numeric(14,2),nullable=False)
    promotion_flag: Mapped[bool] = mapped_column(Boolean,nullable=False,default=False)
    promotion_multiplier: Mapped[float] = mapped_column(Float,nullable=False,default=1.0)
    stockout_flag: Mapped[bool] = mapped_column(Boolean,nullable=False,default=False)
    observed_available_units: Mapped[int] = mapped_column(Integer,nullable=False)
    __table_args__=(UniqueConstraint('sku_id','store_id','sales_date',name='uq_sales_grain'),
                    CheckConstraint('units_sold >= 0 AND observed_available_units >= 0',name='sales_nonnegative'),
                    CheckConstraint('units_sold <= observed_available_units',name='sales_availability'),
                    CheckConstraint('selling_price >= 0 AND promotion_multiplier >= 0',name='sales_price_mult'),
                    Index('idx_sales_date_store','sales_date','store_id'))
class InventorySnapshot(Base):
    __tablename__='inventory_snapshot'
    inventory_id: Mapped[str] = mapped_column(String(64),primary_key=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey('sku.sku_id'),nullable=False)
    store_id: Mapped[str] = mapped_column(ForeignKey('store.store_id'),nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date,nullable=False)
    opening_stock: Mapped[int] = mapped_column(Integer,nullable=False)
    received_units: Mapped[int] = mapped_column(Integer,nullable=False)
    sold_units: Mapped[int] = mapped_column(Integer,nullable=False)
    adjustment_units: Mapped[int] = mapped_column(Integer,nullable=False,default=0)
    closing_stock: Mapped[int] = mapped_column(Integer,nullable=False)
    reserved_units: Mapped[int] = mapped_column(Integer,nullable=False,default=0)
    __table_args__=(UniqueConstraint('sku_id','store_id','snapshot_date',name='uq_inventory_grain'),
                    CheckConstraint('opening_stock >= 0 AND received_units >= 0 AND sold_units >= 0 AND closing_stock >= 0 AND reserved_units >= 0',name='inventory_nonnegative'),
                    CheckConstraint('closing_stock = opening_stock + received_units - sold_units + adjustment_units',name='inventory_balance'),
                    CheckConstraint('reserved_units <= closing_stock',name='inventory_reserves'),
                    Index('idx_inventory_date_store','snapshot_date','store_id'))
class LeadTimeObservation(Base):
    __tablename__='lead_time_observation'
    observation_id: Mapped[str] = mapped_column(String(16),primary_key=True)
    supplier_sku_id: Mapped[str] = mapped_column(ForeignKey('supplier_sku.supplier_sku_id'),nullable=False)
    order_date: Mapped[date] = mapped_column(Date,nullable=False)
    expected_receipt_date: Mapped[date] = mapped_column(Date,nullable=False)
    actual_receipt_date: Mapped[date|None] = mapped_column(Date)
    observed_lead_time_days: Mapped[int|None] = mapped_column(Integer)
    delay_days: Mapped[int|None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16),nullable=False)
    __table_args__=(CheckConstraint("status IN ('PLACED','DELAYED','RECEIVED','CANCELLED')",name='lead_status'),
                    CheckConstraint('expected_receipt_date >= order_date',name='lead_expected'),
                    CheckConstraint('actual_receipt_date IS NULL OR actual_receipt_date >= order_date',name='lead_actual'),
                    CheckConstraint('observed_lead_time_days IS NULL OR observed_lead_time_days >= 0',name='lead_nonnegative'),
                    CheckConstraint("(status = 'RECEIVED' AND actual_receipt_date IS NOT NULL AND observed_lead_time_days = actual_receipt_date - order_date AND delay_days = actual_receipt_date - expected_receipt_date) OR (status <> 'RECEIVED' AND actual_receipt_date IS NULL AND observed_lead_time_days IS NULL AND delay_days IS NULL)",name='lead_derived'),
                    Index('idx_lead_contract_date','supplier_sku_id','order_date'))
class Forecast(Base):
    __tablename__='forecast'
    forecast_id: Mapped[str] = mapped_column(String(110),primary_key=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey('sku.sku_id'),nullable=False)
    store_id: Mapped[str] = mapped_column(ForeignKey('store.store_id'),nullable=False)
    forecast_origin: Mapped[date] = mapped_column(Date,nullable=False)
    target_date: Mapped[date] = mapped_column(Date,nullable=False)
    predicted_units: Mapped[float] = mapped_column(Float,nullable=False)
    model_version: Mapped[str] = mapped_column(String(60),nullable=False)
    run_id: Mapped[str] = mapped_column(String(80),nullable=False)
    status: Mapped[str] = mapped_column(String(16),nullable=False)
    __table_args__=(UniqueConstraint('run_id','model_version','sku_id','store_id','forecast_origin','target_date',name='uq_forecast_grain'),
                    CheckConstraint('target_date > forecast_origin',name='forecast_future'),
                    CheckConstraint('predicted_units >= 0',name='forecast_nonnegative'),
                    CheckConstraint("status IN ('GENERATED','VALIDATED','PUBLISHED','REJECTED')",name='forecast_status'),
                    Index('idx_forecast_run_target','run_id','target_date'))
class ReorderPolicy(Base):
    __tablename__='reorder_policy'
    policy_id: Mapped[str] = mapped_column(String(16),primary_key=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey('sku.sku_id'),nullable=False)
    store_id: Mapped[str] = mapped_column(ForeignKey('store.store_id'),nullable=False)
    supplier_sku_id: Mapped[str] = mapped_column(String(12),nullable=False)
    effective_from: Mapped[date] = mapped_column(Date,nullable=False)
    effective_to: Mapped[date|None] = mapped_column(Date)
    review_period_days: Mapped[int] = mapped_column(Integer,nullable=False)
    service_level: Mapped[float] = mapped_column(Numeric(5,4),nullable=False)
    safety_stock_units: Mapped[int] = mapped_column(Integer,nullable=False)
    capacity_limit_units: Mapped[int] = mapped_column(Integer,nullable=False)
    status: Mapped[str] = mapped_column(String(16),nullable=False)
    __table_args__=(ForeignKeyConstraint(['supplier_sku_id','sku_id'],['supplier_sku.supplier_sku_id','supplier_sku.sku_id']),
                    UniqueConstraint('sku_id','store_id','effective_from',name='uq_policy_effective'),
                    CheckConstraint('effective_to IS NULL OR effective_to > effective_from',name='policy_dates'),
                    CheckConstraint('review_period_days > 0 AND safety_stock_units >= 0 AND capacity_limit_units >= 0',name='policy_quantities'),
                    CheckConstraint('service_level > 0 AND service_level < 1',name='policy_service'),
                    CheckConstraint("status IN ('DRAFT','ACTIVE','SUPERSEDED','DISABLED')",name='policy_status'))
class PurchaseRecommendation(Base):
    __tablename__='purchase_recommendation'
    recommendation_id: Mapped[str] = mapped_column(String(24),primary_key=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey('reorder_policy.policy_id'),nullable=False)
    sku_id: Mapped[str] = mapped_column(ForeignKey('sku.sku_id'),nullable=False)
    store_id: Mapped[str] = mapped_column(ForeignKey('store.store_id'),nullable=False)
    supplier_sku_id: Mapped[str] = mapped_column(String(12),nullable=False)
    decision_date: Mapped[date] = mapped_column(Date,nullable=False)
    forecast_run_id: Mapped[str] = mapped_column(String(80),nullable=False)
    inventory_date: Mapped[date] = mapped_column(Date,nullable=False)
    inventory_position: Mapped[int] = mapped_column(Integer,nullable=False)
    lead_time_demand: Mapped[float] = mapped_column(Float,nullable=False)
    raw_quantity: Mapped[int] = mapped_column(Integer,nullable=False)
    recommended_quantity: Mapped[int] = mapped_column(Integer,nullable=False)
    status: Mapped[str] = mapped_column(String(16),nullable=False)
    reason_codes: Mapped[list] = mapped_column(JSON,nullable=False)
    rule_version: Mapped[str] = mapped_column(String(60),nullable=False)
    decision_trace: Mapped[dict] = mapped_column(JSON,nullable=False)
    __table_args__=(ForeignKeyConstraint(['supplier_sku_id','sku_id'],['supplier_sku.supplier_sku_id','supplier_sku.sku_id']),
                    CheckConstraint('inventory_date <= decision_date',name='rec_inventory_date'),
                    CheckConstraint('lead_time_demand >= 0 AND raw_quantity >= 0 AND recommended_quantity >= 0',name='rec_quantities'),
                    CheckConstraint("status IN ('GENERATED','VALIDATED','ACCEPTED','REJECTED','EXPIRED','BLOCKED')",name='rec_status'),
                    Index('idx_rec_decision_store','decision_date','store_id'))

# Supporting technical event journals: these are NOT additional core entities.
class InventoryMovementEvent(Base):
    __tablename__='tech_inventory_movement_event'
    event_id: Mapped[str] = mapped_column(String(100),primary_key=True)
    inventory_id: Mapped[str] = mapped_column(ForeignKey('inventory_snapshot.inventory_id'),nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    movement_type: Mapped[str] = mapped_column(String(24),nullable=False)
    quantity_delta: Mapped[int] = mapped_column(Integer,nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(100),nullable=False)
    payload: Mapped[dict] = mapped_column(JSON,nullable=False)
    __table_args__=(CheckConstraint("movement_type IN ('OPENING','RECEIPT','SALE','ADJUSTMENT','RESERVATION','RELEASE')",name='inv_event_type'),)
class SupplierObservationEvent(Base):
    __tablename__='tech_supplier_observation_event'
    event_id: Mapped[str] = mapped_column(String(100),primary_key=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey('lead_time_observation.observation_id'),nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    previous_status: Mapped[str|None] = mapped_column(String(16))
    new_status: Mapped[str] = mapped_column(String(16),nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(100),nullable=False)
    payload: Mapped[dict] = mapped_column(JSON,nullable=False)
class RecommendationLifecycleEvent(Base):
    __tablename__='tech_recommendation_lifecycle_event'
    event_id: Mapped[str] = mapped_column(String(100),primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey('purchase_recommendation.recommendation_id'),nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    previous_status: Mapped[str|None] = mapped_column(String(16))
    new_status: Mapped[str] = mapped_column(String(16),nullable=False)
    actor: Mapped[str] = mapped_column(String(100),nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(100),nullable=False)
    payload: Mapped[dict] = mapped_column(JSON,nullable=False)

CORE_ENTITIES = (SKU, Store, SalesHistory, InventorySnapshot, Supplier, SupplierSKU,
                 LeadTimeObservation, Forecast, ReorderPolicy, PurchaseRecommendation)
TECH_ENTITIES = (InventoryMovementEvent, SupplierObservationEvent, RecommendationLifecycleEvent)

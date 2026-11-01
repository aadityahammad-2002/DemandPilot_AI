"""Machine readable dictionary; every field has explicit metadata and constraints."""
import json
from pathlib import Path
from sqlalchemy import inspect
from app.models import CORE_ENTITIES, TECH_ENTITIES
from app.postgres_contracts import ID_CHECK_NAMES, EXCLUSION_NAMES, IMMUTABLE_CORE_ID_COLUMNS, JOURNALS
from app.lifecycle import TRANSITIONS

UNITS={
 'days':'days','date':'ISO-8601 calendar date','quantity':'units','money':'INR / unit',
 'ratio':'dimensionless','timestamp':'UTC','id':'identifier','text':'text','flag':'boolean',
 'json':'JSON object/array','timezone':'IANA timezone'}
FIELD_UNIT={
 'sku_id':'id','store_id':'id','supplier_id':'id','supplier_sku_id':'id','policy_id':'id',
 'sale_id':'id','inventory_id':'id','observation_id':'id','forecast_id':'id','recommendation_id':'id',
 'event_id':'id','correlation_id':'id','run_id':'id','forecast_run_id':'id','model_version':'text',
 'rule_version':'text','effective_from':'date','effective_to':'date','valid_from':'date','valid_to':'date',
 'launch_date':'date','discontinue_date':'date','open_date':'date','close_date':'date',
 'sales_date':'date','snapshot_date':'date','order_date':'date','expected_receipt_date':'date',
 'actual_receipt_date':'date','forecast_origin':'date','target_date':'date',
 'decision_date':'date','inventory_date':'date','occurred_at':'timestamp',
 'base_lead_time_days':'days','observed_lead_time_days':'days','delay_days':'days','review_period_days':'days',
 'base_demand_rate':'units/day','promotion_multiplier':'ratio','reliability_factor':'ratio',
 'service_level':'ratio','selling_price':'money','unit_cost':'money',
 'promotion_flag':'flag','stockout_flag':'flag','reason_codes':'json','decision_trace':'json','payload':'json',
 'storage_units_per_item':'storage units/item','status':'text','timezone':'timezone'}
VALID_ENUMS={
 'SKU.status':['PLANNED','ACTIVE','DISCONTINUED'], 'SKU.unit':['each','case','kg','litre'],
 'Store.status':['PLANNED','OPEN','CLOSED'], 'Supplier.status':['ACTIVE','SUSPENDED','INACTIVE'],
 'SupplierSKU.status':['PENDING','ACTIVE','EXPIRED','TERMINATED'],
 'LeadTimeObservation.status':['PLACED','DELAYED','RECEIVED','CANCELLED'],
 'Forecast.status':['GENERATED','VALIDATED','PUBLISHED','REJECTED'],
 'ReorderPolicy.status':['DRAFT','ACTIVE','SUPERSEDED','DISABLED'],
 'PurchaseRecommendation.status':['GENERATED','VALIDATED','ACCEPTED','REJECTED','EXPIRED','BLOCKED'],
 'InventoryMovementEvent.movement_type':['OPENING','RECEIPT','SALE','ADJUSTMENT','RESERVATION','RELEASE']}
ID_FORMS={'SKU.sku_id':'SKU-000001','Store.store_id':'STORE-000001',
 'SalesHistory.sale_id':'SALE-STORE-000001-SKU-000001-2026-01-01',
 'Forecast.forecast_id':'FC-STORE-000001-SKU-000001-2026-01',
 'PurchaseRecommendation.recommendation_id':'PO-2026-000001'}
DESCRIPTIONS={
 'sku_id':'Product identity','store_id':'Store identity','supplier_id':'Supplier identity',
 'supplier_sku_id':'Supplier-to-product contract identity','status':'Lifecycle state',
 'sale_id':'Deterministic SKU/store/day sale identity','inventory_id':'Deterministic SKU/store/day snapshot identity',
 'forecast_id':'Deterministic store/SKU/month forecast identity plus version/sequence suffix',
 'recommendation_id':'Deterministic year/ordinal decision identity',
 'reason_codes':'Machine-readable reason codes','decision_trace':'Versioned inputs and applied rules',
 'observed_available_units':'Available units at sale observation; sales may not exceed this',
 'adjustment_units':'Signed shrinkage/correction units','reserved_units':'Part of closing inventory not available for fulfillment',
 'delay_days':'Actual receipt date minus expected receipt date (may be negative for early deliveries)',
 'observed_lead_time_days':'Actual receipt date minus order date, calculated not sampled',
 'predicted_units':'Nonnegative expected future demand',
 'inventory_position':'On-hand plus documented pipeline minus commitments; may be negative',
 'payload':'Immutable event payload with source/version metadata'}

def build():
    result={'schema_version':'2.0-phase1-revision-0002','core_entity_count':10,'core_entities':{},'supporting_technical_artifacts':{},
            'date_intervals':'[start,end) unless otherwise stated','currency':'INR (design assumption)',
            'status':'revision-0002 design contract; live PostgreSQL acceptance pending'}
    for cls in CORE_ENTITIES+TECH_ENTITIES:
        model=inspect(cls)
        fields=[]
        table=cls.__table__
        for col in table.columns:
            typ=str(col.type)
            if col.name.endswith('_units') or col.name in {'units_sold','opening_stock','closing_stock','received_units','sold_units','adjustment_units','raw_quantity','recommended_quantity','inventory_position','quantity_delta','capacity_limit_units','safety_stock_units'}: unit='quantity'
            else: unit=FIELD_UNIT.get(col.name, 'text' if 'CHAR' in typ or 'VARCHAR' in typ else 'dimensionless')
            enums=VALID_ENUMS.get(f'{cls.__name__}.{col.name}')
            foreign=[str(f.target_fullname) for f in col.foreign_keys]
            constraints=[str(c.sqltext) for c in table.constraints if hasattr(c,'sqltext') and col.name in str(c.sqltext)]
            if ID_CHECK_NAMES.get(table.name) and col.name == IMMUTABLE_CORE_ID_COLUMNS.get(table.name):
                constraints.append('PostgreSQL revision 0002 CHECK: ' + ID_CHECK_NAMES[table.name])
                constraints.append('PostgreSQL revision 0002 identity immutable after INSERT')
            if table.name == 'purchase_recommendation' and col.name in {'status','recommended_quantity','reason_codes'}:
                constraints.append('PostgreSQL revision 0002: discontinued SKU requires BLOCKED, quantity 0, DISCONTINUED_SKU reason')
            if table.name in EXCLUSION_NAMES and col.name in {'valid_from','valid_to','effective_from','effective_to','supplier_id','sku_id','store_id'}:
                constraints.append('PostgreSQL revision 0002 exclusion constraint: ' + EXCLUSION_NAMES[table.name])
            fields.append({'field':col.name,'type':typ,'required':not col.nullable,
              'primary_key':col.primary_key,'unique':bool(col.unique or col.primary_key),
              'unique_scopes':[','.join(c.columns.keys()) for c in table.constraints if c.__class__.__name__=='UniqueConstraint' and col.name in c.columns],
              'foreign_keys':foreign,'allowed_values':enums if enums else 'per type and validation rules',
              'units':unit,'default':str(col.default.arg) if col.default is not None else None,
              'description':DESCRIPTIONS.get(col.name, col.name.replace('_',' ').capitalize()),
              'validation_rules':constraints or ['Type/nullable/FK constraints; see entity constraints'],
              'example':ID_FORMS.get(f'{cls.__name__}.{col.name}')})
        name='core_entities' if cls in CORE_ENTITIES else 'supporting_technical_artifacts'
        result[name][cls.__name__]={'table':table.name,'fields':fields,
          'constraints':[str(c) for c in table.constraints],
          'indexes':[str(i) for i in table.indexes],
          'postgresql_revision_0002': {
            'id_check':ID_CHECK_NAMES.get(table.name),
            'effective_date_exclusion':EXCLUSION_NAMES.get(table.name),
            'lifecycle_transitions':sorted([a+'->'+b for a,b in TRANSITIONS.get(table.name,())]),
            'technical_event_journal':table.name in JOURNALS
          }}
    return result

if __name__=='__main__':
    dest=Path(__file__).resolve().parents[2]/'docs'/'data_dictionary.json'
    dest.write_text(json.dumps(build(),indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')
    print(dest)

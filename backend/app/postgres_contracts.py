"""PostgreSQL-only v0002 enforcement catalog (not Base.metadata DDL).

IMPORTANT: Alembic revision 0001 builds tables from Base.metadata at runtime.
Adding revision-0002 checks/exclusions to Base.metadata would make a clean
0001 -> 0002 install create these constraints twice (and attempt GiST before
btree_gist is installed). Therefore 0002 DDL lives ONLY in the immutable
versioned migration; this catalog mirrors its names for application/QA
introspection. SQLAlchemy models remain authoritative for column/FK shapes.
"""
ID_CHECK_NAMES = {
    'sku': 'ck_sku_id_format',
    'store': 'ck_store_id_format',
    'supplier': 'ck_supplier_id_format',
    'supplier_sku': 'ck_supplier_sku_id_format',
    'lead_time_observation': 'ck_lead_time_observation_id_format',
    'reorder_policy': 'ck_reorder_policy_id_format',
    'sales_history': 'ck_sales_history_id_format',
    'inventory_snapshot': 'ck_inventory_snapshot_id_format',
    'forecast': 'ck_forecast_id_format',
    'purchase_recommendation': 'ck_purchase_recommendation_id_format',
}
EXCLUSION_NAMES = {
    'supplier_sku': 'ex_supplier_sku_period',
    'reorder_policy': 'ex_reorder_policy_active_period',
}
IMMUTABLE_CORE_ID_COLUMNS = {
    'sku': 'sku_id', 'store': 'store_id', 'supplier': 'supplier_id',
    'supplier_sku': 'supplier_sku_id', 'sales_history': 'sale_id',
    'inventory_snapshot': 'inventory_id', 'lead_time_observation': 'observation_id',
    'forecast': 'forecast_id', 'reorder_policy': 'policy_id',
    'purchase_recommendation': 'recommendation_id',
}
# The v0001 three journal UPDATE/DELETE protections remain unchanged.
JOURNALS = (
    'tech_inventory_movement_event',
    'tech_supplier_observation_event',
    'tech_recommendation_lifecycle_event',
)

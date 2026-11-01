"""Versioned application view of the 0002 database status-transition contract.

Database triggers are authoritative. Callers can use this mapping to preview a
transition, but must still handle PostgreSQL rejection on concurrent updates.
"""
TRANSITIONS = {
    'sku': frozenset({('PLANNED','ACTIVE'),('ACTIVE','DISCONTINUED')}),
    'store': frozenset({('PLANNED','OPEN'),('OPEN','CLOSED')}),
    'supplier': frozenset({('ACTIVE','SUSPENDED'),('SUSPENDED','ACTIVE'),('ACTIVE','INACTIVE'),('SUSPENDED','INACTIVE')}),
    'supplier_sku': frozenset({('PENDING','ACTIVE'),('PENDING','TERMINATED'),('ACTIVE','EXPIRED'),('ACTIVE','TERMINATED')}),
    'lead_time_observation': frozenset({('PLACED','DELAYED'),('PLACED','RECEIVED'),('PLACED','CANCELLED'),('DELAYED','RECEIVED'),('DELAYED','CANCELLED')}),
    'forecast': frozenset({('GENERATED','VALIDATED'),('GENERATED','REJECTED'),('VALIDATED','PUBLISHED'),('VALIDATED','REJECTED')}),
    'reorder_policy': frozenset({('DRAFT','ACTIVE'),('DRAFT','DISABLED'),('ACTIVE','SUPERSEDED'),('ACTIVE','DISABLED')}),
    'purchase_recommendation': frozenset({('GENERATED','VALIDATED'),('GENERATED','REJECTED'),('GENERATED','BLOCKED'),('VALIDATED','ACCEPTED'),('VALIDATED','REJECTED'),('VALIDATED','EXPIRED'),('VALIDATED','BLOCKED')}),
}

def can_transition(table: str, previous: str, next_state: str) -> bool:
    return previous == next_state or (previous, next_state) in TRANSITIONS.get(table, ())

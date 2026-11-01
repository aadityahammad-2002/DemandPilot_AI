"""Small, transparent scenario evaluator; NOT an observed business savings estimate.

Demand is supplied explicitly; order plans are integer quantities dispatched at the
specified day and arrive after a deterministic lead time. This intentionally does
not infer actual future demand from historical observed sales during stockouts.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class ScenarioCosts:
    holding_per_unit_day: float = 0.0
    shortage_per_unit: float = 0.0
    ordering_per_order: float = 0.0

    def validate(self):
        if any(not isfinite(x) or x < 0 for x in
               (self.holding_per_unit_day,self.shortage_per_unit,self.ordering_per_order)):
            raise ValueError('costs must be nonnegative finite scenario assumptions')


def simulate(daily_demand: list[int], *, initial_on_hand: int, lead_days: int,
             order_plan: dict[int,int], costs: ScenarioCosts = ScenarioCosts()) -> dict:
    """One deterministic demand-and-supply replay; day indices begin at zero.

    The arrival happens before sales for that day; late arrivals outside the
    evaluation window are counted as purchased but never available for filling.
    """
    costs.validate()
    if not daily_demand or any(type(x) is not int or x<0 for x in daily_demand):
        raise ValueError('daily demand must contain nonnegative integer units')
    if type(initial_on_hand) is not int or initial_on_hand<0 or type(lead_days) is not int or lead_days<0:
        raise ValueError('initial stock and lead days must be nonnegative integers')
    if any(type(d) is not int or d<0 or d>=len(daily_demand) or
           type(q) is not int or q<0 for d,q in order_plan.items()):
        raise ValueError('order plan must be nonnegative integer day/quantity pairs within window')
    scheduled={}
    for day,qty in order_plan.items():
        scheduled[day+lead_days]=scheduled.get(day+lead_days,0)+qty
    available=initial_on_hand
    fulfilled=lost=inventory_days=stockout_days=0
    for day,requested in enumerate(daily_demand):
        available+=scheduled.get(day,0)
        supplied=min(available,requested)
        available-=supplied
        fulfilled+=supplied
        lost+=requested-supplied
        stockout_days+=int(supplied<requested)
        inventory_days+=available
    ordered=sum(order_plan.values())
    orders=sum(q>0 for q in order_plan.values())
    return {'scenario_only':True,'days':len(daily_demand),
            'demand_units':sum(daily_demand),'fulfilled_units':fulfilled,
            'lost_units':lost,'fill_rate':round(fulfilled/sum(daily_demand),6)
                 if sum(daily_demand) else 1.0,
            'stockout_days':stockout_days,'stockout_day_fraction':round(stockout_days/len(daily_demand),6),
            'ending_on_hand':available,'average_ending_inventory':round(inventory_days/len(daily_demand),6),
            'purchased_units':ordered,'order_count':orders,
            'holding_cost':round(inventory_days*costs.holding_per_unit_day,6),
            'shortage_cost':round(lost*costs.shortage_per_unit,6),
            'ordering_cost':round(orders*costs.ordering_per_order,6),
            'total_simulated_cost':round(inventory_days*costs.holding_per_unit_day+
                       lost*costs.shortage_per_unit+orders*costs.ordering_per_order,6),
            'assumptions':{'lead_days':lead_days,'holding_per_unit_day':costs.holding_per_unit_day,
                           'shortage_per_unit':costs.shortage_per_unit,
                           'ordering_per_order':costs.ordering_per_order,
                           'unfilled_demand':'lost, not backordered',
                           'arrival_timing':'start of receipt day'}}


def compare_policies(daily_demand: list[int], *, initial_on_hand: int,
                     lead_days: int, baseline_orders: dict[int,int],
                     candidate_orders: dict[int,int], costs: ScenarioCosts=ScenarioCosts()):
    before=simulate(daily_demand,initial_on_hand=initial_on_hand,lead_days=lead_days,
                    order_plan=baseline_orders,costs=costs)
    after=simulate(daily_demand,initial_on_hand=initial_on_hand,lead_days=lead_days,
                   order_plan=candidate_orders,costs=costs)
    return {'scenario_only':True,'baseline':before,'candidate':after,
            'delta_simulated_cost':round(after['total_simulated_cost']-before['total_simulated_cost'],6),
            'delta_lost_units':after['lost_units']-before['lost_units']}

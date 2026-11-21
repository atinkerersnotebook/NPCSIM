"""Resident-facing daily planning, replanning and auditable rationale traces."""

from typing import TYPE_CHECKING, Any

from npc_sim import village

if TYPE_CHECKING:
    from npc_sim.world import World

VERB = "village_daily_plan"


def actions() -> list[dict[str, Any]]:
    return [{"id": VERB, "description": "Plan today and tomorrow from real orders, local stock, forecast demand, meals and travel timing.",
             "targets": [], "arguments_schema": {"type": "object", "properties": {}, "additionalProperties": False},
             "availability": "available", "reason": None}]


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    if action != VERB or args:
        raise ValueError("Daily planning takes no arguments")
    plan = reconcile(world, "resident action", force=True)
    if plan is None:
        return "REJECTED", "ASLEEP", "Wake before planning the production day.", None
    return "APPLIED", "DAY_PLANNED", (f"Planned Days {plan['day'] + 1}-{plan['day'] + 2}: "
                                       f"{len(plan['requirements'])} production requirements and "
                                       f"{len(plan['orders'])} commitments reviewed."), None


def _orders(world: "World", resident: str) -> list[dict[str, Any]]:
    economy = village.context(world)[1].economy
    if not economy:
        return []
    return [order.model_dump(mode="json") for order in economy.orders.values()
            if order.status not in {"cancelled", "expired", "declined", "fulfilled"}
            and resident in {order.buyer, order.supplier}]


def _requirements(world: "World", resident: str, days: int = 2) -> list[dict[str, Any]]:
    from npc_sim.town_supply import report
    snapshot = report(world.state, world.scenario.village)
    waterfall = snapshot.get("waterfall", {}).get("requirements", []) if snapshot else []
    return [row for row in waterfall if row["worker"] == resident
            and world.state.village.minute // 1440 + 1 <= row["day"] <= world.state.village.minute // 1440 + days]


def reconcile(world: "World", trigger: str, *, force: bool = False) -> dict[str, Any] | None:
    config, state = village.context(world)
    rid = world.actor_id
    person = state.residents[rid]
    if person.life and person.life.sleeping:
        return None
    day = state.minute // 1440
    today = [item for item in person.fallback.daily_plans if item["day"] == day]
    orders = _orders(world, rid)
    requirements = _requirements(world, rid, days=2)
    signature = (trigger, len(orders), sum(item.get("planned_output", 0) for item in requirements),
                 ",".join(sorted(f"{item['id']}:{item['status']}" for item in orders)))
    if today and not force and today[-1].get("signature") == signature:
        return today[-1]
    resident = config.residents[rid]
    blocked = [item for item in requirements if item["status"] == "blocked"]
    customer_orders = [item for item in orders if item["supplier"] == rid]
    purchase_orders = [item for item in orders if item["buyer"] == rid]
    assumptions = [
        "Upstream suppliers can initially provide the quantities required by today's forecast; confirmation is still required.",
        "Only real stock, accepted orders, physical travel and completed production count as fulfilled.",
        "Unconfirmed supply remains provisional and must not be reported as delivered.",
    ]
    calculations = [
        f"Wake/workday: {resident.wake_minute // 60:02}:{resident.wake_minute % 60:02} to the configured production close.",
        f"Own production requirements: {len(requirements)} standing or demand-driven outputs; {sum(item['planned_output'] for item in requirements)} units planned.",
        f"Customer commitments: {len(customer_orders)}; supplier orders/purchases: {len(purchase_orders)}; blocked own requirements: {len(blocked)}.",
    ]
    actions = []
    confirmations = []
    alternatives = []
    for order in customer_orders:
        actions.append(f"Check {order['product']} commitment #{order['id']} before promising or making more.")
        confirmed = order["status"] not in {"proposed", "blocked"}
        actions.append(("Confirmed" if confirmed else "Unconfirmed") +
                       f" {order['product']} promise #{order['id']}; notify the buyer if the quantity or deadline cannot be met.")
    for order in purchase_orders:
        confirmations.append({"order_id": order["id"], "supplier": order["supplier"],
                              "product": order["product"], "status": order["status"]})
        actions.append(f"Confirm {order['quantity']} {order['product']} with {order['supplier']} by "
                       f"a real conversation or changed physical notice before relying on it.")
    for requirement in requirements:
        needed = ", ".join(f"{quantity} {product}" for product, quantity in requirement["inputs"].items()) or "no raw inputs"
        actions.append(f"Produce {requirement['planned_output']} {requirement['product']} at {requirement['site']}; upstream need: {needed}.")
    for item in blocked:
        actions.append(f"Replan because {item['product']} is blocked: {', '.join(item['basis'] for _ in [0])}.")
        for product, quantity in item["inputs"].items():
            supplier = config.economy.suppliers.get(product) if config.economy else None
            backups = config.economy.supplier_alternatives.get(product, []) if config.economy else []
            alternatives.append({"for_product": item["product"], "input": product,
                                 "quantity": quantity, "primary": supplier, "alternatives": backups})
    if not actions:
        actions.append("No production deficit is forecast; perform daily obligations and avoid unnecessary output.")
    plan = {"day": day, "created_at": state.minute, "trigger": trigger, "signature": signature,
            "assumptions": assumptions, "calculations": calculations, "actions": actions,
            "requirements": requirements, "orders": orders, "planning_days": 2,
            "confirmations": confirmations, "alternatives": alternatives,
            "needs_customer_adjustment": bool(blocked and customer_orders),
            "supplier_confirmations_pending": sum(item["status"] == "proposed" for item in confirmations)}
    person.fallback.daily_plans.append(plan)
    del person.fallback.daily_plans[:-7]
    summary = (f"Planned Day {day + 1} at wake: reviewed {len(customer_orders)} customer order(s), "
               f"{len(purchase_orders)} supplier order(s), and {len(requirements)} production requirements. "
               f"Assumed upstream supply initially, then scheduled confirmation and customer-adjustment checks.")
    details = {**plan, "actor_id": rid, "world_time": state.minute, "known_source": "personal orders, forecasts and local stock"}
    index = village.event(world, rid, "daily_plan", resident.work, summary)
    state.events[index].details = details
    person.memories.append(index)
    return plan


def replan_after_supply(world: "World", order_id: str, result: str) -> dict[str, Any] | None:
    trigger = f"supplier {result.lower()} for {order_id}"
    plan = reconcile(world, trigger, force=True)
    if plan and result.lower() in {"rejected", "declined", "blocked", "failed"}:
        config, state = village.context(world)
        resident = config.residents[world.actor_id]
        affected = [order for order in plan["orders"]
                    if order["supplier"] == world.actor_id and order["status"] not in {"fulfilled", "cancelled"}]
        if affected:
            summary = (f"Customer adjustment required after upstream {result.lower()} for {order_id}. "
                       f"Review {len(affected)} affected promise(s) and communicate a quantity or deadline change through a physical channel.")
            index = village.event(world, world.actor_id, "customer_adjustment_required",
                                  resident.work, summary)
            state.events[index].details = {"order_id": order_id, "result": result,
                                           "affected_orders": affected,
                                           "actor_id": world.actor_id, "world_time": state.minute}
    return plan

from typing import TYPE_CHECKING, Any

from npc_sim import village, village_goods
from npc_sim.models import VillageWarehouseCartLoad

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"village_drive_cart", "village_load_cart", "village_unload_cart"}


def enabled(world: "World") -> bool:
    return bool(world.scenario.village and world.scenario.village.warehouse)


def config(world: "World"):
    value = world.scenario.village
    if value is None or value.warehouse is None:
        raise ValueError("Warehouse configuration is required")
    return value.warehouse


def observation(world: "World") -> dict[str, Any]:
    warehouse, state = config(world), world.state.village
    assert state is not None
    return {"cart": warehouse.cart, "cart_location": world.state.entities[warehouse.cart].location,
            "cart_driver": state.warehouse_cart_driver, "cart_capacity": warehouse.cart_capacity,
            "cart_load": sum(item.quantity for item in state.warehouse_cart_loads),
            "loads": [item.model_dump(mode="json") for item in state.warehouse_cart_loads],
            "tasks": [{**task.model_dump(mode="json"),
                       "moved": state.warehouse_task_progress.get(task.id, 0)} for task in warehouse.tasks],
            "stock_rules": {location: {product: {**rule.model_dump(),
                "quantity": state.stocks.get(location, {}).get(product, 0)}
                for product, rule in rules.items()} for location, rules in warehouse.stock_rules.items()}}


def actions(world: "World") -> list[dict[str, Any]]:
    if not enabled(world) or world.actor_id not in config(world).operators:
        return []
    warehouse, state = config(world), world.state.village
    assert state is not None
    cart_here = world.state.entities[warehouse.cart].location == world.actor.location_id
    result: list[dict[str, Any]] = []
    if cart_here:
        result.append({
            "id": "village_drive_cart", "description": "Take the warehouse cart or leave it parked here.",
            "targets": [warehouse.cart], "arguments_schema": {"type": "object", "properties": {
                "drive": {"type": "boolean"}}, "required": ["drive"], "additionalProperties": False},
            "availability": "available", "reason": None})
        tasks = [task for task in warehouse.tasks
                 if task.source == world.actor.location_id
                 and state.warehouse_task_progress.get(task.id, 0) < task.quantity]
        if state.warehouse_cart_driver == world.actor_id and tasks:
            result.append({
                "id": "village_load_cart", "description": "Load assigned stock onto the warehouse cart.",
                "targets": [warehouse.cart], "arguments_schema": {"type": "object", "properties": {
                    "task": {"type": "string", "enum": [task.id for task in tasks]},
                    "quantity": {"type": "integer", "minimum": 1, "maximum": warehouse.cart_capacity}},
                    "required": ["task", "quantity"], "additionalProperties": False},
                "availability": "available", "reason": None})
        if state.warehouse_cart_loads:
            result.append({
                "id": "village_unload_cart", "description": "Unload all cart stock assigned to this location.",
                "targets": [warehouse.cart], "arguments_schema": {"type": "object", "properties": {},
                    "required": [], "additionalProperties": False},
                "availability": "available", "reason": None})
    return result


def _reject(code: str, text: str) -> tuple[str, str, str, None]:
    return "REJECTED", code, text, None


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    warehouse, state = config(world), world.state.village
    assert state is not None
    if world.actor_id not in warehouse.operators:
        return _reject("CART_OPERATOR_REQUIRED", "Only assigned warehouse operators may use the cart.")
    cart = world.state.entities[warehouse.cart]
    if cart.location != world.actor.location_id:
        return _reject("CART_NOT_HERE", "Walk to the physical warehouse cart first.")
    if action == "village_drive_cart":
        if args["drive"]:
            if state.warehouse_cart_driver not in {None, world.actor_id}:
                return _reject("CART_IN_USE", "Another operator is driving the cart.")
            state.warehouse_cart_driver = world.actor_id
        elif state.warehouse_cart_driver == world.actor_id:
            state.warehouse_cart_driver = None
        else:
            return _reject("CART_DRIVER_REQUIRED", "Only its current driver can park the cart.")
        state.residents[world.actor_id].activity = "Driving the warehouse cart." if args["drive"] else "Parked the warehouse cart."
        village.event(world, world.actor_id, action, warehouse.cart, state.residents[world.actor_id].activity,
                      world.actor.location_id)
        return "APPLIED", "WAREHOUSE_CART_DRIVEN", state.residents[world.actor_id].activity, None
    if state.warehouse_cart_driver != world.actor_id:
        return _reject("CART_DRIVER_REQUIRED", "Take the warehouse cart before loading or unloading it.")
    if action == "village_load_cart":
        task = next((item for item in warehouse.tasks if item.id == args["task"]), None)
        if task is None or task.source != world.actor.location_id:
            return _reject("INVALID_CART_TASK", "Choose a warehouse task at its physical source location.")
        remaining = task.quantity - state.warehouse_task_progress.get(task.id, 0) - sum(
            item.quantity for item in state.warehouse_cart_loads if item.task_id == task.id)
        quantity = args["quantity"]
        aboard = sum(item.quantity for item in state.warehouse_cart_loads)
        if quantity > remaining or aboard + quantity > warehouse.cart_capacity:
            return _reject("CART_CAPACITY", "The requested quantity exceeds the task remainder or cart capacity.")
        stock = state.stocks.get(task.source, {})
        rule = warehouse.stock_rules.get(task.source, {}).get(task.product)
        if stock.get(task.product, 0) < quantity:
            return _reject("STOCK_SHORTAGE", "The source location does not have enough real stock.")
        if rule and stock[task.product] - quantity < rule.minimum and task.kind == "pick":
            return _reject("MINIMUM_STOCK", "A stocking rule protects the source minimum; do not pick below it.")
        lots = village_goods.take(world, task.source, task.product, quantity, kind="loaded")
        state.warehouse_cart_loads.append(VillageWarehouseCartLoad(task_id=task.id, product=task.product,
            quantity=quantity, source=task.source, destination=task.destination, lots=lots))
        village.event(world, world.actor_id, action, task.id,
                      f"Loaded {quantity} {task.product} for warehouse task {task.id}.", world.actor.location_id)
        return "APPLIED", "WAREHOUSE_CART_LOADED", f"Loaded {quantity} {task.product} for {task.id}.", None
    deliveries = [item for item in state.warehouse_cart_loads if item.destination == world.actor.location_id]
    if not deliveries:
        return _reject("NO_WAREHOUSE_CARGO", "No cart cargo is assigned to this location.")
    for product in {item.product for item in deliveries}:
        quantity = sum(item.quantity for item in deliveries if item.product == product)
        rule = warehouse.stock_rules.get(world.actor.location_id, {}).get(product)
        current = state.stocks.get(world.actor.location_id, {}).get(product, 0)
        if rule and current + quantity > rule.maximum:
            return _reject("MAXIMUM_STOCK", "The destination stocking maximum would be exceeded.")
    for item in deliveries:
        village_goods.put(world, item.destination, item.product, item.quantity, item.lots,
                          kind="delivered")
        state.warehouse_task_progress[item.task_id] = state.warehouse_task_progress.get(item.task_id, 0) + item.quantity
    state.warehouse_cart_loads = [item for item in state.warehouse_cart_loads if item.destination != world.actor.location_id]
    village.event(world, world.actor_id, action, warehouse.cart,
                  f"Unloaded {sum(item.quantity for item in deliveries)} warehouse units.", world.actor.location_id)
    return "APPLIED", "WAREHOUSE_CART_UNLOADED", f"Unloaded {sum(item.quantity for item in deliveries)} units at the assigned location.", None


def evaluate_goal(world: "World") -> dict[str, Any]:
    warehouse, state = config(world), world.state.village
    assert state is not None and state.economy is not None
    tasks = []
    for task in warehouse.tasks:
        moved = state.warehouse_task_progress.get(task.id, 0)
        tasks.append({"id": task.id, "kind": task.kind, "product": task.product,
                      "quantity": task.quantity, "moved": moved, "complete": moved >= task.quantity,
                      "source": task.source, "destination": task.destination})
    stock_rules = []
    for location, rules in warehouse.stock_rules.items():
        for product, rule in rules.items():
            quantity = state.stocks.get(location, {}).get(product, 0)
            stock_rules.append({"location": location, "product": product, "minimum": rule.minimum,
                                "maximum": rule.maximum, "quantity": quantity,
                                "complete": rule.minimum <= quantity <= rule.maximum})
    checks = {"tasks_complete": all(task["complete"] for task in tasks),
              "stocking_rules_satisfied": all(rule["complete"] for rule in stock_rules),
              "cart_empty": not state.warehouse_cart_loads}
    before_deadline = state.minute <= world.scenario.village.snapshot.deadline
    completed = all(checks.values())
    checks["before_deadline"] = completed and before_deadline
    status = "achieved" if completed and before_deadline else "failed" if not before_deadline else "active"
    return {"status": status, "reason": ("All warehouse picks and putaways satisfy the stocking plan."
            if status == "achieved" else "The warehouse deadline passed with unfinished cart work."
            if status == "failed" else "Warehouse picks, putaways or stocking rules remain unfinished."),
            "evidence": {"checks": checks, "tasks": tasks, "stock_rules": stock_rules,
                         "cart_loads": [item.model_dump(mode="json") for item in state.warehouse_cart_loads],
                         "minute": state.minute, "deadline": world.scenario.village.snapshot.deadline},
            "observation_revision": world.revision()}

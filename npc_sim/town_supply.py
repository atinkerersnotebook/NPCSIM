"""Read-only supply planning from a saved village revision."""

from collections import defaultdict
from math import ceil
from typing import Any

from npc_sim.models import VillageCommittedWork, VillageConfig, WorldState
from npc_sim.village_economy import ACTIVE, COMMITTED
from npc_sim.village_livestock import SHELF
from npc_sim.village_replenishment import forecast_rows


Lot = tuple[int, int | None]
STARTED, HARVEST, PLANNED_PRODUCTION, DELIVERY = range(4)


def _take(lots: list[Lot], quantity: int, fresh_until: int) -> list[Lot]:
    """Allocate first-expiring eligible units and preserve expiry through delivery."""
    taken = []
    for index, (amount, expiry) in sorted(enumerate(lots), key=lambda pair: (
            pair[1][1] if pair[1][1] is not None else float("inf"))):
        if not quantity:
            break
        if amount and (expiry is None or expiry > fresh_until):
            moved = min(quantity, amount)
            lots[index] = (amount - moved, expiry)
            taken.append((moved, expiry))
            quantity -= moved
    return taken


def _available(lots: list[tuple[int, int | None]], minute: int) -> int:
    return sum(quantity for quantity, expiry in lots if expiry is None or expiry > minute)


def _inventory_location(world: WorldState, site: str, product: str) -> str:
    village = world.village
    policy = village.economy.config if village and village.economy else None
    if site != "tavern" or policy is None:
        return site
    kitchen_inputs = {ingredient for recipe in policy.recipes.values() for ingredient in recipe.inputs}
    kitchen_inputs.update(ingredient for plan in policy.meal_plans for ingredient in plan.additional_inputs)
    return "communal-kitchen" if product in kitchen_inputs and "communal-kitchen" in world.entities else site


def report(world: WorldState, config: VillageConfig | None) -> dict[str, Any] | None:
    state = world.village
    if state is None:
        return None
    now = state.minute
    horizons = [now + hours * 60 for hours in (6, 24, 48, 168, 720)]
    economy = state.economy
    policy = economy.config if economy else None
    reserves = policy.reserve_targets if policy else {}
    targets = policy.production_targets if policy else {}
    lots: dict[tuple[str, str], list[tuple[int, int | None]]] = defaultdict(list)
    stock = {(site, good): amount for site, goods in state.stocks.items() for good, amount in goods.items()}
    for (site, good), amount in stock.items():
        saved = state.stock_lots.get(site, {}).get(good)
        lots[site, good] = ([(lot.quantity, lot.expires_at) for lot in saved] if saved is not None
                            else [(amount, None)])
    for mapping in (reserves, targets):
        for site, goods in mapping.items():
            for good in goods:
                lots[site, good]
    shelf = {**(SHELF if state.livestock else {}), **(config.spoil_after if config else {})}
    if state.research:
        for project in state.research.projects.values():
            if project.product and project.discovered_at is not None and project.proposal.kind in {"food", "crop"}:
                shelf.setdefault(project.product, 1440)

    cargo = defaultdict(list)
    cargo_rows = []
    carriers = [(rid, person.shipments) for rid, person in state.residents.items()]
    if state.wagon:
        carriers.append(("wagon", state.wagon.loads))
    for carrier, shipments in carriers:
        for shipment in shipments:
            cargo_rows.append({"carrier": carrier, "product": shipment.resource, "quantity": shipment.quantity,
                               "destination": shipment.destination, "order_id": shipment.order_id})
            if shipment.order_id:
                cargo[shipment.order_id].extend((lot.quantity, lot.expires_at) for lot in shipment.lots)
                if not shipment.lots and config and not config.expanded_rules:
                    cargo[shipment.order_id].append((shipment.quantity, None))

    orders = sorted(economy.orders.values(), key=lambda order: (order.deadline, order.id)) if economy else []
    production = []
    events: list[tuple[int, int, str, Any]] = []
    risks = []
    active_outputs = defaultdict(set)
    for rid, person in state.residents.items():
        work = person.life.work if person.life else None
        if not work or work.kind != "production":
            continue
        active_outputs[work.location].update(work.outputs)
        equipment = world.entities.get(work.equipment) if work.equipment else None
        worker = world.actors.get(rid)
        viable = not ((equipment and equipment.broken) or (worker and worker.location_id != work.location))
        production.append({"id": f"batch:{rid}", "worker": rid, "location": work.location,
                           "outputs": work.outputs, "inputs": {}, "gathered": {}, "ingredients": work.ingredients,
                           "finish": work.finishes_at, "status": "in_progress",
                           "basis": "Inputs already consumed" if viable else "Worker or equipment unavailable; not forecast"})
        if work.finishes_at >= now and viable:
            events.append((work.finishes_at, STARTED, rid, work))
            for good in work.outputs:
                lots[work.location, good]

    if state.livestock and state.livestock.config.enabled:
        for chunk in state.livestock.chunks:
            if chunk.status != "active" or chunk.operation not in {"milk", "eggs"} or chunk.end < now:
                continue
            worker = world.actors.get(chunk.actor)
            person = state.residents.get(chunk.actor)
            animal_ids = ["farm-cow"] if chunk.operation == "milk" else [
                f"farm-hen-{index:02d}" for index in range(1, state.livestock.config.hens + 1)]
            if (not worker or worker.location_id != chunk.location or not person
                    or (person.life and person.life.sleeping) or chunk.start // 1440 != chunk.end // 1440
                    or any((animal := world.entities.get(key)) is None or animal.kind != "creature"
                           or animal.hidden or animal.broken or animal.location != chunk.location
                           for key in animal_ids)):
                continue
            if any(
                (tool := world.entities.get(key)) is None or tool.hidden or tool.broken
                or (tool.location != chunk.location and key not in worker.inventory)
                for key in chunk.equipment
            ):
                continue
            outputs = {chunk.operation: chunk.quantity}
            active_outputs[chunk.location].update(outputs)
            production.append({"id": f"collection:{chunk.actor}:{chunk.start}", "worker": chunk.actor,
                               "location": chunk.location, "outputs": outputs, "inputs": {}, "gathered": {},
                               "ingredients": [], "finish": chunk.end, "status": "in_progress",
                               "basis": "Collection underway; yield depends on continued care and demand"})
            events.append((chunk.end, STARTED, chunk.actor, VillageCommittedWork(
                location=chunk.location, finishes_at=chunk.end, outputs=outputs)))
            lots[chunk.location, chunk.operation]

    for order in orders:
        plan = order.production_plan
        if order.status in COMMITTED and plan and plan.get("output", 0) > 0:
            finish = plan.get("finish", now)
            superseded = order.status == "in_production" or order.product in active_outputs.get(order.source, ())
            resident = config.residents.get(order.supplier) if config else None
            worker = world.actors.get(order.supplier)
            lacks_skills = bool(resident and worker and set(resident.skills) - set(worker.body.capabilities))
            row = {"id": order.id, "worker": order.supplier, "location": order.source,
                   "outputs": {order.product: plan["output"]}, "ingredients": [],
                   "inputs": {key[6:]: qty for key, qty in plan.items() if key.startswith("input:") and qty > 0},
                   "gathered": {key[7:]: qty for key, qty in plan.items() if key.startswith("gather:") and qty > 0},
                   "finish": finish, "status": order.status,
                   "driver": "harvest" if plan.get("session") and order.product in {"milk", "eggs"}
                   else "planned_production",
                   "basis": ("Overlapping work: only started batches are forecast" if superseded else
                             "Past planned finish; not forecast" if finish < now else
                             "Worker lacks required occupation skills; not forecast" if lacks_skills else
                             "Requires livestock care" if plan.get("requires_care") else "Recorded plan")}
            production.append(row)
            if finish >= now and not superseded and not lacks_skills and not plan.get("requires_care"):
                events.append((finish, PLANNED_PRODUCTION, order.id, row))
                lots[order.source, order.product]
                for good in row["inputs"]:
                    lots[order.source, good]
        if order.status in COMMITTED and order.remaining and order.deadline >= now:
            events.append((order.deadline, DELIVERY, order.id, order))
            lots[order.source, order.product]
            lots[order.destination, order.product]

    replenishment = forecast_rows(world, config)
    for task in replenishment:
        site, product = task["location"], task["product"]
        lots[site, product]
        if task["status"] != "planned" or task["finish"] is None or task["finish"] < now:
            continue
        if task.get("kind") == "production":
            row = {**task, "outputs": {product: task["quantity"]}, "gathered": {}}
            events.append((task["finish"], PLANNED_PRODUCTION, task["id"], row))
            for good in row["inputs"]:
                lots[site, good]
        else:
            events.append((task["finish"], HARVEST, task["id"], task))

    if config:
        standing_replenishment = _standing_forecasts(
            world, config, targets, orders, replenishment, horizons, now)
        for row in standing_replenishment:
            replenishment.append(row)
            lots[row["location"], row["product"]]
            if row["status"] != "planned" or row["finish"] is None or row["finish"] < now:
                continue
            if row.get("kind") == "production":
                event = {**row, "outputs": {row["product"]: row["quantity"]}, "gathered": {}}
                events.append((row["finish"], PLANNED_PRODUCTION, row["id"], event))
                for good in row.get("inputs", {}):
                    lots[row["location"], good]
            else:
                events.append((row["finish"], HARVEST, row["id"], row))

    usable = {key: _available(buckets, now) for key, buckets in lots.items()}
    baseline, _, _ = _project(lots, cargo, events, horizons, shelf, reserves, usable, include_plans=False)
    projected, changes, plan_risks = _project(lots, cargo, events, horizons, shelf, reserves, usable, include_plans=True)
    risks.extend(plan_risks)
    blocked = {risk["id"] for risk in plan_risks if risk["kind"] == "production input"}
    for task in replenishment:
        if task["id"] in blocked:
            task.update(status="blocked", quantity=0, finish=None,
                        basis="Not enough unreserved inputs at the planned time")

    meal_allocations = _meal_allocations(world, config)
    inventory = [{"location": _inventory_location(world, site, good),
                  "accounting_location": site if _inventory_location(world, site, good) != site else None,
                  "product": good, "on_hand": stock.get((site, good), 0),
                  "usable": usable[site, good], "reserve": reserves.get(site, {}).get(good, 0),
                  "target": targets.get(site, {}).get(good), "projected": projected[site, good],
                  "baseline_projected": baseline[site, good], "forecast_changes": changes[site, good],
                  **({"meal_allocations": meal_allocations} if good == "meals" else {})}
                 for site, good in sorted(projected)]
    details = _details(world, config, horizons, inventory, orders, cargo_rows, production, replenishment, risks)
    details["waterfall"] = _waterfall(details, horizons)
    return details


def _standing_forecasts(world: WorldState, config: VillageConfig, targets, orders,
                        replenishment: list[dict[str, Any]], horizons: list[int], now: int) -> list[dict[str, Any]]:
    state = world.village
    assert state is not None
    current_day = now // 1440
    final_day = horizons[-1] // 1440
    rows = []
    existing = {(row["location"], row["product"], row["finish"] // 1440)
                for row in replenishment if row["finish"] is not None}
    active = {(row.location, product, row.finishes_at // 1440)
              for person in state.residents.values() if person.life and person.life.work
              and person.life.work.kind == "production"
              for row, product in ((person.life.work, next(iter(person.life.work.outputs), "")),)
              if product}
    for order in orders:
        finish = order.production_plan.get("finish")
        if finish is not None and order.status in COMMITTED:
            existing.add((order.source, order.product, finish // 1440))

    producers = {}
    for rid, resident in config.residents.items():
        if resident.job:
            for product, quantity in resident.job.outputs.items():
                producers[resident.job.location, product] = (rid, quantity, resident.job.inputs,
                                                               resident.job.equipment, resident.job.duration)
    from npc_sim import village_livestock
    livestock = state.livestock
    if livestock and livestock.config.enabled:
        producers["farm", "milk"] = (livestock.config.keeper, 6, {}, None, 15)
        producers["farm", "eggs"] = (livestock.config.keeper,
                                       min(livestock.config.hens, targets.get("farm", {}).get("eggs", 0)),
                                       {}, None, 10)
    for rid, resident in config.residents.items():
        if "herbalism" in resident.skills and resident.job:
            producers[resident.work, "herbs"] = (rid, 2, {}, None, 5)
    if state.firewood and state.firewood.config.enabled:
        fuel = state.firewood.config
        supplier = config.residents[fuel.supplier]
        producers[supplier.work, "firewood"] = (
            fuel.supplier, fuel.firewood_per_log, {"logs": 1}, "wood-axe", fuel.split_minutes)
    if state.mining and state.mining.config.enabled:
        miner = state.mining.config.miner
        forge = config.residents[miner].work
        producers["iron-mine", "iron-ore"] = (
            miner, 1, {}, "mining-pick", state.mining.config.mining_minutes)
        producers[forge, "iron"] = (
            miner, 1, {"iron-ore": 1, "firewood": 1 if state.firewood else 0},
            "smelting-furnace", state.mining.config.smelting_minutes)

    chain = _daily_chain_requirements(world, config)
    policy = state.economy.config if state.economy else None

    for (site, product), (worker, batch_output, recipe_inputs, equipment, duration) in sorted(producers.items()):
        target = max(targets.get(site, {}).get(product, 0), chain.get((site, product), 0))
        if state.mining and state.mining.config.enabled:
            if product == "iron":
                target = max(target, policy.reserve_targets.get(site, {}).get("iron", 2) if policy else 2)
            elif product == "iron-ore":
                target = max(target, policy.reserve_targets.get(config.residents[state.mining.config.miner].work, {}).get("iron", 2) if policy else 2)
        if not target:
            continue
        resident = config.residents[worker]
        actor = world.actors.get(worker)
        capable = set(resident.skills) <= set(actor.body.capabilities) if actor else bool(resident.skills)
        equipment_ok = equipment is None or (equipment in world.entities and not world.entities[equipment].broken)
        daily_capacity = batch_output * max(1, (1170 - resident.wake_minute) // max(1, resident.job.interval if resident.job else duration))
        if policy and site == config.residents[policy.innkeeper].work and product == "meals":
            daily_capacity = sum(plan.portions for plan in policy.meal_plans)  # 16 breakfast + 16 supper in living Millhaven
            target = daily_capacity
        if product == "milk":
            daily_capacity = 12
        elif product == "eggs":
            daily_capacity = livestock.config.hens if livestock else 0
        for day in range(current_day, final_day + 1):
            if (site, product, day) in existing or (site, product, day) in active:
                continue
            finish = day * 1440 + max(resident.wake_minute + duration, 480 + duration)
            if finish <= now:
                finish = day * 1440 + min(1170, max(resident.wake_minute + duration, 1080))
            if finish > horizons[-1]:
                continue
            on_hand = state.stocks.get(site, {}).get(product, 0)
            quantity = min(max(0, target), daily_capacity)
            reason = (f"Standing chain forecast: {target} required per day for downstream demand; capacity is {daily_capacity}."
                      if capable and equipment_ok else
                      "The assigned worker lacks the occupation capabilities." if not capable else
                      "The required production equipment is unavailable or broken.")
            row = {"id": f"standing:{site}:{product}:{day}", "worker": worker,
                   "location": site, "product": product, "quantity": quantity if capable and equipment_ok else 0,
                   "required_output": target, "day": day + 1,
                   "finish": finish if capable and equipment_ok and quantity else None,
                   "status": "planned" if capable and equipment_ok and quantity else "blocked", "basis": reason}
            if product not in {"milk", "eggs"}:
                batches = ceil(quantity / batch_output) if batch_output else 0
                inputs = {material: amount * batches for material, amount in recipe_inputs.items()}
                if state.firewood and state.firewood.config.enabled and site in {
                        "bakery", "forge", "tavern"}:
                    inputs["firewood"] = inputs.get("firewood", 0) + 1
                row.update(kind="production", inputs=inputs)
            else:
                row["basis"] = ("Daily livestock yield: requires care and one real collection; standing capacity only."
                                if capable and equipment_ok else reason)
            rows.append(row)
    livestock = state.livestock
    wool = [row for row in rows if row["id"] == "long-term:sheep:wool"]
    if livestock and livestock.config.sheep and not wool:
        rows.append({"id": "long-term:sheep:wool", "worker": state.livestock.config.keeper,
                     "location": "farm", "product": "wool", "quantity": 0, "finish": None,
                     "status": "blocked",
                     "basis": "Long-term capability gap: the sheep are physical, but wool shearing is not implemented."})
    return rows


def _daily_chain_requirements(world: WorldState, config: VillageConfig) -> dict[tuple[str, str], int]:
    """Back-propagate one day's meal demand through configured physical producers."""
    state = world.village
    policy = state.economy.config if state and state.economy else None
    if not state or not policy or not config:
        return {}
    producers = {}
    for resident in config.residents.values():
        job = resident.job
        if not job:
            continue
        for product, quantity in job.outputs.items():
            producers[product] = (job.location, quantity, job.inputs)
    healers = [resident for resident in config.residents.values() if "herbalism" in resident.skills]
    if healers:
        healer = healers[0]
        producers.setdefault("herbs", (healer.work, 2, {}))
    if state.livestock and state.livestock.config.enabled:
        producers["milk"] = ("farm", 6, {})
        producers["eggs"] = ("farm", state.livestock.config.hens, {})
    if state.mining and state.mining.config.enabled:
        forge = config.residents[state.mining.config.miner].work
        producers["iron-ore"] = ("iron-mine", 1, {})
        producers["iron"] = (forge, 1, {"iron-ore": 1,
                              "firewood": 1 if state.firewood else 0})
    requirements: dict[tuple[str, str], int] = defaultdict(int)
    pending: list[tuple[str, str, int]] = []
    if state.mining and state.mining.config.enabled:
        forge = config.residents[state.mining.config.miner].work
        for product, quantity in policy.production_targets.get(forge, {}).items():
            if product == "wagon-parts" and quantity:
                pending.append((forge, product, quantity))
    for plan in policy.meal_plans:
        pending.append((config.residents[policy.innkeeper].work, "meals", plan.portions))
        for recipe_name, portions in plan.recipe_portions.items():
            recipe = policy.recipes.get(recipe_name)
            if recipe is None:
                continue
            batches = ceil(portions / recipe.portions)
            for product, amount in recipe.inputs.items():
                pending.append((config.residents[policy.innkeeper].work, product, batches * amount))
        for product, amount in plan.additional_inputs.items():
            pending.append((config.residents[policy.innkeeper].work, product, amount))
        fish_portions = plan.recipe_portions.get("fish", 0)
        if fish_portions and fish_portions not in plan.additional_inputs.values():
            pending.append((config.residents[policy.innkeeper].work, "fish", fish_portions))
        if plan.tea_portions:
                pending.append((config.residents[policy.innkeeper].work, "tea", plan.tea_portions))
        if plan.bread_service:
            pending.append((config.residents[policy.innkeeper].work, "bread", plan.bread_service))
    seen = 0
    while pending:
        site, product, quantity = pending.pop(0)
        source = producers.get(product)
        if source is None:
            continue
        source_site, output, inputs = source
        if source_site == config.residents[policy.innkeeper].work and product == "meals":
            requirements[source_site, product] += quantity
            continue
        requirements[source_site, product] += quantity
        batches = ceil(quantity / output)
        pending.extend((source_site, material, batches * amount) for material, amount in inputs.items())
        seen += 1
        if seen > 1000:
            raise ValueError("Supply-chain dependency cycle in configured recipes")
    if state.livestock and state.livestock.config.enabled:
        requirements["farm", "grain"] += ceil(state.livestock.config.hens / 4)
        requirements["farm", "hay"] += state.livestock.config.cows
    if state.firewood and state.firewood.config.enabled:
        supplier = state.firewood.config.supplier
        site = config.residents[supplier].work
        requirements[site, "firewood"] = max(requirements[site, "firewood"], state.firewood.config.reserve)
        log_need = ceil(requirements[site, "firewood"] / state.firewood.config.firewood_per_log)
        requirements[site, "logs"] += log_need
    fish = producers.get("fish")
    if fish:
        site, output, _ = fish
        requirements[site, "fish"] = min(
            requirements[site, "fish"],
            max(0, output * max(1, (1170 - config.residents[policy.suppliers["fish"]].wake_minute) //
                                max(1, config.residents[policy.suppliers["fish"]].job.interval))))
    return dict(requirements)


def _waterfall(details: dict[str, Any], horizons: list[int]) -> dict[str, Any]:
    inventory = {(row["location"], row["product"]): row for row in details["inventory"]}
    rows = sorted((row for row in details["replenishment"] if row["status"] in {"planned", "blocked"}),
                  key=lambda row: (row.get("finish") or horizons[-1] + 1, row["location"], row["product"]))
    requirements = []
    for row in rows:
        day = row.get("day", ((row.get("finish") or horizons[-1]) // 1440) + 1)
        stock = inventory.get((row["location"], row["product"]), {})
        requirements.append({
            "id": row["id"], "day": day, "worker": row["worker"],
            "site": row["location"], "product": row["product"],             "planned_output": row.get("required_output", row["quantity"]),
            "finish": row.get("finish"), "status": row["status"], "basis": row["basis"],
            "on_hand": stock.get("on_hand", 0), "target": stock.get("target"),
            "inputs": row.get("inputs", {}),
            "blocked_by": [risk for risk in details["risks"] if risk["id"] == row["id"]],
        })
    return {"horizons": horizons, "requirements": requirements}


def _meal_allocations(world: WorldState, config: VillageConfig | None) -> dict[str, Any]:
    state = world.village
    policy = state.economy.config if state and state.economy else None
    if not state or not config or not policy:
        return {"services": [], "unallocated": state.stocks.get("tavern", {}).get("meals", 0) if state else 0}
    day = state.minute // 1440
    prepared = [(lot.quantity, lot.expires_at) for lot in state.stock_lots.get("tavern", {}).get("meals", [])]
    if not prepared:
        quantity = state.stocks.get("tavern", {}).get("meals", 0)
        prepared = [(quantity, None)] if quantity else []
    available = sum(quantity for quantity, expiry in prepared
                    if expiry is None or expiry > state.minute)
    remaining_lots = list(prepared)
    services = []
    service_days = (day, day + 1) if state.minute % 1440 >= 600 else (day,)
    for service_day in service_days:
        for plan in sorted(policy.meal_plans, key=lambda item: item.use_minute):
            due = service_day * 1440 + plan.use_minute
            grace = world.village.day_policy.meal_window if world.village.day_policy else config.meal_window
            if due + grace < state.minute:
                continue
            meal_kind = "breakfast" if plan.id == "breakfast" else "daily"
            eaten = sum(1 for person in state.residents.values() if person.life
                        for meal in person.life.meals.values()
                        if meal.kind == meal_kind and meal.due // 1440 == service_day and meal.status == "eaten")
            needed = max(0, plan.portions - eaten)
            fresh = sum(quantity for quantity, expiry in remaining_lots
                        if expiry is None or expiry > due)
            reserved = min(needed, fresh)
            left = reserved
            next_lots = []
            for quantity, expiry in sorted(remaining_lots, key=lambda item: (
                    item[1] if item[1] is not None else float("inf"))):
                used = min(left, quantity) if expiry is None or expiry > due else 0
                left -= used
                if quantity - used:
                    next_lots.append((quantity - used, expiry))
            remaining_lots = next_lots
            services.append({"id": f"{service_day}:{plan.id}",
                             "service": "breakfast" if plan.id == "breakfast" else "supper",
                             "due": due, "required": needed, "reserved": reserved,
                             "shortfall": max(0, needed - reserved)})
    return {"services": services,
            "unallocated": sum(quantity for quantity, expiry in remaining_lots
                               if expiry is None or expiry > state.minute),
            "prepared": available}


def _project(initial_lots, initial_cargo, events, horizons, shelf, reserves, usable, *, include_plans):
    lots = {key: buckets.copy() for key, buckets in initial_lots.items()}
    cargo = defaultdict(list, {key: buckets.copy() for key, buckets in initial_cargo.items()})
    projected = {key: [] for key in lots}
    changes = {key: [] for key in lots}
    previous = usable.copy()
    risks = []

    def put(site: str, good: str, qty: int, minute: int) -> None:
        lifetime = shelf.get(good)
        lots[site, good].append((qty, minute + lifetime if lifetime else None))

    pending = iter(sorted((event for event in events if include_plans or event[1] in (STARTED, DELIVERY)),
                          key=lambda event: event[:3]))
    event = next(pending, None)
    for horizon in horizons:
        movement: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        while event is not None and event[0] <= horizon:
            minute, kind, event_id, item = event
            if kind == STARTED:
                for good, quantity in item.outputs.items():
                    put(item.location, good, quantity, minute)
                    movement[item.location, good]["production"] += quantity
            elif kind == HARVEST:
                site, good = item["location"], item["product"]
                put(site, good, item["quantity"], minute)
                movement[site, good]["harvest"] += item["quantity"]
            elif kind == PLANNED_PRODUCTION:
                site = item["location"]
                inputs = {good: max(0, quantity - item["gathered"].get(good, 0))
                          for good, quantity in item["inputs"].items()}
                shortages = {good: quantity - max(0, _available(lots[site, good], minute)
                                                  - reserves.get(site, {}).get(good, 0))
                             for good, quantity in inputs.items()}
                for good, missing in shortages.items():
                    if missing > 0:
                        risks.append({"id": event_id, "kind": "production input", "minute": minute,
                                      "location": site, "product": good, "missing": missing})
                if not any(missing > 0 for missing in shortages.values()):
                    for good, quantity in inputs.items():
                        taken = _take(lots[site, good], quantity, minute)
                        movement[site, good]["inputs"] += sum(amount for amount, _ in taken)
                    for good, quantity in item["outputs"].items():
                        put(site, good, quantity, minute)
                        movement[site, good][item.get("driver", "planned_production")] += quantity
            else:
                cutoff = max(minute, item.required_fresh_until)
                incoming = _take(cargo[event_id], min(item.in_transit, item.remaining), cutoff)
                source = lots[item.source, item.product]
                surplus = max(0, _available(source, minute) - reserves.get(item.source, {}).get(item.product, 0))
                loaded = _take(source, min(item.loadable, surplus), cutoff)
                movement[item.source, item.product]["outbound"] += sum(quantity for quantity, _ in loaded)
                incoming += loaded
                moved = sum(quantity for quantity, _ in incoming)
                lots[item.destination, item.product].extend(incoming)
                movement[item.destination, item.product]["inbound"] += moved
                if moved < item.remaining:
                    risks.append({"id": event_id, "kind": "delivery", "minute": minute,
                                  "location": item.destination, "product": item.product, "missing": item.remaining - moved})
            event = next(pending, None)
        for key, checkpoints in projected.items():
            value = _available(lots[key], horizon)
            delta = movement[key]
            # Whatever left usable stock without being allocated expired during this interval.
            gains = delta["production"] + delta["inbound"] + delta.get("harvest", 0) + delta.get("planned_production", 0)
            expired = previous[key] + gains - delta["inputs"] - delta["outbound"] - value
            changes[key].append({**delta, "expired": expired})
            checkpoints.append(value)
            previous[key] = value

    return projected, changes, risks


def _details(world, config, horizons, inventory, orders, cargo_rows, production, replenishment, risks):
    state = world.village
    assert state is not None
    communication = state.communication
    requests = []
    notes = []
    if communication:
        for fixture, papers in communication.placements.items():
            entity = world.entities.get(fixture)
            if entity is None or entity.location is None:
                continue
            for notice_id, version in papers.items():
                history = communication.notices.get(notice_id, [])
                if not 0 < version <= len(history):
                    continue
                paper, latest = history[version - 1], history[-1]
                notes.append({"id": notice_id, "fixture": fixture, "location": entity.location,
                              "author": paper.author, "title": paper.terms.subject, "text": paper.terms.description,
                              "minute": paper.updated_at, "version": version, "status": paper.status,
                              "latest_version": latest.version, "latest_status": latest.status})
        for history in communication.notices.values():
            if history and history[-1].terms.type in {"supply_request", "work_request"}:
                notice = history[-1]
                requests.append({"id": notice.id, "kind": notice.terms.type, "owner": notice.author,
                                 "subject": notice.terms.subject, "quantity": notice.terms.quantity,
                                 "destination": notice.terms.destination, "status": notice.status,
                                 "due": notice.terms.due_at, "order_id": notice.linked_order_id,
                                 "detail": notice.response or notice.terms.description})
    needs = [{"id": key, **need.model_dump()} for person in state.residents.values()
             for key, need in person.fallback.supply_needs.items()]
    board = communication.config.board_id if communication else "noticeboard"
    for event in state.notices:
        fixture = world.entities.get(board)
        notes.append({"id": None, "fixture": board, "location": (fixture.location if fixture else None),
                      "author": event.actor, "title": "Public bell notice", "text": event.result,
                      "minute": event.minute, "status": None, "version": None,
                      "latest_version": None, "latest_status": None})
    for key, entity in world.entities.items():
        if not entity.hidden and not entity.takable and entity.readable_text and entity.location in world.entities:
            if world.entities[entity.location].kind == "location":
                notes.append({"id": key, "fixture": key, "location": entity.location, "author": None,
                              "title": entity.name, "text": entity.readable_text, "minute": None, "status": None,
                              "version": None, "latest_version": None, "latest_status": None})

    projects = []
    if config:
        for key, title, site, complete in (("dig_well", "Dig the well", config.well_site, state.well_dug),
                                            ("repair_mill", "Repair the mill", config.mill, state.repaired)):
            tasks = [task for task in state.tasks.values() if task.kind == key]
            progress = sum(state.project_contributions.get(key, {}).values())
            session = state.crew_sessions.get(key)
            deadline = state.deadlines.get(key)
            blocker = next((task.blocker for task in tasks if task.status == "blocked" and task.blocker), None)
            status = ("completed" if complete else "blocked" if any(task.status == "blocked" for task in tasks)
                      else "in_progress" if progress or (session and session.status == "active")
                      or any(task.status == "in_progress" for task in tasks)
                      else "upcoming" if key == "repair_mill" and not state.breakdown else "pending")
            projects.append({"id": key, "title": title, "location": site, "status": status,
                             "due": deadline.due if deadline else None, "progress": progress if config.expanded_rules else None,
                             "blocker": blocker})
    wagon_entity = world.entities.get("wagon")
    if state.wagon and wagon_entity and wagon_entity.location:
        wagon_repair = next((person.life.work for person in state.residents.values()
                             if person.life and person.life.work and person.life.work.kind == "wagon_repair"), None)
        projects.append({"id": "repair_wagon", "title": "Repair the wagon", "location": wagon_entity.location,
                         "status": "completed" if state.wagon.repaired else "in_progress" if wagon_repair else "pending",
                         "due": None, "progress": None, "blocker": None})
    if state.economy and state.economy.config:
        for public_work in state.economy.config.public_works:
            installed = state.economy.public_work_progress.get(public_work.id, {})
            completed = public_work.id in state.economy.public_work_completed_at
            blocked = any(order.public_work == public_work.id and order.status == "blocked"
                          for order in state.economy.orders.values())
            projects.append({
                "id": public_work.id, "title": public_work.title, "location": public_work.location,
                "status": "completed" if completed else "blocked" if blocked else
                          "in_progress" if any(installed.values()) else "pending",
                "due": public_work.due_at, "progress": sum(installed.values()),
                "blocker": "A funded civic material order is blocked by physical supply." if blocked else None,
                "materials": public_work.materials, "installed": installed,
            })
    dashboard = state.dashboard
    maintenance = [job.model_dump() for job in dashboard.maintenance] if dashboard else []
    upcoming_maintenance = []
    upkeep = state.maintenance
    maintenance_config = upkeep.config if upkeep else config.maintenance if config else None
    if maintenance_config and maintenance_config.enabled:
        tomorrow = state.minute // 1440 + 1
        for template, definition in sorted(maintenance_config.tasks.items()):
            previous = [job for job in upkeep.jobs.values() if job.template == template] if upkeep else []
            if any(job.day == tomorrow for job in previous):
                continue
            unfinished = next((job for job in previous if job.completed_at is None), None)
            if unfinished is not None:
                continue
            upcoming_maintenance.append({
                "id": f"preview:maintenance:{tomorrow}:{template}", "title": definition.title,
                "assignee": definition.assignee, "location": definition.location, "day": tomorrow + 1,
                "release_at": tomorrow * 1440 + definition.release_minute,
                "due_at": tomorrow * 1440 + definition.due_minute, "worked_minutes": 0,
                "required_minutes": definition.duration, "supplies": definition.supplies,
                "status": "upcoming", "completed_at": None, "blocker": None,
            })
    return {"minute": state.minute, "revision": world.revision, "horizons": horizons, "inventory": inventory,
            "orders": [{**order.model_dump(), "remaining": order.remaining, "open": order.status in ACTIVE}
                       for order in orders],
            "requests": requests, "needs": needs, "cargo": cargo_rows, "production": production,
            "replenishment": replenishment,
            "risks": risks, "maintenance": maintenance, "upcoming_maintenance": upcoming_maintenance,
            "projects": projects, "notes": notes,
            "sites": {key: entity.name for key, entity in world.entities.items() if entity.kind == "location"},
            "fixtures": {note["fixture"]: world.entities[note["fixture"]].name for note in notes
                         if note["fixture"] in world.entities},
            "residents": {**{key: world.entities[key].name if key in world.entities else key.replace("-", " ").title()
                             for key in state.residents}, **(state.legacy.current if state.legacy else {})}}

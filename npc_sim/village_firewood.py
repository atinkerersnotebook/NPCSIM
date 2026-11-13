"""Physical firewood production, fueled batches and shared occupied cottage fires."""

import re
from uuid import uuid4
from math import ceil
from typing import TYPE_CHECKING, Any

from npc_sim import handling, village, village_economy, village_goods
from npc_sim.models import (Entity, Scenario, VillageFirewoodConfig, VillageFirewoodState,
                            VillageFuelDeliveryPlan, VillageFuelStop, VillageJob)

if TYPE_CHECKING:
    from npc_sim.world import World

VERBS: set[str] = set()
SPLIT = "village_split_firewood"
AXE = "wood-axe"
PRICE = 1
HOT_ACTIONS = {"village_work", "village_cook_fish", "village_cook_dairy", "village_smelt_iron"}
POLICY = (
    "Firewood is split from real logs at the woodlot with the actual axe and committed work time. "
    "Keep construction logs and promised timber protected. Produce only a small reserve or accepted demand. "
    "Each successful oven, cooking or forge batch consumes one firewood; rejected actions consume none. "
    "Smelting uses firewood instead of logs, never both. Heated research trials and recipes also use fuel. "
    "Cottage hearths share one physical woodpile funded by the named custodian's existing coins. "
    "Only explicitly configured hearth heating burns fuel: one unit for four hours in spring/autumn "
    "or two in winter, between 18:00 and 07:00 when occupied. Summer and unoccupied hearths burn nothing. "
    "Cold homes are reported without health penalties. Season is operator-configured, not inferred weather. "
    "Only local fuel stocks and hearths are visible. Inspect supplies, remember shortages and use a physical "
    "board, inbox or conversation to request funded deliveries. Optional grouped fuel routes execute real "
    "load, travel and unload actions; blocked routes retain their cargo without remote delivery."
)


def enabled(world: "World") -> bool:
    state = world.state.village.firewood
    return bool(state and state.config.enabled)


def example_config(scenario: Scenario) -> dict[str, Any]:
    base = scenario.village
    supplier = next(rid for rid, resident in base.residents.items() if "forestry" in resident.skills)
    buyer = next(rid for rid, resident in base.residents.items()
                 if resident.work == resident.home == scenario.start_location)
    members = sorted(rid for rid, resident in base.residents.items()
                     if resident.home == base.residents[buyer].home)
    return VillageFirewoodConfig(
        supplier=supplier, household_buyer=buyer, heating_enabled=True,
        households={f"hearth-{index // 4 + 1}": members[index:index + 4]
                    for index in range(0, len(members), 4)},
        businesses=[rid for rid, resident in base.residents.items()
                    if resident.job and resident.job.equipment in {"anvil", "oven", "tavern-hearth"}],
        site_capacities={site: 24 if site == base.residents[supplier].work or
                         (site == base.residents[buyer].work and scenario.id == "millhaven-living") else 16
                         for site in {base.residents[supplier].work, base.residents[buyer].work,
                                      *(resident.work for resident in base.residents.values()
                                        if resident.job and resident.job.equipment in
                                        {"anvil", "oven", "tavern-hearth"})}},
        site_reorder_points={"tavern": 9, "bakery": 7, "forge": 7, "cottages": 18}
        if scenario.id == "millhaven-living" else {},
        site_reserve_targets={"tavern": 12, "bakery": 10, "forge": 10, "cottages": 24}
        if scenario.id == "millhaven-living" else {},
    ).model_dump(mode="json")


def _validate(scenario: Scenario, config: VillageFirewoodConfig) -> None:
    base = scenario.village
    if not base or not (base.expanded_rules and base.time_based_turns and base.daily_life and base.ai_controlled):
        raise ValueError("Firewood requires expanded timed AI village life")
    if base.economy is None:
        raise ValueError("Firewood requires a funded demand-led economy")
    supplier = base.residents.get(config.supplier)
    if supplier is None or supplier.job is None or "forestry" not in supplier.skills:
        raise ValueError("Firewood must be supplied by an actual employed forester")
    axe = scenario.entities.get(AXE)
    if axe is None or axe.location != supplier.work or AXE not in base.owners or base.owners[AXE] != config.supplier:
        raise ValueError("The forester must own the actual axe at the woodlot")
    buyer = base.residents.get(config.household_buyer)
    if buyer is None or buyer.work != buyer.home:
        raise ValueError("The cottage custodian must work at the communal household stock location")
    members = [rid for group in config.households.values() for rid in group]
    cottage_residents = {rid for rid, resident in base.residents.items() if resident.home == buyer.home}
    if (set(members) != cottage_residents or len(members) != len(set(members))
            or any(not group for group in config.households.values())
            or any(not re.fullmatch(r"[a-z0-9][a-z0-9-]*", key) for key in config.households)):
        raise ValueError("Assign every cottage resident exactly once to a named shared cottage hearth")
    if len(config.businesses) != len(set(config.businesses)) or any(
            rid not in base.residents or base.residents[rid].job is None for rid in config.businesses):
        raise ValueError("Fuel consumers must be unique real resident businesses")
    required = {base.smith, base.economy.innkeeper} | {
        rid for rid, resident in base.residents.items()
        if resident.job and resident.job.equipment == "oven"}
    if not required <= set(config.businesses):
        raise ValueError("Firewood must cover the smith, innkeeper and all bakers")
    sites = {buyer.work, supplier.work, *(base.residents[rid].work for rid in config.businesses)}
    if any(site not in base.stocks for site in sites):
        raise ValueError("Firewood requires actual stock locations")
    if (set(config.site_capacities) != sites
            or any(type(limit) is not int or limit < 1 for limit in config.site_capacities.values())
            or config.site_capacities[supplier.work] < config.firewood_per_log):
        raise ValueError("Every configured fuel site needs explicit positive storage capacity")
    if (not set(config.site_reorder_points) <= sites
            or not set(config.site_reserve_targets) <= sites
            or any(type(value) is not int or value < 0 or value > config.site_capacities[site]
                   for site, value in config.site_reorder_points.items())
            or any(type(value) is not int or value < 1 or value > config.site_capacities[site]
                   for site, value in config.site_reserve_targets.items())
            or any(config.site_reorder_points.get(site, config.reorder_point) > target
                   for site, target in config.site_reserve_targets.items())):
        raise ValueError("Fuel reorder points and reserve targets must name real sites and fit storage capacities")


def fixtures(scenario: Scenario, config: VillageFirewoodConfig) -> dict[str, Entity]:
    location = scenario.village.residents[config.household_buyer].home
    return {f"cottage-{key}": Entity(
        name=f"Shared cottage {key.replace('-', ' ')}", kind="fixture", location=location,
        description="A shared household hearth, supplied from the communal cottage firewood pile.")
        for key in config.households}


def install_scenario(scenario: Scenario) -> None:
    base = scenario.village
    config = VillageFirewoodConfig.model_validate(example_config(scenario))
    _validate(scenario, config)
    base.firewood = config
    for key, fixture in fixtures(scenario, config).items():
        if key in scenario.entities and scenario.entities[key] != fixture:
            raise ValueError(f"Firewood fixture identity conflicts: {key}")
        scenario.entities.setdefault(key, fixture)
    base.prices.setdefault("firewood", PRICE)
    for stock in base.stocks.values():
        stock.setdefault("firewood", 0)


def validate_config(scenario: Scenario) -> None:
    config = scenario.village.firewood if scenario.village else None
    if config is not None:
        _validate(scenario, config)
        for key, fixture in fixtures(scenario, config).items():
            if key not in scenario.entities or scenario.entities[key] != fixture:
                raise ValueError(f"Firewood requires its actual shared hearth: {key}")


def initial_state(scenario: Scenario) -> VillageFirewoodState | None:
    config = scenario.village.firewood if scenario.village else None
    return VillageFirewoodState(config=config.model_copy(deep=True)) if config else None


def configure(world: "World", values: dict[str, Any]) -> None:
    config = VillageFirewoodConfig.model_validate(values)
    scenario = world.scenario.model_copy(deep=True)
    scenario.village.economy = village_economy.config(world)
    _validate(scenario, config)
    state = world.state.village
    if state.firewood:
        identity = {"supplier", "household_buyer", "households", "businesses"}
        if any(state.stocks.get(site, {}).get("firewood", 0) > limit
               for site, limit in config.site_capacities.items()):
            raise ValueError("Existing physical fuel stocks exceed the proposed storage capacity")
        if state.firewood.config.model_dump(include=identity) != config.model_dump(include=identity):
            raise ValueError("Installed firewood participants and household groups cannot be replaced")
        if state.firewood.delivery_plans and state.firewood.config.site_capacities != config.site_capacities:
            raise ValueError("Finish fuel delivery plans before changing site capacities")
    if any(state.stocks.get(site, {}).get("firewood", 0) > limit
           for site, limit in config.site_capacities.items()):
        raise ValueError("Existing physical fuel stocks exceed the proposed storage capacity")
    for key, fixture in fixtures(scenario, config).items():
        current = world.state.entities.get(key)
        if current is not None and current != fixture:
            raise ValueError(f"Firewood fixture identity conflicts: {key}")
        world.state.entities.setdefault(key, fixture)
    if state.firewood:
        state.firewood.config = config
    else:
        state.firewood = VillageFirewoodState(config=config)
    for stock in state.stocks.values():
        stock.setdefault("firewood", 0)


def capacity_limit(world: "World", location: str) -> int | None:
    if not enabled(world):
        return None
    return world.state.village.firewood.config.site_capacities.get(location)


def capacity_available(world: "World", location: str) -> int | None:
    limit = capacity_limit(world, location)
    if limit is None:
        return None
    return max(0, limit - world.state.village.stocks.get(location, {}).get("firewood", 0))


def production_job(world: "World") -> VillageJob | None:
    if not enabled(world):
        return None
    config = world.state.village.firewood.config
    if world.actor_id != config.supplier:
        return None
    job = world.scenario.village.residents[config.supplier].job
    return job.model_copy(update={
        "inputs": {"logs": 1}, "outputs": {"firewood": config.firewood_per_log},
        "equipment": AXE, "duration": config.split_minutes, "interval": config.split_minutes,
        "interruptible": False, "batch_size": 1,
    })


def _committed(world: "World", location: str, product: str) -> int:
    return sum(person.life.work.outputs.get(product, 0) for person in world.state.village.residents.values()
               if person.life and person.life.work and person.life.work.location == location)


def _timber_logs(world: "World", targets: dict[str, int]) -> int:
    config = world.state.village.firewood.config
    job = world.scenario.village.residents[config.supplier].job
    short = max((ceil(max(0, targets.get(product, 0)
                         - village_economy.usable_stock(world, job.location, product, world.state.village.minute)
                         - _committed(world, job.location, product)) / amount)
                 for product, amount in job.outputs.items()), default=0)
    return short * job.inputs.get("logs", 0)


def source_reserve(world: "World") -> int:
    return world.state.village.firewood.config.firewood_per_log


def log_target(world: "World", location: str, targets: dict[str, int]) -> int:
    if not enabled(world):
        return targets.get("logs", 0)
    config = world.state.village.firewood.config
    if location != world.scenario.village.residents[config.supplier].work:
        return targets.get("logs", 0)
    short = max(0, targets.get("firewood", 0)
                - village_economy.usable_stock(world, location, "firewood", world.state.village.minute)
                - _committed(world, location, "firewood"))
    needed = ceil(short / config.firewood_per_log) + _timber_logs(world, targets)
    floor = village_economy.protected_quantity(world, location, "logs")
    # Felling produces two logs at a time; a one-log target must not deadlock replenishment.
    return max(targets.get("logs", 0), floor + 2 * ceil(needed / 2))


def _burn_minutes(world: "World") -> int:
    config = world.state.village.firewood.config
    return {"spring": 240, "summer": 0, "autumn": 240, "winter": 120}[config.season] if config.heating_enabled else 0


def _heating_end(minute: int) -> int:
    day, clock = divmod(minute, 1440)
    return day * 1440 + 420 if clock < 420 else (day + 1) * 1440 + 420


def _heating_now(world: "World") -> bool:
    clock = world.state.village.minute % 1440
    return bool(_burn_minutes(world) and (clock < 420 or clock >= 1080))


def _household_location(world: "World") -> str:
    config = world.state.village.firewood.config
    return world.scenario.village.residents[config.household_buyer].home


def consumer_target(world: "World", buyer: str) -> int:
    config = world.state.village.firewood.config
    target = config.reserve if buyer in config.businesses else 0
    if buyer == config.household_buyer and (burn := _burn_minutes(world)):
        now = world.state.village.minute
        start = now if _heating_now(world) else now // 1440 * 1440 + 1080
        end = _heating_end(start)
        target += config.reserve + sum(
            ceil(max(0, end - max(start, world.state.village.firewood.warm_until.get(key, 0))) / burn)
            for key in config.households)
    location = world.scenario.village.residents[buyer].work
    if buyer in config.businesses:
        target = max(target, config.reserve * config.business_buffer_days)
    target = max(target, getattr(config, "site_reserve_targets", {}).get(location, 0))
    return min(target, config.site_capacities[location])


def requests(world: "World", actor: str) -> list[tuple[str, int, dict[str, int]]]:
    if not enabled(world):
        return []
    config = world.state.village.firewood.config
    if actor not in {*config.businesses, config.household_buyer}:
        return []
    location = world.scenario.village.residents[actor].work
    if world.actor_id == actor and world.actor.location_id != location:
        return []
    target = consumer_target(world, actor)
    if not target:
        return []
    state = world.state.village
    available = village_economy.usable_stock(world, location, "firewood", state.minute)
    if available > getattr(config, "site_reorder_points", {}).get(location, config.reorder_point):
        return []
    used = state.firewood.consumed.get(location, 0)
    return [(f"firewood:{state.minute // 1440}:{location}:{used}", state.minute + 360, {"firewood": target})]


def delivery_plan(world: "World", carrier: str | None = None) -> VillageFuelDeliveryPlan | None:
    """Plan one physically feasible load from accepted, locally known fuel orders."""
    if not enabled(world):
        return None
    state = world.state.village
    config = state.firewood.config
    carrier = carrier or world.actor_id
    if carrier != config.supplier or world.actor_id != carrier or world.actor.location_id != (
            source := world.scenario.village.residents[config.supplier].work):
        return None
    from npc_sim import village_communication, village_services
    wagon = state.wagon
    vehicle = "wagon" if (wagon and wagon.driver == carrier and wagon.hitched
                          and world.state.entities.get("wagon")
                          and world.state.entities["wagon"].location == source
                          and world.state.entities.get("work-horse")
                          and world.state.entities["work-horse"].location == source
                          and not wagon.automatic) else None
    shipments = wagon.loads if vehicle else state.residents[carrier].shipments
    capacity = (6 if vehicle else village_services.foot_capacity(world, carrier)) - sum(
        item.quantity for item in shipments)
    stock = max(0, village_economy.usable_stock(world, source, "firewood", state.minute)
                - village_economy.policy_reserve(world, source, "firewood"))
    if capacity <= 0 or stock <= 0 or not state.economy:
        return None
    orders = sorted((order for order in state.economy.orders.values()
                     if order.status in village_economy.COMMITTED and order.product == "firewood"
                     and order.source == source and order.loadable > 0 and order.deadline >= state.minute
                     and village_communication.can_use_order(world, order)),
                    key=lambda order: (order.deadline, order.id))
    # The goods loader allocates the first eligible order for each destination.
    orders = list({order.destination: order for order in reversed(orders)}.values())
    location = source
    stops: list[VillageFuelStop] = []
    inventory = min(capacity, stock)
    arrival = state.minute
    while inventory and orders:
        choices = []
        for order in orders:
            path = village.route(world, location, order.destination, road_only=bool(vehicle))
            available = capacity_available(world, order.destination)
            inbound = sum(item.quantity for item in shipments if item.resource == "firewood"
                          and item.destination == order.destination)
            amount = min(inventory, order.loadable,
                         village_economy.loadable_for_order(world, order),
                         max(0, (available if available is not None else inventory) - inbound))
            if path is not None and amount and arrival + len(path) * 5 <= order.deadline:
                choices.append((len(path), order.deadline, order.id, path, amount, order))
        if not choices:
            break
        steps, _, _, path, amount, order = min(choices)
        stops.append(VillageFuelStop(destination=order.destination, order_id=order.id,
                                     quantity=amount, delivered_before=order.delivered_quantity,
                                     reason_for_stop="accepted funded fuel demand", path=path))
        arrival += steps * 5
        inventory -= amount
        location = order.destination
        orders = [item for item in orders if item.destination != order.destination]
    if not stops:
        return None
    return VillageFuelDeliveryPlan(
        route_id=f"fuel:{carrier}:{state.minute}:{'-'.join(stop.order_id for stop in stops)}",
        carrier=carrier, load_plan={"firewood": sum(stop.quantity for stop in stops)}, stops=stops,
        expected_departure=state.minute, required_arrival=min(
            state.economy.orders[stop.order_id].deadline for stop in stops),
        remaining_inventory=sum(stop.quantity for stop in stops), vehicle_id=vehicle,
        reason_for_stop="confirmed local fuel orders grouped by reachable road distance")


def save_delivery_plan(world: "World") -> VillageFuelDeliveryPlan | None:
    """Record an optional plan without loading cargo or changing order ownership."""
    plan = delivery_plan(world)
    if plan is not None:
        plans = world.state.village.firewood.delivery_plans
        active = next((item for item in plans if item.route_id == plan.route_id
                       and item.status in {"loaded", "in_progress"}), None)
        if active is not None:
            return active
        plans[:] = [item for item in plans if item.status not in {"planned", "cancelled"}]
        plans.append(plan)
    return plan


def execute_delivery_plan(world: "World", route_id: str) -> dict[str, Any]:
    """Execute a saved fuel route with ordinary goods and door actions, stopping on any blocker."""
    if not enabled(world):
        return {"status": "blocked", "reason": "Firewood is not enabled."}
    state = world.state.village
    plan = next((item for item in state.firewood.delivery_plans if item.route_id == route_id), None)
    if plan is None or plan.carrier != world.actor_id or plan.carrier != state.firewood.config.supplier:
        return {"status": "blocked", "reason": "No fuel route belongs to this supplier."}
    if plan.status in {"completed", "cancelled"}:
        return {"status": plan.status, "reason": "This route is closed."}
    from npc_sim import village_communication, village_services
    source = world.scenario.village.residents[plan.carrier].work
    wagon = state.wagon
    vehicle = bool(plan.vehicle_id)
    shipments = wagon.loads if vehicle and wagon else state.residents[plan.carrier].shipments

    def blocked(reason: str) -> dict[str, Any]:
        return {"status": "blocked", "reason": reason, "route_id": route_id,
                "completed_stops": sum(stop.quantity <= max(0, state.economy.orders[stop.order_id].delivered_quantity
                                          - stop.delivered_before) for stop in plan.stops
                                       if stop.order_id in state.economy.orders)}

    def act(action: str, **arguments: Any) -> dict[str, Any]:
        return world.act(request_id=f"fuel-route-{uuid4().hex}", expected_observation_revision=world.revision(),
                         action_id=action, arguments=arguments)

    def finish_turn() -> None:
        village.advance(world, 5)
        world.save()

    if not state.economy or not plan.stops or plan.load_plan != {"firewood": sum(stop.quantity for stop in plan.stops)}:
        return blocked("The saved fuel route is invalid.")
    if vehicle and (plan.vehicle_id != "wagon" or not wagon or wagon.driver != plan.carrier
                    or not wagon.hitched or wagon.automatic or not world.state.entities.get("wagon")
                    or world.state.entities["wagon"].location != world.actor.location_id
                    or not world.state.entities.get("work-horse")
                    or world.state.entities["work-horse"].location != world.actor.location_id):
        return blocked("The hitched manual wagon and horse must accompany their driver.")
    if not vehicle and wagon and wagon.driver == plan.carrier:
        return blocked("Park the wagon before starting a foot route.")
    if state.minute < state.residents[plan.carrier].available_at:
        return blocked("The supplier is busy; wait until their next available time.")
    if plan.status == "planned" or (plan.status == "loaded" and world.actor.location_id == source):
        if world.actor.location_id != source:
            return blocked("Return to the actual woodlot before loading.")
        loaded = {stop.order_id: sum(item.quantity for item in shipments
                                     if item.order_id == stop.order_id and item.resource == "firewood")
                  for stop in plan.stops}
        if any(loaded[stop.order_id] not in {0, stop.quantity} for stop in plan.stops):
            return blocked("Route cargo has an unexpected quantity; inspect the shipment ledger.")
        if plan.status == "planned" and any(loaded.values()):
            return blocked("Route cargo is already loaded; inspect the shipment ledger.")
        if plan.status == "loaded" and not any(loaded.values()):
            return blocked("Loaded route cargo is missing; inspect the shipment ledger.")
        if plan.status == "loaded" and any(stop.delivered_before != state.economy.orders[stop.order_id].delivered_quantity
                                           for stop in plan.stops if stop.order_id in state.economy.orders):
            return blocked("The saved route is out of sync with delivered orders.")
        pending = [stop for stop in plan.stops if not loaded[stop.order_id]]
        if (len({stop.destination for stop in plan.stops}) != len(plan.stops)
                or len({stop.order_id for stop in plan.stops}) != len(plan.stops)):
            return blocked("A grouped route must have one confirmed order per destination.")
        if plan.status == "loaded" and any(item.order_id in loaded and item.destination != next(
                stop.destination for stop in plan.stops if stop.order_id == item.order_id)
                for item in shipments):
            return blocked("Loaded cargo does not match the saved destination.")
        if plan.status == "loaded" and any(item.order_id in loaded and item.resource != "firewood"
                                           for item in shipments):
            return blocked("Loaded cargo does not match the saved fuel route.")
        capacity = (6 if vehicle else village_services.foot_capacity(world, plan.carrier)) - sum(
            item.quantity for item in shipments)
        needed = sum(stop.quantity for stop in pending)
        if capacity < needed or (needed and village_economy.usable_stock(
                world, source, "firewood", state.minute) - village_economy.policy_reserve(
                    world, source, "firewood") < needed):
            return blocked("Real unreserved fuel or carrying capacity no longer covers the route.")
        location, arrival = source, state.minute
        for stop in plan.stops:
            order = state.economy.orders.get(stop.order_id)
            if (not order or order.product != "firewood"
                    or order.source != source or order.supplier != plan.carrier
                    or order.destination != stop.destination
                    or (stop in pending and (order.status not in village_economy.COMMITTED
                        or not village_communication.can_use_order(world, order)))
                    or order.delivered_quantity != stop.delivered_before
                    or (stop in pending and village_economy.loadable_for_order(world, order) < stop.quantity)
                    or capacity_available(world, stop.destination) is not None
                    and capacity_available(world, stop.destination) < stop.quantity):
                return blocked("A confirmed known fuel order or destination capacity has changed.")
            path = village.route(world, location, stop.destination, road_only=vehicle)
            if path is None or (stop in pending and path != stop.path):
                return blocked("The saved physical route is no longer traversable.")
            arrival += 5 * (1 + len(path) + 1)
            if stop in pending and arrival > order.deadline:
                return blocked("The order will expire before physical delivery.")
            location = stop.destination
        for stop in pending:
            result = act("village_load_wagon" if vehicle else "village_load_goods",
                         goods={"firewood": stop.quantity}, destination=stop.destination)
            if result["outcome"] != "APPLIED":
                return blocked(f"Loading stopped: {result['code']}: {result['explanation']}")
            finish_turn()
        plan.status = "loaded"
        world.save()
    for stop in plan.stops:
        order = state.economy.orders.get(stop.order_id)
        if not order:
            return blocked("A route order is missing; retain the cargo.")
        if order.delivered_quantity - stop.delivered_before >= stop.quantity:
            continue
        cargo = [item for item in shipments if item.order_id == stop.order_id and item.resource == "firewood"
                 and item.destination == stop.destination]
        if sum(item.quantity for item in cargo) != stop.quantity:
            return blocked("Route cargo no longer matches the accepted order; retain existing cargo.")
        if ((not cargo and (order.status not in village_economy.COMMITTED
                or state.minute > order.deadline or not village_communication.can_use_order(world, order)))
                or order.product != "firewood" or order.source != source
                or order.destination != stop.destination or order.supplier != plan.carrier
                or order.delivered_quantity != stop.delivered_before):
            return blocked("The accepted order is no longer deliverable; retain the cargo.")
        path = village.route(world, world.actor.location_id, stop.destination, road_only=vehicle)
        if path is None:
            return blocked("No legal passage to the destination; cargo remains with the carrier.")
        for destination in path:
            door = next((eid for eid, entity in world.state.entities.items()
                         if entity.kind == "door" and {entity.location, entity.destination}
                         == {world.actor.location_id, destination} and (not vehicle or entity.village_road)
                         and village.route(world, world.actor.location_id, destination,
                                           road_only=vehicle) == [destination]), None)
            if door is None:
                return blocked("The next physical passage is closed; cargo remains with the carrier.")
            result = act("go", target=door)
            if result["outcome"] != "APPLIED":
                return blocked(f"Travel stopped: {result['code']}: {result['explanation']}")
            finish_turn()
        if world.actor.location_id != stop.destination:
            return blocked("The carrier has not reached the delivery destination.")
        if (capacity_available(world, stop.destination) is not None
                and capacity_available(world, stop.destination) < stop.quantity):
            return blocked("Destination storage filled before unloading; retain the cargo.")
        if vehicle and (not wagon.hitched or wagon.driver != plan.carrier or wagon.automatic
                        or world.state.entities["wagon"].location != world.actor.location_id
                        or world.state.entities["work-horse"].location != world.actor.location_id):
            return blocked("The manual wagon is no longer at this stop with its driver and horse.")
        result = act("village_unload_wagon" if vehicle else "village_unload_goods")
        if result["outcome"] != "APPLIED":
            return blocked(f"Unloading stopped: {result['code']}: {result['explanation']}")
        if plan.status != "completed":
            finish_turn()
    return {"status": plan.status, "route_id": route_id,
            "completed_stops": len(plan.stops), "reason": "Physical fuel deliveries settled."}


def execute_delivery_step(world: "World", route_id: str) -> tuple[str, str, str, None]:
    """Advance one explicitly requested physical route step within the current world action."""
    from npc_sim import village_communication, village_services

    def reject(text: str) -> tuple[str, str, str, None]:
        return "REJECTED", "FUEL_ROUTE_BLOCKED", text, None

    if not enabled(world):
        return reject("Firewood delivery is not enabled.")
    state = world.state.village
    plan = next((item for item in state.firewood.delivery_plans if item.route_id == route_id), None)
    if plan is None:
        candidate = delivery_plan(world)
        if candidate is None or candidate.route_id != route_id:
            return reject("Choose a feasible fuel route owned by the supplier.")
        plan = candidate
    if (plan.carrier != world.actor_id or plan.carrier != state.firewood.config.supplier
            or plan.status in {"completed", "cancelled"}):
        return reject("Choose an open fuel route owned by the supplier.")
    if not state.economy or not plan.stops or plan.load_plan != {
            "firewood": sum(stop.quantity for stop in plan.stops)} or any(
            stop.quantity <= 0 for stop in plan.stops) or len({stop.order_id for stop in plan.stops}) != len(plan.stops) or len({
            stop.destination for stop in plan.stops}) != len(plan.stops):
        return reject("The saved fuel route is invalid.")
    source = world.scenario.village.residents[plan.carrier].work
    vehicle = plan.vehicle_id == "wagon"
    wagon = state.wagon
    if plan.vehicle_id not in {None, "wagon"}:
        return reject("The saved vehicle is invalid.")
    if vehicle and (not wagon or wagon.driver != plan.carrier or not wagon.hitched or wagon.automatic
                    or world.state.entities["wagon"].location != world.actor.location_id
                    or world.state.entities["work-horse"].location != world.actor.location_id):
        return reject("The manual wagon and hitched horse must accompany their driver.")
    if not vehicle and wagon and wagon.driver == plan.carrier:
        return reject("Park the wagon before following the foot route.")
    shipments = wagon.loads if vehicle else state.residents[plan.carrier].shipments
    pending = []
    for stop in plan.stops:
        order = state.economy.orders.get(stop.order_id)
        if not order or order.product != "firewood" or order.source != source or order.supplier != plan.carrier or order.destination != stop.destination:
            return reject("A saved fuel order is no longer valid.")
        delivered = order.delivered_quantity - stop.delivered_before
        if delivered == stop.quantity:
            continue
        cargo = [item for item in shipments if item.order_id == stop.order_id]
        if (delivered != 0 or (not cargo and (order.status not in village_economy.COMMITTED
                or not village_communication.can_use_order(world, order)
                or state.minute > order.deadline))):
            return reject("An accepted, known fuel order has changed or expired.")
        if cargo and (sum(item.quantity for item in cargo) != stop.quantity or any(
                item.resource != "firewood" or item.destination != stop.destination for item in cargo)):
            return reject("Existing cargo does not match the saved fuel order.")
        pending.append((stop, order, bool(cargo)))
    if not pending:
        plan.status = "completed"
        plan.remaining_inventory = 0
        return "APPLIED", "FUEL_ROUTE_COMPLETED", "All saved fuel orders were delivered.", None
    if any(not loaded for _, _, loaded in pending):
        if world.actor.location_id != source or plan.status not in {"planned", "loaded"}:
            return reject("Return to the woodlot with the route cargo before loading.")
        capacity = (6 if vehicle else village_services.foot_capacity(world, plan.carrier)) - sum(
            item.quantity for item in shipments)
        needed = sum(stop.quantity for stop, _, loaded in pending if not loaded)
        if (capacity < needed or village_economy.usable_stock(world, source, "firewood", state.minute)
                - village_economy.policy_reserve(world, source, "firewood") < needed):
            return reject("Real unreserved fuel or carrying capacity no longer covers the route.")
        location, arrival = source, state.minute
        for stop, order, _ in pending:
            path = village.route(world, location, stop.destination, road_only=vehicle)
            if path is None or path != stop.path or arrival + 5 * (1 + len(path) + 1) > order.deadline:
                return reject("The saved route cannot meet the confirmed order's deadline.")
            available = capacity_available(world, stop.destination)
            if available is not None and available < stop.quantity:
                return reject("Destination fuel storage is full.")
            arrival += 5 * (1 + len(path) + 1)
            location = stop.destination
        stop, order, _ = next((stop, order, loaded) for stop, order, loaded in pending if not loaded)
        if village_economy.loadable_for_order(world, order) < stop.quantity:
            return reject("Confirmed funded demand no longer covers this load.")
        fresh = plan not in state.firewood.delivery_plans
        if fresh:
            state.firewood.delivery_plans.append(plan)
        if vehicle:
            from npc_sim import village_wagon
            outcome = village_wagon.apply(world, "village_load_wagon", {
                "goods": {"firewood": stop.quantity}, "destination": stop.destination})
        else:
            outcome = village_services.apply(world, "village_load_goods", {
                "goods": {"firewood": stop.quantity}, "destination": stop.destination})
        if outcome[0] != "APPLIED":
            if fresh:
                state.firewood.delivery_plans.remove(plan)
            return outcome
        plan.status = "loaded"
        return "APPLIED", "FUEL_ROUTE_LOADED", f"Loaded {stop.quantity} real firewood for {stop.destination}.", None
    stop, order, _ = pending[0]
    path = village.route(world, world.actor.location_id, stop.destination, road_only=vehicle)
    if path is None:
        return reject(f"edge_unavailable: No legal passage from {world.actor.location_id} to {stop.destination}; cargo remains with the carrier.")
    if world.actor.location_id != stop.destination:
        next_location = path[0]
        door = next((eid for eid, entity in world.state.entities.items()
                     if entity.kind == "door" and {entity.location, entity.destination}
                     == {world.actor.location_id, next_location} and (not vehicle or entity.village_road)
                     and village.route(world, world.actor.location_id, next_location,
                                       road_only=vehicle) == [next_location]), None)
        if door is None:
            return reject(f"edge_unavailable: Passage {world.actor.location_id} -> {next_location} is closed; cargo remains with the carrier.")
        origin = world.actor.location_id
        outcome = world._apply("go", {"target": door})
        if outcome[0] != "APPLIED":
            return outcome
        from npc_sim import village_fallback
        village_fallback.record_location_transition(world, origin, next_location)
        person = state.residents[plan.carrier]
        if door not in person.known_routes:
            person.known_routes.append(door)
        if vehicle:
            world.state.entities["wagon"].location = next_location
            world.state.entities["work-horse"].location = next_location
        elif state.horse_leader == plan.carrier:
            world.state.entities["work-horse"].location = next_location
        return "APPLIED", "FUEL_ROUTE_TRAVEL", f"Walked through {door} to {next_location} with real cargo.", None
    available = capacity_available(world, stop.destination)
    if available is not None and available < stop.quantity:
        return reject("Destination fuel storage is full.")
    if any(item.destination == stop.destination and item.order_id != stop.order_id
           for item in shipments):
        return reject("Other cargo for this destination must be settled separately.")
    if vehicle:
        from npc_sim import village_wagon
        outcome = village_wagon.apply(world, "village_unload_wagon", {})
    else:
        outcome = village_services.apply(world, "village_unload_goods", {})
    if outcome[0] != "APPLIED":
        return outcome
    sync_delivery_plans(world, plan.carrier, destination=stop.destination)
    return "APPLIED", "FUEL_ROUTE_DELIVERED", f"Delivered real firewood at {stop.destination}; actual payment settled.", None


def respond_late_delivery(world: "World", order_id: str, accept: bool) -> tuple[str, str, str, None]:
    """Record an explicit, local buyer decision for already-loaded overdue firewood."""
    state = world.state.village
    if not enabled(world) or state.economy is None:
        return "REJECTED", "FUEL_DISABLED", "Firewood orders are not available.", None
    if type(accept) is not bool:
        return "REJECTED", "INVALID_RESPONSE", "Accept or reject the late delivery explicitly.", None
    order = state.economy.orders.get(order_id)
    if not order or order.product != "firewood" or order.buyer != world.actor_id:
        return "REJECTED", "UNKNOWN_FUEL_ORDER", "Only the named buyer may decide on their fuel order.", None
    if world.actor.location_id != order.destination:
        return "REJECTED", "BUYER_NOT_AT_SITE", "Decide on late firewood at your actual destination.", None
    if state.minute <= order.deadline or order.status == "fulfilled":
        return "REJECTED", "NOT_LATE_DELIVERY", "Only overdue, undelivered cargo needs a buyer response.", None
    if not (order.status in village_economy.COMMITTED or
            order.status == "failed" and order.reason == "MISSED_DEADLINE"):
        return "REJECTED", "ORDER_CLOSED", "This order is not awaiting overdue delivery.", None
    carrier = state.residents[order.supplier]
    loads = [*carrier.shipments, *(state.wagon.loads if state.wagon else [])]
    if not any(item.order_id == order.id and item.resource == "firewood"
               and item.destination == order.destination and item.quantity > 0 for item in loads):
        return "REJECTED", "NO_LATE_CARGO", "No loaded fuel remains for this order.", None
    key = f"late:{order.id}"
    decision = "accepted" if accept else "rejected"
    previous = state.firewood.statuses.get(key)
    if previous:
        if previous != decision:
            return "REJECTED", "DECISION_FINAL", "The buyer already decided on this late shipment.", None
        return "APPLIED", "LATE_FUEL_DECISION_EXISTS", "The buyer's late-delivery decision is unchanged.", None
    state.firewood.statuses[key] = decision
    village.event(world, order.buyer, "late_fuel_decision", order.id,
                  f"Buyer {decision} overdue firewood; unpaid cargo remains physical until unload or return.",
                  order.destination)
    return "APPLIED", "LATE_FUEL_ACCEPTED" if accept else "LATE_FUEL_REJECTED", \
        f"Buyer {decision} overdue firewood; cargo remains with its carrier.", None


def sync_delivery_plans(world: "World", carrier: str, *, destination: str | None = None) -> None:
    if not enabled(world):
        return
    state = world.state.village
    for plan in state.firewood.delivery_plans:
        if plan.carrier != carrier or plan.status in {"completed", "cancelled"}:
            continue
        cargo = [*state.residents[carrier].shipments, *(state.wagon.loads if state.wagon else [])]
        if destination is None:
            if any(item.resource == "firewood" and item.order_id in {stop.order_id for stop in plan.stops}
                   for item in cargo):
                plan.status = "loaded"
        else:
            outstanding = sum(max(0, stop.quantity - max(0,
                              state.economy.orders[stop.order_id].delivered_quantity - stop.delivered_before))
                              for stop in plan.stops)
            plan.remaining_inventory = outstanding
            if outstanding == 0:
                plan.status = "completed"
            elif outstanding < plan.load_plan["firewood"]:
                plan.status = "in_progress"


def fuel_required(world: "World", action: str, args: dict[str, Any] | None = None) -> bool:
    if not enabled(world):
        return False
    base, state = village.context(world)
    config = state.firewood.config
    if world.actor.location_id not in {base.residents[rid].work for rid in config.businesses}:
        return False
    if action in HOT_ACTIONS:
        return True
    if action not in {"village_research", "village_research_make", "village_research_use"} or not args or not state.research:
        return False
    project = state.research.projects.get(args.get("project"))
    if not project:
        return False
    if action == "village_research_use":
        return project.proposal.kind in {"food", "crop"}
    if action == "village_research" and project.trial_materials_consumed:
        return False
    return bool(set(project.proposal.equipment) & {"anvil", "oven", "tavern-hearth", "smelting-furnace"})


def action_blocker(world: "World", action: str, args: dict[str, Any] | None = None,
                   *, personal: bool = False) -> tuple[str, str] | None:
    if not enabled(world):
        return None
    if fuel_required(world, action, args) and village_economy.usable_stock(
            world, world.actor.location_id, "firewood", world.state.village.minute) < 1:
        return "FIREWOOD_REQUIRED", "This hot batch needs one real local firewood unit; arrange a funded physical delivery."
    if action != SPLIT:
        return None
    job = production_job(world)
    if job is None or world.actor.location_id != job.location:
        return None
    axe = world.state.entities[AXE]
    if axe.hidden or (AXE not in world.visible_ids() and AXE not in world.actor.inventory):
        return "FIREWOOD_AXE_REQUIRED", "Bring the actual wood axe to the woodlot before splitting."
    targets = village_economy.production_targets(world, job.location, personal=personal)
    protected = village_economy.protected_quantity(world, job.location, "logs") + _timber_logs(world, targets)
    if village_economy.usable_stock(world, job.location, "logs", world.state.village.minute) - 1 < protected:
        return "RAW_MATERIAL_RESERVE", "Preserve actual log reserves and the logs needed for promised construction timber; fell more logs first."
    if capacity_available(world, job.location) < job.outputs["firewood"] + _committed(world, job.location, "firewood"):
        return "FIREWOOD_STORAGE_FULL", "The real woodlot fuel stack cannot hold another split batch."
    from npc_sim import village_extensions
    base, state = village.context(world)
    resident = base.residents[world.actor_id]
    day = state.minute // 1440 * 1440
    obligations = [(day + 1140, "tavern"), (day + resident.bed_minute, resident.home)]
    if village_extensions.policy(world).breakfast:
        obligations.append((day + resident.wake_minute + village_extensions.policy(world).breakfast_delay, "tavern"))
    for due, destination in obligations:
        route = village.route(world, job.location, destination)
        if state.minute < due and (route is None or state.minute + job.duration + len(route) * 5 + 5 > due):
            return "COMMITMENT_DUE", "Leave time to finish splitting and physically walk to the next meal or bedtime."
    return None


def _consume(world: "World", location: str) -> None:
    village_goods.take(world, location, "firewood", 1)
    state = world.state.village.firewood
    state.consumed[location] = state.consumed.get(location, 0) + 1


def _burn_event(world: "World", location: str, activity: str, quantity: int, *, actor: str | None = None) -> None:
    index = village.event(world, actor or world.actor_id, "firewood_burned", location,
                          f"Consumed {quantity} real firewood for {activity}; "
                          "no additional log fuel charged.", location=location)
    world.state.village.events[index].details = {
        "site": location, "activity_id": activity, "quantity": quantity,
        "remaining_stock": world.state.village.stocks[location]["firewood"],
        "time": world.state.village.minute,
    }


def record_action(world: "World", action: str, already_consumed: int) -> None:
    if already_consumed:
        state = world.state.village.firewood
        location = world.actor.location_id
        state.consumed[location] = state.consumed.get(location, 0) + already_consumed
    else:
        _consume(world, world.actor.location_id)
    _burn_event(world, world.actor.location_id, action, already_consumed or 1)


def _hearth(world: "World", key: str, members: list[str]) -> dict[str, Any]:
    state = world.state.village
    location = _household_location(world)
    occupied = any(village.resident_location(world, rid) == location for rid in members)
    warm_until = state.firewood.warm_until.get(key, 0)
    status = ("not_needed" if not _heating_now(world) else "unoccupied" if not occupied
              else "warm" if warm_until > state.minute else "cold")
    return {"id": key, "location": location, "members": members, "occupied": occupied,
            "status": status, "warm_until": warm_until}


def advance(world: "World") -> None:
    if not enabled(world):
        return
    state = world.state.village
    config = state.firewood.config
    location = _household_location(world)
    for key, members in config.households.items():
        hearth = _hearth(world, key, members)
        fixture = world.state.entities[f"cottage-{key}"]
        if (hearth["status"] == "cold" and fixture.location == location and not fixture.broken
                and village_economy.usable_stock(world, location, "firewood", state.minute) >= 1):
            _consume(world, location)
            _burn_event(world, location, f"cottage-{key}", 1, actor=config.household_buyer)
            state.firewood.warm_until[key] = min(state.minute + _burn_minutes(world), _heating_end(state.minute))
            hearth = _hearth(world, key, members)
        status = hearth["status"]
        if status != state.firewood.statuses.get(key) and status in {"cold", "warm"}:
            village.event(world, "hearth", f"household_{status}", f"cottage-{key}",
                          f"Shared cottage {key}: {status}. "
                          + ("No fuel; cold is reported without health penalties." if status == "cold"
                             else "One real firewood consumed for this household, not per resident."),
                          location=location)
        state.firewood.statuses[key] = status


def production_potential(world: "World", work_end: int) -> int:
    """Conservative capacity for a promise, including timed felling of renewable logs."""
    job = production_job(world)
    if job is None:
        return 0
    axe = world.state.entities[AXE]
    if axe.broken or axe.hidden or (axe.location != job.location and AXE not in world.actor.inventory):
        return 0
    if not {"forestry", "manipulate"} <= set(world.actor.body.capabilities) or handling.use_reason(world, [AXE]):
        return 0
    from npc_sim import village_life
    state = world.state.village
    person = state.residents[world.actor_id]
    start = max(state.minute, person.next_work, person.available_at)
    if person.life and person.life.work:
        start = max(start, person.life.work.finishes_at)
    factor = village_life.cooldown_factor(world, world.actor_id)
    logs = max(0, village_economy.usable_stock(world, job.location, "logs", state.minute)
               - village_economy.protected_quantity(world, job.location, "logs"))
    duration = max(job.duration, job.interval * factor)
    gathering = world.scenario.village.residents[world.actor_id].job.interval * factor
    produced = 0
    while start + duration <= work_end:
        if not logs:
            start += max(5, gathering)
            logs += 2
        else:
            start += duration
            logs -= 1
            produced += world.state.village.firewood.config.firewood_per_log
    return produced


def equipment_reserved(world: "World", tools: set[str]) -> bool:
    state = world.state.village
    return bool(AXE in tools and state and any(
        person.life and person.life.work and person.life.work.equipment == AXE
        and "firewood" in person.life.work.outputs for person in state.residents.values()))


def actions(world: "World") -> list[dict[str, Any]]:
    job = production_job(world)
    if job is None:
        return []
    return [{"id": SPLIT, "description": f"Split one uncommitted log into {job.outputs['firewood']} firewood "
             f"at the woodlot using the axe and {job.duration} minutes of committed work.",
             "targets": [], "arguments_schema": {"type": "object", "properties": {}, "additionalProperties": False},
             "availability": "available", "reason": None}]


def _view(world: "World", *, local: bool) -> dict[str, Any]:
    config = world.state.village.firewood.config
    base, state = village.context(world)
    buyers = list(dict.fromkeys([*config.businesses, config.household_buyer]))
    sites = [{"location": base.residents[buyer].work, "buyer": buyer,
              "stock": (stock := village_economy.usable_stock(
                  world, base.residents[buyer].work, "firewood", state.minute)),
              "reserve": consumer_target(world, buyer),
              "capacity": config.site_capacities[base.residents[buyer].work],
              "status": "out" if stock == 0 else "low" if stock <= config.reorder_point else "normal",
              "consumed": state.firewood.consumed.get(base.residents[buyer].work, 0)}
             for buyer in buyers if not local or base.residents[buyer].work == world.actor.location_id]
    households = [_hearth(world, key, members) for key, members in config.households.items()
                  if not local or _household_location(world) == world.actor.location_id]
    return {"enabled": config.enabled, "season": config.season, "supplier": config.supplier,
            "firewood_per_log": config.firewood_per_log, "split_minutes": config.split_minutes,
            "sites": sites, "households": households, "policy": POLICY}


def observation(world: "World") -> dict[str, Any]:
    return {"firewood": _view(world, local=True)}


def monitor(world: "World") -> dict[str, Any]:
    return {"firewood": _view(world, local=False)}

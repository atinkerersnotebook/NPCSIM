"""Small recoverable incidents and physically present, finite-budget visitors."""

import random
from collections import deque
from typing import TYPE_CHECKING, Any

from npc_sim import village, village_economy, village_goods
from npc_sim.models import VillageBelief, VillageStockLot
from npc_sim.village_events_models import (
    EventTemplate, IncidentState, MerchantProfile, TradeRecord, VillageEventsConfig, VillageEventsState,
    VisitorInterval, VisitorLot, VisitorPlan, VisitorState,
)

if TYPE_CHECKING:
    from npc_sim.models import Scenario
    from npc_sim.world import World


VERBS = {"village_event_accept", "village_event_work", "village_event_deliver",
         "village_event_discover", "village_event_decline", "village_merchant_trade",
         "village_visitor_meal", "village_visitor_lodging"}
DELAYS = {"rain", "equipment", "poor_fishing", "band_absence"}


def _path(entities: dict, start: str, destination: str) -> list[str] | None:
    queue = deque([(start, [])])
    seen = {start}
    while queue:
        node, path = queue.popleft()
        if node == destination:
            return path
        for door in entities.values():
            if (door.kind != "door" or not door.open or door.locked or door.broken
                    or door.traversal_capabilities or door.required_challenges):
                continue
            other = door.destination if door.location == node else door.location if door.destination == node else None
            if other and other not in seen:
                seen.add(other)
                queue.append((other, [*path, other]))
    return None


def _validate(scenario: "Scenario", config: VillageEventsConfig) -> None:
    base = scenario.village
    if base is None:
        raise ValueError("Village events require a village")
    locations = {eid for eid, entity in scenario.entities.items() if entity.kind == "location"}
    resources = set(base.prices) | {r for stocks in base.stocks.values() for r in stocks}
    for item in config.templates:
        if item.location not in locations or set(item.residents) - set(base.residents):
            raise ValueError(f"{item.id}: unknown location or resident")
        used = set(item.inputs) | set(item.outputs) | set(item.demand)
        if item.condition:
            used.add(item.condition.resource)
            if item.condition.location not in locations:
                raise ValueError(f"{item.id}: unknown condition location")
        if used - resources:
            raise ValueError(f"{item.id}: unknown resource")
        for eid in (item.discovery, item.equipment):
            if eid is not None and eid not in scenario.entities:
                raise ValueError(f"{item.id}: unknown entity")
        if item.visitor:
            plan = item.visitor
            places = [plan.entry, plan.destination, plan.departure, *plan.visits,
                      *([plan.meal_location] if plan.meal_location else []),
                      *([plan.lodging_location] if plan.lodging_location else [])]
            if set(places) - locations:
                raise ValueError(f"{item.id}: unknown visitor location")
            if any(_path(scenario.entities, left, right) is None
                   for left in places for right in places):
                raise ValueError(f"{item.id}: visitor itinerary is unreachable")
            if len(_path(scenario.entities, plan.entry, plan.destination) or []) * 5 >= plan.stay_minutes:
                raise ValueError(f"{item.id}: visitor leaves before arrival")
            if plan.stay_minutes > 720 and (not plan.meal_location or not plan.lodging_location):
                raise ValueError("Long visits require meal and lodging locations")
            profile = plan.merchant
            if profile:
                if (set(profile.stock) | set(profile.demand) | set(profile.buy_prices)
                        | set(profile.sell_prices)) - resources:
                    raise ValueError(f"{item.id}: unknown merchant product")
        if item.kind == "equipment" and scenario.entities[item.equipment].kind == "location":
            raise ValueError("An equipment delay must target equipment, not a location")
        if item.demand and not item.visitor:
            raise ValueError("Paid demand requires a funded visitor")


def validate_config(scenario: "Scenario") -> None:
    config = getattr(scenario.village, "events", None)
    if config is not None:
        _validate(scenario, config)


def initial_state(scenario: "Scenario") -> VillageEventsState | None:
    config = getattr(scenario.village, "events", None)
    if config is None:
        return None
    _validate(scenario, config)
    return VillageEventsState(config=config.model_copy(deep=True))


def _context(world: "World") -> VillageEventsState | None:
    if world.state.village is None:
        return None
    return getattr(world.state.village, "incidents", None)


def configure(world: "World", config: dict[str, Any]) -> dict[str, Any]:
    """Privileged DM entry point; deliberately not a resident action."""
    parsed = VillageEventsConfig.model_validate(config)
    _validate(world.scenario, parsed)
    state = world.state.village
    if state is None:
        raise ValueError("Events require village state")
    incidents = _context(world)
    if incidents is None:
        incidents = VillageEventsState(config=parsed)
        state.incidents = incidents
    else:
        incidents.config = parsed
    incidents.revision += 1
    incidents.configuration_history.append({"minute": state.minute, "revision": incidents.revision,
                                            "config": parsed.model_dump()})
    village.event(world, "dm", "events_configured", "village",
                  f"DM configured incident revision {incidents.revision}.", "village-square")
    return incidents.model_dump()


def _log(world: "World", incident: IncidentState, actor: str, verb: str, text: str) -> None:
    index = village.event(world, actor, verb, incident.id, text, incident.template.location)
    incident.evidence.append(index)


def _event_waiting(person: Any) -> bool:
    return person.activity == "wait_event" or person.activity.startswith("Waiting for the next scheduled event at ")


def _wake_waiters(world: "World", location: str) -> None:
    _, state = village.context(world)
    for rid in village.local_residents(world, location):
        person = state.residents[rid]
        if (not _event_waiting(person) or person.timber_route
                or (person.life and (person.life.sleeping or person.life.work))
                or (state.wagon and state.wagon.automatic and state.wagon.driver == rid)
                or person.busy_until > state.minute):
            continue
        person.available_at = min(person.available_at, state.minute)


def _safe(world: "World", item: EventTemplate) -> bool:
    base, state = village.context(world)
    minute = state.minute
    if not item.window_start <= minute % 1440 < min(item.window_end, 1080):
        return False
    affected = item.residents or village.local_residents(world, item.location)
    affected = affected[:3]
    incidents = _context(world)
    for rid in affected:
        person, resident = state.residents[rid], base.residents[rid]
        if (minute % 1440 < max(540, resident.wake_minute)
                or minute % 1440 + item.duration >= min(1080, resident.bed_minute - 30)
                or person.busy_until > minute
                or (person.available_at > minute and not _event_waiting(person))
                or (person.life and (person.life.sleeping or person.life.work or person.life.hungry))):
            return False
        if base.expanded_rules and rid == base.helper and state.horse_fed_day != minute // 1440:
            return False
        if incidents and any(old.status == "active" and (
            rid in old.template.residents or old.template.location == item.location)
                for old in incidents.instances.values()):
            return False
    if item.visitor and village.route(world, item.visitor.entry, item.visitor.destination) is None:
        return False
    return True


def _activate(world: "World", item: EventTemplate) -> None:
    incidents = _context(world)
    _, state = village.context(world)
    assert incidents is not None
    iid = f"{item.id}:{state.minute}"
    if iid in incidents.instances:
        return
    template = item.model_copy(deep=True)
    if not template.residents:
        template.residents = village.local_residents(world, item.location)[:3]
    instance = IncidentState(id=iid, template=template, activated_at=state.minute,
                             expires_at=state.minute + item.duration,
                             witnesses=village.local_residents(world, item.location))
    incidents.instances[iid] = instance
    incidents.last_activation[item.id] = state.minute
    day = str(state.minute // 1440)
    incidents.daily_counts[day] = incidents.daily_counts.get(day, 0) + 1
    if item.scheduled_at is not None:
        incidents.scheduled_fired.append(item.id)
    _log(world, instance, "incident", "event_activated", item.title)
    _wake_waiters(world, item.location)
    if item.visitor:
        plan = item.visitor.model_copy(deep=True)
        profile = plan.merchant
        visitor = VisitorState(
            id=f"visitor:{iid}", incident=iid, name=plan.name, plan=plan,
            location=plan.entry, leave_at=state.minute + plan.stay_minutes,
            next_step_at=state.minute + 5,
            path=village.route(world, plan.entry, plan.destination) or [],
            coins=profile.funds if profile else plan.funds,
            stock=profile.stock.copy() if profile else {},
        )
        for resource, amount in visitor.stock.items():
            if amount:
                shelf = world.scenario.village.spoil_after.get(resource)
                visitor.stock_lots[resource] = [VisitorLot(
                    quantity=amount, produced_at=state.minute,
                    expires_at=state.minute + shelf if shelf else None)]
        visitor.timeline.append(VisitorInterval(label="Arrival travel", location=visitor.location,
                                                start=state.minute, end=state.minute))
        incidents.visitors[visitor.id] = visitor
        instance.expires_at = visitor.leave_at


def _interval(visitor: VisitorState, label: str, location: str, start: int, end: int) -> None:
    if visitor.timeline and visitor.timeline[-1].label == label and visitor.timeline[-1].location == location:
        visitor.timeline[-1].end = max(visitor.timeline[-1].end, end)
    else:
        visitor.timeline.append(VisitorInterval(label=label, location=location, start=start, end=end))


def _visitor_advance(world: "World", visitor: VisitorState) -> None:
    _, state = village.context(world)
    incidents = _context(world)
    assert incidents is not None
    now = state.minute
    if visitor.status == "departed":
        return
    for resource, lots in visitor.stock_lots.items():
        expired = [lot for lot in lots if lot.expires_at is not None and lot.expires_at <= now]
        amount = sum(lot.quantity for lot in expired)
        visitor.stock[resource] -= amount
        if amount:
            visitor.spoiled[resource] = visitor.spoiled.get(resource, 0) + amount
            village.event(world, visitor.id, "visitor_stock_spoiled", resource,
                          f"{amount} {resource} spoiled in {visitor.name}'s real cargo.", visitor.location)
        visitor.stock_lots[resource] = [lot for lot in lots if lot not in expired]
    while visitor.next_step_at <= now:
        tick = visitor.next_step_at
        visitor.next_step_at += 5
        if tick >= visitor.leave_at and visitor.status != "departing":
            route = village.route(world, visitor.location, visitor.plan.departure)
            if route is None:
                visitor.activity = "Departure blocked; still physically present"
                continue
            visitor.status, visitor.path = "departing", route
        if visitor.path:
            origin = visitor.location
            next_location = visitor.path[0]
            route = village.route(world, origin, next_location)
            if route != [next_location]:
                visitor.activity = "Travel blocked; waiting for a safe route"
                continue
            visitor.path.pop(0)
            visitor.location = next_location
            visitor.activity = "Departure travel" if visitor.status == "departing" else "Travel"
            _interval(visitor, visitor.activity, origin, tick - 5, tick)
            if not visitor.path and visitor.status == "present":
                visitor.next_visit_at = max(visitor.next_visit_at, tick + 15)
                visitor.activity = "Trading" if visitor.plan.merchant else "Visiting"
        if visitor.status == "arriving" and not visitor.path:
            visitor.status, visitor.arrived_at = "present", tick
            visitor.next_visit_at = tick + 20
            visitor.activity = "Trading" if visitor.plan.merchant else "Visiting"
            village.event(world, visitor.id, "visitor_arrived", visitor.id,
                          f"{visitor.name} arrived with recorded funds from {visitor.plan.source}.", visitor.location)
            _wake_waiters(world, visitor.location)
        elif visitor.status == "departing" and not visitor.path:
            visitor.status, visitor.departed_at = "departed", tick
            profile = visitor.plan.merchant
            if profile:
                visitor.unsatisfied = {resource: amount - visitor.purchased.get(resource, 0)
                                       for resource, amount in profile.demand.items()
                                       if amount > visitor.purchased.get(resource, 0)}
            instance = incidents.instances[visitor.incident]
            for resource, amount in instance.template.demand.items():
                outstanding = amount - instance.fulfilled.get(resource, 0)
                if outstanding > 0:
                    visitor.unsatisfied[resource] = visitor.unsatisfied.get(resource, 0) + outstanding
            if instance.status == "active":
                instance.status, instance.resolved_at = "expired", tick
                instance.outcome = "Visitor departed; unfulfilled demand expired"
            village.event(world, visitor.id, "visitor_departed", visitor.id,
                          f"{visitor.name} physically departed; unmet purchases: {visitor.unsatisfied}.",
                          visitor.location)
            return
        if visitor.status == "present":
            day, clock = divmod(tick, 1440)
            if clock >= 1200 and day not in visitor.meals and day not in visitor.missed_meals:
                visitor.missed_meals.append(day)
                village.event(world, visitor.id, "visitor_missed_meal", visitor.id,
                              f"{visitor.name} has not been supplied a meal.", visitor.location)
            target = (visitor.plan.lodging_location if clock >= 1260 or clock < 480
                      else visitor.plan.meal_location if 1080 <= clock < 1260 and day not in visitor.meals
                      else visitor.plan.destination)
            if 480 <= clock < 1080 and visitor.plan.visits:
                if visitor.visit_index and visitor.visit_index <= len(visitor.plan.visits):
                    target = visitor.plan.visits[visitor.visit_index - 1]
                if (visitor.next_visit_at <= tick and not visitor.path
                        and visitor.visit_index < len(visitor.plan.visits)):
                    target = visitor.plan.visits[visitor.visit_index]
                    visitor.visit_index += 1
                    visitor.next_visit_at = tick + 20
            if target and target != visitor.location and not visitor.path:
                visitor.path = village.route(world, visitor.location, target) or []
            lodging_day = day - 1 if clock < 480 else day
            label = ("Lodging" if (clock >= 1260 or clock < 480)
                     and lodging_day in visitor.lodging_days else visitor.activity)
            _interval(visitor, label, visitor.location, tick, tick + 5)


def advance(world: "World") -> None:
    incidents = _context(world)
    if incidents is None:
        return
    _, state = village.context(world)
    for visitor in incidents.visitors.values():
        _visitor_advance(world, visitor)
    for instance in incidents.instances.values():
        if instance.status == "active" and state.minute >= instance.expires_at:
            instance.status = "resolved" if instance.template.kind in DELAYS else "expired"
            instance.resolved_at = instance.expires_at
            instance.outcome = ("Temporary delay ended" if instance.template.kind in DELAYS
                                else "Unfulfilled optional task expired")
            _log(world, instance, "incident", "event_ended", instance.outcome)
    config = incidents.config
    if not config.enabled:
        return
    day = state.minute // 1440
    key = str(day)
    rng = random.Random(f"{config.seed}:{day}")
    if key not in incidents.daily_targets:
        incidents.daily_targets[key] = rng.randint(config.daily_min, config.daily_max)
    active = sum(old.status == "active" for old in incidents.instances.values())
    if active >= config.max_active or incidents.daily_counts.get(key, 0) >= config.daily_max:
        return
    candidates = config.templates.copy()
    rng.shuffle(candidates)
    for item in candidates:
        if not item.enabled or state.minute - incidents.last_activation.get(item.id, -10**9) < item.cooldown:
            continue
        scheduled = item.scheduled_at is not None and item.id not in incidents.scheduled_fired and state.minute >= item.scheduled_at
        conditional = bool(item.condition and state.stocks.get(item.condition.location, {}).get(
            item.condition.resource, 0) < item.condition.below)
        random_due = (item.random and item.scheduled_at is None and item.condition is None
                      and incidents.daily_counts.get(key, 0) < incidents.daily_targets[key]
                      and state.minute % 1440 >= item.window_start + rng.randrange(
                          max(1, item.window_end - item.window_start - item.duration)))
        if (scheduled or conditional or random_due) and _safe(world, item):
            _activate(world, item)
            break


def delay_until(world: "World", rid: str, location: str, equipment: str | None = None,
                *, kinds: set[str] | None = None) -> int:
    """Return an absolute end minute, or zero; does not block movement or reserve time."""
    incidents = _context(world)
    if incidents is None:
        return 0
    return max([0, *(old.expires_at for old in incidents.instances.values()
                     if old.status == "active" and old.template.kind in DELAYS
                     and (kinds is None or old.template.kind in kinds)
                     and old.expires_at > world.state.village.minute
                     and old.template.location == location
                     and (not old.template.residents or rid in old.template.residents)
                     and (old.template.kind != "equipment" or old.template.equipment == equipment))])


def action_blocker(world: "World", action: str) -> tuple[str, str] | None:
    """Block only relevant optional starts, never movement, meals, sleep or animal care."""
    incidents = _context(world)
    if incidents is None or world.actor_id not in world.state.village.residents:
        return None
    base, state = village.context(world)
    rid, location = world.actor_id, world.actor.location_id
    resident = base.residents[rid]
    outdoors = location in {"farm", "woods", "bridge", "riverbank", "well-site", "tavern-yard", "village-square"}
    work = {"village_work", "village_cook_fish", "village_grow_hay", "village_gather_herbs",
            "village_fell_logs", "village_fertilize", "village_patrol", "village_project"}
    loading = {"village_load", "village_deliver", "village_load_goods", "village_unload_goods",
               "village_load_wagon", "village_unload_wagon"}
    leisure = {"village_leisure", "village_band_attend"}
    kinds: set[str] = set()
    equipment = None
    if action in work:
        equipment = resident.job.equipment if resident.job else None
        if action in {"village_grow_hay", "village_gather_herbs", "village_fell_logs"}:
            from npc_sim.village_services import JOBS
            equipment = JOBS[action][2]
        kinds.add("equipment")
    if outdoors and action in work | loading | leisure:
        kinds.add("rain")
    if action == "village_work" and "fishing" in resident.skills:
        kinds.add("poor_fishing")
    if action == "village_band_attend":
        kinds.add("band_absence")
    if not kinds:
        return None
    until = delay_until(world, rid, location, equipment, kinds=kinds)
    if until <= state.minute:
        return None
    return ("INCIDENT_DELAY",
            f"This optional activity is delayed until {village.clock_time(until)}. "
            "Choose other work or wait; walking, meals, sleep and animal care remain available.")


def observation(world: "World") -> dict[str, Any]:
    incidents = _context(world)
    if incidents is None:
        return {}
    location, rid = world.actor.location_id, world.actor_id
    return {"incidents": [old.model_dump() for old in incidents.instances.values()
                          if old.template.location == location or rid in old.witnesses
                          or old.accepted_by == rid],
            "visitors": [visitor.model_dump() for visitor in incidents.visitors.values()
                         if visitor.status != "departed" and visitor.location == location],
            "incident_configuration_revision": incidents.revision}


def monitor(world: "World") -> dict[str, Any]:
    incidents = _context(world)
    if incidents is None:
        return {"visitors": [], "incidents": []}
    return {
        "visitors": [{
            "id": visitor.id, "name": visitor.name, "location": visitor.location,
            "arrival": visitor.arrived_at if visitor.arrived_at is not None else visitor.timeline[0].start,
            "departure": visitor.departed_at, "status": visitor.status,
            "activities": [{"actor": visitor.id, **interval.model_dump(),
                            "status": "completed" if interval.end <= world.state.village.minute else "active"}
                           for interval in visitor.timeline],
        } for visitor in incidents.visitors.values()],
        "incidents": [{
            "id": instance.id, "title": instance.template.title, "location": instance.template.location,
            "start": instance.activated_at, "end": instance.resolved_at or instance.expires_at,
            "status": instance.status,
        } for instance in incidents.instances.values()],
    }


def recommended_config(scenario: "Scenario", *, initial_example: bool = False) -> dict[str, Any]:
    """Build the bounded full catalogue against real scenario references."""
    base = scenario.village
    if base is None:
        raise ValueError("A village is required")
    templates = []

    def add(iid: str, kind: str, title: str, rid: str, **effects: Any) -> None:
        location = effects.pop("location", base.residents[rid].work)
        templates.append(EventTemplate(id=iid, kind=kind, title=title, location=location,
                                       residents=[rid], **effects))

    smith = base.smith
    smith_job = base.residents[smith].job
    smith_equipment = smith_job.equipment if smith_job and smith_job.equipment else base.hammer
    add("passing-traveller", "traveller", "A traveller requests two fresh loaves", "jory",
        demand={"bread": 2}, responses=["deliver", "decline"],
        visitor=VisitorPlan(name="Iris Fen", entry="village-square", destination="bakery",
                            departure="village-square", funds=12, meal_location="tavern"))
    add("rain-shower", "rain", "Brief shower delays outdoor work", "ada")
    add("loose-equipment", "equipment", "A minor equipment adjustment delays forge production", smith,
        equipment=smith_equipment, **({"random": False, "scheduled_at": base.start_minute + 120}
                                    if initial_example else {}))
    add("personal-discovery", "discovery", "A villager can inspect the real shovel", smith,
        location=scenario.entities[base.shovel].location, discovery=base.shovel,
        responses=["discover", "decline"])
    add("animal-sighting", "escaped_animal", "Locate the working horse and report its real position", base.helper,
        location="stable", discovery="work-horse", responses=["discover", "decline"])
    add("poor-fishing", "poor_fishing", "A quiet fishing spell delays the next attempt", "finn")
    add("seasonal-harvest", "harvest", "Gather a small seasonal grain yield", "ada",
        outputs={"grain": 2}, source="Seasonal field patch", intensity=2)
    add("visiting-merchant", "merchant", "A finite-budget merchant offers iron and buys spare parts", smith,
        responses=["decline"], window_end=900, visitor=VisitorPlan(
            name="Tamsin Reed", entry="village-square", destination="forge", departure="village-square",
            visits=["workshop", "tavern", "bakery"], stay_minutes=180,
            merchant=MerchantProfile(funds=24, capacity=10, stock={"iron": 3},
                sell_prices={"iron": 3}, buy_prices={"wagon-parts": 5, "planks": 3, "tea": 2, "bread": 3},
                demand={"wagon-parts": 2, "planks": 2, "tea": 2, "bread": 2},
                journey_minutes=120, resale_minutes=60, source="Tamsin's recorded out-of-town cargo and purse")))
    add("misplaced-tool", "misplaced_tool", "Inspect the joiner's actual hammer position", base.carpenter,
        location=scenario.entities[base.hammer].location, discovery=base.hammer,
        responses=["discover", "decline"])
    add("birthday-tea", "birthday", "Prepare a real birthday cup of tea", "elsie",
        inputs={"tea": 1})
    add("band-unavailable", "band_absence", "A band member is briefly unavailable", "finn")
    add("neighbour-help", "neighbour_help", "Make a neighbour one spare plank", base.carpenter,
        inputs={"timber": 1}, outputs={"planks": 1}, source="Neighbour's timber worked into a plank", intensity=2)
    config = VillageEventsConfig(seed=17, daily_min=0, daily_max=2, max_active=2, templates=templates)
    _validate(scenario, config)
    return config.model_dump()


def actions(world: "World") -> list[dict[str, Any]]:
    incidents = _context(world)
    if incidents is None:
        return []
    result = []

    def add(verb: str, description: str, properties: dict) -> None:
        result.append({"id": verb, "description": description, "targets": [],
                       "arguments_schema": {"type": "object", "properties": properties,
                                            "required": list(properties), "additionalProperties": False},
                       "availability": "available", "reason": None})

    local = [old for old in incidents.instances.values() if old.status == "active"
             and old.template.location == world.actor.location_id
             and (not old.template.residents or world.actor_id in old.template.residents)]
    for suffix, response in [("accept", None), ("work", "work"), ("deliver", "deliver"),
                             ("discover", "discover"), ("decline", "decline")]:
        ids = [old.id for old in local if response is None or response in old.template.responses]
        if ids:
            properties = {"incident": {"type": "string", "enum": ids}}
            if suffix == "deliver":
                properties |= {"resource": {"type": "string", "enum": sorted(
                    {resource for old in local for resource in old.template.demand})},
                               "quantity": {"type": "integer", "minimum": 1, "maximum": 6}}
            add(f"village_event_{suffix}", "Respond locally to a bounded optional incident.", properties)
    visitors = [visitor for visitor in incidents.visitors.values()
                if visitor.status == "present" and visitor.location == world.actor.location_id]
    if visitors:
        ids = {"type": "string", "enum": [visitor.id for visitor in visitors]}
        add("village_visitor_meal", "Sell one real local meal to this visitor.", {"visitor": ids})
        add("village_visitor_lodging", "Accept and charge a real overnight lodging commitment.", {"visitor": ids})
        merchants = [visitor for visitor in visitors if visitor.plan.merchant]
        if merchants:
            resources = {r for visitor in merchants for r in
                         (*visitor.plan.merchant.buy_prices, *visitor.plan.merchant.sell_prices)}
            add("village_merchant_trade", "Transfer fresh surplus or finite imports and payment locally.",
                {"visitor": {"type": "string", "enum": [visitor.id for visitor in merchants]},
                 "direction": {"type": "string", "enum": ["export", "import"]},
                 "resource": {"type": "string", "enum": sorted(resources)},
                 "quantity": {"type": "integer", "minimum": 1, "maximum": 6},
                 "transaction": {"type": "string", "minLength": 1, "maxLength": 80}})
    return result


def _protected(world: "World", location: str, resource: str, exclude: str | None = None) -> int:
    reserve = village_economy.protected_quantity(world, location, resource)
    incidents = _context(world)
    if incidents:
        reserve += sum(max(0, old.template.demand.get(resource, 0) - old.fulfilled.get(resource, 0))
                       for old in incidents.instances.values() if old.status == "active"
                       and old.accepted_by is not None and old.template.location == location and old.id != exclude)
    return reserve


def _take(world: "World", location: str, resource: str, amount: int, kind: str,
          until: int | None = None) -> list[VillageStockLot]:
    return village_goods.take(world, location, resource, amount, kind=kind,
                              required_fresh_until=until)


def _put(world: "World", location: str, resource: str, amount: int,
         lots: list[VillageStockLot] | None, kind: str) -> None:
    village_goods.put(world, location, resource, amount, lots, kind=kind)


def _fresh(world: "World", location: str, resource: str, amount: int, until: int) -> bool:
    base, state = village.context(world)
    if not base.expanded_rules:
        return resource not in base.spoil_after
    lots = state.stock_lots.get(location, {}).get(resource, [])
    if sum(lot.quantity for lot in lots) != state.stocks.get(location, {}).get(resource, 0):
        return False
    return sum(lot.quantity for lot in lots if lot.expires_at is None or lot.expires_at > until) >= amount


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    incidents = _context(world)
    base, state = village.context(world)
    rid, location = world.actor_id, world.actor.location_id

    def reject(text: str) -> tuple[str, str, str, None]:
        return "REJECTED", "EVENT_REQUIREMENT", text, None

    def done(text: str, instance: IncidentState | None = None) -> tuple[str, str, str, None]:
        if instance:
            _log(world, instance, rid, action, text)
        else:
            village.event(world, rid, action, str(args.get("visitor", "")), text, location)
        return "APPLIED", "EVENT_APPLIED", text, None

    if incidents is None or action not in VERBS or rid not in state.residents:
        return reject("No enabled event action is available.")
    person = state.residents[rid]
    if person.life and (person.life.sleeping or person.life.work or person.life.hungry):
        return reject("Finish committed work, meals and rest before optional incidents.")
    closing = base.residents[rid].bed_minute - 30 if action in {
        "village_visitor_meal", "village_visitor_lodging"} else 1140
    if state.minute % 1440 >= min(closing, base.residents[rid].bed_minute - 30):
        return reject("Optional incidents do not displace supper or homecoming.")
    if action.startswith("village_event_"):
        instance = incidents.instances.get(args.get("incident"))
        if instance is None or instance.status != "active" or state.minute >= instance.expires_at:
            return reject("This incident is not active.")
        item = instance.template
        if item.location != location or (item.residents and rid not in item.residents):
            return reject("Only an eligible villager physically here may respond.")
        if instance.accepted_by not in {None, rid}:
            return reject("Another resident has already accepted this request.")
        response = action.removeprefix("village_event_")
        if response != "accept" and response not in item.responses:
            return reject("This template does not support that response.")
        if response == "accept":
            if instance.accepted_by == rid:
                return reject("You already accepted this incident.")
            instance.accepted_by = rid
            _wake_waiters(world, location)
            return done("Accepted the optional request and protected its remaining demand.", instance)
        if response == "decline":
            instance.status, instance.resolved_at = "declined", state.minute
            instance.outcome = "Optional request declined; no rewards"
            return done(instance.outcome, instance)
        if response == "discover":
            entity = world.state.entities.get(item.discovery)
            if entity is None or entity.location != location:
                return reject("The real discovery is not here; search its physical location.")
            person.beliefs[item.discovery] = VillageBelief(
                subject=item.discovery, location=location, source="direct_observation",
                learned_at=state.minute, confidence=1.0)
            instance.status, instance.resolved_at = "resolved", state.minute
            instance.outcome = "Personally discovered the real local entity"
            return done(instance.outcome, instance)
        if response == "work":
            if instance.next_work_at > state.minute:
                return reject("The previous concrete task step is still taking five minutes.")
            if item.kind in DELAYS:
                if item.kind != "equipment":
                    return reject("This short-lived delay clears at its expiry; it needs no invented reward.")
            if any(state.stocks.get(location, {}).get(resource, 0) - _protected(
                    world, location, resource, instance.id) < amount
                    or not _fresh(world, location, resource, amount, state.minute)
                   for resource, amount in item.inputs.items()):
                return reject("Bring the real required inputs before performing the task.")
            instance.accepted_by = rid
            instance.progress += 1
            instance.next_work_at = state.minute + 5
            if instance.progress < item.intensity:
                return done("Performed one concrete five-minute task step; more work remains.", instance)
            for resource, amount in item.inputs.items():
                _take(world, location, resource, amount, "consumed")
            for resource, amount in item.outputs.items():
                _put(world, location, resource, amount, None, "produced")
            instance.status, instance.resolved_at = "resolved", state.minute
            instance.outcome = f"Completed {item.intensity} task steps; source: {item.source or 'local labour'}"
            return done(instance.outcome, instance)
        if response == "deliver":
            resource, amount = args.get("resource"), args.get("quantity")
            if type(amount) is not int or not 1 <= amount <= 6 or resource not in item.demand:
                return reject("Choose a positive bounded requested quantity.")
            remaining = item.demand[resource] - instance.fulfilled.get(resource, 0)
            visitor = next((v for v in incidents.visitors.values() if v.incident == instance.id), None)
            price = base.prices.get(resource, 0)
            if visitor is None or visitor.status != "present" or visitor.location != location:
                return reject("The funded customer must be physically present.")
            if (amount > remaining or visitor.coins < amount * price
                    or sum(visitor.stock.values()) + amount > (
                        visitor.plan.merchant.capacity if visitor.plan.merchant else visitor.plan.capacity)
                    or state.stocks.get(location, {}).get(resource, 0) - _protected(
                        world, location, resource, instance.id) < amount
                    or not _fresh(world, location, resource, amount, state.minute)):
                return reject("Insufficient unreserved fresh stock, demand or customer funds.")
            owner = next((r for r, resident in base.residents.items() if resident.work == location), None)
            if owner != rid:
                return reject("Only the local producer may sell these goods.")
            lots = _take(world, location, resource, amount, "exported")
            visitor.stock[resource] = visitor.stock.get(resource, 0) + amount
            visitor.stock_lots.setdefault(resource, []).extend(VisitorLot(
                quantity=lot.quantity, produced_at=lot.produced_at, expires_at=lot.expires_at) for lot in lots)
            if not lots:
                visitor.stock_lots.setdefault(resource, []).append(VisitorLot(quantity=amount, produced_at=state.minute))
            visitor.coins -= amount * price
            state.coins[rid] += amount * price
            village_economy.record_external_payment(world, visitor.id, rid, amount * price)
            instance.accepted_by = rid
            instance.fulfilled[resource] = instance.fulfilled.get(resource, 0) + amount
            tid = f"order:{instance.id}:{resource}:{instance.fulfilled[resource]}"
            incidents.trades[tid] = TradeRecord(id=tid, visitor=visitor.id, resident=rid, direction="order",
                resource=resource, quantity=amount, payment=amount * price, minute=state.minute,
                location=location, source=visitor.plan.source)
            if all(instance.fulfilled.get(r, 0) >= q for r, q in item.demand.items()):
                instance.status, instance.resolved_at = "resolved", state.minute
                instance.outcome = "All paid customer demand physically supplied"
            return done("Transferred the real requested goods and settled finite customer payment.", instance)
    visitor = incidents.visitors.get(args.get("visitor"))
    if visitor is None or visitor.status != "present" or visitor.location != location or state.minute >= visitor.leave_at:
        return reject("The visitor must be here before departure.")
    owner = next((r for r, resident in base.residents.items() if resident.work == location), None)
    if owner != rid:
        return reject("Only the local producer or host can transact from this stock.")
    day = state.minute // 1440
    if action in {"village_visitor_meal", "village_visitor_lodging"}:
        lodging = action == "village_visitor_lodging"
        if (location != (visitor.plan.lodging_location if lodging else visitor.plan.meal_location)
                or day in (visitor.lodging_days if lodging else visitor.meals)):
            return reject("Use the committed host location once per day.")
        if lodging and sum(day in other.lodging_days and other.plan.lodging_location == location
                           for other in incidents.visitors.values()) >= visitor.plan.lodging_capacity:
            return reject("All finite lodging places are already committed tonight.")
        resource = "lodging" if lodging else next((r for r in ("meals", "bread") if
            state.stocks.get(location, {}).get(r, 0) > _protected(world, location, r)), None)
        price = visitor.plan.lodging_price if lodging else base.meal_price
        if visitor.coins < price or resource is None:
            return reject("The host needs real unreserved food and the visitor needs funds.")
        if not lodging:
            if not _fresh(world, location, resource, 1, state.minute):
                return reject("The meal is not fresh.")
            _take(world, location, resource, 1, "consumed")
        visitor.coins -= price
        state.coins[rid] += price
        village_economy.record_external_payment(world, visitor.id, rid, price, "goods" if lodging else "meal")
        (visitor.lodging_days if lodging else visitor.meals).append(day)
        tid = f"{action}:{visitor.id}:{day}"
        incidents.trades[tid] = TradeRecord(id=tid, visitor=visitor.id, resident=rid,
            direction="lodging" if lodging else "meal", resource=resource, quantity=1,
            payment=price, minute=state.minute, location=location, source=visitor.plan.source)
        _interval(visitor, "Lodging commitment" if lodging else "Meal", location, state.minute, state.minute + 5)
        return done("Settled the visitor's real meal or lodging commitment exactly once.")
    if action == "village_merchant_trade":
        profile = visitor.plan.merchant
        resource, amount, direction, token = (args.get(key) for key in ("resource", "quantity", "direction", "transaction"))
        if profile is None or direction not in {"export", "import"} or type(amount) is not int or not 1 <= amount <= 6:
            return reject("Choose a real bounded merchant offer.")
        if not isinstance(token, str) or not 1 <= len(token) <= 80:
            return reject("Provide a stable transaction ID for retry safety.")
        tid = f"{visitor.id}:{rid}:{token}"
        previous = incidents.trades.get(tid)
        if previous:
            if (previous.direction, previous.resource, previous.quantity) != (direction, resource, amount):
                return reject("Transaction ID was already used for different terms.")
            return "APPLIED", "TRADE_ALREADY_APPLIED", "This recorded transfer was already settled; nothing moved again.", None
        if direction == "export":
            price = profile.buy_prices.get(resource)
            until = max(state.minute, visitor.leave_at) + profile.journey_minutes + profile.resale_minutes
            if (price is None or amount > profile.demand.get(resource, 0) - visitor.purchased.get(resource, 0)
                    or sum(visitor.stock.values()) + amount > profile.capacity
                    or visitor.coins < amount * price
                    or state.stocks.get(location, {}).get(resource, 0) - _protected(world, location, resource) < amount
                    or not _fresh(world, location, resource, amount, until)):
                return reject("Offer exceeds fresh surplus, finite demand, capacity, or funds.")
            lots = _take(world, location, resource, amount, "exported", until)
            visitor.stock[resource] = visitor.stock.get(resource, 0) + amount
            visitor.stock_lots.setdefault(resource, []).extend(VisitorLot(
                quantity=lot.quantity, produced_at=lot.produced_at, expires_at=lot.expires_at) for lot in lots)
            if not lots:
                visitor.stock_lots.setdefault(resource, []).append(VisitorLot(quantity=amount, produced_at=state.minute))
            visitor.purchased[resource] = visitor.purchased.get(resource, 0) + amount
            visitor.coins -= amount * price
            state.coins[rid] += amount * price
        else:
            price = profile.sell_prices.get(resource)
            if price is None or visitor.stock.get(resource, 0) < amount or state.coins[rid] < amount * price:
                return reject("Not enough actual imports or resident money.")
            lots = visitor.stock_lots.get(resource, [])
            if sum(lot.quantity for lot in lots) != visitor.stock.get(resource, 0):
                return reject("Merchant freshness ledger does not match actual stock.")
            remaining, moved, retained = amount, [], []
            for lot in lots:
                used = min(lot.quantity, remaining)
                if used:
                    if lot.expires_at is not None and lot.expires_at <= state.minute:
                        return reject("The merchant imports have expired; wait for spoilage accounting.")
                    moved.append(VillageStockLot(quantity=used, produced_at=lot.produced_at, expires_at=lot.expires_at))
                    remaining -= used
                if lot.quantity > used:
                    retained.append(lot.model_copy(update={"quantity": lot.quantity - used}))
            visitor.stock_lots[resource] = retained
            visitor.stock[resource] -= amount
            _put(world, location, resource, amount, moved if base.expanded_rules else None, "imported")
            state.coins[rid] -= amount * price
            visitor.coins += amount * price
        incidents.trades[tid] = TradeRecord(id=tid, visitor=visitor.id, resident=rid, direction=direction,
            resource=resource, quantity=amount, payment=amount * price, minute=state.minute,
            location=location, source=profile.source)
        village_economy.record_external_payment(
            world, visitor.id if direction == "export" else rid,
            rid if direction == "export" else visitor.id, amount * price)
        _interval(visitor, "Trading", location, state.minute, state.minute + 5)
        return done(f"Settled {direction} of {amount} {resource} for {amount * price} coins.")
    return reject("Unsupported event action.")

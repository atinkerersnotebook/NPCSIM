from collections import deque
from typing import TYPE_CHECKING, Any

from npc_sim.models import (
    Scenario, VillageBelief, VillageConfig, VillageEvent, VillagePersonState, VillageState, VillageTask, VillageWagon,
    VillageDeadline, VillageStockLot, VillageStockSnapshot,
)

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"village_ask", "village_request", "village_wait", "village_trade",
         "village_tell", "village_cancel"}
TASKS = {
    "fetch_shovel": "Find the borrowed shovel using personal knowledge and return it to our meeting place.",
    "dig_well": "Work with Bren to dig the tavern well, using the shovel, timber and rope.",
    "repair_mill": "Work with Mara to repair the mill using a replacement beam and hammer.",
}


def validate_config(scenario: Scenario) -> None:
    config = scenario.village
    if config is None:
        raise ValueError("Missing village configuration")
    if scenario.goal_type != "village_restored" or any((
            scenario.bakery, scenario.voyage, scenario.rps, scenario.erp, scenario.bbq)):
        raise ValueError("Villages require their own village goal without other simulation modes")
    if bool(scenario.agents) != config.ai_controlled:
        raise ValueError("Only AI-controlled villages declare resident agents")
    if config.daily_life and (not config.ai_controlled or "tavern" not in scenario.entities):
        raise ValueError("Daily life requires an AI village with a tavern")
    if config.time_based_turns and (not config.daily_life or "wagon" not in scenario.entities):
        raise ValueError("Timed village turns require daily life and a wagon")
    if config.expanded_rules:
        if not config.time_based_turns or not {"work-horse", "iron-trader"} <= set(scenario.entities):
            raise ValueError("Expanded rules require timed turns, a real horse and the visiting trader")
        if any(resource not in config.prices or minutes < 5 or minutes % 5
               for resource, minutes in config.spoil_after.items()):
            raise ValueError("Shelf lives must name priced resources and positive five-minute durations")
        if any(goal not in {"bread_delivery", "dig_well", "repair_mill"}
               or minute < config.start_minute or minute % 5 for goal, minute in config.goal_deadlines.items()):
            raise ValueError("Goal deadlines must name village goals and future five-minute times")
    entities = scenario.entities
    locations = {eid for eid, entity in entities.items() if entity.kind == "location"}
    if config.time_based_turns and (
            not {"farm", "watermill", "bakery", "tavern", "village-square", "forge"} <= locations
            or "noticeboard" not in entities or entities["wagon"].location != "forge"):
        raise ValueError("Timed Millhaven needs its supply circuit, noticeboard and a wagon at the forge")
    residents = set(config.residents)
    if config.ai_controlled:
        if residents != {agent.id for agent in scenario.agents}:
            raise ValueError("Every AI village resident must have exactly one agent slot")
    elif not residents or any(eid not in entities or entities[eid].kind != "villager" for eid in residents):
        raise ValueError("Village residents must reference villager entities")
    if not {config.smith, config.helper, config.carpenter, config.miller, config.courier} <= residents:
        raise ValueError("Village specialist references must name residents")
    if not {config.mill, config.well_site} <= locations:
        raise ValueError("Village work sites must be locations")
    tools = [config.shovel, config.timber, config.rope, config.repair_part, config.hammer]
    if len(set(tools)) != len(tools) or any(
            eid not in entities or not entities[eid].takable for eid in tools):
        raise ValueError("Village supplies must be distinct portable objects")
    if config.mill_equipment not in entities or entities[config.mill_equipment].location != config.mill:
        raise ValueError("Mill equipment must be at the mill")
    if config.breakdown_minute <= config.start_minute:
        raise ValueError("The mill breakdown must follow the starting time")
    if set(config.coins) != residents or any(value < 0 for value in config.coins.values()):
        raise ValueError("Each resident needs a nonnegative coin balance")
    if not config.prices or any(value <= 0 for value in config.prices.values()):
        raise ValueError("Resource prices must be positive")
    if not set(config.stocks) <= locations or any(
            resource not in config.prices or amount < 0
            for stock in config.stocks.values() for resource, amount in stock.items()):
        raise ValueError("Stocks require real locations, priced resources and nonnegative quantities")
    if any(eid not in entities or owner not in residents for eid, owner in config.owners.items()):
        raise ValueError("Object ownership must reference existing objects and residents")
    for rid, resident in config.residents.items():
        start = (next(agent.start_location or scenario.start_location for agent in scenario.agents if agent.id == rid)
                 if config.ai_controlled else entities[rid].location)
        if not {resident.home, resident.work, start} <= locations:
            raise ValueError("Resident homes, workplaces and starting locations must exist")
        times = [entry.minute for entry in resident.schedule]
        if config.daily_life and not {resident.wake_minute, resident.bed_minute} <= set(times):
            raise ValueError("Daily-life wake and bed times must match the resident's schedule")
        if (not times or times != sorted(set(times)) or times[0] != 0
                or any(entry.location not in locations for entry in resident.schedule)):
            raise ValueError("Schedules must start at midnight with ordered unique times and real locations")
        if not set(resident.relationships) <= residents:
            raise ValueError("Relationships must name residents")
        if any(belief.subject not in entities or belief.location not in locations
               for belief in resident.beliefs):
            raise ValueError("Seed beliefs must reference existing subjects and locations")
        if resident.job is not None:
            job = resident.job
            if job.location not in locations or (job.equipment is not None and job.equipment not in entities):
                raise ValueError("Jobs must reference existing locations and equipment")
            if any(resource not in config.prices or amount <= 0
                   for stock in (job.inputs, job.outputs) for resource, amount in stock.items()):
                raise ValueError("Job quantities must be positive, priced resources")
    for delivery in config.deliveries:
        if (not {delivery.source, delivery.destination} <= locations
                or not {delivery.seller, delivery.buyer} <= residents
                or delivery.resource not in config.prices):
            raise ValueError("Deliveries require existing locations, trading residents and resources")
    required = {config.smith: "digging", config.helper: "digging",
                config.carpenter: "carpentry", config.miller: "milling"}
    if any(skill not in config.residents[rid].skills for rid, skill in required.items()):
        raise ValueError("Village specialists must have the skills their tasks require")
    reachable = {scenario.start_location}
    while True:
        expanded = reachable | {
            end for entity in entities.values() if entity.kind == "door" and entity.open
            and not entity.locked and not entity.broken
            for start, end in [(entity.location, entity.destination), (entity.destination, entity.location)]
            if start in reachable and end in locations
        }
        if expanded == reachable:
            break
        reachable = expanded
    if reachable != locations:
        raise ValueError("All village locations must have connected, initially passable routes")


def initial_state(scenario: Scenario) -> VillageState:
    from npc_sim import village_life
    config = scenario.village
    if config is None:
        raise ValueError("Missing village configuration")
    return VillageState(
        minute=config.start_minute,
        residents={rid: VillagePersonState(
            beliefs={belief.subject: belief.model_copy(deep=True) for belief in resident.beliefs},
            trust=resident.relationships.copy(), busy_until=resident.busy_until, available_at=config.start_minute,
            life=village_life.initial_state(resident, config.start_minute) if config.daily_life else None,
            next_work=config.start_minute if config.ai_controlled
            else config.start_minute + (resident.job.interval if resident.job else 30))
            for rid, resident in config.residents.items()},
        stocks={location: stock.copy() for location, stock in config.stocks.items()},
        coins={**config.coins, "visitor": 20, **({"iron-trader": 0} if config.expanded_rules else {})},
        roster={rid: resident.model_copy(deep=True) for rid, resident in config.residents.items()}
        if config.ai_controlled else {},
        wagon=VillageWagon() if config.time_based_turns else None,
        stock_lots={location: {resource: [VillageStockLot(
            quantity=amount, produced_at=config.start_minute,
            expires_at=config.start_minute + config.spoil_after[resource]
            if resource in config.spoil_after else None)] if amount else []
            for resource, amount in stock.items()} for location, stock in config.stocks.items()}
        if config.expanded_rules else {},
        deadlines={goal: VillageDeadline(due=minute) for goal, minute in config.goal_deadlines.items()},
        stock_history=[VillageStockSnapshot(minute=config.start_minute,
            stocks={location: stock.copy() for location, stock in config.stocks.items()},
            coins={**config.coins, "visitor": 20, "iron-trader": 0})] if config.expanded_rules else [],
    )


def clock_time(minute: int) -> str:
    time = f"{minute % 1440 // 60:02}:{minute % 60:02}"
    return f"{time} on day {minute // 1440 + 1}" if minute >= 1440 else time


def context(world: "World") -> tuple[VillageConfig, VillageState]:
    config, state = world.scenario.village, world.state.village
    if config is None or state is None:
        raise ValueError("Missing village configuration or persistent state")
    return config, state


def local_residents(world: "World", location: str) -> list[str]:
    _, state = context(world)
    return [rid for rid in state.residents if resident_location(world, rid) == location]


def resident_location(world: "World", resident: str) -> str:
    config, _ = context(world)
    if config.ai_controlled:
        actor = world.state.actors.get(resident)
        if actor is not None:
            return actor.location_id
        return next(agent.start_location or world.scenario.start_location
                    for agent in world.scenario.agents if agent.id == resident)
    location = world.state.entities[resident].location
    if location is None:
        raise ValueError("A village resident has no physical location")
    return location


def resident_name(world: "World", resident: str) -> str:
    entity = world.state.entities.get(resident)
    return entity.name if entity is not None else resident.replace("-", " ").title()


def event(world: "World", actor: str, action: str, target: str, result: str,
          location: str | None = None) -> int:
    _, state = context(world)
    if location is None:
        location = resident_location(world, actor) if actor in state.residents else world.actor.location_id
    witnesses = local_residents(world, location)
    witnesses += [aid for aid, person in world.state.actors.items() if person.location_id == location]
    witnesses = [rid for rid in witnesses if rid not in state.residents
                 or state.residents[rid].life is None or not state.residents[rid].life.sleeping]
    if world.scenario.village and world.scenario.village.ai_controlled and action in {"learn", "missed_meal"}:
        witnesses = [actor]
    if actor not in witnesses:
        witnesses.append(actor)
    state.events.append(VillageEvent(
        minute=state.minute, actor=actor, action=action, target=target,
        result=result, witnesses=list(dict.fromkeys(witnesses))))
    index = len(state.events) - 1
    for rid in witnesses:
        if rid in state.residents:
            state.residents[rid].memories.append(index)
    return index


def announce(world: "World", actor: str, action: str, target: str, text: str) -> None:
    _, state = context(world)
    index = event(world, actor, action, target, text, "village-square")
    notice = state.events[index]
    for rid, person in state.residents.items():
        if person.life is None or not person.life.sleeping:
            if rid not in notice.witnesses:
                notice.witnesses.append(rid)
                person.memories.append(index)
    state.notices.append(notice.model_copy(deep=True))


def read_noticeboard(world: "World") -> str:
    _, state = context(world)
    person = state.residents[world.actor_id]
    routes = [(eid, entity) for eid, entity in world.state.entities.items() if entity.kind == "door"]
    person.known_routes = [eid for eid, _ in routes]
    lines = [world.state.entities["noticeboard"].readable_text, "Village map (free):"]
    for _, door in routes:
        if door.location and door.destination:
            lines.append(f"{world.state.entities[door.location].name} <-> "
                         f"{world.state.entities[door.destination].name}: {door.name}"
                         f"{' (road)' if door.village_road else ' (footpath)'}.")
    lines.append("Public bell notices:")
    config, _ = context(world)
    if config.expanded_rules:
        person.stock_report = {key: value.copy() for key, value in state.stocks.items()}
        person.stock_report_at = state.minute
        lines.append("Current workplace stock:")
        for location, stock in state.stocks.items():
            lines.append(f"{world.state.entities[location].name}: {stock}.")
        for project, crew in state.project_contributions.items():
            lines.append(f"{project.replace('_', ' ')}: {min(6, sum(crew.values()))}/6 contributions.")
        for goal, deadline in state.deadlines.items():
            lines.append(f"{goal.replace('_', ' ')}: due {clock_time(deadline.due)}; "
                         f"{'completed' if deadline.completed_at is not None else 'overdue' if deadline.overdue else 'pending'}.")
    for notice in state.notices:
        lines.append(f"Day {notice.minute // 1440 + 1} {notice.minute % 1440 // 60:02}:"
                     f"{notice.minute % 60:02} / {resident_name(world, notice.actor)}: {notice.result}")
        for index, recorded in enumerate(state.events):
            if recorded == notice:
                if index not in person.memories:
                    person.memories.append(index)
                if world.actor_id not in recorded.witnesses:
                    recorded.witnesses.append(world.actor_id)
                    notice.witnesses.append(world.actor_id)
                break
    return "\n".join(lines)


def learn(world: "World", resident: str, subject: str, location: str | None,
          source: str, confidence: float) -> None:
    _, state = context(world)
    old = state.residents[resident].beliefs.get(subject)
    if old and (old.location, old.source, old.confidence) == (location, source, confidence):
        return
    state.residents[resident].beliefs[subject] = VillageBelief(
        subject=subject, location=location, source=source,
        learned_at=state.minute, confidence=confidence)
    event(world, resident, "learn", subject,
          f"Believes its location is {location or 'unknown'}; source: {source}.")
    config = world.scenario.village
    if config and config.time_based_turns and source == "observation" and location is not None:
        crew = ({config.smith, config.helper} if subject in {config.shovel, config.timber, config.rope}
                else {config.carpenter, config.miller} if subject in {config.repair_part, config.hammer} else set())
        if crew:
            place = world.state.entities[location].name
            announce(world, resident, "discovery", subject,
                     f"{resident_name(world, resident)} found {world.state.entities[subject].name} at {place}. "
                     f"Message for {', '.join(resident_name(world, rid) for rid in sorted(crew))}.")
            for other in crew - {resident}:
                life = state.residents[other].life
                if life is None or not life.sleeping:
                    learn(world, other, subject, location, f"bell:{resident}", 0.9)


def route(world: "World", start: str, destination: str, *, road_only: bool = False,
          known_routes: list[str] | None = None) -> list[str] | None:
    queue = deque([(start, [])])
    visited = {start}
    while queue:
        current, path = queue.popleft()
        if current == destination:
            return path
        for eid, door in world.state.entities.items():
            if door.kind != "door" or (road_only and not door.village_road):
                continue
            if known_routes is not None:
                if eid not in known_routes:
                    continue
            elif (not door.open or door.locked or door.broken
                    or world.lock_for(door).locked or world.lock_for(door).broken
                    or door.traversal_capabilities or door.required_challenges
                    or door.max_body_size == "small"):
                continue
            neighbor = door.destination if door.location == current else door.location if door.destination == current else None
            if neighbor is not None and neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, [*path, neighbor]))
    return None


def remember_step(world: "World", resident: str, origin: str, destination: str) -> None:
    _, state = context(world)
    eid = next(eid for eid, door in world.state.entities.items()
               if door.kind == "door" and {door.location, door.destination} == {origin, destination})
    known = state.residents[resident].known_routes
    if eid not in known:
        known.append(eid)


def walk(world: "World", resident: str, destination: str) -> bool:
    entity = world.state.entities[resident]
    if entity.location == destination:
        observe_local(world, resident)
        return True
    path = route(world, entity.location, destination)
    if not path:
        return False
    origin = entity.location
    entity.location = path[0]
    event(world, resident, "move", path[0], f"Walked from {origin} to {path[0]}.")
    observe_local(world, resident)
    return entity.location == destination


def observe_local(world: "World", resident: str) -> None:
    location = resident_location(world, resident)
    for eid, entity in world.state.entities.items():
        if entity.takable and not entity.hidden and entity.location == location:
            learn(world, resident, eid, location, "observation", 1)


def held(world: "World", resident: str) -> list[str]:
    return [eid for eid, entity in world.state.entities.items() if entity.location == resident]


def take(world: "World", resident: str, item: str, permitted: bool = False) -> bool:
    config, _ = context(world)
    entity, person = world.state.entities[item], world.state.entities[resident]
    if entity.location == resident:
        return not entity.broken
    owner = config.owners.get(item)
    if (entity.location != person.location or entity.hidden or entity.broken or not entity.takable
            or "hand" not in entity.carry_grips
            or (owner not in {None, resident} and not permitted)
            or sum(world.state.entities[eid].inventory_units for eid in held(world, resident))
            + entity.inventory_units > 4):
        return False
    entity.location = resident
    event(world, resident, "take", item, f"Picked up {entity.name}.")
    learn(world, resident, item, person.location, "observation:pickup", 1)
    return True


def block(world: "World", task: VillageTask, reason: str) -> None:
    if task.blocker != reason:
        task.evidence.append(event(world, task.assignee, "blocked", task.kind, reason))
    task.status, task.blocker = "blocked", reason


def fetch(world: "World", task: VillageTask) -> None:
    config, state = context(world)
    rid, shovel = task.assignee, world.state.entities[config.shovel]
    person = world.state.entities[rid]
    if shovel.location != rid:
        belief = state.residents[rid].beliefs.get(config.shovel)
        if not belief or belief.location is None:
            block(world, task, "No known shovel location. Waiting for information from someone nearby.")
            for other in local_residents(world, person.location):
                known = state.residents[other].beliefs.get(config.shovel)
                if other != rid and known and known.location is not None:
                    learn(world, rid, config.shovel, known.location, f"conversation:{other}", 0.8)
                    event(world, rid, "ask", other, f"Asked about the shovel; heard {known.location}.")
                    task.status, task.blocker = "in_progress", None
                    break
            return
        task.step = f"search:{belief.location}"
        if person.location != belief.location:
            if route(world, person.location, belief.location) is None:
                block(world, task, "The route to the believed shovel location is blocked.")
            else:
                walk(world, rid, belief.location)
            return
        if take(world, rid, config.shovel, permitted=True):
            task.step = "return"
        else:
            event(world, rid, "search", person.location, "Searched locally; the shovel was not accessible here.")
            learn(world, rid, config.shovel, None, "observation:unsuccessful-search", 1)
        return
    task.step = "deliver"
    if person.location != task.rendezvous:
        if route(world, person.location, task.rendezvous) is None:
            block(world, task, "The return route is blocked.")
        else:
            walk(world, rid, task.rendezvous)
        return
    recipient = world.state.actors[task.requester]
    bound = world.bind(task.requester, recipient.session_id)
    if (recipient.location_id != person.location
            or bound.inventory_units_used() + shovel.inventory_units > recipient.body.capacity
            or not set(shovel.carry_grips) & set(recipient.body.grips)
            or (recipient.body.max_held_items is not None
                and len(recipient.inventory) >= recipient.body.max_held_items)):
        block(world, task, "Return to the agreed meeting place with space and suitable grips for the shovel.")
        return
    shovel.location = task.requester
    recipient.inventory.append(config.shovel)
    learn(world, rid, config.shovel, person.location, "observation:delivery", 1)
    task.evidence.append(event(world, rid, "deliver", config.shovel, "Handed the shovel to the requester."))
    task.status, task.blocker = "completed", None
    state.residents[rid].trust[task.requester] = state.residents[rid].trust.get(task.requester, 0) + 1


def project(world: "World", task: VillageTask) -> None:
    config, state = context(world)
    digging = task.kind == "dig_well"
    leader, helper = task.assignee, config.helper if digging else config.miller
    site = config.well_site if digging else config.mill
    materials = [config.timber, config.rope] if digging else [config.repair_part]
    tool = config.shovel if digging else config.hammer
    people = [leader, helper]
    if state.minute % 1440 < 480 or state.minute % 1440 >= 1080:
        block(world, task, "The crew will resume during working hours, 08:00-18:00.")
        return
    if state.residents[helper].busy_until > state.minute:
        block(world, task, f"{helper} is busy until {clock_time(state.residents[helper].busy_until)}.")
        return
    if state.residents[helper].trust.get(leader, 0) < -2:
        block(world, task, f"{helper} declines to cooperate with {leader}.")
        return
    task.step = "gather_crew"
    if world.state.entities[leader].location != world.state.entities[helper].location and task.progress == 0:
        destination = config.residents[helper].work
        if world.state.entities[leader].location == destination:
            block(world, task, f"{helper} is not at the expected workplace. The crew waits for a meeting.")
            return
        if route(world, world.state.entities[leader].location, destination) is None:
            block(world, task, "Cannot reach the helper.")
        else:
            walk(world, leader, destination)
        return
    if task.progress == 0:
        task.evidence.append(event(world, leader, "cooperate", helper, f"{helper} agrees to help with {task.kind}."))
        task.progress = 1
    task.step = "gather_supplies"
    for item in [tool, *materials]:
        entity = world.state.entities[item]
        if entity.location in people:
            if entity.broken:
                block(world, task, f"The required {entity.name} is broken.")
                return
            continue
        # Plans use only supplies observed by the crew, or given to the crew.
        if entity.location != world.state.entities[leader].location or entity.hidden:
            belief = state.residents[leader].beliefs.get(item) or state.residents[helper].beliefs.get(item)
            if not belief or belief.location is None:
                block(world, task, f"Bring {entity.name} to the crew, or tell them where it was seen.")
                return
            if world.state.entities[leader].location == belief.location:
                learn(world, leader, item, None, "observation:unsuccessful-search", 1)
                block(world, task, f"{entity.name} was not found where expected.")
                return
            if route(world, world.state.entities[leader].location, belief.location) is None:
                block(world, task, f"The route to {entity.name} is blocked.")
            else:
                walk(world, leader, belief.location)
                walk(world, helper, world.state.entities[leader].location)
            return
        if not take(world, leader, item, permitted=True):
            block(world, task, f"Cannot collect {entity.name}; check carrying space, condition and placement.")
            return
    task.step = "travel_to_site"
    arrived = [walk(world, rid, site) for rid in people]
    if not all(arrived):
        if any(route(world, world.state.entities[rid].location, site) is None for rid in people):
            block(world, task, "The work site cannot be reached.")
        return
    if not digging and not state.breakdown:
        block(world, task, "The mill has not broken down; no repair is needed yet.")
        return
    task.step = "work"
    task.progress += 1
    task.evidence.append(event(world, leader, "work", site,
                               f"{leader} and {helper} work with the required tool and supplies."))
    if task.progress < 7:
        return
    for item in materials:
        world.state.entities[item].location = None
    if digging:
        state.well_dug = True
        world.state.entities[site].description = (
            "A newly dug, timber-lined well stands behind the tavern. "
            "Torvin and Bren completed it with the real shovel, timber and safety rope.")
    else:
        state.repaired = True
        equipment = world.state.entities[config.mill_equipment]
        equipment.health = equipment.max_health
        equipment.description = "Hobb and Mara repaired the mill drive with a new beam. It can grind grain again."
    task.evidence.append(event(world, leader, "complete", task.kind,
                               "Work verified; materials consumed and the site changed."))
    task.status, task.blocker = "completed", None


def do_task(world: "World", task: VillageTask) -> None:
    _, state = context(world)
    if state.residents[task.assignee].busy_until > state.minute:
        block(world, task, f"Urgent work takes priority until {clock_time(state.residents[task.assignee].busy_until)}.")
        return
    task.status = "in_progress"
    if task.kind == "fetch_shovel":
        fetch(world, task)
    else:
        project(world, task)
    if task.status != "blocked":
        task.blocker = None


def deliver(world: "World") -> None:
    config, state = context(world)
    rid = config.courier
    person = state.residents[rid]
    if not config.deliveries:
        return
    index = person.delivery if person.delivery is not None else person.delivery_cursor
    delivery = config.deliveries[index]
    if person.delivery is None:
        if not walk(world, rid, delivery.source):
            return
        source = state.stocks.setdefault(delivery.source, {})
        if source.get(delivery.resource, 0) < 1 or state.coins[delivery.buyer] < delivery.price:
            person.activity = f"Waiting for {delivery.resource} stock or buyer funds"
            person.delivery_cursor = (index + 1) % len(config.deliveries)
            return
        source[delivery.resource] -= 1
        person.cargo[delivery.resource] = 1
        person.delivery = index
        event(world, rid, "load", delivery.resource, f"Loaded one unit at {delivery.source}.")
        return
    if not walk(world, rid, delivery.destination):
        return
    if state.coins[delivery.buyer] < delivery.price:
        person.activity = "Delivery blocked: buyer cannot pay"
        return
    state.coins[delivery.buyer] -= delivery.price
    state.coins[delivery.seller] += delivery.price
    stock = state.stocks.setdefault(delivery.destination, {})
    stock[delivery.resource] = stock.get(delivery.resource, 0) + person.cargo.pop(delivery.resource)
    if delivery.resource == "bread" and state.repaired:
        state.bread_after_repair += 1
    event(world, rid, "trade", delivery.resource,
          f"Delivered one unit to {delivery.destination}; {delivery.buyer} paid {delivery.seller} {delivery.price} coins.")
    person.delivery = None
    person.delivery_cursor = (index + 1) % len(config.deliveries)


def share_news(world: "World", resident: str) -> None:
    _, state = context(world)
    person = state.residents[resident]
    day = state.minute // 1440
    if person.last_news_day == day:
        return
    location = world.state.entities[resident].location
    nearby = local_residents(world, location)
    if len(nearby) < 2:
        return
    person.last_news_day = day
    # Only a personal, already-recorded memory can be passed on.
    memory = next((state.events[index] for index in reversed(person.memories)
                   if state.events[index].action in {"breakdown", "produce", "shortage", "complete"}), None)
    if memory is not None:
        event(world, resident, "news", memory.target,
              f"{resident} reports an earlier experience: {memory.result}", location)
    for subject, belief in list(person.beliefs.items()):
        if belief.location is None:
            continue
        for other in nearby:
            known = state.residents[other].beliefs.get(subject)
            if other != resident and (known is None or known.learned_at < belief.learned_at):
                learn(world, other, subject, belief.location, f"conversation:{resident}", 0.6)


def advance(world: "World", minutes: int) -> None:
    config, state = context(world)
    for _ in range(minutes // 5):
        state.minute += 5
        if not state.breakdown and state.minute >= config.breakdown_minute:
            state.breakdown = True
            world.state.entities[config.mill_equipment].health = 0
            event(world, "environment" if config.ai_controlled else config.miller, "breakdown", config.mill_equipment,
                  "The mill drive failed. Flour production stops until a carpenter and miller repair it.",
                  config.mill)
            if config.time_based_turns:
                announce(world, "village-bell", "breakdown", config.mill_equipment,
                         "The mill drive failed at 14:00. Flour production has stopped. "
                         "Hobb and Mara: meet at the watermill with the replacement beam and hammer.")
        if config.ai_controlled:
            from npc_sim import village_goods, village_life
            village_goods.advance(world)
            village_life.advance(world)
            if config.time_based_turns:
                from npc_sim import village_wagon
                village_wagon.advance(world)
            if config.expanded_rules:
                from npc_sim import village_services
                village_services.advance(world)
                village_goods.snapshot(world)
            continue
        active = [task for task in state.tasks.values() if task.status not in {"completed", "cancelled"}]
        occupied = {task.assignee for task in active}
        occupied |= {config.helper if task.kind == "dig_well" else config.miller
                     for task in active if task.kind != "fetch_shovel" and task.progress > 0}
        for task in active:
            do_task(world, task)
        for rid, resident in config.residents.items():
            if rid in occupied:
                continue
            person = state.residents[rid]
            routine = next(entry for entry in reversed(resident.schedule)
                           if entry.minute <= state.minute % 1440)
            person.activity = routine.activity
            if rid == config.courier and 480 <= state.minute % 1440 < 1080:
                deliver(world)
                continue
            if not walk(world, rid, routine.location):
                continue
            if 1140 <= state.minute % 1440 < 1320:
                share_news(world, rid)
            job = resident.job
            if (job is None or routine.minute == 0 or routine.minute >= 1140
                    or routine.location != job.location or state.minute < person.next_work):
                continue
            person.next_work = state.minute + job.interval
            stock = state.stocks.setdefault(job.location, {})
            if job.equipment and world.state.entities[job.equipment].broken:
                person.activity = "Blocked by broken equipment; needs repairs"
                event(world, rid, "job_blocked", job.equipment, person.activity)
                continue
            if any(stock.get(resource, 0) < amount for resource, amount in job.inputs.items()):
                person.activity = "Shortage: " + ", ".join(
                    resource for resource, amount in job.inputs.items() if stock.get(resource, 0) < amount)
                event(world, rid, "shortage", job.location, person.activity)
                continue
            for resource, amount in job.inputs.items():
                stock[resource] -= amount
            for resource, amount in job.outputs.items():
                stock[resource] = stock.get(resource, 0) + amount
            event(world, rid, "produce", job.location,
                  f"Consumed {job.inputs}; produced {job.outputs}.")


def after_action(world: "World", action: str, arguments: dict[str, Any]) -> int:
    if world.scenario.village is None:
        return 0
    if world.scenario.village.ai_controlled:
        from npc_sim import village_ai
        return village_ai.after_action(world, action, arguments)
    duration = arguments["minutes"] if action == "village_wait" else 0 if action in {"look", "read"} else 5
    if duration:
        advance(world, duration)
    return duration


def observation(world: "World") -> dict[str, Any]:
    if world.scenario.village is None:
        return {}
    if world.scenario.village.ai_controlled:
        from npc_sim import village_ai
        return village_ai.observation(world)
    config, state = context(world)
    nearby = local_residents(world, world.actor.location_id)
    events = [entry.model_dump() for entry in state.events if world.actor_id in entry.witnesses][-12:]
    return {"village": {
        "minute": state.minute, "day": state.minute // 1440 + 1,
        "time": f"{state.minute % 1440 // 60:02}:{state.minute % 60:02}",
        "residents": [{"id": rid, "name": world.state.entities[rid].name,
                       "occupation": config.residents[rid].occupation,
                       "activity": state.residents[rid].activity} for rid in nearby],
        "stocks": state.stocks.get(world.actor.location_id, {}).copy(),
        "coins": state.coins["visitor"],
        "resource_inventory": state.stocks.get(f"visitor:{world.actor_id}", {}).copy(),
        "tasks": [{"id": tid, "kind": task.kind, "assignee": task.assignee,
                   "status": task.status, "step": task.step, "blocker": task.blocker}
                  for tid, task in state.tasks.items() if task.requester == world.actor_id],
        "events": events,
        "policy": "Each action takes five game minutes; looking and reading are free. "
                  "Use village_wait to advance 5-1440 minutes in five-minute steps. "
                  "Task cards are engine progress reports, not the visitor's remote observations. "
                  "Ask nearby residents about the shovel, their work, or local news.",
    }}


def actions(world: "World") -> list[dict[str, Any]]:
    if world.scenario.village is None:
        return []
    if world.scenario.village.ai_controlled:
        from npc_sim import village_ai
        return village_ai.actions(world)
    config, state = context(world)
    nearby = local_residents(world, world.actor.location_id)
    result: list[dict[str, Any]] = []

    def add(aid: str, description: str, properties: dict[str, Any]) -> None:
        result.append({
            "id": aid, "description": description, "targets": nearby,
            "arguments_schema": {"type": "object", "properties": properties,
                                 "required": list(properties), "additionalProperties": False},
            "availability": "available", "reason": None})

    add("village_wait", "Let residents travel, work, trade and pursue accepted tasks without further orders.",
        {"minutes": {"type": "integer", "minimum": 5, "maximum": 1440, "multipleOf": 5}})
    if nearby:
        who = {"type": "string", "enum": nearby}
        add("village_ask", "Hear a nearby villager's own belief, work priorities or witnessed news. "
            "Dialogue cannot execute work or change world truth.",
            {"target": who, "topic": {"type": "string", "enum": ["shovel", "work", "news"]}})
        add("village_request", "Request a specialist's help. Busy or unwilling residents can delay or refuse. "
            "fetch_shovel needs Torvin; dig_well needs Torvin and Bren; repair_mill needs Hobb and Mara. "
            "Give supplies by dropping them at the crew's location; plans use observed or reported locations.",
            {"target": who, "task": {"type": "string", "enum": list(TASKS)}})
        known = sorted(set(world.actor.discovered_entities) | set(world.visible_ids()))
        subjects = [eid for eid in known if eid in world.state.entities and world.state.entities[eid].takable]
        locations = world.actor.visited_locations
        if subjects:
            add("village_tell", "Tell a resident where you believe a discovered item was seen. "
                "This is unverified testimony, not a physical move or proof.",
                {"target": who, "subject": {"type": "string", "enum": subjects},
                 "location": {"type": "string", "enum": locations}})
        add("village_trade", "Buy or sell one local stock unit at posted prices with a resident working here. "
            "Resources are a stock ledger, distinct from portable tool objects.",
            {"target": who, "resource": {"type": "string", "enum": list(config.prices)},
             "direction": {"type": "string", "enum": ["buy", "sell"]}})
    cancellable = [tid for tid, task in state.tasks.items()
                   if task.requester == world.actor_id and task.status not in {"completed", "cancelled"}]
    if cancellable:
        add("village_cancel", "Cancel your unfinished request; resources already used are not refunded.",
            {"task_id": {"type": "string", "enum": cancellable}})
    return result


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    config, state = context(world)

    def result(code: str, text: str, outcome: str = "APPLIED") -> tuple[str, str, str, None]:
        return outcome, code, text, None

    if action == "village_wait":
        return result("VILLAGE_WAITED", f"You let the village carry on for {args['minutes']} minutes.")
    if action == "village_cancel":
        task = state.tasks[args["task_id"]]
        task.status = "cancelled"
        event(world, world.actor_id, "cancel", args["task_id"], "The requester cancelled the task.")
        return result("TASK_CANCELLED", "You cancel the request. The crew returns to its routine.")
    target = args["target"]
    if target not in local_residents(world, world.actor.location_id):
        return result("OUT_OF_REACH", "The resident must be here.", "REJECTED")
    person, resident = state.residents[target], config.residents[target]
    name = world.state.entities[target].name
    if action == "village_tell":
        learn(world, target, args["subject"], args["location"], f"testimony:{world.actor_id}", 0.5)
        return result("BELIEF_REPORTED", f"{name} remembers your report, but has not verified it.")
    if action == "village_ask":
        if args["topic"] == "shovel":
            shovel = world.state.entities[config.shovel]
            if shovel.location == world.actor.location_id and not shovel.hidden:
                learn(world, target, config.shovel, shovel.location, "observation", 1)
            belief = person.beliefs.get(config.shovel)
            answer = (f"I last heard it was at {world.state.entities[belief.location].name}; "
                      f"my source is {belief.source}. That may be out of date."
                      if belief and belief.location else "I don't know where the shovel is now.")
        elif args["topic"] == "work":
            answer = f"I'm the {resident.occupation}. {resident.personality} Right now: {person.activity}."
            if person.busy_until > state.minute:
                answer += f" I cannot leave my urgent work until {clock_time(person.busy_until)}."
        else:
            memories = [state.events[index].result for index in person.memories[-3:]]
            answer = " ".join(memories) if memories else "I have no recent news of my own."
        event(world, target, "talk", world.actor_id, answer)
        return result("VILLAGER_ANSWERED", f'{name} says: "{answer}"')
    if action == "village_trade":
        resource, buying = args["resource"], args["direction"] == "buy"
        if world.actor.location_id != resident.work:
            return result("SHOP_CLOSED", "Trade with this resident at their workplace.", "REJECTED")
        stock = state.stocks.setdefault(resident.work, {})
        inventory = state.stocks.setdefault(f"visitor:{world.actor_id}", {})
        source, destination = (stock, inventory) if buying else (inventory, stock)
        buyer, seller = ("visitor", target) if buying else (target, "visitor")
        price = config.prices[resource]
        if source.get(resource, 0) < 1 or state.coins[buyer] < price:
            return result("TRADE_BLOCKED", "The seller needs stock and the buyer needs enough coins.", "REJECTED")
        source[resource] -= 1
        destination[resource] = destination.get(resource, 0) + 1
        state.coins[buyer] -= price
        state.coins[seller] += price
        event(world, world.actor_id, "trade", resource, f"Transferred one {resource} and {price} coins.")
        return result("TRADED", f"You {'buy' if buying else 'sell'} one {resource} for {price} coins.")
    kind = args["task"]
    specialist = config.carpenter if kind == "repair_mill" else config.smith
    if target != specialist or person.trust.get(world.actor_id, 0) < -2:
        return result("REQUEST_REFUSED", f"{name} declines; this needs a willing specialist.", "REJECTED")
    if kind != "fetch_shovel" and any(
            task.kind == kind and task.status == "completed" for task in state.tasks.values()):
        return result("ALREADY_COMPLETED", "This village project is already complete.", "NO_CHANGE")
    crew = {target}
    if kind != "fetch_shovel":
        crew.add(config.helper if kind == "dig_well" else config.miller)
    for task in state.tasks.values():
        assigned = {task.assignee}
        if task.kind != "fetch_shovel":
            assigned.add(config.helper if task.kind == "dig_well" else config.miller)
        if task.status not in {"completed", "cancelled"} and crew & assigned:
            return result("CREW_BUSY", "A required crew member already has an unfinished request.", "REJECTED")
    tid = f"village-task-{len(state.tasks) + 1}"
    task = VillageTask(kind=kind, requester=world.actor_id, assignee=target,
                       rendezvous=world.actor.location_id)
    task.evidence.append(event(world, world.actor_id, "request", target, TASKS[kind]))
    state.tasks[tid] = task
    delayed = person.busy_until > state.minute
    if delayed:
        block(world, task, f"Urgent work first; I can start at {clock_time(person.busy_until)}.")
    return result("TASK_ACCEPTED", f"{name} accepts {kind.replace('_', ' ')}"
                  + (" after finishing urgent work." if delayed else "."))


def permission_reason(world: "World", item: str) -> str | None:
    if world.scenario.village is None:
        return None
    config, _ = context(world)
    owner = config.owners.get(item)
    if config.ai_controlled:
        _, state = context(world)
        if state.residents[world.actor_id].cargo:
            return "Deliver your shipment before taking portable tools."
        if owner == world.actor_id:
            return None
        permitted = {
            config.timber: {config.smith, config.helper},
            config.rope: {config.smith, config.helper},
        }
        if world.actor_id in permitted.get(item, set()):
            return None
    if owner:
        return f"This belongs to {resident_name(world, owner)}. Ask the specialist to handle it."
    return None


def evaluate_goal(world: "World") -> dict[str, Any]:
    if world.scenario.village and world.scenario.village.ai_controlled:
        from npc_sim import village_ai
        return village_ai.evaluate_goal(world)
    _, state = context(world)
    checks = {
        "shovel_delivered": any(task.kind == "fetch_shovel" and task.status == "completed"
                                for task in state.tasks.values()),
        "well_dug": state.well_dug,
        "mill_repaired": state.repaired,
        "bread_supply_resumed": state.bread_after_repair > 0,
    }
    return {
        "status": "achieved" if all(checks.values()) else "active",
        "reason": "Village recovery requires real delivery, cooperative work, repair and resumed bread trade.",
        "evidence": {"checks": checks, "minute": state.minute,
                     "tasks": {tid: task.model_dump() for tid, task in state.tasks.items()},
                     "bread_after_repair": state.bread_after_repair},
        "observation_revision": world.revision(),
    }

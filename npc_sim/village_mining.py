"""Renewable ore extraction, owned hauling and real fuelled smelting."""

from math import ceil
from typing import TYPE_CHECKING, Any

from npc_sim import handling, village, village_economy, village_goods, village_life
from npc_sim.models import Entity, Scenario, VillageMiningConfig, VillageMiningChunk, VillageMiningState, VillageShipment

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"village_mine_ore", "village_load_ore", "village_smelt_iron", "village_mining_pause"}
FREE_ACTIONS = {"look", "read", "say", "village_observe", "village_wait", "village_mining_pause"}
MINE, ORE, PICK, FURNACE = "iron-mine", "iron-ore", "mining-pick", "smelting-furnace"


def example_config(scenario: Scenario) -> VillageMiningConfig:
    if scenario.village is None:
        raise ValueError("Mining requires a village")
    return VillageMiningConfig(miner=scenario.village.smith)


def fixtures(scenario: Scenario) -> dict[str, Entity]:
    if scenario.village is None:
        raise ValueError("Mining requires a village")
    forge = scenario.village.residents[scenario.village.smith].work
    return {
        MINE: Entity(name="Millhaven iron mine", kind="location",
                     description="A renewable iron seam. Ore must be mined with a real pick, hauled and smelted; no finished iron lies here."),
        "square-mine": Entity(name="Mine road", kind="door", description="The square's road to the iron mine.",
                              location="village-square", destination=MINE, open=True, health=None),
        "mine-square": Entity(name="Road back to Millhaven", kind="door", description="The iron mine's road to the village square.",
                              location=MINE, destination="village-square", open=True, health=None),
        PICK: Entity(name="Mining pick", kind="tool", description="A real hand-held pick for extracting iron ore.",
                     location=MINE, takable=True, max_health=100),
        FURNACE: Entity(name="Smelting furnace", kind="tool", description="Smelts one iron ore into one usable iron unit using real fuel: logs normally, firewood when its policy is enabled.",
                        location=forge, max_health=100),
    }


def install_scenario(scenario: Scenario) -> None:
    base = scenario.village
    if base is None:
        raise ValueError("Mining requires a village")
    base.mining = example_config(scenario)
    for key, entity in fixtures(scenario).items():
        if key in scenario.entities and scenario.entities[key].kind != entity.kind:
            raise ValueError(f"Mining fixture identity conflicts with an existing object: {key}")
        scenario.entities.setdefault(key, entity)
    base.prices.setdefault(ORE, 1)
    base.stocks.setdefault(MINE, {}).setdefault(ORE, 0)
    base.owners.setdefault(PICK, base.smith)
    base.owners.setdefault(FURNACE, base.smith)


def validate_config(scenario: Scenario) -> None:
    base = scenario.village
    if base and base.mining:
        if not (base.expanded_rules and base.time_based_turns and base.daily_life and base.ai_controlled):
            raise ValueError("Mining requires expanded timed AI village life")
        if base.mining.miner != base.smith:
            raise ValueError("The town smith is assigned ore extraction and smelting")
        if not set(fixtures(scenario)) <= set(scenario.entities):
            raise ValueError("Mining requires its real mine, road, pick and furnace")


def initial_state(scenario: Scenario) -> VillageMiningState | None:
    config = scenario.village.mining if scenario.village else None
    return VillageMiningState(config=config.model_copy(deep=True)) if config else None


def _state(world: "World") -> VillageMiningState:
    mining = village.context(world)[1].mining
    if mining is None:
        raise ValueError("Mining is not enabled")
    return mining


def configure(world: "World", value: dict[str, Any]) -> None:
    config = VillageMiningConfig.model_validate(value)
    base, state = village.context(world)
    if not (base.expanded_rules and base.time_based_turns and base.daily_life and base.ai_controlled):
        raise ValueError("Mining requires expanded timed AI village life")
    if config.miner != base.smith:
        raise ValueError("Only the actual smith may be assigned this mining/smelting workflow")
    if state.mining and any(chunk.status == "active" for chunk in state.mining.chunks):
        raise ValueError("Pause active mining/smelting before reconfiguring it")
    for key, entity in fixtures(world.scenario).items():
        previous = world.state.entities.get(key)
        if previous and (previous.kind != entity.kind or previous.kind == "door"
                         and (previous.location != entity.location or previous.destination != entity.destination)):
            raise ValueError(f"Mining fixture conflicts with an existing world object: {key}")
        world.state.entities.setdefault(key, entity)
    state.stocks.setdefault(MINE, {}).setdefault(ORE, 0)
    state.stock_lots.setdefault(MINE, {}).setdefault(ORE, [])
    state.mining = VillageMiningState(config=config, chunks=state.mining.chunks if state.mining else [])


def blocked_until(world: "World", actor: str) -> int | None:
    return next((chunk.end for chunk in _state(world).chunks if chunk.actor == actor and chunk.status == "active"), None)


def equipment_reserved(world: "World", tools: set[str]) -> bool:
    mining = village.context(world)[1].mining
    return bool(mining and any(chunk.status == "active" and chunk.equipment in tools for chunk in mining.chunks))


def iron_needed(world: "World") -> int:
    base, state = village.context(world)
    smith = base.smith
    location = base.residents[smith].work
    policy = village_economy.config(world)
    required = policy.reserve_targets.get(location, {}).get("iron", 2) if policy else 2
    job = base.residents[smith].job
    if job:
        targets = village_economy.production_targets(world, location)
        batches = max((ceil(max(0, targets.get(product, 0) -
                               village_economy.usable_stock(world, location, product, state.minute)) / amount)
                       for product, amount in job.outputs.items()), default=0)
        required += batches * job.inputs.get("iron", 0)
    if state.research:
        for project in state.research.projects.values():
            if project.owner != smith:
                continue
            if project.discovered_at is None and not project.trial_materials_consumed:
                required += project.proposal.inputs.get("iron", 0)
            elif project.discovered_at is not None and state.economy:
                demand = sum(order.loadable for order in state.economy.orders.values()
                             if order.status == "accepted" and order.product == project.product and order.supplier == smith)
                required += max(0, demand - village_economy.usable_stock(world, location, project.product, state.minute)) * project.proposal.inputs.get("iron", 0)
    if state.maintenance:
        required += sum(job.definition.supplies.get("iron", 0) for job in state.maintenance.jobs.values()
                        if job.definition.assignee == smith and job.completed_at is None and not job.materials_consumed)
    available = village_economy.usable_stock(world, location, "iron", state.minute)
    available += sum(item.quantity for person in state.residents.values() for item in person.shipments
                     if item.resource == "iron" and item.destination == location)
    if state.wagon:
        available += sum(item.quantity for item in state.wagon.loads if item.resource == "iron" and item.destination == location)
    if state.mining:
        available += sum(chunk.operation == "smelt" for chunk in state.mining.chunks if chunk.status == "active")
    return max(0, required - available)


def ore_needed(world: "World") -> int:
    base, state = village.context(world)
    ore = sum(village_economy.usable_stock(world, site, ORE, state.minute)
              for site in [MINE, base.residents[base.smith].work])
    ore += sum(item.quantity for person in state.residents.values() for item in person.shipments if item.resource == ORE)
    if state.wagon:
        ore += sum(item.quantity for item in state.wagon.loads if item.resource == ORE)
    ore += sum(chunk.operation == "mine" for chunk in _state(world).chunks if chunk.status == "active")
    return max(0, iron_needed(world) - ore)


def load_owned_ore(world: "World", carrier: str, goods: dict[str, int], destination: str,
                   shipments: list[VillageShipment], capacity: int) -> str | None:
    base, state = village.context(world)
    if carrier != _state(world).config.miner or village.resident_location(world, carrier) != MINE:
        return "Only the assigned miner may collect owned ore at the actual mine."
    if destination != base.residents[base.smith].work or set(goods) != {ORE}:
        return "Owned ore must be carried to the real forge; this is not a stock transfer shortcut."
    quantity = goods[ORE]
    if type(quantity) is not int or quantity <= 0 or quantity + sum(item.quantity for item in shipments) > capacity:
        return "The ore must fit actual carrying capacity alongside tools and existing cargo."
    if village_economy.usable_stock(world, MINE, ORE, state.minute) < quantity:
        return "Mine the actual ore before loading it."
    shipments.append(VillageShipment(resource=ORE, quantity=quantity, seller=carrier, buyer=carrier,
                                     destination=destination, price=1, owned_transfer=True,
                                     lots=village_goods.take(world, MINE, ORE, quantity, kind="loaded")))
    return None


def _equipment_blocker(world: "World", equipment: str, location: str, actor: str) -> tuple[str, str] | None:
    from npc_sim import village_livestock
    if village_livestock.equipment_reserved(world, {equipment}):
        return "EQUIPMENT_BUSY", "Livestock work has reserved the actual equipment."
    from npc_sim import village_research
    _, state = village.context(world)
    entity = world.state.entities.get(equipment)
    body = world.state.actors[actor]
    if (entity is None or entity.hidden or entity.broken
            or entity.location != location and equipment not in body.inventory):
        return "MINING_TOOL_REQUIRED", "Use the actual usable mining pick/furnace at the work site."
    if not set(entity.use_grips).intersection(body.body.grips) or "manipulate" not in body.body.capabilities:
        return "MINING_BODY_REQUIREMENT", "Real manipulation capability and a compatible grip are required."
    capability = "digging" if equipment == PICK else "smithing"
    if capability not in body.body.capabilities:
        return "MINING_BODY_REQUIREMENT", f"The actual body needs {capability} capability for this work."
    if village_research.equipment_reserved(world, {equipment}):
        return "EQUIPMENT_BUSY", "The actual equipment is reserved by research."
    if state.maintenance and any(job.chunks and job.chunks[-1].status == "active" and equipment in job.definition.equipment
                                 for job in state.maintenance.jobs.values()):
        return "EQUIPMENT_BUSY", "Maintenance has reserved the equipment."
    if state.social and any(interval.status == "active" and equipment in interval.equipment for interval in state.social.intervals):
        return "EQUIPMENT_BUSY", "A real activity has reserved the equipment."
    if any(person.life and person.life.work and person.life.work.equipment == equipment for person in state.residents.values()):
        return "EQUIPMENT_BUSY", "A production batch has reserved the equipment."
    if state.incidents:
        from npc_sim import village_events
        if village_events.delay_until(world, actor, location, equipment) > state.minute:
            return "INCIDENT_BLOCKED", "An incident prevents mining/smelting at this site."
    return None


def action_blocker(world: "World", action: str, actor: str) -> tuple[str, str] | None:
    until = blocked_until(world, actor)
    if until and action not in FREE_ACTIONS:
        return "MINING_COMMITTED", f"Working until {village.clock_time(until)}; pause the batch explicitly before leaving."
    return None


def advance(world: "World") -> None:
    _, state = village.context(world)
    for chunk in _state(world).chunks:
        if chunk.status != "active":
            continue
        person = state.residents[chunk.actor]
        if (person.life and person.life.sleeping or village.resident_location(world, chunk.actor) != chunk.location
                or _equipment_blocker(world, chunk.equipment, chunk.location, chunk.actor)):
            chunk.status, chunk.end = "interrupted", state.minute
            person.available_at = state.minute
            village.event(world, chunk.actor, "mining_interrupted", chunk.operation,
                          "Physical work was interrupted; no unearned ore/iron was created. Used smelting inputs are not refunded.")
        elif state.minute >= chunk.end:
            chunk.status = "completed"
            resource = ORE if chunk.operation == "mine" else "iron"
            village_goods.put(world, chunk.location, resource, 1)
            state.role_contributions[chunk.actor] = state.role_contributions.get(chunk.actor, 0) + 1
            person.activity = f"Completed {chunk.operation}: one {resource}"
            village.event(world, chunk.actor, "ore_mined" if chunk.operation == "mine" else "iron_smelted",
                          resource, person.activity)


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    base, state = village.context(world)
    mining = _state(world)
    if action == "village_mining_pause":
        chunk = next((chunk for chunk in mining.chunks if chunk.actor == world.actor_id and chunk.status == "active"), None)
        if chunk is None:
            return "REJECTED", "NO_MINING_COMMITMENT", "There is no active mining/smelting batch.", None
        chunk.status, chunk.end = "interrupted", state.minute
        state.residents[world.actor_id].available_at = state.minute
        village.event(world, world.actor_id, "mining_paused", chunk.operation,
                      "Paused the incomplete batch without output; used smelting inputs are not refunded.")
        return "APPLIED", "MINING_PAUSED", "Paused without unearned output or input refunds.", None
    if not mining.config.enabled or world.actor_id != mining.config.miner:
        return "REJECTED", "NOT_ASSIGNED_MINER", "Only the assigned smith may perform this enabled mining/smelting workflow.", None
    if action == "village_load_ore":
        from npc_sim import village_services
        reason = village_services.foot_load(world, world.actor_id, {ORE: args["quantity"]}, base.residents[base.smith].work)
        if reason:
            return "REJECTED", "ORE_LOAD_BLOCKED", reason, None
        village.event(world, world.actor_id, action, ORE, "Loaded actual owned ore for the forge; no purchase or delivery fee is invented.")
        return "APPLIED", "ORE_LOADED", "Carry this real ore along the mine road to the forge, then unload.", None
    operation = "mine" if action == "village_mine_ore" else "smelt"
    location = MINE if operation == "mine" else base.residents[base.smith].work
    equipment = PICK if operation == "mine" else FURNACE
    if world.actor.location_id != location:
        return "REJECTED", "MINING_NOT_PRESENT", "Travel to the actual mine or forge before working.", None
    if operation == "mine" and ore_needed(world) == 0 or operation == "smelt" and iron_needed(world) == 0:
        return "REJECTED", "DEMAND_MET", "Existing stock, cargo and active work cover current iron needs; do not mine surplus indefinitely.", None
    person, resident = state.residents[world.actor_id], base.residents[world.actor_id]
    if (person.life and person.life.work or person.timber_route or person.delivery is not None
            or person.busy_until > state.minute or state.horse_leader == world.actor_id
            or state.wagon and state.wagon.driver == world.actor_id):
        return "REJECTED", "WORK_COMMITTED", "Finish work, routes and animal commitments first.", None
    minutes = mining.config.mining_minutes if operation == "mine" else mining.config.smelting_minutes
    end = state.minute + minutes * village_life.cooldown_factor(world, world.actor_id)
    home = village.route(world, location, resident.home)
    if home is None or end + 5 * len(home) + 5 > state.minute // 1440 * 1440 + resident.bed_minute:
        return "REJECTED", "BEDTIME_CONFLICT", "Leave real travel and sleep time before bedtime.", None
    due = [state.minute // 1440 * 1440 + 1140]
    if (state.day_policy or base).breakfast:
        due.append(state.minute // 1440 * 1440 + resident.wake_minute + (state.day_policy or base).breakfast_delay)
    meal_route = village.route(world, location, "tavern")
    if any(state.minute < meal < end + (5 * len(meal_route) if meal_route is not None else 1440) + 5 for meal in due):
        return "REJECTED", "MEAL_CONFLICT", "Leave time to walk to the next required meal.", None
    blocker = _equipment_blocker(world, equipment, location, world.actor_id)
    if blocker:
        return "REJECTED", *blocker, None
    if operation == "mine":
        reason = handling.use_reason(world, [PICK])
        if reason:
            return "REJECTED", "MINING_BODY_REQUIREMENT", reason, None
    else:
        from npc_sim import village_firewood
        resources = [ORE] if village_firewood.enabled(world) else [ORE, "logs"]
        for resource in resources:
            if village_economy.usable_stock(world, location, resource, state.minute) - 1 < village_economy.protected_quantity(world, location, resource):
                return "REJECTED", "SMELTING_SUPPLIES", "Smelting needs actual uncommitted ore and configured fuel at the forge; arrange real deliveries.", None
        village_goods.take(world, location, ORE, 1)
        if not village_firewood.enabled(world):
            village_goods.take(world, location, "logs", 1)
    mining.chunks.append(VillageMiningChunk(actor=world.actor_id, operation=operation, location=location,
                                          equipment=equipment, start=state.minute, end=end))
    person.available_at = end
    person.activity = f"{'Mining ore' if operation == 'mine' else 'Smelting iron'} until {village.clock_time(end)}"
    village.event(world, world.actor_id, action, location, person.activity)
    return "APPLIED", "MINING_STARTED" if operation == "mine" else "SMELTING_STARTED", person.activity, None


def fuel_requests(world: "World", actor: str) -> list[tuple[str, int, dict[str, int]]]:
    from npc_sim import village_firewood
    if village_firewood.enabled(world):
        return []
    mining = village.context(world)[1].mining
    if not mining or not mining.config.enabled or actor != mining.config.miner:
        return []
    needed = iron_needed(world)
    return [(f"mine-fuel:{actor}", village.context(world)[1].minute + 180, {"logs": needed})] if needed else []


def observation(world: "World") -> dict[str, Any]:
    config = _state(world).config
    from npc_sim import village_firewood
    fuel = "firewood" if village_firewood.enabled(world) else "log"
    return {"mining": {"enabled": config.enabled, "miner": config.miner, "mine": MINE, "pick": PICK, "furnace": FURNACE,
                       "mining_minutes": config.mining_minutes, "smelting_minutes": config.smelting_minutes,
                       "policy": "The iron mine is reached from the village square by a real road. The assigned smith extracts one "
                       f"ore with the actual pick, carries owned ore back to the forge, and smelts one ore plus one {fuel} into one iron "
                       "using the actual furnace. Mine only enough for reserves, accepted orders and required trials. Ask the forester "
                       "for fuel in person. The daily trader remains an alternative: meet him at the square from noon to 18:00 and "
                       "use village_buy_iron to place and collect a funded purchase. Nothing creates free finished iron or moves cargo remotely."}}


def actions(world: "World") -> list[dict[str, Any]]:
    mining = _state(world)
    from npc_sim import village_firewood
    fuel = "firewood" if village_firewood.enabled(world) else "log"
    if blocked_until(world, world.actor_id):
        return [{"id": "village_mining_pause", "description": "Pause an incomplete batch; no output or refunds.",
                 "targets": [], "arguments_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                 "availability": "available", "reason": None}]
    if not mining.config.enabled or world.actor_id != mining.config.miner:
        return []
    return [{"id": action, "description": description, "targets": [],
             "arguments_schema": {"type": "object", "properties": properties,
                                  "required": list(properties), "additionalProperties": False},
             "availability": "available", "reason": None}
            for action, description, properties in [
                ("village_mine_ore", "Extract one ore with the actual pick at the mine, using real work time.", {}),
                ("village_load_ore", "Load actual owned ore at the mine; carry it physically to the forge and unload.",
                 {"quantity": {"type": "integer", "minimum": 1, "maximum": 3}}),
                ("village_smelt_iron", f"At the forge, consume one ore and one {fuel}; use the furnace and real time to produce one iron.", {}),
            ]]


def monitor(world: "World") -> dict[str, Any]:
    return {"activities": [{"actor": chunk.actor, "label": "Mining ore" if chunk.operation == "mine" else "Smelting iron",
                            "location": chunk.location, "start": chunk.start, "end": chunk.end, "status": chunk.status}
                           for chunk in _state(world).chunks]}

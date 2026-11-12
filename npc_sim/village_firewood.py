"""Physical firewood production, fueled batches and shared occupied cottage fires."""

import re
from math import ceil
from typing import TYPE_CHECKING, Any

from npc_sim import handling, village, village_economy, village_goods
from npc_sim.models import Entity, Scenario, VillageFirewoodConfig, VillageFirewoodState, VillageJob

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
    "An occupied household burns one unit for four hours in spring/autumn or two in winter, "
    "only between 18:00 and 07:00. Summer needs no household heating. Unoccupied hearths burn nothing. "
    "Cold homes are reported without health penalties. Season is operator-configured, not inferred weather. "
    "Only local fuel stocks and hearths are visible. Inspect supplies, remember shortages and use a physical "
    "board, inbox or conversation to request funded deliveries; nothing is delivered or learned remotely."
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
        supplier=supplier, household_buyer=buyer,
        households={f"hearth-{index // 4 + 1}": members[index:index + 4]
                    for index in range(0, len(members), 4)},
        businesses=[rid for rid, resident in base.residents.items()
                    if resident.job and resident.job.equipment in {"anvil", "oven", "tavern-hearth"}],
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
        if state.firewood.config.model_dump(include=identity) != config.model_dump(include=identity):
            raise ValueError("Installed firewood participants and household groups cannot be replaced")
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
    return {"spring": 240, "summer": 0, "autumn": 240, "winter": 120}[world.state.village.firewood.config.season]


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
    return target


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
    used = state.firewood.consumed.get(location, 0)
    return [(f"firewood:{state.minute // 1440}:{location}:{used}", state.minute + 360, {"firewood": target})]


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


def record_action(world: "World", action: str, already_consumed: int) -> None:
    if already_consumed:
        state = world.state.village.firewood
        location = world.actor.location_id
        state.consumed[location] = state.consumed.get(location, 0) + already_consumed
    else:
        _consume(world, world.actor.location_id)
    village.event(world, world.actor_id, "firewood_burned", world.actor.location_id,
                  f"Consumed {already_consumed or 1} real firewood for {action}; no additional log fuel charged.")


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
              "stock": village_economy.usable_stock(world, base.residents[buyer].work, "firewood", state.minute),
              "reserve": consumer_target(world, buyer),
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

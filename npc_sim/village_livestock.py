"""Physical livestock care, limited daily food production and real tavern cooking."""

from math import ceil
from typing import TYPE_CHECKING, Any, Literal

from npc_sim import handling, village, village_economy, village_goods, village_life, village_recycling
from npc_sim.models import Entity, VillageStockLot
from npc_sim.village_livestock_models import VillageLivestockConfig, VillageLivestockState, LivestockChunk

if TYPE_CHECKING:
    from npc_sim.world import World

VERBS = {"village_draw_livestock_water", "village_care_cow", "village_care_hens",
         "village_milk_cow", "village_collect_eggs", "village_cook_dairy", "village_livestock_pause",
         "village_make_plant_scraps", "village_compost"}
FREE_ACTIONS = {"look", "read", "say", "village_observe", "village_wait", "village_livestock_pause"}
BUCKET, BRUSH, PAIL, BASKET = "livestock-bucket", "livestock-brush", "milking-pail", "egg-basket"
PRICES = {"milk": 2, "eggs": 2, **village_recycling.PRICES}
SHELF = {"milk": 1440, "eggs": 2880, village_recycling.SCRAPS: 720}


def farmer(base):
    return next(rid for rid, resident in base.residents.items() if resident.work == "farm" and resident.job
                and "grain" in resident.job.outputs)


def fixtures(scenario) -> dict[str, Entity]:
    result = {
        "farm-cow": Entity(name="Millhaven dairy cow", kind="creature", location="farm",
                           description="One adult dairy cow; daily hay, real water and clean housing are required."),
        "hen-coop": Entity(name="Hen coop", kind="tool", location="farm",
                           description="Real housing and nests for the adult laying flock.", max_health=100),
        "cow-stall": Entity(name="Cow stall", kind="tool", location="farm",
                            description="Clean housing and a secure milking place.", max_health=100),
    }
    for key, name in [(BUCKET, "Livestock water bucket"), (BRUSH, "Livestock cleaning brush"),
                      (PAIL, "Milking pail"), (BASKET, "Egg basket")]:
        result[key] = Entity(name=name, kind="tool", location="farm", takable=True, max_health=100,
                             description=f"Real hand-held {name.lower()} for farm chores.")
    count = scenario.village.livestock.hens if scenario.village.livestock else 12
    for index in range(1, count + 1):
        result[f"farm-hen-{index:02d}"] = Entity(name=f"Adult laying hen {index}", kind="creature",
                                                location="farm", description="A real adult hen, not free egg stock.")
    return {**result, **village_recycling.fixtures()}


def _validate(scenario, config) -> None:
    base = scenario.village
    if not base or not (base.expanded_rules and base.time_based_turns and base.daily_life and base.ai_controlled):
        raise ValueError("Livestock requires expanded timed AI village life")
    if config.keeper != farmer(base):
        raise ValueError("Assign the actual farmer to the farm livestock")


def install_scenario(scenario) -> None:
    base = scenario.village
    base.livestock = VillageLivestockConfig(keeper=farmer(base))
    scenario.entities.update({key: value for key, value in fixtures(scenario).items() if key not in scenario.entities})
    for product, price in PRICES.items():
        base.prices.setdefault(product, price)
        if product in SHELF:
            base.spoil_after.setdefault(product, SHELF[product])
        for site in list(base.stocks) if product in village_recycling.PRICES else ["farm", "tavern", "bakery"]:
            base.stocks.setdefault(site, {}).setdefault(product, 0)


def validate_config(scenario) -> None:
    if scenario.village and scenario.village.livestock:
        _validate(scenario, scenario.village.livestock)
        if not set(fixtures(scenario)) <= set(scenario.entities):
            raise ValueError("Livestock requires its actual animals, housing and tools")


def initial_state(scenario):
    config = scenario.village.livestock if scenario.village else None
    return VillageLivestockState(config=config.model_copy(deep=True), started_day=scenario.village.start_minute // 1440) if config else None


def configure(world: "World", values: dict[str, Any]) -> None:
    config = VillageLivestockConfig.model_validate(values)
    _validate(world.scenario, config)
    _, state = village.context(world)
    if state.livestock:
        if state.livestock.config.hens != config.hens:
            raise ValueError("An installed flock cannot be replaced through configuration")
        if any(chunk.status == "active" for chunk in state.livestock.chunks):
            raise ValueError("Pause livestock work before reconfiguring")
    scenario = world.scenario.model_copy(deep=True)
    assert scenario.village is not None
    scenario.village.livestock = config
    for key, entity in fixtures(scenario).items():
        if state.livestock and key not in village_recycling.fixtures():
            continue
        previous = world.state.entities.get(key)
        if previous and (previous.kind != entity.kind or state.livestock is None and previous.location != entity.location):
            raise ValueError(f"Livestock fixture conflicts with an existing entity: {key}")
        world.state.entities.setdefault(key, entity)
    for site in set(state.stocks) | {"farm", "tavern", "bakery"}:
        for product in PRICES if site in {"farm", "tavern", "bakery"} else village_recycling.PRICES:
            if state.livestock is None and state.stocks.get(site, {}).get(product, 0):
                raise ValueError("Existing unvalidated livestock stock cannot be upgraded")
            state.stocks.setdefault(site, {}).setdefault(product, 0)
            state.stock_lots.setdefault(site, {}).setdefault(product, [])
    if state.livestock:
        state.livestock.config = config
    else:
        state.livestock = VillageLivestockState(config=config, started_day=state.minute // 1440)


def _state(world) -> VillageLivestockState:
    state = village.context(world)[1].livestock
    if state is None:
        raise ValueError("Livestock is not enabled")
    return state


def _rollover(world) -> None:
    state = _state(world)
    day = village.context(world)[1].minute // 1440
    for care in [state.cow, state.hens]:
        if care.day != day:
            care.day = day
            care.fed = care.watered = care.cleaned = False
            care.eggs = 0
            care.milk_sessions = []


def blocked_until(world, actor):
    state = village.context(world)[1].livestock
    return next((chunk.end for chunk in state.chunks if chunk.actor == actor and chunk.status == "active"), None) if state else None


def equipment_reserved(world, identifiers: set[str], actor=None) -> bool:
    state = village.context(world)[1].livestock
    return bool(state and any(chunk.status == "active" and chunk.actor != actor
                             and identifiers.intersection(chunk.equipment) for chunk in state.chunks))


def action_blocker(world, action, actor):
    if blocked_until(world, actor) and action not in FREE_ACTIONS:
        return "LIVESTOCK_COMMITTED", "Finish or explicitly pause the actual livestock chore before leaving."
    if action in village_life.PRODUCTION:
        job = world.scenario.village.residents[actor].job
        if job and job.equipment and equipment_reserved(world, {job.equipment}):
            return "EQUIPMENT_BUSY", "Livestock work has reserved the actual equipment."
    state = _state(world)
    if state and state.config.enabled and actor == state.config.keeper and (
            action.startswith("village_research") and action != "village_research_pause"
            or action in {"village_leisure", "village_band_attend", "village_quest"}):
        day = village.context(world)[1].minute // 1440
        if any(care.day != day or not care.cleaned for care in [state.cow, state.hens]):
            return "LIVESTOCK_CARE_DUE", "Today's real livestock care takes priority over optional research/leisure."
    return None


def _tools(world, actor, location, tools):
    from npc_sim import village_mining, village_research
    _, state = village.context(world)
    body = world.state.actors[actor]
    if "manipulate" not in body.body.capabilities or "hand" not in body.body.grips:
        return "LIVESTOCK_BODY_REQUIREMENT", "Actual manipulation and compatible hand grips are required."
    if set(tools).intersection({"cow-stall", "hen-coop", "compost-fork"}) and "farming" not in body.body.capabilities:
        return "LIVESTOCK_BODY_REQUIREMENT", "Farm animal care and compost work require the actual farming capability."
    job = world.scenario.village.residents[actor].job
    if location == "tavern" and job and job.equipment in tools and "hospitality" not in body.body.capabilities:
        return "LIVESTOCK_BODY_REQUIREMENT", "The actual inn cook needs hospitality capability to operate the hearth."
    for key in tools:
        entity = world.state.entities.get(key)
        if (not entity or entity.kind not in {"tool", "fixture"} or entity.hidden or entity.broken or
                entity.location != location and key not in body.inventory):
            return "LIVESTOCK_TOOL_REQUIRED", "Bring the actual usable housing and tools to the site."
        if "manipulate" not in body.body.capabilities or not set(entity.use_grips).intersection(body.body.grips):
            return "LIVESTOCK_BODY_REQUIREMENT", "Actual manipulation and compatible grips are required."
    if (village_mining.equipment_reserved(world, set(tools)) or
            village_research.equipment_reserved(world, set(tools)) or equipment_reserved(world, set(tools), actor)):
        return "EQUIPMENT_BUSY", "Another physical work chunk has reserved these tools."
    if state.maintenance and any(job.chunks and job.chunks[-1].status == "active"
                                 and set(tools).intersection(job.definition.equipment) for job in state.maintenance.jobs.values()):
        return "EQUIPMENT_BUSY", "Maintenance is using the tools."
    if state.social and any(interval.status == "active" and set(tools).intersection(interval.equipment)
                            for interval in state.social.intervals):
        return "EQUIPMENT_BUSY", "A committed activity is using the tools."
    if any(person.life and person.life.work and person.life.work.equipment in tools for person in state.residents.values()):
        return "EQUIPMENT_BUSY", "A committed batch is using the tools."
    if state.incidents:
        from npc_sim import village_events
        if any(village_events.delay_until(world, actor, location, tool) > state.minute for tool in tools):
            return "INCIDENT_BLOCKED", "A real incident blocks this site or equipment."
    return None


def _animals(world, operation):
    identifiers = ["farm-cow"] if operation in {"cow", "milk"} else [
        f"farm-hen-{index:02d}" for index in range(1, _state(world).config.hens + 1)]
    return all((entity := world.state.entities.get(key)) and entity.kind == "creature" and entity.location == "farm"
               and not entity.hidden and not entity.broken for key in identifiers)


def needed(world, product) -> int:
    state = village.context(world)[1]
    target = village_economy.production_targets(world, "farm").get(product, 0)
    available = village_economy.usable_stock(world, "farm", product, state.minute)
    # Production targets already deduct order cargo via order.loadable.
    available += sum(chunk.quantity for chunk in _state(world).chunks
                     if chunk.status == "active" and chunk.operation == ("milk" if product == "milk" else "eggs"))
    return max(0, target - available)


def feed_needs(world):
    state = village.context(world)[1].livestock
    if not state or not state.config.enabled:
        return {}
    base, town = village.context(world)
    day = town.minute // 1440
    horse = 0
    if town.horse_fed_day < day:
        horse = max(0, 1 - village_economy.usable_stock(world, base.residents[base.helper].work, "hay", town.minute)
                    - village_economy.protected_quantity(world, "farm", "hay"))
    return {"hay": int(state.cow.day != day or not state.cow.fed) + horse,
            "grain": village_recycling.hen_ration(world)[0] if state.hens.day != day or not state.hens.fed else 0}


def requests(world, actor):
    state = village.context(world)[1].livestock
    if not state or not state.config.enabled:
        return []
    _, town = village.context(world)
    if actor == state.config.keeper:
        return village_recycling.requests(world, actor)
    policy = village_economy.config(world)
    if policy and actor == policy.innkeeper:
        due = town.minute // 1440 * 1440 + 1140
        plans = village_economy.meal_requirements(world)
        portions = sum(plan["remaining_portions"] for plan in plans if plan["due"] == due)
        # A modest alternative, not a replacement for existing funded bread/fish orders.
        batches = min(2, ceil(portions / 2))
        return [(f"{town.minute // 1440}:dairy", due, {"milk": batches, "eggs": batches})] if batches else []
    return []


def _interrupt(world, chunk):
    town = village.context(world)[1]
    if chunk.operation in {"cow", "hens"}:
        care = getattr(_state(world), chunk.operation)
        care.progress += max(0, min(town.minute, chunk.end, (chunk.start // 1440 + 1) * 1440) - chunk.start)
    chunk.status, chunk.end = "interrupted", town.minute
    town.residents[chunk.actor].available_at = town.minute
    village.event(world, chunk.actor, "livestock_interrupted", chunk.operation,
                  "Interrupted physical work; no unearned output. Today's consumed feed and care progress persist.")


def advance(world):
    state, town = _state(world), village.context(world)[1]
    for chunk in state.chunks:
        if chunk.status != "active":
            continue
        life = town.residents[chunk.actor].life
        invalid = (chunk.start // 1440 != town.minute // 1440 or life and life.sleeping
                   or village.resident_location(world, chunk.actor) != chunk.location
                   or _tools(world, chunk.actor, chunk.location, chunk.equipment))
        if chunk.operation == "water":
            well = world.scenario.village.well_site
            invalid = invalid or not town.well_dug or world.state.entities[well].broken or world.state.entities[well].hidden or BUCKET not in world.state.actors[chunk.actor].inventory
        if chunk.operation in {"cow", "hens", "milk", "eggs"}:
            invalid = invalid or not _animals(world, chunk.operation) or BUCKET not in world.state.actors[chunk.actor].inventory and chunk.operation in {"cow", "hens"}
        if invalid:
            _interrupt(world, chunk)
        elif town.minute >= chunk.end:
            chunk.status = "completed"
            if chunk.operation == "water":
                if BUCKET not in world.state.actors[chunk.actor].inventory or not town.well_dug:
                    chunk.status = "interrupted"
                else:
                    state.water = 2
            elif chunk.operation in {"cow", "hens"}:
                care = getattr(state, chunk.operation)
                care.cleaned, care.progress = True, 0
                village_goods.put(world, "farm", "manure", 1)
            elif chunk.operation in {"milk", "eggs"}:
                # Demand may have been cancelled while working: discard unwanted yield.
                product = "milk" if chunk.operation == "milk" else "eggs"
                quantity = min(chunk.quantity, needed(world, product))
                if quantity:
                    village_goods.put(world, "farm", product, quantity)
            elif chunk.operation == "cook":
                village_goods.put(world, "tavern", "meals", 2, [
                    VillageStockLot(quantity=2, produced_at=town.minute, expires_at=town.minute + 1440,
                                    ingredients=["milk", "eggs"])])
            elif chunk.operation == "scraps":
                if chunk.output_expires_at is not None and chunk.output_expires_at <= town.minute:
                    village_goods.put(world, chunk.location, village_recycling.MATERIAL, chunk.quantity, [
                        VillageStockLot(quantity=chunk.quantity, produced_at=town.minute,
                                        source_resource=chunk.source_product, ingredients=[chunk.source_product])])
                else:
                    village_goods.put(world, chunk.location, village_recycling.SCRAPS, chunk.quantity, [
                        VillageStockLot(quantity=chunk.quantity, produced_at=town.minute, expires_at=chunk.output_expires_at,
                                        ingredients=chunk.ingredients, source_resource=chunk.source_product)])
            elif chunk.operation == "compost":
                village_goods.put(world, "farm", village_recycling.COMPOST, 1)
            town.role_contributions[chunk.actor] = town.role_contributions.get(chunk.actor, 0) + 1
            village.event(world, chunk.actor, "livestock_completed", chunk.operation,
                          f"Completed timed {chunk.operation} work with actual animals/ingredients.")
    _rollover(world)


def apply(world, action, args):
    base, town = village.context(world)
    state = _state(world)
    _rollover(world)
    actor, location = world.actor_id, world.actor.location_id
    if action == "village_livestock_pause":
        chunk = next((chunk for chunk in state.chunks if chunk.actor == actor and chunk.status == "active"), None)
        if not chunk:
            return "REJECTED", "NO_LIVESTOCK_COMMITMENT", "No active livestock work.", None
        _interrupt(world, chunk)
        return "APPLIED", "LIVESTOCK_PAUSED", "Paused; no output or refunds. Resume unfinished care today.", None
    operations: dict[str, Literal["water", "cow", "hens", "milk", "eggs", "cook", "scraps", "compost"]] = {
                 "village_draw_livestock_water": "water", "village_care_cow": "cow",
                 "village_care_hens": "hens", "village_milk_cow": "milk",
                 "village_collect_eggs": "eggs", "village_cook_dairy": "cook",
                 "village_make_plant_scraps": "scraps", "village_compost": "compost"}
    operation = operations[action]
    policy = village_economy.config(world)
    assigned = actor if operation == "scraps" else policy.innkeeper if operation == "cook" and policy else state.config.keeper
    if not state.config.enabled or actor != assigned:
        return "REJECTED", "NOT_LIVESTOCK_KEEPER", "Only the assigned farmer or inn cook may do this work.", None
    site = base.residents[actor].work if operation == "scraps" else base.well_site if operation == "water" else "tavern" if operation == "cook" else "farm"
    cooking_job = base.residents[assigned].job
    tools = {"water": [BUCKET], "cow": [BUCKET, BRUSH, "cow-stall"],
             "hens": [BUCKET, BRUSH, "hen-coop"], "milk": [PAIL, "cow-stall"],
             "eggs": [BASKET, "hen-coop"], "cook": [cooking_job.equipment if cooking_job else None],
             "scraps": [], "compost": ["compost-pile", "compost-fork"]}[operation]
    if location != site:
        return "REJECTED", "LIVESTOCK_NOT_PRESENT", "Walk to the actual well, farm or tavern first.", None
    if any(tool is None for tool in tools):
        return "REJECTED", "LIVESTOCK_TOOL_REQUIRED", "The cook needs actual configured cooking equipment.", None
    tools = [tool for tool in tools if tool is not None]
    blocker = _tools(world, actor, location, tools)
    if blocker:
        return "REJECTED", *blocker, None
    reason = handling.use_reason(world, [tool for tool in tools if world.state.entities[tool].takable])
    if reason or "manipulate" not in world.actor.body.capabilities or "hand" not in world.actor.body.grips:
        return "REJECTED", "LIVESTOCK_BODY_REQUIREMENT", reason, None
    if operation in {"water", "cow", "hens"} and BUCKET not in world.actor.inventory:
        return "REJECTED", "WATER_BUCKET_REQUIRED", "Carry the real livestock bucket; water is not teleported.", None
    if operation in {"cow", "hens", "milk", "eggs"} and not _animals(world, operation):
        return "REJECTED", "LIVESTOCK_ANIMALS_REQUIRED", "The actual living animals must be here.", None
    person, resident = town.residents[actor], base.residents[actor]
    if (person.life and person.life.work or person.timber_route or person.delivery or person.busy_until > town.minute
            or town.horse_leader == actor or town.wagon and town.wagon.driver == actor):
        return "REJECTED", "WORK_COMMITTED", "Finish existing work and physical transport first.", None
    quantity, session = 0, ""
    source_product, expires_at, ingredients = "", None, []
    care = state.cow if operation in {"cow", "milk"} else state.hens
    minutes = {"water": 5, "cow": 15, "hens": 10, "milk": 15, "eggs": 10, "cook": 15,
               "scraps": 5 * args.get("quantity", 1), "compost": 20}[operation]
    factor = village_life.cooldown_factor(world, actor)
    if operation in {"cow", "hens"}:
        if care.cleaned:
            return "REJECTED", "CARE_DONE", "Today's care is already complete.", None
        minutes = max(5, minutes * factor - care.progress)
    else:
        minutes *= factor
    end = town.minute + minutes
    home = village.route(world, location, resident.home)
    meals = [town.minute // 1440 * 1440 + 1140]
    day_policy = town.day_policy or base
    if day_policy.breakfast:
        meals.append(town.minute // 1440 * 1440 + resident.wake_minute + day_policy.breakfast_delay)
    meal_route = village.route(world, location, "tavern")
    if home is None or end + len(home) * 5 + 5 > town.minute // 1440 * 1440 + resident.bed_minute:
        return "REJECTED", "BEDTIME_CONFLICT", "Allow real travel home and sleep.", None
    if any(town.minute < due < end + (len(meal_route) * 5 if meal_route is not None else 1440) + 5 for due in meals):
        return "REJECTED", "MEAL_CONFLICT", "Allow travel to the next required meal.", None
    job = resident.job
    if (operation in {"milk", "eggs", "scraps", "compost"} and job and person.next_work < end
            and town.economy and all(village_economy.usable_stock(world, location, resource, town.minute) >= amount
                                     for resource, amount in job.inputs.items())
            and (not job.equipment or not world.state.entities[job.equipment].broken)
            and any(order.status == "accepted" and order.supplier == actor and order.product in job.outputs
                    and order.loadable and max(town.minute, person.next_work) + job.duration <= order.deadline <= end + job.duration
                    and village_economy.loadable_for_order(world, order) < order.loadable
                    for order in town.economy.orders.values())):
        return "REJECTED", "FUNDED_GRAIN_FIRST" if actor == state.config.keeper else "FUNDED_PRODUCTION_FIRST", "Finish feasible urgent funded production before optional dairy/recycling.", None
    if operation == "water":
        if not town.well_dug or world.state.entities[base.well_site].broken or world.state.entities[base.well_site].hidden:
            return "REJECTED", "NO_USABLE_WATER", "The actual well must be completed and usable; repair it first.", None
        if state.water:
            return "REJECTED", "BUCKET_HAS_WATER", "Use the real water already in the bucket.", None
    elif operation in {"cow", "hens"}:
        grain, scraps = village_recycling.hen_ration(world)
        resource, amount = ("hay", 1) if operation == "cow" else ("grain", grain)
        protected = village_economy.protected_quantity(world, "farm", resource)
        if resource == "hay" and town.horse_fed_day < town.minute // 1440:
            horse_stock = village_economy.usable_stock(world, base.residents[base.helper].work, "hay", town.minute)
            protected = max(protected, max(0, 1 - horse_stock))
        if not care.fed and village_economy.usable_stock(world, "farm", resource, town.minute) - amount < protected:
            return "REJECTED", "LIVESTOCK_FEED_REQUIRED", "Produce real surplus feed; horse hay and accepted grain commitments are protected.", None
        if not care.watered and state.water < 1:
            return "REJECTED", "LIVESTOCK_WATER_REQUIRED", "Draw actual well water with the bucket and carry it here.", None
        if not care.fed:
            village_goods.take(world, "farm", resource, amount)
            if operation == "hens" and scraps:
                village_goods.take(world, "farm", village_recycling.SCRAPS, scraps)
            care.fed = True
        if not care.watered:
            state.water -= 1
            care.watered = True
    elif operation in {"milk", "eggs"}:
        if not (care.fed and care.watered and care.cleaned):
            return "REJECTED", "LIVESTOCK_CARE_REQUIRED", "Feed, water and clean the animals today before collecting food.", None
        clock = town.minute % 1440
        if not 480 <= clock < 1080 or end % 1440 > 1080:
            return "REJECTED", "LIVESTOCK_SESSION_TIME", "Use practical daytime collection windows, 08:00–18:00.", None
        product = "milk" if operation == "milk" else "eggs"
        session = "morning" if clock < 780 else "afternoon"
        if operation == "milk" and (session in care.milk_sessions or session == "morning" and end % 1440 > 780):
            return "REJECTED", "MILKING_SESSION_USED", "At most one milking in each 08:00–13:00 / 13:00–18:00 window.", None
        previous = next((chunk for chunk in reversed(state.chunks) if chunk.operation == "milk"
                         and chunk.start // 1440 == town.minute // 1440), None)
        if operation == "milk" and previous and town.minute < previous.start + 240:
            return "REJECTED", "MILKING_RECOVERY_TIME", f"Leave four real game hours between milking starts; next eligible at {village.clock_time(previous.start + 240)}.", None
        if operation == "eggs" and care.eggs:
            return "REJECTED", "EGGS_COLLECTED_TODAY", "At most one laying/collection session today.", None
        quantity = min(6 if operation == "milk" else state.config.hens, needed(world, product))
        if not quantity:
            return "REJECTED", "DEMAND_MET", "Stock/cargo/work covers actual reserves and accepted orders.", None
        if operation == "milk":
            care.milk_sessions.append(session)
        else:
            care.eggs = quantity
    elif operation == "scraps":
        source_product, quantity = args["product"], args["quantity"]
        lots = town.stock_lots.get(location, {}).get(source_product, [])
        available = village_economy.usable_stock(world, location, source_product, end)
        if (source_product == village_recycling.SCRAPS or not lots or
                not all(village_recycling.safe_lot(world, source_product, lot) for lot in lots)
                or available - quantity < village_recycling.protected(world, location, source_product)):
            return "REJECTED", "UNSAFE_OR_COMMITTED_SCRAPS", "Use only actual unexpired, proven plant-only surplus beyond food/feed/orders; rotten or animal food is never feed.", None
        taken = village_goods.take(world, location, source_product, quantity, required_fresh_until=end)
        expiry = min((lot.expires_at for lot in taken if lot.expires_at is not None), default=end + 720)
        expires_at = min(end + 720, expiry)
        ingredients = sorted({item for lot in taken for item in (lot.ingredients or [source_product])})
    elif operation == "compost":
        source_product = args["material"]
        if (village_economy.usable_stock(world, "farm", village_recycling.COMPOST, town.minute) >= 2):
            return "REJECTED", "DEMAND_MET", "Keep only a small usable compost fertilizer buffer.", None
        if (village_economy.usable_stock(world, "farm", source_product, end) - 2 <
                village_economy.protected_quantity(world, "farm", source_product)):
            return "REJECTED", "COMPOST_MATERIAL_REQUIRED", "Deliver two actual uncommitted compost-material or safe plant-scraps to the farm.", None
        lots = town.stock_lots.get("farm", {}).get(source_product, [])
        if source_product == village_recycling.SCRAPS and not all(village_recycling.safe_lot(world, source_product, lot) for lot in lots):
            return "REJECTED", "UNSAFE_COMPOST_INPUT", "Scraps must retain actual plant provenance; expired scraps become compost-material first.", None
        if source_product == village_recycling.MATERIAL and not all(
                lot.source_resource and village_recycling.food(world, lot.source_resource) for lot in lots):
            return "REJECTED", "UNSAFE_COMPOST_INPUT", "Compost material must retain provenance from actual spoiled food.", None
        village_goods.take(world, "farm", source_product, 2, required_fresh_until=end)
    elif operation == "cook":
        target = village_economy.production_targets(world, "tavern").get("meals", 0)
        if village_economy.usable_stock(world, "tavern", "meals", town.minute) >= target:
            return "REJECTED", "DEMAND_MET", "Existing meals cover current demand.", None
        if any(village_economy.usable_stock(world, "tavern", resource, town.minute) - 1 <
               village_economy.protected_quantity(world, "tavern", resource) for resource in ["milk", "eggs"]):
            return "REJECTED", "DAIRY_INGREDIENTS_REQUIRED", "Deliver one actual uncommitted milk and egg to the tavern.", None
        for resource in ["milk", "eggs"]:
            village_goods.take(world, "tavern", resource, 1)
    state.chunks.append(LivestockChunk(actor=actor, operation=operation, location=location, equipment=tools,
                                      start=town.minute, end=end, quantity=quantity, session=session,
                                      source_product=source_product, output_expires_at=expires_at, ingredients=ingredients))
    person.available_at = end
    person.activity = f"Livestock {operation} until {village.clock_time(end)}"
    village.event(world, actor, action, location, person.activity)
    return "APPLIED", "LIVESTOCK_STARTED", person.activity, None


def observation(world):
    state = _state(world)
    return {"livestock": {"enabled": state.config.enabled, "keeper": state.config.keeper,
                         "hen_count": state.config.hens, "cow": state.cow.model_dump(), "hens": state.hens.model_dump(),
                         "bucket_water": state.water,
                         "policy": "Daily: carry the livestock bucket to the completed well, draw water (5m), carry it to farm; "
                         "cow care 15m consumes 1 hay, hen care 10m consumes ceil(hens/4) grain. Hold the bucket for care. "
                         "Unfinished care resumes today without duplicate feed; new days require new care, never backdated production. "
                         "Care earns one manure per housing only from real fed animals. Protect horse hay and funded grain. "
                         "Milk: at most 6 units each 08:00–13:00 and 13:00–18:00 (15m/session), at least four hours between starts. Eggs: at most one per hen/day (10m). "
                         "Produce only for reserves/accepted orders; meet buyers in person and use normal funded orders/delivery. "
                         "Inn: one milk + one egg, actual cooking equipment and 15m yields two ordinary paid meals. "
                         "No butter/cheese; existing bread/fish commitments and meals/sleep take priority. "
                         "Recycling: deliberately convert only proven, unexpired surplus plant food to plant-scraps (1:1, 5m/unit). "
                         "Never use fish, milk, eggs, animal meals or rotten/mouldy food as feed. Scraps last at most 12h, "
                         "never longer than the original food. For twelve hens, up to one scrap substitutes one of three grain "
                         "units; at least two balanced grain remain. Cow hay is never replaced. Actual spoiled food becomes "
                         "finite compost-material at its physical location (never feed). Trade/haul these resources using "
                         "normal funded in-person orders. At farm, village_compost uses the real pile/fork, two material or "
                         "safe scrap units and 20m to make one compost fertilizer (buffer two). village_fertilize_compost "
                         "consumes one compost for the same three-harvest benefit as manure, without creating manure."}}


def actions(world):
    state = _state(world)
    policy = village_economy.config(world)
    if blocked_until(world, world.actor_id):
        verbs = ["village_livestock_pause"]
    elif not state.config.enabled:
        return []
    elif world.actor_id == state.config.keeper:
        verbs = sorted(VERBS - {"village_livestock_pause", "village_cook_dairy"})
    elif policy and world.actor_id == policy.innkeeper:
        verbs = ["village_cook_dairy", "village_make_plant_scraps"]
    else:
        verbs = ["village_make_plant_scraps"]
    from npc_sim import village_research
    properties = {
        "village_make_plant_scraps": {"product": {"type": "string", "enum": sorted(village_research.prices(world))},
                                    "quantity": {"type": "integer", "minimum": 1, "maximum": 3}},
        "village_compost": {"material": {"type": "string", "enum": [village_recycling.MATERIAL, village_recycling.SCRAPS]}},
    }
    return [{"id": verb, "description": verb.replace("village_", "").replace("_", " "),
             "targets": [], "arguments_schema": {"type": "object", "properties": properties.get(verb, {}),
                                               "required": list(properties.get(verb, {})), "additionalProperties": False},
             "availability": "available", "reason": None} for verb in verbs]


def monitor(world):
    return {"activities": [{"actor": chunk.actor, "label": f"Livestock {chunk.operation}",
                            "location": chunk.location, "start": chunk.start, "end": chunk.end, "status": chunk.status}
                           for chunk in _state(world).chunks]}

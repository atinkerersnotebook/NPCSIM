"""Occupational experiments and durable, executable discoveries."""

import hashlib
from typing import TYPE_CHECKING, Any

from npc_sim import village, village_economy, village_goods, village_life
from npc_sim.models import Entity
from npc_sim.village_research_models import (
    ResearchChunk, ResearchProject, ResearchProposal, VillageResearchConfig, VillageResearchState,
)

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"village_research_propose", "village_research", "village_research_pause",
         "village_research_make", "village_research_use", "village_research_share"}
FREE_ACTIONS = {"look", "read", "say", "village_observe", "village_wait", "village_research_pause"}
STUDY_ACTIONS = {"village_research_propose", "village_research"}
KINDS = {
    "baking": ["food", "material"], "farming": ["crop", "material"],
    "hospitality": ["food", "material"], "herbalism": ["food", "material"],
    "fishing": ["food", "tool"], "smithing": ["tool", "material"],
    "carpentry": ["tool", "material"], "milling": ["material", "tool"],
    "animal_care": ["tool", "material"], "trade": ["tool", "material"],
    "forestry": ["tool", "material"], "patrol": ["tool", "material"],
}
FOOD_INPUTS = {"flour", "bread", "grain", "fish", "herbs", "tea", "manure"}


def _state(world: "World") -> VillageResearchState:
    state = village.context(world)[1].research
    if state is None:
        raise ValueError("Occupational research is not enabled")
    return state


def validate_config(scenario) -> None:
    base = scenario.village
    if base is not None and base.research is not None:
        if not (base.ai_controlled and base.daily_life and base.time_based_turns and base.expanded_rules):
            raise ValueError("Research requires an expanded AI-controlled timed village")


def initial_state(scenario) -> VillageResearchState | None:
    config = scenario.village.research if scenario.village else None
    return VillageResearchState(config=config.model_copy(deep=True)) if config else None


def configure(world: "World", values: dict[str, Any]) -> None:
    config = VillageResearchConfig.model_validate(values)
    base, state = village.context(world)
    if not (base.ai_controlled and base.daily_life and base.time_based_turns and base.expanded_rules):
        raise ValueError("Research requires an expanded AI-controlled timed village")
    if state.research is None:
        state.research = VillageResearchState(config=config)
    else:
        if any(_active(project) for project in state.research.projects.values()):
            raise ValueError("Pause active research/production before reconfiguring research")
        state.research.config = config


def _active(project: ResearchProject) -> ResearchChunk | None:
    return project.chunks[-1] if project.chunks and project.chunks[-1].status == "active" else None


def discoveries(world: "World") -> dict[str, ResearchProject]:
    state = village.context(world)[1].research
    return {project.product: project for project in state.projects.values()
            if project.discovered_at is not None} if state else {}


def prices(world: "World") -> dict[str, int]:
    base, state = village.context(world)
    result = dict(base.prices)
    if state.firewood is not None:
        from npc_sim import village_firewood
        result.setdefault("firewood", village_firewood.PRICE)
    if state.mining is not None:
        result.setdefault("iron-ore", 1)
    if state.livestock is not None:
        from npc_sim import village_livestock
        for product, price in village_livestock.PRICES.items():
            result.setdefault(product, price)
    for product, project in sorted(discoveries(world).items(), key=lambda item: item[1].discovered_at or 0):
        result[product] = max(1, sum(result[resource] * amount for resource, amount in project.proposal.inputs.items()))
    return result


def shelf_life(world: "World", product: str) -> int | None:
    if village.context(world)[1].livestock:
        from npc_sim import village_livestock
        if product in village_livestock.SHELF:
            return village_livestock.SHELF[product]
    project = discoveries(world).get(product)
    if project:
        return 1440 if project.proposal.kind in {"food", "crop"} else None
    return village.context(world)[0].spoil_after.get(product)


def blocked_until(world: "World", actor: str) -> int | None:
    return next((chunk.end for project in _state(world).projects.values()
                 if (chunk := _active(project)) and chunk.actor == actor), None)


def equipment_reserved(world: "World", identifiers: set[str], actor: str | None = None) -> bool:
    state = village.context(world)[1].research
    return bool(state and any((chunk := _active(project)) and chunk.actor != actor
                             and identifiers.intersection(chunk.equipment)
                             for project in state.projects.values()))


def _tools(world: "World", project: ResearchProject) -> tuple[str, str] | None:
    actor = world.state.actors[project.owner]
    for identifier in project.proposal.equipment:
        entity = world.state.entities.get(identifier)
        if (entity is None or entity.kind in {"location", "door", "discovery"} or entity.hidden or entity.broken
                or entity.location != project.location and identifier not in actor.inventory):
            return "MISSING_RESEARCH_TOOLS", "The actual research/production equipment must be usable at the site."
        if not set(entity.use_grips).intersection(actor.body.grips):
            return "INCOMPATIBLE_GRIP", "The real equipment requires a compatible body grip."
    tools = set(project.proposal.equipment)
    from npc_sim import village_livestock
    if village_livestock.equipment_reserved(world, tools):
        return "EQUIPMENT_BUSY", "Livestock work has reserved the actual equipment."
    _, state = village.context(world)
    if equipment_reserved(world, tools, project.owner):
        return "EQUIPMENT_BUSY", "Another experiment has reserved the actual equipment."
    from npc_sim import village_mining
    if village_mining.equipment_reserved(world, tools):
        return "EQUIPMENT_BUSY", "Ore production has reserved the actual equipment."
    if state.maintenance and any(job.chunks and job.chunks[-1].status == "active"
                                 and tools.intersection(job.definition.equipment)
                                 for job in state.maintenance.jobs.values()):
        return "EQUIPMENT_MAINTENANCE", "Town maintenance has reserved the actual equipment."
    if state.social and any(interval.status == "active" and tools.intersection(interval.equipment)
                            for interval in state.social.intervals):
        return "EQUIPMENT_BUSY", "A committed activity is using the actual equipment."
    for person in state.residents.values():
        if person.life and person.life.work and person.life.work.equipment in tools:
            return "EQUIPMENT_BUSY", "The equipment is processing a committed production batch."
    if state.incidents:
        from npc_sim import village_events
        if any(village_events.delay_until(world, project.owner, project.location, tool) > state.minute
               for tool in project.proposal.equipment or [None]):
            return "INCIDENT_BLOCKED", "A real incident delays work at this site."
    return None


def _supplies(world: "World", project: ResearchProject, operation: str) -> tuple[str, str] | None:
    if operation == "research" and project.trial_materials_consumed:
        return None
    inputs = {project.product: 1} if operation == "use" else project.proposal.inputs
    _, state = village.context(world)
    for resource, amount in inputs.items():
        stock = state.stocks.get(project.location, {}).get(resource, 0)
        lots = state.stock_lots.get(project.location, {}).get(resource, [])
        if sum(lot.quantity for lot in lots) != stock:
            raise ValueError(f"Research freshness ledger disagrees with stock: {resource}")
        available = village_economy.usable_stock(world, project.location, resource, state.minute)
        if available - amount < village_economy.protected_quantity(world, project.location, resource):
            return "RESEARCH_SUPPLIES", "Arrange real usable supplies beyond reserves and accepted orders; experiments cannot consume committed stock."
    return None


def _downtime(world: "World", actor: str, end: int) -> tuple[str, str] | None:
    base, state = village.context(world)
    person, resident = state.residents[actor], base.residents[actor]
    if "patrol" in resident.skills and (person.next_work <= state.minute or state.minute < person.next_work < end):
        return "PATROL_DUE", "Perform scheduled patrols before research, and leave time for the next patrol."
    if any(task.assignee == actor and task.status not in {"completed", "cancelled"} for task in state.tasks.values()):
        return "TASK_COMMITTED", "Finish required village tasks before occupational research."
    if actor == base.helper and state.horse_fed_day < state.minute // 1440:
        return "ANIMAL_CARE_DUE", "Care for the real horse before starting occupational research."
    if actor == base.courier and village_economy.delivery_runs(world, actor):
        return "DELIVERY_DUE", "Move ready funded shipments before researching new items."
    if state.economy:
        for order in state.economy.orders.values():
            if order.status == "accepted" and order.supplier == actor and order.loadable:
                return "ORDER_COMMITTED", "Fulfil accepted supplier orders before experiments."
            if order.status in {"accepted", "proposed"} and order.buyer == actor and state.minute <= order.deadline < end:
                return "ORDER_COMMITTED", "This experiment overlaps an outstanding procurement deadline."
    if state.maintenance:
        from npc_sim import village_maintenance
        blocker = village_maintenance.action_blocker(world, "village_research", actor)
        if blocker:
            return blocker
        for job in state.maintenance.jobs.values():
            if job.definition.assignee == actor and job.completed_at is None and state.minute < job.release_at < end:
                return "MAINTENANCE_DUE", "Leave time for upcoming assigned maintenance."
    if resident.job and resident.job.location == world.actor.location_id and person.next_work <= state.minute:
        blocker = village_economy.production_blocker(world, "village_work")
        if (blocker is None and all(village_economy.usable_stock(world, resident.work, resource, state.minute) >= amount
                                    for resource, amount in resident.job.inputs.items())):
            return "WORK_DEMAND_DUE", "Ordinary production is feasible and needed; research is for workday downtime."
    if state.social:
        for plan in state.social.band_plans.values():
            if plan.status == "invited" and actor in plan.accepted + plan.audience and state.minute < plan.ends_at and end > plan.starts_at:
                return "SOCIAL_COMMITTED", "Research overlaps an accepted music commitment."
        for plan in state.social.social_plans.values():
            if plan.status == "invited" and actor in plan.accepted and state.minute < plan.ends_at and end > plan.starts_at:
                return "SOCIAL_COMMITTED", "Research overlaps an accepted social commitment."
    return None


def _guard(world: "World", project: ResearchProject, operation: str, end: int) -> tuple[str, str] | None:
    base, state = village.context(world)
    actor = world.actor_id
    person, resident = state.residents[actor], base.residents[actor]
    life = person.life
    if world.actor.location_id != project.location:
        return "NOT_PRESENT", "Walk to the actual research/production site."
    if "manipulate" not in world.actor.body.capabilities:
        return "MISSING_CAPABILITY", "Actual manipulation capability is required for experiments and production."
    if life is None or life.sleeping or village_life.rest_due(resident, life, state.minute):
        return "REST_DUE", "Actual sleep takes precedence over research."
    if life.hungry or village_life.meal_due(life, state.minute):
        return "MEAL_DUE", "Eat real food before research or discovered production."
    if (life.work or person.timber_route or person.delivery is not None or person.busy_until > state.minute
            or state.horse_leader == actor or state.wagon and state.wagon.driver == actor):
        return "WORK_COMMITTED", "Finish work, deliveries and animal commitments first."
    if person.available_at > state.minute or blocked_until(world, actor):
        return "ACTOR_BUSY", "Finish or explicitly pause the current time reservation."
    if operation == "research":
        if not resident.wake_minute <= state.minute % 1440 < 1020 or end > state.minute // 1440 * 1440 + 1020:
            return "OUTSIDE_RESEARCH_HOURS", "Occupational research uses free workday time, ending by 17:00."
        blocker = _downtime(world, actor, end)
        if blocker:
            return blocker
    elif end > state.minute // 1440 * 1440 + village_life.production_closing_minute(world, actor):
        closing = village_life.production_closing_minute(world, actor)
        return "OUTSIDE_WORK_HOURS", f"Discovered production/use must finish by {village.clock_time(closing)}."
    home = village.route(world, project.location, resident.home)
    if home is None or end + 5 * len(home) + 5 > state.minute // 1440 * 1440 + resident.bed_minute:
        return "BEDTIME_CONFLICT", "Leave real travel time home and time to sleep."
    policy = state.day_policy or base
    due = [state.minute // 1440 * 1440 + 1140]
    if policy.breakfast:
        due.append(state.minute // 1440 * 1440 + resident.wake_minute + policy.breakfast_delay)
    meal_route = village.route(world, project.location, "tavern")
    if any(state.minute < time < end + (5 * len(meal_route) if meal_route is not None else 1440) + 5 for time in due):
        return "MEAL_CONFLICT", "Leave time to walk to the next required meal."
    return _tools(world, project) or _supplies(world, project, operation)


def _proposal(world: "World", args: dict[str, Any]) -> tuple[str, str, str, None]:
    proposal = ResearchProposal.model_validate(args)
    base, state = village.context(world)
    resident = base.residents[world.actor_id]
    research = _state(world)
    if len(proposal.title.strip()) < 3 or len(proposal.description.strip()) < 10:
        return "REJECTED", "INVALID_RESEARCH_NAME", "Give the research a meaningful title and description.", None
    if proposal.skill not in resident.skills or proposal.kind not in KINDS.get(proposal.skill, []):
        return "REJECTED", "INVALID_RESEARCH_FIELD", "Choose a supported discovery related to your actual occupational skill.", None
    known = set(research.known.get(world.actor_id, []))
    products = discoveries(world)
    valid_inputs = set(base.prices) | {products[key].product for key in known if key in products}
    if state.mining is not None and world.actor_id == state.mining.config.miner:
        valid_inputs.add("iron-ore")
    if state.livestock:
        from npc_sim import village_livestock
        valid_inputs |= set(village_livestock.PRICES)
    # Known stores also reveal a delivered discovered material, not its recipe or remote location.
    valid_inputs |= set(state.stocks.get(world.actor.location_id, {})) & set(products)
    if any(resource not in valid_inputs for resource in proposal.inputs):
        return "REJECTED", "UNKNOWN_RESEARCH_INPUT", "Use real known inputs, not imaginary resources or undiscovered ingredients.", None
    if proposal.kind in {"food", "crop"}:
        edible = FOOD_INPUTS | {key for key, value in products.items() if value.proposal.kind in {"food", "crop"}}
        if state.livestock:
            edible |= {"milk", "eggs"}
        if (not set(proposal.inputs) <= edible or not set(proposal.inputs) - {"manure"}
                or proposal.kind == "food" and "manure" in proposal.inputs):
            return "REJECTED", "INVALID_FOOD_RECIPE", "Food needs actual edible ingredients; crop trials need real seed/plant stock, optionally manure.", None
    if proposal.kind == "crop" and proposal.duration < 60:
        return "REJECTED", "CROP_TIME_REQUIRED", "Growing a discovered crop requires at least 60 real work minutes.", None
    if resident.job and resident.job.equipment and resident.job.equipment not in proposal.equipment:
        return "REJECTED", "RESEARCH_EQUIPMENT_REQUIRED", "Use your occupation's actual production equipment.", None
    if any(project.owner == world.actor_id and project.discovered_at is None for project in research.projects.values()):
        return "REJECTED", "RESEARCH_ALREADY_OPEN", "Finish the current occupational research project before proposing another.", None
    title = proposal.title.strip().casefold()
    if any(project.proposal.title.strip().casefold() == title
           and (project.owner == world.actor_id or project.product in known)
           for project in research.projects.values()):
        return "REJECTED", "RESEARCH_ALREADY_EXISTS", "That named research/discovery already exists; continue it or learn it in person.", None
    key = "discovery-" + hashlib.sha256(f"{world.actor_id}:{title}".encode()).hexdigest()[:16]
    if key in world.state.entities or key in prices(world):
        return "REJECTED", "RESEARCH_ID_CONFLICT", "That discovery identity is already used by the world.", None
    project = ResearchProject(owner=world.actor_id, location=resident.work, proposal=proposal, product=key,
                              required_minutes=research.config.required_minutes,
                              required_successes=research.config.required_successes)
    # Proposing consumes no trial materials; physical tools, needs and obligations still apply.
    blocker = _guard(world, project, "research", state.minute + 5)
    if blocker and blocker[0] != "RESEARCH_SUPPLIES":
        return "REJECTED", *blocker, None
    research.projects[key] = project
    research.known.setdefault(world.actor_id, []).append(key)
    village.event(world, world.actor_id, "research_proposed", key,
                  f"Proposed {proposal.title}; verified {proposal.kind} recipe using real inputs. "
                  "This is an experiment, not yet a discovery.")
    return "APPLIED", "RESEARCH_PROPOSED", "Research registered. Work and successful trials are required before this recipe exists.", None


def _trial_success(world: "World", project: ResearchProject) -> bool:
    seed = f"{world.state.world_id}:{project.product}:{project.attempts}"
    roll = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:4], "big") % 100
    experience = min(20, project.successes * 5 + project.attempts * 2)
    return roll < 60 + experience


def _pause(world: "World", project: ResearchProject, credit: bool) -> None:
    _, state = village.context(world)
    chunk = _active(project)
    if chunk is None:
        raise ValueError("No active research chunk")
    factor = max(1, (chunk.end - chunk.start) // max(1, chunk.minutes))
    earned = min(chunk.minutes, (state.minute - chunk.start) // (5 * factor) * 5) if credit else 0
    if chunk.operation == "research":
        project.worked_minutes += earned
    chunk.end, chunk.minutes, chunk.status = state.minute, earned, "interrupted"
    state.residents[chunk.actor].available_at = state.minute
    state.residents[chunk.actor].activity = f"Paused: {project.proposal.title}"
    village.event(world, chunk.actor, "research_paused", project.product,
                  "Paused work; only earned study progress is retained. Used trial/batch materials are not refunded.")


def advance(world: "World") -> None:
    research = _state(world)
    _, state = village.context(world)
    for key, project in research.projects.items():
        chunk = _active(project)
        if chunk is None:
            continue
        person = state.residents[chunk.actor]
        working = project.model_copy(update={
            "owner": chunk.actor, "location": chunk.location,
            "proposal": project.proposal.model_copy(update={"equipment": chunk.equipment})})
        if (person.life and person.life.sleeping
                or village.resident_location(world, chunk.actor) != chunk.location
                or _tools(world, working)):
            _pause(world, project, credit=False)
            continue
        if state.minute < chunk.end:
            continue
        chunk.status = "completed"
        if chunk.operation == "research":
            project.worked_minutes += chunk.minutes
            if project.worked_minutes % 60 == 0:
                project.attempts += 1
                chunk.succeeded = _trial_success(world, project)
                project.successes += int(chunk.succeeded)
                project.trial_materials_consumed = False
                village.event(world, project.owner, "research_trial", key,
                              f"{project.proposal.title}: trial {project.attempts} "
                              f"{'succeeded' if chunk.succeeded else 'failed'} after real study and materials.")
            if project.worked_minutes >= project.required_minutes and project.successes >= project.required_successes:
                project.discovered_at = state.minute
                world.state.entities[key] = Entity(
                    name=project.proposal.title, kind="discovery", location=project.location,
                    description=project.proposal.description + " A verified recipe, not free stock.",
                    readable_text=f"Verified {project.proposal.kind}: {project.proposal.inputs} -> one {key}; "
                                  f"{project.proposal.duration} work minutes; equipment {project.proposal.equipment}.")
                village.event(world, project.owner, "research_discovered", key,
                              f"Discovered {project.proposal.title}. The real recipe is now available to make and use; no inventory was created.")
        elif chunk.operation == "produce":
            village_goods.put(world, chunk.location, project.product, 1, ingredients=list(project.proposal.inputs))
            village.event(world, chunk.actor, "discovery_produced", key,
                          f"Made one {project.proposal.title} using actual inputs and {chunk.minutes} work minutes.")
        elif project.proposal.kind in {"food", "crop"}:
            village_goods.put(world, chunk.location, "meals", 2, ingredients=[project.product])
            village.event(world, chunk.actor, "discovery_used", key,
                          f"Prepared one {project.proposal.title} into two real meal portions.")
        elif chunk.target:
            entity = world.state.entities[chunk.target]
            if entity.health is None or entity.max_health is None:
                raise ValueError("Discovered tool repair target lost its repairable health")
            entity.health = min(entity.max_health, entity.health + 25)
            village.event(world, chunk.actor, "discovery_used", chunk.target,
                          f"Used one {project.proposal.title} to restore 25 health on {entity.name}.")
        person.activity = f"Occupational work: {project.proposal.title}"


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    research = _state(world)
    base, state = village.context(world)
    if action == "village_research_pause":
        project = next((p for p in research.projects.values() if (chunk := _active(p)) and chunk.actor == world.actor_id), None)
        if project is None:
            return "REJECTED", "NO_RESEARCH_COMMITMENT", "There is no active research or discovered batch to pause.", None
        _pause(world, project, credit=True)
        return "APPLIED", "RESEARCH_PAUSED", "Earned study retained; incomplete production/use is discarded without a refund.", None
    if not research.config.enabled:
        return "REJECTED", "RESEARCH_DISABLED", "Occupational research is disabled.", None
    if action == "village_research_propose":
        return _proposal(world, args)
    key = args["project"]
    project = research.projects.get(key)
    if project is None:
        return "REJECTED", "UNKNOWN_RESEARCH", "Choose a real known research project.", None
    if key not in research.known.get(world.actor_id, []):
        return "REJECTED", "DISCOVERY_NOT_KNOWN", "Learn the verified discovery in person before using its recipe.", None
    if action == "village_research_share":
        target = args["target"]
        if project.discovered_at is None or target == world.actor_id or target not in village.local_residents(world, world.actor.location_id):
            return "REJECTED", "DISCOVERY_SHARING_BLOCKED", "Share only a verified discovery with a different resident actually present.", None
        blocker = village_economy._speaker_blocker(world, target)
        if blocker:
            return "REJECTED", *blocker, None
        learned = research.known.setdefault(target, [])
        if key in learned:
            return "REJECTED", "DISCOVERY_ALREADY_KNOWN", "That resident already knows the recipe.", None
        learned.append(key)
        village.event(world, world.actor_id, "research_shared", target,
                      f"Shared {project.proposal.title} with {village.resident_name(world, target)} in person.")
        return "APPLIED", "DISCOVERY_SHARED", "The present resident learned the verified recipe; making it still requires the skill, site and inputs.", None
    operation = "research" if action == "village_research" else "produce" if action == "village_research_make" else "use"
    if _active(project):
        return "REJECTED", "RESEARCH_COMMITTED", "This recipe already has an active experiment or batch; wait for it to finish.", None
    if operation == "research" and world.actor_id != project.owner:
        return "REJECTED", "RESEARCH_OWNER_REQUIRED", "Only the project owner conducts its experiments.", None
    if operation == "produce" and project.proposal.skill not in base.residents[world.actor_id].skills:
        return "REJECTED", "RESEARCH_SKILL_REQUIRED", "Knowing a recipe does not grant its actual occupational skill.", None
    if (operation == "research") != (project.discovered_at is None):
        return "REJECTED", "DISCOVERY_REQUIRED", "Research unfinished projects; make or use only verified discoveries.", None
    if operation == "use" and project.proposal.kind == "material":
        return "REJECTED", "RECIPE_INGREDIENT", "This material is usable as a real input in subsequent researched recipes, not a magic effect.", None
    minutes = min(15, 60 - project.worked_minutes % 60) if operation == "research" else project.proposal.duration if operation == "produce" else 15
    end = state.minute + minutes * village_life.cooldown_factor(world, world.actor_id)
    equipment = project.proposal.equipment
    location = project.location
    if operation == "use" and project.proposal.kind in {"food", "crop"}:
        location = world.actor.location_id
        equipment = ["tavern-hearth"]
        if location != "tavern":
            return "REJECTED", "MEAL_SITE_REQUIRED", "Deliver discovered food to the tavern and prepare it on the actual hearth for village meals.", None
    working = project.model_copy(update={
        "owner": world.actor_id, "location": location,
        "proposal": project.proposal.model_copy(update={"equipment": equipment})})
    blocker = _guard(world, working, operation, end)
    if blocker:
        return "REJECTED", *blocker, None
    target = args.get("target")
    if operation == "use" and project.proposal.kind == "tool":
        if not isinstance(target, str):
            return "REJECTED", "INVALID_REPAIR_TARGET", "Name an actual damaged occupational object to repair.", None
        entity = world.state.entities.get(target)
        allowed = set(project.proposal.equipment)
        if world.actor_id == base.smith and state.social is not None and state.social.config is not None:
            allowed |= {activity.repair_target for activity in state.social.config.activities.values() if activity.repair_target}
        allowed.discard(base.mill_equipment)
        if (entity is None or target not in allowed or entity.hidden
                or entity.location != project.location and target not in world.actor.inventory
                or entity.health is None or entity.max_health is None or entity.health >= entity.max_health):
            return "REJECTED", "INVALID_REPAIR_TARGET", "Use a discovered repair tool on an actual damaged, repairable occupational object at the site.", None
        if project.proposal.skill not in base.residents[world.actor_id].skills:
            return "REJECTED", "RESEARCH_SKILL_REQUIRED", "Repair requires the real occupational skill as well as a known tool.", None
    if operation == "produce":
        orders = state.economy.orders.values() if state.economy else []
        orders = [order for order in orders if order.status == "accepted" and order.product == project.product
                  and order.source == project.location and order.loadable]
        lots = [lot.model_copy(deep=True) for lot in state.stock_lots.get(project.location, {}).get(project.product, [])]
        needed = 0
        for order in sorted(orders, key=lambda item: item.required_fresh_until):
            remaining = order.loadable
            for lot in lots:
                if lot.expires_at is None or lot.expires_at > max(state.minute, order.required_fresh_until):
                    used = min(remaining, lot.quantity)
                    remaining -= used
                    lot.quantity -= used
            needed += remaining
        if not needed and village_economy.usable_stock(world, project.location, project.product, state.minute) >= 1:
            return "REJECTED", "DEMAND_MET", "Keep one demonstration unit or fulfil accepted orders; do not produce unlimited surplus.", None
        shelf = shelf_life(world, project.product)
        if needed and shelf is not None and all(end + shelf <= order.required_fresh_until for order in orders):
            return "REJECTED", "TOO_EARLY_FOR_FRESHNESS", "Wait to make fresh discovered goods near their ordered use time.", None
    if operation != "research" or not project.trial_materials_consumed:
        inputs = {project.product: 1} if operation == "use" else project.proposal.inputs
        for resource, amount in inputs.items():
            village_goods.take(world, location, resource, amount)
        if operation == "research":
            project.trial_materials_consumed = True
    project.chunks.append(ResearchChunk(actor=world.actor_id, location=location, equipment=list(equipment),
                                        start=state.minute, end=end, minutes=minutes, operation=operation, target=target))
    state.residents[world.actor_id].available_at = end
    state.residents[world.actor_id].activity = f"{operation.capitalize()}: {project.proposal.title}"
    village.event(world, world.actor_id, action, key, f"{operation.capitalize()} until {village.clock_time(end)}.")
    return "APPLIED", "RESEARCH_STARTED" if operation == "research" else "DISCOVERY_WORK_STARTED", "Real timed work started; outputs appear only after completion.", None


def action_blocker(world: "World", action: str, actor: str) -> tuple[str, str] | None:
    base, state = village.context(world)
    until = blocked_until(world, actor)
    if until and action not in FREE_ACTIONS:
        return "RESEARCH_COMMITTED", f"Work reserved until {village.clock_time(until)}; explicitly pause to interrupt."
    job = base.residents[actor].job
    if action in village_life.PRODUCTION and job and job.equipment and equipment_reserved(world, {job.equipment}):
        return "EQUIPMENT_RESEARCH", "The actual production equipment is reserved by occupational research."
    if action in {"village_wait_event", "village_leisure", "village_social_invite", "village_quest"} and _state(world).config.enabled:
        unfinished = False
        for project in _state(world).projects.values():
            if project.owner == actor and project.discovered_at is None:
                unfinished = True
                if world.actor.location_id == project.location:
                    if _guard(world, project, "research", state.minute + 15 * village_life.cooldown_factor(world, actor)) is None:
                        return "RESEARCH_AVAILABLE", "Use genuinely free workday time for your occupational research before leisure or a long wait."
        resident = base.residents[actor]
        skills = [skill for skill in resident.skills if skill in KINDS]
        if not unfinished and skills and world.actor.location_id == resident.work:
            candidate = ResearchProject(
                owner=actor, location=resident.work, product="unproposed",
                required_minutes=240, required_successes=3,
                proposal=ResearchProposal(title="Occupational research", description="A prospective occupational experiment.",
                    skill=skills[0], kind="material", inputs={next(iter(base.prices)): 1},
                    equipment=[resident.job.equipment] if resident.job and resident.job.equipment else [], duration=15))
            blocker = _guard(world, candidate, "research", state.minute + 5)
            if blocker is None or blocker[0] == "RESEARCH_SUPPLIES":
                return "RESEARCH_PROPOSAL_DUE", "Propose a new item related to your occupation instead of spending genuinely free workday time idle."
    return None


def requests(world: "World", actor: str) -> list[tuple[str, int, dict[str, int]]]:
    _, village_state = village.context(world)
    state = village_state.research
    if not state or not state.config.enabled:
        return []
    now = village_state.minute
    result = []
    for key, project in state.projects.items():
        if project.owner != actor:
            continue
        if project.discovered_at is None and not project.trial_materials_consumed:
            result.append((f"research:{key}:{project.attempts}", now + 120, dict(project.proposal.inputs)))
        elif project.discovered_at is not None and village_state.economy:
            orders = [order for order in village_state.economy.orders.values()
                      if order.status == "accepted" and order.supplier == actor and order.product == project.product]
            needed = max(0, sum(order.loadable for order in orders) -
                         village_economy.usable_stock(world, project.location, project.product, now))
            if needed:
                result.append((f"discovery:{key}", max(now, min(order.deadline for order in orders) - project.proposal.duration * needed),
                               {resource: amount * needed for resource, amount in project.proposal.inputs.items()}))
    return result


def _view(world: "World", key: str, project: ResearchProject, local: bool) -> dict[str, Any]:
    blocker = None
    if not local or world.actor.location_id == project.location:
        reason = _tools(world, project) or _supplies(world, project, "research" if project.discovered_at is None else "produce")
        blocker = reason[1] if reason else None
    status = "discovered" if project.discovered_at is not None else "in_progress" if _active(project) else "blocked" if blocker else "researching"
    return {"id": key, "title": project.proposal.title, "description": project.proposal.description,
            "owner": project.owner, "location": project.location, "product": project.product,
            "kind": project.proposal.kind, "inputs": project.proposal.inputs, "equipment": project.proposal.equipment,
            "duration": project.proposal.duration, "worked_minutes": project.worked_minutes,
            "required_minutes": project.required_minutes, "attempts": project.attempts, "successes": project.successes,
            "required_successes": project.required_successes, "discovered_at": project.discovered_at,
            "status": status, "blocker": blocker}


def observation(world: "World") -> dict[str, Any]:
    research = _state(world)
    return {"research": {"enabled": research.config.enabled,
                        "projects": [_view(world, key, project, True) for key, project in research.projects.items()
                                     if key in research.known.get(world.actor_id, [])],
                        "policy": "Use genuinely free workday time for occupational research. Propose a new item using your actual skill, "
                        "real inputs and equipment at your workplace. Study in 15-minute chunks; each 60-minute trial consumes inputs once "
                        "and can fail. Progress and failed trials persist across days and saves. Discovery needs both study and successful trials. "
                        "Validated discoveries unlock real production, not free stock. Food/crops prepare into two meals; tools repair actual "
                        "occupational objects by 25 health per unit; materials can enter later recipes. Share verified recipes face-to-face. "
                        "Meals, sleep, accepted orders, animal care and maintenance take priority. Check supplied proposal schema for supported fields."}}


def record_action(world: "World", action: str, args: dict[str, Any]) -> None:
    if action != "read":
        return
    key = args.get("target")
    research = _state(world)
    if key in research.projects and key in world.actor.read_entities:
        project = research.projects[key]
        known = research.known.setdefault(world.actor_id, [])
        if project.discovered_at is not None and key not in known:
            known.append(key)
            village.event(world, world.actor_id, "research_learned", key,
                          f"Learned {project.proposal.title} by reading its actual verified recipe at the site.")


def actions(world: "World") -> list[dict[str, Any]]:
    research = _state(world)
    result = []

    def add(verb: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> None:
        result.append({"id": verb, "description": description, "targets": [],
                       "arguments_schema": {"type": "object", "properties": properties,
                                            "required": list(properties) if required is None else required,
                                            "additionalProperties": False},
                       "availability": "available", "reason": None})

    if blocked_until(world, world.actor_id):
        add("village_research_pause", "Pause study, production or use; retain only earned study, never refund used materials.", {})
        return result
    if not research.config.enabled:
        return result
    own = {key: project for key, project in research.projects.items() if project.owner == world.actor_id}
    skills = [skill for skill in village.context(world)[0].residents[world.actor_id].skills if skill in KINDS]
    if skills and not any(project.discovered_at is None for project in own.values()):
        schema = ResearchProposal.model_json_schema()
        properties = schema["properties"]
        properties["skill"] = {"type": "string", "enum": skills}
        properties["kind"] = {"type": "string", "enum": sorted({kind for skill in skills for kind in KINDS[skill]})}
        add("village_research_propose", "During free workday time, propose a NEW occupational discovery with real inputs/equipment. "
            "Food cannot contain manure; crop growing needs >=60 minutes. Actual job equipment is required when configured. "
            "Examples: baker's herb loaf, farmer's edible grain variety, carpenter's repair kit. No imaginary abilities or free materials.",
            properties, schema["required"])
    unfinished = [key for key, project in own.items() if project.discovered_at is None]
    if unfinished:
        add("village_research", "Study for 15 productive minutes toward a fallible 60-minute trial; real inputs consumed once per trial.",
            {"project": {"type": "string", "enum": unfinished}})
    complete = [key for key in research.known.get(world.actor_id, []) if research.projects[key].discovered_at is not None]
    if complete:
        add("village_research_make", "Make one discovered item using the verified inputs, actual equipment and full recipe time.",
            {"project": {"type": "string", "enum": complete}})
        usable = [key for key in complete if research.projects[key].proposal.kind != "material"]
        if usable:
            targets = [key for key in world.visible_ids() if key in world.state.entities
                       and world.state.entities[key].health is not None and world.state.entities[key].max_health is not None]
            add("village_research_use", "Consume one discovered food/crop into two actual meals, or use one repair tool on a damaged occupational object.",
                {"project": {"type": "string", "enum": usable}, "target": {"type": "string", "enum": targets}},
                ["project"])
    shareable = [key for key in research.known.get(world.actor_id, []) if research.projects[key].discovered_at is not None]
    nearby = [rid for rid in village.local_residents(world, world.actor.location_id) if rid != world.actor_id]
    if shareable and nearby:
        add("village_research_share", "Teach a present awake resident a verified recipe in person; this does not create products.",
            {"project": {"type": "string", "enum": shareable}, "target": {"type": "string", "enum": nearby}})
    return result


def monitor(world: "World") -> dict[str, Any]:
    research = _state(world)
    return {"research": [_view(world, key, project, False) for key, project in research.projects.items()],
            "activities": [{"actor": chunk.actor, "label": f"{chunk.operation.capitalize()}: {project.proposal.title}",
                            "location": chunk.location, "start": chunk.start, "end": chunk.end, "status": chunk.status}
                           for project in research.projects.values() for chunk in project.chunks]}

from typing import TYPE_CHECKING, Any

from npc_sim import village, village_goods, village_life
from npc_sim.models import (
    Scenario, VillageMaintenanceChunk, VillageMaintenanceConfig,
    VillageMaintenanceJob, VillageMaintenanceState,
)

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"village_maintain", "village_maintenance_pause"}
FREE_ACTIONS = {"look", "read", "say", "village_observe", "village_wait", "village_maintenance_pause"}
OPTIONAL_ACTIONS = {"village_leisure", "village_band_invite", "village_band_attend",
                    "village_social_invite", "village_quest", "village_wait_event",
                    "village_research", "village_research_propose"}


def example_config(scenario: Scenario) -> dict[str, Any]:
    base = scenario.village
    if base is None:
        raise ValueError("Maintenance requires a village")
    smith, carpenter = base.residents[base.smith], base.residents[base.carpenter]
    return {"tasks": {
        "well-fittings": {"title": "Inspect and maintain the well fittings",
                          "assignee": base.smith, "location": base.well_site},
        "forge-tools": {"title": "Clean and service the forge tools",
                        "assignee": base.smith, "location": smith.work,
                        "equipment": [smith.job.equipment] if smith.job and smith.job.equipment else []},
        "mill-woodwork": {"title": "Inspect and maintain the mill's wooden supports",
                          "assignee": base.carpenter, "location": base.mill,
                          "equipment": [base.mill_equipment]},
        "workshop-tools": {"title": "Clean and service the carpentry workshop",
                           "assignee": base.carpenter, "location": carpenter.work,
                           "equipment": [carpenter.job.equipment]
                           if carpenter.job and carpenter.job.equipment else []},
    }}


def _validate(scenario: Scenario, config: VillageMaintenanceConfig) -> None:
    base = scenario.village
    if base is None or not (base.ai_controlled and base.daily_life and base.time_based_turns):
        raise ValueError("Maintenance requires AI-controlled timed daily life")
    for key, task in config.tasks.items():
        if not key.strip() or not task.title.strip() or task.assignee not in base.residents:
            raise ValueError("Maintenance requires a named task and an actual resident assignee")
        site = scenario.entities.get(task.location)
        if site is None or site.kind != "location":
            raise ValueError(f"Unknown maintenance site: {task.location}")
        for identifier in task.equipment:
            entity = scenario.entities.get(identifier)
            if entity is None or entity.kind in {"location", "door"}:
                raise ValueError(f"Unknown maintenance equipment: {identifier}")
        for resource in task.supplies:
            if not any(resource in stock for stock in base.stocks.values()):
                raise ValueError(f"Maintenance supply is not configured real stock: {resource}")


def validate_config(scenario: Scenario) -> None:
    if scenario.village and scenario.village.maintenance is not None:
        _validate(scenario, scenario.village.maintenance)


def initial_state(scenario: Scenario) -> VillageMaintenanceState | None:
    if scenario.village is None or scenario.village.maintenance is None:
        return None
    config = scenario.village.maintenance.model_copy(deep=True)
    day = scenario.village.start_minute // 1440
    return VillageMaintenanceState(config=config, last_generated_day=day, jobs={
        f"maintenance:{day}:{key}": VillageMaintenanceJob(
            template=key, definition=task.model_copy(deep=True), day=day,
            release_at=day * 1440 + task.release_minute, due_at=day * 1440 + task.due_minute)
        for key, task in config.tasks.items() if config.enabled
    })


def _state(world: "World") -> VillageMaintenanceState:
    _, state = village.context(world)
    if state.maintenance is None:
        raise ValueError("Town maintenance is not enabled")
    return state.maintenance


def _active(job: VillageMaintenanceJob) -> VillageMaintenanceChunk | None:
    return job.chunks[-1] if job.chunks and job.chunks[-1].status == "active" else None


def _sync(world: "World") -> None:
    _, state = village.context(world)
    upkeep = _state(world)
    day = state.minute // 1440
    if not upkeep.config.enabled or upkeep.last_generated_day == day:
        return
    announce_carry = 0 <= upkeep.last_generated_day < day
    for key, definition in upkeep.config.tasks.items():
        previous = [job for job in upkeep.jobs.values() if job.template == key]
        if any(job.day == day for job in previous):
            continue
        unfinished = next((job for job in previous if job.completed_at is None), None)
        if unfinished is not None:
            if announce_carry:
                village.event(world, "dungeon-master", "maintenance_carried_over", key,
                              f"{definition.title} remains required; unfinished work carries forward.")
            continue
        identifier = f"maintenance:{day}:{key}"
        upkeep.jobs[identifier] = VillageMaintenanceJob(
            template=key, definition=definition.model_copy(deep=True), day=day,
            release_at=day * 1440 + definition.release_minute,
            due_at=day * 1440 + definition.due_minute,
        )
        village.event(world, "dungeon-master", "maintenance_due", identifier,
                      f"{village.resident_name(world, definition.assignee)} is assigned "
                      f"{definition.title}; {definition.duration} work minutes, due "
                      f"{village.clock_time(day * 1440 + definition.due_minute)}.",
                      definition.location)
    upkeep.last_generated_day = day


def configure(world: "World", patch: dict[str, Any]) -> None:
    config = VillageMaintenanceConfig.model_validate(patch)
    _validate(world.scenario, config)
    _, state = village.context(world)
    if state.maintenance is None:
        state.maintenance = VillageMaintenanceState(config=config)
    else:
        if any(_active(job) for job in state.maintenance.jobs.values()):
            raise ValueError("Pause committed maintenance before changing its configuration")
        state.maintenance.config = config
        state.maintenance.last_generated_day = -1
    _sync(world)


def _equipment_present(world: "World", job: VillageMaintenanceJob) -> bool:
    actor = world.state.actors.get(job.definition.assignee)
    return all(identifier in world.state.entities
               and not world.state.entities[identifier].hidden
               and (world.state.entities[identifier].location == job.definition.location
                    or actor is not None and identifier in actor.inventory)
               for identifier in job.definition.equipment)


def _supply_blocker(world: "World", job: VillageMaintenanceJob) -> str | None:
    if job.materials_consumed:
        return None
    base, state = village.context(world)
    for resource, quantity in job.definition.supplies.items():
        stock = state.stocks.get(job.definition.location, {}).get(resource, 0)
        if base.expanded_rules:
            lots = state.stock_lots.get(job.definition.location, {}).get(resource, [])
            if sum(lot.quantity for lot in lots) != stock:
                raise ValueError(f"Maintenance freshness ledger disagrees with stock: {resource}")
            stock = sum(lot.quantity for lot in lots if lot.expires_at is None or lot.expires_at > state.minute)
        if stock < quantity:
            return f"Needs {quantity} usable {resource} at the work site; arrange real supplies."
    return None


def _equipment_blocker(world: "World", job: VillageMaintenanceJob) -> str | None:
    base, state = village.context(world)
    tools = set(job.definition.equipment)
    from npc_sim import village_livestock
    if village_livestock.equipment_reserved(world, tools):
        return "The actual equipment is reserved by livestock work."
    from npc_sim import village_research
    if village_research.equipment_reserved(world, tools):
        return "The actual equipment is reserved by occupational research."
    from npc_sim import village_mining
    if village_mining.equipment_reserved(world, tools):
        return "The actual equipment is reserved by mining/smelting."
    for other in _state(world).jobs.values():
        if other is not job and _active(other) and tools.intersection(other.definition.equipment):
            return "The actual equipment is reserved by another maintenance worker."
    for actor, person in state.residents.items():
        if actor == job.definition.assignee:
            continue
        if person.life and person.life.work and person.life.work.equipment in tools:
            return "The actual equipment is processing a committed batch."
        work = base.residents[actor].job
        if (work and work.equipment in tools and person.available_at > state.minute
                and person.current_action in village_life.PRODUCTION):
            return "The actual equipment is being used for production."
    if state.incidents is not None:
        from npc_sim import village_events
        if any(village_events.delay_until(world, job.definition.assignee,
                                          job.definition.location, equipment) > state.minute
               for equipment in job.definition.equipment or [None]):
            return "A real village incident delays maintenance at this site."
    return None


def blocked_until(world: "World", actor: str) -> int | None:
    return next((chunk.end for job in _state(world).jobs.values()
                 if job.definition.assignee == actor and (chunk := _active(job)) is not None), None)


def action_blocker(world: "World", action: str, actor: str) -> tuple[str, str] | None:
    upkeep = _state(world)
    base, _ = village.context(world)
    work = base.residents[actor].job
    if action in village_life.PRODUCTION and work and work.equipment:
        if any(_active(job) and work.equipment in job.definition.equipment for job in upkeep.jobs.values()):
            return "EQUIPMENT_MAINTENANCE", "The actual production equipment is reserved for maintenance."
    until = blocked_until(world, actor)
    if until is not None and action not in FREE_ACTIONS:
        return "MAINTENANCE_COMMITTED", f"Working until {village.clock_time(until)}; pause explicitly before leaving."
    if upkeep.config.enabled and action in OPTIONAL_ACTIONS:
        _, state = village.context(world)
        for job in upkeep.jobs.values():
            if (job.definition.assignee == actor and job.completed_at is None
                    and job.release_at <= state.minute and _equipment_present(world, job)
                    and _supply_blocker(world, job) is None):
                return "MAINTENANCE_DUE", "Complete your assigned town maintenance before optional activities or long idle waits."
    return None


def _interrupt(world: "World", identifier: str, job: VillageMaintenanceJob, credit: bool) -> None:
    _, state = village.context(world)
    chunk = _active(job)
    if chunk is None:
        raise ValueError("There is no active maintenance chunk to interrupt")
    factor = (chunk.end - chunk.start) // chunk.minutes
    earned = min(chunk.minutes, (state.minute - chunk.start) // (5 * factor) * 5) if credit else 0
    job.worked_minutes += earned
    chunk.end, chunk.minutes, chunk.status = state.minute, earned, "interrupted"
    person = state.residents[job.definition.assignee]
    person.available_at = min(person.available_at, state.minute)
    person.activity = f"Paused maintenance: {job.definition.title}"
    village.event(world, job.definition.assignee, "maintenance_paused", identifier,
                  f"Maintenance paused; {job.worked_minutes}/{job.definition.duration} work minutes retained.")


def advance(world: "World") -> None:
    _, state = village.context(world)
    upkeep = _state(world)
    for identifier, job in upkeep.jobs.items():
        chunk = _active(job)
        if chunk is None:
            continue
        person = state.residents[job.definition.assignee]
        if (person.life is not None and person.life.sleeping
                or village.resident_location(world, job.definition.assignee) != job.definition.location
                or not _equipment_present(world, job)):
            _interrupt(world, identifier, job, credit=False)
        elif state.minute >= chunk.end:
            job.worked_minutes += chunk.minutes
            chunk.status = "completed"
            person = state.residents[job.definition.assignee]
            person.activity = f"Maintenance: {job.definition.title} ({job.worked_minutes}/{job.definition.duration} min)"
            if job.worked_minutes == job.definition.duration:
                job.completed_at = state.minute
                upkeep.last_maintained[job.template] = state.minute
                state.role_contributions[job.definition.assignee] = state.role_contributions.get(
                    job.definition.assignee, 0) + 1
                village.event(world, job.definition.assignee, "maintenance_completed", identifier,
                              f"Completed {job.definition.title} with real work at the site. "
                              "Routine upkeep does not replace structural repairs.")
    _sync(world)


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    upkeep = _state(world)
    _, state = village.context(world)
    if action == "village_maintenance_pause":
        for identifier, job in upkeep.jobs.items():
            if job.definition.assignee == world.actor_id and _active(job):
                _interrupt(world, identifier, job, credit=True)
                return "APPLIED", "MAINTENANCE_PAUSED", "Maintenance paused; earned progress and consumed materials are retained.", None
        return "REJECTED", "NO_MAINTENANCE_COMMITMENT", "No maintenance work is currently committed.", None
    job = upkeep.jobs.get(args["job"])
    if job is None or job.definition.assignee != world.actor_id:
        return "REJECTED", "NOT_ASSIGNED", "Choose your own assigned maintenance job.", None
    if job.completed_at is not None:
        return "REJECTED", "MAINTENANCE_ALREADY_DONE", "This maintenance job is already complete.", None
    if not upkeep.config.enabled or state.minute < job.release_at:
        return "REJECTED", "MAINTENANCE_NOT_DUE", "Maintenance is disabled or not yet released.", None
    if blocked_until(world, world.actor_id) is not None:
        return "REJECTED", "MAINTENANCE_COMMITTED", "Finish or explicitly pause the current maintenance chunk.", None
    definition = job.definition
    if "manipulate" not in world.actor.body.capabilities:
        return "REJECTED", "MISSING_CAPABILITY", "Routine servicing requires actual manipulation capability.", None
    if any(not (set(world.state.entities[identifier].use_grips) & set(world.actor.body.grips))
           for identifier in definition.equipment if identifier in world.state.entities):
        return "REJECTED", "INCOMPATIBLE_GRIP", "Use real maintenance tools with compatible grips.", None
    if world.actor.location_id != definition.location:
        return "REJECTED", "NOT_PRESENT", "Walk to the actual maintenance site before working.", None
    if not _equipment_present(world, job):
        return "REJECTED", "MISSING_MAINTENANCE_TOOLS", "Bring the actual configured equipment to the site.", None
    blocker = _equipment_blocker(world, job)
    if blocker:
        return "REJECTED", "MAINTENANCE_EQUIPMENT_BUSY", blocker, None
    blocker = _supply_blocker(world, job)
    if blocker:
        return "REJECTED", "MISSING_MAINTENANCE_SUPPLIES", blocker, None
    person = state.residents[world.actor_id]
    if (person.life and person.life.work or person.timber_route or person.delivery is not None
            or state.wagon and state.wagon.driver == world.actor_id):
        return "REJECTED", "WORK_COMMITTED", "Finish or stop existing work and routes first.", None
    minutes = min(15, definition.duration - job.worked_minutes)
    end = state.minute + minutes * village_life.cooldown_factor(world, world.actor_id)
    base, _ = village.context(world)
    resident = base.residents[world.actor_id]
    path = village.route(world, definition.location, resident.home)
    if path is None or end + 5 * len(path) + 5 > state.minute // 1440 * 1440 + resident.bed_minute:
        return "REJECTED", "BEDTIME_CONFLICT", "Leave enough real walking and sleep-action time before bedtime.", None
    due = [state.minute // 1440 * 1440 + 1140]
    policy = state.day_policy or base
    if policy.breakfast:
        due.append(state.minute // 1440 * 1440 + resident.wake_minute + policy.breakfast_delay)
    meal_path = village.route(world, definition.location, "tavern")
    if any(state.minute < time < end + (5 * len(meal_path) if meal_path is not None else 1440) for time in due):
        return "REJECTED", "MEAL_CONFLICT", "Maintenance must leave time to walk to the next meal.", None
    if not job.materials_consumed:
        for resource, quantity in definition.supplies.items():
            village_goods.take(world, definition.location, resource, quantity)
        job.materials_consumed = True
    job.chunks.append(VillageMaintenanceChunk(start=state.minute, end=end, minutes=minutes))
    person.available_at = end
    person.activity = f"Maintenance: {definition.title}"
    village.event(world, world.actor_id, "village_maintain", args["job"],
                  f"Working on {definition.title} until {village.clock_time(end)}.")
    return "APPLIED", "MAINTENANCE_STARTED", person.activity, None


def actions(world: "World") -> list[dict[str, Any]]:
    upkeep = _state(world)
    if blocked_until(world, world.actor_id) is not None:
        return [{"id": "village_maintenance_pause", "description": "Pause town maintenance, retaining only earned progress.",
                 "targets": [], "arguments_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                 "availability": "available", "reason": None}]
    jobs = [key for key, job in upkeep.jobs.items()
            if job.definition.assignee == world.actor_id and job.completed_at is None]
    if not upkeep.config.enabled or not jobs:
        return []
    return [{"id": "village_maintain", "description": "Do 15 real work minutes at your maintenance site; supplies are consumed once.",
             "targets": [], "arguments_schema": {"type": "object", "properties": {
                 "job": {"type": "string", "enum": jobs}}, "required": ["job"], "additionalProperties": False},
             "availability": "available", "reason": None}]


def _view(world: "World", key: str, job: VillageMaintenanceJob, local: bool = False) -> dict[str, Any]:
    _, state = village.context(world)
    blocker = None
    status = "pending"
    if job.completed_at is not None:
        status = "completed"
    elif _active(job):
        status = "in_progress"
    elif state.minute < job.release_at:
        status = "upcoming"
    else:
        if not local or world.actor.location_id == job.definition.location:
            blocker = _supply_blocker(world, job) or _equipment_blocker(world, job)
            if not _equipment_present(world, job):
                blocker = "Actual maintenance equipment is missing from the site."
        status = "blocked" if blocker else "overdue" if state.minute >= job.due_at else "pending"
    return {"id": key, "title": job.definition.title, "assignee": job.definition.assignee,
            "location": job.definition.location, "day": job.day + 1, "release_at": job.release_at,
            "due_at": job.due_at, "worked_minutes": job.worked_minutes,
            "required_minutes": job.definition.duration, "supplies": job.definition.supplies,
            "status": status, "completed_at": job.completed_at, "blocker": blocker}


def observation(world: "World") -> dict[str, Any]:
    upkeep = _state(world)
    return {"maintenance": {
        "jobs": [_view(world, key, job, local=True) for key, job in upkeep.jobs.items()
                 if job.definition.assignee == world.actor_id
                 or job.definition.location == world.actor.location_id],
        "enabled": upkeep.config.enabled,
        "policy": "Town maintenance is required daily work, not optional leisure. "
                  "Travel to the site and do your assigned jobs before leisure. Meals, sleep and funded "
                  "commitments still take precedence. Pause explicitly to interrupt. Unfinished jobs carry "
                  "forward at midnight without duplicate backlogs; routine upkeep is not structural repair.",
    }}


def monitor(world: "World") -> dict[str, Any]:
    upkeep = _state(world)
    return {
        "maintenance": [_view(world, key, job) for key, job in upkeep.jobs.items()]
                       if upkeep.config.enabled else [],
        "activities": [{"actor": job.definition.assignee, "label": f"Maintenance: {job.definition.title}",
                        "location": job.definition.location, "start": chunk.start, "end": chunk.end,
                        "status": chunk.status}
                       for job in upkeep.jobs.values() for chunk in job.chunks],
    }

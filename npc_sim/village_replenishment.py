"""One explicit, local and time-bounded supply intention per resident."""

from typing import TYPE_CHECKING, Any

from npc_sim import village
from npc_sim.models import Scenario, ScenarioAgent, VillageReplenishmentPlan

if TYPE_CHECKING:
    from npc_sim.models import VillageConfig, VillageState, WorldState
    from npc_sim.world import World


PLAN = "village_plan_replenishment"
CANCEL = "village_cancel_replenishment"
VERBS = {PLAN, CANCEL}
MAX_MINUTES = 120
MAX_LEAD = 48 * 60
WORK = {
    "gather_herbs": ("village_gather_herbs", "herbs"),
    "harvest_garden": ("village_garden_harvest", "herbs"),
    "collect_eggs": ("village_collect_eggs", "eggs"),
    "milk_cow": ("village_milk_cow", "milk"),
    "fell_logs": ("village_fell_logs", "logs"),
    "split_firewood": ("village_split_firewood", "firewood"),
}
PRODUCTION = {"gather_herbs", "fell_logs", "split_firewood"}
WORK_ACTIONS = {action for action, _ in WORK.values()}


def _sites(state: "VillageState", config: "VillageConfig", rid: str) -> dict[str, str]:
    if not (config.ai_controlled and config.expanded_rules and rid in config.residents):
        return {}
    resident = config.residents[rid]
    result = {}
    if resident.job:
        if "herbalism" in resident.skills:
            result["gather_herbs"] = resident.job.location
        if "forestry" in resident.skills:
            result["fell_logs"] = resident.job.location
            if state.firewood and state.firewood.config.enabled and rid == state.firewood.config.supplier:
                result["split_firewood"] = resident.job.location
    if state.livestock and state.livestock.config.enabled and rid == state.livestock.config.keeper:
        result.update(collect_eggs="farm", milk_cow="farm")
    if rid == "iona" and state.social and (state.day_policy or config).purposeful_downtime:
        social_config = state.social.config or config.social
        garden = social_config.activities.get("herb-garden-care") if social_config else None
        if garden:
            result["harvest_garden"] = garden.location
    return result


def personal(world: "World") -> dict[str, Any] | None:
    _, state = village.context(world)
    plan = state.residents[world.actor_id].replenishment
    if plan and plan.expires_at > state.minute:
        return {**plan.model_dump(), "work_action": WORK[plan.task][0]}
    return None


def choices(world: "World") -> list[str]:
    config, state = village.context(world)
    if personal(world):
        return []
    return [task for task, site in _sites(state, config, world.actor_id).items()
            if site == world.actor.location_id]


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    _, state = village.context(world)
    person = state.residents[world.actor_id]
    if action == CANCEL:
        if args or not personal(world):
            return "REJECTED", "NO_REPLENISHMENT_PLAN", "You have no current replenishment plan to cancel.", None
        plan = person.replenishment
        assert plan is not None
        person.replenishment = None
        code, message = "REPLENISHMENT_CANCELLED", "Cancelled your own replenishment plan; no stock changed."
    else:
        task = args.get("task")
        if not isinstance(task, str) or set(args) - {"task", "starts_at"} or task not in choices(world):
            return ("REJECTED", "REPLENISHMENT_UNAVAILABLE",
                    "Plan one advertised batch at its actual site, or cancel your existing plan first.", None)
        start = args.get("starts_at", state.minute + 5)
        if type(start) is not int or not state.minute + 5 <= start <= state.minute + MAX_LEAD:
            return ("REJECTED", "INVALID_REPLENISHMENT_TIME",
                    "Choose an absolute game minute within the next 48 hours.", None)
        plan = VillageReplenishmentPlan(task=task, location=world.actor.location_id, created_at=state.minute,
                                        starts_at=start, expires_at=start + MAX_MINUTES)
        person.replenishment = plan
        code, message = "REPLENISHMENT_PLANNED", (
            f"Planned one local {WORK[task][1]} batch from {village.clock_time(plan.starts_at)}; "
            f"use {WORK[task][0]} to do the actual work. "
            f"This plan expires at {village.clock_time(plan.expires_at)} and supplies nothing on its own.")
    village.event(world, world.actor_id, action, plan.location, message)
    return "APPLIED", code, message, None


def record_work(world: "World", action: str) -> None:
    _, state = village.context(world)
    person = state.residents[world.actor_id]
    plan = person.replenishment
    if plan and WORK[plan.task][0] == action and world.actor.location_id == plan.location:
        person.replenishment = None


def _check(view: "World", plan: VillageReplenishmentPlan) -> tuple[int, int | None, str]:
    from npc_sim import village_ai, village_life, village_livestock, village_social

    config, state = village.context(view)
    worker = view.actor_id
    person = state.residents[worker]
    action, product = WORK[plan.task]
    if _sites(state, config, worker).get(plan.task) != plan.location:
        return 0, None, "The resident is no longer assigned to perform this work here."
    if view.actor.location_id != plan.location:
        return 0, None, "The resident must return to the actual work site."
    if (person.timber_route or person.delivery is not None or state.horse_leader == worker
            or state.wagon and state.wagon.driver == worker):
        return 0, None, "The resident must finish their current commitment first."

    now = state.minute
    start = max(now, person.available_at, person.busy_until, plan.starts_at)
    if plan.task in PRODUCTION:
        start = max(start, person.next_work)
    if plan.task == "harvest_garden":
        ready = state.social.garden_yields.get(plan.location) if state.social else None
        if ready is None:
            return 0, None, "The garden has no recorded ready or growing yield."
        start = max(start, ready)
    if start + 5 > plan.expires_at:
        return 0, None, "No eligible start within the plan's remaining time."
    if person.life and (person.life.sleeping and (person.life.sleep_until is None or person.life.sleep_until > start)
                        or person.life.work and person.life.work.finishes_at > start):
        return 0, None, "The resident must finish their current commitment first."
    state.minute = start
    if person.life:
        life = person.life
        resident = config.residents[worker]
        if life.work:
            # Keep physical output uncredited; the production guard still sees the work of other residents.
            if product in life.work.outputs:
                return 0, None, "Matching production is already committed."
            life.work = None
        if life.sleeping and life.sleep_until is not None:
            life.sleeping = False
            life.last_woke = life.sleep_until
        if start // 1440 > now // 1440:
            wake = start // 1440 * 1440 + resident.wake_minute
            if start >= wake:
                life.last_woke = wake
            # Future work is conditional on the normal daily routine; do not generate meals or care.
            life.meals = {key: meal for key, meal in life.meals.items() if meal.due // 1440 == start // 1440}
        else:
            village_life.add_meals(life, resident, start, state.day_policy or config)

    blocker = view._village_action_blocker(action, {})
    if blocker:
        return 0, None, blocker[1]
    if plan.task in PRODUCTION:
        blocker = village_ai.production_ready(view, action)
        if blocker:
            return 0, None, blocker[1]
        job = village_ai.production_job(view, action)
        assert job is not None
        quantity, finish = job.outputs[product], start + job.duration
    elif plan.task == "harvest_garden":
        blocker = village_social.harvest_blocker(view)
        if blocker:
            return 0, None, blocker[1]
        quantity, finish = 1, start + 5
    else:
        result, quantity, finish = village_livestock.preview_collection(view, action)
        if result:
            return 0, None, result[1]
    if finish > plan.expires_at:
        return 0, None, "The work would finish after the plan expires."
    return quantity, finish, "Explicit local plan; real work must still be performed."


def forecast_rows(world: "WorldState", config: "VillageConfig | None") -> list[dict[str, Any]]:
    """Project eligible intents from a saved snapshot, never advancing or modifying it."""
    from npc_sim import village_economy, village_firewood
    from npc_sim.world import World

    state = world.village
    if not state:
        return []
    rows = []
    for worker, person in sorted(state.residents.items()):
        plan = person.replenishment
        if not plan or plan.expires_at <= state.minute:
            continue
        _, product = WORK[plan.task]
        inputs: dict[str, int] = {}
        earliest_overlap = max(state.minute, plan.starts_at - MAX_MINUTES)
        latest_overlap = plan.expires_at
        if product in {"milk", "eggs"}:
            # One egg collection per day and restricted milk windows can overlap even hours apart.
            earliest_overlap = max(state.minute, plan.starts_at // 1440 * 1440)
            latest_overlap = (plan.starts_at // 1440 + 1) * 1440 - 1
        overlap = state.economy and any(
            order.supplier == worker and order.source == plan.location and order.product == product
            and order.status in village_economy.COMMITTED and order.loadable and order.deadline >= state.minute
            and order.production_plan.get("output", 0) > 0 and not order.production_plan.get("requires_care")
            and earliest_overlap <= order.production_plan.get("finish", state.minute)
            <= min(order.deadline, latest_overlap)
            for order in state.economy.orders.values())
        if overlap:
            quantity, finish, basis = 0, None, "An overlapping accepted order already has a production plan."
        elif config is None or worker not in world.actors or worker not in config.residents:
            quantity, finish, basis = 0, None, "The resident or scenario rules are unavailable in the saved state."
        else:
            # The existing physical guards run on an isolated actor view. No clock advancement,
            # saves, work actions, or price/stock mutations run during projection.
            view = object.__new__(World)
            view._owner = view
            view.state = world.model_copy(deep=True)
            view.actor_id = worker
            view.scenario = Scenario.model_construct(
                id=world.scenario_id, village=config,
                agents=[ScenarioAgent(id=rid, role=resident.occupation, start_location=resident.home)
                        for rid, resident in config.residents.items()])
            quantity, finish, basis = _check(view, plan)
            if quantity and plan.task == "split_firewood":
                job = village_firewood.production_job(view)
                assert job is not None
                inputs = dict(job.inputs)
        row = {"id": f"replenishment:{worker}:{plan.created_at}", "worker": worker,
               "location": plan.location, "product": product, "quantity": quantity, "finish": finish,
               "status": "planned" if quantity else "blocked", "basis": basis}
        if plan.task == "split_firewood":
            row.update(kind="production", inputs=inputs)
        rows.append(row)
    return rows

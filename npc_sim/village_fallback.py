import hashlib
import json
from typing import TYPE_CHECKING, Any

from npc_sim import village, village_ai, village_extensions, village_life, village_economy
from npc_sim.models import (VillageBlockedAction, VillageEvent, VillageIdleWait,
                            VillageLocationTransition, VillageRouteGoal, VillageSupplierSearch,
                            VillageSupplyNeed, VillageTravelEvent)

if TYPE_CHECKING:
    from npc_sim.world import World


WAKE_ON = ["local_stock_changed", "personally_learned_notice", "local_encounter",
           "needs_or_schedule_changed", "tool_condition_changed", "safety_review"]
REASONS = {
    "DEMAND_MET": "STOCK_POLICY_PROHIBITS_PRODUCTION",
    "RAW_MATERIAL_RESERVE": "STOCK_POLICY_PROHIBITS_PRODUCTION",
    "STOCK_SHORTAGE": "NO_INPUTS", "NO_FOOD": "NO_INPUTS",
    "MEAL_MISSED": "NO_INPUTS", "WORK_COOLDOWN": "COOLDOWN_ACTIVE",
    "MEAL_DUE": "HUNGRY", "REST_DUE": "NEEDS_REST",
    "WRONG_WORKPLACE": "LOCATION_MISMATCH", "OUT_OF_REACH": "PERSON_UNAVAILABLE",
    "MEAL_UNAFFORDABLE": "PAYMENT_INSUFFICIENT",
}


def enabled(world: "World") -> bool:
    config = world.scenario.village
    return bool(config and config.ai_controlled and config.time_based_turns and config.daily_life
                and village_extensions.policy(world).anti_idle)


def purposeful(world: "World") -> bool:
    return enabled(world) and village_extensions.policy(world).purposeful_downtime


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]


def _key(action: str, args: dict[str, Any]) -> str:
    return f"{action}:{_digest(args)}"


def _context_values(world: "World") -> dict[str, Any]:
    config, state = village.context(world)
    person = state.residents[world.actor_id]
    location = world.actor.location_id
    visible = set(world.visible_ids()) | set(world.actor.inventory) | {location}
    life = person.life
    economy = village_economy.config(world)
    known = state.communication.knowledge.get(world.actor_id, {}) if state.communication else {}
    return {
        "location": location, "inventory": world.actor.inventory, "inspected": world.actor.inspected,
        "body": world.actor.body.model_dump(),
        "stock": state.stocks.get(location, {}), "cargo": person.cargo,
        "coins": state.coins.get(world.actor_id),
        "meal_funded": village_economy.meal_affordable(world, world.actor_id) if location == "tavern" else None,
        "entities": {key: entity.model_dump() for key, entity in world.state.entities.items()
                     if key in visible},
        "people": sorted(village.local_residents(world, location)),
        "heard_messages": world.actor.messages,
        "beliefs": {key: belief.model_dump(exclude={"learned_at"}) for key, belief in person.beliefs.items()},
        "known_notices": {key: memory.snapshot.model_dump() for key, memory in known.items()},
        "communication_blocker": state.communication.recent_blockers.get(world.actor_id) if state.communication else None,
        "supply_needs": {key: need.model_dump() for key, need in person.fallback.supply_needs.items()},
        "route_goal": person.fallback.route_goal.model_dump(exclude={"attempted_edges", "last_progress_at", "retry_after"})
        if person.fallback.route_goal else None,
        "supplier_searches": repeated_supplier_searches(world),
        "next_work_ready": state.minute >= person.next_work,
        "day": state.minute // 1440,
        "work_window": (config.residents[world.actor_id].wake_minute <= state.minute % 1440
                        <= village_life.production_closing_minute(world, world.actor_id)
                        - (5 if config.residents[world.actor_id].work == "tavern" else 0)),
        "life": life.model_dump() if life else None,
        "rest_due": village_life.rest_due(config.residents[world.actor_id], life, state.minute) if life else False,
        "departure_due": any(time <= state.minute for time in _obligation_limits(world)),
        "stock_policy": economy.model_dump(include={
            "reserve_targets", "production_targets", "require_orders", "stock_buffer"}) if economy else None,
        "firewood_policy": state.firewood.config.model_dump() if state.firewood else None,
        **({"supplier_alternatives": economy.supplier_alternatives} if economy and economy.supplier_alternatives else {}),
        "policy": village_extensions.policy(world).model_dump(include={
            "anti_idle", "purposeful_downtime", "downtime_review_minutes", "board_check_minutes",
            "idle_review_minutes", "breakfast", "breakfast_delay", "meal_window"}),
    }


def context_revision(world: "World") -> str:
    return _digest(_context_values(world))


def _meal_funded(world: "World") -> bool:
    config, state = village.context(world)
    return (village_economy.meal_affordable(world, world.actor_id) if state.economy is not None
            else not config.expanded_rules or state.coins[world.actor_id] >= config.meal_price)


def _retry_revision(world: "World", action: str) -> str:
    if action == "village_eat":
        _, state = village.context(world)
        life = state.residents[world.actor_id].life
        meal = village_life.available_meal(world, world.actor_id, include_missed=True) if life else None
        local_food = state.stocks.get("tavern", {}) if world.actor.location_id == "tavern" else {}
        return _digest({
            "location": world.actor.location_id, "meal": meal.model_dump() if meal else None,
            "food": {key: local_food.get(key, 0) for key in ("meals", "bread", "fish", "tea")},
            "funded": _meal_funded(world), "inn_open": village_life.inn_open(world),
        })
    values = _context_values(world)
    if action in village_life.PRODUCTION:
        for key in ("people", "heard_messages", "beliefs"):
            values.pop(key)
    return _digest(values)


def _emit(world: "World", kind: str, task_id: str, reason: str, **details: Any) -> None:
    _, state = village.context(world)
    payload = {"actor_id": world.actor_id, "world_time": state.minute,
               "task_id": task_id, "location": world.actor.location_id,
               "known_source": "personal observation and remembered notices",
               "context_revision": context_revision(world), **details}
    state.events.append(VillageEvent(
        minute=state.minute, actor=world.actor_id, action=kind, target=task_id,
        result=reason, witnesses=[world.actor_id], details=payload))
    state.residents[world.actor_id].memories.append(len(state.events) - 1)


def update_supply_needs(world: "World") -> None:
    _, state = village.context(world)
    if state.communication is None:
        return
    rid = world.actor_id
    fallback = state.residents[rid].fallback
    for notice_id, memory in state.communication.knowledge.get(rid, {}).items():
        notice = memory.snapshot
        terms = notice.terms
        if notice.author != rid or terms.type != "supply_request" or not terms.recipients:
            continue
        key = f"{rid}:{terms.recipients[0]}:{terms.subject}:{terms.destination}"
        status = ("delivered" if notice.status in {"delivered", "completed"} else
                  "rejected" if notice.status == "rejected" else
                  "expired" if notice.status == "expired" else
                  "cancelled" if notice.status in {"cancelled", "superseded"} else
                  "accepted" if notice.status in {"accepted", "in_progress"} else
                  "awaiting-response")
        existing = fallback.supply_needs.get(key)
        if (existing and existing.notice_id == notice_id and existing.status == status
                and existing.quantity_needed == terms.quantity):
            continue
        fallback.supplier_searches.pop(key, None)
        fallback.supply_needs[key] = VillageSupplyNeed(
            owner_id=rid, good=terms.subject, quantity_needed=terms.quantity,
            purpose="supply", destination_id=terms.destination, supplier_id=terms.recipients[0],
            status=status, notice_id=notice_id, last_material_change=state.minute,
            next_check_after=state.minute + village_extensions.policy(world).idle_review_minutes,
            blocker=None)


def current_tasks(world: "World") -> dict[str, list[dict[str, Any]]]:
    """Summarize only the actor's commitments and observed daily progress."""
    _, state = village.context(world)
    person = state.residents[world.actor_id]
    day = state.minute // 1440
    commitments: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    if person.life:
        if person.life.work:
            commitments.append({"task": "work", "due": person.life.work.finishes_at})
        for key, meal in person.life.meals.items():
            if key.startswith(f"{day}:"):
                target = completed if meal.status == "eaten" else commitments if meal.status == "pending" else blockers
                target.append({"task": key, "status": meal.status, "due": meal.due})
    for need in person.fallback.supply_needs.values():
        if need.status in {"delivered", "rejected", "expired", "cancelled"} and need.last_material_change // 1440 < day:
            continue
        item = {"task": need.notice_id or f"supply:{need.good}", "status": need.status,
                "good": need.good, "supplier": need.supplier_id}
        target = (completed if need.status == "delivered" else blockers if need.status in
                  {"rejected", "expired", "cancelled", "blocked"} else commitments)
        target.append(item)
    if state.economy:
        from npc_sim import village_communication
        for order in state.economy.orders.values():
            if world.actor_id not in {order.buyer, order.supplier}:
                continue
            status = order.status
            if village_communication.enabled(world) and order.coordination_notice_id:
                memory = state.communication.knowledge.get(world.actor_id, {}).get(order.coordination_notice_id)
                if memory is None or not memory.snapshot.order_snapshot:
                    continue
                status = memory.snapshot.order_snapshot["status"]
            if status in {"fulfilled", "failed", "cancelled", "rejected", "expired", "declined"} and order.deadline // 1440 < day:
                continue
            item = {"task": order.id, "status": status, "product": order.product,
                    "deadline": order.deadline}
            target = (completed if status == "fulfilled" else blockers if status in
                      {"failed", "cancelled", "rejected", "expired", "declined"} else commitments)
            target.append(item)
    if state.livestock and world.actor_id == state.livestock.config.keeper:
        for name in ("cow", "hens"):
            care = getattr(state.livestock, name)
            (completed if care.day == day and care.cleaned else commitments).append(
                {"task": f"care:{name}", "day": day})
    for blocked in person.fallback.blocked.values():
        blockers.append({"task": blocked.action_id, "reason": blocked.reason_code,
                         "blocked_at": blocked.blocked_at})
    return {"commitments": commitments, "completed": completed, "blockers": blockers}


def _supplier_evidence(world: "World", need: VillageSupplyNeed) -> str:
    _, state = village.context(world)
    known = state.communication.knowledge.get(world.actor_id, {}) if state.communication else {}
    return _digest({
        "need": need.model_dump(include={"notice_id", "status", "quantity_needed", "last_material_change"}),
        "notices": {nid: memory.snapshot.model_dump() for nid, memory in known.items()
                    if nid == need.notice_id or (memory.snapshot.author == need.supplier_id
                                                 and memory.snapshot.terms.subject == need.good)},
    })


def _route_evidence(world: "World") -> str:
    _, state = village.context(world)
    person = state.residents[world.actor_id]
    known = state.communication.knowledge.get(world.actor_id, {}) if state.communication else {}
    return _digest({
        "inventory": world.actor.inventory, "cargo": person.cargo,
        "messages": world.actor.messages,
        "notices": {key: memory.snapshot.model_dump() for key, memory in known.items()},
        "needs": {key: need.model_dump(include={"status", "quantity_needed", "last_material_change"})
                  for key, need in person.fallback.supply_needs.items()},
    })


def plan_optional_route(world: "World", goal_id: str, target: str, reason: str) -> None:
    """Record an explicitly optional meeting or information-search destination."""
    _, state = village.context(world)
    fallback = state.residents[world.actor_id].fallback
    if fallback.route_goal and fallback.route_goal.goal_id == goal_id:
        return
    fallback.route_goal = VillageRouteGoal(
        goal_id=goal_id, target_location=target, reason=reason,
        evidence_timestamp=state.minute, last_known_target_location=target,
        last_progress_at=state.minute, blocked_reason="", retry_after=state.minute,
        wake_on=WAKE_ON.copy(), evidence=_route_evidence(world))


def route_blocker(world: "World", args: dict[str, Any]) -> tuple[str, str] | None:
    _, state = village.context(world)
    goal = state.residents[world.actor_id].fallback.route_goal
    door = world.state.entities.get(args.get("target"))
    if goal is None or door is None or door.kind != "door":
        return None
    current = world.actor.location_id
    destination = door.destination if door.location == current else door.location if door.destination == current else None
    if destination is None:
        return None
    if _route_evidence(world) != goal.evidence:
        return None
    known = state.residents[world.actor_id].known_routes
    path = village.route(world, current, goal.target_location, known_routes=known)
    if path and destination == path[0] and goal.blocked_reason:
        return "ROUTE_GOAL_DEFERRED", "This optional search has no new personal evidence; wait for an encounter or appointment."
    if path and destination == path[0] and f"{destination}->{current}" in goal.attempted_edges:
        return "ROUTE_GOAL_DEFERRED", "Repeated travel for this optional search has no new personal evidence."
    return None


def record_location_transition(world: "World", origin: str, destination: str) -> None:
    config, state = village.context(world)
    edge = next((eid for eid, entity in world.state.entities.items()
                 if entity.kind == "door" and {entity.location, entity.destination} == {origin, destination}),
                f"{origin}->{destination}")
    state.travel_events.append(VillageTravelEvent(
        actor=world.actor_id, origin=origin, destination=destination, edge=edge,
        started_at=max(0, state.minute - 5), ended_at=state.minute,
        current_location=destination))
    del state.travel_events[:-500]
    fallback = state.residents[world.actor_id].fallback
    from npc_sim import village_depot
    if village_depot.feed_store_enabled(world) and world.actor_id == config.livestock.keeper:
        if destination == village_depot.FEED_STORE:
            fallback.feed_store_stock = state.stocks[destination].copy()
            fallback.feed_store_checked_day = state.minute // 1440
    goal = fallback.route_goal
    if goal and _route_evidence(world) != goal.evidence:
        goal.blocked_reason = ""
        goal.attempted_edges.clear()
        goal.evidence = _route_evidence(world)
        goal.evidence_timestamp = state.minute
        goal.retry_after = state.minute
    if goal and not goal.blocked_reason:
        edge = f"{origin}->{destination}"
        reverse = f"{destination}->{origin}"
        if reverse in goal.attempted_edges and _route_evidence(world) == goal.evidence:
            goal.blocked_reason = "Repeated optional route without a new observation or useful interaction."
            goal.retry_after = state.minute + village_extensions.policy(world).idle_review_minutes
            _emit(world, "ROUTE_GOAL_DEFERRED", goal.goal_id, goal.blocked_reason,
                  attempted_edges=goal.attempted_edges.copy())
        goal.attempted_edges.append(edge)
        goal.attempted_edges = goal.attempted_edges[-12:]
        if destination == goal.target_location:
            goal.last_progress_at = state.minute
            goal.blocked_reason = "Optional destination visited; defer further searches until new evidence."
            goal.retry_after = state.minute + village_extensions.policy(world).idle_review_minutes
    if purposeful(world):
        communication = state.communication
        if communication and destination == world.state.entities[communication.config.board_id].location:
            fallback.board_last_checked = state.minute
        if world.actor_id == config.carpenter and destination == config.mill:
            fallback.mill_last_checked = state.minute
    fallback.recent_locations.append(VillageLocationTransition(
        origin=origin, destination=destination, minute=state.minute))
    del fallback.recent_locations[:-12]
    for key, need in fallback.supply_needs.items():
        if (need.status not in {"identified", "request-posted", "awaiting-response", "accepted", "conditional"}
                or need.supplier_id not in config.residents
                or destination != config.residents[need.supplier_id].work or origin == destination):
            continue
        evidence = _supplier_evidence(world, need)
        search = fallback.supplier_searches.get(key)
        stock = state.stocks.get(destination, {}).get(need.good, 0)
        if search is None or search.evidence != evidence or search.observed_stock != stock:
            fallback.supplier_searches[key] = VillageSupplierSearch(
                visits=1, evidence=evidence, observed_stock=stock, last_visit=state.minute)
        else:
            search.visits += 1
            search.last_visit = state.minute
        if len(fallback.supplier_searches) > 24:
            del fallback.supplier_searches[next(iter(fallback.supplier_searches))]
        if fallback.supplier_searches[key].visits == 2:
            _emit(world, "SUPPLIER_SEARCH_LOOP", f"supplier-search:{key}",
                  "Repeated visits to a supplier for the same unresolved personally known need.",
                  need_key=key, visits=2)


def repeated_supplier_searches(world: "World") -> list[str]:
    config, state = village.context(world)
    fallback = state.residents[world.actor_id].fallback
    return [key for key, search in fallback.supplier_searches.items()
            if (need := fallback.supply_needs.get(key)) is not None
            and search.visits >= 2 and search.evidence == _supplier_evidence(world, need)
            and (need.supplier_id in config.residents
                 and (world.actor.location_id != config.residents[need.supplier_id].work
                      or search.observed_stock == state.stocks.get(world.actor.location_id, {}).get(need.good, 0)))
            and need.status in {"identified", "request-posted", "awaiting-response", "accepted", "conditional"}
            and not (state.minute >= need.next_check_after > search.last_visit)]


def _inspection_revision(world: "World") -> str:
    location = world.actor.location_id
    visible = set(world.visible_ids()) | set(world.actor.inventory) | {location}
    return _digest({key: entity.model_dump() for key, entity in world.state.entities.items()
                    if key in visible})


def _obligation_limits(world: "World") -> list[int]:
    config, state = village.context(world)
    policy = village_extensions.policy(world)
    resident = config.residents[world.actor_id]
    day = state.minute // 1440 * 1440
    obligations = [(day + village_life.daily_meal_minute(resident), "tavern"),
                   (day + resident.bed_minute, resident.home)]
    if policy.breakfast:
        due = day + resident.wake_minute + policy.breakfast_delay
        obligations.append((due, "tavern"))
    limits = []
    for due, destination in obligations:
        if due > state.minute:
            path = village.route(world, world.actor.location_id, destination)
            if path is not None:
                journal = (5 if destination == resident.home and state.legacy is not None
                           and not _journal_written(world) else 0)
                limits.append(due - (5 * len(path) + 5 if path else 0) - journal)
    return limits


def _journal_written(world: "World") -> bool:
    from npc_sim import village_legacy
    return village_legacy.journal_written(world, world.actor_id)


def next_review(world: "World") -> int:
    config, state = village.context(world)
    policy = village_extensions.policy(world)
    resident = config.residents[world.actor_id]
    interval = policy.downtime_review_minutes if purposeful(world) and (
        resident.wake_minute <= state.minute % 1440 < resident.bed_minute) else policy.idle_review_minutes
    times = [state.minute + interval, village_extensions.next_personal_event(world)]
    times.extend(max(state.minute, time) for time in _obligation_limits(world))
    if policy.breakfast:
        cutoff = (state.minute // 1440 * 1440 + resident.wake_minute
                  + policy.breakfast_delay + policy.meal_window)
        if cutoff > state.minute:
            times.append(cutoff)
    return min(time for time in times if time >= state.minute)


def _inspection_ready(world: "World") -> bool:
    config, state = village.context(world)
    resident = config.residents[world.actor_id]
    person = state.residents[world.actor_id]
    if (world.actor.location_id != resident.work or person.life is None or person.life.sleeping
            or person.life.work or village_life.rest_due(resident, person.life, state.minute)
            or village_extensions.blocked_until(world, world.actor_id) is not None
            or state.minute + 5 > next_review(world)):
        return False
    previous = person.fallback.inspections.get(world.actor.location_id)
    return previous is None or previous[1] != _inspection_revision(world) or (
        state.minute >= previous[0] + village_extensions.policy(world).idle_review_minutes)


def _unread_paper(world: "World", fixture: str) -> bool:
    communication = world.state.village.communication
    entity = world.state.entities.get(fixture)
    if not communication or not entity or entity.hidden or entity.broken or entity.location != world.actor.location_id:
        return False
    for nid, version in communication.placements.get(fixture, {}).items():
        notice = communication.notices[nid][version - 1]
        if notice.terms.private and world.actor_id not in {notice.author, *notice.terms.recipients}:
            continue
        memory = communication.knowledge.get(world.actor_id, {}).get(nid)
        if memory is None or memory.snapshot.version < version or memory.confidence != "direct":
            return True
    return False


def _travel_choice(world: "World", destination: str, task: str, reason: str,
                   priority: str) -> dict[str, Any] | None:
    config, state = village.context(world)
    if world.actor.location_id == destination:
        return None
    clock = state.minute % 1440
    sleeping_contact = next((other for other, resident in config.residents.items()
                             if other != world.actor_id and destination == resident.work
                             and clock < resident.wake_minute
                             and state.residents[other].life is not None
                             and state.residents[other].life.sleeping
                             and "inbox" not in reason.casefold()
                             and "board" not in reason.casefold()), None)
    if sleeping_contact:
        return None
    from npc_sim import village_walking
    if village_walking.enabled(world):
        plan = state.residents[world.actor_id].walking_plan
        if plan and plan.destination == destination and plan.next_step < len(plan.doors):
            door = plan.doors[plan.next_step]
            if village_walking.check_step(world, {"target": door}) is None:
                return {"task_id": task, "action_id": "go", "arguments": {"target": door},
                        "destination": destination, "priority": priority, "reason": reason}
        if plan or village_walking.check_plan(world, destination) is not None:
            return None
    known = state.residents[world.actor_id].known_routes
    path = village.route(world, world.actor.location_id, destination,
                         known_routes=known if known else None)
    if not path:
        return None
    limits = _obligation_limits(world)
    if limits and state.minute + 5 * len(path) + 10 > min(limits):
        return None
    if village_walking.enabled(world):
        return {"task_id": task, "action_id": "village_plan_walk",
                "arguments": {"destination": destination, "purpose": reason},
                "destination": destination, "priority": priority, "reason": reason}
    door = next((eid for eid, entity in world.state.entities.items()
                 if entity.kind == "door" and {entity.location, entity.destination}
                 == {world.actor.location_id, path[0]} and not entity.hidden
                 and entity.open and not entity.locked and not entity.broken
                 and not world.lock_for(entity).locked and not world.lock_for(entity).broken
                 and not entity.traversal_capabilities and not entity.required_challenges
                 and entity.max_body_size != "small" and (not known or eid in known)), None)
    if door is None:
        return None
    return {"task_id": task, "action_id": "go", "arguments": {"target": door},
            "destination": destination, "priority": priority, "reason": reason}


def _egg_negotiation_choices(world: "World") -> list[dict[str, Any]]:
    from npc_sim import village_communication
    from npc_sim.village_economy_models import VillageSupplierOrder

    _, state = village.context(world)
    communication = state.communication
    livestock = state.livestock
    economy = state.economy
    if not communication or not livestock or not livestock.config.enabled or economy is None:
        return []
    rid = world.actor_id
    if rid not in {livestock.config.keeper, economy.config.innkeeper}:
        return []
    known = communication.knowledge.get(rid, {})
    choices = []
    for memory in known.values():
        notice = memory.snapshot
        terms = notice.terms
        if (memory.confidence != "direct" or terms.type != "supply_request" or terms.subject != "eggs"
                or terms.recipients != [livestock.config.keeper] or notice.status not in {"posted", "seen"}
                or terms.quantity is None or terms.destination is None or terms.unit_price is None
                or terms.delivery_fee is None or terms.required_fresh_until is None
                or terms.due_at is None):
            continue
        if rid == livestock.config.keeper:
            if notice.author == rid or notice.responder == rid:
                continue
            if terms.due_at <= state.minute:
                continue
            proposed_day = state.minute // 1440 + 1
            if notice.revised_from_counteroffer and terms.due_at // 1440 in {state.minute // 1440, proposed_day}:
                response = "accept"
                arguments = {"request_id": notice.id, "response": response}
            else:
                if terms.due_at // 1440 != state.minute // 1440:
                    continue
                response = "propose"
                if notice.status != "posted" or notice.proposed_deadline is not None:
                    continue
                use_at = proposed_day * 1440 + 1140
                deadline = use_at - economy.config.delivery_lead_minutes
                arguments = {"request_id": notice.id, "response": response,
                             "proposed_deadline": deadline, "proposed_fresh_until": use_at}
                current = VillageSupplierOrder(
                    id="candidate-egg-current", request_key=f"notice:{notice.id}",
                    buyer=notice.author, supplier=rid, source="farm", destination=terms.destination,
                    product="eggs", quantity=terms.quantity, price=terms.unit_price,
                    delivery_fee=terms.delivery_fee, deadline=terms.due_at,
                    required_fresh_until=terms.required_fresh_until, created_at=state.minute)
                available = village_economy.available_for_order(world, "farm", "eggs", current.required_fresh_until)
                if available >= current.quantity:
                    feasible_today, _ = village_economy.order_feasibility(world, current)
                else:
                    plan_today, _ = village_economy.production_plan(world, current)
                    feasible_today = bool(plan_today)
                if feasible_today:
                    continue
            deadline = arguments["proposed_deadline"] if response == "propose" else terms.due_at
            fresh_until = arguments["proposed_fresh_until"] if response == "propose" else terms.required_fresh_until
            order = VillageSupplierOrder(
                id="candidate-egg", request_key=f"notice:{notice.id}", buyer=notice.author,
                supplier=rid, source="farm", destination=terms.destination, product="eggs",
                quantity=terms.quantity, price=terms.unit_price, delivery_fee=terms.delivery_fee,
                deadline=deadline, required_fresh_until=fresh_until,
                created_at=state.minute)
            stock = village_economy.available_for_order(world, "farm", "eggs", order.required_fresh_until)
            if stock >= order.quantity:
                feasible, _ = village_economy.order_feasibility(world, order)
            else:
                plan, _ = village_economy.production_plan(world, order)
                feasible = bool(plan)
            if not feasible:
                continue
        else:
            if (notice.author != rid or notice.responder != livestock.config.keeper
                    or notice.status != "seen" or not notice.proposed_deadline
                    or not notice.proposed_fresh_until or notice.proposed_deadline <= state.minute):
                continue
            if notice.proposed_deadline // 1440 not in {state.minute // 1440, state.minute // 1440 + 1}:
                continue
            next_day_plans = [plan for plan in economy.config.meal_plans
                              if plan.id == "supper" and plan.portions > 0]
            from npc_sim import village_livestock
            dairy = village_livestock.requests(world, rid)
            needs_eggs = (notice.proposed_fresh_until <= notice.proposed_deadline // 1440 * 1440 + 1140
                          and bool(next_day_plans)
                          and (notice.proposed_deadline // 1440 > state.minute // 1440
                               and min(2, (next_day_plans[0].portions + 1) // 2) >= terms.quantity
                               or notice.proposed_deadline // 1440 == state.minute // 1440
                               and any(inputs.get("eggs", 0) >= terms.quantity for _, _, inputs in dairy))
                          and village_economy.usable_stock(world, terms.destination, "eggs",
                                                             notice.proposed_fresh_until) < terms.quantity)
            if not needs_eggs:
                response = "decline"
                arguments = {"notice_id": notice.id, "expected_version": notice.version,
                             "reason": "Tomorrow's supper does not need these eggs", "status": "cancelled"}
            else:
                arguments = {"request_id": notice.id, "changes": {
                    "due_at": notice.proposed_deadline,
                    "required_fresh_until": notice.proposed_fresh_until}}
                response = "revise"
        try:
            channel = village_communication._intent_channel(
                world, terms, author=notice.author if response in {"accept", "propose"} else None)
        except village_communication.ChannelUnavailable:
            recipient = notice.author if response in {"accept", "propose"} else livestock.config.keeper
            box = next((key for key, owner in communication.boxes.items() if owner == recipient), None)
            if box and world.state.entities.get(box):
                travel = _travel_choice(world, world.state.entities[box].location,
                                        f"supply:eggs:reach-{recipient}:{notice.id}",
                                        "Reach the recipient's real workplace inbox with the egg request terms.", "P3")
                if travel:
                    choices.append(travel)
            continue
        if response == "decline":
            arguments.update(channel)
        choices.append({"task_id": f"supply:eggs:{response}:{notice.id}",
                        "action_id": ("village_update_request" if response == "revise" else
                                      "village_notice_close" if response == "decline" else
                                      "village_respond_to_request"),
                        "arguments": arguments, "priority": "P2",
                        "reason": "Negotiate only a physically received, funded and feasible next-day egg supply."})
    return choices


def _purposeful_choices(world: "World") -> list[dict[str, Any]]:
    config, state = village.context(world)
    person = state.residents[world.actor_id]
    fallback = person.fallback
    location = world.actor.location_id
    choices: list[dict[str, Any]] = []
    if not (config.residents[world.actor_id].wake_minute <= state.minute % 1440
            < village_life.production_closing_minute(world, world.actor_id)):
        return choices
    from npc_sim import village_depot
    if village_depot.feed_store_enabled(world) and world.actor_id == config.livestock.keeper:
        choices.extend(_feed_store_choices(world))
    choices.extend(_egg_negotiation_choices(world))
    if state.research and state.research.config.enabled and state.economy:
        from npc_sim import village_research
        projects = village_research.discoveries(world)
        for order in sorted(village_economy._personal_orders(world), key=lambda order: order.deadline):
            project = projects.get(order.product)
            if (not project or project.proposal.kind not in {"food", "crop"}
                    or order.supplier != world.actor_id or project.owner != world.actor_id
                    or order.status not in village_economy.LOADABLE or not order.loadable
                    or village_economy.loadable_for_order(world, order) >= order.loadable):
                continue
            task = f"discovery:produce:{order.id}"
            reason = "Make the verified food or crop for a personally confirmed order."
            if location != project.location:
                travel = _travel_choice(world, project.location, task, reason, "P1")
                if travel:
                    choices.append(travel)
                continue
            end = state.minute + project.proposal.duration * village_life.cooldown_factor(world, world.actor_id)
            shelf = village_research.shelf_life(world, order.product)
            if end >= order.deadline or (shelf and end + shelf <= order.required_fresh_until):
                continue
            args = {"project": order.product}
            action = "village_research_make"
            if (village_research._guard(world, project, "produce", end) is None
                    and village_life.action_blocker(world, action, args=args) is None
                    and action_blocker(world, action, args, check_wait=False) is None):
                choices.append({"task_id": task, "action_id": action, "arguments": args,
                                "priority": "P1", "reason": reason})
                break
    if state.communication and state.economy:
        from npc_sim import village_communication
        notices = sorted((memory for memory in state.communication.knowledge.get(world.actor_id, {}).values()
                          if memory.snapshot.terms.due_at is not None),
                         key=lambda memory: memory.snapshot.terms.due_at)
        for memory in notices:
            notice = memory.snapshot
            terms = notice.terms
            if (memory.confidence != "direct" or terms.type != "supply_request"
                    or terms.recipients != [world.actor_id] or notice.status not in {"posted", "seen"}
                    or terms.due_at is None or terms.due_at <= state.minute
                    or terms.required_fresh_until is None or terms.quantity is None
                    or terms.unit_price is None or terms.delivery_fee is None):
                continue
            from npc_sim.village_economy_models import VillageSupplierOrder
            order = VillageSupplierOrder(
                id=f"candidate-{notice.id}", request_key=f"notice:{notice.id}",
                buyer=notice.author, supplier=world.actor_id, source=config.residents[world.actor_id].work,
                destination=terms.destination, product=terms.subject, quantity=terms.quantity,
                price=terms.unit_price, delivery_fee=terms.delivery_fee,
                deadline=terms.due_at, required_fresh_until=terms.required_fresh_until,
                created_at=state.minute)
            feasible, _ = village_economy.order_feasibility(world, order)
            plan, _ = ({}, "") if feasible else village_economy.production_plan(world, order)
            if not feasible and not plan:
                continue
            try:
                channel = village_communication._intent_channel(world, terms, author=notice.author)
            except village_communication.ChannelUnavailable:
                box = next((box for box, owner in state.communication.boxes.items()
                            if owner == notice.author), None)
                target = world.state.entities[box].location if box else None
                travel = _travel_choice(world, target, f"supply:reply:{notice.id}",
                                        "Reach the buyer's real inbox to accept a feasible supply request.", "P1") if target else None
                if travel:
                    choices.append(travel)
                continue
            choices.append({"task_id": f"supply:reply:{notice.id}",
                            "action_id": "village_respond_to_request",
                            "arguments": {"request_id": notice.id, "response": "accept"},
                            "priority": "P1", "reason": "Accept a personally learned, feasible funded supply order."})
            break
    if state.communication:
        from npc_sim import village_communication
        for memory in state.communication.knowledge.get(world.actor_id, {}).values():
            notice = memory.snapshot
            plan = notice.appointment
            if (memory.confidence != "direct" or plan is None or world.actor_id not in plan.members
                    or plan.status != "confirmed" or notice.status in {"completed", "cancelled", "expired"}
                    or state.minute >= plan.end_at):
                continue
            if location != plan.place:
                travel = _travel_choice(world, plan.place, f"crew:attend:{plan.id}",
                                        "Reach the confirmed crew meeting before its window closes.", "P1")
                if travel and state.minute + 5 <= plan.end_at:
                    choices.append(travel)
            elif state.minute >= plan.due_at and village_communication.project_blocker(world, plan.project) is None:
                args = {"project": plan.project}
                if (village_life.action_blocker(world, "village_project") is None
                        and action_blocker(world, "village_project", args, check_wait=False) is None):
                    choices.append({"task_id": f"crew:work:{plan.id}",
                                    "action_id": "village_project", "arguments": args,
                                    "priority": "P1", "reason": "Contribute to the confirmed crew session."})
            break
    if state.communication:
        from npc_sim import village_communication
        for request in village_economy.suggestions(world, personal=True):
            if (not request["request_key"].endswith(f":production:{world.actor_id}:{request['product']}")
                    and not request["request_key"].endswith(f":recycling:{world.actor_id}:{request['product']}")
                    and not (state.firewood and request["product"] == "firewood")):
                continue
            if request["deadline"] <= state.minute:
                continue
            terms = village_communication.NoticeTerms(
                type="supply_request", subject=request["product"], description="Inputs for accepted production",
                recipients=[request["supplier"]], quantity=request["quantity"],
                destination=request["destination"], due_at=request["deadline"],
                required_fresh_until=request["required_fresh_until"])
            if any(history[-1].author == world.actor_id
                   and village_communication._need_key(world.actor_id, history[-1].terms)
                   == village_communication._need_key(world.actor_id, terms)
                   and history[-1].status not in village_communication.TERMINAL
                   for history in state.communication.notices.values()):
                continue
            terms = village_communication.preferred_supply_contact(world, terms)
            if terms is None:
                continue
            try:
                village_communication._intent_channel(world, terms)
            except village_communication.ChannelUnavailable:
                board = state.communication.config.board_id
                travel = _travel_choice(world, world.state.entities[board].location,
                                        f"production:request:{request['request_key']}",
                                        "Reach the board to request inputs for a pending order.", "P1")
                if travel:
                    choices.append(travel)
                break
            args = {"good": request["product"], "quantity": request["quantity"],
                    "purpose": terms.description, "deadline": request["deadline"],
                    "required_fresh_until": request["required_fresh_until"],
                    "preferred_supplier_id": terms.recipients[0]}
            if action_blocker(world, "village_request_supply", args, check_wait=False) is None:
                choices.append({"task_id": f"production:request:{request['request_key']}",
                                "action_id": "village_request_supply", "arguments": args,
                                "priority": "P1", "reason": "Fund upstream inputs before a pending output order expires."})
            break
    if state.firewood and world.actor_id == state.firewood.config.supplier:
        from npc_sim import village_firewood
        plan = (next((item for item in state.firewood.delivery_plans
                      if item.carrier == world.actor_id and item.status in {"loaded", "in_progress"}), None)
                or village_firewood.delivery_plan(world))
        if (plan is not None
                and village_life.action_blocker(world, "village_execute_fuel_delivery") is None
                and action_blocker(world, "village_execute_fuel_delivery", {
                    "route_id": plan.route_id}, check_wait=False) is None):
            choices.append({"task_id": f"fuel:delivery:{plan.route_id}",
                            "action_id": "village_execute_fuel_delivery",
                            "arguments": {"route_id": plan.route_id}, "priority": "P1",
                            "reason": "Deliver a confirmed funded fuel load before its deadline."})
    if world.actor_id == "nella" and state.communication and state.economy:
        from npc_sim import village_services
        orders = [order for order in village_economy._personal_orders(world)
                  if order.buyer == world.actor_id and order.status in village_economy.LOADABLE
                  and order.loadable and order.deadline >= state.minute]
        cargo = [item for item in person.shipments if item.order_id in {order.id for order in orders}]
        if cargo:
            destination = cargo[0].destination
            if location == destination:
                choices.append({"task_id": f"store:unload:{cargo[0].order_id}",
                                "action_id": "village_unload_goods", "arguments": {}, "priority": "P1",
                                "reason": "Unload and pay for bounded general-store replenishment."})
            else:
                travel = _travel_choice(world, destination, f"store:deliver:{cargo[0].order_id}",
                                        "Carry paid store stock to the general store.", "P1")
                if travel:
                    choices.append(travel)
        elif orders:
            order = min(orders, key=lambda item: (item.deadline, item.id))
            if location != order.source:
                travel = _travel_choice(world, order.source, f"store:collect:{order.id}",
                                        "Collect a personally confirmed store purchase from its real source.", "P1")
                if travel:
                    choices.append(travel)
            else:
                quantity = min(order.loadable, village_economy.loadable_for_order(world, order),
                               village_services.foot_capacity(world, world.actor_id))
                if quantity:
                    choices.append({"task_id": f"store:load:{order.id}", "action_id": "village_load_goods",
                                    "arguments": {"goods": {order.product: quantity},
                                                  "destination": order.destination},
                                    "priority": "P1",
                                    "reason": "Load only confirmed, affordable store surplus within capacity."})
        for request in village_economy.suggestions(world, personal=True):
            if (request["buyer"] != world.actor_id
                    or request["request_key"] != f"{state.minute // 1440}:reserve:{world.actor_id}:{request['product']}"
                    or request["deadline"] <= state.minute):
                continue
            terms = village_communication.NoticeTerms(
                type="supply_request", subject=request["product"], description="Bounded general-store replenishment",
                recipients=[request["supplier"]], quantity=request["quantity"],
                destination=request["destination"], due_at=request["deadline"],
                required_fresh_until=request["required_fresh_until"])
            open_need = any(history[-1].author == world.actor_id
                            and village_communication._need_key(world.actor_id, history[-1].terms)
                            == village_communication._need_key(world.actor_id, terms)
                            and history[-1].status not in village_communication.TERMINAL
                            for history in state.communication.notices.values())
            if open_need:
                continue
            terms = village_communication.preferred_supply_contact(world, terms)
            if terms is None:
                continue
            try:
                village_communication._intent_channel(world, terms)
            except village_communication.ChannelUnavailable:
                board = state.communication.config.board_id
                travel = _travel_choice(world, world.state.entities[board].location,
                                        f"store:post:{request['request_key']}",
                                        "Reach the physical board to fund store replenishment.", "P1")
                if travel:
                    choices.append(travel)
                break
            choices.append({"task_id": f"store:request:{request['request_key']}",
                            "action_id": "village_request_supply",
                            "arguments": {"good": request["product"], "quantity": request["quantity"],
                                          "purpose": terms.description, "deadline": request["deadline"],
                                          "required_fresh_until": request["required_fresh_until"],
                                          "preferred_supplier_id": terms.recipients[0]},
                            "priority": "P1",
                            "reason": "Buy real producer surplus above reserve for bounded resale."})
            break
    if state.economy and (world.actor_id == "nella" or any(
            order.buyer == world.actor_id and order.status in village_economy.LOADABLE
            for order in village_economy._personal_orders(world))):
        freight = _freight_choices(world)
        if freight:
            choices.append(freight)
    if village_depot.enabled(world) and not person.shipments:
        depot_choice = _depot_choice(world)
        if depot_choice:
            choices.append(depot_choice)
    if world.actor_id == village_economy.config(world).innkeeper and state.communication:
        from npc_sim import village_communication
        for request in village_economy.suggestions(world, personal=True):
            if request["product"] not in {"bread", "fish", "preserved-fish"} or request["deadline"] <= state.minute:
                continue
            terms = village_communication.NoticeTerms(
                type="supply_request", subject=request["product"],
                description=f"Food for {request['request_key']}",
                recipients=[request["supplier"]], quantity=request["quantity"],
                destination=request["destination"], due_at=request["deadline"],
                required_fresh_until=request["required_fresh_until"])
            open_need = any(history[-1].author == world.actor_id
                            and village_communication._need_key(world.actor_id, history[-1].terms)
                            == village_communication._need_key(world.actor_id, terms)
                            and history[-1].status not in village_communication.TERMINAL
                            for history in state.communication.notices.values())
            if open_need:
                continue
            terms = village_communication.preferred_supply_contact(world, terms)
            if terms is None:
                continue
            try:
                village_communication._intent_channel(world, terms)
            except village_communication.ChannelUnavailable:
                board = state.communication.config.board_id
                target = world.state.entities[board].location
                travel = _travel_choice(world, target, f"food:request:{request['request_key']}",
                                        "Reach the physical board to request funded food before its cutoff.", "P1")
                if travel:
                    choices.append(travel)
                continue
            args = {"good": request["product"], "quantity": request["quantity"],
                    "purpose": terms.description, "deadline": request["deadline"],
                    "required_fresh_until": request["required_fresh_until"],
                    "preferred_supplier_id": terms.recipients[0]}
            if action_blocker(world, "village_request_supply", args, check_wait=False) is None:
                choices.append({"task_id": f"food:request:{request['request_key']}",
                                "action_id": "village_request_supply", "arguments": args,
                                "priority": "P1", "reason": "Fund a new physical order for uncovered food service."})
            break

    # The check-in is scheduled, not triggered by privileged knowledge of a remote breakdown.
    personally_informed = any(message.get("actor_id") == config.miller and "mill drive fail" in message.get("text", "")
                              for message in world.actor.messages)
    if world.actor_id == config.carpenter and not state.repaired and state.minute >= config.breakdown_minute + 15:
        if location == config.mill and state.breakdown and world.state.entities[config.mill_equipment].health == 0:
            if village.resident_location(world, config.miller) == location:
                supplies = [config.repair_part, config.hammer]
                ready = (not config.expanded_rules or state.stocks.get(config.mill, {}).get("planks", 0) >= 1)
                ready = ready and all(world.state.entities[item].location in
                                      {config.mill, config.carpenter, config.miller}
                                      and not world.state.entities[item].broken
                                      and not world.state.entities[item].hidden for item in supplies)
                if ready and state.minute + 5 <= next_review(world):
                    choices.append({"task_id": "incident:mill-repair", "action_id": "village_project",
                                    "arguments": {"project": "repair_mill"}, "priority": "P1",
                                    "reason": "The mill is visibly broken and both repair workers are physically here with real supplies."})
        elif personally_informed or fallback.mill_last_checked < config.breakdown_minute:
            check = _travel_choice(world, config.mill, "incident:mill-check-in",
                                   "Scheduled mill check-in; confirm any failure only at the mill.", "P2")
            if check:
                choices.append(check)
    witnessed_failure = any(state.events[index].action == "breakdown" and
                            state.events[index].target == config.mill_equipment and
                            state.events[index].minute <= state.minute
                            for index in person.memories if index < len(state.events))
    if (world.actor_id == config.miller and location == config.mill and state.breakdown
            and not state.repaired and witnessed_failure
            and village.resident_location(world, config.carpenter) != location
            and "mill-relay" not in fallback.inspections):
        check = _travel_choice(world, config.residents[config.carpenter].work, "incident:relay",
                               "Personally witnessed mill failure; find the carpenter at the workshop to relay it in person.", "P2")
        if check:
            choices.append(check)
    if (world.actor_id == config.miller and location == config.residents[config.carpenter].work
            and state.breakdown and not state.repaired and witnessed_failure
            and village.resident_location(world, config.carpenter) == location
            and "mill-relay" not in fallback.inspections):
        choices.append({"task_id": "incident:relay", "action_id": "say",
                        "arguments": {"message": "I saw the mill drive fail at the watermill. Please meet me there with the beam and hammer."},
                        "priority": "P1", "reason": "Relay a personally witnessed failure to the carpenter physically present."})

    if state.social and "fish-smoking" in state.social.config.activities:
        if world.actor_id == "finn":
            needs = [item for item in village_economy.suggestions(world, personal=True)
                     if item["product"] == "logs" and item["request_key"].endswith(":fish-smoking:finn:logs")]
            if needs:
                need = needs[0]
                request = {"good": "logs", "quantity": need["quantity"],
                           "purpose": "One log for smoking uncommitted fresh fish at my riverbank smoker",
                           "deadline": need["deadline"], "required_fresh_until": need["required_fresh_until"],
                           "preferred_supplier_id": "rowan"}
                communication = state.communication
                open_notice = any(memory.snapshot.author == world.actor_id
                                  and memory.snapshot.terms.type == "supply_request"
                                  and memory.snapshot.terms.subject == "logs"
                                  and memory.snapshot.status not in {"completed", "rejected", "cancelled", "expired", "superseded"}
                                  for memory in communication.knowledge.get(world.actor_id, {}).values()) if communication else False
                if open_notice and location == config.residents["finn"].work and _unread_paper(
                        world, next(box for box, owner in communication.boxes.items() if owner == "finn")):
                    choices.append({"task_id": "preservation:check-reply", "action_id": "village_check_messages",
                                    "arguments": {"channel": "inbox"}, "priority": "P4",
                                    "reason": "Read a changed physical reply before considering another smoker-log request."})
                if not open_notice and communication:
                    from npc_sim import village_communication
                    try:
                        terms = village_communication.NoticeTerms(
                            type="supply_request", subject="logs", description=request["purpose"],
                            recipients=["rowan"], quantity=need["quantity"],
                            destination=config.residents[world.actor_id].work,
                            due_at=request["deadline"], required_fresh_until=request["required_fresh_until"])
                        channel = village_communication._intent_channel(world, terms)
                    except village_communication.ChannelUnavailable:
                        channel = None
                    if channel:
                        choices.append({"task_id": "preservation:request-log", "action_id": "village_request_supply",
                                        "arguments": request, "priority": "P4",
                                        "reason": "Fund one real log for optional fish preservation through a physical channel."})
                    elif state.minute % 1440 < 780:
                        box = next((box for box, owner in communication.boxes.items() if owner == "rowan"), None)
                        target = world.state.entities[box].location if box else None
                        choice = _travel_choice(world, target, "preservation:post-log-request",
                                                "Reach Rowan's physical inbox to request a funded smoker log.", "P4") if target else None
                        if choice:
                            choices.append(choice)
            if (not needs and state.stocks.get(config.residents["finn"].work, {}).get("logs", 0) >= 1
                    and village_economy.usable_stock(world, config.residents["finn"].work,
                                                     "preserved-fish", state.minute)
                    < max(1, village_economy.policy_reserve(
                        world, config.residents["finn"].work, "preserved-fish"))):
                from npc_sim import village_social
                activity = village_social.observation(world).get("social", {}).get("activities", {}).get("fish-smoking")
                if activity and activity["available"] and location == activity["location"]:
                    choices.append({"task_id": "preservation:smoke-fish", "action_id": "village_leisure",
                                    "arguments": {"activity": "fish-smoking", "companions": []}, "priority": "P4",
                                    "reason": "Use only uncommitted fresh fish and the paid, physically delivered smoker log."})
        elif world.actor_id == "rowan":
            from npc_sim import village_services
            known = state.communication.knowledge.get("rowan", {}) if state.communication else {}
            requests = [memory.snapshot for memory in known.values()
                        if memory.snapshot.author == "finn" and memory.snapshot.terms.type == "supply_request"
                        and memory.snapshot.terms.subject == "logs"
                        and memory.snapshot.status == "posted" and memory.confidence == "direct"]
            if requests:
                notice = max(requests, key=lambda item: item.terms.due_at)
                from npc_sim import village_communication
                try:
                    channel = village_communication._intent_channel(world, notice.terms, author="finn")
                except village_communication.ChannelUnavailable:
                    channel = None
                if channel:
                    choices.append({"task_id": "preservation:accept-log", "action_id": "village_respond_to_request",
                                    "arguments": {"request_id": notice.id, "response": "accept"}, "priority": "P3",
                                    "reason": "Accept Finn's personally read, funded log request through a physical channel."})
                else:
                    box = next((box for box, owner in state.communication.boxes.items() if owner == "finn"), None)
                    target = world.state.entities[box].location if box else None
                    choice = _travel_choice(world, target, "preservation:reply-log",
                                            "Reach Finn's physical inbox to accept his personally read log request.", "P4") if target else None
                    if choice:
                        choices.append(choice)
            orders = [order for order in village_economy._personal_orders(world)
                      if order.supplier == "rowan" and order.buyer == "finn"
                      and order.product == "logs" and order.status in village_economy.COMMITTED
                      and order.coordination_notice_id in known]
            if orders:
                order = min(orders, key=lambda item: item.deadline)
                cargo = person.shipments
                if any(item.order_id == order.id for item in cargo):
                    if location == order.destination:
                        choices.append({"task_id": "preservation:unload-log", "action_id": "village_unload_goods",
                                        "arguments": {}, "priority": "P3",
                                        "reason": "Unload the real ordered log and settle its funded delivery."})
                    else:
                        choice = _travel_choice(world, order.destination, "preservation:deliver-log",
                                                "Carry the accepted log shipment to Finn's riverbank smoker.", "P3")
                        if choice:
                            choices.append(choice)
                elif location == order.source and village_economy.loadable_for_order(world, order) > 0:
                    amount = min(order.loadable, village_services.foot_capacity(world, "rowan"))
                    if amount > 0:
                        choices.append({"task_id": "preservation:load-log", "action_id": "village_load_goods",
                                        "arguments": {"goods": {"logs": amount}, "destination": order.destination},
                                        "priority": "P3", "reason": "Load one funded, accepted and unreserved log."})
                elif location != order.source:
                    choice = _travel_choice(world, order.source, "preservation:collect-log",
                                            "Return to the known source of Finn's accepted log order.", "P3")
                    if choice:
                        choices.append(choice)

    if state.social is not None and not choices:
        from npc_sim import village_social
        social = village_social.observation(world).get("social", {})
        activities = social.get("activities", {})
        garden = activities.get("herb-garden-care") if world.actor_id == "iona" else None
        limits = _obligation_limits(world)
        if (garden and state.minute % 1440 >= 540 and state.minute % 1440 + 5 <= 1080
                and (not limits or state.minute + 5 <= min(limits))
                and village_social.harvest_blocker(world) is None
                and action_blocker(world, "village_garden_harvest", {}, check_wait=False) is None):
            choices.append({"task_id": "garden:harvest", "action_id": "village_garden_harvest",
                            "arguments": {}, "priority": "P5",
                            "reason": "Harvest the ripe herb before tending the plot again."})
        options = list(activities.items())
        if garden:
            options.sort(key=lambda option: option[0] != "herb-garden-care")
        chosen = None if choices else next(((key, activity) for key, activity in options
                       if activity["available"] and activity["preferred"]
                       and activity["location"] == location
                       and (not limits or state.minute + activity["duration"] <= min(limits))
                       and action_blocker(world, "village_leisure", {"activity": key, "companions": []},
                                          check_wait=False) is None), None)
        if chosen:
            choices.append({"task_id": f"leisure:{chosen[0]}", "action_id": "village_leisure",
                            "arguments": {"activity": chosen[0], "companions": []}, "priority": "P5",
                            "reason": "Preferred activity is locally feasible with actual time, equipment and supplies."})

    communication = state.communication
    if communication:
        board = communication.config.board_id
        board_entity = world.state.entities.get(board)
        if board_entity and location == board_entity.location and _unread_paper(world, board):
            choices.append({"task_id": "information:board", "action_id": "village_check_messages",
                            "arguments": {"channel": "board"}, "priority": "P4",
                            "reason": "Unread accessible paper at the physical noticeboard."})
        own_box = next((box for box, owner in communication.boxes.items() if owner == world.actor_id), None)
        if own_box and _unread_paper(world, own_box):
            choices.append({"task_id": "information:inbox", "action_id": "village_check_messages",
                            "arguments": {"channel": "inbox"}, "priority": "P4",
                            "reason": "Unread accessible paper at your own workplace inbox."})
    return choices


def _feed_ration(world: "World") -> dict[str, int]:
    from npc_sim import village_recycling

    state = village.context(world)[1]
    day = state.minute // 1440
    return {"hay": int(not state.livestock.cow.fed or state.livestock.cow.day != day),
            "grain": (village_recycling.hen_ration(world)[0]
                      if not state.livestock.hens.fed or state.livestock.hens.day != day else 0)}


def _feed_trip_blocker(world: "World", farm: str, store: str) -> str | None:
    state = village.context(world)[1]
    path = village.route(world, farm, store, known_routes=state.residents[world.actor_id].known_routes or None)
    if path is None:
        return "No known passable route from farm to the feed store."
    limits = _obligation_limits(world)
    to_farm = village.route(world, world.actor.location_id, farm,
                            known_routes=state.residents[world.actor_id].known_routes or None)
    if to_farm is None:
        return "No known passable route back to the farm."
    if limits and state.minute + 5 * (len(to_farm) + len(path) * 2) + 15 > min(limits):
        return "Not enough time for physical carrying, unloading and return before the next meal or rest obligation."
    return None


def _feed_store_choices(world: "World") -> list[dict[str, Any]]:
    from npc_sim import village_depot, village_services

    config, state = village.context(world)
    person = state.residents[world.actor_id]
    fallback = person.fallback
    location = world.actor.location_id
    store = village_depot.FEED_STORE
    farm = config.residents[world.actor_id].work
    day = state.minute // 1440
    ration = _feed_ration(world)
    cargo = [item for item in person.shipments if item.depot_transfer == "deposit" and item.destination == store]
    if cargo:
        if location == store:
            return [{"task_id": "feed:unload", "action_id": "village_unload_goods", "arguments": {},
                     "priority": "P1", "reason": "Unload the real carried feed before animal care."}]
        route = _travel_choice(world, store, "feed:deliver", "Carry the loaded feed to its real store.", "P1")
        return [route] if route else []
    if person.shipments or not any(ration.values()):
        return []
    if location == store:
        fallback.feed_store_stock = state.stocks[store].copy()
        fallback.feed_store_checked_day = day
    if fallback.feed_store_checked_day != day:
        route = _travel_choice(world, store, "feed:inspect", "Check today's feed at the physical store.", "P2")
        return [route] if route else []
    missing = {product: max(0, quantity - fallback.feed_store_stock.get(product, 0))
               for product, quantity in ration.items()}
    if not any(missing.values()):
        return []
    if _feed_trip_blocker(world, farm, store):
        return []
    if location != farm:
        route = _travel_choice(world, farm, "feed:collect", "Collect unreserved feed at the farm.", "P2")
        return [route] if route else []
    capacity = village_services.foot_capacity(world, world.actor_id)
    goods = {}
    for product in ("hay", "grain"):
        available = max(0, village_economy.usable_stock(world, farm, product, state.minute)
                        - village_economy.protected_quantity(world, farm, product))
        amount = min(missing[product], available, capacity)
        if amount:
            goods[product] = amount
            capacity -= amount
    if not goods:
        return []
    args = {"goods": goods}
    if action_blocker(world, "village_store_feed", args, check_wait=False) is not None:
        return []
    return [{"task_id": "feed:load", "action_id": "village_store_feed", "arguments": args,
             "priority": "P2", "reason": "Move only unreserved farm feed needed for today's cow and hens."}]


def _freight_choices(world: "World") -> dict[str, Any] | None:
    from npc_sim import village_services, village_wagon

    config, state = village.context(world)
    person = state.residents[world.actor_id]
    location = world.actor.location_id
    if person.shipments:
        shipment = min(person.shipments, key=lambda item: item.order_id or "")
        if location == shipment.destination:
            return {"task_id": f"freight:unload:{shipment.order_id or shipment.resource}",
                    "action_id": "village_unload_goods", "arguments": {}, "priority": "P1",
                    "reason": "Unload known funded freight before optional store work."}
        travel = _travel_choice(world, shipment.destination, f"freight:deliver:{shipment.order_id}",
                                "Deliver personally loaded funded freight before its deadline.", "P1")
        return travel
    if (world.actor_id == config.courier and state.wagon and not state.wagon.automatic
            and village_wagon.readiness(world)["ready"]):
        wagon_location = world.state.entities["wagon"].location
        if location != wagon_location:
            return _travel_choice(world, wagon_location, "freight:wagon",
                                  "Collect the repaired wagon to batch the confirmed delivery run.", "P1")
        return {"task_id": "shipment:wagon", "action_id": "village_start_route", "arguments": {},
                "priority": "P1",
                "reason": "Use the delivery wagon's full batch capacity instead of making foot trips."}
    orders = [order for order in village_economy._personal_orders(world)
              if order.status in village_economy.LOADABLE and order.loadable
              and order.deadline >= state.minute]
    feasible = []
    for order in orders:
        quantity = min(order.loadable, village_economy.loadable_for_order(world, order),
                       village_services.foot_capacity(world, world.actor_id))
        if not quantity:
            continue
        approach = village.route(world, location, order.source,
                                 known_routes=state.residents[world.actor_id].known_routes or None)
        delivery = village.route(world, order.source, order.destination,
                                 known_routes=state.residents[world.actor_id].known_routes or None)
        if approach is None or delivery is None:
            continue
        if state.minute + 5 * (len(approach) + len(delivery)) + 10 <= order.deadline:
            feasible.append((order.deadline, order.id, order, quantity))
    if not feasible:
        return None
    _, _, order, quantity = min(feasible)
    if location != order.source:
        return _travel_choice(world, order.source, f"freight:collect:{order.id}",
                              "Collect the earliest personally known funded freight shipment.", "P1")
    return {"task_id": f"freight:load:{order.id}", "action_id": "village_load_goods",
            "arguments": {"goods": {order.product: quantity}, "destination": order.destination},
            "priority": "P1", "reason": "Batch one feasible funded shipment within real capacity and deadline."}


def _depot_choice(world: "World") -> dict[str, Any] | None:
    from npc_sim import village_depot, village_services

    config, state = village.context(world)
    rid, location = world.actor_id, world.actor.location_id
    work = config.residents[rid].work
    capacity = village_services.foot_capacity(world, rid)
    if capacity < 1:
        return None
    cargo = [item for item in state.residents[rid].shipments if item.depot_transfer]
    if cargo:
        shipment = cargo[0]
        if location == shipment.destination:
            return {"task_id": f"depot:unload:{shipment.depot_transfer}",
                    "action_id": "village_unload_goods", "arguments": {}, "priority": "P2",
                    "reason": "Complete the existing physical managed-store move."}
        travel = _travel_choice(world, shipment.destination, f"depot:deliver:{shipment.depot_transfer}",
                                "Carry the existing managed-store load to its real destination.", "P2")
        return travel
    for product, target in village_economy.production_targets(world, work, personal=True).items():
        available = village_economy.usable_stock(world, work, product, state.minute)
        missing = max(0, target - available)
        if not missing:
            continue
        stores = [store for store in village_depot.stores(world)
                  if product in village_depot.store_products(world, store) and store != village_depot.FEED_STORE]
        store = next((site for site in stores
                      if village_economy.usable_stock(world, site, product, state.minute) > 0), None)
        if not store or state.coins[rid] < village_economy.unit_price(world, product):
            continue
        if location == store:
            quantity = min(capacity, missing,
                           village_economy.usable_stock(world, store, product, state.minute))
            return {"task_id": f"depot:withdraw:{product}", "action_id": "village_depot_withdraw",
                    "arguments": {"goods": {product: quantity}}, "priority": "P2",
                    "reason": "Purchase only physically stored stock for a real affordable workplace target."}
        return _travel_choice(world, store, f"depot:reach:{product}",
                              "Visit managed storage for a real affordable workplace shortage.", "P2")
    owned = {}
    for product in state.stocks.get(work, {}):
        surplus = (village_economy.usable_stock(world, work, product, state.minute)
                   - village_economy.protected_quantity(world, work, product))
        if surplus > 0 and village_depot.deposit_store(world, {product: 1}):
            owned[product] = min(surplus, capacity)
    if not owned:
        return None
    store = village_depot.deposit_store(world, owned)
    if store is None and len(owned) > 1:
        candidate = next(iter(owned))
        owned = {candidate: owned[candidate]}
        store = village_depot.deposit_store(world, owned)
    if location != work or store is None:
        return None
    return {"task_id": f"depot:deposit:{next(iter(owned))}", "action_id": "village_depot_load",
            "arguments": {"goods": owned}, "priority": "P3",
            "reason": "Deposit only genuine workplace surplus above commitments and reserve."}


def candidates(world: "World") -> list[dict[str, Any]]:
    config, state = village.context(world)
    resident, person = config.residents[world.actor_id], state.residents[world.actor_id]
    life = person.life
    if life is None or life.sleeping or life.work or person.available_at > state.minute:
        return []
    if state.legacy is not None:
        from npc_sim import village_legacy
        if village_legacy.history_pending(world):
            return [{"task_id": "legacy:read", "action_id": "village_read_history", "arguments": {},
                     "priority": "P0", "reason": "Read inherited history before starting this generation."}]
        if (world.actor.location_id == resident.home and not village_legacy.journal_written(world, world.actor_id)
                and (village_life.rest_due(resident, life, state.minute)
                     or village_life.early_inn_close_ready(world, world.actor_id))):
            return [{"task_id": "legacy:journal", "action_id": "village_write_diary",
                     "arguments": {}, "requires_input": ["text"], "priority": "P0",
                     "reason": "Write today's personal journal before sleeping; include a firsthand note and optionally problems and suggestions."}]
    if not enabled(world):
        return []
    choices: list[dict[str, Any]] = []
    if village_life.rest_due(resident, life, state.minute) or village_life.early_inn_close_ready(world, world.actor_id):
        if world.actor.location_id == resident.home:
            choices.append({"task_id": "need:sleep", "action_id": "village_sleep", "arguments": {},
                            "priority": "P1", "reason": "Sleep at your actual home; the inn may close after everyone has eaten and gone home."})
        return choices
    meal = village_life.available_meal(world, world.actor_id, include_missed=True)
    if meal and world.actor.location_id == "tavern":
        stock = state.stocks.get("tavern", {})
        if village_life.inn_open(world) and (stock.get("meals", 0) or stock.get("bread", 0)
                                             or stock.get("fish", 0) and stock.get("tea", 0)):
            if _meal_funded(world) and action_blocker(world, "village_eat", {}, check_wait=False) is None:
                choices.append({"task_id": "need:meal", "action_id": "village_eat", "arguments": {},
                                "priority": "P1", "reason": "A due meal has actual local food; payment is validated on eating."})
    if purposeful(world):
        for step in village_economy.meal_delivery_actions(world):
            action_id, args = step["action"], step["args"]
            if action_id == "go":
                route = _travel_choice(world, step["destination"], f"meal-delivery:{step['order_id']}",
                                       "Carry a personally known paid bread order toward supper before its real deadline.", "P1")
                if route is None:
                    continue
                args = route["arguments"]
            if action_blocker(world, action_id, args, check_wait=False) is None:
                choices.append({"task_id": f"meal-delivery:{step['order_id']}", "action_id": action_id,
                                "arguments": args, "priority": "P1",
                                "reason": "A personally known funded bread delivery can reach the inn before supper."})
        from npc_sim import village_wagon
        if world.actor_id == config.courier and state.wagon and not state.wagon.automatic:
            wagon = state.wagon
            ready = village_wagon.readiness(world)
            if (ready["ready"] and world.actor.location_id == world.state.entities["wagon"].location
                    and village_life.action_blocker(world, "village_start_route") is None
                    and action_blocker(world, "village_start_route", {}, check_wait=False) is None):
                choices.append({"task_id": "shipment:wagon", "action_id": "village_start_route",
                                "arguments": {}, "priority": "P1",
                                "reason": "Drive the repaired, hitched wagon only for confirmed funded cargo."})
    if state.maintenance is not None:
        from npc_sim import village_maintenance
        for job in village_maintenance.observation(world)["maintenance"]["jobs"]:
            if (job["assignee"] == world.actor_id and job["location"] == world.actor.location_id
                    and job["status"] in {"pending", "overdue"} and not job["blocker"]
                    and state.minute + 15 <= next_review(world)):
                args = {"job": job["id"]}
                if action_blocker(world, "village_maintain", args, check_wait=False) is None:
                    choices.append({"task_id": f"maintenance:{job['id']}", "action_id": "village_maintain",
                                    "arguments": args, "priority": "P2",
                                    "reason": "Assigned local maintenance is ready before the next obligation."})
    if state.equipment is not None:
        from npc_sim import village_equipment
        for action in village_equipment.actions(world):
            for identifier in action["arguments_schema"]["properties"]["asset"]["enum"]:
                definition = state.equipment.config.assets[identifier]
                if definition.location != world.actor.location_id:
                    continue
                current = state.equipment.assets[identifier].tier
                if current + 1 >= len(definition.tiers):
                    continue
                payers = action["arguments_schema"]["properties"]["payer"]["enum"]
                payer = definition.owner if definition.owner in payers else payers[0]
                args = {"asset": identifier, "payer": payer}
                if action_blocker(world, action["id"], args, check_wait=False) is None:
                    choices.append({"task_id": f"equipment:{identifier}",
                                    "action_id": action["id"], "arguments": args,
                                    "priority": "P4",
                                    "reason": "A funded local equipment upgrade improves real future capacity."})
    for action in village_ai.actions(world):
        action_id = action["id"]
        if action_id not in village_life.PRODUCTION:
            continue
        blocked = village_life.action_blocker(world, action_id, personal=True)
        if blocked is None and village_ai.production_ready(world, action_id) is None:
            if action_blocker(world, action_id, {}, check_wait=False) is None:
                economy_policy = village_economy.config(world)
                cooking = (economy_policy is not None and world.actor_id == economy_policy.innkeeper
                           and action_id in {"village_work", "village_cook_fish"}
                           and any(item["cookable_portions"] and item["planned_portions"] > item["prepared_portions"]
                                   for item in village_economy.meal_readiness(world)))
                choices.append({"task_id": f"occupation:{action_id}", "action_id": action_id, "arguments": {},
                                "priority": "P1" if cooking else "P3",
                                "reason": ("Cook real portions for the next service while inputs, fuel and time permit."
                                           if cooking else "Your real occupation work is currently eligible.")})
    if purposeful(world):
        choices.extend(_purposeful_choices(world))
        choices.sort(key=lambda choice: choice["priority"])
    policy = village_extensions.policy(world)
    breakfast_due = (state.minute // 1440 * 1440 + resident.wake_minute + policy.breakfast_delay)
    breakfast = life.meals.get(f"{state.minute // 1440}:breakfast")
    awaiting_breakfast = (policy.breakfast and world.actor.location_id == "tavern"
                          and state.minute < breakfast_due + policy.meal_window
                          and (breakfast is None or breakfast.status == "pending"))
    if (not choices and world.actor.location_id != resident.work and not awaiting_breakfast
            and not village_life.rest_due(resident, life, state.minute)
            and state.minute % 1440 < village_life.production_closing_minute(world, world.actor_id)):
        from npc_sim import village_walking
        if village_walking.enabled(world):
            plan = person.walking_plan
            if plan and plan.destination == resident.work and plan.next_step < len(plan.doors):
                if village_walking.check_step(world, {"target": plan.doors[plan.next_step]}) is None:
                    choices.append({"task_id": "return:workplace", "action_id": "go",
                                    "arguments": {"target": plan.doors[plan.next_step]}, "priority": "P4",
                                    "reason": "Return to your main workplace rather than wait elsewhere."})
            elif village_walking.check_plan(world, resident.work) is None:
                path = village_walking._path(world, resident.work)
                limits = _obligation_limits(world)
                if path and (not limits or state.minute + 5 * len(path[0]) + 10 <= min(limits)):
                    choices.append({"task_id": "return:workplace", "action_id": "village_plan_walk",
                                    "arguments": {"destination": resident.work, "purpose": "Return to my workplace while idle"},
                                    "priority": "P4", "reason": "Return to your main workplace rather than wait elsewhere."})
        else:
            route = _travel_choice(world, resident.work, "return:workplace",
                                   "Return to your main workplace rather than wait elsewhere.", "P4")
            if route:
                choices.append(route)
    if not choices and _inspection_ready(world):
        choices.append({"task_id": f"fallback:{world.actor_id}:inspect:{resident.work}",
                        "action_id": "village_observe", "arguments": {}, "priority": "P5",
                        "duration_minutes": 5, "reason": "Inspect your actual workplace and local tools once."})
    return choices


def action_blocker(world: "World", action: str, args: dict[str, Any], *,
                   check_wait: bool = True) -> tuple[str, str] | None:
    if not enabled(world):
        return None
    _, state = village.context(world)
    person = state.residents[world.actor_id]
    cached = person.fallback.blocked.get(_key(action, args))
    if cached and cached.context_revision == _retry_revision(world, action):
        return "RETRY_SUPPRESSED", f"{cached.reason} No relevant personal condition changed; choose an eligible alternative or village_wait_event."
    if action in village_life.PRODUCTION or action == "village_observe":
        job = village_ai.production_job(world, action) if action in village_life.PRODUCTION else None
        duration = job.duration if job and not job.interruptible else 5
        limits = _obligation_limits(world)
        if limits and state.minute + duration > min(limits):
            return "COMMITMENT_DUE", "Leave time for the actual walk and meal/sleep action before your next obligation."
    if purposeful(world) and action == "village_check_messages":
        communication = state.communication
        fixture = (communication.config.board_id if args.get("channel", "board") == "board" else
                   next((box for box, owner in communication.boxes.items() if owner == world.actor_id), "")) if communication else ""
        if fixture and not _unread_paper(world, fixture):
            return "PAPER_UNCHANGED", "No unread accessible paper here; do not poll unchanged messages."
    if action == "village_observe":
        previous = person.fallback.inspections.get(world.actor.location_id)
        if previous and previous[1] == _inspection_revision(world) and (
                state.minute < previous[0] + village_extensions.policy(world).idle_review_minutes):
            return "INSPECTION_UNCHANGED", "Already inspected here; wait for a real condition change or the next safety review."
    if check_wait and action in {"village_wait", "village_wait_event", "wait_until_next_relevant_event"} and not (
            person.life and (person.life.sleeping or person.life.work)):
        config, _ = village.context(world)
        resident = config.residents[world.actor_id]
        if person.life and village_life.rest_due(resident, person.life, state.minute) and world.actor.location_id != resident.home:
            return "REST_DUE", "Travel to your actual home and sleep before planning idle time."
        if person.life and village_life.meal_due(person.life, state.minute) and world.actor.location_id != "tavern":
            return "MEAL_DUE", "The due meal has not been checked; travel to the tavern before planning idle time."
        if next_review(world) <= state.minute:
            return "COMMITMENT_DUE", "Your scheduled meal or bedtime needs travel or action now; do not start an idle wait."
        if (action == "village_wait" and args.get("minutes") == 5 and person.life
                and person.life.urgent_project and person.life.urgent_until is not None
                and state.minute < person.life.urgent_until <= state.minute + 5
                and world.actor.location_id == (config.well_site if person.life.urgent_project == "dig_well"
                                                else config.mill if person.life.urgent_project == "repair_mill"
                                                else None)):
            return None
        choices = candidates(world)
        required = next((choice for choice in choices if choice["priority"] != "P5"), None)
        if choices and (action != "wait_until_next_relevant_event" or required):
            choice = required if action == "wait_until_next_relevant_event" else choices[0]
            return "ELIGIBLE_TASK_PENDING", f"Before idle waiting, consider {choice['action_id']}: {choice['reason']}"
    return None


def start_wait(world: "World") -> int:
    _, state = village.context(world)
    fallback = state.residents[world.actor_id].fallback
    duration = 10 if fallback.last_wait_end == state.minute else 5
    until = min(next_review(world), state.minute + duration)
    fallback.last_wait_end = until
    fallback.wait = VillageIdleWait(
        start=state.minute, planned_end=until, wake_on=WAKE_ON.copy(),
        next_safety_review=until, context_revision=context_revision(world))
    fallback.decision = {
        "actor_id": world.actor_id, "world_time": state.minute, "eligible_fallbacks": [],
        "blocked_primary_task_id": next(reversed(fallback.blocked), None),
        "selected_task_id": "wait:event", "reason": ("No feasible personally grounded work, paper check, or safe route before the next obligation."
                                                   if purposeful(world) else "No eligible local work or useful inspection."),
        "next_review": {"type": "event_or_time", "events": WAKE_ON, "latest_time": until},
    }
    _emit(world, "WAIT_STARTED", "wait:event", fallback.decision["reason"],
          reason_code="NO_ELIGIBLE_WORK", next_wake_trigger=fallback.wait.model_dump())
    return until


def record_result(world: "World", action: str, args: dict[str, Any],
                  outcome: str, code: str, explanation: str) -> bool:
    if outcome == "APPLIED" and world.state.village.communication is not None:
        update_supply_needs(world)
    if not enabled(world):
        return False
    _, state = village.context(world)
    person = state.residents[world.actor_id]
    fallback = person.fallback
    key = _key(action, args)
    if code == "RETRY_SUPPRESSED":
        cached = fallback.blocked.get(key)
        if cached:
            cached.attempt_count_since_change += 1
        return True
    if (outcome == "REJECTED" and code not in {
            "ACTOR_BUSY", "ASLEEP", "INSPECTION_UNCHANGED", "ELIGIBLE_TASK_PENDING", "EVENT_WAIT_REQUIRED",
            "ROUTE_GOAL_DEFERRED"}
            or action == "village_eat" and code == "MEAL_MISSED"):
        reason_code = REASONS.get(code, "OTHER")
        if action == "village_eat" and code == "MEAL_MISSED" and not _meal_funded(world):
            reason_code = "PAYMENT_INSUFFICIENT"
        fallback.blocked[key] = VillageBlockedAction(
            action_id=action, arguments=args.copy(), blocked_at=state.minute,
            reason_code=reason_code, engine_code=code, reason=explanation,
            context_revision=_retry_revision(world, action), recheck_on=WAKE_ON.copy())
        if len(fallback.blocked) > 32:
            del fallback.blocked[next(iter(fallback.blocked))]
        _emit(world, "TASK_BLOCKED", key, explanation,
              **fallback.blocked[key].model_dump(exclude={"reason"}))
        return True
    if outcome != "APPLIED":
        return False
    fallback.blocked.pop(key, None)
    if code != "EVENT_WAIT_STARTED":
        fallback.wait = None
        fallback.last_wait_end = None
    if purposeful(world):
        if action in {"village_check_messages", "village_notice_read"} and args.get("channel", "board") == "board":
            fallback.board_last_checked = state.minute
        config, _ = village.context(world)
        if (world.actor_id == config.miller and action == "say"
                and "mill drive fail" in args.get("message", "")
                and village.resident_location(world, config.carpenter) == world.actor.location_id):
            fallback.inspections["mill-relay"] = (state.minute, "relayed")
    if action == "village_observe":
        location = world.actor.location_id
        fallback.inspections[location] = (state.minute, _inspection_revision(world))
        fallback.active_inspection_until = person.available_at
        fallback.active_inspection_location = location
        task = f"fallback:{world.actor_id}:inspect:{location}"
        fallback.decision = {
            "actor_id": world.actor_id, "world_time": state.minute,
            "blocked_primary_task_id": next(reversed(fallback.blocked), None),
            "eligible_fallbacks": [task], "selected_task_id": task,
            "reason": "Chose a real local inspection; no goods or tool improvements are created.",
            "next_review": {"type": "event_or_time", "events": WAKE_ON, "latest_time": next_review(world)},
        }
        _emit(world, "FALLBACK_SELECTED", task, fallback.decision["reason"], decision=fallback.decision)
        _emit(world, "FALLBACK_STARTED", task, "Checking actual local tools.", action_result=code,
              completes_at=person.available_at)
    return True


def advance(world: "World") -> None:
    if not enabled(world):
        return
    _, state = village.context(world)
    for rid, person in state.residents.items():
        if rid not in world.state.actors:
            continue
        bound = world.bind(rid, world.state.actors[rid].session_id)
        fallback = person.fallback
        if fallback.active_inspection_until is not None and state.minute >= fallback.active_inspection_until:
            location = fallback.active_inspection_location
            if bound.actor.location_id == location:
                _emit(bound, "FALLBACK_COMPLETED", f"fallback:{rid}:inspect:{location}",
                      "Completed the five-minute inspection; only actual observations were learned.")
            fallback.active_inspection_until = None
            fallback.active_inspection_location = None
        wait = fallback.wait
        if wait is None or person.life and (person.life.sleeping or person.life.work):
            continue
        changed = wait.context_revision != context_revision(bound)
        due = state.minute >= wait.next_safety_review
        if changed or due:
            fallback.wait = None
            if changed:
                fallback.last_wait_end = None
            person.available_at = min(person.available_at, state.minute)
            _emit(bound, "WAIT_INTERRUPTED" if changed else "TASK_REEVALUATED", "wait:event",
                  "Personally observable conditions changed." if changed else "Bounded safety review is due.",
                  reason_code="PERSONAL_CONTEXT_CHANGED" if changed else "SAFETY_REVIEW")


def observation(world: "World") -> dict[str, Any]:
    if not enabled(world):
        _, state = village.context(world)
        return {"fallback": {"eligible_candidates": candidates(world)}} if state.legacy is not None else {}
    update_supply_needs(world)
    _, state = village.context(world)
    fallback = state.residents[world.actor_id].fallback
    revision = context_revision(world)
    feed_plan = None
    from npc_sim import village_depot
    if village_depot.feed_store_enabled(world) and world.actor_id == world.scenario.village.livestock.keeper:
        ration = _feed_ration(world)
        if world.actor.location_id == village_depot.FEED_STORE:
            fallback.feed_store_stock = state.stocks[village_depot.FEED_STORE].copy()
            fallback.feed_store_checked_day = state.minute // 1440
        if fallback.feed_store_checked_day != state.minute // 1440:
            feed_plan = {"status": "check_store_in_person", "daily_ration": ration}
        else:
            missing = {product: max(0, quantity - fallback.feed_store_stock.get(product, 0))
                       for product, quantity in ration.items()}
            feed_plan = {"status": "personally_checked", "daily_ration": ration,
                         "last_known_store_stock": fallback.feed_store_stock.copy(), "missing": missing}
            if world.actor.location_id == world.scenario.village.residents[world.actor_id].work:
                available = {product: max(0, village_economy.usable_stock(world, world.actor.location_id,
                                                                            product, state.minute)
                                           - village_economy.protected_quantity(world, world.actor.location_id,
                                                                                product))
                             for product in missing}
                feed_plan["farm_unreserved"] = available
                feed_plan["shortage"] = {product: quantity - available[product]
                                         for product, quantity in missing.items() if quantity > available[product]}
                if any(missing.values()):
                    feed_plan["trip_blocker"] = _feed_trip_blocker(
                        world, world.actor.location_id, village_depot.FEED_STORE)
    return {"fallback": {
        "context_revision": revision,
        "blocked_tasks": [{**item.model_dump(), "retry_suppressed": item.context_revision == _retry_revision(world, item.action_id)}
                          for item in fallback.blocked.values()],
        "supply_needs": [need.model_dump() for need in fallback.supply_needs.values()],
        "current_tasks": current_tasks(world),
        "communication_blocker": (state.communication.recent_blockers.get(world.actor_id)
                                  if purposeful(world) and state.communication else None),
        "recent_locations": [step.model_dump() for step in fallback.recent_locations],
        "route_goal": fallback.route_goal.model_dump() if fallback.route_goal else None,
        "repeated_supplier_searches": repeated_supplier_searches(world),
        "eligible_candidates": candidates(world),
        **({"feed_store_plan": feed_plan} if feed_plan is not None else {}),
        "information_check": ({"last_board_visit": fallback.board_last_checked,
                               "next_board_visit": (fallback.board_last_checked + village_extensions.policy(world).board_check_minutes
                                                    if fallback.board_last_checked >= 0 else state.minute),
                               "last_mill_check": fallback.mill_last_checked}
                              if purposeful(world) else None),
        "next_known_event": next_review(world),
        "wait": fallback.wait.model_dump() if fallback.wait else None,
        "decision": fallback.decision,
        "policy": "Choose among legal alternatives; unchanged failed actions are suppressed. "
                  "A repeated supplier search is detected from your own visits for the same unresolved need. "
                  "Do not repeat that search without new personally known evidence, a credible meeting, "
                  "or a due deadline; ordinary deliveries, chores, patrols and meetings remain available. "
                  "Inspect only actual local tools. No invented production, maintenance or movement. "
                  "Use village_wait_event for inert periods; personal changes or bounded safety reviews wake you.",
    }}


def annotate(world: "World", actions: list[dict[str, Any]]) -> None:
    if not enabled(world):
        return
    for action in actions:
        if action["arguments_schema"].get("required"):
            continue
        blocked = action_blocker(world, action["id"], {})
        if blocked:
            action["availability"], action["reason"] = "blocked", blocked[1]

import hashlib
import json
from typing import TYPE_CHECKING, Any

from npc_sim import village, village_ai, village_extensions, village_life, village_economy
from npc_sim.models import (VillageBlockedAction, VillageEvent, VillageIdleWait,
                            VillageLocationTransition, VillageRouteGoal, VillageSupplierSearch, VillageSupplyNeed)

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
        "location": location, "inventory": world.actor.inventory, "body": world.actor.body.model_dump(),
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
        meal = village_life.meal_due(life, state.minute, include_missed=True) if life else None
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
    fallback = state.residents[world.actor_id].fallback
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
    obligations = [(day + 1140, "tavern"), (day + resident.bed_minute, resident.home)]
    if policy.breakfast:
        due = day + resident.wake_minute + policy.breakfast_delay
        obligations.append((due, "tavern"))
    limits = []
    for due, destination in obligations:
        if due > state.minute:
            path = village.route(world, world.actor.location_id, destination)
            if path is not None:
                limits.append(due if not path else due - 5 * len(path) - 5)
    return limits


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
    _, state = village.context(world)
    if world.actor.location_id == destination:
        return None
    known = state.residents[world.actor_id].known_routes
    path = village.route(world, world.actor.location_id, destination,
                         known_routes=known if known else None)
    if not path:
        return None
    limits = _obligation_limits(world)
    if limits and state.minute + 5 * len(path) + 10 > min(limits):
        return None
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


def _purposeful_choices(world: "World") -> list[dict[str, Any]]:
    config, state = village.context(world)
    person = state.residents[world.actor_id]
    fallback = person.fallback
    location = world.actor.location_id
    choices: list[dict[str, Any]] = []
    if not (config.residents[world.actor_id].wake_minute <= state.minute % 1440
            < village_life.production_closing_minute(world, world.actor_id)):
        return choices

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

    if state.social is not None and not choices:
        from npc_sim import village_social
        social = village_social.observation(world).get("social", {})
        activities = social.get("activities", {})
        chosen = next(((key, activity) for key, activity in activities.items()
                       if activity["available"] and activity["preferred"]
                       and activity["location"] == location
                       and (not _obligation_limits(world) or
                            state.minute + activity["duration"] <= min(_obligation_limits(world)))
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


def candidates(world: "World") -> list[dict[str, Any]]:
    config, state = village.context(world)
    resident, person = config.residents[world.actor_id], state.residents[world.actor_id]
    life = person.life
    if not enabled(world) or life is None or life.sleeping or life.work or person.available_at > state.minute:
        return []
    choices: list[dict[str, Any]] = []
    if village_life.rest_due(resident, life, state.minute):
        if world.actor.location_id == resident.home:
            choices.append({"task_id": "need:sleep", "action_id": "village_sleep", "arguments": {},
                            "priority": "P1", "reason": "Required sleep at your actual home."})
        return choices
    meal = village_life.meal_due(life, state.minute, include_missed=True)
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
    for action in village_ai.actions(world):
        action_id = action["id"]
        if action_id not in village_life.PRODUCTION:
            continue
        blocked = village_life.action_blocker(world, action_id, personal=True)
        if blocked is None and village_ai.production_ready(world, action_id) is None:
            if action_blocker(world, action_id, {}, check_wait=False) is None:
                choices.append({"task_id": f"occupation:{action_id}", "action_id": action_id, "arguments": {},
                                "priority": "P3", "reason": "Your real occupation work is currently eligible."})
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
        choices = candidates(world)
        required = next((choice for choice in choices if choice["priority"] != "P5"), None)
        if choices and (action != "wait_until_next_relevant_event" or required):
            choice = required if action == "wait_until_next_relevant_event" else choices[0]
            return "ELIGIBLE_TASK_PENDING", f"Before idle waiting, consider {choice['action_id']}: {choice['reason']}"
    return None


def start_wait(world: "World") -> int:
    _, state = village.context(world)
    until = next_review(world)
    fallback = state.residents[world.actor_id].fallback
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
            person.available_at = min(person.available_at, state.minute)
            _emit(bound, "WAIT_INTERRUPTED" if changed else "TASK_REEVALUATED", "wait:event",
                  "Personally observable conditions changed." if changed else "Bounded safety review is due.",
                  reason_code="PERSONAL_CONTEXT_CHANGED" if changed else "SAFETY_REVIEW")


def observation(world: "World") -> dict[str, Any]:
    if not enabled(world):
        return {}
    update_supply_needs(world)
    _, state = village.context(world)
    fallback = state.residents[world.actor_id].fallback
    revision = context_revision(world)
    return {"fallback": {
        "context_revision": revision,
        "blocked_tasks": [{**item.model_dump(), "retry_suppressed": item.context_revision == _retry_revision(world, item.action_id)}
                          for item in fallback.blocked.values()],
        "supply_needs": [need.model_dump() for need in fallback.supply_needs.values()],
        "communication_blocker": (state.communication.recent_blockers.get(world.actor_id)
                                  if purposeful(world) and state.communication else None),
        "recent_locations": [step.model_dump() for step in fallback.recent_locations],
        "route_goal": fallback.route_goal.model_dump() if fallback.route_goal else None,
        "repeated_supplier_searches": repeated_supplier_searches(world),
        "eligible_candidates": candidates(world),
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

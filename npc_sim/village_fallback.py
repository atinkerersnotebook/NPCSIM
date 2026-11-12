import hashlib
import json
from typing import TYPE_CHECKING, Any

from npc_sim import village, village_ai, village_extensions, village_life, village_economy
from npc_sim.models import (VillageBlockedAction, VillageEvent, VillageIdleWait,
                            VillageLocationTransition, VillageSupplierSearch, VillageSupplyNeed)

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
        "supply_needs": {key: need.model_dump() for key, need in person.fallback.supply_needs.items()},
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
            "anti_idle", "idle_review_minutes", "breakfast", "breakfast_delay", "meal_window"}),
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


def record_location_transition(world: "World", origin: str, destination: str) -> None:
    config, state = village.context(world)
    fallback = state.residents[world.actor_id].fallback
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
    times = [state.minute + policy.idle_review_minutes, village_extensions.next_personal_event(world)]
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
    for action in village_ai.actions(world):
        action_id = action["id"]
        if action_id not in village_life.PRODUCTION:
            continue
        blocked = village_life.action_blocker(world, action_id, personal=True)
        if blocked is None and village_ai.production_ready(world, action_id) is None:
            if action_blocker(world, action_id, {}, check_wait=False) is None:
                choices.append({"task_id": f"occupation:{action_id}", "action_id": action_id, "arguments": {},
                                "priority": "P3", "reason": "Your real occupation work is currently eligible."})
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
    if action == "village_observe":
        previous = person.fallback.inspections.get(world.actor.location_id)
        if previous and previous[1] == _inspection_revision(world) and (
                state.minute < previous[0] + village_extensions.policy(world).idle_review_minutes):
            return "INSPECTION_UNCHANGED", "Already inspected here; wait for a real condition change or the next safety review."
    if check_wait and action in {"village_wait", "village_wait_event"} and not (
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
        if choices:
            return "ELIGIBLE_TASK_PENDING", f"Before idle waiting, consider {choices[0]['action_id']}: {choices[0]['reason']}"
        if action == "village_wait":
            return "EVENT_WAIT_REQUIRED", "Use village_wait_event with a recorded wake trigger instead of repeated short idle waits."
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
        "selected_task_id": "wait:event", "reason": "No eligible local work or useful inspection.",
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
            "ACTOR_BUSY", "ASLEEP", "INSPECTION_UNCHANGED", "ELIGIBLE_TASK_PENDING", "EVENT_WAIT_REQUIRED"}
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
    if action != "village_wait_event":
        fallback.wait = None
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
        "recent_locations": [step.model_dump() for step in fallback.recent_locations],
        "repeated_supplier_searches": repeated_supplier_searches(world),
        "eligible_candidates": candidates(world),
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

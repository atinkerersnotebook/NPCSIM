from typing import TYPE_CHECKING, Any

from npc_sim import village, village_economy, village_events, village_social, village_maintenance, village_research, village_mining, village_livestock
from npc_sim.models import VillageConfigurationPatch, VillageDashboard, VillageDayPolicy, VillageTimeUse
from npc_sim import village_communication, village_firewood

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = village_economy.VERBS | village_events.VERBS | village_social.VERBS | village_maintenance.VERBS | village_research.VERBS | village_mining.VERBS | village_livestock.VERBS | village_communication.VERBS | {"village_wait_event"}


def policy(world: "World") -> VillageDayPolicy:
    config, state = village.context(world)
    return state.day_policy or config


def modules(world: "World") -> list[Any]:
    _, state = village.context(world)
    return [module for module, enabled in (
        (village_economy, state.economy),
        (village_events, state.incidents),
        (village_social, state.social),
        (village_maintenance, state.maintenance),
        (village_research, state.research),
        (village_mining, state.mining),
        (village_livestock, state.livestock),
        (village_communication, state.communication),
        (village_firewood, state.firewood),
    ) if enabled is not None]


def observation(world: "World") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for module in modules(world):
        result.update(module.observation(world))
    result["day_policy"] = policy(world).model_dump(include=set(VillageDayPolicy.model_fields))
    from npc_sim import village_fallback
    result.update(village_fallback.observation(world))
    return result


def actions(world: "World") -> list[dict[str, Any]]:
    result = [action for module in modules(world) for action in module.actions(world)]
    if policy(world).event_driven_waits or policy(world).anti_idle:
        result.append({
            "id": "village_wait_event", "description": "Wait until your next work, meal or bedtime; new commitments can wake you.",
            "targets": [], "arguments_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "availability": "available", "reason": None,
        })
    return result


def next_personal_event(world: "World") -> int:
    config, state = village.context(world)
    person = state.residents[world.actor_id]
    resident = config.residents[world.actor_id]
    day = state.minute // 1440 * 1440
    times = [day + resident.bed_minute, day + 1140]
    if policy(world).breakfast:
        times.append(day + resident.wake_minute + policy(world).breakfast_delay)
    if person.next_work > state.minute:
        times.append(person.next_work)
    if person.life and person.life.sleep_until:
        times.append(person.life.sleep_until)
    if state.dashboard:
        times.extend(job.release_at for job in state.dashboard.maintenance
                     if job.assignee == world.actor_id and job.status == "upcoming")
        times.extend(order.deadline for order in village_economy._personal_orders(world)
                     if order.buyer == world.actor_id and order.status in {"proposed", "accepted"})
        times.extend(incident.end for incident in state.dashboard.incidents
                     if incident.location == world.actor.location_id
                     and incident.status not in {"resolved", "expired", "cancelled"})
    if state.communication:
        times.extend(memory.snapshot.appointment.due_at
                     for memory in state.communication.knowledge.get(world.actor_id, {}).values()
                     if memory.snapshot.appointment and memory.snapshot.appointment.status == "confirmed")
    return min((time for time in times if time > state.minute), default=day + 1440 + resident.wake_minute)


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    if action == "village_wait_event":
        _, state = village.context(world)
        from npc_sim import village_fallback
        until = village_fallback.start_wait(world) if village_fallback.enabled(world) else next_personal_event(world)
        state.residents[world.actor_id].available_at = until
        text = f"Waiting for the next scheduled event at {village.clock_time(until)}."
        state.residents[world.actor_id].activity = text
        village.event(world, world.actor_id, action, world.actor.location_id, text)
        return "APPLIED", "EVENT_WAIT_STARTED", text, None
    for module in modules(world):
        if action in module.VERBS:
            return module.apply(world, action, args)
    raise ValueError(f"Village extension action is not enabled: {action}")


def advance(world: "World") -> None:
    for module in modules(world):
        module.advance(world)
    wake_local_waiters(world)
    from npc_sim import village_fallback
    village_fallback.advance(world)
    refresh(world)


def wake_local_waiters(world: "World") -> None:
    _, state = village.context(world)
    for event in reversed(state.events):
        if event.minute < state.minute:
            break
        if event.action in {"look", "read", "village_wait", "village_wait_event", "learn"}:
            continue
        for rid in event.witnesses:
            person = state.residents.get(rid)
            if person is not None and policy(world).anti_idle and person.fallback.wait is not None:
                continue
            if (person is not None and person.current_action == "village_wait_event"
                    and rid != event.actor and not (person.life and (person.life.sleeping or person.life.work))
                    and not person.timber_route and not (state.wagon and state.wagon.driver == rid)):
                person.available_at = min(person.available_at, state.minute + 5)


def refresh(world: "World") -> None:
    _, state = village.context(world)
    enabled = modules(world)
    if enabled:
        values = {}
        for module in enabled:
            monitored = module.monitor(world)
            activities = values.get("activities", []) + monitored.pop("activities", [])
            values.update(monitored)
            values["activities"] = activities
        values["time_use"] = {rid: usage.model_dump() for rid, usage in state.time_use.items()}
        values["daily_time_use"] = {day: {rid: usage.model_dump() for rid, usage in residents.items()}
                                    for day, residents in state.daily_time_use.items()}
        values["day_policy"] = policy(world).model_dump(include=set(VillageDayPolicy.model_fields))
        state.dashboard = VillageDashboard.model_validate(values)


def account_time(world: "World", minutes: int) -> None:
    from npc_sim import village_life
    _, state = village.context(world)
    if not modules(world):
        return
    for rid, person in state.residents.items():
        category = None
        if person.life and person.life.sleeping:
            category = "sleep"
        elif person.life and person.life.work:
            category = "work"
        elif state.maintenance is not None and village_maintenance.blocked_until(world, rid) is not None:
            category = "work"
        elif state.research is not None and village_research.blocked_until(world, rid) is not None:
            category = "work"
        elif state.mining is not None and village_mining.blocked_until(world, rid) is not None:
            category = "work"
        elif state.livestock is not None and village_livestock.blocked_until(world, rid) is not None:
            category = "work"
        elif state.wagon and state.wagon.automatic and state.wagon.driver == rid:
            if state.wagon.phase == "travel":
                category = "travel"
            else:
                category = "work"
        elif person.timber_route:
            category = "travel"
        elif blocked_until(world, rid) is not None:
            category = "leisure"
        elif person.available_at > state.minute:
            if person.current_action == "go":
                category = "travel"
            elif person.current_action in {"village_wait", "village_wait_event"}:
                category = "wait"
            elif person.current_action in village_life.WORK_ACTIONS:
                category = "work"
            else:
                category = "leisure"
        else:
            category = "idle"

        cumulative = state.time_use.setdefault(rid, VillageTimeUse(since=state.minute))
        daily = state.daily_time_use.setdefault(state.minute // 1440, {}).setdefault(
            rid, VillageTimeUse(since=state.minute // 1440 * 1440))
        for usage in (cumulative, daily):
            setattr(usage, category, getattr(usage, category) + minutes)
            if category == "work":
                usage.useful_minutes += minutes
            if category == "travel" and person.current_action == "go":
                steps = person.fallback.recent_locations
                if (len(steps) >= 2 and steps[-1].origin == steps[-2].destination
                        and steps[-1].destination == steps[-2].origin
                        and cumulative.last_useful_minute < steps[-2].minute):
                    usage.wasted_travel += minutes


def blocked_until(world: "World", rid: str) -> int | None:
    _, state = village.context(world)
    times = [
        village_social.blocked_until(world, rid) if state.social is not None else None,
        village_maintenance.blocked_until(world, rid) if state.maintenance is not None else None,
        village_research.blocked_until(world, rid) if state.research is not None else None,
        village_mining.blocked_until(world, rid) if state.mining is not None else None,
        village_livestock.blocked_until(world, rid) if state.livestock is not None else None,
    ]
    return min((time for time in times if time is not None), default=None)


def record_action(world: "World", action: str, args: dict[str, Any]) -> None:
    _, state = village.context(world)
    if action not in {"look", "read"}:
        state.residents[world.actor_id].current_action = action
    if modules(world):
        usage = state.time_use.setdefault(world.actor_id, VillageTimeUse(since=state.minute))
        usage.successful_actions += 1
        daily = state.daily_time_use.setdefault(state.minute // 1440, {}).setdefault(
            world.actor_id, VillageTimeUse(since=state.minute // 1440 * 1440))
        daily.successful_actions += 1
        if action not in {"look", "read", "say", "go", "village_observe", "village_wait",
                          "village_wait_event", "village_sleep", "village_eat", "village_drink_tea",
                          "village_check_messages", "village_notice_read", "village_inbox_read"}:
            usage.useful_actions += 1
            daily.useful_actions += 1
            usage.last_useful_minute = state.minute
            daily.last_useful_minute = state.minute
    if state.social is not None:
        village_social.record_action(world, action, args, "APPLIED")
    if state.research is not None:
        village_research.record_action(world, action, args)
    wake_local_waiters(world)
    from npc_sim import village_fallback
    if action == "village_wait_event" and village_fallback.enabled(world):
        wait = state.residents[world.actor_id].fallback.wait
        if wait is not None:
            wait.context_revision = village_fallback.context_revision(world)
    village_fallback.advance(world)
    refresh(world)


def configure(world: "World", request_id: str, revision: str, patch: dict[str, Any]) -> dict[str, Any]:
    with world._lock:
        _, state = village.context(world)
        request = {"revision": revision, "patch": patch}
        previous = state.configuration_requests.get(request_id)
        if previous:
            if previous["request"] != request:
                raise ValueError("Configuration request ID was reused with different arguments")
            return previous["result"]
        if world.revision() != revision:
            raise ValueError("The world changed; obtain a fresh revision before configuring it")
        parsed = VillageConfigurationPatch.model_validate(patch)
        if not parsed.model_fields_set or all(getattr(parsed, field) is None for field in parsed.model_fields_set):
            raise ValueError("Specify at least one non-null village configuration")
        before = world.state.model_copy(deep=True)
        try:
            for field, module in (("economy", village_economy), ("social", village_social),
                                  ("events", village_events), ("maintenance", village_maintenance),
                                  ("research", village_research), ("mining", village_mining), ("livestock", village_livestock),
                                  ("communication", village_communication), ("firewood", village_firewood)):
                value = getattr(parsed, field)
                if value is not None:
                    module.configure(world, value.model_dump(mode="json"))
            if parsed.day_policy is not None:
                if parsed.day_policy.anti_idle and not (
                        world.scenario.village.ai_controlled and world.scenario.village.time_based_turns
                        and world.scenario.village.daily_life):
                    raise ValueError("Anti-idle planning requires an AI-controlled timed daily-life village")
                if parsed.day_policy.demand_led_completion and state.economy is None:
                    raise ValueError("Demand-led completion requires an enabled economy")
                state.day_policy = parsed.day_policy
            refresh(world)
            village.event(world, "dungeon-master", "village_configured", "village",
                          f"Configured village systems: {', '.join(sorted(parsed.model_fields_set))}.",
                          location="operator-console" if parsed.model_fields_set == {"livestock"} else None)
            world.state.revision += 1
            result = {"revision": world.revision(), "configured": sorted(parsed.model_fields_set)}
            state.configuration_requests[request_id] = {"request": request, "result": result}
            world.save()
            return result
        except (OSError, ValueError):
            world.state = before
            raise

"""Opt-in inherited stories and a physical, persistent village chronicle."""

from typing import TYPE_CHECKING, Any

from npc_sim.models import Scenario, WorldState, VillageLegacyEntry, VillageLegacyLesson, VillageLegacyState
from npc_sim import village

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"village_read_chronicle", "village_share_chronicle"}
RECORDED = {
    "breakdown", "project_completed", "deadline_completed", "deadline_overdue",
    "missed_meal", "visitor_arrived", "visitor_departed", "village_merchant_trade",
    "trader_arrived", "batch_completed", "research_completed", "trade",
}
LESSONS = {"missed_meal": "shortage", "deadline_overdue": "delivery",
           "project_completed": "repair", "deadline_completed": "repair"}
LESSON_DAYS = 3


def validate_config(scenario: Any) -> None:
    config = scenario.village
    legacy = config.legacy
    if legacy is None:
        return
    if not config.ai_controlled or not config.daily_life or not config.time_based_turns:
        raise ValueError("Village legacy requires timed AI daily life")
    if set(legacy.families) != set(config.residents):
        raise ValueError("Every resident needs an inherited family history")
    for rid, family in legacy.families.items():
        if family.inherited_skill not in config.residents[rid].skills:
            raise ValueError(f"{rid} must already possess the inherited skill")
        if family.successor is not None and family.successor.strip() != family.successor:
            raise ValueError(f"{rid} successor name must not have surrounding whitespace")
    successors = [family.successor for family in legacy.families.values() if family.successor]
    if len(set(successors)) != len(successors):
        raise ValueError("Successor names must be unique")
    if set(successors) & {person.name for person in legacy.arrivals}:
        raise ValueError("Successors must not be non-playable newcomers")
    if len({person.name for person in legacy.arrivals}) != len(legacy.arrivals):
        raise ValueError("Newcomer names must be unique")
    for person in legacy.arrivals:
        if person.home not in scenario.entities or scenario.entities[person.home].kind != "location":
            raise ValueError("Newcomer home must be an existing location")


def initial_state(scenario: Any) -> VillageLegacyState | None:
    config = scenario.village
    if config.legacy is None:
        return None
    validate_config(scenario)
    return VillageLegacyState(
        chronicle=[VillageLegacyEntry(minute=0, kind="founding", text=text)
                   for text in config.legacy.history],
        last_day=config.start_minute // 1440,
        current={rid: rid.replace("-", " ").title() for rid in config.residents},
    )


def advance(world: "World") -> None:
    config, state = village.context(world)
    legacy = state.legacy
    if legacy is None or config.legacy is None:
        return
    for index, entry in enumerate(state.events[legacy.recorded_events:], legacy.recorded_events):
        if entry.action in RECORDED and (entry.action != "batch_completed" or entry.target == config.mill):
            legacy.chronicle.append(VillageLegacyEntry(
                minute=entry.minute, kind=entry.action,
                text=f"{village.resident_name(world, entry.actor)}: {entry.result}",
                source_event=index, lesson=LESSONS.get(entry.action),
                origin_world_id=world.state.world_id, origin_minute=entry.minute))
            if entry.action in {"project_completed", "missed_meal"} and entry.actor in state.residents:
                learn(world, entry.actor, len(legacy.chronicle) - 1, "firsthand experience")
    legacy.recorded_events = len(state.events)
    day = state.minute // 1440
    for next_day in range(legacy.last_day + 1, day + 1):
        for arrival in config.legacy.arrivals:
            if arrival.day == next_day + 1:
                legacy.newcomers[arrival.name] = arrival.model_copy(deep=True)
                text = f"{arrival.name} arrived at {arrival.home}: {arrival.reason}"
                legacy.chronicle.append(VillageLegacyEntry(minute=next_day * 1440, kind="arrival", text=text))
                village.event(world, "village", "newcomer_arrived", arrival.home, text, arrival.home)
            if arrival.departure_day == next_day + 1:
                if arrival.name not in legacy.newcomers:
                    raise ValueError(f"Cannot depart an absent newcomer: {arrival.name}")
                del legacy.newcomers[arrival.name]
                text = f"{arrival.name} departed from {arrival.home}."
                legacy.chronicle.append(VillageLegacyEntry(minute=next_day * 1440, kind="departure", text=text))
                village.event(world, "village", "newcomer_departed", arrival.home, text, arrival.home)
    legacy.last_day = day


def observation(world: "World") -> dict[str, Any]:
    config, state = village.context(world)
    legacy = state.legacy
    if legacy is None or config.legacy is None:
        return {}
    rid = world.actor_id
    family = config.legacy.families[rid]
    known = legacy.learned.get(rid, [])
    lessons = active_lessons(world, rid)
    current = legacy.current.get(rid, rid.replace("-", " ").title())
    return {"legacy": {
        "family": family.model_dump(),
        "chronicle": [legacy.chronicle[index].model_dump() for index in known],
        "lessons": [{**legacy.chronicle[item.entry].model_dump(), "source": item.source,
                     "learned_at": item.learned_at} for item in lessons],
        "lineage": legacy.lineage.get(rid, []),
        "current": current,
        "inherited_coins": legacy.inherited_coins.get(rid, 0),
        "newcomers_here": [person.name for person in legacy.newcomers.values()
                           if person.home == world.actor.location_id],
    }}


def actions(world: "World") -> list[dict[str, Any]]:
    result = [{
        "id": "village_read_chronicle",
        "description": "Read the village chronicle at the square; learn past events and inherited obligations (five minutes).",
        "targets": [],
        "arguments_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "availability": "available", "reason": None,
    }]
    _, state = village.context(world)
    legacy = state.legacy
    if legacy is not None:
        nearby = [rid for rid in village.local_residents(world, world.actor.location_id)
                  if rid != world.actor_id]
        known = [index for index in legacy.learned.get(world.actor_id, [])
                 if legacy.chronicle[index].lesson]
        if nearby and known:
            result.append({"id": "village_share_chronicle",
                           "description": "Tell a nearby resident one personally read chronicle lesson.",
                           "targets": [], "arguments_schema": {"type": "object", "properties": {
                               "target": {"type": "string", "enum": nearby},
                               "entry": {"type": "integer", "enum": known}},
                               "required": ["target", "entry"], "additionalProperties": False},
                           "availability": "available", "reason": None})
    return result


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    _, state = village.context(world)
    legacy = state.legacy
    if legacy is None:
        raise ValueError("Village legacy is not enabled")
    if action == "village_share_chronicle":
        target, index = args["target"], args["entry"]
        if (target == world.actor_id or target not in village.local_residents(world, world.actor.location_id)
                or index not in legacy.learned.get(world.actor_id, [])
                or index >= len(legacy.chronicle) or legacy.chronicle[index].lesson is None):
            return "REJECTED", "STORY_UNAVAILABLE", "Share only a personally read lesson with a nearby resident.", None
        if state.residents[target].life and state.residents[target].life.sleeping:
            return "REJECTED", "RESIDENT_ASLEEP", "Wait until the listener wakes.", None
        learn(world, target, index, f"story:{world.actor_id}")
        text = f"Shared chronicle entry {index} with {village.resident_name(world, target)}."
        village.event(world, world.actor_id, action, target, text)
        return "APPLIED", "STORY_SHARED", text, None
    if world.actor.location_id != "village-square":
        return "REJECTED", "NOT_AT_CHRONICLE", "The chronicle is kept at the village square.", None
    known = legacy.learned.setdefault(world.actor_id, [])
    new = [index for index in range(len(legacy.chronicle)) if index not in known]
    for index in new:
        learn(world, world.actor_id, index, "square chronicle")
    text = f"Read {len(new)} new chronicle entries at the square."
    village.event(world, world.actor_id, action, "village-square", text)
    return "APPLIED", "CHRONICLE_READ", text, None


def monitor(world: "World") -> dict[str, Any]:
    return {}


def learn(world: "World", rid: str, index: int, source: str) -> None:
    _, state = village.context(world)
    legacy = state.legacy
    if index in legacy.learned.setdefault(rid, []):
        return
    legacy.learned[rid].append(index)
    if legacy.chronicle[index].lesson:
        legacy.lessons.setdefault(rid, []).append(VillageLegacyLesson(
            entry=index, learned_at=state.minute, source=source))


def active_lessons(world: "World", rid: str) -> list[VillageLegacyLesson]:
    _, state = village.context(world)
    legacy = state.legacy
    if legacy is None:
        return []
    return [lesson for lesson in legacy.lessons.get(rid, [])
            if 0 <= lesson.entry < len(legacy.chronicle)
            and lesson.entry in legacy.learned.get(rid, [])
            and 0 <= state.minute - legacy.chronicle[lesson.entry].minute <= LESSON_DAYS * 1440]


def decision_hint(world: "World") -> str:
    _, state = village.context(world)
    legacy = state.legacy
    if legacy is None:
        return ""
    kinds = {legacy.chronicle[item.entry].lesson for item in active_lessons(world, world.actor_id)}
    hints = []
    if "shortage" in kinds:
        hints.append("A verified food shortage recurred: check actual local meal demand and prepare early if funded.")
    if "delivery" in kinds:
        hints.append("A verified delivery was late: check your known order deadlines before optional trips.")
    if "repair" in kinds:
        hints.append("A verified repair succeeded: coordinate the real crew and supplies at the work site.")
    return " ".join(hints)


def successor_state(source_scenario: Scenario, source: WorldState, target: "World", save_id: str) -> None:
    """Transfer bounded family assets at a fresh-world checkpoint, never live actor bindings."""
    old, new = source.village, target.state.village
    if old is None or new is None or old.legacy is None or new.legacy is None:
        raise ValueError("Both generations must enable village legacy")
    if old.minute < 1440 or old.minute % 1440 != 0:
        raise ValueError("A generation handoff requires a midnight checkpoint")
    if old.legacy.last_day != old.minute // 1440:
        raise ValueError("Advance the source chronicle to midnight before handoff")
    if source_scenario.id != target.scenario.id:
        raise ValueError("Generation handoff requires the same scenario")
    if source.world_id == target.state.world_id:
        raise ValueError("Generation handoff requires a fresh world")
    if old.legacy.recorded_events > len(old.events):
        raise ValueError("The source chronicle has invalid event references")
    successors = target.scenario.village.legacy.families
    if not any(family.successor for family in successors.values()):
        raise ValueError("Configure a named successor before handing off a generation")
    for rid, family in successors.items():
        if not family.successor:
            continue
        if (rid not in source_scenario.village.residents or rid not in old.residents
                or rid not in source.actors
                or family.inherited_skill not in source_scenario.village.residents[rid].skills
                or target.scenario.village.residents[rid].work != source_scenario.village.residents[rid].work
                or target.scenario.village.residents[rid].home != source_scenario.village.residents[rid].home):
            raise ValueError(f"Incompatible successor role: {rid}")
        if (old.residents[rid].cargo or old.residents[rid].shipments
                or old.residents[rid].life and old.residents[rid].life.work
                or source.actors[rid].inventory):
            raise ValueError(f"Settle {rid}'s carried possessions and work before succession")
        if old.residents[rid].timber_route:
            raise ValueError(f"Stop {rid}'s active route before succession")
        if old.residents[rid].delivery is not None:
            raise ValueError(f"Settle {rid}'s active delivery before succession")
        if old.legacy.current.get(rid) == family.successor:
            raise ValueError(f"{rid} successor must differ from the incumbent")
        if any(lot.quantity for owners in old.depot_lots.values() for owner, inventory in owners.items()
               if owner == rid for lots in inventory.values() for lot in lots):
            raise ValueError(f"Settle {rid}'s depot property before succession")
        if (old.wagon is not None and any(shipment.seller == rid or shipment.buyer == rid
               for shipment in old.wagon.loads)) or any(
               shipment.seller == rid or shipment.buyer == rid
               for person in old.residents.values() for shipment in person.shipments):
            raise ValueError(f"Settle {rid}'s in-transit orders before succession")
        if old.economy and any(order.status not in {"fulfilled", "cancelled", "failed", "expired", "declined", "rejected"}
                               and (order.buyer == rid or order.supplier == rid)
                               for order in old.economy.orders.values()):
            raise ValueError(f"Settle {rid}'s outstanding orders before succession")
        if old.legacy.current.get(rid, rid.replace("-", " ").title()) in old.legacy.retired.get(rid, []):
            raise ValueError(f"{rid} incumbent is already retired")
        if family.successor in old.legacy.lineage.get(rid, []):
            raise ValueError(f"{rid} successor has already held this role")
        if family.successor in old.legacy.current.values():
            raise ValueError(f"{rid} successor already occupies a village role")
        if family.successor in {name for names in old.legacy.retired.values() for name in names}:
            raise ValueError(f"{rid} successor has already retired elsewhere")
        if family.successor in old.legacy.newcomers:
            raise ValueError(f"{rid} successor is not a physical resident")
        if source.actors[rid].location_id != source_scenario.village.residents[rid].home:
            raise ValueError(f"{rid} must return home before succession")
        if old.residents[rid].busy_until > old.minute:
            raise ValueError(f"{rid} is still committed to work")
        if source.actors[rid].session_id == target.session_id:
            raise ValueError(f"{rid} cannot reuse an old actor session")
        if any(index >= len(old.legacy.chronicle) for index in old.legacy.learned.get(rid, [])):
            raise ValueError(f"{rid} has invalid chronicle references")
        if any(lesson.entry >= len(old.legacy.chronicle) for lesson in old.legacy.lessons.get(rid, [])):
            raise ValueError(f"{rid} has invalid learned lessons")
        if any(lesson.entry not in old.legacy.learned.get(rid, []) for lesson in old.legacy.lessons.get(rid, [])):
            raise ValueError(f"{rid} has lessons without provenance")
        if any(lesson.learned_at > old.minute for lesson in old.legacy.lessons.get(rid, [])):
            raise ValueError(f"{rid} has lessons from the future")
    if old.wagon and old.wagon.driver in {
            rid for rid, family in successors.items() if family.successor}:
        raise ValueError("Park the wagon before its driver retires")
    new.legacy.chronicle = [entry.model_copy(update={
        "minute": new.minute,
        "origin_world_id": entry.origin_world_id or source.world_id,
        "origin_minute": entry.origin_minute if entry.origin_minute is not None else entry.minute,
        "lesson": entry.lesson if old.minute - entry.minute <= LESSON_DAYS * 1440 else None})
        for entry in old.legacy.chronicle[-100:]] + new.legacy.chronicle
    new.legacy.recorded_events = 0
    for rid, family in successors.items():
        if not family.successor:
            continue
        previous = old.legacy.current.get(rid, rid.replace("-", " ").title())
        new.legacy.lineage[rid] = [*old.legacy.lineage.get(rid, []), previous]
        new.legacy.retired[rid] = [*old.legacy.retired.get(rid, []), previous]
        new.legacy.current[rid] = family.successor
        if rid in target.state.entities:
            target.state.entities[rid].name = family.successor
        inherited = min(old.coins[rid], 20)
        new.coins[rid] += inherited
        new.legacy.inherited_coins[rid] = inherited
        for old_lesson in old.legacy.lessons.get(rid, []):
            if old_lesson.entry < max(0, len(old.legacy.chronicle) - 100):
                continue
            entry = old.legacy.chronicle[old_lesson.entry]
            if old.minute - entry.minute <= LESSON_DAYS * 1440:
                index = old_lesson.entry - max(0, len(old.legacy.chronicle) - 100)
                learn(target, rid, index, f"family:{previous}")
        new.legacy.chronicle.append(VillageLegacyEntry(
            minute=new.minute, kind="succession",
            text=f"{previous} retired; {family.successor} succeeded to the {rid} household and {inherited} coins."))
    new.legacy.handoff = {"save_id": save_id, "source_world_id": source.world_id}

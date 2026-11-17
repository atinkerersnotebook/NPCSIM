"""Opt-in inherited stories and a physical, persistent village chronicle."""

import copy
from typing import TYPE_CHECKING, Any

from npc_sim.models import Scenario, WorldState, VillageDiaryEntry, VillageLegacyEntry, VillageLegacyLesson, VillageLegacyState
from npc_sim import village

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"village_read_chronicle", "village_share_chronicle", "village_write_diary", "village_read_history"}
RECORDED = {
    "breakdown", "project_completed", "deadline_completed", "deadline_overdue",
    "missed_meal", "visitor_arrived", "visitor_departed", "village_merchant_trade",
    "trader_arrived", "trader_departed", "batch_completed", "research_completed", "trade",
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
                text=(f"{village.resident_name(world, entry.actor)}: {entry.result}"
                      if entry.actor in state.residents else entry.result),
                source_event=index, lesson=LESSONS.get(entry.action),
                origin_world_id=world.state.world_id, origin_minute=entry.minute,
                source_actor=entry.actor))
            if entry.action in {"project_completed", "missed_meal"} and entry.actor in state.residents:
                learn(world, entry.actor, len(legacy.chronicle) - 1, "firsthand experience")
    legacy.recorded_events = len(state.events)
    day = state.minute // 1440
    for next_day in range(legacy.last_day + 1, day + 1):
        midnight = next_day * 1440
        published = {entry.journal_id for entry in legacy.chronicle if entry.journal_id is not None}
        # Older archives can contain published journals without a journal_id.
        published.update(f"{entry.origin_world_id}:{entry.source_actor}:{entry.origin_minute}"
                         for entry in legacy.chronicle if entry.kind == "resident_journal"
                         and entry.origin_world_id is not None and entry.source_actor is not None
                         and entry.origin_minute is not None)
        for rid, diary in legacy.diaries.items():
            for note in diary:
                if note.written_at >= midnight:
                    continue
                identity = f"{note.origin_world_id}:{rid}:{note.written_at}"
                if identity in published:
                    continue
                legacy.chronicle.append(VillageLegacyEntry(
                    minute=midnight, kind="resident_journal", text=note.text,
                    origin_world_id=note.origin_world_id, origin_minute=note.written_at,
                    source_actor=rid, author=note.author, journal_id=identity, authored=True,
                    origin_day=note.written_at // 1440))
                published.add(identity)
        if state.incidents is not None:
            for visitor in state.incidents.visitors.values():
                if (visitor.arrived_at is not None and visitor.arrived_at < midnight
                        and (visitor.departed_at is None or visitor.departed_at > midnight)):
                    lodging = next_day - 1 in visitor.lodging_days
                    if not any(entry.kind == "visitor_overnight" and entry.origin_world_id == world.state.world_id
                               and entry.origin_minute == midnight and entry.source_actor == visitor.id
                               for entry in legacy.chronicle):
                        legacy.chronicle.append(VillageLegacyEntry(
                            minute=midnight, kind="visitor_overnight",
                            text=(f"{visitor.name} remained in the village overnight"
                                  + (f" with recorded lodging at {visitor.plan.lodging_location}." if lodging
                                     else "; no lodging commitment is recorded.")),
                            origin_world_id=world.state.world_id, origin_minute=midnight,
                            source_actor=visitor.id))
        trader = world.state.entities.get("iron-trader")
        if (next_day == day and trader is not None and trader.location == "village-square"
                and state.trader_day == next_day - 1 and not any(
                    entry.kind == "trader_overnight" and entry.origin_world_id == world.state.world_id
                    and entry.origin_minute == midnight and entry.source_actor == "iron-trader"
                    for entry in legacy.chronicle)):
            legacy.chronicle.append(VillageLegacyEntry(
                minute=midnight, kind="trader_overnight",
                text=f"The iron trader remained at {trader.location} overnight; no lodging is recorded.",
                origin_world_id=world.state.world_id, origin_minute=midnight,
                source_actor="iron-trader"))
        for arrival in config.legacy.arrivals:
            if arrival.day == next_day + 1:
                legacy.newcomers[arrival.name] = arrival.model_copy(deep=True)
                text = f"{arrival.name} arrived at {arrival.home}: {arrival.reason}"
                legacy.chronicle.append(VillageLegacyEntry(minute=midnight, kind="arrival", text=text,
                    origin_world_id=world.state.world_id, origin_minute=midnight, source_actor="village"))
                village.event(world, "village", "newcomer_arrived", arrival.home, text, arrival.home)
            if arrival.departure_day == next_day + 1:
                if arrival.name not in legacy.newcomers:
                    raise ValueError(f"Cannot depart an absent newcomer: {arrival.name}")
                del legacy.newcomers[arrival.name]
                text = f"{arrival.name} departed from {arrival.home}."
                legacy.chronicle.append(VillageLegacyEntry(minute=midnight, kind="departure", text=text,
                    origin_world_id=world.state.world_id, origin_minute=midnight, source_actor="village"))
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
        "chronicle": [legacy.chronicle[index].model_dump() for index in known] if history_ready(world) else [],
        "public_history": [legacy.chronicle[index].model_dump() for index in known
                           if 0 <= index < len(legacy.chronicle)] if history_ready(world) else [],
        "lessons": [{**legacy.chronicle[item.entry].model_dump(), "source": item.source,
                     "learned_at": item.learned_at} for item in lessons] if history_ready(world) else [],
        "lineage": legacy.lineage.get(rid, []) if history_ready(world) else [],
        "current": current,
        "inherited_coins": legacy.inherited_coins.get(rid, 0),
        "diary": [entry.model_dump() for entry in legacy.diaries.get(rid, [])] if history_ready(world) else [],
        "history_pending": history_pending(world),
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
        if history_pending(world):
            result.append({"id": "village_read_history",
                           "description": "Read your inherited family journal and village history before starting this generation (five minutes).",
                           "targets": [], "arguments_schema": {"type": "object", "properties": {},
                                                          "additionalProperties": False},
                           "availability": "available", "reason": None})
        if not journal_written(world, world.actor_id):
            result.append({"id": "village_write_diary",
                           "description": "Write one personal note today for the next resident in your role. Optionally detail observed problems and suggestions; advice is not proof of current conditions (five minutes).",
                           "targets": [], "arguments_schema": {"type": "object", "properties": {
                               "text": {"type": "string", "minLength": 20, "maxLength": 500},
                               "problems": {"type": "string", "minLength": 20, "maxLength": 1000},
                               "suggestions": {"type": "string", "minLength": 20, "maxLength": 1000}},
                               "required": ["text"], "additionalProperties": False},
                           "availability": "available", "reason": None})
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
    if action == "village_read_history":
        if not history_pending(world):
            return "REJECTED", "HISTORY_ALREADY_READ", "This generation's inherited history has already been read.", None
        if world.actor.location_id != world.scenario.village.residents[world.actor_id].home:
            return "REJECTED", "NOT_AT_HOME", "Read your inherited history at home before setting out.", None
        if state.residents[world.actor_id].life and state.residents[world.actor_id].life.sleeping:
            return "REJECTED", "ASLEEP", "Wake before reading your inherited history.", None
        for index, entry in enumerate(legacy.chronicle):
            if entry.origin_world_id and index not in legacy.learned.get(world.actor_id, []):
                learn(world, world.actor_id, index, "inherited chronicle")
        legacy.history_read.append(world.actor_id)
        text = "Read the inherited family journal and village chronicle; old accounts may not describe today's world."
        village.event(world, world.actor_id, action, world.actor.location_id, text)
        return "APPLIED", "HISTORY_READ", text, None
    if action == "village_write_diary":
        text, problems, suggestions = args.get("text"), args.get("problems"), args.get("suggestions")
        if (set(args) - {"text", "problems", "suggestions"}
                or not isinstance(text, str) or not 20 <= len(text.strip()) <= 500
                or any(value is not None and (not isinstance(value, str) or not 20 <= len(value.strip()) <= 1000)
                       for value in (problems, suggestions))):
            return "REJECTED", "INVALID_DIARY", "Write a 20-500 character note; optional problems and suggestions must each be 20-1000 characters.", None
        diary = legacy.diaries.setdefault(world.actor_id, [])
        if journal_written(world, world.actor_id):
            return "REJECTED", "DIARY_ALREADY_WRITTEN", "You have already written today's diary note.", None
        diary.append(VillageDiaryEntry(author=village.resident_name(world, world.actor_id),
                                       written_at=state.minute, text=text.strip(),
                                       problems=problems.strip() if problems is not None else None,
                                       suggestions=suggestions.strip() if suggestions is not None else None,
                                       origin_world_id=world.state.world_id))
        result = "Wrote a private diary note for a future successor."
        village.event(world, world.actor_id, action, world.actor.location_id, result)
        return "APPLIED", "DIARY_WRITTEN", result, None
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


def history_pending(world: "World") -> bool:
    _, state = village.context(world)
    return bool(state.legacy and state.legacy.handoff and world.actor_id not in state.legacy.history_read)


def history_ready(world: "World") -> bool:
    return not history_pending(world)


def journal_written(world: "World", rid: str) -> bool:
    _, state = village.context(world)
    if state.legacy is None or state.residents[rid].life is None:
        return False
    return any(entry.origin_world_id == world.state.world_id
               and entry.written_at >= state.residents[rid].life.last_woke
               for entry in state.legacy.diaries.get(rid, []))


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
    if legacy is None or history_pending(world):
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
    """Transfer bounded property and complete history, never live actor bindings."""
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
    old_research, new_research = old.research, new.research
    discoveries = {key: project for key, project in old_research.projects.items()
                   if project.discovered_at is not None} if old_research else {}
    if discoveries and new_research is None:
        raise ValueError("The successor generation must enable research to inherit discoveries")
    for key in discoveries:
        entity = source.entities.get(key)
        if (entity is None or entity.kind != "discovery" or entity.location != discoveries[key].location
                or not entity.readable_text or key in target.state.entities
                or new_research is not None and key in new_research.projects):
            raise ValueError(f"Invalid inherited discovery: {key}")
        if (discoveries[key].owner not in target.scenario.village.residents
                or discoveries[key].location not in target.state.entities
                or any(tool not in target.state.entities for tool in discoveries[key].proposal.equipment)):
            raise ValueError(f"Incompatible inherited discovery: {key}")
    new.legacy.chronicle = [entry.model_copy(update={
        "minute": new.minute,
        "origin_world_id": entry.origin_world_id or source.world_id,
        "origin_minute": entry.origin_minute if entry.origin_minute is not None else entry.minute,
        "lesson": entry.lesson if old.minute - entry.minute <= LESSON_DAYS * 1440 else None})
        for entry in old.legacy.chronicle] + new.legacy.chronicle
    new.legacy.recorded_events = 0
    new.legacy.diaries = {rid: [entry.model_copy(deep=True) for entry in old.legacy.diaries.get(rid, [])]
                          for rid in target.scenario.village.residents if old.legacy.diaries.get(rid)}
    if new_research is not None:
        for key, project in discoveries.items():
            new_research.projects[key] = project.model_copy(deep=True, update={
                "discovered_at": 0, "chunks": [], "trial_materials_consumed": False,
                "origin_world_id": project.origin_world_id or source.world_id})
            target.state.entities[key] = copy.deepcopy(source.entities[key])
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
            entry = old.legacy.chronicle[old_lesson.entry]
            if old.minute - entry.minute <= LESSON_DAYS * 1440:
                learn(target, rid, old_lesson.entry, f"family:{previous}")
        new.legacy.chronicle.append(VillageLegacyEntry(
            minute=new.minute, kind="succession",
            text=f"{previous} retired; {family.successor} succeeded to the {rid} household and {inherited} coins."))
    new.legacy.history_read = []
    new.legacy.handoff = {"save_id": save_id, "source_world_id": source.world_id}

"""Real, optional leisure and sidequests; the parent engine owns clock/save hooks."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from npc_sim import village, village_goods, village_life
from npc_sim.village_social_models import (
    ActivityInterval, BandPlan, ChapterProgress, QuestProgress, SocialPerson, SocialPlan, SocialDiscovery,
    VillageSocialConfig, VillageSocialState,
)

if TYPE_CHECKING:
    from npc_sim.world import World

VERBS = {
    "village_leisure", "village_social_cancel", "village_band_invite",
    "village_band_respond", "village_band_attend", "village_quest",
    "village_social_invite", "village_social_respond",
}
FREE_ACTIONS = {"look", "read", "say", "village_observe", "village_wait", "village_social_cancel"}
BOOK_STEPS = {
    "field_observe": ("observed_at", None),
    "organize_notes": ("organized_at", "observed_at"),
    "draft": ("drafted_at", "organized_at"),
    "revise": ("revised_at", "drafted_at"),
    "recipe_test": ("observed_at", None),
}
RECIPES = {
    "bread_test": ({"flour": 1}, {"bread": 1}),
    "tea_blending": ({"herbs": 1}, {"tea": 1}),
    "plank_practice": ({"timber": 1}, {"planks": 1}),
    "garden_tending": ({"manure": 1}, {}),
}


def validate_config(scenario) -> None:
    base = scenario.village
    config = getattr(base, "social", None) if base else None
    if config is None:
        return
    if not (base.ai_controlled and base.daily_life and base.time_based_turns):
        raise ValueError("Social activities require AI-controlled timed daily life")
    people, entities = base.residents, scenario.entities

    def resident(rid):
        if rid not in people:
            raise ValueError(f"Unknown social resident: {rid}")

    def location(eid):
        if eid not in entities or entities[eid].kind != "location":
            raise ValueError(f"Unknown social location: {eid}")

    def equipment(eid):
        if eid not in entities or entities[eid].kind in {"location", "door"}:
            raise ValueError(f"Equipment must reference an existing object: {eid}")

    for rid, interests in config.interests.items():
        resident(rid)
        for companion in interests.companions:
            resident(companion)
            if companion == rid:
                raise ValueError("A preferred companion must be another resident")
    for owner, instruments in config.instrument_inventory.items():
        resident(owner)
        for instrument in instruments:
            equipment(instrument)
            if not entities[instrument].takable:
                raise ValueError("Configured instrument inventory requires portable existing objects")
    listed = [eid for inventory in config.instrument_inventory.values() for eid in inventory]
    if len(set(listed)) != len(listed):
        raise ValueError("A physical instrument cannot have multiple inventory owners")
    declared_instruments = set(listed) | {
        member.instrument for band in config.bands.values() for member in band.members.values()
    }
    for project in config.projects.values():
        resident(project.owner)
        if not project.chapters:
            raise ValueError("A herbal project needs configured chapters")
        for chapter in project.chapters.values():
            location(chapter.site)
            equipment(chapter.specimen)
            if entities[chapter.specimen].location != chapter.site:
                raise ValueError("A chapter specimen must initially exist at its field site")
    for activity in config.activities.values():
        location(activity.location)
        for eid in activity.equipment:
            equipment(eid)
        for resource in activity.supplies:
            if not any(resource in stock for stock in base.stocks.values()):
                raise ValueError(f"Activity supply is not real configured stock: {resource}")
        if activity.recipe:
            inputs, outputs = RECIPES[activity.recipe]
            if activity.supplies != inputs:
                raise ValueError("Practical activity inputs must match its finite supported material recipe")
            if any(not any(resource in stock for stock in base.stocks.values()) for resource in outputs):
                raise ValueError("Recipe outputs must use actual configured world resources")
        if activity.repair_target:
            equipment(activity.repair_target)
            if activity.repair_target not in declared_instruments:
                raise ValueError("Instrument repair cannot bypass other engine projects; declare a real instrument")
            if not activity.equipment:
                raise ValueError("Instrument repair requires actual configured repair tools")
            if activity.supplies != {"iron": 1}:
                raise ValueError("The supported instrument fitting repair consumes exactly one real iron")
            if entities[activity.repair_target].health is None:
                raise ValueError("A repair target must have actual repairable health")
        if activity.project is not None:
            project = config.projects.get(activity.project)
            if project is None or activity.chapter not in project.chapters:
                raise ValueError("Writing activity references an unknown chapter")
            if activity.kind == "field_observe" and activity.location != project.chapters[activity.chapter].site:
                raise ValueError("Field observation must take place at the chapter site")
            if activity.kind == "recipe_test":
                if project.kind != "recipe" or activity.location != project.chapters[activity.chapter].site:
                    raise ValueError("Recipe testing must happen at its actual configured recipe site")
                if project.chapters[activity.chapter].specimen not in activity.equipment:
                    raise ValueError("Recipe testing requires the actual configured oven/equipment")
            if activity.kind == "field_observe" and project.kind != "herbal_book":
                raise ValueError("Recipe progress requires an actual recipe test, not field observation")
    for band in config.bands.values():
        location(band.venue)
        resident(band.coordinator)
        if people[band.coordinator].work != band.venue:
            raise ValueError("Venue coordinator must actually work at the band venue")
        if band.payer:
            resident(band.payer)
        for rid, member in band.members.items():
            resident(rid)
            equipment(member.instrument)
    for qid, quest in config.quests.items():
        resident(quest.giver)
        for rid in quest.eligible:
            resident(rid)
            if rid == quest.giver:
                raise ValueError("Quest giver cannot pay themselves a quest reward")
        if any(key not in config.quests or key == qid for key in quest.prerequisites):
            raise ValueError("Unknown or self-dependent quest prerequisite")
        obj = quest.objective
        if obj.kind == "activity" and obj.target not in config.activities:
            raise ValueError("Quest references an unsupported activity")
        if obj.kind == "chapter":
            if obj.project not in config.projects or obj.target not in config.projects[obj.project].chapters:
                raise ValueError("Quest references an unknown project chapter")
            if not any(rid == config.projects[obj.project].owner for rid in quest.eligible):
                raise ValueError("Chapter quest must allow the project's owner")
        if obj.kind == "item_at":
            equipment(obj.target)
            location(obj.location)
            if not entities[obj.target].takable:
                raise ValueError("Item quest requires an actually portable object")
        if obj.kind == "production":
            resident(obj.target)
            if obj.target not in quest.eligible or people[obj.target].job is None:
                raise ValueError("Production quest requires an eligible real worker")
        if obj.kind == "repair":
            equipment(obj.target)
            if not any(activity.repair_target == obj.target for activity in config.activities.values()):
                raise ValueError("Repair quest requires an implemented real repair activity")
        if obj.kind == "discovery_shared":
            kinds = {activity.kind for activity in config.activities.values() if activity.discovery == obj.target}
            if not {"fishing_survey", "share_discovery"} <= kinds:
                raise ValueError("Discovery quest requires implemented survey and actual sharing activities")
        if obj.kind == "band" and obj.target not in config.bands:
            raise ValueError("Band quest requires a configured coordinated band")
    for root in config.quests:
        def visit(key, path):
            if key in path:
                raise ValueError("Cyclic quest prerequisites")
            for dependency in config.quests[key].prerequisites:
                visit(dependency, path | {key})
        visit(root, set())


def initial_state(scenario) -> VillageSocialState | None:
    config = getattr(scenario.village, "social", None)
    if config is None:
        return None
    validate_config(scenario)
    return VillageSocialState(
        config=config.model_copy(deep=True),
        residents={rid: SocialPerson() for rid in scenario.village.residents},
        projects={key: {chapter: ChapterProgress() for chapter in project.chapters}
                  for key, project in config.projects.items()},
        quests={key: QuestProgress() for key in config.quests},
    )


def _context(world):
    base, state = village.context(world)
    config = getattr(base, "social", None)
    social = getattr(state, "social", None)
    if social is not None and social.config is not None:
        config = social.config
    if config is None:
        return base, state, None, None
    if social is None:
        raise ValueError("Social state is missing; initialize from the matching frozen scenario")
    return base, state, config, social


def configure(world: "World", configdict: dict[str, Any]) -> dict[str, Any]:
    """Privileged DM-only entry point. The authenticated parent persists the result."""
    config = VillageSocialConfig.model_validate(configdict)
    base, state = village.context(world)
    values = {key: getattr(base, key) for key in type(base).model_fields}
    values["social"] = config
    validate_config(SimpleNamespace(village=SimpleNamespace(**values), entities=world.scenario.entities))
    social = getattr(state, "social", None)
    if social is None:
        social = VillageSocialState()
        state.social = social
    old = social.config or getattr(base, "social", None)
    if old:
        # Durable interval/quest evidence refers to these definitions; DM additions
        # cannot rewrite completed work, promised rewards or existing commitments.
        for field in ("activities", "projects", "bands", "quests"):
            previous, updated = getattr(old, field), getattr(config, field)
            if any(key not in updated or updated[key] != value for key, value in previous.items()):
                raise ValueError("DM may add templates but cannot remove or rewrite existing social definitions")
    for rid in base.residents:
        social.residents.setdefault(rid, SocialPerson())
    for key, project in config.projects.items():
        social.projects.setdefault(key, {chapter: ChapterProgress() for chapter in project.chapters})
    for key in config.quests:
        social.quests.setdefault(key, QuestProgress())
    social.config = config
    social.revision += 1
    social.configuration_history.append(config.model_copy(deep=True))
    _event(world, "dm", "social_configured", "village",
           f"Validated social configuration revision {social.revision}; finite capability templates only.",
           "village-square")
    return social.model_dump()


def _active(social, rid):
    return next((item for item in social.intervals
                 if item.status == "active" and rid in item.participants), None)


def blocked_until(world: "World", rid: str) -> int | None:
    _, _, config, social = _context(world)
    if config is None:
        return None
    interval = _active(social, rid)
    return interval.ends_at if interval else None


def action_blocker(world: "World", action: str, rid: str | None = None) -> tuple[str, str] | None:
    until = blocked_until(world, rid or world.actor_id)
    if until is not None and action not in FREE_ACTIONS:
        return "SOCIAL_COMMITTED", f"Committed until {village.clock_time(until)}; cancel explicitly to interrupt."
    return None


def _equipment_access(world, rid, eid, location, config):
    _, state, _, social = _context(world)
    entity = world.state.entities[eid]
    from npc_sim import village_research
    from npc_sim import village_livestock
    if village_livestock.equipment_reserved(world, {eid}):
        return False
    if village_research.equipment_reserved(world, {eid}):
        return False
    from npc_sim import village_mining
    if village_mining.equipment_reserved(world, {eid}):
        return False
    if entity.broken or entity.hidden:
        return False
    if any(interval.status == "active" and eid in interval.equipment and rid not in interval.participants
           for interval in social.intervals):
        return False
    if getattr(state, "incidents", None) is not None:
        from npc_sim import village_events
        if village_events.delay_until(world, rid, location, eid) > state.minute:
            return False
    actor = world.state.actors.get(rid)
    if entity.location == location:
        return True
    # Config inventory is an access grant to an existing item, not a duplicate item.
    # Actual inventory/physical placement remains authoritative.
    if actor and eid in actor.inventory:
        return True
    return (eid in config.instrument_inventory.get(rid, [])
            and entity.location == getattr(actor, "location_id", None) == location)


def _guard(world, rid, location, end, *, existing=None, require_present=True):
    base, state, config, social = _context(world)
    person, resident = state.residents[rid], base.residents[rid]
    now, life = state.minute, person.life
    if require_present and village.resident_location(world, rid) != location:
        return "NOT_PRESENT", "Walk to the configured activity location; remote participation is not supported."
    if life is None or life.sleeping or village_life.rest_due(resident, life, now):
        return "REST_DUE", "Meals and actual sleep take precedence over optional leisure."
    if village_life.meal_due(life, now) or life.hungry:
        return "MEAL_DUE", "Eat an actual meal first; missed food cannot be replaced by leisure."
    if life.work or person.timber_route or person.delivery is not None:
        return "WORK_COMMITTED", "Finish or explicitly stop the existing work or delivery commitment."
    if state.wagon and state.wagon.driver == rid:
        return "ROUTE_COMMITTED", "Park and release the wagon before leisure."
    if state.horse_leader == rid:
        return "ANIMAL_COMMITTED", "Stable and release the horse before optional leisure."
    if person.busy_until > now or (existing is None and person.available_at > now):
        return "BUSY", "The resident has not finished their existing time reservation."
    if _active(social, rid) is not None and _active(social, rid) is not existing:
        return "SOCIAL_COMMITTED", "Already participating in another activity."
    for task in state.tasks.values():
        if task.assignee == rid and task.status not in {"completed", "cancelled"}:
            return "TASK_COMMITTED", "Resolve the accepted village task before optional leisure."
    for plan in social.band_plans.values():
        if plan.status == "invited" and rid in plan.accepted + plan.audience:
            if now < plan.ends_at and end > plan.starts_at:
                return "BAND_COMMITTED", "This activity overlaps an accepted band invitation."
    for plan in social.social_plans.values():
        if plan.status == "invited" and rid in plan.accepted:
            if now < plan.ends_at and end > plan.starts_at:
                return "SOCIAL_COMMITTED", "This activity overlaps an accepted social invitation."
    if getattr(base, "events", None) is not None or getattr(state, "incidents", None) is not None:
        from npc_sim import village_events
        if village_events.delay_until(world, rid, location) > now:
            return "INCIDENT_BLOCKED", "A real incident prevents this activity or attendance."
    path = village.route(world, location, resident.home)
    if path is None:
        return "NO_HOME_ROUTE", "No currently passable real route home exists."
    # Engine travel is five minutes per edge. Include action overhead and sleep action.
    return_time = 5 * len(path) + config.action_allowance
    bedtime = now // 1440 * 1440 + resident.bed_minute
    if end + return_time > bedtime:
        return "BEDTIME_CONFLICT", "Activity does not leave enough actual walking time home before bedtime."
    meals = [meal.due for meal in life.meals.values() if meal.status == "pending" and meal.due > now]
    if now % 1440 < 1140:
        meals.append(now // 1440 * 1440 + 1140)
    day_policy = getattr(state, "day_policy", None) or base
    if getattr(day_policy, "breakfast", False):
        breakfast = now // 1440 * 1440 + resident.wake_minute + day_policy.breakfast_delay
        if now < breakfast:
            meals.append(breakfast)
    for due in meals:
        route = village.route(world, location, "tavern")
        if end + (5 * len(route) if route is not None else 1440) + config.action_allowance > due:
            return "MEAL_CONFLICT", "Activity would prevent travel to the required meal."
    return None


def _book_guard(world, rid, activity, social, config):
    if activity.kind not in BOOK_STEPS:
        return None
    project = config.projects[activity.project]
    if project.owner != rid:
        return "NOT_PROJECT_OWNER", "Only the project's owner may perform its writing operations."
    chapter = project.chapters[activity.chapter]
    progress = social.projects[activity.project][activity.chapter]
    field, prerequisite = BOOK_STEPS[activity.kind]
    if getattr(progress, field) is not None:
        return "ALREADY_RECORDED", "This chapter operation is already durably recorded."
    if prerequisite and getattr(progress, prerequisite) is None:
        return "NOTES_REQUIRED", "Observe, organize notes, draft, then revise; no unsupported writing progress."
    if activity.kind == "field_observe":
        specimen = world.state.entities[chapter.specimen]
        if specimen.location != chapter.site or specimen.hidden or specimen.broken:
            return "NO_SPECIMEN", "The actual configured plant must be present and observable at its site."
    return None


def _practical_guard(world, rid, activity):
    base, state, _, social = _context(world)
    person = social.residents[rid]
    required_skill = {
        "instrument_repair": "smithing", "fishing_survey": "fishing",
        "recipe_test": "baking",
    }.get(activity.kind)
    if required_skill and required_skill not in base.residents[rid].skills:
        return "CAPABILITY_REQUIRED", "This practical operation requires the resident's actual occupation capability."
    if activity.recipe == "plank_practice" and "carpentry" not in base.residents[rid].skills:
        return "CAPABILITY_REQUIRED", "Real plank practice requires actual carpentry capability."
    if activity.recipe == "garden_tending" and "farming" not in base.residents[rid].skills:
        return "CAPABILITY_REQUIRED", "Real garden care requires actual farming capability."
    if activity.repair_target:
        target = world.state.entities[activity.repair_target]
        if target.location not in {activity.location, rid} or target.hidden:
            return "NO_REPAIR_TARGET", "Bring the actual damaged instrument to the real repair site."
        if not target.broken:
            return "ALREADY_REPAIRED", "The instrument is already usable; no duplicate repair credit."
    if activity.recipe == "garden_tending" and person.practice_days.get(activity.location) == state.minute // 1440:
        return "ALREADY_TENDED", "Today's real garden care is already recorded."
    if activity.kind == "fishing_survey" and activity.discovery in person.discoveries:
        return "ALREADY_OBSERVED", "This actual fishing-site discovery is already recorded."
    if activity.kind == "share_discovery" and activity.discovery not in person.discoveries:
        return "DISCOVERY_REQUIRED", "Survey the actual fishing site before sharing your own verified discovery."
    return None


def _id(social, prefix):
    social.sequence += 1
    return f"{prefix}:{social.sequence}"


def _event(world, rid, verb, target, text, location):
    return village.event(world, rid, verb, target, text, location)


def _start(world, aid, kind, location, duration, participants, supplies=None,
           equipment=None, band_plan=None, social_plan=None):
    _, state, _, social = _context(world)
    consumed = {resource: amount * len(participants) for resource, amount in (supplies or {}).items()}
    for resource, amount in consumed.items():
        village_goods.take(world, location, resource, amount)
    interval = ActivityInterval(
        id=_id(social, "social"), activity=aid, kind=kind, location=location,
        participants=participants, starts_at=state.minute, ends_at=state.minute + duration,
        consumed=consumed, equipment=equipment or [], band_plan=band_plan, social_plan=social_plan,
    )
    social.intervals.append(interval)
    for rid in participants:
        state.residents[rid].available_at = interval.ends_at
        state.residents[rid].activity = f"{kind} at {location} until {village.clock_time(interval.ends_at)}"
        interval.evidence.append(_event(
            world, rid, "social_started", aid,
            f"Started {kind}; duration {duration} minutes; participants {participants}; consumed {consumed}.",
            location,
        ))
    return interval


def _award(world, interval, skill):
    _, state, config, social = _context(world)
    day = interval.starts_at // 1440
    for rid in interval.participants:
        person = social.residents[rid]
        person.enjoyment = min(100, person.enjoyment + 1)
        if skill:
            key = f"{day}:{skill}"
            count = person.skill_awards.get(key, 0)
            if count < config.daily_skill_limit:
                person.skills[skill] = min(config.skill_cap, person.skills.get(skill, 0) + 1)
                person.skill_awards[key] = count + 1
        for other in interval.participants:
            if other == rid:
                continue
            key = f"{day}:{other}"
            count = person.relationship_awards.get(key, 0)
            if count < config.daily_relationship_limit:
                trust = state.residents[rid].trust
                trust[other] = max(trust.get(other, 0), min(config.relationship_cap, trust.get(other, 0) + 1))
                person.relationship_awards[key] = count + 1


def _finish(world, interval, reason=None):
    _, state, config, social = _context(world)
    interval.status = "interrupted" if reason else "completed"
    interval.actual_end = min(state.minute, interval.ends_at)
    interval.reason = reason
    for rid in interval.participants:
        person = state.residents[rid]
        if person.available_at == interval.ends_at:
            person.available_at = state.minute
        person.activity = f"{interval.kind} {'interrupted' if reason else 'completed'}"
        interval.evidence.append(_event(
            world, rid, "social_interrupted" if reason else "social_completed", interval.activity,
            f"{person.activity}; actual interval {interval.starts_at}-{state.minute}"
            + (f"; {reason}" if reason else "."), interval.location,
        ))
    if interval.band_plan:
        plan = social.band_plans[interval.band_plan]
        plan.status = "cancelled" if reason else "completed"
        plan.reason = reason
        band = config.bands[plan.band]
        if not reason:
            _award(world, interval, None)
            # Listeners enjoy the music; only configured musicians learn instrumental skills.
            for rid in band.members:
                member_interval = interval.model_copy(update={"participants": [rid]})
                enjoyment = social.residents[rid].enjoyment
                _award(world, member_interval, band.skill)
                social.residents[rid].enjoyment = enjoyment
            if plan.mode == "performance" and band.fee_per_member:
                total = band.fee_per_member * len(band.members)
                if state.coins[band.payer] >= total:
                    state.coins[band.payer] -= total
                    for rid in band.members:
                        state.coins[rid] += band.fee_per_member
                    plan.paid = True
                    _event(world, band.payer, "band_paid", plan.id,
                           f"Paid {total} coins to actual performers exactly once.", band.venue)
                else:
                    plan.reason = "Completed, but explicit payer lacks funds; no payment fabricated."
                    _event(world, band.coordinator, "band_unpaid", plan.id, plan.reason, band.venue)
        return
    if interval.social_plan:
        plan = social.social_plans[interval.social_plan]
        plan.status, plan.reason = ("cancelled" if reason else "completed"), reason
    if not reason:
        activity = config.activities[interval.activity]
        _award(world, interval, activity.skill)
        if activity.recipe:
            _, outputs = RECIPES[activity.recipe]
            for resource, amount in outputs.items():
                village_goods.put(world, interval.location, resource, amount)
            interval.produced = outputs.copy()
            if activity.recipe == "garden_tending":
                person = social.residents[interval.participants[0]]
                person.garden_care[interval.location] = person.garden_care.get(interval.location, 0) + 1
                person.practice_days[interval.location] = interval.starts_at // 1440
            _event(world, interval.participants[0], "social_material_outcome", interval.activity,
                   f"Verified conversion {interval.consumed} -> {outputs}; garden care "
                   "is recorded separately and never invents harvest stock.", interval.location)
        if activity.repair_target:
            target = world.state.entities[activity.repair_target]
            target.health = target.max_health
            _event(world, interval.participants[0], "instrument_repaired", activity.repair_target,
                   f"Restored the actual instrument using {interval.consumed} and real repair tools.", interval.location)
        if activity.kind == "fishing_survey":
            person = social.residents[interval.participants[0]]
            person.discoveries[activity.discovery] = SocialDiscovery(
                location=interval.location, observed_at=state.minute, evidence=interval.evidence.copy())
        if activity.kind == "share_discovery":
            for rid in interval.participants:
                discovery = social.residents[rid].discoveries.get(activity.discovery)
                if discovery:
                    discovery.shared_with = list(dict.fromkeys(discovery.shared_with +
                                                                [other for other in interval.participants if other != rid]))
                    discovery.evidence.extend(interval.evidence)
        if activity.kind in BOOK_STEPS:
            progress = social.projects[activity.project][activity.chapter]
            field, _ = BOOK_STEPS[activity.kind]
            if getattr(progress, field) is None:
                setattr(progress, field, state.minute)
                progress.evidence.extend(interval.evidence)
                _event(world, interval.participants[0], "book_progress", activity.project,
                       f"Verified {activity.kind} for chapter {activity.chapter}; fictional lore, not medical advice.",
                       interval.location)


def _cancel_plan(world, plan, reason):
    _, _, config, _ = _context(world)
    plan.status, plan.reason = "cancelled", reason
    band = config.bands[plan.band]
    _event(world, band.coordinator, "band_cancelled", plan.id, reason, band.venue)


def _quest_value(world, template, rid):
    _, state, config, social = _context(world)
    obj = template.objective
    if obj.kind == "activity":
        return sum(item.status == "completed" and item.activity == obj.target and rid in item.participants
                   for item in social.intervals)
    if obj.kind == "chapter":
        return int(social.projects[obj.project][obj.target].revised_at is not None)
    if obj.kind == "item_at":
        entity = world.state.entities[obj.target]
        return int(entity.location == obj.location and not entity.hidden and not entity.broken)
    if obj.kind == "repair":
        entity = world.state.entities[obj.target]
        if entity.broken:
            return 0
        return sum(interval.status == "completed" and rid in interval.participants
                   and interval.activity in config.activities
                   and config.activities[interval.activity].repair_target == obj.target
                   for interval in social.intervals)
    if obj.kind == "discovery_shared":
        discovery = social.residents[rid].discoveries.get(obj.target)
        return int(discovery is not None and bool(discovery.shared_with))
    if obj.kind == "band":
        return sum(plan.band == obj.target and plan.mode == "performance" and plan.status == "completed"
                   and config.bands[plan.band].coordinator == rid for plan in social.band_plans.values())
    return social.production_counts.get(obj.target, 0)


def _refund(world, template, quest):
    _, state, _, _ = _context(world)
    if quest.reward_reserved:
        state.coins[template.giver] += quest.reward_reserved
        _event(world, template.giver, "quest_refunded", template.title,
               f"Returned {quest.reward_reserved} escrowed coins.", village.resident_location(world, template.giver))
        quest.reward_reserved = 0


def _quests_advance(world):
    _, state, config, social = _context(world)
    for qid, template in config.quests.items():
        quest = social.quests[qid]
        if quest.status in {"completed", "declined", "abandoned", "expired", "failed"}:
            continue
        if state.minute >= template.expires_at:
            _refund(world, template, quest)
            quest.status = "failed" if quest.status == "accepted" else "expired"
            quest.reason = "Deadline passed without a verified outcome."
            _event(world, template.giver, "quest_expired", qid, quest.reason,
                   village.resident_location(world, template.giver))
        elif quest.status == "deferred" and state.minute >= quest.defer_until:
            quest.status, quest.defer_until = "offered", None
            _event(world, template.giver, "quest_reoffered", qid, "The explicit deferral has elapsed.",
                   village.resident_location(world, template.giver))
        elif quest.status == "accepted":
            value = _quest_value(world, template, quest.assignee)
            obj = template.objective
            progress = max(0, value - quest.baseline)
            if obj.kind in {"item_at", "chapter"}:
                progress = value
            if obj.kind == "item_at" and not quest.objective_verified:
                progress = 0
            quest.progress = min(obj.quantity, progress)
            if quest.progress >= obj.quantity:
                quest.status, quest.reward_paid = "completed", True
                state.coins[quest.assignee] += quest.reward_reserved
                reward = quest.reward_reserved
                quest.reward_reserved = 0
                quest.evidence.append(_event(
                    world, quest.assignee, "quest_completed", qid,
                    f"Verified {obj.kind} objective; paid {reward} escrowed coins exactly once.",
                    village.resident_location(world, quest.assignee),
                ))


def _supplies_guard(world, activity, participants):
    _, state, _, _ = _context(world)
    for resource, each in activity.supplies.items():
        available = state.stocks.get(activity.location, {}).get(resource, 0)
        if getattr(state, "economy", None) is not None:
            from npc_sim import village_economy
            available -= village_economy.protected_quantity(world, activity.location, resource)
        if available < each * len(participants):
            return "NO_SUPPLIES", "Required real local supplies are unavailable or reserved for actual commitments."
    return None


def _cancel_social_plan(world, plan, reason):
    _, _, config, _ = _context(world)
    plan.status, plan.reason = "cancelled", reason
    _event(world, plan.organizer, "social_cancelled", plan.id, reason,
           config.activities[plan.activity].location)


def record_action(world: "World", action: str, args: dict[str, Any], outcome) -> None:
    """Call after successful engine actions; claims in args/outcome are never evidence."""
    _, state, config, social = _context(world)
    if config is None:
        return
    applied = outcome == "APPLIED" or (isinstance(outcome, dict) and outcome.get("outcome") == "APPLIED")
    if isinstance(outcome, tuple):
        applied = bool(outcome) and outcome[0] == "APPLIED"
    if applied:
        for qid, template in config.quests.items():
            quest, obj = social.quests[qid], template.objective
            if (quest.status == "accepted" and quest.assignee == world.actor_id
                    and obj.kind == "item_at" and action == "drop" and args.get("target") == obj.target
                    and world.actor.location_id == obj.location and _quest_value(world, template, world.actor_id)):
                if not quest.objective_verified:
                    quest.objective_verified = True
                    quest.evidence.append(_event(
                        world, world.actor_id, "quest_item_verified", qid,
                        "Verified the assignee's successful drop of the real object at its actual destination.",
                        world.actor.location_id,
                    ))
        advance(world)


def advance(world: "World") -> None:
    _, state, config, social = _context(world)
    if config is None:
        return
    production_verbs = village_life.PRODUCTION
    # Scan trusted engine events, not the user-provided outcome text. A timed
    # batch's start is never production credit, and failed batches award nothing.
    end = len(state.events)
    for notice in state.events[social.processed_event_count:end]:
        if notice.actor not in state.residents:
            continue
        resident = world.scenario.village.residents[notice.actor]
        immediate = (notice.action in production_verbs and resident.job is not None
                     and (resident.job.interruptible or notice.action != "village_work"))
        batch = notice.action == "batch_completed" and resident.job is not None
        if immediate or batch:
            social.production_counts[notice.actor] = social.production_counts.get(notice.actor, 0) + 1
    social.processed_event_count = end
    for interval in social.intervals:
        if interval.status != "active":
            continue
        reason = None
        for rid in interval.participants:
            problem = _guard(world, rid, interval.location, interval.ends_at, existing=interval)
            if problem:
                reason = problem[1]
                break
        if reason is None:
            if interval.band_plan:
                band = config.bands[social.band_plans[interval.band_plan].band]
                if any(not _equipment_access(world, rid, member.instrument, interval.location, config)
                       for rid, member in band.members.items()):
                    reason = "A performer lost actual access to their own configured instrument."
            else:
                for eid in interval.equipment:
                    if not any(_equipment_access(world, rid, eid, interval.location, config)
                               for rid in interval.participants):
                        reason = f"Required actual equipment {eid} is no longer accessible."
                        break
        if reason is None and not interval.band_plan:
            activity = config.activities[interval.activity]
            problem = _book_guard(world, interval.participants[0], activity, social, config)
            if problem:
                reason = problem[1]
            problem = _practical_guard(world, interval.participants[0], activity)
            if problem:
                reason = problem[1]
        if reason or state.minute >= interval.ends_at:
            _finish(world, interval, reason)
    for plan in social.band_plans.values():
        if plan.status != "invited" or state.minute < plan.starts_at:
            continue
        band = config.bands[plan.band]
        if state.minute != plan.starts_at:
            _cancel_plan(world, plan, "Start time was missed; no retroactive attendance or performance.")
            continue
        if set(plan.accepted) != set(plan.invited):
            _cancel_plan(world, plan, "Missing member acceptance; the group cannot perform.")
            continue
        participants = plan.accepted + plan.audience
        # Remove this pending commitment from overlap checks during its coordinated start.
        plan.status = "active"
        problem = next((_guard(world, rid, band.venue, plan.ends_at) for rid in participants
                        if _guard(world, rid, band.venue, plan.ends_at)), None)
        instruments = [member.instrument for member in band.members.values()]
        if problem:
            _cancel_plan(world, plan, problem[1])
        elif any(not _equipment_access(world, rid, member.instrument, band.venue, config)
                 for rid, member in band.members.items()):
            _cancel_plan(world, plan, "An actual configured instrument is unavailable.")
        elif plan.mode == "performance" and state.coins.get(band.payer, 0) < band.fee_per_member * len(band.members):
            _cancel_plan(world, plan, "The explicit performer fee payer cannot fund this show.")
        else:
            _start(world, plan.band, plan.mode, band.venue, band.duration,
                   participants, equipment=instruments, band_plan=plan.id)
    for plan in social.social_plans.values():
        if plan.status != "invited" or state.minute < plan.starts_at:
            continue
        activity = config.activities[plan.activity]
        if state.minute != plan.starts_at or set(plan.accepted) != set(plan.invited):
            _cancel_social_plan(world, plan, "Start missed or participants did not each accept.")
            continue
        plan.status = "active"
        problem = next((_guard(world, rid, activity.location, plan.ends_at) for rid in plan.accepted
                        if _guard(world, rid, activity.location, plan.ends_at)), None)
        problem = problem or _supplies_guard(world, activity, plan.accepted)
        problem = problem or _practical_guard(world, plan.organizer, activity)
        if problem:
            _cancel_social_plan(world, plan, problem[1])
        elif any(not any(_equipment_access(world, rid, eid, activity.location, config)
                         for rid in plan.accepted) for eid in activity.equipment):
            _cancel_social_plan(world, plan, "Required actual equipment is unavailable.")
        else:
            _start(world, plan.activity, activity.kind, activity.location, activity.duration,
                   plan.accepted, activity.supplies, activity.equipment, social_plan=plan.id)
    _quests_advance(world)


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    _, state, config, social = _context(world)
    if config is None:
        return "REJECTED", "SOCIAL_DISABLED", "This scenario has not opted into social activities.", None
    advance(world)
    rid = world.actor_id

    def reject(code, text):
        return "REJECTED", code, text, None

    def done(code, text):
        _event(world, rid, action, args.get("activity", args.get("plan", args.get("quest", action))),
               text, world.actor.location_id)
        return "APPLIED", code, text, None

    if action not in VERBS:
        raise ValueError(f"Unknown social action: {action}")
    expected = {
        "village_leisure": {"activity", "companions"},
        "village_social_cancel": set(),
        "village_band_invite": {"band", "mode", "starts_at"},
        "village_band_respond": {"plan", "response"},
        "village_band_attend": {"plan"},
        "village_quest": {"quest", "response", "until"},
        "village_social_invite": {"activity", "companions", "starts_at"},
        "village_social_respond": {"plan", "response"},
    }[action]
    if set(args) != expected:
        return reject("BAD_ARGUMENTS", "Use exactly the advertised action arguments.")
    blocker = action_blocker(world, action)
    if blocker:
        return reject(*blocker)
    if action == "village_social_cancel":
        interval = _active(social, rid)
        if interval is None:
            return reject("NO_ACTIVITY", "No activity is currently committed.")
        _finish(world, interval, f"{rid} explicitly interrupted the optional activity.")
        return done("SOCIAL_INTERRUPTED", "Stopped the actual activity; no completion or skill credit.")
    if action == "village_social_invite":
        activity = config.activities.get(args["activity"])
        companions, start = args["companions"], args["starts_at"]
        if activity is None or activity.project is not None or activity.max_participants < 2:
            return reject("BAD_ACTIVITY", "Choose a supported configured social activity.")
        if (not isinstance(companions, list) or any(type(item) is not str for item in companions)
                or not companions or len(set([rid, *companions])) != len(companions) + 1
                or any(other not in state.residents for other in companions)):
            return reject("BAD_PARTICIPANTS", "Invite unique real resident companions.")
        if not activity.min_participants <= 1 + len(companions) <= activity.max_participants:
            return reject("PARTICIPANT_COUNT", "Respect the configured participant bounds.")
        if type(start) is not int or start % 5 or start < state.minute + config.action_allowance:
            return reject("BAD_START", "Choose a future absolute five-minute start.")
        if start // 1440 != state.minute // 1440:
            return reject("PLAN_TOO_FAR", "Protect today's meals before scheduling later days.")
        problem = _guard(world, rid, activity.location, start + activity.duration)
        if problem:
            return reject(*problem)
        plan = SocialPlan(id=_id(social, "gathering"), activity=args["activity"], organizer=rid,
                          starts_at=start, ends_at=start + activity.duration,
                          invited=[rid, *companions], accepted=[rid])
        social.social_plans[plan.id] = plan
        return done("SOCIAL_INVITED", f"Invitation {plan.id}; companions must each choose to accept and attend.")
    if action == "village_social_respond":
        plan = social.social_plans.get(args["plan"])
        if (plan is None or plan.status != "invited" or state.minute >= plan.starts_at
                or rid not in plan.invited):
            return reject("NO_INVITATION", "No eligible future social invitation exists.")
        if rid in plan.accepted:
            return reject("ALREADY_RESPONDED", "Your acceptance is already persisted.")
        if args["response"] == "decline":
            _cancel_social_plan(world, plan, f"{rid} declined the optional invitation.")
            return done("SOCIAL_DECLINED", "Declined; no compulsory leisure.")
        if args["response"] != "accept":
            return reject("BAD_RESPONSE", "Accept or decline your own invitation.")
        activity = config.activities[plan.activity]
        path = village.route(world, world.actor.location_id, activity.location)
        if path is None or state.minute + 5 * len(path) + config.action_allowance > plan.starts_at:
            return reject("CANNOT_ARRIVE", "No feasible actual route to the shared start.")
        problem = _guard(world, rid, activity.location, plan.ends_at, require_present=False)
        if problem:
            return reject(*problem)
        plan.accepted.append(rid)
        return done("SOCIAL_ACCEPTED", "Accepted shared start; physical attendance is still required.")
    if action == "village_leisure":
        activity = config.activities.get(args["activity"])
        companions = args["companions"]
        if activity is None or not isinstance(companions, list) or any(type(item) is not str for item in companions):
            return reject("BAD_ACTIVITY", "Choose a configured activity and resident companion list.")
        participants = [rid, *companions]
        if len(set(participants)) != len(participants) or any(person not in state.residents for person in participants):
            return reject("BAD_PARTICIPANTS", "Participants must be unique real residents.")
        if not activity.min_participants <= len(participants) <= activity.max_participants:
            return reject("PARTICIPANT_COUNT", "The activity needs its configured number of participants.")
        if any(_active(social, person) for person in participants):
            return reject("SOCIAL_COMMITTED", "A participant is already committed to another activity.")
        if companions:
            # A companion's own prior, explicit band-style acceptance is required.
            return reject("INVITATION_REQUIRED", "Use a configured coordinated band plan for group activities; never volunteer another actor.")
        problem = _guard(world, rid, activity.location, state.minute + activity.duration)
        if problem:
            return reject(*problem)
        problem = _book_guard(world, rid, activity, social, config)
        if problem:
            return reject(*problem)
        problem = _practical_guard(world, rid, activity)
        if problem:
            return reject(*problem)
        if any(not _equipment_access(world, rid, eid, activity.location, config) for eid in activity.equipment):
            return reject("NO_EQUIPMENT", "Required existing equipment is unavailable.")
        problem = _supplies_guard(world, activity, participants)
        if problem:
            return reject(*problem)
        interval = _start(world, args["activity"], activity.kind, activity.location, activity.duration,
                          participants, activity.supplies, activity.equipment)
        return done("SOCIAL_STARTED", f"Committed actual activity until {village.clock_time(interval.ends_at)}.")
    if action == "village_band_invite":
        band = config.bands.get(args["band"])
        start = args["starts_at"]
        if band is None or rid != band.coordinator or world.actor.location_id != band.venue:
            return reject("NOT_COORDINATOR", "The real venue coordinator must invite members at the venue.")
        if args["mode"] not in {"rehearsal", "performance"} or type(start) is not int or start % 5:
            return reject("BAD_PLAN", "Choose rehearsal/performance and an absolute five-minute start time.")
        if start < state.minute + config.action_allowance or not band.earliest_minute <= start % 1440 <= band.latest_minute:
            return reject("BAD_START", "The start must be future and inside the configured evening window.")
        if any(plan.band == args["band"] and plan.status in {"invited", "active"} for plan in social.band_plans.values()):
            return reject("DUPLICATE_PLAN", "This band already has a pending or active commitment.")
        if any(plan.band == args["band"] and plan.mode == args["mode"]
               and plan.starts_at // 1440 == start // 1440 and plan.status == "completed"
               for plan in social.band_plans.values()):
            return reject("ALREADY_PLAYED", "This band has already completed that session type today.")
        plan = BandPlan(id=_id(social, "band"), band=args["band"], mode=args["mode"],
                        starts_at=start, ends_at=start + band.duration, invited=list(band.members))
        social.band_plans[plan.id] = plan
        return done("BAND_INVITED", f"Invitation {plan.id}; each member must accept, bring their real instrument and attend.")
    if action in {"village_band_respond", "village_band_attend"}:
        plan = social.band_plans.get(args["plan"])
        if plan is None or plan.status != "invited" or state.minute >= plan.starts_at:
            return reject("NO_INVITATION", "No open future invitation exists.")
        band = config.bands[plan.band]
        if action == "village_band_respond":
            if rid not in plan.invited or args["response"] not in {"accept", "decline"}:
                return reject("BAD_RESPONSE", "Only invited members may accept or decline.")
            if rid in plan.accepted or rid in plan.declined:
                return reject("ALREADY_RESPONDED", "This member's response is already persisted.")
            if args["response"] == "decline":
                plan.declined.append(rid)
                _cancel_plan(world, plan, f"{rid} declined the optional band invitation.")
                return done("BAND_DECLINED", "Declined; no attendance or skill invented.")
            if not _equipment_access(world, rid, band.members[rid].instrument, world.actor.location_id, config):
                return reject("NO_INSTRUMENT", "Your actual configured instrument is not accessible.")
        elif rid in plan.invited or rid in plan.audience:
            return reject("ALREADY_LISTED", "Members use their invitation; audience attendance cannot be duplicated.")
        path = village.route(world, world.actor.location_id, band.venue)
        if path is None or state.minute + 5 * len(path) + config.action_allowance > plan.starts_at:
            return reject("CANNOT_ARRIVE", "Insufficient real travel time to reach the agreed start.")
        problem = _guard(world, rid, band.venue, plan.ends_at, require_present=False)
        if problem:
            return reject(*problem)
        if plan.starts_at // 1440 != state.minute // 1440:
            return reject("PLAN_TOO_FAR", "Accept only today's evening commitment after protecting today's meals.")
        if args.get("response") == "accept":
            plan.accepted.append(rid)
        else:
            plan.audience.append(rid)
        return done("BAND_ACCEPTED", "Committed to the shared start; actual presence will be checked, never teleported.")
    qid = args["quest"]
    template, quest = config.quests.get(qid), social.quests.get(qid)
    if template is None or rid not in template.eligible:
        return reject("NO_QUEST", "No eligible configured quest exists.")
    response = args["response"]
    if response not in {"accept", "decline", "defer", "abandon"}:
        return reject("BAD_RESPONSE", "Choose accept, decline, defer, or abandon.")
    if quest.status in {"completed", "declined", "abandoned", "expired", "failed"}:
        return reject("QUEST_CLOSED", "This durable quest outcome is final.")
    if state.minute < template.offer_at or any(social.quests[key].status != "completed" for key in template.prerequisites):
        return reject("QUEST_UNAVAILABLE", "The real offer time or prerequisite outcome has not been reached.")
    if response == "abandon":
        if quest.status != "accepted" or quest.assignee != rid:
            return reject("NOT_ASSIGNEE", "Only the actual accepting resident can abandon this quest.")
        _refund(world, template, quest)
        quest.status = "abandoned"
        return done("QUEST_ABANDONED", "Abandoned optional quest; escrow refunded and no reward credited.")
    if quest.status not in {"offered", "deferred"}:
        return reject("QUEST_COMMITTED", "This quest already has a persisted assignee.")
    if village.resident_location(world, template.giver) != world.actor.location_id:
        return reject("GIVER_NOT_PRESENT", "Discuss the optional offer with the actual co-located giver.")
    if quest.status == "deferred" and state.minute < quest.defer_until:
        return reject("QUEST_DEFERRED", "The requested deferral has not elapsed.")
    if response == "decline":
        quest.status = "declined"
        return done("QUEST_DECLINED", "Declined this optional quest.")
    if response == "defer":
        until = args["until"]
        if type(until) is not int or until % 5 or not state.minute < until < template.expires_at:
            return reject("BAD_DEFERRAL", "Defer to a future five-minute time before the real expiry.")
        quest.status, quest.defer_until = "deferred", until
        return done("QUEST_DEFERRED", f"Deferred until {village.clock_time(until)}; no work or payment inferred.")
    if args["until"] is not None:
        return reject("BAD_ARGUMENTS", "until must be null except for deferral.")
    obj = template.objective
    if obj.kind == "production" and obj.target != rid:
        return reject("NOT_WORKER", "Only the configured worker can accept their own production objective.")
    if obj.kind == "chapter" and config.projects[obj.project].owner != rid:
        return reject("NOT_PROJECT_OWNER", "Only the actual writer can accept a chapter objective.")
    value = _quest_value(world, template, rid)
    if obj.kind in {"item_at", "chapter", "repair", "discovery_shared"} and value:
        return reject("ALREADY_DONE", "An already-satisfied objective cannot be rewarded as new work.")
    if obj.kind == "repair" and not world.state.entities[obj.target].broken:
        return reject("ALREADY_DONE", "The actual instrument is already healthy; no new repair is required.")
    if obj.kind == "band" and config.bands[obj.target].coordinator != rid:
        return reject("NOT_COORDINATOR", "Only the actual venue coordinator can accept the gathering objective.")
    if state.coins[template.giver] < template.reward:
        return reject("REWARD_UNFUNDED", "The giver lacks the real funds; no unfunded reward promise.")
    state.coins[template.giver] -= template.reward
    quest.reward_reserved = template.reward
    quest.assignee, quest.accepted_at, quest.baseline, quest.status = rid, state.minute, value, "accepted"
    quest.evidence.append(_event(world, rid, "quest_accepted", qid,
                                 f"Accepted optional objective; reserved {template.reward} real giver coins.",
                                 world.actor.location_id))
    return done("QUEST_ACCEPTED", "Accepted optional multi-day objective with funded, exactly-once reward escrow.")


def observation(world: "World") -> dict[str, Any]:
    _, state, config, social = _context(world)
    if config is None:
        return {}
    rid, location = world.actor_id, world.actor.location_id
    interests = config.interests.get(rid)
    activities = {}
    for key, activity in config.activities.items():
        problem = _guard(world, rid, activity.location, state.minute + activity.duration)
        problem = problem or _book_guard(world, rid, activity, social, config)
        problem = problem or _practical_guard(world, rid, activity)
        problem = problem or _supplies_guard(world, activity, [rid])
        if not problem and any(not _equipment_access(world, rid, eid, activity.location, config)
                               for eid in activity.equipment):
            problem = "NO_EQUIPMENT", "Required real equipment is unavailable."
        activities[key] = {**activity.model_dump(), "available": problem is None,
                           "reason": problem[0] if problem else None,
                           "outputs": RECIPES[activity.recipe][1].copy() if activity.recipe else {},
                           "preferred": bool(interests and set(activity.interests)
                                             & set(interests.solo + interests.social))}
    return {"social": {
        "interests": interests.model_dump() if interests else {},
        "personal": social.residents[rid].model_dump(),
        "activities": activities,
        "active": _active(social, rid).model_dump() if _active(social, rid) else None,
        "projects": {key: {"title": project.title,
                          "chapters": {chapter: progress.model_dump()
                                       for chapter, progress in social.projects[key].items()}}
                     for key, project in config.projects.items() if project.owner == rid},
        "band_plans": [plan.model_dump() for plan in social.band_plans.values()
                       if rid in plan.invited + plan.audience or config.bands[plan.band].venue == location],
        "social_plans": [plan.model_dump() for plan in social.social_plans.values() if rid in plan.invited],
        "quests": {key: {**social.quests[key].model_dump(), "template": template.model_dump()}
                   for key, template in config.quests.items()
                   if rid == social.quests[key].assignee
                   or (rid in template.eligible and village.resident_location(world, template.giver) == location
                       and template.offer_at <= state.minute
                       and all(social.quests[item].status == "completed" for item in template.prerequisites))},
        "history": [item.model_dump() for item in social.intervals if rid in item.participants][-12:],
        "policy": "Leisure, rest, books, music and quests are optional. Use real sites, supplies and instruments. "
                  "Never volunteer companions. Invitations require each actor's acceptance and real attendance. "
                  "Meals, work, animal care, and walking home before sleep take precedence.",
    }}


def actions(world: "World") -> list[dict[str, Any]]:
    _, state, config, social = _context(world)
    if config is None:
        return []
    rid = world.actor_id
    result = []

    def add(verb, description, properties):
        result.append({"id": verb, "description": description, "targets": [],
                       "arguments_schema": {"type": "object", "properties": properties,
                                            "required": list(properties), "additionalProperties": False},
                       "availability": "available", "reason": None})

    def enum(values):
        return {"type": "string", "enum": list(values)}

    if _active(social, rid):
        add("village_social_cancel", "Explicitly interrupt your optional activity; no completion credit.", {})
        return result
    if config.activities:
        add("village_leisure", "Commit a real timed solo activity, protecting supplies, meals and the route home.",
            {"activity": enum(config.activities), "companions": {"type": "array", "maxItems": 0, "items": {"type": "string"}}})
    group = [key for key, activity in config.activities.items() if activity.max_participants > 1]
    others = [other for other in state.residents if other != rid]
    if group and others:
        add("village_social_invite", "Invite real companions to a coordinated timed social activity.",
            {"activity": enum(group), "companions": {"type": "array", "minItems": 1, "uniqueItems": True,
                                                    "items": enum(others)},
             "starts_at": {"type": "integer", "minimum": state.minute + config.action_allowance, "multipleOf": 5}})
    groups = [plan.id for plan in social.social_plans.values()
              if plan.status == "invited" and rid in plan.invited and rid not in plan.accepted]
    if groups:
        add("village_social_respond", "Choose to accept or decline; actual attendance is required.",
            {"plan": enum(groups), "response": enum(["accept", "decline"])})
    bands = [key for key, band in config.bands.items() if band.coordinator == rid]
    if bands:
        add("village_band_invite", "Offer a coordinated evening session; members decide for themselves.",
            {"band": enum(bands), "mode": enum(["rehearsal", "performance"]),
             "starts_at": {"type": "integer", "minimum": state.minute + config.action_allowance, "multipleOf": 5}})
    pending = [plan.id for plan in social.band_plans.values() if plan.status == "invited" and plan.starts_at > state.minute]
    invited = [pid for pid in pending if rid in social.band_plans[pid].invited]
    audience = [pid for pid in pending if rid not in social.band_plans[pid].invited]
    if invited:
        add("village_band_respond", "Accept or decline your own invitation; bring the existing instrument.",
            {"plan": enum(invited), "response": enum(["accept", "decline"])})
    if audience:
        add("village_band_attend", "Choose to listen; actual co-located attendance is required.",
            {"plan": enum(audience)})
    quests = [key for key, template in config.quests.items() if rid in template.eligible]
    if quests:
        add("village_quest", "Discuss and accept, decline, defer or abandon a funded optional quest.",
            {"quest": enum(quests), "response": enum(["accept", "decline", "defer", "abandon"]),
             "until": {"type": ["integer", "null"], "minimum": 0, "multipleOf": 5}})
    return result


def monitor(world: "World") -> dict[str, Any]:
    """Typed intervals become flattened actual-time dashboard rows."""
    _, state, config, social = _context(world)
    if config is None:
        return {"activities": [], "projects": [], "quests": []}
    return {
        "activities": [
            {"actor": rid, "label": interval.kind, "location": interval.location,
             "start": interval.starts_at,
             "end": interval.actual_end if interval.actual_end is not None else interval.ends_at,
             "status": interval.status}
            for interval in social.intervals for rid in interval.participants
        ],
        "projects": [
            {"id": key, "owner": project.owner, "title": project.title,
             "progress": sum(sum(getattr(progress, field) is not None for field in
                                 ("observed_at", "organized_at", "drafted_at", "revised_at"))
                             for progress in social.projects[key].values()),
             "required": 4 * len(project.chapters),
             "status": "completed" if all(progress.revised_at is not None
                                         for progress in social.projects[key].values()) else "in_progress"}
            for key, project in config.projects.items()
        ],
        "quests": [
            {"id": key, "actor": social.quests[key].assignee or template.giver,
             "title": template.title, "progress": social.quests[key].progress,
             "required": template.objective.quantity, "status": social.quests[key].status}
            for key, template in config.quests.items()
        ],
    }


def example_config() -> dict[str, Any]:
    """Fresh Millhaven example; install ``example_fixtures()`` in the new scenario.

    Music uses existing portable utensils as percussion, not invented instruments.
    Nothing in this function installs configuration or changes a running world.
    """
    interests = {
        "torvin": ("metalwork", "storytelling"), "bren": ("animals", "games"),
        "mara": ("reading", "music"), "elsie": ("recipes", "music"),
        "hobb": ("woodcraft", "storytelling"), "jory": ("recipes", "music"),
        "ada": ("gardening", "games"), "nella": ("maps", "visiting"),
        "rowan": ("plants", "storytelling"), "garrick": ("reading", "games"),
        "iona": ("plants", "music"), "finn": ("rivers", "visiting"),
    }
    activities = {
        "quiet-rest": {"kind": "rest", "location": "cottages", "duration": 15},
        "inn-reading": {"kind": "reading", "location": "tavern", "duration": 20,
                        "equipment": ["guest-book"], "skill": "reading", "interests": ["reading"]},
        "inn-stories": {"kind": "storytelling", "location": "tavern", "duration": 20,
                        "min_participants": 2, "max_participants": 4, "interests": ["storytelling"]},
        "shared-tea": {"kind": "shared_tea", "location": "tavern", "duration": 15,
                       "supplies": {"tea": 1}, "min_participants": 2, "max_participants": 4,
                       "interests": ["visiting"]},
    }
    for key, kind, location in (
        ("herb-observation", "field_observe", "woods"),
        ("herb-notes", "organize_notes", "cottages"),
        ("herb-draft", "draft", "cottages"),
        ("herb-revision", "revise", "cottages"),
    ):
        activities[key] = {"kind": kind, "location": location, "duration": 20,
                           "project": "iona-herbal-book", "chapter": "woodland-plant",
                           "interests": ["plants"], "skill": "herbal-writing"}
    example = {
        "interests": {rid: {"solo": [solo], "social": [social]} for rid, (solo, social) in interests.items()},
        "activities": activities,
        "projects": {"iona-herbal-book": {
            "owner": "iona", "title": "Iona's herbal book",
            "chapters": {"woodland-plant": {"site": "woods", "specimen": "wild-herb"}},
        }},
        "bands": {"inn-percussion": {
            "coordinator": "elsie", "venue": "tavern", "duration": 20,
            "members": {
                "iona": {"role": "mortar rhythm", "instrument": "mortar"},
                "elsie": {"role": "tray percussion", "instrument": "serving-tray"},
                "jory": {"role": "wooden peel rhythm", "instrument": "bread-peel"},
            },
        }},
        "quests": {
            "woodland-chapter": {
                "giver": "elsie", "title": "Document a fictional local plant", "eligible": ["iona"],
                "objective": {"kind": "chapter", "project": "iona-herbal-book", "target": "woodland-plant"},
                "offer_at": 0, "expires_at": 4320, "reward": 1,
            },
            "share-stories": {
                "giver": "elsie", "title": "Share a story with neighbours", "eligible": ["rowan", "hobb", "torvin"],
                "objective": {"kind": "activity", "target": "inn-stories"},
                "offer_at": 0, "expires_at": 2880, "reward": 0,
            },
        },
    }
    example["activities"].update({
        "forge-reading": {"kind": "reading", "location": "forge", "duration": 15,
                          "equipment": ["forge-manual"], "interests": ["metalwork"]},
        "stable-reading": {"kind": "reading", "location": "stable", "duration": 15,
                           "equipment": ["animal-notes"], "interests": ["animals"]},
        "mill-history": {"kind": "reading", "location": "village-square", "duration": 25,
                         "equipment": ["noticeboard"], "interests": ["reading"]},
        "inn-recipe-reading": {"kind": "reading", "location": "tavern", "duration": 15,
                               "equipment": ["recipe-notes"], "interests": ["recipes"]},
        "plank-practice": {"kind": "crafting", "location": "workshop", "duration": 20,
                           "equipment": ["workbench"], "supplies": {"timber": 1},
                           "recipe": "plank_practice", "interests": ["woodcraft"], "skill": "woodcraft"},
        "woodcraft-reading": {"kind": "reading", "location": "workshop", "duration": 15,
                              "equipment": ["woodcraft-notes"], "interests": ["woodcraft"]},
        "garden-reading": {"kind": "reading", "location": "farm", "duration": 15,
                           "equipment": ["garden-notes"], "interests": ["gardening"]},
        "garden-tending": {"kind": "gardening", "location": "farm", "duration": 20,
                           "equipment": ["watering-can"], "supplies": {"manure": 1},
                           "recipe": "garden_tending", "interests": ["gardening"], "skill": "gardening"},
        "map-reading": {"kind": "reading", "location": "village-square", "duration": 15,
                        "equipment": ["noticeboard"], "interests": ["maps"]},
        "plant-reading": {"kind": "reading", "location": "cottages", "duration": 15,
                          "equipment": ["plant-notes"], "interests": ["plants"]},
        "guard-reading": {"kind": "reading", "location": "village-square", "duration": 20,
                          "equipment": ["noticeboard"], "interests": ["reading"]},
        "river-rest": {"kind": "rest", "location": "riverbank", "duration": 15,
                       "interests": ["rivers"]},
        "fishing-survey": {"kind": "fishing_survey", "location": "riverbank", "duration": 20,
                           "equipment": ["fishing-rod"], "discovery": "riverbank-spot",
                           "interests": ["rivers"], "skill": "fishing"},
        "share-fishing-spot": {"kind": "share_discovery", "location": "tavern", "duration": 15,
                              "min_participants": 2, "max_participants": 3,
                              "discovery": "riverbank-spot", "interests": ["visiting"]},
        "inn-games": {"kind": "games", "location": "tavern", "duration": 20,
                      "min_participants": 2, "max_participants": 4, "interests": ["games"]},
        "inn-visiting": {"kind": "visiting", "location": "tavern", "duration": 15,
                         "min_participants": 2, "max_participants": 4, "interests": ["visiting"]},
        "music-stories": {"kind": "storytelling", "location": "tavern", "duration": 20,
                          "min_participants": 2, "max_participants": 4, "interests": ["music"]},
        "test-bread-recipe": {"kind": "recipe_test", "location": "bakery", "duration": 30,
                              "project": "jory-recipe", "chapter": "festival-bread",
                              "equipment": ["oven"], "recipe": "bread_test", "supplies": {"flour": 1},
                              "interests": ["recipes"], "skill": "baking"},
        "repair-festival-drum": {"kind": "instrument_repair", "location": "forge", "duration": 30,
                                 "repair_target": "festival-drum", "equipment": ["anvil", "smith-hammer"],
                                 "supplies": {"iron": 1}, "interests": ["metalwork"], "skill": "metalwork"},
    })
    example["projects"]["jory-recipe"] = {
        "kind": "recipe", "owner": "jory", "title": "Jory's festival bread recipe",
        "chapters": {"festival-bread": {"site": "bakery", "specimen": "oven"}},
    }
    example["instrument_inventory"] = {"torvin": ["festival-drum"]}
    for key, kind in (("recipe-notes", "organize_notes"), ("recipe-draft", "draft"), ("recipe-revise", "revise")):
        example["activities"][key] = {
            "kind": kind, "location": "bakery", "duration": 20,
            "project": "jory-recipe", "chapter": "festival-bread", "interests": ["recipes"],
        }
    example["quests"].update({
        "festival-recipe": {
            "giver": "elsie", "title": "Develop a real tested bread recipe", "eligible": ["jory"],
            "objective": {"kind": "chapter", "project": "jory-recipe", "target": "festival-bread"},
            "expires_at": 4320, "reward": 1,
        },
        "repair-band-instrument": {
            "giver": "elsie", "title": "Repair the actual damaged festival drum", "eligible": ["torvin"],
            "objective": {"kind": "repair", "target": "festival-drum"}, "expires_at": 2880, "reward": 1,
        },
        "fishing-spot-help": {
            "giver": "elsie", "title": "Survey and share a real fishing spot", "eligible": ["finn"],
            "objective": {"kind": "discovery_shared", "target": "riverbank-spot"},
            "expires_at": 4320, "reward": 1,
        },
        "arrange-band": {
            "giver": "nella", "title": "Arrange an actually attended evening performance", "eligible": ["elsie"],
            "objective": {"kind": "band", "target": "inn-percussion"},
            "expires_at": 2880, "reward": 1,
        },
    })
    return example


def example_fixtures() -> dict[str, dict[str, Any]]:
    """Explicit new-scenario object definitions; never installed in live state."""
    fixtures = {
        "wild-herb": {"name": "Woodland herb", "kind": "fixture",
                      "description": "A fictional local plant for observations.", "location": "woods"},
        "festival-drum": {"name": "Damaged festival drum", "kind": "item",
                          "description": "A real damaged drum awaiting a metal fitting repair.",
                          "location": "forge", "takable": True, "health": 0, "max_health": 100},
    }
    for eid, name, location in (
        ("forge-manual", "Metalwork practice manual", "forge"),
        ("animal-notes", "Stable animal notes", "stable"),
        ("recipe-notes", "Inn recipe notes", "tavern"),
        ("garden-notes", "Garden care notes", "farm"),
        ("plant-notes", "Local plant notes", "cottages"),
        ("woodcraft-notes", "Woodcraft practice notes", "workshop"),
    ):
        fixtures[eid] = {
            "name": name, "kind": "item", "description": f"An actual readable collection: {name}.",
            "location": location, "readable_text": f"Fictional Millhaven {name.lower()}; not real-world medical advice.",
        }
    return fixtures

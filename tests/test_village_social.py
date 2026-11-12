"""Clock, physical evidence, consent, and recovery regressions for optional leisure."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from npc_sim import village, village_social, village_turns
from npc_sim.models import Entity
from npc_sim.village_social_models import VillageSocialConfig, VillageSocialState
from npc_sim.world import World
from test_village_expanded import scenario
from test_world import act


def configuration():
    return VillageSocialConfig.model_validate({
        "interests": {
            "iona": {"solo": ["plants"], "social": ["music"], "companions": ["elsie"]},
            "elsie": {"solo": ["reading"], "social": ["music"], "companions": ["iona"]},
        },
        "activities": {
            "rest": {"kind": "rest", "location": "cottages", "duration": 15},
            "read": {"kind": "reading", "location": "tavern", "duration": 20,
                     "equipment": ["guest-book"], "skill": "reading"},
            "tea": {"kind": "shared_tea", "location": "tavern", "duration": 15,
                    "supplies": {"tea": 1}, "min_participants": 2, "max_participants": 3},
            "observe": {"kind": "field_observe", "location": "woods", "duration": 15,
                        "project": "herbal-book", "chapter": "local-plant", "interests": ["plants"]},
            "notes": {"kind": "organize_notes", "location": "cottages", "duration": 15,
                      "project": "herbal-book", "chapter": "local-plant"},
            "draft": {"kind": "draft", "location": "cottages", "duration": 20,
                      "project": "herbal-book", "chapter": "local-plant"},
            "revise": {"kind": "revise", "location": "cottages", "duration": 20,
                       "project": "herbal-book", "chapter": "local-plant"},
            "craft": {"kind": "crafting", "location": "cottages", "duration": 15,
                      "supplies": {"herbs": 1}, "skill": "crafting"},
        },
        "projects": {"herbal-book": {"owner": "iona", "title": "Iona's herbal book",
                                    "chapters": {"local-plant": {"site": "woods", "specimen": "wild-herb"}}}},
        "bands": {"inn-band": {"coordinator": "elsie", "venue": "tavern",
                               "members": {"iona": {"role": "fiddle", "instrument": "fiddle"},
                                           "jory": {"role": "percussion", "instrument": "drum"}},
                               "duration": 20, "fee_per_member": 2, "payer": "elsie"}},
        "quests": {
            "book": {"giver": "elsie", "title": "Document a local plant", "eligible": ["iona"],
                     "objective": {"kind": "chapter", "target": "local-plant", "project": "herbal-book"},
                     "expires_at": 5000, "reward": 3},
            "return-tool": {"giver": "elsie", "title": "Bring the herb pouch", "eligible": ["iona"],
                            "objective": {"kind": "item_at", "target": "herb-pouch", "location": "tavern"},
                            "expires_at": 2800, "reward": 2},
            "make-tea": {"giver": "elsie", "title": "Make two real batches", "eligible": ["iona"],
                         "objective": {"kind": "production", "target": "iona", "quantity": 2},
                         "expires_at": 2800, "reward": 2},
        },
    })


@pytest.fixture
def cast():
    value = scenario()
    value.village.start_minute = 900
    value.village.expanded_rules = False
    value.village.goal_deadlines = {}
    value.village.daily_life = True
    value.village.time_based_turns = True
    value.village.breakdown_minute = 99990
    value.village.stocks["tavern"]["tea"] = 8
    value.entities["wild-herb"] = Entity(name="Wild herb", kind="fixture", description="A fictional local plant.", location="woods")
    for eid in ("fiddle", "drum"):
        value.entities[eid] = Entity(name=eid.title(), kind="item", description="A real configured instrument.",
                                     location="tavern", takable=True)
    value.village.social = configuration()
    for agent in value.agents:
        agent.body.capacity = 4
        agent.start_location = value.village.residents[agent.id].home
    path = Path(__file__).parent / f".social-test-{uuid4().hex}.json"
    world = World(value, path, "social-test", "iona", "session-iona")
    world.state.village.social = village_social.initial_state(value)
    worlds = {agent.id: world.bind(agent.id, f"session-{agent.id}") for agent in value.agents}
    yield worlds
    for file in path.parent.glob(f"{path.stem}*"):
        if file.is_file():
            file.unlink()


def place(cast, rid, location):
    """Fixture setup only; activity code itself must never move a resident."""
    cast[rid].actor.location_id = location
    cast[rid].state.village.residents[rid].available_at = cast[rid].state.village.minute


def tick(cast, target):
    world = cast["iona"]
    while world.state.village.minute < target:
        village.advance(world, 5)
        village_social.advance(world)


def leisure(cast, aid, rid="iona"):
    return village_social.apply(cast[rid], "village_leisure", {"activity": aid, "companions": []})


def feed(cast):
    tick(cast, 1140)
    for rid in cast:
        place(cast, rid, "tavern")
        life = cast[rid].state.village.residents[rid].life
        life.meals["0:daily"].status = "eaten"
        life.meals["0:daily"].source = "fixture:actual meal"
        life.hungry = False


def quest(cast, qid, response="accept", until=None):
    return village_social.apply(cast["iona"], "village_quest",
                               {"quest": qid, "response": response, "until": until})


def band(cast, start=1180, mode="performance"):
    return village_social.apply(cast["elsie"], "village_band_invite",
                               {"band": "inn-band", "mode": mode, "starts_at": start})


def test_real_duration_skips_calls_and_survives_json_reload(cast):
    world = cast["iona"]
    assert leisure(cast, "rest")[0] == "APPLIED"
    assert village_social.blocked_until(world, "iona") == 915
    assert village_social.action_blocker(world, "go")[0] == "SOCIAL_COMMITTED"
    assert village_social.action_blocker(world, "look") is None
    tick(cast, 910)
    assert world.state.village.social.intervals[0].status == "active"
    world.state.village.social = VillageSocialState.model_validate_json(world.state.village.social.model_dump_json())
    world.save()
    reloaded = World(world.scenario, world.path, "social-test", "iona", "session-iona")
    assert reloaded.state.village.social.intervals[0].status == "active"
    tick(cast, 915)
    interval = world.state.village.social.intervals[0]
    assert interval.status == "completed" and interval.actual_end == 915
    assert village_social.blocked_until(world, "iona") is None
    before = len(world.state.village.events)
    village_social.advance(world)
    assert len(world.state.village.events) == before
    assert village_social.monitor(world)["activities"][0]["end"] == 915


def test_tavern_leisure_must_finish_by_inn_closing(cast):
    world = cast["iona"]
    feed(cast)
    world.scenario.village.residents["iona"].bed_minute = 1380
    tick(cast, 1295)
    assert leisure(cast, "read")[0] == "APPLIED"
    tick(cast, 1315)
    assert world.state.village.social.intervals[-1].status == "completed"
    assert leisure(cast, "read")[1] == "INN_CLOSED"
    tick(cast, 1320)
    assert leisure(cast, "read")[1] == "INN_CLOSED"


def test_tavern_band_cannot_be_scheduled_past_closing(cast):
    world = cast["elsie"]
    place(cast, "elsie", "tavern")
    tick(cast, 1270)
    world.state.village.residents["elsie"].life.meals["0:daily"].status = "eaten"
    world.state.village.residents["elsie"].life.hungry = False
    config = world.state.village.social.config.bands["inn-band"]
    config.latest_minute = 1320
    assert band(cast, start=1300)[0] == "APPLIED"
    assert band(cast, start=1305)[1] == "INN_CLOSED"


def test_meals_bedtime_missing_material_equipment_and_commitments(cast):
    world = cast["iona"]
    world.state.village.stocks["cottages"]["herbs"] = 0
    assert leisure(cast, "craft")[1] == "NO_SUPPLIES"
    place(cast, "iona", "tavern")
    world.state.entities["guest-book"].location = "bakery"
    assert leisure(cast, "read")[1] == "NO_EQUIPMENT"
    world.state.entities["guest-book"].location = "tavern"
    tick(cast, 1125)
    assert leisure(cast, "read")[1] == "MEAL_CONFLICT"
    tick(cast, 1140)
    assert leisure(cast, "read")[1] == "MEAL_DUE"
    feed(cast)
    bedtime = world.scenario.village.residents["iona"].bed_minute
    tick(cast, bedtime - 20)
    assert leisure(cast, "read")[1] == "BEDTIME_CONFLICT"
    world.state.village.residents["iona"].timber_route = True
    assert leisure(cast, "read")[1] == "WORK_COMMITTED"


def test_interruptions_never_create_book_or_skill_progress(cast):
    world = cast["iona"]
    assert leisure(cast, "craft")[0] == "APPLIED"
    initial_stock = world.state.village.stocks["cottages"]["herbs"]
    tick(cast, 905)
    assert village_social.apply(world, "village_social_cancel", {})[0] == "APPLIED"
    tick(cast, 915)
    assert world.state.village.social.residents["iona"].skills == {}
    assert world.state.village.social.intervals[0].actual_end == 905
    assert world.state.village.stocks["cottages"]["herbs"] == initial_stock
    place(cast, "iona", "woods")
    assert leisure(cast, "observe")[0] == "APPLIED"
    world.state.entities["wild-herb"].location = "cottages"
    tick(cast, 920)
    assert world.state.village.social.projects["herbal-book"]["local-plant"].observed_at is None
    assert world.state.village.social.intervals[-1].status == "interrupted"


def test_book_verified_order_multiday_and_exactly_once_reward(cast):
    world = cast["iona"]
    place(cast, "iona", "tavern")
    place(cast, "elsie", "tavern")
    initial_total = sum(world.state.village.coins.values())
    giver, writer = world.state.village.coins["elsie"], world.state.village.coins["iona"]
    assert quest(cast, "book")[0] == "APPLIED"
    place(cast, "iona", "cottages")
    assert leisure(cast, "draft")[1] == "NOTES_REQUIRED"
    assert leisure(cast, "observe")[1] == "NOT_PRESENT"
    place(cast, "iona", "woods")
    assert leisure(cast, "observe")[0] == "APPLIED"
    tick(cast, 915)
    assert leisure(cast, "observe")[1] == "ALREADY_RECORDED"
    place(cast, "iona", "cottages")
    assert leisure(cast, "notes")[0] == "APPLIED"
    tick(cast, 930)
    # Persist/reload across days without re-awarding the earlier operations.
    world.state.village.social = VillageSocialState.model_validate_json(world.state.village.social.model_dump_json())
    tick(cast, 1980)
    for actor in cast.values():
        life = actor.state.village.residents[actor.actor_id].life
        life.last_woke = 1980
        life.hungry = False
    assert leisure(cast, "draft")[0] == "APPLIED"
    tick(cast, 2000)
    assert leisure(cast, "revise")[0] == "APPLIED"
    tick(cast, 2020)
    progress = world.state.village.social.projects["herbal-book"]["local-plant"]
    assert progress.revised_at == 2020
    assert quest(cast, "book")[1] == "QUEST_CLOSED"
    for _ in range(3):
        village_social.record_action(world, "say", {"text": "I wrote a book"}, "APPLIED")
    assert world.state.village.coins["elsie"] == giver - 3
    assert world.state.village.coins["iona"] == writer + 3
    assert sum(world.state.village.coins.values()) == initial_total
    assert village_social.monitor(world)["projects"][0]["progress"] == 4


def test_group_consent_actual_attendance_consumption_relationship_bounds(cast):
    feed(cast)
    world = cast["iona"]
    initial = world.state.village.stocks["tavern"]["tea"]
    assert village_social.apply(world, "village_social_invite",
                               {"activity": "tea", "companions": ["elsie"], "starts_at": 1155})[0] == "APPLIED"
    pid = next(iter(world.state.village.social.social_plans))
    assert village_social.apply(cast["elsie"], "village_social_respond",
                               {"plan": pid, "response": "accept"})[0] == "APPLIED"
    tick(cast, 1155)
    assert village_social.blocked_until(world, "elsie") == 1170
    assert world.state.village.stocks["tavern"]["tea"] == initial - 2
    tick(cast, 1170)
    assert world.state.village.residents["iona"].trust["elsie"] <= world.scenario.village.social.relationship_cap
    assert world.state.village.social.social_plans[pid].status == "completed"
    assert village_social.apply(world, "village_leisure", {"activity": "tea", "companions": ["elsie"]})[1] == "INVITATION_REQUIRED"


def test_band_missing_members_and_instruments_cancel_without_rewards(cast):
    feed(cast)
    assert band(cast)[0] == "APPLIED"
    world = cast["iona"]
    pid = next(iter(world.state.village.social.band_plans))
    assert village_social.apply(world, "village_band_respond", {"plan": pid, "response": "accept"})[0] == "APPLIED"
    tick(cast, 1180)
    assert world.state.village.social.band_plans[pid].status == "cancelled"
    assert world.state.village.social.residents["iona"].skills == {}
    assert band(cast, 1200)[0] == "APPLIED"
    pid = list(world.state.village.social.band_plans)[-1]
    world.state.entities["drum"].location = "bakery"
    assert village_social.apply(cast["jory"], "village_band_respond", {"plan": pid, "response": "accept"})[1] == "NO_INSTRUMENT"


def test_band_real_members_audience_skill_and_funded_payment_once(cast):
    feed(cast)
    world = cast["iona"]
    initial = world.state.village.coins.copy()
    assert band(cast)[0] == "APPLIED"
    pid = next(iter(world.state.village.social.band_plans))
    for rid in ("iona", "jory"):
        assert village_social.apply(cast[rid], "village_band_respond", {"plan": pid, "response": "accept"})[0] == "APPLIED"
    assert village_social.apply(cast["ada"], "village_band_attend", {"plan": pid})[0] == "APPLIED"
    tick(cast, 1180)
    assert village_social.blocked_until(world, "ada") == 1200
    tick(cast, 1200)
    assert world.state.village.social.band_plans[pid].paid
    assert world.state.village.social.residents["iona"].skills["music"] == 1
    assert "music" not in world.state.village.social.residents["ada"].skills
    assert world.state.village.coins["elsie"] == initial["elsie"] - 4
    assert world.state.village.coins["jory"] == initial["jory"] + 2
    world.state.village.social = VillageSocialState.model_validate_json(world.state.village.social.model_dump_json())
    village_social.advance(world)
    assert world.state.village.coins["jory"] == initial["jory"] + 2
    assert band(cast, 1210)[1] == "ALREADY_PLAYED"


def test_quest_item_requires_actual_assignee_action_not_claim(cast):
    world = cast["iona"]
    place(cast, "iona", "tavern")
    place(cast, "elsie", "tavern")
    assert quest(cast, "return-tool")[0] == "APPLIED"
    village_social.record_action(world, "say", {"text": "I returned the shovel"}, "APPLIED")
    assert world.state.village.social.quests["return-tool"].progress == 0
    world.state.entities["herb-pouch"].location = "tavern"
    village_social.advance(world)
    assert world.state.village.social.quests["return-tool"].status == "accepted"
    # Actual engine take/drop actions provide the physical objective and actor identity.
    result = act(world, "take", target="herb-pouch")
    assert result["outcome"] == "APPLIED", result
    tick(cast, world.state.village.residents["iona"].available_at)
    assert act(world, "drop", target="herb-pouch")["outcome"] == "APPLIED"
    village_social.record_action(world, "drop", {"target": "herb-pouch"}, "APPLIED")
    assert world.state.village.social.quests["return-tool"].status == "completed"


def test_quest_deferral_abandon_expiry_and_unfunded_giver(cast):
    world = cast["iona"]
    place(cast, "iona", "tavern")
    place(cast, "elsie", "tavern")
    assert quest(cast, "book", "defer", 930)[0] == "APPLIED"
    assert quest(cast, "book")[1] == "QUEST_DEFERRED"
    tick(cast, 930)
    giver = world.state.village.coins["elsie"]
    assert quest(cast, "book")[0] == "APPLIED"
    assert quest(cast, "book", "abandon")[0] == "APPLIED"
    assert world.state.village.coins["elsie"] == giver
    world.state.village.coins["elsie"] = 0
    assert quest(cast, "return-tool")[1] == "REWARD_UNFUNDED"
    assert world.state.village.social.quests["return-tool"].status == "offered"
    tick(cast, 2800)
    assert world.state.village.social.quests["return-tool"].status == "expired"


def test_configure_adds_finite_templates_persists_and_forbids_rewrites(cast):
    world = cast["iona"]
    config = configuration().model_dump()
    config["quests"]["rest-request"] = {
        "giver": "elsie", "title": "Take a useful break", "eligible": ["iona"],
        "objective": {"kind": "activity", "target": "rest"}, "expires_at": 3000,
    }
    village_social.configure(world, config)
    assert "rest-request" in world.state.village.social.quests
    restored = VillageSocialState.model_validate_json(world.state.village.social.model_dump_json())
    assert "rest-request" in restored.config.quests
    config["quests"]["rest-request"]["reward"] = 99
    with pytest.raises(ValueError, match="rewrite"):
        village_social.configure(world, config)
    config["quests"]["rest-request"]["objective"]["kind"] = "execute_code"
    with pytest.raises(ValidationError):
        village_social.configure(world, config)
    assert json.loads(restored.model_dump_json())["revision"] == 1


def test_invalid_reference_and_strict_config(cast):
    world = cast["iona"]
    config = configuration().model_dump()
    config["bands"]["inn-band"]["members"]["iona"]["instrument"] = "invented-lute"
    with pytest.raises(ValueError, match="existing object"):
        village_social.configure(world, config)
    with pytest.raises(ValidationError):
        VillageSocialConfig.model_validate({"skill_cap": "10"})


def test_band_present_acceptance_but_absent_start_interrupt_and_unfunded_fee(cast):
    feed(cast)
    world = cast["iona"]
    assert band(cast)[0] == "APPLIED"
    pid = next(iter(world.state.village.social.band_plans))
    for rid in ("iona", "jory"):
        assert village_social.apply(cast[rid], "village_band_respond", {"plan": pid, "response": "accept"})[0] == "APPLIED"
    place(cast, "jory", "bakery")
    tick(cast, 1180)
    assert world.state.village.social.band_plans[pid].status == "cancelled"
    assert world.state.village.social.intervals == []
    place(cast, "jory", "tavern")
    assert band(cast, 1200)[0] == "APPLIED"
    pid = list(world.state.village.social.band_plans)[-1]
    for rid in ("iona", "jory"):
        assert village_social.apply(cast[rid], "village_band_respond", {"plan": pid, "response": "accept"})[0] == "APPLIED"
    tick(cast, 1200)
    world.state.entities["fiddle"].location = "bakery"
    tick(cast, 1205)
    assert world.state.village.social.intervals[-1].status == "interrupted"
    assert not world.state.village.social.band_plans[pid].paid
    assert world.state.village.social.residents["iona"].skills == {}
    world.state.entities["fiddle"].location = "tavern"
    world.state.village.coins["elsie"] = 0
    assert band(cast, 1215)[0] == "APPLIED"
    pid = list(world.state.village.social.band_plans)[-1]
    for rid in ("iona", "jory"):
        assert village_social.apply(cast[rid], "village_band_respond", {"plan": pid, "response": "accept"})[0] == "APPLIED"
    tick(cast, 1215)
    assert world.state.village.social.band_plans[pid].status == "cancelled"


def test_book_without_specimen_and_breakfast_window(cast):
    world = cast["iona"]
    place(cast, "iona", "woods")
    world.state.entities["wild-herb"].hidden = True
    assert leisure(cast, "observe")[1] == "NO_SPECIMEN"
    world.state.entities["wild-herb"].hidden = False
    world.scenario.village.breakfast = True
    world.scenario.village.breakfast_delay = 30
    world.state.village.minute = world.scenario.village.residents["iona"].wake_minute + 20
    life = world.state.village.residents["iona"].life
    life.last_woke = world.scenario.village.residents["iona"].wake_minute
    world.state.village.residents["iona"].available_at = world.state.village.minute
    assert leisure(cast, "observe")[1] == "MEAL_CONFLICT"


def test_production_quest_needs_completed_engine_work_not_speech_or_load(cast):
    world = cast["iona"]
    place(cast, "iona", "tavern")
    place(cast, "elsie", "tavern")
    assert quest(cast, "make-tea")[0] == "APPLIED"
    village_social.record_action(world, "say", {"text": "I made two batches"}, "APPLIED")
    assert world.state.village.social.quests["make-tea"].progress == 0
    place(cast, "iona", "cottages")
    for _ in range(2):
        person = world.state.village.residents["iona"]
        tick(cast, max(person.available_at, person.next_work, world.state.village.minute))
        result = act(world, "village_work")
        assert result["outcome"] == "APPLIED", result
        village_social.record_action(world, "village_work", {}, "APPLIED")
    assert world.state.village.social.quests["make-tea"].status == "completed"
    assert world.state.village.social.production_counts["iona"] == 2
    coins = world.state.village.coins.copy()
    village_social.record_action(world, "village_work", {}, "APPLIED")
    assert world.state.village.coins == coins


def test_fresh_example_has_distinct_interests_existing_instruments_and_validates(cast):
    world = cast["iona"]
    for eid, definition in village_social.example_fixtures().items():
        world.scenario.entities[eid] = Entity.model_validate(definition)
    example = village_social.example_config()
    assert len(example["interests"]) == 12
    assert len({entry["solo"][0] for entry in example["interests"].values()}) > 6
    for band in example["bands"].values():
        assert all(member["instrument"] in world.state.entities for member in band["members"].values())
    world.scenario.village.social = VillageSocialConfig.model_validate(example)
    village_social.validate_config(world.scenario)


def test_actual_walk_to_field_and_scheduler_skip_committed_activity(cast):
    world = cast["iona"]
    path = village.route(world, world.actor.location_id, "woods")
    assert path
    for destination in path:
        origin = world.actor.location_id
        door = next(eid for eid, entity in world.state.entities.items()
                    if entity.kind == "door" and {entity.location, entity.destination} == {origin, destination})
        result = act(world, "go", target=door)
        assert result["outcome"] == "APPLIED", result
        tick(cast, world.state.village.residents["iona"].available_at)
    assert world.actor.location_id == "woods"
    assert leisure(cast, "observe")[0] == "APPLIED"
    end = village_social.blocked_until(world, "iona")
    for rid, person in world.state.village.residents.items():
        if rid != "iona":
            person.available_at = end
    assert village_turns.next_actor(world, stop_at_minute=end) is None
    assert world.state.village.social.intervals[-1].status == "completed"
    assert world.state.village.social.projects["herbal-book"]["local-plant"].observed_at == end


def install_example(cast):
    world = cast["iona"]
    for eid, definition in village_social.example_fixtures().items():
        entity = Entity.model_validate(definition)
        world.scenario.entities[eid] = entity
        world.state.entities[eid] = entity.model_copy(deep=True)
    values = world.state.village.social.config.model_dump()
    example = village_social.example_config()
    for field in ("interests", "activities", "projects", "bands", "quests", "instrument_inventory"):
        values[field].update(example[field])
    village_social.configure(world, values)


def respond_quest(cast, rid, key, response="accept"):
    return village_social.apply(cast[rid], "village_quest",
                               {"quest": key, "response": response, "until": None})


def test_example_every_resident_has_matching_optional_solo_and_social(cast):
    install_example(cast)
    config = cast["iona"].state.village.social.config
    for rid, interests in config.interests.items():
        assert any(activity.max_participants == 1 and set(activity.interests) & set(interests.solo)
                   for activity in config.activities.values()), rid
        assert any(activity.max_participants > 1 and set(activity.interests) & set(interests.social)
                   for activity in config.activities.values()), rid


def test_jory_recipe_consumes_actual_flour_produces_bread_and_persists(cast):
    install_example(cast)
    world = cast["jory"]
    place(cast, "jory", "bakery")
    place(cast, "elsie", "bakery")
    assert respond_quest(cast, "jory", "festival-recipe")[0] == "APPLIED"
    assert leisure(cast, "recipe-draft", "jory")[1] == "NOTES_REQUIRED"
    stock = world.state.village.stocks["bakery"]
    flour, bread = stock["flour"], stock["bread"]
    assert leisure(cast, "test-bread-recipe", "jory")[0] == "APPLIED"
    assert stock["flour"] == flour - 1 and stock["bread"] == bread
    tick(cast, 930)
    assert stock["bread"] == bread + 1
    assert world.state.village.social.intervals[-1].produced == {"bread": 1}
    assert leisure(cast, "test-bread-recipe", "jory")[1] == "ALREADY_RECORDED"
    for key, target in (("recipe-notes", 950), ("recipe-draft", 970), ("recipe-revise", 990)):
        assert leisure(cast, key, "jory")[0] == "APPLIED"
        tick(cast, target)
    assert world.state.village.social.quests["festival-recipe"].status == "completed"
    coins = world.state.village.coins.copy()
    world.state.village.social = VillageSocialState.model_validate_json(world.state.village.social.model_dump_json())
    village_social.advance(world)
    assert world.state.village.coins == coins


def test_real_instrument_repair_consumes_iron_and_rewards_once(cast):
    install_example(cast)
    world = cast["torvin"]
    place(cast, "torvin", "forge")
    place(cast, "iona", "forge")
    place(cast, "elsie", "forge")
    assert leisure(cast, "repair-festival-drum")[1] == "CAPABILITY_REQUIRED"
    assert respond_quest(cast, "torvin", "repair-band-instrument")[0] == "APPLIED"
    iron = world.state.village.stocks["forge"]["iron"]
    assert world.state.entities["festival-drum"].broken
    assert leisure(cast, "repair-festival-drum", "torvin")[0] == "APPLIED"
    assert world.state.village.stocks["forge"]["iron"] == iron - 1
    tick(cast, 930)
    assert not world.state.entities["festival-drum"].broken
    assert world.state.village.social.quests["repair-band-instrument"].status == "completed"
    assert leisure(cast, "repair-festival-drum", "torvin")[1] == "ALREADY_REPAIRED"
    coins = world.state.village.coins.copy()
    village_social.record_action(world, "say", {"text": "I repaired it again"}, "APPLIED")
    assert world.state.village.coins == coins


def test_finn_discovery_requires_actual_survey_and_shared_participation(cast):
    install_example(cast)
    world = cast["finn"]
    place(cast, "finn", "tavern")
    place(cast, "elsie", "tavern")
    assert respond_quest(cast, "finn", "fishing-spot-help")[0] == "APPLIED"
    assert village_social.apply(world, "village_social_invite",
                               {"activity": "share-fishing-spot", "companions": ["elsie"], "starts_at": 915})[0] == "APPLIED"
    pid = list(world.state.village.social.social_plans)[-1]
    assert village_social.apply(cast["elsie"], "village_social_respond",
                               {"plan": pid, "response": "accept"})[0] == "APPLIED"
    tick(cast, 915)
    assert world.state.village.social.social_plans[pid].status == "cancelled"
    place(cast, "finn", "riverbank")
    assert leisure(cast, "fishing-survey", "finn")[0] == "APPLIED"
    tick(cast, 935)
    feed(cast)
    assert village_social.apply(world, "village_social_invite",
                               {"activity": "share-fishing-spot", "companions": ["elsie"], "starts_at": 1155})[0] == "APPLIED"
    pid = list(world.state.village.social.social_plans)[-1]
    assert village_social.apply(cast["elsie"], "village_social_respond",
                               {"plan": pid, "response": "accept"})[0] == "APPLIED"
    tick(cast, 1170)
    discovery = world.state.village.social.residents["finn"].discoveries["riverbank-spot"]
    assert discovery.observed_at == 935 and discovery.shared_with == ["elsie"]
    assert world.state.village.social.quests["fishing-spot-help"].status == "completed"


def test_garden_and_craft_material_accounting_without_fabricated_harvest(cast):
    install_example(cast)
    world = cast["ada"]
    place(cast, "ada", "farm")
    assert leisure(cast, "garden-tending", "ada")[1] == "NO_SUPPLIES"
    world.state.village.stocks["farm"]["manure"] = 1
    grain = world.state.village.stocks["farm"]["grain"]
    assert leisure(cast, "garden-tending", "ada")[0] == "APPLIED"
    tick(cast, 920)
    assert world.state.village.stocks["farm"]["manure"] == 0
    assert world.state.village.stocks["farm"]["grain"] == grain
    assert world.state.village.social.residents["ada"].garden_care["farm"] == 1
    assert leisure(cast, "garden-tending", "ada")[1] == "ALREADY_TENDED"
    place(cast, "hobb", "workshop")
    stock = world.state.village.stocks["workshop"]
    timber, planks = stock["timber"], stock["planks"]
    assert leisure(cast, "plank-practice", "hobb")[0] == "APPLIED"
    tick(cast, 940)
    assert stock["timber"] == timber - 1 and stock["planks"] == planks + 1
    assert world.state.village.social.intervals[-1].produced == {"planks": 1}


def test_elsie_band_quest_needs_completed_attended_performance(cast):
    install_example(cast)
    world = cast["elsie"]
    place(cast, "elsie", "general-store")
    place(cast, "nella", "general-store")
    assert respond_quest(cast, "elsie", "arrange-band")[0] == "APPLIED"
    village_social.record_action(world, "say", {"text": "I arranged the band"}, "APPLIED")
    assert world.state.village.social.quests["arrange-band"].progress == 0
    feed(cast)
    # Carry the real instruments from their real owners' inventories.
    world.state.entities["mortar"].location = "iona"
    cast["iona"].actor.inventory.append("mortar")
    world.state.entities["bread-peel"].location = "jory"
    cast["jory"].actor.inventory.append("bread-peel")
    assert village_social.apply(world, "village_band_invite",
                               {"band": "inn-percussion", "mode": "performance", "starts_at": 1180})[0] == "APPLIED"
    pid = list(world.state.village.social.band_plans)[-1]
    for rid in ("iona", "elsie", "jory"):
        assert village_social.apply(cast[rid], "village_band_respond",
                                   {"plan": pid, "response": "accept"})[0] == "APPLIED"
    tick(cast, 1200)
    assert world.state.village.social.quests["arrange-band"].status == "completed"

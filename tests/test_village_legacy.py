from pathlib import Path

import pytest

from npc_sim import village, village_legacy
from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.world import World
from test_world import act


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def world(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-generations.json"))
    world = World(scenario, tmp_path / "world.json", "legacy", "rowan", "session-rowan")
    return world


def test_family_history_and_chronicle_are_personal(world):
    state = world.state.village
    assert state.legacy is not None
    assert "woodlot" in village_legacy.observation(world)["legacy"]["family"]["history"]
    assert village_legacy.observation(world)["legacy"]["chronicle"] == []
    assert village_legacy.apply(world, "village_read_chronicle", {})[1] == "NOT_AT_CHRONICLE"
    world.actor.location_id = "village-square"
    assert village_legacy.apply(world, "village_read_chronicle", {})[1] == "CHRONICLE_READ"
    assert len(village_legacy.observation(world)["legacy"]["chronicle"]) == 3
    assert state.legacy.learned["rowan"] == [0, 1, 2]


def test_reading_chronicle_through_world_action_and_observation(world):
    assert "village_read_chronicle" in {item["id"] for item in world.actions()}
    state = world.state.village
    state.minute = 480
    state.residents["rowan"].life.sleeping = False
    assert act(world, "village_read_chronicle")["code"] == "NOT_AT_CHRONICLE"
    world.actor.location_id = "village-square"
    assert act(world, "village_read_chronicle")["code"] == "CHRONICLE_READ"
    assert len(world.observe()["village"]["legacy"]["chronicle"]) == 3


def test_mill_events_newcomers_and_departures_survive_reload(world):
    state = world.state.village
    assert state.legacy is not None
    village.event(world, "mara", "batch_completed", "watermill", "Milled one real grain into flour.", "watermill")
    village_legacy.advance(world)
    assert state.legacy.chronicle[-1].kind == "batch_completed"
    count = len(state.legacy.chronicle)
    village_legacy.advance(world)
    assert len(state.legacy.chronicle) == count
    state.minute = 1440
    village_legacy.advance(world)
    assert "Lena Vale" in state.legacy.newcomers
    assert any(entry.kind == "arrival" for entry in state.legacy.chronicle)
    world.save()
    restored = World(world.scenario, world.path, "legacy", "rowan", "session-rowan")
    assert "Lena Vale" in restored.state.village.legacy.newcomers
    restored.state.village.minute = 4320
    village_legacy.advance(restored)
    assert "Lena Vale" not in restored.state.village.legacy.newcomers
    assert "Oren Pike" in restored.state.village.legacy.newcomers
    assert any(entry.kind == "departure" for entry in restored.state.village.legacy.chronicle)


def test_invalid_legacy_references_are_rejected(world):
    scenario = world.scenario.model_copy(deep=True)
    scenario.village.legacy.families.pop("rowan")
    with pytest.raises(ValueError, match="Every resident"):
        village_legacy.validate_config(scenario)


def test_read_and_share_only_personally_known_lessons(world):
    state = world.state.village
    village.event(world, "mara", "project_completed", "repair_mill", "Mill repaired.", "watermill")
    village_legacy.advance(world)
    index = len(state.legacy.chronicle) - 1
    assert village_legacy.active_lessons(world, "rowan") == []
    assert village_legacy.apply(world, "village_share_chronicle", {"target": "mara", "entry": index})[1] == "STORY_UNAVAILABLE"
    world.actor.location_id = "village-square"
    assert village_legacy.apply(world, "village_read_chronicle", {})[1] == "CHRONICLE_READ"
    assert village_legacy.active_lessons(world, "rowan")[0].source == "square chronicle"
    assert village_legacy.apply(world, "village_share_chronicle", {"target": "mara", "entry": index})[1] == "STORY_UNAVAILABLE"
    state.minute += 4 * 1440
    assert village_legacy.active_lessons(world, "rowan") == []
    world.save()
    restored = World(world.scenario, world.path, "legacy", "rowan", "session-rowan")
    assert restored.state.village.legacy.lessons["rowan"][0].entry == index
    assert village_legacy.active_lessons(restored, "rowan") == []


def test_explicit_handoff_preserves_only_bounded_family_assets(world, tmp_path):
    state = world.state.village
    state.coins["rowan"] = 47
    state.minute = 1440
    village_legacy.advance(world)
    target = World(world.scenario, tmp_path / "next.json", "next", "rowan", "next-rowan")
    village_legacy.successor_state(world.scenario, world.state, target, "a" * 32)
    legacy = target.state.village.legacy
    assert legacy.current["rowan"] == "Rina Woodward"
    assert legacy.lineage["rowan"] == ["Rowan"]
    assert legacy.inherited_coins["rowan"] == 20
    assert target.state.village.coins["rowan"] == target.scenario.village.coins["rowan"] + 20
    assert target.state.village.stocks == target.scenario.village.stocks
    assert legacy.handoff == {"save_id": "a" * 32, "source_world_id": "legacy"}
    target.save()
    restored = World(world.scenario, target.path, "next", "rowan", "next-rowan")
    assert restored.state.village.legacy.lineage["rowan"] == ["Rowan"]
    unrelated = World(world.scenario, tmp_path / "unrelated.json", "unrelated", "rowan", "unrelated-rowan")
    assert unrelated.state.village.legacy.handoff is None
    assert unrelated.state.village.legacy.current["rowan"] == "Rowan"


def test_private_diary_survives_reload_and_reaches_only_successor(world, tmp_path):
    state = world.state.village
    note = "I found that checking the woodlot before accepting orders saves a wasted trip."
    state.minute = 480
    state.residents["rowan"].life.sleeping = False
    assert "village_write_diary" in {item["id"] for item in world.actions()}
    assert act(world, "village_write_diary", text=note)["code"] == "DIARY_WRITTEN"
    assert village_legacy.apply(world, "village_write_diary", {"text": note})[1] == "DIARY_ALREADY_WRITTEN"
    assert len(state.legacy.diaries["rowan"]) == 1
    assert village_legacy.observation(world)["legacy"]["diary"][0]["text"] == note
    mara = world.bind("mara", "mara-session")
    assert village_legacy.observation(mara)["legacy"]["diary"] == []
    world.save()
    restored = World(world.scenario, world.path, "legacy", "rowan", "session-rowan")
    assert restored.state.village.legacy.diaries["rowan"][0].text == note
    restored.state.village.minute = 1440
    village_legacy.advance(restored)
    target = World(world.scenario, tmp_path / "next.json", "next", "rowan", "next-rowan")
    village_legacy.successor_state(restored.scenario, restored.state, target, "a" * 32)
    assert village_legacy.observation(target)["legacy"]["history_pending"] is True
    target.state.village.residents["rowan"].life.sleeping = False
    assert village_legacy.apply(target, "village_read_history", {})[1] == "HISTORY_READ"
    diary = village_legacy.observation(target)["legacy"]["diary"]
    assert diary[0]["author"] == "Rowan"
    assert diary[0]["text"] == note
    assert diary[0]["origin_world_id"] == "legacy"
    assert village_legacy.observation(target.bind("mara", "next-mara"))["legacy"]["diary"] == []
    assert target.state.village.stocks == target.scenario.village.stocks


def test_handoff_carries_each_role_diary_without_sharing_it(world, tmp_path):
    state = world.state.village
    mara = world.bind("mara", "mara-session")
    state.minute = 480
    problems = "The mill grain was reserved for orders, so I could not mill the next batch."
    suggestions = "Check unreserved grain and order deadlines before committing to milling."
    assert village_legacy.apply(mara, "village_write_diary", {
        "text": "I learned to check grain reserves before scheduling milling work.",
        "problems": problems, "suggestions": suggestions,
    })[1] == "DIARY_WRITTEN"
    world.save()
    restored = World(world.scenario, world.path, "legacy", "rowan", "session-rowan")
    assert restored.state.village.legacy.diaries["mara"][0].problems == problems
    state.minute = 1440
    village_legacy.advance(world)
    target = World(world.scenario, tmp_path / "next.json", "next", "rowan", "next-rowan")
    village_legacy.successor_state(world.scenario, world.state, target, "a" * 32)
    assert target.state.village.legacy.current["mara"] == "Mara"
    target.state.village.residents["mara"].life.sleeping = False
    assert village_legacy.apply(target.bind("mara", "next-mara"), "village_read_history", {})[1] == "HISTORY_READ"
    inherited = village_legacy.observation(target.bind("mara", "next-mara"))["legacy"]["diary"][0]
    assert inherited["author"] == "Mara"
    assert inherited["problems"] == problems
    assert inherited["suggestions"] == suggestions
    assert village_legacy.observation(target)["legacy"]["diary"] == []


def test_diary_notes_are_complete_and_not_inherited_without_handoff(world, tmp_path):
    state = world.state.village
    assert village_legacy.apply(world, "village_write_diary", {"text": "too short"})[1] == "INVALID_DIARY"
    assert village_legacy.apply(world, "village_write_diary", {
        "text": "A sufficiently detailed short diary note.", "problems": "too short"
    })[1] == "INVALID_DIARY"
    assert village_legacy.apply(world, "village_write_diary", {
        "text": "A sufficiently detailed short diary note.", "suggestions": "x" * 1001
    })[1] == "INVALID_DIARY"
    assert state.legacy.diaries == {}
    for day in range(4):
        state.minute = day * 1440 + 300
        state.residents["rowan"].life.last_woke = state.minute
        assert village_legacy.apply(world, "village_write_diary", {
            "text": f"Day {day}: I checked the woodlot and confirmed which logs were available."
        })[1] == "DIARY_WRITTEN"
    assert len(state.legacy.diaries["rowan"]) == 4
    assert "Day 0" in state.legacy.diaries["rowan"][0].text
    assert state.legacy.diaries["rowan"][0].problems is None
    assert state.legacy.diaries["rowan"][0].suggestions is None
    fresh = World(world.scenario, tmp_path / "fresh.json", "fresh", "rowan", "fresh-rowan")
    assert village_legacy.observation(fresh)["legacy"]["diary"] == []


def test_full_public_history_and_private_diary_cross_multiple_generations(world, tmp_path):
    state = world.state.village
    state.minute = 480
    state.residents["rowan"].life.last_woke = 480
    assert village_legacy.apply(world, "village_write_diary", {
        "text": "I checked the woodlot and confirmed which logs were available."
    })[1] == "DIARY_WRITTEN"
    for index in range(110):
        village.event(world, "mara", "project_completed", "repair_mill", f"Repair milestone {index}.", "watermill")
    village_legacy.advance(world)
    state.minute = 1440
    village_legacy.advance(world)
    published = [entry for entry in state.legacy.chronicle if entry.kind == "resident_journal"]
    assert len(published) == 1
    assert published[0].text == "I checked the woodlot and confirmed which logs were available."
    assert published[0].author == "Rowan"
    assert published[0].origin_minute == 480
    assert published[0].origin_day == 0
    assert published[0].journal_id == "legacy:rowan:480"
    assert published[0].authored is True
    second = World(world.scenario, tmp_path / "second.json", "second", "rowan", "second-rowan")
    village_legacy.successor_state(world.scenario, world.state, second, "a" * 32)
    assert len(second.state.village.legacy.chronicle) >= 113
    assert second.state.village.legacy.chronicle[3].text.endswith("Repair milestone 0.")
    second.state.village.residents["rowan"].life.sleeping = False
    assert village_legacy.observation(second)["legacy"]["public_history"] == []
    assert village_legacy.apply(second, "village_read_history", {})[1] == "HISTORY_READ"
    public = village_legacy.observation(second)["legacy"]["public_history"]
    assert len(public) >= 113
    assert len(public) < len(second.state.village.legacy.chronicle)
    assert any("I checked the woodlot" in entry["text"] for entry in public)
    mara = second.bind("mara", "second-mara")
    assert village_legacy.observation(mara)["legacy"]["diary"] == []
    assert "I checked the woodlot" in village_legacy.observation(second)["legacy"]["diary"][0]["text"]
    second.state.village.minute = 1440
    village_legacy.advance(second)
    next_scenario = world.scenario.model_copy(deep=True)
    next_scenario.village.legacy.families["rowan"].successor = "Talia Woodward"
    third = World(next_scenario, tmp_path / "third.json", "third", "rowan", "third-rowan")
    village_legacy.successor_state(world.scenario, second.state, third, "b" * 32)
    assert len(third.state.village.legacy.chronicle) > len(second.state.village.legacy.chronicle)
    assert third.state.village.legacy.chronicle[3].text.endswith("Repair milestone 0.")
    assert third.state.village.legacy.diaries["rowan"][0].origin_world_id == "legacy"
    third.save()
    loaded = World(next_scenario, third.path, "third", "rowan", "third-rowan")
    assert len(loaded.state.village.legacy.chronicle) == len(third.state.village.legacy.chronicle)
    assert len([entry for entry in loaded.state.village.legacy.chronicle
                if entry.kind == "resident_journal"]) == 1


def test_two_days_publish_24_distinct_authored_journals_once(world, tmp_path):
    state = world.state.village
    assert len(world.scenario.village.residents) == 12
    for day in range(2):
        state.minute = day * 1440 + 480
        for rid in world.scenario.village.residents:
            state.residents[rid].life.last_woke = state.minute
            actor = world if rid == "rowan" else world.bind(rid, f"journal-{rid}")
            assert village_legacy.apply(actor, "village_write_diary", {
                "text": f"Day {day}: I recorded the work I actually did as {rid} today."
            })[1] == "DIARY_WRITTEN"
            assert village_legacy.apply(actor, "village_write_diary", {
                "text": "A duplicate reflection must not be added for this waking day."
            })[1] == "DIARY_ALREADY_WRITTEN"
        state.minute = (day + 1) * 1440
        village_legacy.advance(world)
        village_legacy.advance(world)
        world.save()
        loaded = World(world.scenario, world.path, "legacy", "rowan", "session-rowan")
        village_legacy.advance(loaded)
        assert len([entry for entry in loaded.state.village.legacy.chronicle
                    if entry.kind == "resident_journal"]) == 12 * (day + 1)
    journals = [entry for entry in state.legacy.chronicle if entry.kind == "resident_journal"]
    assert len(journals) == 24
    assert len({entry.journal_id for entry in journals}) == 24
    assert {entry.source_actor for entry in journals} == set(world.scenario.village.residents)
    assert {entry.origin_minute for entry in journals} == {480, 1920}
    assert {entry.origin_day for entry in journals} == {0, 1}


def test_published_history_requires_personal_physical_read(world):
    state = world.state.village
    state.minute = 480
    note = "I found the mill needed repairs before taking further orders."
    assert village_legacy.apply(world, "village_write_diary", {"text": note})[1] == "DIARY_WRITTEN"
    state.minute = 1440
    village_legacy.advance(world)
    mara = world.bind("mara", "mara-session")
    assert village_legacy.observation(mara)["legacy"]["public_history"] == []
    assert village_legacy.observation(mara)["legacy"]["diary"] == []
    assert village_legacy.observation(world)["legacy"]["public_history"] == []
    assert village_legacy.apply(mara, "village_read_chronicle", {})[1] == "NOT_AT_CHRONICLE"
    assert village_legacy.observation(mara)["legacy"]["public_history"] == []
    mara.actor.location_id = "village-square"
    assert village_legacy.apply(mara, "village_read_chronicle", {})[1] == "CHRONICLE_READ"
    public = village_legacy.observation(mara)["legacy"]["public_history"]
    assert len([entry for entry in public if entry["kind"] == "resident_journal"]) == 1
    assert next(entry for entry in public if entry["kind"] == "resident_journal")["text"] == note
    assert village_legacy.observation(mara)["legacy"]["diary"] == []
    assert village_legacy.observation(world)["legacy"]["public_history"] == []


def test_visitor_overnight_and_departure_history_has_source_and_is_idempotent(world):
    from npc_sim.village_events_models import VisitorState

    state = world.state.village
    assert state.incidents is not None
    plan = state.incidents.config.templates[0].visitor
    assert plan is not None
    visitor = VisitorState(id="test-visitor", incident="test-incident", name="Test Visitor",
                           plan=plan, location=plan.lodging_location or "tavern",
                           arrived_at=1000, leave_at=2000, next_step_at=1000, coins=10,
                           status="present", lodging_days=[0])
    state.incidents.visitors[visitor.id] = visitor
    state.minute = 1440
    village_legacy.advance(world)
    entries = [item for item in state.legacy.chronicle if item.kind == "visitor_overnight"]
    assert len(entries) == 1
    assert entries[0].source_actor == visitor.id
    assert entries[0].origin_world_id == "legacy"
    assert entries[0].origin_minute == 1440
    assert entries[0].authored is False
    assert "recorded lodging" in entries[0].text
    village_legacy.advance(world)
    assert len([item for item in state.legacy.chronicle if item.kind == "visitor_overnight"]) == 1
    world.save()
    reloaded = World(world.scenario, world.path, "legacy", "rowan", "session-rowan")
    village_legacy.advance(reloaded)
    assert len([item for item in reloaded.state.village.legacy.chronicle
                if item.kind == "visitor_overnight"]) == 1
    village.event(world, visitor.id, "visitor_departed", visitor.id,
                  "Test Visitor physically departed.", visitor.location)
    village_legacy.advance(world)
    departed = state.legacy.chronicle[-1]
    assert departed.kind == "visitor_departed"
    assert departed.source_actor == visitor.id
    assert departed.text == "Test Visitor physically departed."
    assert departed.authored is False


def test_trader_history_records_departure_without_inventing_overnight(world):
    state = world.state.village
    assert "iron-trader" in world.state.entities
    village.event(world, "iron-trader", "trader_arrived", "iron", "The iron trader arrived.", "village-square")
    village.event(world, "iron-trader", "trader_departed", "iron", "The iron trader departed.", "village-square")
    state.minute = 1440
    village_legacy.advance(world)
    entries = [item for item in state.legacy.chronicle if item.source_actor == "iron-trader"]
    assert [entry.kind for entry in entries] == ["trader_arrived", "trader_departed"]
    assert entries[-1].text == "The iron trader departed."
    assert entries[-1].source_event is not None


def test_trader_overnight_is_deduplicated_after_replayed_midnight(world):
    state = world.state.village
    state.trader_day = 0
    world.state.entities["iron-trader"].location = "village-square"
    state.minute = 1440
    village_legacy.advance(world)
    assert len([entry for entry in state.legacy.chronicle if entry.kind == "trader_overnight"]) == 1
    state.legacy.last_day = 0
    village_legacy.advance(world)
    assert len([entry for entry in state.legacy.chronicle if entry.kind == "trader_overnight"]) == 1
    world.save()
    restored = World(world.scenario, world.path, "legacy", "rowan", "session-rowan")
    restored.state.village.legacy.last_day = 0
    village_legacy.advance(restored)
    assert len([entry for entry in restored.state.village.legacy.chronicle
                if entry.kind == "trader_overnight"]) == 1


def test_sleep_requires_journal_for_each_waking_day(world):
    state = world.state.village
    life = state.residents["rowan"].life
    state.minute = 1320
    life.sleeping = False
    life.last_woke = 480
    from npc_sim import village_fallback
    assert village_fallback.candidates(world)[0]["action_id"] == "village_write_diary"
    assert act(world, "village_sleep")["code"] == "JOURNAL_DUE"
    assert state.residents["rowan"].life.sleeping is False
    assert village_legacy.apply(world, "village_write_diary", {
        "text": "Today I checked the woodlot and found the remaining logs uncommitted."
    })[1] == "DIARY_WRITTEN"
    assert act(world, "village_sleep")["code"] == "SLEEP_STARTED"
    state.minute = 1920
    from npc_sim import village_life
    village_life.advance(world)
    assert not village_legacy.journal_written(world, "rowan")
    state.minute = 2760
    assert act(world, "village_sleep")["code"] == "JOURNAL_DUE"


def test_successor_must_read_history_before_other_actions(world, tmp_path):
    state = world.state.village
    state.minute = 1440
    village_legacy.advance(world)
    target = World(world.scenario, tmp_path / "next.json", "next", "rowan", "next-rowan")
    village_legacy.successor_state(world.scenario, world.state, target, "a" * 32)
    successor = target.state.village.residents["rowan"].life
    successor.sleeping = False
    target.state.village.minute = 480
    successor.last_woke = 480
    assert village_legacy.observation(target)["legacy"]["history_pending"] is True
    assert act(target, "village_observe")["code"] == "HISTORY_DUE"
    assert target.observe()["village"]["fallback"]["eligible_candidates"][0]["action_id"] == "village_read_history"
    assert act(target, "village_read_history")["code"] == "HISTORY_READ"
    assert village_legacy.observation(target)["legacy"]["history_pending"] is False
    target.save()
    reloaded = World(world.scenario, target.path, "next", "rowan", "next-rowan")
    assert village_legacy.observation(reloaded)["legacy"]["history_pending"] is False
    assert act(target, "village_read_history")["code"] == "ACTION_UNAVAILABLE"
    assert "HISTORY_DUE" != act(target, "village_observe")["code"]


def test_handoff_rejects_carried_property_without_mutating_target(world, tmp_path):
    state = world.state.village
    state.minute = 1440
    village_legacy.advance(world)
    state.residents["rowan"].cargo["logs"] = 1
    target = World(world.scenario, tmp_path / "next.json", "next", "rowan", "next-rowan")
    with pytest.raises(ValueError, match="carried possessions"):
        village_legacy.successor_state(world.scenario, world.state, target, "a" * 32)
    assert target.state.village.legacy.handoff is None


def test_story_requires_nearby_awake_listener_and_preserves_source(world):
    state = world.state.village
    village.event(world, "rowan", "missed_meal", "daily", "Missed supper.", "cottages")
    village_legacy.advance(world)
    index = len(state.legacy.chronicle) - 1
    world.actor.location_id = "village-square"
    village_legacy.apply(world, "village_read_chronicle", {})
    mara = world.bind("mara", "mara-session")
    mara.actor.location_id = "village-square"
    state.residents["mara"].life.sleeping = False
    result = village_legacy.apply(world, "village_share_chronicle", {"target": "mara", "entry": index})
    assert result[1] == "STORY_SHARED"
    assert state.legacy.lessons["mara"][0].source == "story:rowan"
    assert village_legacy.observation(mara)["legacy"]["lessons"][0]["source"] == "story:rowan"
    count = len(state.legacy.lessons["mara"])
    village_legacy.apply(world, "village_share_chronicle", {"target": "mara", "entry": index})
    assert len(state.legacy.lessons["mara"]) == count
    mara.actor.location_id = "watermill"
    assert village_legacy.apply(world, "village_share_chronicle", {"target": "mara", "entry": index})[1] == "STORY_UNAVAILABLE"


def test_unread_shortage_cannot_change_work_cooldown(world):
    state = world.state.village
    inn = world.bind("elsie", "elsie-session")
    inn.actor.location_id = "tavern"
    state.minute = 600
    state.residents["elsie"].life.sleeping = False
    village.event(world, "rowan", "missed_meal", "daily", "Missed supper.", "cottages")
    village_legacy.advance(world)
    assert village_legacy.decision_hint(inn) == ""
    assert village_legacy.active_lessons(inn, "elsie") == []
    inn.actor.location_id = "village-square"
    village_legacy.apply(inn, "village_read_chronicle", {})
    assert "food shortage" in village_legacy.decision_hint(inn)

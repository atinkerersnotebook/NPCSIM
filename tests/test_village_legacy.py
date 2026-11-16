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


def test_diary_notes_are_bounded_and_not_inherited_without_handoff(world, tmp_path):
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
    assert len(state.legacy.diaries["rowan"]) == village_legacy.DIARY_LIMIT
    assert "Day 0" not in state.legacy.diaries["rowan"][0].text
    assert state.legacy.diaries["rowan"][0].problems is None
    assert state.legacy.diaries["rowan"][0].suggestions is None
    fresh = World(world.scenario, tmp_path / "fresh.json", "fresh", "rowan", "fresh-rowan")
    assert village_legacy.observation(fresh)["legacy"]["diary"] == []


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

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from npc_sim.controller import Controller
from npc_sim.models import AppConfig, Decision
from npc_sim.storage import read_json
from npc_sim.assessment import Finding, StoryReview
from npc_sim.dungeon_master import DungeonMasterReview


ROOT = Path(__file__).resolve().parent.parent
SOLUTION = [
    ("look", {"target": "door"}),
    ("open", {"target": "box"}),
    ("take", {"target": "key"}),
    ("unlock", {"target": "door", "item": "key"}),
    ("open", {"target": "door"}),
    ("go", {"target": "door"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id", ["millhaven-generations", "millhaven-complete"])
async def test_generation_handoff_from_saved_midnight_is_explicit_and_reversible(configuration, scenario_id):
    data, config = configuration
    config.world.scenario = scenario_id
    controller = Controller(ROOT, data, config)
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": scenario_id})
        first = controller.status()["world"]
        assert first["village"]["legacy"]["handoff"] is None
        await controller.command("save_episode", {
            "run_id": controller.run["id"], "name": "Generation one checkpoint"})
        from npc_sim import episode_saves
        source = episode_saves.read_save(data, next((data / "saves").glob("*.json")).stem)
        source.world.village.minute = 1440
        source.world.village.legacy.last_day = 1
        from npc_sim.models import Entity
        from npc_sim.village_research_models import ResearchProject, ResearchProposal
        key = "discovery-inherited"
        proposal = ResearchProposal(title="Seasoned timber braces", description="Prepare real logs for durable braces.",
                                    skill="forestry", kind="material", inputs={"logs": 1}, duration=60)
        source.world.village.research.projects[key] = ResearchProject(
            owner="rowan", location="woods", proposal=proposal, product=key,
            required_minutes=240, required_successes=3, worked_minutes=240,
            attempts=4, successes=3, discovered_at=700)
        source.world.entities[key] = Entity(name=proposal.title, kind="discovery", location="woods",
                                            description=proposal.description,
                                            readable_text="Verified material: one log makes one brace.")
        from npc_sim.models import VillageDiaryEntry
        source.world.village.legacy.diaries["rowan"] = [VillageDiaryEntry(
            author="Rowan", written_at=1320,
            text="I learned to check the woodlot before promising logs to neighbours.",
            origin_world_id=source.world.world_id)]
        source.id = "a" * 32
        source.name = "Generation one midnight"
        episode_saves.write_save(data, source)
        save_id = source.id
        await controller.command("start", {"scenario_id": scenario_id,
                                           "generation_save_id": save_id})
        next_world = controller.status()["world"]
        assert next_world["village"]["legacy"]["lineage"]["rowan"] == ["Rowan"]
        assert next_world["village"]["legacy"]["handoff"]["save_id"] == save_id
        assert next_world["village"]["research"]["projects"][key]["proposal"]["title"] == proposal.title
        assert next_world["entities"][key]["readable_text"] == source.world.entities[key].readable_text
        assert controller.agents["rowan"]["observation"]["village"]["legacy"]["history_pending"] is True
        assert controller.agents["rowan"]["observation"]["village"]["legacy"]["diary"] == []
        assert controller.run["agent_profiles"]["rowan"]["name"] == "Rina Woodward"
        await controller.command("start", {"scenario_id": scenario_id})
        assert controller.status()["world"]["village"]["legacy"]["handoff"] is None
        assert not controller.status()["world"]["village"]["research"]["projects"]
        assert controller.agents["rowan"]["observation"]["village"]["legacy"]["diary"] == []
    finally:
        await controller.shutdown()


def assessment_response(messages):
    payload = json.loads(messages[1]["content"])
    passed = payload["final_verification"]["result"]["status"] == "achieved"
    return StoryReview(verdict="pass" if passed else "fail", findings=[
        Finding(reason="The objective is verified." if passed else "The attempt ended without fulfilling the objective.",
                evidence_ids=[payload["final_verification"]["event_id"]])])


class TestProxy:
    __test__ = False

    def __init__(self, real_proxy, actions):
        self.real_proxy = real_proxy
        self.actions = iter(actions)
        self.contexts = []
        self.completion_contexts = []
        self.dungeon_master_contexts = []

    def context(self, goal, observation, memory, repair=None, memory_tier="long"):
        messages = self.real_proxy.context(goal, observation, memory, repair, memory_tier)
        self.contexts.append(json.loads(messages[1]["content"]))
        return messages

    async def choose(self, model, messages, correlation_id, timeout):
        action, arguments = next(self.actions)
        return Decision(action_id=action, arguments=arguments,
                        justification="Test fixture decision.", expected_outcome="Test fixture effect."), {}

    def completion_context(self, payload):
        messages = self.real_proxy.completion_context(payload)
        self.completion_contexts.append(payload)
        return messages

    async def assess(self, model, messages, correlation_id, timeout):
        return assessment_response(messages), {}

    def dungeon_master_context(self, payload, repair=None):
        self.dungeon_master_contexts.append(payload)
        return self.real_proxy.dungeon_master_context(payload, repair)

    async def review_progress(self, model, messages, correlation_id, timeout):
        payload = json.loads(messages[1]["content"])
        return DungeonMasterReview(verdict="continue", findings=[
            Finding(reason="Continue based on the observed action.",
                    evidence_ids=[payload["evidence"][-1]["id"]])]), {}


@pytest.fixture
def configuration(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    config = AppConfig.model_validate(read_json(ROOT / "data" / "config.json"))
    config.proxy.model = "test-model"
    config.budgets.max_seconds = 60
    return data, config


def test_deterministic_village_candidate_bypasses_model_and_preserves_strict_schema(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.run = {"id": "a" * 32, "actions": 0, "actor_id": "villager", "session_id": "session"}
    controller.observation = {
        "observation_revision": "obs-1",
        "village": {"minute": 500, "day_policy": {"typed_execution": True},
                    "fallback": {"context_revision": "known-state", "eligible_candidates": [{
                        "task_id": "return:workplace", "action_id": "go",
                        "arguments": {"target": "door"}, "reason": "Use the known route."}]}},
        "actions": [{"id": "go", "availability": "available", "arguments_schema": {
            "type": "object", "properties": {"target": {"type": "string", "enum": ["door"]}},
            "required": ["target"], "additionalProperties": False}}],
    }
    controller.events = None

    decision = controller._deterministic_decision()

    assert decision is not None
    assert decision.action_id == "go"
    assert decision.arguments == {"target": "door"}
    assert controller.memory["active_typed_step"]["action_id"] == "go"


def test_deterministic_decision_breaks_repeated_speech_at_open_final_exit(configuration):
    data, config = configuration
    config.world.scenario = "decision-gauntlet"
    controller = Controller(ROOT, data, config)
    run_directory = data / "runs" / ("a" * 32)
    run_directory.mkdir(parents=True)
    from npc_sim.storage import write_json
    write_json(run_directory / "scenario.json", read_json(
        ROOT / "data" / "scenarios" / "decision-gauntlet.json"))
    controller.run = {
        "id": "a" * 32, "actions": 10, "actor_id": "benchmark-badger",
        "session_id": "session", "cycles": 0,
        "goal": {"type": "all_actors_at_exit_location"},
        "exit_location": "shelter",
    }
    controller.memory["recent"] = [
        {"action_id": "say", "arguments": {"message": "Please verify the relay."}},
        {"action_id": "say", "arguments": {"message": "The relay still needs verification."}},
    ]
    controller.observation = {
        "self": {"actor_id": "benchmark-badger", "location_id": "relay-yard"},
        "actions": [{"id": "go", "availability": "available", "targets": ["relay-gate"],
                     "arguments_schema": {
                         "type": "object", "properties": {"target": {
                             "type": "string", "enum": ["relay-gate"]}},
                         "required": ["target"], "additionalProperties": False}}],
    }

    class EventStub:
        directory = run_directory

        def __init__(self):
            self.events = []

        def append(self, event):
            self.events.append(event)
            return event

    controller.events = EventStub()

    decision = controller._deterministic_decision()

    assert decision is not None
    assert decision.action_id == "go"
    assert decision.arguments == {"target": "relay-gate"}
    assert controller.memory["active_typed_step"]["step_id"] == "exit-after-speech:relay-gate"


@pytest.mark.parametrize("location,inventory,actions,expected", [
    ("entry-hall", ["sun-medallion", "moss-seal"], ["drop"], ("drop", {"moss-seal", "sun-medallion"})),
    ("crossed-gallery", ["sun-medallion", "moss-seal"], ["go"], ("go", "hall-arch")),
])
def test_treasure_policy_returns_carried_relics(configuration, location, inventory, actions, expected):
    data, config = configuration
    config.world.scenario = "zork"
    controller = Controller(ROOT, data, config)
    run_directory = data / "runs" / ("b" * 32)
    run_directory.mkdir(parents=True)
    from npc_sim.storage import write_json
    write_json(run_directory / "scenario.json", read_json(ROOT / "data" / "scenarios" / "zork.json"))
    controller.run = {
        "id": "b" * 32, "actions": 10, "actor_id": "npc", "session_id": "session",
        "cycles": 0, "goal": {"type": "treasures_returned"},
    }
    controller.observation = {
        "self": {"actor_id": "npc", "location_id": location,
                 "inventory": [{"id": item, "inventory_units": 1} for item in inventory]},
        "visible_entities": [],
        "actions": [{"id": "drop", "availability": "available", "targets": inventory,
                     "arguments_schema": {"type": "object", "properties": {"target": {
                         "type": "string", "enum": inventory}},
                         "required": ["target"], "additionalProperties": False}},
                    {"id": "go", "availability": "available", "targets": ["hall-arch"],
                     "arguments_schema": {"type": "object", "properties": {"target": {
                         "type": "string", "enum": ["hall-arch"]}},
                         "required": ["target"], "additionalProperties": False}}],
    }

    class EventStub:
        directory = run_directory

        def __init__(self):
            self.events = []

        def append(self, event):
            self.events.append(event)
            return event

    controller.events = EventStub()
    decision = controller._deterministic_decision()
    if isinstance(expected[1], set):
        assert decision.action_id == expected[0]
        assert decision.arguments["target"] in expected[1]
    else:
        assert (decision.action_id, decision.arguments["target"]) == expected


def test_treasure_policy_drops_nonobjective_item_before_repeat_search(configuration):
    data, config = configuration
    config.world.scenario = "zork"
    controller = Controller(ROOT, data, config)
    run_directory = data / "runs" / ("c" * 32)
    run_directory.mkdir(parents=True)
    from npc_sim.storage import write_json
    write_json(run_directory / "scenario.json", read_json(ROOT / "data" / "scenarios" / "zork.json"))
    controller.run = {
        "id": "c" * 32, "actions": 10, "actor_id": "npc", "session_id": "session",
        "cycles": 0, "goal": {"type": "treasures_returned"},
    }
    controller.observation = {
        "self": {"actor_id": "npc", "location_id": "root-cellar", "inventory": [
            {"id": "tarnished-lantern", "inventory_units": 1},
            {"id": "sun-medallion", "inventory_units": 1},
        ]},
        "visible_entities": [
            {"id": "moss-seal", "takable": True, "location": "root-cellar", "inventory_units": 1}],
        "actions": [
            {"id": "take", "availability": "blocked", "targets": [], "arguments_schema": {
                "type": "object", "properties": {"target": {"type": "string", "enum": ["moss-seal"]}},
                "required": ["target"], "additionalProperties": False}},
            {"id": "drop", "availability": "available", "targets": ["tarnished-lantern", "sun-medallion"],
             "arguments_schema": {"type": "object", "properties": {"target": {
                 "type": "string", "enum": ["tarnished-lantern", "sun-medallion"]}},
                 "required": ["target"], "additionalProperties": False}},
        ],
    }

    class EventStub:
        directory = run_directory

        def __init__(self):
            self.events = []

        def append(self, event):
            self.events.append(event)
            return event

    controller.events = EventStub()
    decision = controller._deterministic_decision()
    assert decision.action_id == "drop"
    assert decision.arguments == {"target": "tarnished-lantern"}


def test_repeated_no_change_inspection_forces_available_travel(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.run = {
        "id": "d" * 32, "actions": 10, "actor_id": "npc", "session_id": "session",
        "cycles": 0, "goal": {"type": "actor_at_exit_location"},
    }
    controller.memory["recent"] = [
        {"action_id": "look", "arguments": {}, "outcome": "NO_CHANGE", "location_id": "flood-wing"},
        {"action_id": "search", "arguments": {"target": "flood-wing"},
         "outcome": "NO_CHANGE", "location_id": "flood-wing"},
    ]
    controller.observation = {
        "self": {"actor_id": "npc", "location_id": "flood-wing", "visited_locations": ["flood-wing"],
                 "body": {"capabilities": ["swim"]}},
        "visible_entities": [
            {"id": "inspection-channel", "destination": "submerged-control-room", "kind": "door",
             "traversal_capabilities": ["swim"]},
            {"id": "flood-path", "destination": "relay-yard", "kind": "door"}],
        "actions": [{"id": "go", "availability": "available",
                     "targets": ["flood-path", "inspection-channel"],
                     "arguments_schema": {"type": "object", "properties": {"target": {
                         "type": "string", "enum": ["flood-path", "inspection-channel"]}},
                         "required": ["target"], "additionalProperties": False}}],
    }
    controller.events = None

    decision = controller._deterministic_decision()

    assert decision is not None
    assert decision.action_id == "go"
    assert decision.arguments["target"] == "inspection-channel"
    assert controller._repeats_confirmed_no_change(Decision(
        action_id="search", arguments={"target": "flood-wing"},
        justification="test", expected_outcome="test"))


def test_loop_recovery_replaces_repeated_action_nonfatally(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.run = {
        "id": "e" * 32, "actions": 10, "actor_id": "npc", "session_id": "session",
        "cycles": 0, "goal": {"type": "actor_at_exit_location"},
    }
    controller.memory["recent"] = [
        {"action_id": "look", "arguments": {"target": "power-frame"},
         "outcome": "NO_CHANGE", "location_id": "relay-yard"},
    ]
    controller.observation = {
        "self": {"actor_id": "npc", "location_id": "relay-yard", "visited_locations": ["relay-yard"],
                 "body": {"capabilities": []}},
        "visible_entities": [{"id": "archive-path", "destination": "evidence-hub", "kind": "door"}],
        "actions": [
            {"id": "look", "availability": "available", "targets": ["power-frame"],
             "arguments_schema": {"type": "object", "properties": {"target": {
                 "type": "string", "enum": ["power-frame"]}},
                 "additionalProperties": False}},
            {"id": "go", "availability": "available", "targets": ["archive-path"],
             "target_requirements": {"archive-path": None},
             "arguments_schema": {"type": "object", "properties": {"target": {
                 "type": "string", "enum": ["archive-path"]}},
                 "required": ["target"], "additionalProperties": False}},
        ],
    }

    rejected = Decision(action_id="look", arguments={"target": "power-frame"},
                        justification="test", expected_outcome="test")
    replacement = controller._loop_recovery_decision(rejected)

    assert replacement is not None
    assert replacement.action_id == "go"
    assert replacement.arguments == {"target": "archive-path"}


def test_deterministic_executor_rejects_unknown_schema_without_dispatch(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.run = {"id": "a" * 32, "actions": 0, "actor_id": "villager", "session_id": "session"}
    controller.observation = {
        "village": {"day_policy": {"typed_execution": True}, "minute": 500,
                    "fallback": {"eligible_candidates": [{
                        "task_id": "invalid", "action_id": "go", "arguments": {"target": "unknown"}}]}},
        "actions": [{"id": "go", "availability": "available", "arguments_schema": {
            "type": "object", "properties": {"target": {"type": "string", "enum": ["door"]}},
            "required": ["target"], "additionalProperties": False}}],
    }
    controller.events = None

    assert controller._deterministic_decision() is None
    assert "active_typed_step" not in controller.memory


def test_deterministic_plan_drops_stale_meal_when_breakfast_is_disabled(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.run = {"id": "a" * 32, "actions": 0, "actor_id": "villager", "session_id": "session"}
    step = {"step_id": "stale-breakfast", "intent": "other", "action_id": "village_eat",
            "arguments": {}, "status": "pending", "attempts": 0, "prerequisites": [],
            "interrupt_on": [], "earliest_at": None, "latest_at": None, "blocker": None}
    controller.memory["typed_plan"] = [step]
    controller.observation = {
        "village": {"minute": 590, "day_policy": {"typed_execution": True, "breakfast": False,
                                                   "meal_window": 90},
                    "personal": {"life": {"meals": {"0:breakfast": {
                        "kind": "breakfast", "status": "pending", "due": 450}}}},
                    "fallback": {"eligible_candidates": []}},
        "actions": [{"id": "village_eat", "availability": "available",
                     "arguments_schema": {"type": "object", "properties": {},
                                          "additionalProperties": False}}],
    }
    controller.events = None

    assert controller._deterministic_decision() is None
    assert controller.memory["typed_plan"] == []


def test_action_memory_aggregates_repeated_results_with_provenance(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.run = {"id": "a" * 32, "actions": 0, "actor_id": "villager", "session_id": "session",
                     "cycles": 0, "goal": {"revealed": True}}
    controller.observation = {"self": {"actor_id": "villager", "location_id": "forge"},
                              "location": {"id": "forge", "description": "Forge"},
                              "visible_entities": []}
    controller.events = type("Events", (), {"events": [{"id": "e1"}], "append": lambda self, value: value})()
    controller.emit = lambda *args, **kwargs: None

    result = {"outcome": "APPLIED", "code": "LOCAL_TOOLS_CHECKED", "explanation": "Inspected.",
              "observation": {"self": {"actor_id": "villager", "location_id": "forge"},
                              "location": {"id": "forge", "description": "Forge"},
                              "visible_entities": [], "village": {"minute": 100}}}
    controller._accept_action_result({"request_id": "r1", "action_id": "village_observe", "arguments": {}},
                                     result, None, True)
    controller.events.events = [{"id": "e2"}]
    controller._accept_action_result({"request_id": "r2", "action_id": "village_observe", "arguments": {}},
                                     result, None, True)

    assert controller.memory["event_summaries"] == [{
        "action_id": "village_observe", "outcome": "APPLIED", "code": "LOCAL_TOOLS_CHECKED",
        "minute": 100, "first_minute": 100, "count": 2, "source_events": ["e1", "e2"],
    }]


@pytest.mark.asyncio
async def test_unhandled_automatic_cycle_reports_error_without_stopping_worker(configuration, monkeypatch):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    await controller.start_worker()
    try:
        await controller.command("start")
        monkeypatch.setattr(controller, "cycle", AsyncMock(side_effect=RuntimeError("cycle failed")))
        monkeypatch.setattr(controller, "assess_completion", AsyncMock())
        await controller.command("run")
        for _ in range(100):
            if controller.run["status"] == "error":
                break
            await asyncio.sleep(0.01)
        assert controller.run["status"] == "error"
        assert controller.run["reason"] == "cycle failed"
        assert not controller.worker.done()
        assert read_json(controller.events.directory / "run.json")["status"] == "error"
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_three_separated_agents_take_ordered_actions_in_shared_rounds(configuration):
    data, config = configuration
    config.world.scenario = "storm-signal"
    actions = [
        ("release_lens", {"target": "optical-cache"}),
        ("take", {"target": "mast-weight"}),
        ("go", {"target": "service-channel"}),
        ("take", {"target": "signal-lens"}),
        ("go", {"target": "quarry-track"}),
        ("release_coil", {"target": "cable-chest"}),
        ("go", {"target": "rookery-path"}),
        ("brace_mast", {"target": "mast-footing"}),
        ("take", {"target": "wire-coil"}),
    ]
    controller = Controller(ROOT, data, config)
    fake = TestProxy(controller.proxy, actions)
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start", {"turn_mode": "shared_round"})
        session = controller.world.session
        actors = controller.status()["world"]["actors"]
        assert {key: actor["location_id"] for key, actor in actors.items()} == {
            "raven": "rookery", "badger": "quarry", "otter": "river-landing"}
        assert len({agent["session_id"] for agent in controller.run["agents"].values()}) == 3
        for index in range(3):
            await controller.command("step")
            assert controller.world.session is session
            assert controller.run["actor_id"] == "otter"
            assert controller.run["status"] == "paused"
            assert controller.run["cycles"] == index + 1
            assert controller.run["actions"] == (index + 1) * 3
        assert controller.run["actions"] == len(actions)
        assert [context["observation"]["self"]["actor_id"] for context in fake.contexts] == [
            "raven", "badger", "otter"] * 3
        assert len({id(agent["memory"]) for agent in controller.run["agents"].values()}) == 3
        action_events = [event for event in controller.events.events if event["kind"] == "action_request"]
        assert [event["round"] for event in action_events] == [1, 1, 1, 2, 2, 2, 3, 3, 3]
        assert [event["round_action_index"] for event in action_events] == [1, 2, 3] * 3
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_two_agents_cooperate_over_one_real_mcp_world(configuration):
    data, config = configuration
    config.world.scenario = "two-agent-escape"
    actions = [
        ("open", {"target": "supply-box"}),
        ("say", {"message": "Please bring me the passage key."}),
        ("take", {"target": "passage-key"}),
        ("look", {"target": "passage"}),
        ("give", {"target": "passage-key", "recipient": "locksmith"}),
        ("unlock", {"target": "passage", "item": "passage-key"}),
        ("open", {"target": "passage"}),
        ("go", {"target": "passage"}),
        ("go", {"target": "passage"}),
        ("open", {"target": "cabinet"}),
        ("take", {"target": "courtyard-key"}),
        ("drop", {"target": "passage-key"}),
        ("give", {"target": "courtyard-key", "recipient": "locksmith"}),
        ("unlock", {"target": "gate", "item": "courtyard-key"}),
        ("open", {"target": "gate"}),
        ("go", {"target": "gate"}),
        ("look", {"target": "gate"}),
        ("go", {"target": "gate"}),
    ]
    controller = Controller(ROOT, data, config)
    fake = TestProxy(controller.proxy, actions)
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start")
        session = controller.world.session
        assert set(controller.status()["world"]["actors"]) == {"scout", "locksmith"}
        for index in range(len(actions)):
            await controller.command("step")
            assert controller.world.session is session
            assert controller.run["actor_id"] == ("scout" if index % 2 == 0 or index == 17 else "locksmith")
            if index < len(actions) - 1:
                assert controller.run["status"] == "paused"
        assert controller.run["status"] == "succeeded"
        assert controller.run["successful"] is True
        assert controller.run["actions"] == len(actions)
        agents = controller.run["agents"]
        assert agents["scout"]["memory"] is not agents["locksmith"]["memory"]
        assert agents["scout"]["memory"]["recent"] != agents["locksmith"]["memory"]["recent"]
        assert agents["scout"]["session_id"] != agents["locksmith"]["session_id"]
        assert fake.contexts[0]["observation"]["role"].startswith("Scout:")
        assert fake.contexts[1]["observation"]["role"].startswith("Locksmith:")
        assert fake.contexts[2]["observation"]["messages"][0]["actor_id"] == "locksmith"
        assert "courtyard-key" not in json.dumps(fake.contexts[0])
        assert controller.run["verification"]["evidence"]["agents"]["scout"]["status"] == "achieved"
        review_story = fake.completion_contexts[-1]["story"]
        assert any("Scout" in paragraph["text"] for paragraph in review_story)
        assert any("Locksmith" in paragraph["text"] for paragraph in review_story)
        sequence = {event["id"]: event["sequence"] for event in controller.events.events}
        assert [sequence[paragraph["id"]] for paragraph in review_story] == sorted(
            sequence[paragraph["id"]] for paragraph in review_story)
        saved = read_json(controller.events.directory / "run.json")
        assert set(saved["agents"]) == {"scout", "locksmith"}
        assert saved["agents"]["scout"]["memory"]["recent"] == agents["scout"]["memory"]["recent"]
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_two_agent_reset_is_clean(configuration):
    data, config = configuration
    config.world.scenario = "two-agent-escape"
    controller = Controller(ROOT, data, config)
    fake = TestProxy(controller.proxy, [("say", {"message": "I will inspect the room."})])
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start")
        assert controller.run["next_agent_index"] == 0
        await controller.command("step")
        assert controller.run["actor_id"] == "scout"
        old_world = controller.run["world_id"]
        await controller.command("reset")
        assert controller.run["world_id"] != old_world
        assert controller.run["actions"] == 0
        assert all(not agent["memory"]["recent"] for agent in controller.agents.values())
        assert all(not agent["observation"]["messages"] for agent in controller.agents.values())
        assert controller.run["next_agent_index"] == 0
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_asymmetric_river_team_through_real_mcp_and_controller(configuration):
    data, config = configuration
    config.world.scenario = "river-crossing"
    actions = [
        ("say", {"message": "Please rig the rope and pass it to me."}),
        ("take", {"target": "rope"}),
        ("look", {"target": "river"}),
        ("prepare_rope", {"target": "bench"}),
        ("look", {"target": "river"}),
        ("give", {"target": "rope", "recipient": "otter"}),
        ("go", {"target": "river"}),
        ("take", {"target": "toolbox"}),
        ("anchor_rope", {"target": "mooring"}),
        ("repair_ferry", {"target": "winch"}),
        ("go", {"target": "shelter-path"}),
        ("go", {"target": "ferry"}),
        ("go", {"target": "shelter-path"}),
    ]
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, actions)
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in actions:
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.run["successful"] is True
        assert controller.status()["world"]["skill_uses"] == {
            "prepare_rope": "fox", "anchor_rope": "otter", "repair_ferry": "fox"}
        otter = proxy.contexts[0]["observation"]
        fox = proxy.contexts[1]["observation"]
        assert "swim" in otter["self"]["body"]["capabilities"]
        assert "repair" not in otter["self"]["body"]["capabilities"]
        assert "repair" in fox["self"]["body"]["capabilities"]
        assert "swim" not in fox["self"]["body"]["capabilities"]
        assert otter["self"]["body"]["carrying"]["limit"] == 1
        assert fox["self"]["body"]["carrying"]["limit"] == 2
        assert "mooring" not in {entity["id"] for entity in fox["visible_entities"]}
        assert proxy.completion_contexts[-1]["final_verification"]["result"]["evidence"]["cooperative_work"] == {
            "prepare_rope": "fox", "anchor_rope": "otter", "repair_ferry": "fox"}
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_controller_real_mcp_with_fake_decision_model(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    fake = TestProxy(controller.proxy, SOLUTION)
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in SOLUTION:
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.run["actions"] == 6
        assert "key" not in json.dumps(fake.contexts[0]["observation"]["visible_entities"])
        assert "key_id" not in json.dumps(fake.contexts)
        assert "requests" not in json.dumps(fake.contexts)
        assert controller.run["verification"]["status"] == "achieved"
        assert (controller.events.directory / "summary.json").exists()
        for path in data.rglob("*.json"):
            read_json(path)
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_invalid_decision_repair_never_dispatches(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [("invented", {}), ("invented", {})])
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        assert controller.run["status"] == "error"
        assert controller.run["model_calls"] == 2
        assert controller.run["actions"] == 0
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_empty_decisions_pause_at_action_boundary(configuration):
    from npc_sim.proxy import EmptyModelResponse

    data, config = configuration
    controller = Controller(ROOT, data, config)

    class EmptyProxy(TestProxy):
        async def choose(self, *args, **kwargs):
            raise EmptyModelResponse("Model returned an empty decision")

    controller.proxy = EmptyProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        assert controller.run["status"] == "paused"
        assert controller.run["actions"] == 0
        assert controller.run["model_calls"] == 2
        assert "empty model decisions" in controller.run["reason"]
        assert read_json(controller.events.directory / "run.json")["status"] == "paused"
    finally:
        await controller.shutdown(preserve_paused=True)


@pytest.mark.asyncio
async def test_nonprogress_stalls_and_pause_reset(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [("look", {})] * 6)
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in range(6):
            await controller.command("step")
        assert controller.run["status"] == "stalled"
        old_directory = controller.events.directory
        await controller.command("reset")
        assert controller.run["status"] == "ready"
        assert controller.run["actions"] == 0
        assert old_directory.exists()
        assert controller.observation["self"]["inventory"] == []
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("model timed out"),
    *[httpx.HTTPStatusError("proxy unavailable",
                           request=httpx.Request("POST", "http://localhost/chat/completions"),
                           response=httpx.Response(code))
      for code in (408, 429, 500, 502, 503, 504)],
])
async def test_proxy_failure_pauses_without_finishing_or_retrying_immediately(configuration, failure):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    class OfflineProxy(TestProxy):
        async def choose(self, *args, **kwargs):
            raise failure
    controller.proxy = OfflineProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("run")
        for _ in range(500):
            if controller.run["status"] == "paused" and not controller.in_flight:
                break
            await asyncio.sleep(0.01)
        assert controller.run["status"] == "paused"
        assert not controller.in_flight
        assert not controller.worker.done()
        await asyncio.wait_for(controller.command("pause"), timeout=2)
        assert controller.run["actions"] == 0
        assert controller.run["model_calls"] == 1
        assert "waiting to reconnect" in controller.run["reason"]
        assert controller.run["goal"]["status"] != "failed"
        assert controller.started_clock is None
        assert not any(event["kind"] in {"finished", "assessment_started", "error"}
                       for event in controller.events.events)
        saved = read_json(controller.events.directory / "run.json")
        assert saved["status"] == "paused"
        await asyncio.sleep(0.1)
        assert controller.run["model_calls"] == 1
        assert controller.run["elapsed_seconds"] == saved["elapsed_seconds"]
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_running_episode_reconnects_without_replaying_actions(configuration):
    data, config = configuration
    config.world.scenario = "storm-signal"
    controller = Controller(ROOT, data, config)
    recovered = asyncio.Event()

    class IntermittentProxy(TestProxy):
        attempts = 0
        probes = 0

        async def choose(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise httpx.ConnectError("connection refused")
            recovered.set()
            return await super().choose(*args, **kwargs)

        async def models(self):
            self.probes += 1
            if self.probes == 1:
                raise httpx.ConnectError("connection refused")
            return ["test-model"]

    fake = IntermittentProxy(controller.proxy, [("release_lens", {"target": "optical-cache"})])
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start")
        world, run_id = controller.world, controller.run["id"]
        await controller.command("run")
        for _ in range(500):
            if controller.run["status"] == "paused":
                break
            await asyncio.sleep(0.01)
        assert controller._llm_retry_at is not None
        controller._llm_retry_delay = 0.02
        await asyncio.wait_for(recovered.wait(), 10)
        await controller.command("pause")
        assert controller.world is world
        assert controller.run["id"] == run_id
        assert controller.run["actions"] == 1
        assert fake.probes == 2
        assert fake.attempts == 2
        assert controller.run["reason"] == ""
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_manual_pause_cancels_llm_reconnection(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)

    class OfflineProxy(TestProxy):
        probes = 0

        async def choose(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

        async def models(self):
            self.probes += 1
            raise httpx.ConnectError("connection refused")

    fake = OfflineProxy(controller.proxy, [])
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("run")
        for _ in range(500):
            if controller.run["status"] == "paused":
                break
            await asyncio.sleep(0.01)
        assert controller._llm_retry_at is not None
        await controller.command("pause")
        assert controller._llm_retry_at is None
        await asyncio.sleep(0.1)
        assert fake.probes == 0
        assert controller.run["status"] == "paused"
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_proxy_recovery_resumes_same_world_and_actor(configuration):
    data, config = configuration
    config.world.scenario = "storm-signal"
    controller = Controller(ROOT, data, config)
    recovered = asyncio.Event()

    class RecoveringProxy(TestProxy):
        offline = True

        async def choose(self, *args, **kwargs):
            if self.offline:
                self.offline = False
                raise httpx.ConnectError("connection refused")
            recovered.set()
            return await super().choose(*args, **kwargs)

    fake = RecoveringProxy(controller.proxy, [("release_lens", {"target": "optical-cache"})])
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start")
        world, run_id = controller.world, controller.run["id"]
        await controller.command("step")
        assert controller.run["next_agent_index"] == 0
        assert controller.run["status"] == "paused"
        await controller.command("resume")
        await asyncio.wait_for(recovered.wait(), 5)
        await controller.command("pause")
        assert controller.world is world
        assert controller.run["id"] == run_id
        assert controller.run["actions"] == 1
        assert controller.run["actor_id"] == "raven"
        assert controller.run["reason"] == ""
        assert [context["observation"]["self"]["actor_id"] for context in fake.contexts] == ["raven", "raven"]
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_model_discovery_outage_pauses_and_can_be_retried(configuration):
    data, config = configuration
    config.proxy.model = ""
    controller = Controller(ROOT, data, config)

    class DiscoveryProxy(TestProxy):
        offline = True

        async def models(self):
            if self.offline:
                self.offline = False
                raise httpx.ConnectError("connection refused")
            return ["test-model"]

    controller.proxy = DiscoveryProxy(controller.proxy, [SOLUTION[0]])
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        assert controller.run["status"] == "paused"
        assert controller.run["model_calls"] == 0
        assert controller.run["actions"] == 0
        assert "Adventure paused" in controller.run["reason"]
        await controller.command("step")
        assert controller.run["model"] == "test-model"
        assert controller.run["actions"] == 1
        assert controller.run["reason"] == ""
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_supervisor_outage_retries_review_before_next_action(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)

    class SupervisorProxy(TestProxy):
        offline = True
        operations = []

        async def choose(self, *args, **kwargs):
            self.operations.append("decision")
            return await super().choose(*args, **kwargs)

        async def review_progress(self, *args, **kwargs):
            self.operations.append("review")
            if self.offline:
                raise httpx.ConnectError("connection refused")
            return await super().review_progress(*args, **kwargs)

    fake = SupervisorProxy(controller.proxy, SOLUTION[:2])
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        assert controller.run["actions"] == 1
        assert controller.run["pending_dungeon_master_review"]
        assert "Adventure paused" in controller.run["reason"]
        await controller.command("step")
        assert controller.run["actions"] == 1
        fake.offline = False
        await controller.command("step")
        assert controller.run["actions"] == 2
        assert "pending_dungeon_master_review" not in controller.run
        assert fake.operations == ["decision", "review", "review", "review", "decision", "review"]
        assert len([event for event in controller.events.events if event["kind"] == "action_request"]) == 2
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_proxy_outage_freezes_continuous_simulation_clock(configuration):
    data, config = configuration
    config.world.scenario = "bread-bakery"
    controller = Controller(ROOT, data, config)

    class OfflineProxy(TestProxy):
        async def choose(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    controller.proxy = OfflineProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("clock", {"mode": "continuous", "rate": 120})
        await controller.command("step")
        clock = controller.status()["world"]["clock"]
        assert controller.run["status"] == "paused"
        assert not clock["running"]
        await asyncio.sleep(0.1)
        observed = await controller.world.operation("observe", {})
        assert observed["clock"]["seconds"] == clock["seconds"]
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_nonretryable_proxy_error_still_terminates(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)

    class UnauthorizedProxy(TestProxy):
        async def choose(self, *args, **kwargs):
            raise httpx.HTTPStatusError("unauthorized",
                                       request=httpx.Request("POST", "http://localhost/chat/completions"),
                                       response=httpx.Response(401))

    controller.proxy = UnauthorizedProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        assert controller.run["status"] == "error"
        assert controller.run["actions"] == 0
        assert "HTTP 401" in controller.run["reason"]
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_preserving_paused_shutdown_does_not_request_completion_review(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    fake = TestProxy(controller.proxy, [SOLUTION[0]])
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
    finally:
        await controller.shutdown(preserve_paused=True)
    assert controller.run["status"] == "paused"
    assert read_json(controller.events.directory / "run.json")["status"] == "paused"
    assert fake.completion_contexts == []
    assert not any(event["kind"] == "finished" for event in controller.events.events)


@pytest.mark.asyncio
async def test_world_connection_failure_is_not_treated_as_llm_outage(configuration, monkeypatch):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start")

        async def offline_world(*args, **kwargs):
            raise httpx.ConnectError("world transport unavailable")

        monkeypatch.setattr(controller.world, "operation", offline_world)
        await controller.command("step")
        assert controller.run["status"] == "error"
        assert controller.run["model_calls"] == 0
        assert not any(event["kind"] == "control" and event["payload"].get("source") == "llm_unavailable"
                       for event in controller.events.events)
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_new_world_without_planner_changes(configuration):
    data, config = configuration
    config.world.scenario = "sleepy-room"
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [("nap", {"duration": 2}), ("go", {"target": "archway"})])
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.observation["self"]["location_id"] == "courtyard"
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_success_on_exact_action_budget_boundary(configuration):
    data, config = configuration
    config.budgets.max_actions = len(SOLUTION)
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, SOLUTION)
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in SOLUTION:
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.run["actions"] == config.budgets.max_actions
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_control_rejection_does_not_terminate_and_toggles_persist(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, SOLUTION)
    await controller.start_worker()
    try:
        await controller.command("start")
        with pytest.raises(ValueError, match="Unknown actor"):
            await controller.command("need", {"actor_id": "unknown", "need": "sleep", "enabled": True})
        assert controller.run["status"] == "ready"
        await controller.command("need", {"actor_id": "npc-001", "need": "sleep", "enabled": True})
        assert controller.observation["self"]["needs"][1]["enabled"]
        assert read_json(controller.world.state_path)["actors"]["npc-001"]["needs"]["sleep"]["enabled"]
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_broken_lock_stops_agent_as_authoritatively_blocked(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    actions = [("look", {"target": "door"}), ("take", {"target": "rock"})] + [("hit", {"target": "lock", "item": "rock"})] * 3
    controller.proxy = TestProxy(controller.proxy, actions)
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in actions:
            await controller.command("step")
        assert controller.run["status"] == "blocked"
        assert controller.run["verification"]["status"] == "failed"
        assert controller.run["actions"] == 5
        assert "No remaining route" in controller.run["reason"]
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_smash_box_solution_through_real_mcp(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    actions = [
        ("look", {"target": "door"}),
        ("take", {"target": "rock"}),
        ("hit", {"target": "box", "item": "rock"}),
        ("hit", {"target": "box", "item": "rock"}),
        ("drop", {"target": "rock"}),
        ("take", {"target": "key"}),
        ("unlock", {"target": "lock", "item": "key"}),
        ("open", {"target": "door"}),
        ("go", {"target": "door"}),
    ]
    controller.proxy = TestProxy(controller.proxy, actions)
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in actions:
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.run["actions"] == 9
        assert read_json(controller.world.state_path)["entities"]["box"]["health"] == 0
        saved = read_json(controller.world.state_path)
        assert "box-splinters" in saved["entities"]
        assert saved["entities"]["rock"]["health"] == 90
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_invalid_replacement_preserves_existing_episode(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    await controller.start_worker()
    try:
        await controller.command("start")
        run_id = controller.run["id"]
        with pytest.raises(ValueError):
            await controller.command("reset", {"objective": "Unsupported goal"})
        assert controller.run["id"] == run_id
        assert controller.run["status"] == "ready"
        assert controller.world.session is not None
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_two_room_solution_remembers_observed_keys_after_backtracking(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    actions = [
        ("look", {"target": "door-one"}),
        ("open", {"target": "box"}),
        ("take", {"target": "rock"}),
        ("hit", {"target": "box", "item": "rock"}),
        ("hit", {"target": "box", "item": "rock"}),
        ("drop", {"target": "rock"}),
        ("take", {"target": "silver-key"}),
        ("unlock", {"target": "lock-one", "item": "silver-key"}),
        ("open", {"target": "door-one"}),
        ("go", {"target": "door-one"}),
        ("look", {"target": "door-two"}),
        ("go", {"target": "door-one"}),
        ("drop", {"target": "silver-key"}),
        ("take", {"target": "brass-key"}),
        ("go", {"target": "door-one"}),
        ("unlock", {"target": "lock-two", "item": "brass-key"}),
        ("open", {"target": "door-two"}),
        ("go", {"target": "door-two"}),
    ]
    fake = TestProxy(controller.proxy, actions)
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "two-locked-rooms"})
        assert "brass-key" not in controller.memory["known_entities"]
        seen = {entity["id"] for entity in controller.observation["visible_entities"]}
        visited = {controller.observation["self"]["location_id"]}
        assert set(controller.memory["discoveries"]) == seen
        for index, _ in enumerate(actions):
            await controller.command("step")
            seen.update(entity["id"] for entity in controller.observation["visible_entities"])
            visited.add(controller.observation["self"]["location_id"])
            assert set(controller.memory["known_entities"]) == set(controller.memory["discoveries"]) == seen
            assert set(controller.memory["visited_locations"]) == visited
            if index == 0:
                initial_context = json.dumps(fake.contexts[0])
                assert not any(spoiler in initial_context for spoiler in (
                    "two-locked-rooms", "room-two", "door-two", "both rooms", "First door"
                ))
                assert "no memory of how you got here" in initial_context
            if index == 9:
                assert controller.run["status"] == "paused"
                assert controller.run["verification"]["status"] == "active"
                assert controller.observation["self"]["location_id"] == "room-two"
                assert controller.memory["known_entities"]["brass-key"]["last_seen_location"] == "room-one"
                assert controller.memory["known_entities"]["brass-key"]["location"] == "box"
                assert controller.memory["known_entities"]["box"]["name"] == "Broken box"
        assert controller.run["status"] == "succeeded"
        assert controller.run["actions"] == len(actions)
        assert controller.run["verification"]["status"] == "achieved"
        assert controller.run["config"]["world"]["scenario"] == "two-locked-rooms"
        assert config.world.scenario == "escape-room"
        assert "key_id" not in json.dumps(fake.contexts)
    finally:
        await controller.shutdown()

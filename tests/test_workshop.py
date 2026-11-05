import json

import pytest

from npc_sim.controller import Controller
from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.world import World
from test_controller import ROOT, TestProxy, configuration
from test_world import act


ROUTE = [
    ("open", {"target": "lunch-tin"}),
    ("take", {"target": "bread"}),
    ("eat", {"target": "bread"}),
    ("look", {"target": "workshop-door"}),
    ("open", {"target": "cabinet"}),
    ("take", {"target": "mallet"}),
    ("hit", {"target": "cabinet", "item": "mallet"}),
    ("hit", {"target": "cabinet", "item": "mallet"}),
    ("drop", {"target": "mallet"}),
    ("take", {"target": "workshop-key"}),
    ("unlock", {"target": "door-lock", "item": "workshop-key"}),
    ("open", {"target": "workshop-door"}),
    ("go", {"target": "workshop-door"}),
    ("look", {"target": "garden-gate"}),
    ("open", {"target": "garden-gate"}),
    ("go", {"target": "garden-gate"}),
]


@pytest.fixture
def world(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "forgotten-workshop.json"))
    return World(scenario, tmp_path / "world-state.json", "world", "npc", "session")


def test_workshop_initial_view_has_no_hidden_supplies_or_exit_map(world):
    initial = world.observe()
    assert initial["opening"]
    assert world.actor.body.capacity == 1
    assert world.actor.needs["food"].enabled
    assert not any(hidden in json.dumps(initial) for hidden in (
        "workshop-key", "bread", "door-lock", "garden-gate", '"passage"', '"garden"'
    ))
    assert act(world, "go", target="workshop-door")["code"] == "NEED_CONSTRAINT"
    assert act(world, "take", target="cabinet")["code"] == "CAPACITY_EXCEEDED"
    assert act(world, "take", target="lunch-tin")["code"] == "CAPACITY_EXCEEDED"
    assert world.actor.action_clock == 0
    act(world, "open", target="lunch-tin")
    act(world, "take", target="bread")
    assert act(world, "eat", target="bread")["code"] == "RECOVERED"
    assert world.actor.needs["food"].level == 0
    assert world.actor.inventory == []


def test_workshop_requires_dropping_broken_tool_and_discovering_the_remaining_route(world):
    for index, (action, arguments) in enumerate(ROUTE):
        result = act(world, action, **arguments)
        assert result["outcome"] == ("REJECTED" if index == 4 else "APPLIED")
        if index == 6:
            assert world.state.entities["cabinet"].health == 20
            assert world.state.entities["mallet"].health == 4
            assert "workshop-key" not in world.visible_ids()
        if index == 7:
            assert world.state.entities["cabinet"].name == "Splintered cabinet"
            assert world.state.entities["mallet"].name == "Broken mallet"
            assert world.state.entities["mallet"].health == 0
            assert {"workshop-key", "mallet-shards"} <= set(world.visible_ids())
            assert act(world, "take", target="workshop-key")["code"] == "CAPACITY_EXCEEDED"
        if index == 11:
            assert "passage" not in json.dumps(world.observe())
        if index == 12:
            assert world.actor.location_id == "passage"
            assert "garden-gate" in world.visible_ids()
            assert world.evaluate_goal(world.scenario.goal_type)["status"] == "active"
            assert act(world, "take", target="mallet-shards")["outcome"] == "REJECTED"
    assert world.actor.location_id == "garden"
    assert world.actor.needs["food"].level == 40
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "achieved"


def test_workshop_lock_destruction_is_not_an_escape_route(world):
    act(world, "look", target="workshop-door")
    act(world, "take", target="mallet")
    for _ in range(3):
        act(world, "hit", target="door-lock", item="mallet")
    assert world.state.entities["door-lock"].health == 0
    assert world.state.entities["mallet"].health == 0
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "failed"


@pytest.mark.asyncio
async def test_workshop_solution_uses_unchanged_agent_and_real_mcp(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    fake = TestProxy(controller.proxy, ROUTE)
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "forgotten-workshop"})
        for action, arguments in ROUTE:
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.run["actions"] == 16
        assert controller.run["verification"]["status"] == "achieved"
        assert "workshop-key" not in json.dumps(fake.contexts[0])
        assert "garden-gate" not in json.dumps(fake.contexts[0])
        saved = read_json(controller.world.state_path)
        assert saved["entities"]["mallet"]["health"] == 0
        assert saved["actors"][controller.run["actor_id"]]["needs"]["food"]["level"] == 40
    finally:
        await controller.shutdown()

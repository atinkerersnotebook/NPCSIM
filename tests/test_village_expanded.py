import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from npc_sim.contracts import observation
from npc_sim.controller import Controller
from npc_sim.models import Scenario
from npc_sim.storage import read_json, write_json
from npc_sim.world import World
from test_controller import TestProxy, configuration
from test_interactions import InteractionProxy
from test_world import act


ROOT = Path(__file__).resolve().parent.parent


def scenario():
    return Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-expanded.json"))


@pytest.fixture
def cast(tmp_path):
    value = scenario()
    value.village.daily_life = False
    value.village.expanded_rules = False
    value.village.goal_deadlines = {}
    value.village.time_based_turns = False
    value.village.start_minute = 480
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    for rid in ("mara", "jory", "elsie"):
        value.village.residents[rid].job.batch_size = 1
    value.village.residents["elsie"].job.outputs = {"meals": 1}
    value.village.stocks["watermill"] = {"grain": 2, "flour": 0}
    value.village.stocks["bakery"] = {"flour": 1, "bread": 0}
    value.village.stocks["tavern"] = {"bread": 0, "meals": 0}
    first = value.agents[0].id
    world = World(value, tmp_path / "village.json", "expanded", first, f"session-{first}")
    return {agent.id: world.bind(agent.id, f"session-{agent.id}") for agent in value.agents}


def perform(cast, rid, action, **args):
    result = act(cast[rid], action, **args)
    assert result["outcome"] == "APPLIED", result
    return result


def walk(cast, rid, *doors):
    for door in doors:
        perform(cast, rid, "go", target=door)


def test_twelve_real_actors_without_duplicate_positions_and_private_context(cast):
    world = cast["torvin"]
    assert len(cast) == 12
    assert set(world.state.actors) == set(world.state.village.roster)
    assert not set(world.state.actors) & set(world.state.entities)
    assert sum(entity.kind == "location" for entity in world.state.entities.values()) == 15
    assert sum(entity.kind not in {"location", "door"}
               for entity in world.state.entities.values()) >= 50
    contexts = {rid: observation(actor.observe())["village"] for rid, actor in cast.items()}
    assert contexts["torvin"]["personal"]["beliefs"]["shovel"]["location"] == "stable"
    assert contexts["bren"]["personal"]["beliefs"]["shovel"]["location"] == "mill-shed"
    assert "mill-shed" not in json.dumps(contexts["torvin"])
    assert contexts["mara"]["personal"]["beliefs"] == {}
    assert contexts["torvin"]["residents"] == []
    assert all("roster" not in value and "role_contributions" not in value for value in contexts.values())
    assert all(value["personal"]["deliveries"] == [] for rid, value in contexts.items() if rid != "nella")


def test_wait_advances_clock_and_failure_not_other_ai_actions_or_remote_news(cast):
    world = cast["torvin"]
    before = {rid: actor.location_id for rid, actor in world.state.actors.items()}
    stocks = {key: value.copy() for key, value in world.state.village.stocks.items()}
    for _ in range(6):
        perform(cast, "torvin", "village_wait", minutes=60)
    state = world.state.village
    assert state.minute == 840 and state.breakdown
    assert world.state.entities["mill-drive"].broken
    assert {rid: actor.location_id for rid, actor in world.state.actors.items()} == before
    assert state.stocks == stocks
    assert state.role_contributions == {} and state.project_contributions == {}
    assert not any(entry["action"] == "breakdown" for entry in cast["hobb"].observe()["village"]["events"])
    assert any(entry["action"] == "breakdown" for entry in cast["mara"].observe()["village"]["events"])
    perform(cast, "torvin", "village_wait", minutes=60)
    assert sum(entry.action == "breakdown" for entry in state.events) == 1
    assert act(world, "village_request", target="bren", task="dig_well")["outcome"] == "REJECTED"


def test_knowledge_requires_local_testimony_not_omniscient_tool_location(cast):
    walk(cast, "torvin", "square-forge", "square-stable")
    perform(cast, "torvin", "village_observe")
    state = cast["torvin"].state.village
    assert state.residents["torvin"].beliefs["shovel"].location is None
    learn_event = next(entry for entry in state.events if entry.actor == "torvin" and entry.action == "learn")
    assert learn_event.witnesses == ["torvin"]
    assert learn_event not in [state.events[index] for index in state.residents["bren"].memories]
    perform(cast, "torvin", "village_ask", target="bren", topic="shovel")
    belief = state.residents["torvin"].beliefs["shovel"]
    assert belief.location == "mill-shed" and belief.source == "testimony:bren"
    assert not state.residents["mara"].beliefs
    assert act(cast["hobb"], "village_ask", target="bren", topic="shovel")["outcome"] == "REJECTED"
    perform(cast, "torvin", "say", message="I will bring the supplies to the well.")
    assert any("bring the supplies" in state.events[index].result for index in state.residents["bren"].memories)


def test_jobs_cooldowns_capabilities_and_shortages_are_real(cast):
    state = cast["mara"].state.village
    perform(cast, "mara", "village_work")
    assert state.stocks["watermill"] == {"grain": 1, "flour": 1}
    minute = state.minute
    assert act(cast["mara"], "village_work")["code"] == "WORK_COOLDOWN"
    assert state.minute == minute
    assert act(cast["elsie"], "village_work")["code"] == "STOCK_SHORTAGE"
    cast["iona"].actor.body.capabilities.remove("herbalism")
    assert act(cast["iona"], "village_work")["code"] == "CAPABILITY_REQUIRED"
    cast["rowan"].actor.body.grips = ["mouth"]
    assert act(cast["rowan"], "village_work")["code"] == "BODY_HANDLING_REQUIRED"
    assert state.role_contributions == {"mara": 1}
    walk(cast, "jory", "square-bakery")
    assert act(cast["jory"], "village_work")["code"] == "WRONG_WORKPLACE"


def test_merchant_shipments_conserve_stock_coins_and_survive_failed_delivery(cast):
    world = cast["nella"]
    state = world.state.village
    original_coins = sum(state.coins.values())
    original_grain = sum(stock.get("grain", 0) for stock in state.stocks.values())
    assert act(world, "village_load", route=0)["code"] == "WRONG_SOURCE"
    walk(cast, "nella", "square-store", "square-farm")
    perform(cast, "nella", "village_load", route=0)
    assert state.residents["nella"].cargo == {"grain": 1}
    assert world.inventory_units_used() == 1
    restored = World(world.scenario, world.path, "expanded", "nella", "session-nella")
    assert restored.state.village.residents["nella"].cargo == {"grain": 1}
    assert restored.state.village.residents["nella"].delivery == 0
    assert act(world, "take", target="sickle")["outcome"] == "REJECTED"
    assert act(world, "village_load", route=0)["code"] == "CARGO_LOADED"
    assert act(world, "village_deliver")["code"] == "WRONG_DESTINATION"
    walk(cast, "nella", "farm-mill")
    state.coins["mara"] = 0
    world.save()
    assert act(world, "village_deliver")["code"] == "INSUFFICIENT_COINS"
    assert state.residents["nella"].cargo == {"grain": 1}
    state.coins["mara"] = 100
    world.save()
    perform(cast, "nella", "village_deliver")
    assert state.coins["ada"] == 31 and state.coins["mara"] == 99
    assert sum(state.coins.values()) == original_coins
    assert sum(stock.get("grain", 0) for stock in state.stocks.values()) == original_grain
    assert state.residents["nella"].cargo == {} and state.residents["nella"].delivery is None
    assert act(world, "village_deliver")["code"] == "NO_CARGO"


def test_full_recovery_through_real_actions_and_persistence(cast):
    world = cast["torvin"]
    state = world.state.village
    for rid in ["torvin", "bren", "mara", "hobb", "jory", "ada", "rowan", "iona", "finn"]:
        perform(cast, rid, "village_work")
    perform(cast, "garrick", "village_patrol")
    assert act(cast["garrick"], "village_patrol")["code"] == "WORK_COOLDOWN"
    perform(cast, "torvin", "take", target="well-timber")
    perform(cast, "torvin", "take", target="well-rope")
    walk(cast, "torvin", "square-forge", "square-stable")
    perform(cast, "torvin", "village_ask", target="bren", topic="shovel")
    walk(cast, "torvin", "stable-shed")
    perform(cast, "torvin", "take", target="shovel")
    assert state.shovel_recovered
    walk(cast, "torvin", "stable-shed", "square-stable", "square-tavern", "tavern-yard-gate")
    assert act(world, "village_project", project="dig_well")["code"] == "CREW_MISSING"
    walk(cast, "bren", "square-stable", "square-tavern", "tavern-yard-gate")
    for _ in range(4):
        perform(cast, "torvin", "village_project", project="dig_well")
    assert not state.well_dug
    for _ in range(2):
        perform(cast, "bren", "village_project", project="dig_well")
    assert state.well_dug
    assert "well-rope" not in world.actor.inventory and "well-timber" not in world.actor.inventory
    assert world.state.entities["well-rope"].location is None
    assert world.state.entities["shovel"].location == "torvin"
    perform(cast, "hobb", "take", target="mill-beam")
    perform(cast, "hobb", "take", target="joiner-hammer")
    walk(cast, "hobb", "square-workshop", "square-bridge", "bridge-mill")
    assert act(cast["hobb"], "village_project", project="repair_mill")["code"] == "NOT_BROKEN"
    while state.minute < 840:
        perform(cast, "hobb", "village_wait", minutes=min(60, 840 - state.minute))
    for rid in ["hobb", "mara"] * 3:
        perform(cast, rid, "village_project", project="repair_mill")
    assert state.repaired and not world.state.entities["mill-drive"].broken
    assert world.state.entities["mill-beam"].location is None
    perform(cast, "mara", "village_work")
    walk(cast, "nella", "square-store", "square-farm")
    perform(cast, "nella", "village_load", route=0)
    walk(cast, "nella", "farm-mill")
    perform(cast, "nella", "village_deliver")
    perform(cast, "nella", "village_load", route=1)
    walk(cast, "nella", "mill-bakery")
    perform(cast, "nella", "village_deliver")
    perform(cast, "jory", "village_work")
    perform(cast, "nella", "village_load", route=2)
    walk(cast, "nella", "bakery-tavern")
    perform(cast, "nella", "village_deliver")
    assert world.evaluate_goal("village_restored")["status"] == "active"
    perform(cast, "elsie", "village_work")
    assert world.evaluate_goal("village_restored")["status"] == "achieved"
    assert len(state.role_contributions) == 12
    restored = World(world.scenario, world.path, "expanded", "torvin", "session-torvin")
    assert restored.state.village == state
    assert restored.state.actors == world.state.actors
    assert restored.evaluate_goal("village_restored")["status"] == "achieved"


@pytest.mark.asyncio
async def test_all_twelve_take_ai_turns_over_one_mcp_world_with_private_memory(configuration):
    data, config = configuration
    value = scenario()
    value.village.start_minute = 480
    write_json(data / "scenarios" / "millhaven-expanded.json", value.model_dump(mode="json"))
    config.world.scenario = "millhaven-expanded"
    config.budgets.max_seconds = 180
    controller = Controller(ROOT, data, config)
    fake = TestProxy(controller.proxy, [("village_observe", {})] * 12)
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start")
        status = controller.status()
        assert len(status["world"]["actors"]) == len(controller.agents) == 12
        assert {profile["name"] for profile in controller.run["agent_profiles"].values()} == {
            rid.title() for rid in controller.agents}
        assert "blacksmith" in controller.run["agent_profiles"]["torvin"]["identity"]
        session = controller.world.session
        controller.agents["torvin"]["memory"]["private_marker"] = "Torvin-only"
        for rid in controller.agents:
            await controller.command("step")
            assert controller.world.session is session
            assert controller.run["actor_id"] == rid
            assert controller.run["status"] == "paused"
        assert [value["observation"]["self"]["actor_id"] for value in fake.contexts] == list(controller.agents)
        for value in fake.contexts:
            rid = value["observation"]["self"]["actor_id"]
            if rid != "torvin":
                assert "Torvin-only" not in json.dumps(value)
            if rid not in {"torvin", "bren"}:
                assert "mill-shed" not in json.dumps(value["observation"]["village"]["personal"]["beliefs"])
        world = controller.status()["world"]
        assert world["village"]["role_contributions"] == {}
        assert world["village"]["minute"] == 480
        assert all(event["village_minute"] == 480 and event["actor_name"] == event["actor_id"].title()
                   for event in controller.events.events)
        assert len({id(agent["memory"]) for agent in controller.agents.values()}) == 12
        assert len({agent["session_id"] for agent in controller.agents.values()}) == 12
    finally:
        await controller.shutdown()


def test_expanded_config_rejects_missing_cast_and_colliding_actor_objects():
    value = scenario().model_dump(mode="json")
    value["agents"].pop()
    with pytest.raises(ValidationError, match="Every AI village resident"):
        Scenario.model_validate(value)
    value = scenario().model_dump(mode="json")
    value["entities"]["torvin"] = {"name": "Duplicate Torvin", "kind": "villager", "description": "Invalid", "location": "forge"}
    with pytest.raises(ValidationError, match="distinct from entity IDs"):
        Scenario.model_validate(value)


def test_projects_require_actual_supplies_both_skills_and_each_partners_action(cast):
    world = cast["torvin"]
    state = world.state.village
    walk(cast, "torvin", "square-forge", "square-tavern", "tavern-yard-gate")
    walk(cast, "bren", "square-stable", "square-tavern", "tavern-yard-gate")
    assert act(world, "village_project", project="dig_well")["code"] == "SUPPLIES_MISSING"
    assert state.project_contributions == {}
    cast["bren"].actor.body.capabilities.remove("digging")
    assert act(world, "village_project", project="dig_well")["code"] == "CAPABILITY_REQUIRED"
    assert state.project_contributions == {}
    cast["bren"].actor.body.capabilities.append("digging")
    walk(cast, "torvin", "tavern-yard-gate", "square-tavern", "square-forge")
    for eid in ["well-timber", "well-rope"]:
        perform(cast, "torvin", "take", target=eid)
    walk(cast, "torvin", "square-forge", "square-stable", "stable-shed")
    perform(cast, "torvin", "take", target="shovel")
    walk(cast, "torvin", "stable-shed", "square-stable", "square-tavern", "tavern-yard-gate")
    for _ in range(6):
        perform(cast, "torvin", "village_project", project="dig_well")
    assert not state.well_dug
    assert world.state.entities["well-rope"].location == "torvin"
    cast["bren"].actor.body.grips = ["mouth"]
    assert act(world, "village_project", project="dig_well")["code"] == "BODY_HANDLING_REQUIRED"
    assert state.project_contributions["dig_well"] == {"torvin": 6}
    cast["bren"].actor.body.grips = ["hand"]
    perform(cast, "bren", "village_project", project="dig_well")
    assert not state.well_dug
    perform(cast, "bren", "village_project", project="dig_well")
    assert state.well_dug


@pytest.mark.asyncio
async def test_every_villager_can_answer_private_questions_during_and_after_play(configuration, monkeypatch):
    data, config = configuration
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    proxy = InteractionProxy(controller.proxy)
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        before = controller.status()["world"]
        controller.agents["torvin"]["memory"]["private_marker"] = "Torvin-only"
        request = {"run_id": controller.run["id"], "question": "What do you personally remember?"}
        for rid in controller.agents:
            await controller.command("npc_chat", {**request, "actor_id": rid})
            payload = json.loads(proxy.questions[-1][1]["content"])
            assert payload["observation"]["self"]["actor_id"] == rid
            if rid != "torvin":
                assert "Torvin-only" not in json.dumps(payload)
        assert controller.run["actor_id"] == "torvin"
        assert controller.status()["world"] == before
        assert controller.run["model_calls"] == 12
        controller.finish("cancelled", "Ended test episode.")
        outcome = controller.run["status"]

        async def no_world_access(*args, **kwargs):
            raise AssertionError("A follow-up question touched the live world")

        monkeypatch.setattr(controller.world, "operation", no_world_access)
        for rid in controller.agents:
            await controller.command("npc_chat", {**request, "actor_id": rid})
            assert controller.agents[rid]["memory"]["operator_conversation"][-1]["question"] == request["question"]
        assert controller.run["status"] == outcome
        assert controller.run["followup_model_calls"] == 12
        assert controller.run["model_calls"] == 12
        assert controller.run["actions"] == controller.run["cycles"] == 0
        assert controller.status()["world"] == before
    finally:
        await controller.shutdown()

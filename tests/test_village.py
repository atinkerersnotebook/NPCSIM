import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from npc_sim.contracts import observation
from npc_sim.controller import Controller
from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.world import World
from test_controller import TestProxy, configuration
from test_world import act


ROOT = Path(__file__).resolve().parent.parent


def scenario():
    return Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven.json"))


@pytest.fixture
def world(tmp_path):
    return World(scenario(), tmp_path / "village.json", "millhaven", "visitor", "session")


def wait(world, minutes):
    result = act(world, "village_wait", minutes=minutes)
    assert result["outcome"] == "APPLIED", result
    return result


def request(world, target, task):
    result = act(world, "village_request", target=target, task=task)
    assert result["code"] == "TASK_ACCEPTED", result
    return next(reversed(world.state.village.tasks.values()))


def fetch_shovel(world):
    act(world, "go", target="square-forge")
    task = request(world, "torvin", "fetch_shovel")
    wait(world, 120)
    assert task.status == "completed", task
    assert "shovel" in world.actor.inventory
    return task


def test_village_content_and_observation_are_local(world):
    config = world.scenario.village
    assert len(config.residents) == 12
    assert len({resident.occupation for resident in config.residents.values()}) >= 8
    assert sum(entity.kind == "location" for entity in world.state.entities.values()) == 15
    assert sum(entity.kind not in {"location", "door", "villager"}
               for entity in world.state.entities.values()) >= 50
    obs = observation(world.observe())
    assert obs["village"]["time"] == "08:00"
    assert obs["village"]["residents"] == []
    assert "mill-shed" not in json.dumps(obs)
    assert "beliefs" not in obs["village"]
    assert act(world, "village_ask", target="torvin", topic="shovel")["outcome"] == "REJECTED"
    assert world.state.village.minute == 480
    act(world, "look", target="noticeboard")
    act(world, "read", target="noticeboard")
    assert world.state.village.minute == 480


def test_wrong_belief_retrieval_route_learning_delivery_and_audit(world):
    act(world, "go", target="square-forge")
    answer = act(world, "village_ask", target="torvin", topic="shovel")
    assert "stable" in answer["explanation"]
    assert "mill shed" not in answer["explanation"].lower()
    task = request(world, "torvin", "fetch_shovel")
    assert task.status == "blocked"
    assert "urgent" in task.blocker.lower()
    assert "shovel" not in world.actor.inventory
    wait(world, 120)
    state = world.state.village
    assert task.status == "completed"
    assert world.state.entities["shovel"].location == world.actor_id
    assert world.actor.inventory.count("shovel") == 1
    events = state.events
    assert any(entry.actor == "torvin" and entry.action == "search" and entry.target == "stable"
               for entry in events)
    assert any(entry.actor == "torvin" and entry.action == "ask" and entry.target == "bren"
               for entry in events)
    assert any(entry.actor == "torvin" and entry.action == "take" and entry.target == "shovel"
               for entry in events)
    assert any(entry.action == "deliver" for entry in events)
    assert state.residents["torvin"].beliefs["shovel"].location == "forge"
    assert state.residents["torvin"].beliefs["shovel"].source == "observation:delivery"
    assert state.residents["torvin"].trust[world.actor_id] == 1
    assert all(entry.minute >= 480 and entry.result and entry.witnesses for entry in events)
    assert task.evidence and all(index < len(events) for index in task.evidence)


def test_rendezvous_and_capacity_block_honest_delivery(world):
    act(world, "go", target="square-forge")
    task = request(world, "torvin", "fetch_shovel")
    world.actor.body.capacity = 0
    wait(world, 100)
    assert task.status == "blocked"
    assert "space" in task.blocker
    assert world.state.entities["shovel"].location == "torvin"
    assert not world.actor.inventory
    world.actor.body.capacity = 4
    act(world, "go", target="square-forge")
    wait(world, 10)
    assert task.status == "blocked"
    act(world, "go", target="square-forge")
    assert task.status == "completed"
    assert world.actor.inventory == ["shovel"]


def test_blocked_routes_do_not_teleport_residents_and_reopening_resumes_task(world):
    act(world, "go", target="square-forge")
    task = request(world, "torvin", "fetch_shovel")
    world.state.entities["square-forge"].open = False
    wait(world, 60)
    assert task.status == "blocked"
    assert "route" in task.blocker
    assert world.state.entities["torvin"].location == "forge"
    assert world.state.entities["shovel"].location == "mill-shed"
    world.state.entities["square-forge"].open = True
    wait(world, 120)
    assert task.status == "completed"
    assert world.actor.inventory == ["shovel"]


def test_ownership_refusal_and_unverified_testimony(world):
    act(world, "go", target="square-forge")
    assert act(world, "take", target="smith-hammer")["code"] == "PERMISSION_REQUIRED"
    assert act(world, "village_request", target="torvin", task="repair_mill")["code"] == "REQUEST_REFUSED"
    act(world, "look", target="well-rope")
    original = world.state.entities["well-rope"].location
    result = act(world, "village_tell", target="torvin", subject="well-rope", location="village-square")
    assert result["code"] == "BELIEF_REPORTED"
    # Direct observation wins over a claim about a tool sitting in front of the resident.
    belief = world.state.village.residents["torvin"].beliefs["well-rope"]
    assert belief.location == "forge"
    assert belief.source == "observation"
    for door in ["square-forge", "square-stable"]:
        act(world, "go", target=door)
    act(world, "look", target="stable-bucket")
    for door in ["square-stable", "square-forge"]:
        act(world, "go", target=door)
    act(world, "village_tell", target="torvin", subject="stable-bucket", location="forge")
    belief = world.state.village.residents["torvin"].beliefs["stable-bucket"]
    assert belief.location == "forge"
    assert belief.source == "testimony:visitor"
    assert belief.confidence == 0.5
    assert world.state.entities["stable-bucket"].location == "stable"
    assert world.state.entities["well-rope"].location == original
    assert "stable-bucket" not in world.state.village.residents["finn"].beliefs
    assert "well-rope" not in world.state.village.residents["finn"].beliefs


def test_independent_day_production_shortages_schedules_and_real_trades(world):
    initial_coins = sum(world.state.village.coins.values())
    wait(world, 720)
    state = world.state.village
    assert state.minute == 1200
    assert state.breakdown
    assert world.state.entities["mill-drive"].broken
    assert any(entry.actor == "mara" and entry.action == "produce" for entry in state.events)
    assert any(entry.actor == "nella" and entry.action == "trade" for entry in state.events)
    assert any(entry.actor == "jory" and entry.action == "shortage" for entry in state.events)
    assert any(entry.actor == "mara" and entry.action == "job_blocked" for entry in state.events)
    assert all(world.state.entities[rid].location == "tavern" for rid in state.residents)
    assert sum(state.coins.values()) == initial_coins
    assert all(amount >= 0 for stock in state.stocks.values() for amount in stock.values())
    assert all(world.actor_id in entry["witnesses"] for entry in world.observe()["village"]["events"])
    assert not any(entry["action"] in {"produce", "breakdown", "shortage"}
                   for entry in world.observe()["village"]["events"])
    wait(world, 180)
    assert world.observe()["village"]["time"] == "23:00"
    assert all(world.state.entities[rid].location == world.scenario.village.residents[rid].home
               for rid in state.residents)
    wait(world, 600)
    assert world.observe()["village"]["day"] == 2
    assert world.observe()["village"]["time"] == "09:00"


def test_mill_failure_changes_flour_and_bread_production(world):
    wait(world, 360)
    state = world.state.village
    before = len([entry for entry in state.events if entry.actor == "mara" and entry.action == "produce"])
    wait(world, 240)
    after = len([entry for entry in state.events if entry.actor == "mara" and entry.action == "produce"])
    assert before > 0
    assert before == after
    assert state.stocks["watermill"]["flour"] == 0
    assert state.stocks["bakery"]["flour"] == 0
    assert state.residents["jory"].activity == "Shortage: flour"
    assert not state.repaired


def test_cooperation_repair_resupply_and_persistent_world(world):
    fetch_shovel(world)
    act(world, "drop", target="shovel")
    task = request(world, "torvin", "dig_well")
    wait(world, 150)
    state = world.state.village
    assert task.status == "completed", task
    assert state.well_dug
    assert "timber-lined well" in world.state.entities["tavern-yard"].description
    assert world.state.entities["well-timber"].location is None
    assert world.state.entities["well-rope"].location is None
    assert world.state.entities["shovel"].location == "torvin"
    assert any(entry.action == "cooperate" and entry.target == "bren" for entry in state.events)
    act(world, "go", target="square-forge")
    act(world, "go", target="square-workshop")
    task = request(world, "hobb", "repair_mill")
    wait(world, 300)
    assert task.status == "completed", task
    assert state.repaired
    assert not world.state.entities["mill-drive"].broken
    assert "new beam" in world.state.entities["mill-drive"].description
    assert world.state.entities["mill-beam"].location is None
    assert world.state.entities["joiner-hammer"].location == "hobb"
    assert state.bread_after_repair > 0
    assert all(world.evaluate_goal("village_restored")["evidence"]["checks"].values())
    assert world.evaluate_goal("village_restored")["status"] == "achieved"
    loaded = World(world.scenario, world.path, "millhaven", "visitor", "session")
    assert loaded.state.model_dump() == world.state.model_dump()
    assert loaded.observe()["village"] == world.observe()["village"]
    assert loaded.evaluate_goal("village_restored")["status"] == "achieved"


def test_blocked_project_does_not_consume_materials_or_finish(world):
    act(world, "go", target="square-forge")
    task = request(world, "torvin", "dig_well")
    wait(world, 120)
    assert task.status == "blocked"
    assert not world.state.village.well_dug
    assert world.state.entities["well-rope"].location == "forge"
    assert not any(entry.action == "complete" for entry in world.state.village.events)
    assert world.evaluate_goal("village_restored")["status"] == "active"
    result = act(world, "village_cancel", task_id="village-task-1")
    assert result["code"] == "TASK_CANCELLED"
    assert task.status == "cancelled"


def test_local_stock_trade_conserves_money_and_resources(world):
    # Iona stays at her cottage workplace, so her stock can be traded without chasing the courier.
    act(world, "go", target="square-cottages")
    state = world.state.village
    before = state.coins.copy()
    herbs = state.stocks["cottages"]["herbs"]
    assert act(world, "village_trade", target="iona", resource="herbs", direction="buy")["code"] == "TRADED"
    assert state.stocks["cottages"]["herbs"] == herbs - 1
    assert state.stocks["visitor:visitor"]["herbs"] == 1
    assert state.coins["visitor"] == before["visitor"] - 1
    assert state.coins["iona"] == before["iona"] + 1
    assert act(world, "village_trade", target="iona", resource="herbs", direction="sell")["code"] == "TRADED"
    assert state.coins["visitor"] == before["visitor"]
    assert state.coins["iona"] == before["iona"]
    assert sum(state.coins.values()) == sum(before.values())
    assert state.stocks["visitor:visitor"]["herbs"] == 0
    assert act(world, "village_trade", target="iona", resource="herbs", direction="sell")["code"] == "TRADE_BLOCKED"


def test_wait_validation_and_idempotency(world):
    for minutes in (0, 7, 1445, "10"):
        assert act(world, "village_wait", minutes=minutes)["outcome"] == "REJECTED"
    assert world.state.village.minute == 480
    revision = world.revision()
    first = world.act("wait-once", revision, "village_wait", {"minutes": 60})
    assert world.act("wait-once", revision, "village_wait", {"minutes": 60}) == first
    assert world.state.village.minute == 540
    assert world.act("stale", revision, "village_wait", {"minutes": 60})["code"] == "STALE_OBSERVATION"
    assert world.state.village.minute == 540


def test_helper_refusal_blocks_cooperation_without_consuming_supplies(world):
    fetch_shovel(world)
    act(world, "drop", target="shovel")
    world.state.village.residents["bren"].trust["torvin"] = -3
    task = request(world, "torvin", "dig_well")
    wait(world, 60)
    assert task.status == "blocked"
    assert "declines" in task.blocker
    assert not world.state.village.well_dug
    assert world.state.entities["well-rope"].location == "forge"


def test_project_requested_at_night_resumes_when_helper_returns_to_work(world):
    wait(world, 870)
    act(world, "go", target="square-cottages")
    task = request(world, "hobb", "repair_mill")
    assert task.status == "blocked"
    assert "working hours" in task.blocker
    wait(world, 660)
    assert task.status == "completed", task
    assert world.state.village.repaired


@pytest.mark.parametrize("mutation", [
    lambda data: data["village"]["residents"]["torvin"].update(home="missing"),
    lambda data: data["village"]["stocks"]["forge"].update(iron=-1),
    lambda data: data["village"]["owners"].update(shovel="missing"),
    lambda data: data["village"].update(shovel="missing"),
    lambda data: data["village"]["residents"]["bren"]["beliefs"][0].update(location="missing"),
    lambda data: data["village"]["residents"]["torvin"]["schedule"].reverse(),
    lambda data: data["village"]["stocks"]["forge"].update(iron=1.5),
    lambda data: data["village"]["stocks"]["forge"].update(iron=True),
])
def test_invalid_village_config_fails_at_load(mutation):
    data = read_json(ROOT / "data" / "scenarios" / "millhaven.json")
    mutation(data)
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


@pytest.mark.asyncio
async def test_millhaven_runs_through_existing_controller_and_mcp(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    route = [
        ("go", {"target": "square-forge"}),
        ("village_ask", {"target": "torvin", "topic": "shovel"}),
        ("village_request", {"target": "torvin", "task": "fetch_shovel"}),
        ("village_wait", {"minutes": 120}),
        ("drop", {"target": "shovel"}),
        ("village_request", {"target": "torvin", "task": "dig_well"}),
        ("village_wait", {"minutes": 150}),
        ("go", {"target": "square-forge"}),
        ("go", {"target": "square-workshop"}),
        ("village_request", {"target": "hobb", "task": "repair_mill"}),
        ("village_wait", {"minutes": 300}),
    ]
    controller.proxy = TestProxy(controller.proxy, route)
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "millhaven"})
        for _ in route:
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.observation["village"]["tasks"][0]["status"] == "completed"
        saved = read_json(controller.world.state_path)
        assert saved["village"]["residents"]["torvin"]["memories"]
        assert saved["village"]["well_dug"]
        assert saved["village"]["repaired"]
        assert saved["village"]["bread_after_repair"] > 0
        assert all(controller.run["verification"]["evidence"]["checks"].values())
    finally:
        await controller.shutdown()

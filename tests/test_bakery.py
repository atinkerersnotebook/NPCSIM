import json

import pytest

from npc_sim.models import ClockRequest, Scenario, SimulationClock
from npc_sim.storage import read_json, write_json
from npc_sim.world import World
from test_controller import ROOT, configuration
from test_world import act


@pytest.fixture
def bakery(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("npc_sim.world.time.monotonic", lambda: now[0])
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "bread-bakery.json"))
    world = World(scenario, tmp_path / "world-state.json", "world", "npc", "session")

    def advance(seconds):
        now[0] += seconds / world.state.clock.rate
        world.tick()

    world.state.clock.running = True
    world.state.clock.mode = "continuous"
    return world, advance, now


def prepare(world):
    assert act(world, "open", target="bowl-cupboard")["outcome"] in {"APPLIED", "NO_CHANGE"}
    for bowl in world.scenario.bakery.bowls:
        assert act(world, "take", target=bowl)["outcome"] == "APPLIED"
    assert act(world, "go", target="storeroom-door")["outcome"] == "APPLIED"
    assert act(world, "open", target="ingredient-cupboard")["outcome"] == "APPLIED"
    for bowl in world.scenario.bakery.bowls:
        for ingredient in world.scenario.bakery.ingredients:
            assert act(world, "measure", target=bowl, ingredient=ingredient)["outcome"] == "APPLIED"
    assert act(world, "go", target="storeroom-door")["outcome"] == "APPLIED"
    for machine, outlet in (("mixer", "mixer-outlet"), ("oven-a", "oven-a-outlet"), ("oven-b", "oven-b-outlet")):
        assert act(world, "plug", target=machine, outlet=outlet)["outcome"] == "APPLIED"
        assert act(world, "turn_on", target=machine)["outcome"] == "APPLIED"


def applied(world, action, **arguments):
    result = act(world, action, **arguments)
    assert result["outcome"] == "APPLIED", result
    return result


def test_bakery_starts_with_hidden_bowls_ingredients_and_no_timers(bakery):
    world, _, _ = bakery
    value = world.observe()
    assert value["clock"]["rate"] == 120
    assert value["bread_batches"] == {}
    assert value["ingredient_stock"] == {}
    assert '"batch-a"' not in json.dumps(value)
    assert '"yeast"' not in json.dumps(value)
    assert world.evaluate_goal("bread_batches_served")["status"] == "active"


def test_clock_is_real_scaled_paused_persisted_and_changes_rate_without_jumping(bakery):
    world, _, now = bakery
    now[0] += 60
    world.tick()
    assert world.state.clock.seconds == 7200
    assert world.state.clock.real_seconds == 60
    write_json(world.path.parent / "clock-command.json", {
        "id": "rate", "world_id": "world", "rate": 60, "running": True})
    world.tick()
    now[0] += 10
    world.tick()
    assert world.state.clock.seconds == 7800
    assert world.state.clock.real_seconds == 70
    write_json(world.path.parent / "clock-command.json", {
        "id": "pause", "world_id": "world", "rate": 60, "running": False})
    world.tick()
    revision = world.revision()
    now[0] += 1000
    world.tick()
    assert world.state.clock.seconds == 7800
    assert world.revision() == revision
    reloaded = World(world.scenario, world.path, "world", "npc", "session")
    assert reloaded.state.clock == world.state.clock
    assert reloaded.observe()["clock"]["seconds"] == 7800


@pytest.mark.parametrize("rate", [0, 601, float("nan"), float("inf")])
def test_clock_rejects_invalid_rates(rate):
    with pytest.raises(ValueError):
        ClockRequest(rate=rate)


def test_finite_measurements_locality_power_and_equipment_contention(bakery):
    world, advance, _ = bakery
    applied(world, "open", target="bowl-cupboard")
    assert act(world, "mix_dough", target="batch-a", equipment="mixer")["code"] == "INGREDIENTS_MISSING"
    assert world.state.batches == {}
    prepare(world)
    assert world.state.ingredient_stock == {"flour": 0, "water": 0, "yeast": 0, "salt": 0}
    applied(world, "turn_off", target="mixer")
    assert act(world, "mix_dough", target="batch-a", equipment="mixer")["code"] == "NO_POWER"
    applied(world, "turn_on", target="mixer")
    applied(world, "mix_dough", target="batch-a", equipment="mixer")
    assert act(world, "mix_dough", target="batch-b", equipment="mixer")["code"] == "EQUIPMENT_BUSY"
    assert act(world, "take", target="batch-a")["code"] == "BATCH_IN_PROGRESS"
    advance(599)
    assert act(world, "rise_dough", target="batch-a")["outcome"] == "REJECTED"
    advance(1)
    applied(world, "rise_dough", target="batch-a")
    applied(world, "mix_dough", target="batch-b", equipment="mixer")
    assert world.state.batches["batch-a"].stage == "rising"
    assert world.state.batches["batch-b"].stage == "mixing"
    assert world.actor.inventory == ["batch-c"]


def test_three_interleaved_batches_cool_and_serve_with_shared_ovens(bakery):
    world, advance, _ = bakery
    prepare(world)
    for bowl in ("batch-a", "batch-b", "batch-c"):
        applied(world, "mix_dough", target=bowl, equipment="mixer")
        advance(600)
        applied(world, "rise_dough", target=bowl)
    advance(6000)
    applied(world, "shape_dough", target="batch-a")
    advance(600)
    applied(world, "shape_dough", target="batch-b")
    advance(600)
    applied(world, "shape_dough", target="batch-c")
    advance(1500)
    applied(world, "bake_bread", target="batch-a", equipment="oven-a")
    advance(600)
    applied(world, "bake_bread", target="batch-b", equipment="oven-b")
    advance(600)
    assert act(world, "bake_bread", target="batch-c", equipment="oven-a")["code"] == "EQUIPMENT_BUSY"
    advance(600)
    applied(world, "unload_bread", target="batch-a")
    applied(world, "bake_bread", target="batch-c", equipment="oven-a")
    advance(600)
    applied(world, "unload_bread", target="batch-b")
    advance(600)
    applied(world, "serve_bread", target="batch-a")
    assert world.evaluate_goal("bread_batches_served")["status"] == "active"
    advance(600)
    applied(world, "unload_bread", target="batch-c")
    applied(world, "serve_bread", target="batch-b")
    advance(1200)
    applied(world, "serve_bread", target="batch-c")
    assert world.evaluate_goal("bread_batches_served")["status"] == "achieved"
    reloaded = World(world.scenario, world.path, "world", "npc", "session")
    assert reloaded.evaluate_goal("bread_batches_served")["status"] == "achieved"


@pytest.mark.parametrize("stage", ["rising", "proofing", "baking"])
def test_missed_handling_windows_ruin_batch_even_without_actions(bakery, stage):
    world, advance, _ = bakery
    prepare(world)
    applied(world, "mix_dough", target="batch-a", equipment="mixer")
    advance(600)
    applied(world, "rise_dough", target="batch-a")
    if stage in {"proofing", "baking"}:
        advance(7200)
        applied(world, "shape_dough", target="batch-a")
    if stage == "baking":
        advance(2700)
        applied(world, "bake_bread", target="batch-a", equipment="oven-a")
    batch = world.state.batches["batch-a"]
    advance(batch.deadline - world.state.clock.seconds + 1)
    assert batch.stage == "ruined"
    assert world.evaluate_goal("bread_batches_served")["status"] == "failed"
    assert world.observe()["bread_batches"]["batch-a"]["failure"]


def test_duplicate_action_does_not_measure_twice_and_readiness_invalidates_stale_actions(bakery):
    world, advance, _ = bakery
    prepare(world)
    revision = world.revision()
    request = ("mix-once", revision, "mix_dough", {"target": "batch-a", "equipment": "mixer"})
    first = world.act(*request)
    advance(600)
    assert world.act(*request) == first
    assert world.state.batches["batch-a"].stage == "mixed"
    assert world.act("stale", revision, "rise_dough", {"target": "batch-a"})["code"] == "STALE_OBSERVATION"


def test_switching_off_occupied_equipment_ruins_batch(bakery):
    world, _, _ = bakery
    prepare(world)
    applied(world, "mix_dough", target="batch-a", equipment="mixer")
    applied(world, "turn_off", target="mixer")
    assert world.state.batches["batch-a"].stage == "ruined"
    assert world.evaluate_goal("bread_batches_served")["status"] == "failed"


def test_fixed_action_costs_are_advertised_atomic_and_idempotent(bakery):
    world, _, now = bakery
    clock = world.state.clock
    clock.mode, clock.running = "decision_paused", False
    value = world.observe()
    assert next(action for action in value["actions"] if action["id"] == "open")["simulation_seconds"] == 30
    assert next(action for action in value["actions"] if action["id"] == "read")["simulation_seconds"] == 0
    now[0] += 11
    assert world.observe()["clock"]["seconds"] == 0
    request = ("open-once", world.revision(), "open", {"target": "bowl-cupboard"})
    first = world.act(*request)
    assert first["outcome"] == "APPLIED"
    assert clock.seconds == 30
    assert "30 simulated seconds" in first["explanation"]
    assert world.act(*request) == first
    assert clock.seconds == 30
    assert act(world, "open", target="bowl-cupboard")["outcome"] == "NO_CHANGE"
    assert act(world, "mix_dough", target="batch-a", equipment="mixer")["code"] == "INGREDIENTS_MISSING"
    applied(world, "read", target="recipe-board")
    assert clock.seconds == 30
    assert clock.real_seconds == 0


def test_setup_timers_start_after_action_and_other_batches_progress_during_actions(bakery):
    world, advance, _ = bakery
    prepare(world)
    clock = world.state.clock
    clock.mode, clock.running = "decision_paused", False
    applied(world, "mix_dough", target="batch-a", equipment="mixer")
    assert clock.seconds == 60
    assert world.state.batches["batch-a"].ready_at == 660
    clock.running = True
    advance(570)
    clock.running = False
    applied(world, "drop", target="batch-b")
    assert clock.seconds == 660
    assert world.state.batches["batch-a"].stage == "mixed"


def test_thinking_cannot_burn_bread_but_other_physical_work_can_miss_deadlines(bakery):
    world, _, now = bakery
    prepare(world)
    clock = world.state.clock
    clock.mode, clock.running = "decision_paused", False
    batch = world.state.batches["batch-a"]
    batch.stage, batch.equipment = "baked", "oven-a"
    batch.ready_at, batch.deadline = 0, 1
    world.state.entities["batch-a"].location = "oven-a"
    world.actor.inventory.remove("batch-a")
    before = clock.seconds
    now[0] += 11
    world.observe()
    assert clock.seconds == before
    applied(world, "unload_bread", target="batch-a")
    assert batch.stage == "cooling"
    assert batch.ready_at == clock.seconds + 1200
    batch.stage, batch.deadline = "proofed", clock.seconds + 10
    applied(world, "drop", target="batch-b")
    assert batch.stage == "ruined"
    assert world.evaluate_goal("bread_batches_served")["status"] == "failed"


def test_decision_paused_three_batch_shift_still_requires_interleaving_and_timed_waits(bakery):
    world, advance, _ = bakery
    prepare(world)
    clock = world.state.clock
    clock.mode, clock.running = "decision_paused", False
    interleaved, waits = False, 0
    for _ in range(60):
        batches = world.state.batches
        interleaved |= sum(batch.stage in {"rising", "proofing", "baking", "cooling"}
                           for batch in batches.values()) > 1
        if world.evaluate_goal("bread_batches_served")["status"] == "achieved":
            break
        busy = {batch.equipment for batch in batches.values()}
        selected = None
        for stage, action in [("baked", "unload_bread"), ("cooled", "serve_bread"),
                              ("proofed", "bake_bread"), ("risen", "shape_dough"),
                              ("mixed", "rise_dough"), ("measuring", "mix_dough")]:
            machines = ["oven-a", "oven-b"] if action == "bake_bread" else ["mixer"] if action == "mix_dough" else []
            free = next((eid for eid in machines if eid not in busy), None)
            if machines and free is None:
                continue
            bowl = next((eid for eid, batch in batches.items() if batch.stage == stage), None)
            if bowl:
                selected = (action, {"target": bowl, **({"equipment": free} if machines else {})})
                break
        if selected:
            applied(world, selected[0], **selected[1])
        else:
            ready = min(batch.ready_at for batch in batches.values()
                        if batch.ready_at is not None and batch.ready_at > clock.seconds)
            clock.running = True
            advance(ready - clock.seconds)
            clock.running = False
            waits += 1
    assert world.evaluate_goal("bread_batches_served")["status"] == "achieved"
    assert interleaved and waits > 0


def test_legacy_clock_keeps_continuous_mode():
    assert SimulationClock.model_validate({"seconds": 0, "rate": 120, "running": False}).mode == "continuous"


@pytest.mark.asyncio
async def test_slow_model_calls_and_repairs_freeze_bakery_clock(configuration):
    import asyncio

    from npc_sim.controller import Controller
    from test_controller import TestProxy

    data, config = configuration
    controller = Controller(ROOT, data, config)
    samples = []
    woke_from_wait = asyncio.Event()

    class SlowProxy(TestProxy):
        async def choose(self, model, messages, correlation_id, timeout):
            before = (await controller.world.operation("observe", {}))["clock"].copy()
            await asyncio.sleep(0.2)
            after = (await controller.world.operation("observe", {}))["clock"].copy()
            samples.append((before, after))
            result = await super().choose(model, messages, correlation_id, timeout)
            if result[0].action_id == "look":
                woke_from_wait.set()
            return result

    controller.proxy = SlowProxy(controller.proxy, [
        ("invented", {}), ("read", {"target": "recipe-board"}), ("open", {"target": "bowl-cupboard"}),
        ("wait", {"seconds": 1}), ("look", {}), ("read", {"target": "recipe-board"})])
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "bread-bakery"})
        first = await controller.command("step")
        assert first["run"]["model_calls"] == 2
        assert first["world"]["clock"]["seconds"] == 0
        second = await controller.command("step")
        assert second["world"]["clock"]["seconds"] == 30
        assert len(samples) == 3
        assert all(not before["running"] and before == after for before, after in samples)
        await controller.command("run")
        await asyncio.wait_for(woke_from_wait.wait(), timeout=10)
        after_wait = await controller.command("pause")
        assert after_wait["world"]["clock"]["seconds"] > 30
        assert len(samples) == 5
        assert all(not before["running"] and before == after for before, after in samples)
        await controller.command("clock", {"rate": 600, "mode": "continuous"})
        await controller.command("step")
        before, after = samples[-1]
        assert before["running"] and after["seconds"] > before["seconds"]
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_controller_clock_pause_resume_and_wait_do_not_waste_model_calls(configuration):
    import asyncio

    from npc_sim.controller import Controller
    from test_controller import TestProxy

    data, config = configuration
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [("wait", {"seconds": 7200})] * 10)
    await controller.start_worker()
    try:
        started = await controller.command("start", {"scenario_id": "bread-bakery"})
        assert started["world"]["clock"]["running"] is False
        assert started["run"]["config"]["budgets"]["max_seconds"] == 600
        updated = await controller.command("clock", {"rate": 600})
        assert updated["world"]["clock"]["rate"] == 600
        await controller.command("run")
        await asyncio.sleep(0.7)
        paused = await controller.command("pause")
        assert paused["world"]["clock"]["seconds"] > 0
        assert paused["world"]["clock"]["running"] is False
        calls = paused["run"]["model_calls"]
        seconds = paused["world"]["clock"]["seconds"]
        budget_elapsed = paused["run"]["elapsed_seconds"]
        await asyncio.sleep(0.4)
        assert controller.status()["world"]["clock"]["seconds"] == seconds
        assert controller.status()["run"]["elapsed_seconds"] == budget_elapsed
        await controller.command("resume")
        await asyncio.sleep(0.6)
        paused = await controller.command("pause")
        assert paused["world"]["clock"]["seconds"] > seconds
        assert paused["run"]["model_calls"] <= calls + 1
        assert paused["run"]["status"] == "paused"
        await controller.command("cancel")
        assert controller.status()["world"]["clock"]["running"] is False
    finally:
        await controller.shutdown()

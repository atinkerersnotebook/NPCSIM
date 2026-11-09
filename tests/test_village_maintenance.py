import copy

import pytest
from pydantic import ValidationError

from npc_sim import village, village_extensions, village_maintenance, village_turns
from npc_sim.controller import Controller
from npc_sim.episode_saves import read_save
from npc_sim.living_village import build_scenario
from npc_sim.models import VillageMaintenanceConfig
from npc_sim.storage import read_json
from npc_sim.world import World
from test_controller import ROOT, TestProxy, configuration
from test_village_expanded import scenario
from test_world import act


def make_world(tmp_path, supplies=None):
    value = scenario()
    value.village.start_minute = 600
    value.village.maintenance = VillageMaintenanceConfig.model_validate({"tasks": {
        "forge-upkeep": {"title": "Service forge tools", "assignee": "torvin",
                         "location": "forge", "equipment": ["anvil"],
                         "release_minute": 0, "supplies": supplies or {}},
    }})
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "maintenance.json", "maintenance", "torvin", "session-torvin")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    return world


def job(world):
    return next(iter(world.state.village.maintenance.jobs.values()))


def start(world):
    return act(world, "village_maintain", job=next(iter(world.state.village.maintenance.jobs)))


def test_maintenance_requires_real_time_and_counts_as_work_once(tmp_path):
    world = make_world(tmp_path)
    before = world.state.entities["anvil"].health
    revision = world.revision()
    arguments = {"job": next(iter(world.state.village.maintenance.jobs))}
    result = world.act("start-once", revision, "village_maintain", arguments)
    assert result["code"] == "MAINTENANCE_STARTED"
    assert world.act("start-once", revision, "village_maintain", arguments) == result
    assert job(world).worked_minutes == 0 and len(job(world).chunks) == 1
    assert act(world, "go", target="forge-square")["outcome"] == "REJECTED"
    village.advance(world, 15)
    assert job(world).worked_minutes == 15 and job(world).completed_at is None
    assert start(world)["outcome"] == "APPLIED"
    village.advance(world, 15)
    assert job(world).worked_minutes == 30 and job(world).completed_at == 630
    assert world.state.village.time_use["torvin"].work == 30
    assert world.state.village.role_contributions["torvin"] == 1
    assert world.state.entities["anvil"].health == before
    assert not any(a["id"] == "village_maintain" for a in world.observe()["actions"])
    assert any(e.action == "maintenance_completed" for e in world.state.village.events)


def test_interruption_keeps_only_earned_progress_and_charges_supplies_once(tmp_path):
    world = make_world(tmp_path, {"iron": 1})
    iron = world.state.village.stocks["forge"]["iron"]
    assert start(world)["outcome"] == "APPLIED"
    village.advance(world, 5)
    assert act(world, "village_maintenance_pause")["code"] == "MAINTENANCE_PAUSED"
    assert job(world).worked_minutes == 5
    assert world.state.village.stocks["forge"]["iron"] == iron - 1
    village.advance(world, 5)
    assert start(world)["outcome"] == "APPLIED"
    village.advance(world, 15)
    assert job(world).worked_minutes == 20
    assert world.state.village.stocks["forge"]["iron"] == iron - 1


def test_remote_wrong_assignee_missing_tools_and_supplies_do_not_complete_work(tmp_path):
    world = make_world(tmp_path, {"iron": 100})
    before = copy.deepcopy(world.state.village.stocks)
    world.actor.location_id = "tavern"
    assert start(world)["code"] == "NOT_PRESENT"
    world.actor.location_id = "forge"
    assert start(world)["code"] == "MISSING_MAINTENANCE_SUPPLIES"
    world.state.entities["anvil"].location = "tavern"
    assert start(world)["code"] == "MISSING_MAINTENANCE_TOOLS"
    other = world.bind("hobb", "session-hobb")
    assert village_maintenance.apply(other, "village_maintain", {
        "job": next(iter(world.state.village.maintenance.jobs))})[1] == "NOT_ASSIGNED"
    assert world.state.village.stocks == before and job(world).worked_minutes == 0


def test_unfinished_jobs_carry_forward_without_duplicate_backlogs(tmp_path):
    world = make_world(tmp_path)
    identifier = next(iter(world.state.village.maintenance.jobs))
    assert start(world)["outcome"] == "APPLIED"
    village.advance(world, 15)
    village.advance(world, 1440 - world.state.village.minute)
    assert list(world.state.village.maintenance.jobs) == [identifier]
    assert job(world).worked_minutes == 15
    assert world.state.village.dashboard.maintenance[0].status == "overdue"
    assert any(e.action == "maintenance_carried_over" for e in world.state.village.events)
    assert village_turns.next_actor(world, stop_at_minute=1440) is None


def test_completed_jobs_recur_next_day_and_survive_reload(tmp_path):
    world = make_world(tmp_path)
    start(world)
    village.advance(world, 15)
    start(world)
    village.advance(world, 15)
    village.advance(world, 1440 - world.state.village.minute)
    assert len(world.state.village.maintenance.jobs) == 2
    jobs = list(world.state.village.maintenance.jobs.values())
    assert jobs[0].completed_at == 630 and jobs[1].worked_minutes == 0 and jobs[1].day == 1
    world.save()
    loaded = World(world.scenario, world.path, "maintenance", "torvin", "session-torvin")
    assert loaded.state.village.maintenance == world.state.village.maintenance


def test_meals_sleep_and_optional_activity_priority_are_preserved(tmp_path):
    world = make_world(tmp_path)
    assert village_maintenance.action_blocker(world, "village_leisure", "torvin")[0] == "MAINTENANCE_DUE"
    world.state.village.minute = 1135
    assert start(world)["code"] == "MEAL_CONFLICT"
    world.state.village.minute = 1320
    assert start(world)["code"] in {"REST_DUE", "BEDTIME_CONFLICT"}


def test_configuration_is_atomic_idempotent_and_only_starts_current_day(tmp_path):
    world = make_world(tmp_path)
    revision = world.revision()
    value = {"maintenance": village_maintenance.example_config(world.scenario)}
    result = village_extensions.configure(world, "maintenance-config", revision, value)
    assert village_extensions.configure(world, "maintenance-config", revision, value) == result
    before = world.state.model_dump(mode="json")
    with pytest.raises(ValueError):
        village_extensions.configure(world, "bad-maintenance", world.revision(), {
            "maintenance": {"tasks": {"bad": {"title": "Unknown worker", "assignee": "nobody", "location": "forge"}}}})
    assert world.state.model_dump(mode="json") == before
    assert all(task.day == 0 for task in world.state.village.maintenance.jobs.values())


def test_shared_equipment_and_actual_body_capabilities_are_required(tmp_path):
    world = make_world(tmp_path)
    world.actor.body.capabilities.remove("manipulate")
    assert start(world)["code"] == "MISSING_CAPABILITY"
    world.actor.body.capabilities.append("manipulate")
    assert start(world)["outcome"] == "APPLIED"
    assert village_maintenance.action_blocker(world, "village_work", "torvin")[0] == "EQUIPMENT_MAINTENANCE"


def test_legacy_is_opt_out_and_living_defaults_assign_both_workers():
    legacy = scenario()
    assert legacy.village.maintenance is None
    living = build_scenario(legacy)
    tasks = living.village.maintenance.tasks
    assert len(tasks) == 4
    assert sum(task.duration for task in tasks.values() if task.assignee == "torvin") == 60
    assert sum(task.duration for task in tasks.values() if task.assignee == "hobb") == 60
    with pytest.raises(ValidationError):
        VillageMaintenanceConfig.model_validate({"tasks": {
            "bad": {"title": "Bad quantities", "assignee": "torvin", "location": "forge", "supplies": {"iron": True}}}})


@pytest.mark.asyncio
async def test_runtime_maintenance_is_saved_and_loaded_with_no_model_calls(configuration):
    data, config = configuration
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, [])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        value = scenario()
        await controller.command("configure_village", {
            "run_id": controller.run["id"], "request_id": "town-maintenance",
            "expected_revision": controller.observation["observation_revision"],
            "configuration": {"maintenance": village_maintenance.example_config(value)}})
        saved = await controller.command("save_episode", {"run_id": controller.run["id"], "name": "Maintenance ready"})
        snapshot = read_save(data, saved["save"]["id"])
        assert snapshot.world.village.maintenance is not None
        assert snapshot.scenario.village.maintenance is None
        await controller.command("load_episode", {
            "save_id": snapshot.id, "current_run_id": controller.run["id"]})
        assert read_json(controller.world.state_path)["village"]["maintenance"] is not None
        assert controller.run["status"] == "paused"
        assert controller.run["actions"] == controller.run["model_calls"] == 0
        assert not proxy.contexts and not proxy.completion_contexts
    finally:
        await controller.shutdown()

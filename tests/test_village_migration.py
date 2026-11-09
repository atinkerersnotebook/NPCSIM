import copy

import pytest

from npc_sim import episode_saves, village
from npc_sim.controller import Controller
from npc_sim.models import Entity, VillageShipment
from npc_sim.storage import read_json, write_json
from npc_sim.village_migration import capture_midnight, publish_copy, upgrade_midnight
from npc_sim.world import World
from test_controller import ROOT, configuration
from test_episode_saves import SaveProxy


async def midnight_fixture(configuration):
    data, config = configuration
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    controller.proxy = SaveProxy(controller.proxy, [])
    await controller.start_worker()
    await controller.command("start")
    result = await controller.command("save_episode", {"run_id": controller.run["id"], "name": "Fixture"})
    await controller.shutdown()
    saved = episode_saves.read_save(data, result["save"]["id"])
    path = data / "worlds" / saved.world.world_id / "world-state.json"
    world = World(saved.scenario, path, saved.world.world_id, saved.run["actor_id"], saved.run["session_id"])
    village.advance(world, 1380 - world.state.village.minute)
    state = world.state.village
    state.coins["elsie"] = 1
    village.event(world, "jory", "produce", "flour", "Old-day flour production.")
    for actor_id, person in state.residents.items():
        person.life.sleeping = True
        person.life.sleep_until = 1440 + saved.scenario.village.residents[actor_id].wake_minute
        person.available_at = person.life.sleep_until
    state.residents["jory"].trust["elsie"] = 73
    state.residents["jory"].shipments.append(VillageShipment(
        resource="bread", quantity=1, seller="jory", buyer="elsie", destination="tavern", price=1))
    state.residents["jory"].cargo["bread"] = 1
    world.state.entities["festival-drum"] = Entity(
        name="Previously repaired drum", description="An existing village drum.",
        kind="item", location="cottages", portable=True, health=61)
    world.state.requests["old-cache"] = {"world_id": saved.world.world_id, "historical": True}
    world.save()
    raw = path.read_bytes()
    village.advance(world, 60)
    saved.world = world.state.model_copy(deep=True)
    saved.run["village_minute"] = 1440
    saved.run["observation"] = world.observe()
    for actor_id, agent in saved.run["agents"].items():
        agent["observation"] = world.bind(actor_id, agent["session_id"]).observe()
    saved.run["actions"] = saved.run["model_calls"] = 17
    saved.memory["private_test_note"] = "Iona's private notes remain private."
    live = {"run": saved.run, "memory": saved.memory, "observation": saved.run["observation"]}
    path.write_bytes(raw)
    return data, config, saved, live


@pytest.mark.asyncio
async def test_capture_reconciles_only_confirmed_midnight_and_preserves_raw_original(configuration):
    data, _, expected, live = await midnight_fixture(configuration)
    path = data / "worlds" / expected.world.world_id / "world-state.json"
    original = path.read_bytes()
    captured, backup = capture_midnight(data, live)
    assert path.read_bytes() == original == (backup / "original-world-state.json").read_bytes()
    assert captured.world.village.minute == 1440
    assert captured.world.village.coins == expected.world.village.coins
    assert captured.world.village.residents == expected.world.village.residents
    assert captured.run["actions"] == captured.run["model_calls"] == 17
    assert captured.memory == expected.memory
    assert episode_saves.read_save(data, captured.id).world.village.minute == 1440


@pytest.mark.asyncio
async def test_upgrade_preserves_town_and_initializes_only_future_living_progress(configuration):
    _, _, original, _ = await midnight_fixture(configuration)
    before = original.model_dump(mode="json")
    saved = upgrade_midnight(original)
    assert original.model_dump(mode="json") == before
    old, new = original.world.village, saved.world.village
    for field in ("coins", "wagon", "deadlines", "tasks"):
        assert getattr(new, field) == getattr(old, field)
    for field in ("stocks", "stock_lots"):
        assert {key: value for key, value in getattr(new, field).items() if key != "iron-mine"} == getattr(old, field)
    assert new.stocks["iron-mine"] == {"iron-ore": 0}
    assert new.stock_lots["iron-mine"] == {"iron-ore": []}
    assert new.mining.config.enabled
    assert new.residents == old.residents
    assert saved.world.entities["festival-drum"] == original.world.entities["festival-drum"]
    assert saved.world.requests == original.world.requests
    assert saved.memory == original.memory
    assert new.economy.initial_coins == old.coins
    assert not new.economy.orders and not new.economy.payments
    assert not new.incidents.visitors and not new.incidents.instances
    assert new.social.processed_event_count == len(new.events)
    assert all(value.since == 1440 and value.work == value.leisure == 0 for value in new.time_use.values())
    assert new.day_policy.breakfast and new.day_policy.demand_led_completion
    assert saved.run["village_stop_minute"] == 2880
    assert saved.run.get("run_until_complete", False) == original.run.get("run_until_complete", False)
    assert saved.run["config"]["budgets"] == original.run["config"]["budgets"]
    templates = {entry.id: entry for entry in saved.scenario.village.events.templates}
    assert templates["visiting-merchant"].scheduled_at == 2010
    assert templates["passing-traveller"].scheduled_at == 2220
    assert all(quest.offer_at >= 1440 for quest in saved.scenario.village.social.quests.values())
    assert saved.run["actions"] == saved.run["model_calls"] == 17


@pytest.mark.asyncio
async def test_published_copy_reconnects_paused_without_models_and_keeps_original(configuration):
    data, config, original, _ = await midnight_fixture(configuration)
    old_path = data / "runs" / original.run["id"] / "run.json"
    old_bytes = old_path.read_bytes()
    saved = upgrade_midnight(original)
    episode_saves.write_save(data, saved)
    copied = publish_copy(data, saved)
    restored = Controller(ROOT, data, config)
    proxy = SaveProxy(restored.proxy, [])
    restored.proxy = proxy
    await restored.start_worker()
    try:
        assert restored.run["id"] == copied["run_id"] != original.run["id"]
        assert restored.run["world_id"] != original.world.world_id
        assert restored.run["status"] == "paused" and restored.world is not None
        assert restored.run["village_minute"] == 1440
        assert restored.run["actions"] == restored.run["model_calls"] == 17
        assert not proxy.contexts and not proxy.completion_contexts
        state = read_json(restored.world.state_path)["village"]
        assert state["economy"]["initial_coins"]["elsie"] == 1
        assert state["social"] is not None and state["incidents"] is not None
        assert state["minute"] == 1440
        assert restored.run["village_stop_minute"] == 2880
        assert old_path.read_bytes() == old_bytes
        assert restored.memory["private_test_note"] == original.memory["private_test_note"]
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
async def test_capture_rejects_mismatched_observation_or_runnable_npc(configuration):
    data, _, original, live = await midnight_fixture(configuration)
    wrong = copy.deepcopy(live)
    wrong["observation"]["village"]["coins"] += 1
    with pytest.raises(ValueError, match="observed coins"):
        capture_midnight(data, wrong)
    path = data / "worlds" / original.world.world_id / "world-state.json"
    state = read_json(path)
    person = state["village"]["residents"][original.run["actor_id"]]
    person["life"]["sleeping"] = False
    person["available_at"] = 1380
    write_json(path, state)
    with pytest.raises(ValueError, match="runnable NPC"):
        capture_midnight(data, live)
    assert read_json(path)["village"]["minute"] == 1380

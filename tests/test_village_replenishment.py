from pathlib import Path

import pytest

from npc_sim import village, village_goods, village_replenishment as repl, village_social
from npc_sim.models import Scenario
from npc_sim.village_economy_models import VillageSupplierOrder
from npc_sim.storage import read_json
from npc_sim.world import World
from test_village_livestock import make_world, reserves
from test_world import act


@pytest.fixture
def world(tmp_path):
    root = Path(__file__).resolve().parents[1]
    scenario = Scenario.model_validate(read_json(root / "data" / "scenarios" / "millhaven-living.json"))
    scenario.village.start_minute = 480
    scenario.village.breakfast = False
    if scenario.village.legacy:
        scenario.village.legacy = None
    if not scenario.village.social or "herb-garden-care" not in scenario.village.social.activities:
        village_social.install_optional_goods(scenario)
    scenario.village.purposeful_downtime = True
    for agent in scenario.agents:
        agent.start_location = scenario.village.residents[agent.id].work
    result = World(scenario, tmp_path / "world.json", "replenishment", "iona", "session-iona")
    for agent in scenario.agents:
        result.bind(agent.id, f"session-{agent.id}")
    result.state.village.economy.config.reserve_targets.setdefault(result.actor.location_id, {})["herbs"] = 2
    return result


def rows(world):
    return repl.forecast_rows(world.state, world.scenario.village)


def test_plans_are_local_persistent_bounded_and_cancelable(world):
    state = world.state.village
    site = world.actor.location_id
    village_goods.take(world, site, "herbs", state.stocks[site].get("herbs", 0))
    assert act(world, repl.PLAN, task=[])["code"] == "INVALID_ARGUMENTS"
    assert act(world, repl.PLAN, task="gather_herbs", starts_at=state.minute + 2881)["code"] == "INVALID_ARGUMENTS"
    assert act(world, repl.PLAN, task="gather_herbs")["code"] == "REPLENISHMENT_PLANNED"
    saved = world.export_state()
    snapshot = saved.model_dump_json()
    row, = repl.forecast_rows(saved, world.scenario.village)
    assert (row["status"], row["quantity"], row["finish"]) == ("planned", 2, state.minute + 10)
    assert saved.model_dump_json() == snapshot
    assert state.stocks[site]["herbs"] == 0
    assert not state.economy.orders
    restored = World(world.scenario, world.path, world.state.world_id, "iona", "session-iona")
    assert rows(restored) == [row]
    ada = restored.bind("ada", "session-ada")
    assert act(ada, repl.CANCEL)["code"] == "ACTION_UNAVAILABLE"
    village.advance(restored, 5)
    assert act(restored, repl.CANCEL)["code"] == "REPLENISHMENT_CANCELLED"
    assert rows(restored) == []
    village.advance(restored, 5)
    assert act(restored, repl.PLAN, task="gather_herbs")["outcome"] == "APPLIED"
    restored.state.village.minute = restored.state.village.residents["iona"].replenishment.expires_at
    assert rows(restored) == []


def test_future_gather_waits_for_real_expiry_and_actual_work_retires_plan(world):
    state = world.state.village
    site = world.actor.location_id
    village_goods.take(world, site, "herbs", state.stocks[site].get("herbs", 0))
    world.scenario.village.spoil_after["herbs"] = 1440
    village_goods.put(world, site, "herbs", 6)
    expiry = state.stock_lots[site]["herbs"][0].expires_at
    assert expiry is not None
    # The current lot covers demand; a later morning after its expiry does not.
    starts_at = (expiry // 1440 + 1) * 1440 + 480
    assert starts_at <= state.minute + repl.MAX_LEAD
    assert act(world, repl.PLAN, task="gather_herbs", starts_at=starts_at)["outcome"] == "APPLIED"
    row, = rows(world)
    assert (row["status"], row["finish"], row["quantity"]) == ("planned", starts_at + 5, 2)
    assert state.stocks[site]["herbs"] == 6
    village.advance(world, 5)
    assert act(world, "village_gather_herbs")["code"] == "DEMAND_MET"
    assert rows(world) == [row]
    state.minute = starts_at
    state.residents["iona"].life.last_woke = starts_at
    village_goods.advance(world)
    assert act(world, "village_gather_herbs")["code"] == "WORK_COMPLETED"
    assert rows(world) == []
    assert state.stocks[site]["herbs"] == 2


def test_garden_waits_for_recorded_yield_and_requires_physical_plot(world):
    state = world.state.village
    site = world.actor.location_id
    assert act(world, repl.PLAN, task="harvest_garden")["outcome"] == "APPLIED"
    assert rows(world)[0]["status"] == "blocked"
    state.social.garden_yields[site] = state.minute + 30
    row, = rows(world)
    assert (row["status"], row["quantity"], row["finish"]) == ("planned", 1, state.minute + 35)
    world.actor.body.capabilities.remove("gardening")
    assert rows(world)[0]["status"] == "blocked"
    world.actor.body.capabilities.append("gardening")
    world.state.entities["herb-garden-plot"].hidden = True
    assert rows(world)[0]["status"] == "blocked"


def test_sustainable_logs_need_the_actual_axe(world):
    rowan = world.bind("rowan", "session-rowan")
    state = world.state.village
    site = rowan.actor.location_id
    state.economy.config.reserve_targets.setdefault(site, {})["logs"] = 12
    assert act(rowan, repl.PLAN, task="fell_logs")["outcome"] == "APPLIED"
    assert rows(world)[0]["quantity"] == 2
    world.state.entities["wood-axe"].location = "farm"
    assert rows(world)[0]["status"] == "blocked"
    village.advance(world, 5)
    assert act(rowan, "village_fell_logs")["code"] == "WOOD_AXE_REQUIRED"


def test_firewood_conversion_requires_surplus_real_logs(world):
    rowan = world.bind("rowan", "session-rowan")
    state = world.state.village
    site = rowan.actor.location_id
    for product in ("logs", "firewood"):
        quantity = state.stocks[site].get(product, 0)
        if quantity:
            village_goods.take(rowan, site, product, quantity)
    state.economy.config.reserve_targets.setdefault(site, {})["logs"] = 2
    village_goods.put(rowan, site, "logs", 2)
    assert act(rowan, repl.PLAN, task="split_firewood")["outcome"] == "APPLIED"
    blocked, = rows(world)
    assert blocked["status"] == "blocked"
    assert "reserve" in blocked["basis"]
    village_goods.put(rowan, site, "logs", 6)
    row, = rows(world)
    assert (row["status"], row["kind"], row["inputs"], row["quantity"]) == (
        "planned", "production", {"logs": 1}, state.firewood.config.firewood_per_log)
    assert state.stocks[site]["logs"] == 8
    village.advance(world, 5)
    assert act(rowan, "village_split_firewood")["code"] == "BATCH_STARTED"
    assert state.stocks[site]["logs"] == 7
    assert rows(world) == []


@pytest.mark.parametrize(("task", "product", "action", "duration", "amount"), [
    ("milk_cow", "milk", "village_milk_cow", 15, 6),
    ("collect_eggs", "eggs", "village_collect_eggs", 10, 12),
])
def test_collection_needs_care_and_deduplicates_trade_and_started_work(tmp_path, task, product, action, duration, amount):
    world = make_world(tmp_path)
    reserves(world)
    state = world.state.village
    assert act(world, repl.PLAN, task=task)["outcome"] == "APPLIED"
    assert rows(world)[0]["status"] == "blocked"
    care = state.livestock.cow if product == "milk" else state.livestock.hens
    care.day = state.minute // 1440
    care.fed = care.watered = care.cleaned = True
    row, = rows(world)
    assert (row["status"], row["quantity"], row["finish"]) == ("planned", amount, state.minute + 5 + duration)
    state.economy.orders["order"] = VillageSupplierOrder(
        id="order", request_key="test", buyer="elsie", supplier="ada", source="farm", destination="tavern",
        product=product, quantity=1, price=1, deadline=state.minute + 60,
        required_fresh_until=state.minute + 60, created_at=state.minute, status="accepted_planned",
        production_plan={"output": amount, "finish": row["finish"]})
    assert rows(world)[0]["status"] == "blocked"
    state.economy.orders.clear()
    plan = state.residents["ada"].replenishment
    plan.starts_at += 1440
    plan.expires_at += 1440
    assert rows(world)[0]["status"] == "blocked"
    plan.starts_at -= 1440
    plan.expires_at -= 1440
    village.advance(world, 5)
    assert act(world, action)["code"] == "LIVESTOCK_STARTED"
    assert rows(world) == []
    assert state.stocks["farm"][product] == 0
    village.advance(world, duration)
    assert state.stocks["farm"][product] == amount
    assert rows(world) == []

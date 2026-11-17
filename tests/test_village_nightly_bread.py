"""Focused nighttime bread protection for actual tavern meals."""

from pathlib import Path

from npc_sim import village_economy, village_goods, village_life
from npc_sim.models import Scenario, VillageMeal, VillageStockLot
from npc_sim.storage import read_json
from npc_sim.world import World
from test_world import act


ROOT = Path(__file__).resolve().parent.parent


def world_for_meal(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-generations.json"))
    world = World(scenario, tmp_path / "nightly-bread.json", "nightly-bread", "elsie", "session-elsie")
    state = world.state.village
    state.minute = 1080
    world.actor.location_id = "tavern"
    state.residents["elsie"].life.sleeping = False
    state.stocks["tavern"] = {}
    state.stock_lots["tavern"] = {}
    return world


def test_daytime_spends_only_bread_surplus_after_next_breakfast_window(tmp_path):
    world = world_for_meal(tmp_path)
    state = world.state.village
    actor = world.bind("mara", "session-mara")
    actor.actor.location_id = "tavern"
    state.residents["mara"].life.sleeping = False
    for rid, person in state.residents.items():
        if rid != "mara":
            person.life.meals["0:daily"] = VillageMeal(kind="daily", due=1140, status="eaten")
    state.residents["mara"].life.meals["0:breakfast"] = VillageMeal(kind="breakfast", due=540, status="eaten")
    required, _ = village_life.overnight_breakfast_bread_reserve(world)
    assert required == 6
    village_goods.put(world, "tavern", "bread", required)
    before_coins = state.coins.copy()
    assert village_life.apply(actor, "village_eat")[1] == "BREAKFAST_BREAD_RESERVED"
    assert state.stocks["tavern"]["bread"] == required
    assert state.coins == before_coins
    assert "0:daytime" not in state.residents["mara"].life.meals
    village_goods.put(world, "tavern", "bread", 1)
    assert village_life.apply(actor, "village_eat")[1] == "MEAL_EATEN"
    assert state.stocks["tavern"]["bread"] == required


def test_daytime_can_eat_loaf_expiring_before_breakfast(tmp_path):
    world = world_for_meal(tmp_path)
    state = world.state.village
    actor = world.bind("mara", "session-mara")
    actor.actor.location_id = "tavern"
    state.residents["mara"].life.sleeping = False
    for rid, person in state.residents.items():
        if rid != "mara":
            person.life.meals["0:daily"] = VillageMeal(kind="daily", due=1140, status="eaten")
    state.residents["mara"].life.meals["0:breakfast"] = VillageMeal(kind="breakfast", due=540, status="eaten")
    required, _ = village_life.overnight_breakfast_bread_reserve(world)
    village_goods.put(world, "tavern", "bread", required)
    village_goods.put(world, "tavern", "bread", 1, lots=[VillageStockLot(
        quantity=1, produced_at=state.minute, expires_at=1500)])
    assert village_life.overnight_breakfast_bread_reserve(world) == (required, required)
    assert village_life.apply(actor, "village_eat")[1] == "MEAL_EATEN"
    assert village_life.overnight_breakfast_bread_reserve(world) == (required, required)


def test_due_supper_cannot_consume_reserved_bread_in_shortage(tmp_path):
    world = world_for_meal(tmp_path)
    state = world.state.village
    state.minute = 1170
    village_goods.put(world, "tavern", "bread", 1)
    state.residents["elsie"].life.meals["0:daily"] = VillageMeal(kind="daily", due=1170)
    assert village_life.overnight_breakfast_bread_reserve(world)[0] > 0
    assert village_life.apply(world, "village_eat")[1] == "BREAKFAST_BREAD_RESERVED"
    assert state.stocks["tavern"]["bread"] == 1
    assert state.residents["elsie"].life.meals["0:daily"].status == "pending"


def test_supper_cooking_cannot_spend_tomorrows_breakfast_inputs(tmp_path):
    world = world_for_meal(tmp_path)
    state = world.state.village
    state.minute = 1080
    required, _ = village_life.overnight_breakfast_bread_reserve(world)
    village_goods.put(world, "tavern", "bread", required)
    village_goods.put(world, "tavern", "firewood", 3)
    assert village_economy.production_blocker(world, "village_work")[0] == "BREAKFAST_BREAD_RESERVED"
    before = state.coins.copy()
    assert act(world, "village_work")["code"] == "BREAKFAST_BREAD_RESERVED"
    assert state.stocks["tavern"]["bread"] == required
    assert state.coins == before
    village_goods.put(world, "tavern", "bread", 1)
    assert village_economy.production_blocker(world, "village_work") is None

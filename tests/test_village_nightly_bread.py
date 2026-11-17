"""Focused nighttime bread protection for actual tavern meals."""

from pathlib import Path

from npc_sim import village, village_economy, village_goods, village_life
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
    assert village_life.meal_plan(world)["prepared"] == 0
    assert village_life.meal_plan(world)["reserved"] == []
    assert village_life.apply(world, "village_eat")[1] == "BREAKFAST_BREAD_RESERVED"
    assert state.stocks["tavern"]["bread"] == 1
    assert state.residents["elsie"].life.meals["0:daily"].status == "pending"


def test_supper_forecast_counts_only_unprotected_or_expiring_bread(tmp_path):
    world = world_for_meal(tmp_path)
    state = world.state.village
    state.minute = 1080
    required, _ = village_life.overnight_breakfast_bread_reserve(world)
    village_goods.put(world, "tavern", "bread", required)
    assert village_life.meal_plan(world)["prepared"] == 0
    village_goods.put(world, "tavern", "bread", 1, lots=[VillageStockLot(
        quantity=1, produced_at=state.minute, expires_at=1500)])
    assert village_life.meal_plan(world)["prepared"] == 1
    village_goods.put(world, "tavern", "meals", 2, lots=[VillageStockLot(
        quantity=2, produced_at=state.minute, expires_at=1130)])
    assert village_life.meal_plan(world)["prepared"] == 1


def test_supper_cooking_cannot_spend_tomorrows_breakfast_inputs(tmp_path):
    world = world_for_meal(tmp_path)
    state = world.state.village
    state.minute = 1080
    required, _ = village_life.overnight_breakfast_bread_reserve(world)
    village_goods.put(world, "tavern", "bread", required)
    village_goods.put(world, "tavern", "firewood", 3)
    readiness = next(item for item in village_economy.meal_readiness(world)
                     if item["service"] == "0:supper")
    assert readiness["cookable_portions"] == 0
    assert "bread" in readiness["missing_inputs"]
    assert village_economy.production_blocker(world, "village_work")[0] == "BREAKFAST_BREAD_RESERVED"
    before = state.coins.copy()
    assert act(world, "village_work")["code"] == "BREAKFAST_BREAD_RESERVED"
    assert state.stocks["tavern"]["bread"] == required
    assert state.coins == before
    village_goods.put(world, "tavern", "bread", 1)
    assert village_economy.production_blocker(world, "village_work") is None


def test_breakfast_serves_prepared_portions_before_fresh_bread(tmp_path):
    world = world_for_meal(tmp_path)
    state = world.state.village
    state.minute = 450
    village_goods.put(world, "tavern", "bread", 6)
    village_goods.put(world, "tavern", "meals", 12, lots=[VillageStockLot(
        quantity=12, produced_at=330, expires_at=690)])
    diners = list(state.residents)
    for rid in diners:
        state.residents[rid].life.sleeping = False
        state.residents[rid].life.meals["0:breakfast"] = VillageMeal(
            kind="breakfast", due=world.scenario.village.residents[rid].wake_minute + 30)
    opening_coins = dict(state.coins)
    for rid in diners:
        actor = world.bind(rid, f"session-{rid}")
        actor.actor.location_id = "tavern"
        result = village_life.apply(actor, "village_eat")
        assert result[1] == "MEAL_EATEN", (rid, result)
    assert all(state.residents[rid].life.meals["0:breakfast"].source == "tavern:meals"
               for rid in diners)
    assert state.stocks["tavern"]["meals"] == 0
    assert state.stocks["tavern"]["bread"] == 6
    assert all(state.coins[rid] < opening_coins[rid] for rid in diners if rid != "elsie")
    village.advance(world, 690 - state.minute)
    assert not any(event.action == "stock_spoiled" and "meals" in event.result
                   for event in state.events)


def test_real_supper_batches_feed_all_without_spending_next_breakfast(tmp_path):
    world = world_for_meal(tmp_path)
    state = world.state.village
    village_goods.put(world, "tavern", "bread", 10)
    village_goods.put(world, "tavern", "fish", 2)
    village_goods.put(world, "tavern", "firewood", 3)
    for rid, person in state.residents.items():
        person.life.sleeping = False
        person.life.last_woke = world.scenario.village.residents[rid].wake_minute
        person.life.meals["0:breakfast"] = VillageMeal(
            kind="breakfast", due=world.scenario.village.residents[rid].wake_minute + 30,
            status="eaten", source="tavern:meals")
    for minute, action in ((1080, "village_work"), (1085, "village_work"),
                           (1090, "village_cook_fish")):
        village.advance(world, minute - state.minute)
        result = act(world, action)
        assert result["outcome"] == "APPLIED", result
    assert state.stocks["tavern"]["meals"] == 12
    assert state.stocks["tavern"]["bread"] == 6
    village.advance(world, 1140 - state.minute)
    opening_coins = dict(state.coins)
    for rid in state.residents:
        if rid == "elsie":
            continue
        actor = world.bind(rid, f"session-{rid}")
        actor.actor.location_id = "tavern"
        result = village_life.apply(actor, "village_eat")
        assert result[1] == "MEAL_EATEN", (rid, result)
    village.advance(world, 1170 - state.minute)
    assert village_life.apply(world, "village_eat")[1] == "MEAL_EATEN"
    assert all(person.life.meals["0:daily"].status == "eaten"
               for person in state.residents.values())
    assert all(state.coins[rid] < opening_coins[rid]
               for rid in state.residents if rid != "elsie")
    assert state.stocks["tavern"]["bread"] == 6
    assert state.stocks["tavern"]["meals"] == 0

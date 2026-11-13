import pytest

from npc_sim import village, village_fallback, village_goods, village_life
from npc_sim.world import World
from test_village_expanded import scenario
from test_world import act


@pytest.fixture
def cast(tmp_path):
    value = scenario()
    value.village.start_minute = 480
    value.village.expanded_rules = True
    value.village.goal_deadlines = {}
    value.village.stocks["tavern"] = {"meals": 9, "bread": 0}
    for agent in value.agents:
        agent.start_location = "tavern"
    world = World(value, tmp_path / "meal-planning.json", "meals", "elsie", "session-elsie")
    return {agent.id: world.bind(agent.id, f"session-{agent.id}") for agent in value.agents}


def test_early_plan_tracks_twelve_real_portions_and_inputs(cast):
    world = cast["elsie"]
    plan = village_life.meal_plan(world)
    assert plan["expected"] == 12
    assert plan["prepared"] == 9
    assert plan["consumed"] == 0
    assert plan["shortfall"] == 3
    assert len(plan["reserved"]) == 9
    assert plan["inputs_needed"] == {"bread": 2}
    assert plan["cutoff"] == 1110
    assert village_life.personal(world)["life"]["meal_plan"] == plan
    assert world.state.village.stocks["tavern"]["meals"] == 9


def test_allocation_is_fixed_and_independent_of_action_order(cast):
    world = cast["elsie"]
    village.advance(world, 1140 - world.state.village.minute)
    plan = village_life.meal_plan(world)
    outside = next(rid for rid in plan["diners"] if rid not in plan["reserved"])
    rejected = act(cast[outside], "village_eat")
    assert rejected["code"] == "MEAL_MISSED"
    assert world.state.village.stocks["tavern"]["meals"] == 9
    for rid in reversed([rid for rid in plan["reserved"] if rid != "elsie"]):
        result = act(cast[rid], "village_eat")
        assert result["code"] == "MEAL_EATEN", (rid, result)
    village.advance(world, 1170 - world.state.village.minute)
    assert act(cast["elsie"], "village_eat")["code"] == "MEAL_EATEN"
    assert world.state.village.stocks["tavern"]["meals"] == 0
    assert village_life.meal_plan(world)["consumed"] == 9
    assert village_life.meal_plan(world)["shortfall"] == 3


def test_reserved_late_diner_keeps_portion_through_grace(cast):
    world = cast["elsie"]
    village.advance(world, 1140 - world.state.village.minute)
    reserved = village_life.meal_plan(world)["reserved"]
    late = next(rid for rid in reserved if rid != "elsie")
    village.advance(world, 35)
    assert late in village_life.meal_plan(world)["reserved"]
    for rid in reserved:
        if rid not in {late, "elsie"}:
            assert act(cast[rid], "village_eat")["code"] == "MEAL_EATEN"
    assert world.state.village.stocks["tavern"]["meals"] == 2
    assert act(cast[late], "village_eat")["code"] == "MEAL_EATEN"
    village.advance(world, 1170 - world.state.village.minute)
    assert act(cast["elsie"], "village_eat")["code"] == "MEAL_EATEN"
    assert world.state.village.stocks["tavern"]["meals"] == 0


def test_elsie_serves_supper_before_eating_at_seven_thirty(cast):
    world = cast["elsie"]
    village.advance(world, 1140 - world.state.village.minute)
    stock = world.state.village.stocks["tavern"]
    village_goods.take(world, "tavern", "meals", 7)
    plan = village_life.meal_plan(world)
    assert "elsie" in plan["reserved"]
    assert village_life.available_meal(world, "elsie") is None
    assert act(world, "village_eat")["code"] == "NO_MEAL_DUE"
    assert not any(choice["action_id"] == "village_eat" for choice in village_fallback.candidates(world))
    other = next(rid for rid in plan["reserved"] if rid != "elsie")
    assert act(cast[other], "village_eat")["code"] == "MEAL_EATEN"
    assert stock["meals"] == 1
    village.advance(world, 1170 - world.state.village.minute)
    assert world.state.village.residents["elsie"].life.meals["0:daily"].due == 1170
    assert village_life.available_meal(world, "elsie") is not None
    assert act(world, "village_eat")["code"] == "MEAL_EATEN"
    assert stock["meals"] == 0


def test_elsie_has_no_phantom_reserved_food_when_supply_is_short(cast):
    world = cast["elsie"]
    village.advance(world, 1140 - world.state.village.minute)
    stock = world.state.village.stocks["tavern"]
    village_goods.take(world, "tavern", "meals", 8)
    plan = village_life.meal_plan(world)
    assert plan["reserved"] == ["elsie"]
    assert act(cast["mara"], "village_eat")["code"] == "MEAL_MISSED"
    assert stock["meals"] == 1
    village_goods.take(world, "tavern", "meals", 1)
    village.advance(world, 1170 - world.state.village.minute)
    assert act(world, "village_eat")["code"] == "MEAL_MISSED"
    assert stock["meals"] == 0


def test_late_diner_can_eat_unclaimed_real_food_after_grace(cast):
    world = cast["elsie"]
    village.advance(world, 1245 - world.state.village.minute)
    stock = world.state.village.stocks["tavern"]
    village_goods.put(world, "tavern", "bread", 1)
    result = act(cast["elsie"], "village_eat")
    assert result["code"] == "MEAL_EATEN"
    assert stock["bread"] == 0


def test_inn_cannot_serve_meals_until_elsie_wakes_and_is_present(cast):
    world = cast["elsie"]
    village.advance(world, 1140 - world.state.village.minute)
    stock = world.state.village.stocks["tavern"]["meals"]
    world.state.village.residents["elsie"].life.sleeping = True
    assert act(cast["mara"], "village_eat")["code"] == "INN_NOT_OPEN"
    assert world.state.village.stocks["tavern"]["meals"] == stock
    world.actor.location_id = "cottages"
    world.state.village.residents["elsie"].life.sleeping = False
    assert act(cast["mara"], "village_eat")["code"] == "INN_NOT_OPEN"
    world.actor.location_id = "tavern"
    assert act(cast["mara"], "village_eat")["code"] == "MEAL_EATEN"


def test_inn_serves_real_late_supper_before_ten_but_not_at_closing(cast):
    world = cast["elsie"]
    assert world.scenario.village.inn_closing_minute == 1320
    village.advance(world, 1315 - world.state.village.minute)
    village_goods.put(world, "tavern", "bread", 2)
    assert act(world, "village_eat")["code"] == "MEAL_EATEN"
    village.advance(world, 5)
    result = act(cast["mara"], "village_eat")
    assert result["code"] == "INN_CLOSED"
    assert world.state.village.stocks["tavern"]["bread"] == 1


def test_inn_production_hours_do_not_extend_the_mills(cast):
    world = cast["elsie"]
    village.advance(world, 1315 - world.state.village.minute)
    world.state.village.residents["elsie"].life.meals["0:daily"].status = "eaten"
    world.state.village.residents["mara"].life.meals["0:daily"].status = "eaten"
    assert (blocker := village_life.action_blocker(world, "village_work")) is None or blocker[0] != "OUTSIDE_WORK_HOURS"
    assert village_life.action_blocker(cast["mara"], "village_work")[0] == "OUTSIDE_WORK_HOURS"
    village.advance(world, 5)
    assert village_life.action_blocker(world, "village_work")[0] == "OUTSIDE_WORK_HOURS"


def test_meal_cooking_outranks_optional_research_while_feasible(cast):
    world = cast["elsie"]
    world.state.village.stocks["tavern"]["bread"] = 2
    blocked = village_life.action_blocker(world, "village_research", "elsie")
    assert blocked and blocked[0] == "MEAL_PREPARATION_PRIORITY"
    world.state.village.stocks["tavern"]["bread"] = 0
    assert village_life.action_blocker(world, "village_research", "elsie") != blocked


def test_new_stock_recalculates_shortfall_without_phantom_portions(cast):
    world = cast["elsie"]
    world.state.village.stocks["tavern"]["bread"] = 3
    plan = village_life.meal_plan(world)
    assert plan["prepared"] == 12
    assert plan["shortfall"] == 0
    assert len(plan["reserved"]) == 12
    world.state.village.stocks["tavern"]["bread"] = 0
    assert village_life.meal_plan(world)["shortfall"] == 3

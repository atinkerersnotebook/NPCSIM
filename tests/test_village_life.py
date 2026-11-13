import pytest

from npc_sim import village, village_life, village_turns
from npc_sim.world import World
from npc_sim.storage import read_json
from npc_sim.models import Scenario
from test_village_expanded import ROOT, scenario
from test_world import act


@pytest.fixture
def cast(tmp_path):
    value = scenario()
    value.village.start_minute = 480
    value.village.expanded_rules = False
    value.village.goal_deadlines = {}
    value.village.stocks["watermill"]["flour"] = 0
    for agent in value.agents:
        agent.body.capacity = 4
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "life.json", "life", "torvin", "session-torvin")
    return {agent.id: world.bind(agent.id, f"session-{agent.id}") for agent in value.agents}


def life(cast, rid):
    return cast[rid].state.village.residents[rid].life


def advance_to(cast, minute):
    world = cast["torvin"]
    assert minute >= world.state.village.minute
    village.advance(world, minute - world.state.village.minute)
    world.save()


def do(cast, rid, action, **args):
    world = cast[rid]
    ready = world.state.village.residents[rid].available_at
    if ready > world.state.village.minute:
        advance_to(cast, ready)
    result = act(world, action, **args)
    assert result["outcome"] == "APPLIED", result
    return result


def walk(cast, rid, *doors):
    for door in doors:
        do(cast, rid, "go", target=door)


def test_individual_schedules_no_home_pantry_and_p1_opening_stock(cast):
    world = cast["torvin"]
    for rid, actor in cast.items():
        resident = actor.scenario.village.residents[rid]
        assert not life(cast, rid).sleeping and life(cast, rid).meals == {}
        assert life(cast, rid).last_woke == resident.wake_minute
        assert "home_food" not in life(cast, rid).model_dump()
    assert world.state.village.stocks["watermill"]["grain"] == 6
    assert world.state.village.stocks["bakery"] == {"flour": 6, "bread": 4}
    assert world.state.village.stocks["tavern"]["bread"] == 12
    assert act(cast["iona"], "village_eat")["code"] == "NO_MEAL_DUE"
    do(cast, "iona", "village_work")


@pytest.mark.parametrize("stock,source", [
    ({"meals": 1}, "tavern:meals"), ({"bread": 1}, "tavern:bread"),
    ({"fish": 1, "tea": 1}, "tavern:fish+tea")
])
def test_one_real_daily_meal_at_tavern(cast, stock, source):
    walk(cast, "iona", "square-cottages", "square-tavern")
    advance_to(cast, 1140)
    state = cast["iona"].state.village
    state.stocks["tavern"] = stock.copy()
    do(cast, "iona", "village_eat")
    assert life(cast, "iona").meals["0:daily"].source == source
    assert all(value == 0 for value in state.stocks["tavern"].values())
    advance_to(cast, 1145)
    assert act(cast["iona"], "village_eat")["code"] == "NO_MEAL_DUE"
    assert not life(cast, "iona").hungry


def test_early_tavern_meal_and_early_bed_keep_the_next_morning_wake(cast):
    walk(cast, "iona", "square-cottages", "square-tavern")
    advance_to(cast, 1080)
    state = cast["iona"].state.village
    stock_before = state.stocks["tavern"]["bread"]
    assert do(cast, "iona", "village_eat")["code"] == "MEAL_EATEN"
    assert life(cast, "iona").meals["0:daily"].due == 1140
    assert state.stocks["tavern"]["bread"] == stock_before - 1
    advance_to(cast, state.residents["iona"].available_at)
    assert act(cast["iona"], "village_eat")["code"] == "NO_MEAL_DUE"
    walk(cast, "iona", "square-tavern", "square-cottages")
    assert do(cast, "iona", "village_sleep")["code"] == "SLEEP_STARTED"
    assert life(cast, "iona").sleep_until == 1440 + cast["iona"].scenario.village.residents["iona"].wake_minute
    advance_to(cast, 1140)
    assert life(cast, "iona").meals["0:daily"].status == "eaten"
    assert life(cast, "iona").sleeping


def test_early_sleep_requires_completed_supper_and_does_not_skip_it(cast):
    cast["iona"].actor.location_id = "cottages"
    advance_to(cast, 1080)
    assert act(cast["iona"], "village_sleep")["code"] == "REST_NOT_DUE"
    assert not life(cast, "iona").sleeping


def test_no_remote_meals_and_tea_alone_is_not_food(cast):
    advance_to(cast, 1140)
    assert act(cast["iona"], "village_eat")["code"] == "TRY_TAVERN"
    walk(cast, "iona", "square-cottages", "square-tavern")
    cast["iona"].state.village.stocks["tavern"] = {"tea": 3}
    do(cast, "iona", "village_eat")
    assert life(cast, "iona").meals["0:daily"].status == "missed"
    assert cast["iona"].state.village.stocks["tavern"]["tea"] == 3
    advance_to(cast, cast["iona"].state.village.minute + 5)
    assert act(cast["iona"], "village_eat")["code"] == "NO_FOOD"
    assert life(cast, "iona").missed_meals == 1


def test_missed_daily_meal_doubles_only_the_next_days_work_cooldown(cast):
    advance_to(cast, 1440)
    assert life(cast, "iona").missed_meals == 1
    assert village_life.cooldown_factor(cast["iona"], "iona") == 2
    do(cast, "iona", "village_sleep")
    advance_to(cast, life(cast, "iona").sleep_until)
    do(cast, "iona", "village_work")
    state = cast["iona"].state.village
    assert state.residents["iona"].next_work - state.minute == 120
    advance_to(cast, 2880)
    assert village_life.cooldown_factor(cast["iona"], "iona") == 2
    assert life(cast, "iona").missed_meals == 2


def test_late_food_before_midnight_cancels_tomorrows_penalty(cast):
    walk(cast, "iona", "square-cottages", "square-tavern")
    advance_to(cast, 1140)
    state = cast["iona"].state.village
    state.stocks["tavern"] = {}
    do(cast, "iona", "village_eat")
    state.stocks["tavern"]["bread"] = 1
    do(cast, "iona", "village_eat")
    assert life(cast, "iona").missed_meals == 0
    advance_to(cast, 1440)
    assert village_life.cooldown_factor(cast["iona"], "iona") == 1


def test_sleep_requires_home_full_rest_and_cannot_be_skipped(cast):
    advance_to(cast, 1320)
    world = cast["torvin"]
    assert act(world, "village_work")["code"] == "REST_DUE"
    assert act(world, "village_sleep")["code"] == "NOT_AT_HOME"
    walk(cast, "torvin", "square-forge", "square-cottages")
    do(cast, "torvin", "village_sleep")
    until = life(cast, "torvin").sleep_until
    advance_to(cast, until - 5)
    assert life(cast, "torvin").sleeping
    assert act(world, "village_work")["code"] in {"ASLEEP", "ACTOR_BUSY"}
    advance_to(cast, until)
    assert not life(cast, "torvin").sleeping
    assert act(world, "village_sleep")["code"] == "REST_NOT_DUE"
    assert act(cast["bren"], "village_work")["code"] == "REST_DUE"


@pytest.mark.parametrize("scenario_id,wake", [("millhaven-expanded", 360), ("millhaven-living", 330)])
def test_elsie_wakes_early_to_prepare_her_inn(tmp_path, scenario_id, wake):
    value = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / f"{scenario_id}.json"))
    resident = value.village.residents["elsie"]
    assert resident.home == resident.work == "tavern"
    assert resident.wake_minute == wake
    assert resident.schedule[1].minute == wake
    assert "prepar" in resident.schedule[1].activity.lower()
    assert "food" in resident.schedule[1].activity.lower()
    world = World(value, tmp_path / "elsie.json", "elsie", "elsie", "session-elsie")
    assert world.state.village.residents["elsie"].life.sleep_until == wake
    village.advance(world, wake - world.state.village.minute)
    assert not world.state.village.residents["elsie"].life.sleeping
    assert world.state.village.residents["elsie"].life.last_woke == wake


@pytest.mark.parametrize("rid,wake", [
    ("elsie", 330), ("torvin", 420), ("mara", 420), ("hobb", 420),
    ("nella", 420), ("garrick", 420), ("iona", 420),
    ("bren", 360), ("jory", 360), ("ada", 360), ("rowan", 420), ("finn", 420),
])
def test_living_village_morning_wake_schedule(tmp_path, rid, wake):
    from npc_sim.living_village import build_scenario

    base = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-expanded.json"))
    generated = build_scenario(base, firewood=True)
    configured = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-living.json"))
    for value in (generated, configured):
        resident = value.village.residents[rid]
        assert resident.wake_minute == wake
        assert resident.schedule[1].minute == wake
    world = World(configured, tmp_path / f"wake-{rid}.json", "living-wake", rid, f"session-{rid}")
    person = world.state.village.residents[rid].life
    assert person.sleeping
    assert person.sleep_until == wake
    village.advance(world, wake - world.state.village.minute)
    assert not person.sleeping
    assert person.last_woke == wake
    village.advance(world, configured.village.breakfast_delay)
    assert person.meals["0:breakfast"].due == wake + configured.village.breakfast_delay


def test_established_living_stock_is_seeded_once_and_persists(tmp_path):
    from npc_sim.living_village import build_scenario

    base = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-expanded.json"))
    generated = build_scenario(base, firewood=True)
    configured = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-living.json"))
    expected = {"tavern": {"firewood": 12, "bread": 12},
                "bakery": {"firewood": 10, "flour": 8}, "forge": {"firewood": 10},
                "cottages": {"firewood": 24}, "workshop": {"timber": 6},
                "watermill": {"grain": 8}, "farm": {"hay": 8}, "stable": {"hay": 6}}
    for value in (generated, configured):
        path = tmp_path / f"seed-{len(list(tmp_path.iterdir()))}.json"
        world = World(value, path, "seed", "elsie", "session-elsie")
        state = world.state.village
        assert state.initial_stock_seed_id == value.village.initial_stock_seed_id
        assert state.initial_stock_seed_id
        for location, goods in expected.items():
            for product, quantity in goods.items():
                assert state.stocks[location][product] == quantity
                lot = state.stock_lots[location][product][0]
                assert lot.origin == "initial_village_stock"
                assert lot.seed_event_id == state.initial_stock_seed_id
                assert lot.produced_at == value.village.start_minute
        imports = {(event.location, event.product): event.quantity for event in state.economy.movements
                   if event.kind == "imported"}
        assert all(imports[location, product] == quantity for location, goods in expected.items()
                   for product, quantity in goods.items())
        assert len(imports) == sum(bool(quantity) for goods in state.stocks.values()
                                   for quantity in goods.values())
        opening_events = [event.model_dump() for event in state.economy.movements]
        state.stocks["tavern"]["firewood"] -= 1
        state.stock_lots["tavern"]["firewood"][0].quantity -= 1
        world.save()
        reloaded = World(value, path, "seed", "elsie", "session-elsie")
        assert reloaded.state.village.stocks["tavern"]["firewood"] == 11
        assert len(reloaded.state.village.economy.movements) == len(state.economy.movements)
        village.advance(reloaded, 1440)
        assert reloaded.state.village.initial_stock_seed_id == state.initial_stock_seed_id
        assert [event.model_dump() for event in reloaded.state.village.economy.movements[:len(opening_events)]] == opening_events


def test_living_opening_bread_covers_configured_breakfast_and_supper(tmp_path):
    from npc_sim import village_economy
    from npc_sim.living_village import build_scenario

    base = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-expanded.json"))
    generated = build_scenario(base, firewood=True)
    configured = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-living.json"))
    for value in (generated, configured):
        world = World(value, tmp_path / f"bread-{id(value)}.json", "living-bread", "elsie", "session-elsie")
        requirements = village_economy.meal_requirements(world)
        assert [plan["portions"] for plan in requirements] == [12, 12]
        required_bread = sum(plan["inputs"].get("bread", 0) for plan in requirements)
        assert required_bread == 12
        assert world.state.village.stocks["tavern"]["bread"] >= required_bread
        readiness = village_economy.meal_readiness(world)
        assert [item["planned_portions"] for item in readiness] == [12, 12]
        assert readiness[0]["cookable_portions"] == 12
        assert readiness[1]["reason_code"] == "ingredients_missing"
        assert readiness[1]["projected_unserved"] == 4
        assert readiness[1]["missing_inputs"] == ["fish"]
        assert [item["fuel_needed"] for item in readiness] == [6, 6]
        world.actor.location_id = "tavern"
        assert village_economy.observation(world)["meal_readiness"] == readiness
        world.actor.location_id = "bakery"
        assert "meal_readiness" not in village_economy.observation(world)
        world.actor.location_id = "tavern"
        from npc_sim import village_goods
        village_goods.take(world, "tavern", "firewood", 12, kind="consumed")
        missing_fuel = village_economy.meal_readiness(world)
        assert missing_fuel[0]["reason_code"] == "fuel_missing"
        assert missing_fuel[0]["projected_unserved"] == 12
        assert missing_fuel[0]["fuel_on_hand"] == 0
        village_goods.put(world, "tavern", "meals", 3, kind="imported")
        partially_ready = village_economy.meal_readiness(world)
        assert partially_ready[0]["prepared_portions"] == 3
        assert partially_ready[0]["projected_unserved"] == 9
        assert partially_ready[1]["prepared_portions"] == 0


@pytest.mark.parametrize("rid,home", [("elsie", "tavern"), ("ada", "farm")])
def test_residents_sleep_at_their_workplace_homes(cast, rid, home):
    world = cast[rid]
    assert world.scenario.village.residents[rid].home == home
    advance_to(cast, world.scenario.village.residents[rid].bed_minute)
    if rid == "ada":
        for destination in village.route(world, world.actor.location_id, "tavern"):
            door = next(eid for eid, entity in world.state.entities.items()
                        if entity.kind == "door" and {entity.location, entity.destination}
                        == {world.actor.location_id, destination})
            do(cast, rid, "go", target=door)
        advance_to(cast, world.state.village.residents[rid].available_at)
        assert act(world, "village_sleep")["code"] == "NOT_AT_HOME"
        for destination in village.route(world, world.actor.location_id, home):
            door = next(eid for eid, entity in world.state.entities.items()
                        if entity.kind == "door" and {entity.location, entity.destination}
                        == {world.actor.location_id, destination})
            do(cast, rid, "go", target=door)
    do(cast, rid, "village_sleep")
    assert world.actor.location_id == home
    assert life(cast, rid).sleeping


def test_two_unit_baking_is_committed_idempotent_and_survives_reload(cast):
    world = cast["jory"]
    revision = world.revision()
    first = world.act("batch", revision, "village_work", {})
    assert first["code"] == "BATCH_STARTED"
    assert world.act("batch", revision, "village_work", {}) == first
    assert world.state.village.stocks["bakery"] == {"flour": 4, "bread": 4}
    assert life(cast, "jory").work.finishes_at == 510
    advance_to(cast, 505)
    assert act(world, "go", target="square-bakery")["code"] in {"ACTOR_BUSY", "WORK_MUST_FINISH"}
    restored = World(world.scenario, world.path, "life", "jory", "session-jory")
    assert restored.state.village.residents["jory"].life.work.outputs == {"bread": 2}
    village.advance(restored, 5)
    assert restored.state.village.stocks["bakery"]["bread"] == 6
    assert restored.state.village.residents["jory"].life.work is None
    village.advance(restored, 5)
    assert restored.state.village.stocks["bakery"]["bread"] == 6


def test_baking_crosses_mealtime_but_no_new_work_until_fed(cast):
    advance_to(cast, 1135)
    do(cast, "jory", "village_work")
    advance_to(cast, 1140)
    assert life(cast, "jory").work.finishes_at == 1165
    assert life(cast, "jory").meals["0:daily"].status == "pending"
    advance_to(cast, 1165)
    assert act(cast["jory"], "village_work")["code"] == "MEAL_DUE"
    walk(cast, "jory", "bakery-tavern")
    do(cast, "jory", "village_eat")


def test_failed_oven_does_not_create_output(cast):
    do(cast, "jory", "village_work")
    cast["jory"].state.entities["oven"].health = 0
    advance_to(cast, 510)
    assert cast["jory"].state.village.stocks["bakery"]["bread"] == 4
    assert life(cast, "jory").work is None
    assert any(event.action == "batch_failed" for event in cast["jory"].state.village.events)


@pytest.mark.parametrize("rid,input_,output,count", [
    ("mara", "grain", "flour", 2), ("elsie", "bread", "meals", 4)
])
def test_production_handles_two_inputs(cast, rid, input_, output, count):
    world = cast[rid]
    stock = world.state.village.stocks[world.actor.location_id]
    before = stock[input_]
    do(cast, rid, "village_work")
    assert stock[input_] == before - 2
    assert stock[output] == count


def test_cooking_fish_and_partial_batch(cast):
    world = cast["elsie"]
    world.state.village.stocks["tavern"]["fish"] = 1
    do(cast, "elsie", "village_cook_fish")
    assert world.state.village.stocks["tavern"]["fish"] == 0
    assert world.state.village.stocks["tavern"]["meals"] == 2


def test_twelve_workers_act_at_same_time_instead_of_spending_an_hour(cast):
    world = cast["torvin"]
    chosen = []
    for _ in range(12):
        rid = village_turns.next_actor(world)
        chosen.append(rid)
        do(cast, rid, "village_wait", minutes=5)
    assert len(set(chosen)) == 12
    assert world.state.village.minute == 480
    village_turns.next_actor(world)
    assert world.state.village.minute == 485


def test_idle_sleeping_and_committed_villagers_skip_paid_turns(cast):
    world = cast["torvin"]
    do(cast, "jory", "village_work")
    advance_to(cast, 1320)
    for rid in cast:
        actor = cast[rid]
        actor.actor.location_id = actor.scenario.village.residents[rid].home
        if rid == "elsie":
            continue
        do(cast, rid, "village_sleep")
    assert village_turns.next_actor(world) == "elsie"
    do(cast, "elsie", "village_wait", minutes=60)
    assert village_turns.next_actor(world) == "elsie"
    assert world.state.village.minute == 1380
    do(cast, "elsie", "village_sleep")
    selected = village_turns.next_actor(world)
    assert world.state.village.minute == 1800
    assert selected in {"bren", "jory", "ada"}
    assert not life(cast, selected).sleeping


def test_sleepers_do_not_hear_bells_or_spoken_messages(cast):
    advance_to(cast, 1320)
    do(cast, "iona", "village_sleep")
    walk(cast, "torvin", "square-forge", "square-cottages")
    before = list(cast["iona"].state.village.residents["iona"].memories)
    village.announce(cast["torvin"], "torvin", "meeting", "dig_well", "Meet at the well.")
    do(cast, "torvin", "say", message="Iona, are you awake?")
    assert cast["iona"].state.village.residents["iona"].memories == before
    assert cast["iona"].actor.messages == []

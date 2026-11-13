import pytest

from npc_sim import village, village_goods, village_life, village_services, village_turns
from npc_sim.controller import Controller
from npc_sim.storage import write_json
from npc_sim.world import World
from test_controller import ROOT, TestProxy, configuration
from test_village_expanded import scenario
from test_village_life import advance_to, do, walk
from test_world import act


@pytest.fixture
def cast(tmp_path):
    value = scenario()
    value.village.start_minute = 480
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "complete.json", "complete", "torvin", "session-torvin")
    return {agent.id: world.bind(agent.id, f"session-{agent.id}") for agent in value.agents}


def hitch(cast):
    do(cast, "bren", "village_work")
    do(cast, "bren", "village_lead_horse")
    walk(cast, "bren", "square-stable", "square-forge")
    do(cast, "bren", "village_hitch")


def wagon_ready(cast):
    do(cast, "torvin", "village_work")
    do(cast, "torvin", "village_fit_wagon")
    hitch(cast)
    advance_to(cast, max(cast["torvin"].state.village.minute, 515))
    walk(cast, "nella", "square-store", "square-forge")
    do(cast, "nella", "village_drive_wagon")


def go_to(cast, rid, destination):
    world = cast[rid]
    path = village.route(world, world.actor.location_id, destination,
                         road_only=world.state.village.wagon.driver == rid)
    assert path is not None
    for next_place in path:
        door = next(eid for eid, entity in world.state.entities.items() if entity.kind == "door"
                    and {entity.location, entity.destination} == {world.actor.location_id, next_place})
        do(cast, rid, "go", target=door)


def test_new_day_starts_asleep_at_home_and_waking_villagers_walk_to_work(tmp_path):
    value = scenario()
    world = World(value, tmp_path / "morning.json", "morning", "torvin", "session-torvin")
    actors = {agent.id: world.bind(agent.id, f"session-{agent.id}") for agent in value.agents}
    state = world.state.village
    assert state.minute == min(resident.wake_minute for resident in value.village.residents.values()) - 60 == 300
    for rid, actor in actors.items():
        assert actor.actor.location_id == actor.actor.start_location_id == value.village.residents[rid].home
        assert state.residents[rid].life.sleeping
        assert state.residents[rid].life.sleep_until == value.village.residents[rid].wake_minute
    assert {rid: value.village.residents[rid].home for rid in ("elsie", "ada")} == {"elsie": "tavern", "ada": "farm"}
    stocks = {place: amounts.copy() for place, amounts in state.stocks.items()}
    assert act(actors["bren"], "village_work")["code"] == "ASLEEP"
    assert state.stocks == stocks and state.minute == 300
    assert village_turns.next_actor(world) == "bren"
    assert state.minute == 360
    assert {rid for rid, person in state.residents.items() if not person.life.sleeping} == {"bren", "jory", "ada", "elsie"}
    assert all(actor.actor.location_id == value.village.residents[rid].home for rid, actor in actors.items())
    assert act(actors["bren"], "village_work")["code"] == "WRONG_WORKPLACE"
    walk(actors, "bren", "square-cottages", "square-stable")
    do(actors, "bren", "village_work")
    assert actors["bren"].actor.location_id == "stable"
    assert state.minute == 370
    assert state.stocks["stable"]["hay"] == stocks["stable"]["hay"] - value.village.residents["bren"].job.inputs["hay"]
    assert all(actor.actor.location_id == value.village.residents[rid].home
               for rid, actor in actors.items() if rid != "bren")


@pytest.mark.asyncio
async def test_controller_waits_for_first_wake_before_requesting_a_decision(configuration):
    data, config = configuration
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, [("go", {"target": "square-cottages"})])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        before = controller.status()
        assert before["world"]["village"]["minute"] == 300
        assert len(before["world"]["actors"]) == 12
        assert all(actor["location_id"] == scenario().village.residents[rid].home
                   for rid, actor in before["world"]["actors"].items())
        assert controller.run["model_calls"] == 0 and proxy.contexts == []
        await controller.command("step")
        assert len(proxy.contexts) == 1
        observed = proxy.contexts[0]["observation"]
        assert observed["self"]["actor_id"] == "bren"
        assert observed["village"]["minute"] == 360 and observed["location"]["id"] == "cottages"
        assert not observed["village"]["personal"]["life"]["sleeping"]
        after = controller.status()["world"]
        assert after["actors"]["bren"]["location_id"] == "village-square"
        assert all(actor["location_id"] == scenario().village.residents[rid].home
                   for rid, actor in after["actors"].items() if rid != "bren")
    finally:
        await controller.shutdown()


def test_remaining_opening_stocks_and_fifo_ledgers(cast):
    state = cast["torvin"].state.village
    for place, resource, quantity in [("forge", "iron", 4), ("stable", "hay", 6),
                                    ("watermill", "flour", 2), ("woods", "logs", 10),
                                    ("workshop", "timber", 4), ("cottages", "herbs", 10)]:
        assert state.stocks[place][resource] == quantity
        assert sum(lot.quantity for lot in state.stock_lots[place][resource]) == quantity
    assert all(actor.actor.body.capacity == 3 for actor in cast.values())
    assert state.stock_history[0].minute == 480


@pytest.mark.parametrize("minute,expected", [(0, "00:00"), (485, "08:05"),
                                           (1439, "23:59"), (1440, "00:00 on day 2"),
                                           (1800, "06:00 on day 2")])
def test_clock_time_preserves_midnight_and_overnight_day(minute, expected):
    assert village.clock_time(minute) == expected


def test_action_availability_uses_clock_time_for_success_and_rejection(cast):
    world = cast["torvin"]
    result = do(cast, "torvin", "village_wait", minutes=5)
    assert "Available again at 08:05." in result["explanation"]
    assert world.state.village.residents["torvin"].available_at == 485
    assert act(world, "village_wait", minutes=5)["explanation"] == "Available again at 08:05."
    advance_to(cast, 1435)
    result = do(cast, "torvin", "village_wait", minutes=10)
    assert "Available again at 00:05 on day 2." in result["explanation"]


def test_free_look_and_read_never_end_actor_turn(cast):
    world = cast["torvin"]
    rid = village_turns.next_actor(world)
    do(cast, rid, "look", target="anvil")
    assert village_turns.next_actor(world) == rid
    walk(cast, rid, "square-forge")
    advance_to(cast, world.state.village.residents[rid].available_at)
    do(cast, rid, "read", target="noticeboard")
    before = world.state.village.minute
    assert village_turns.next_actor(world) == rid
    assert world.state.village.minute == before
    do(cast, rid, "village_wait", minutes=5)
    assert village_turns.next_actor(world) != rid


def test_wait_is_capped_while_somebody_is_baking(cast):
    do(cast, "jory", "village_work")
    world = cast["torvin"]
    descriptor = next(action for action in world.actions() if action["id"] == "village_wait")
    assert descriptor["arguments_schema"]["properties"]["minutes"]["maximum"] == 15
    assert act(world, "village_wait", minutes=30)["code"] == "INVALID_ARGUMENTS"
    advance_to(cast, 505)
    assert village_life.wait_limit(world) == 5


def test_walked_routes_can_be_shared_and_map_suggests_shortest_known_path(cast):
    walk(cast, "torvin", "square-forge", "square-stable")
    state = cast["torvin"].state.village
    assert set(state.residents["torvin"].known_routes) == {"square-forge", "square-stable"}
    do(cast, "torvin", "village_share_route", target="bren", destination="forge")
    assert set(state.residents["bren"].known_routes) == {"square-forge", "square-stable"}
    assert state.residents["mara"].known_routes == []
    walk(cast, "torvin", "square-stable")
    do(cast, "torvin", "read", target="noticeboard")
    routes = cast["torvin"].observe()["village"]["personal"]["routes"]
    mill = next(route for route in routes if route["destination"] == "watermill")
    assert mill["steps"] == 2


def test_horse_is_fed_led_hitched_unhitched_and_returned_physically(cast):
    hitch(cast)
    world = cast["bren"]
    assert world.state.village.wagon.hitched
    assert world.state.entities["work-horse"].location == "forge"
    do(cast, "bren", "village_unhitch")
    assert not world.state.village.wagon.hitched
    advance_to(cast, 1320)
    walk(cast, "bren", "square-forge", "square-cottages")
    assert act(world, "village_sleep")["code"] in {"ACTOR_BUSY", "HORSE_NOT_STABLED"}
    walk(cast, "bren", "square-cottages", "square-stable")
    do(cast, "bren", "village_release_horse")
    assert world.state.entities["work-horse"].location == "stable"
    assert world.state.village.horse_leader is None


@pytest.mark.parametrize("rid", ["ada", "jory"])
def test_backup_driver_can_use_hithed_wagon_when_nella_is_away(cast, rid):
    do(cast, "torvin", "village_work")
    do(cast, "torvin", "village_fit_wagon")
    hitch(cast)
    advance_to(cast, max(cast["torvin"].state.village.minute, 515))
    door = "square-farm" if rid == "ada" else "square-bakery"
    walk(cast, rid, door, "square-forge")
    do(cast, rid, "village_drive_wagon")
    do(cast, rid, "village_start_route")
    assert cast[rid].state.village.wagon.driver == rid
    advance_to(cast, cast[rid].state.village.minute + 60)
    assert cast[rid].actor.location_id == cast[rid].state.entities["wagon"].location
    assert cast[rid].actor.location_id == cast[rid].state.entities["work-horse"].location
    assert cast[rid].state.village.role_contributions[rid] > 0


@pytest.mark.parametrize("path,door", [
    (["square-forge", "square-stable"], "stable-shed"),
    (["square-forge", "square-bridge"], "bridge-woods"),
    (["square-forge", "square-bridge"], "bridge-river"),
    (["square-forge", "square-bakery", "mill-bakery"], "mill-shed-path"),
    (["square-forge", "square-tavern"], "tavern-yard-gate")
])
def test_wagon_refuses_all_five_footpaths(cast, path, door):
    wagon_ready(cast)
    walk(cast, "nella", *path)
    advance_to(cast, cast["nella"].state.village.residents["nella"].available_at)
    before = cast["nella"].actor.location_id
    assert act(cast["nella"], "go", target=door)["code"] == "WAGON_ROAD_ONLY"
    assert cast["nella"].actor.location_id == before


def test_foot_capacity_counts_tools_and_bulk_fee_is_paid_once(cast):
    world = cast["hobb"]
    do(cast, "hobb", "village_work")
    do(cast, "hobb", "take", target="mill-beam")
    do(cast, "hobb", "take", target="joiner-hammer")
    do(cast, "hobb", "village_load_goods", goods={"planks": 1}, destination="watermill")
    advance_to(cast, world.state.village.residents["hobb"].available_at)
    assert act(world, "village_load_goods", goods={"timber": 1}, destination="watermill")["outcome"] == "REJECTED"
    before = world.state.village.coins.copy()
    walk(cast, "hobb", "square-workshop", "square-bridge", "bridge-mill")
    do(cast, "hobb", "village_unload_goods")
    assert world.state.village.stocks["watermill"]["planks"] == 1
    assert world.state.village.coins["mara"] == before["mara"] - 4
    assert world.state.village.coins["hobb"] == before["hobb"] + 4
    assert sum(world.state.village.coins.values()) == sum(before.values())


@pytest.mark.parametrize("body_capacity", [2, 3, 5])
def test_foot_limit_counts_tools_even_for_custom_stronger_bodies(cast, body_capacity):
    do(cast, "hobb", "take", target="mill-beam")
    do(cast, "hobb", "take", target="joiner-hammer")
    cast["hobb"].actor.body.capacity = body_capacity
    assert village_services.foot_capacity(cast["hobb"], "hobb") == max(0, min(3, body_capacity) - 2)


def test_timber_route_crosses_footpath_and_delivers_actual_stock(cast):
    world = cast["rowan"]
    do(cast, "rowan", "village_work")
    do(cast, "rowan", "village_start_timber_route")
    advance_to(cast, world.state.village.minute + 50)
    assert world.state.village.stocks["workshop"]["timber"] == 5
    assert world.state.village.coins["rowan"] == 23
    assert not world.state.village.residents["rowan"].timber_route
    assert any(event.action == "timber_walk" for event in world.state.village.events)


def test_renewable_hay_herbs_logs_and_manure_speed_real_harvests(cast):
    do(cast, "ada", "village_grow_hay")
    do(cast, "iona", "village_gather_herbs")
    do(cast, "rowan", "village_fell_logs")
    state = cast["ada"].state.village
    assert state.stocks["farm"]["hay"] == 2 and state.stocks["cottages"]["herbs"] == 12
    assert state.stocks["woods"]["logs"] == 12
    do(cast, "bren", "village_work")
    do(cast, "bren", "village_load_goods", goods={"manure": 1}, destination="farm")
    walk(cast, "bren", "square-stable", "square-farm")
    do(cast, "bren", "village_unload_goods")
    do(cast, "ada", "village_fertilize")
    advance_to(cast, max(state.minute, state.residents["ada"].next_work))
    do(cast, "ada", "village_work")
    assert state.residents["ada"].next_work - state.minute == 30
    assert state.residents["ada"].fertilized_harvests == 2
    assert state.stocks["farm"]["manure"] == 0


def test_noon_iron_is_paid_on_delivery_and_unsold_stock_leaves(cast):
    walk(cast, "torvin", "square-forge")
    advance_to(cast, 720)
    world = cast["torvin"]
    do(cast, "torvin", "village_buy_iron", quantity=3)
    before = world.state.village.coins.copy()
    walk(cast, "torvin", "square-forge")
    do(cast, "torvin", "village_unload_goods")
    assert world.state.village.stocks["forge"]["iron"] == 7
    assert world.state.village.coins["iron-trader"] == 9
    assert world.state.village.coins["torvin"] == before["torvin"] - 9
    advance_to(cast, 1080)
    assert world.state.village.stocks["village-square"]["iron"] == 0
    assert world.state.entities["iron-trader"].location == "bridge"
    advance_to(cast, 1085)
    assert world.state.entities["iron-trader"].location is None


def test_paid_meal_no_money_no_food_consumed_and_late_payment_recovers(cast):
    advance_to(cast, 1170)
    world = cast["elsie"]
    state = world.state.village
    state.coins["elsie"] = 0
    before = state.stocks["tavern"]["bread"]
    do(cast, "elsie", "village_eat")
    assert state.stocks["tavern"]["bread"] == before
    assert state.residents["elsie"].life.meals["0:daily"].status == "missed"
    state.coins["elsie"] = 1
    do(cast, "elsie", "village_eat")
    assert state.stocks["tavern"]["bread"] == before - 1
    assert state.residents["elsie"].life.missed_meals == 0


def test_closing_grace_accepts_1905_input_and_prevents_1935_start(cast):
    walk(cast, "jory", "bakery-tavern")
    world = cast["jory"]
    village_goods.take(world, "bakery", "flour", 6)
    advance_to(cast, 1140)
    do(cast, "jory", "village_eat")
    walk(cast, "jory", "bakery-tavern")
    assert world.state.village.minute == 1145
    village_goods.put(world, "bakery", "flour", 2)
    advance_to(cast, 1170)
    do(cast, "jory", "village_work")
    assert world.state.village.residents["jory"].life.work is not None
    advance_to(cast, 1200)
    assert world.state.village.residents["jory"].life.work is None
    assert act(world, "village_work")["code"] == "OUTSIDE_WORK_HOURS"


@pytest.mark.parametrize("minute,allowed", [(1170, True), (1175, False)])
def test_exact_production_closing_boundary(cast, minute, allowed):
    walk(cast, "jory", "bakery-tavern")
    advance_to(cast, 1140)
    do(cast, "jory", "village_eat")
    walk(cast, "jory", "bakery-tavern")
    advance_to(cast, minute)
    result = act(cast["jory"], "village_work")
    assert (result["outcome"] == "APPLIED") == allowed
    if not allowed:
        assert result["code"] == "OUTSIDE_WORK_HOURS"


def test_fifo_spoilage_preserves_original_expiry_through_foot_transport_and_reload(cast):
    world = cast["finn"]
    do(cast, "finn", "village_work")
    expiry = world.state.village.stock_lots["riverbank"]["fish"][0].expires_at
    do(cast, "finn", "village_load_goods", goods={"fish": 1}, destination="tavern")
    assert world.state.village.residents["finn"].shipments[0].lots[0].expires_at == expiry
    restored = World(world.scenario, world.path, "complete", "finn", "session-finn")
    village.advance(restored, expiry - restored.state.village.minute)
    assert restored.state.village.residents["finn"].shipments == []
    assert restored.state.village.residents["finn"].cargo == {}
    assert any(event.action == "cargo_spoiled" for event in restored.state.village.events)
    assert restored.state.village.stock_history[-1].minute == expiry


def test_tiredness_hunger_stack_and_real_tea_relief(cast):
    advance_to(cast, 1560)
    world = cast["iona"]
    state = world.state.village
    assert village_life.cooldown_factor(world, "iona") == 4
    village_goods.put(world, "cottages", "tea", 1)
    do(cast, "iona", "village_drink_tea")
    assert village_life.cooldown_factor(world, "iona") == 2
    assert state.stocks["cottages"]["tea"] == 0
    assert act(world, "village_work")["code"] in {"REST_DUE", "ACTOR_BUSY"}


def test_public_stock_report_progress_deadlines_and_hourly_logging(cast):
    world = cast["torvin"]
    walk(cast, "torvin", "square-forge")
    world.state.village.project_contributions["dig_well"] = {"torvin": 2, "bren": 2}
    result = do(cast, "torvin", "read", target="noticeboard")
    assert "dig well: 4/6" in result["explanation"]
    assert "Current workplace stock" in result["explanation"]
    report = world.observe()["village"]["personal"]["stock_report"]
    assert report["forge"]["iron"] == 4
    advance_to(cast, 1145)
    assert world.state.village.deadlines["bread_delivery"].overdue
    assert world.evaluate_goal("village_restored")["status"] == "active"
    assert [row.minute for row in world.state.village.stock_history] == list(range(480, 1141, 60))


def test_fifo_oldest_consumed_first_and_fresh_additions_keep_their_expiry(cast):
    world = cast["torvin"]
    advance_to(cast, 540)
    village_goods.put(world, "tavern", "bread", 2)
    taken = village_goods.take(world, "tavern", "bread", 12)
    assert [(lot.quantity, lot.produced_at) for lot in taken] == [(12, 480)]
    remaining = world.state.village.stock_lots["tavern"]["bread"]
    assert [(lot.quantity, lot.expires_at) for lot in remaining] == [(2, 1980)]
    advance_to(cast, 1920)
    assert world.state.village.stocks["tavern"]["bread"] == 2
    advance_to(cast, 1980)
    assert world.state.village.stocks["tavern"]["bread"] == 0


def test_foot_delivery_uses_freshness_and_carrier_fee(cast):
    walk(cast, "nella", "square-store", "square-farm")
    world = cast["nella"]
    do(cast, "nella", "village_load_goods", goods={"grain": 1}, destination="watermill")
    assert sum(lot.quantity for lot in world.state.village.stock_lots["farm"]["grain"]) == 5
    before = world.state.village.coins.copy()
    walk(cast, "nella", "farm-mill")
    do(cast, "nella", "village_unload_goods")
    assert world.state.village.coins["nella"] == before["nella"] + 1
    assert world.state.village.residents["nella"].delivery is None
    assert world.state.village.stocks["watermill"]["grain"] == 7
    assert sum(lot.quantity for lot in world.state.village.stock_lots["watermill"]["grain"]) == 7


def test_wagon_unaffordable_batch_is_atomic_including_driver_and_fee(cast):
    wagon_ready(cast)
    go_to(cast, "nella", "farm")
    do(cast, "nella", "village_load_wagon", goods={"grain": 2}, destination="watermill")
    go_to(cast, "nella", "watermill")
    do(cast, "nella", "village_stop_route")
    advance_to(cast, cast["nella"].state.village.residents["nella"].available_at)
    world = cast["nella"]
    world.state.village.coins["mara"] = 2
    before = world.state.village.model_dump()
    result = act(world, "village_unload_wagon")
    assert result["outcome"] == "REJECTED"
    assert "delivery fee" in result["explanation"]
    assert world.state.village.model_dump() == before
    world.state.village.coins["mara"] = 3
    do(cast, "nella", "village_unload_wagon")
    assert world.state.village.coins["mara"] == 0
    assert world.state.village.wagon.loads == []


def test_six_mixed_wagon_units_pay_only_one_fee_and_keep_freshness(cast):
    wagon_ready(cast)
    do(cast, "ada", "village_grow_hay")
    go_to(cast, "nella", "farm")
    world = cast["nella"]
    expiry = world.state.village.stock_lots["farm"]["hay"][0].expires_at
    before = world.state.village.coins.copy()
    cost = 4 * world.scenario.village.prices["grain"] + 2 * world.scenario.village.prices["hay"]
    do(cast, "nella", "village_load_wagon", goods={"grain": 4, "hay": 2}, destination="watermill")
    assert sum(item.quantity for item in world.state.village.wagon.loads) == 6
    go_to(cast, "nella", "watermill")
    do(cast, "nella", "village_unload_wagon")
    state = world.state.village
    assert state.coins["mara"] == before["mara"] - cost - 1
    assert state.coins["ada"] == before["ada"] + cost
    assert state.coins["nella"] == before["nella"] + 1
    assert state.stock_lots["watermill"]["hay"][0].expires_at == expiry


@pytest.mark.parametrize("minute,overdue", [(1140, False), (1145, True)])
def test_deadline_boundary_and_late_recovery(cast, minute, overdue):
    advance_to(cast, minute)
    world = cast["torvin"]
    village_goods.complete_deadline(world, "bread_delivery", "nella")
    deadline = world.state.village.deadlines["bread_delivery"]
    assert deadline.completed_at == minute and deadline.overdue == overdue
    assert any(event.action == "deadline_completed" for event in world.state.village.notices)


def test_explicit_urgent_project_requires_both_partners_and_ends_at_0200(cast):
    for item in ["well-timber", "well-rope"]:
        go_to(cast, "bren", "forge")
        do(cast, "bren", "take", target=item)
    go_to(cast, "torvin", "mill-shed")
    do(cast, "torvin", "take", target="shovel")
    advance_to(cast, 1140)
    for rid in ["torvin", "bren"]:
        go_to(cast, rid, "tavern")
        do(cast, rid, "village_eat")
        go_to(cast, rid, "tavern-yard")
    advance_to(cast, 1330)
    world = cast["torvin"]
    assert act(world, "village_project", project="dig_well")["code"] == "REST_DUE"
    do(cast, "torvin", "village_urgent_work", project="dig_well")
    advance_to(cast, world.state.village.residents["torvin"].available_at)
    assert act(world, "village_project", project="dig_well")["code"] == "CREW_UNAVAILABLE"
    do(cast, "bren", "village_urgent_work", project="dig_well")
    do(cast, "torvin", "village_project", project="dig_well")
    advance_to(cast, 1560)
    assert act(world, "village_project", project="dig_well")["code"] == "REST_DUE"
    assert village_life.cooldown_factor(world, "torvin") == 2


def test_full_village_physical_supply_projects_meals_horse_return_and_sleep(cast):
    wagon_ready(cast)
    world = cast["torvin"]
    state = world.state.village
    assert state.stocks["forge"]["wagon-parts"] == 0
    assert state.stock_lots["forge"]["wagon-parts"] == []
    do(cast, "nella", "village_start_route")
    for rid in ["mara", "elsie", "hobb", "jory", "ada", "rowan", "iona", "finn"]:
        do(cast, rid, "village_work")
    for rid, action in [("ada", "village_grow_hay"), ("iona", "village_gather_herbs"),
                        ("rowan", "village_fell_logs")]:
        advance_to(cast, max(state.minute, state.residents[rid].next_work))
        do(cast, rid, action)
    do(cast, "iona", "village_drink_tea")
    go_to(cast, "ada", "stable")
    do(cast, "ada", "village_load_goods", goods={"manure": 1}, destination="farm")
    go_to(cast, "ada", "farm")
    do(cast, "ada", "village_unload_goods")
    do(cast, "ada", "village_fertilize")
    do(cast, "garrick", "village_patrol")
    do(cast, "rowan", "village_start_timber_route")
    for item in ["well-timber", "well-rope"]:
        do(cast, "bren", "take", target=item)
    go_to(cast, "bren", "tavern-yard")
    go_to(cast, "torvin", "mill-shed")
    do(cast, "torvin", "take", target="shovel")
    go_to(cast, "torvin", "tavern-yard")
    for rid in ["torvin", "bren"] * 3:
        do(cast, rid, "village_project", project="dig_well")
    for item in ["mill-beam", "joiner-hammer"]:
        do(cast, "hobb", "take", target=item)
    do(cast, "hobb", "village_load_goods", goods={"planks": 1}, destination="watermill")
    go_to(cast, "hobb", "watermill")
    do(cast, "hobb", "village_unload_goods")
    advance_to(cast, 840)
    for rid in ["hobb", "mara"] * 3:
        do(cast, rid, "village_project", project="repair_mill")
    assert world.evaluate_goal("village_restored")["status"] == "active"
    assert state.stocks["watermill"]["planks"] == 0
    assert any(notice.action == "project_completed" and "iona" in notice.witnesses for notice in state.notices)
    assert state.deadlines["bread_delivery"].completed_at < 1140
    assert len(state.role_contributions) == 12
    advance_to(cast, 1140)
    coins_before = state.coins.copy()
    for rid in (rid for rid in cast if rid != "elsie"):
        go_to(cast, rid, "tavern")
        do(cast, rid, "village_eat")
        assert state.residents[rid].life.meals["0:daily"].status == "eaten"
    if state.minute < 1170:
        advance_to(cast, 1170)
    go_to(cast, "elsie", "tavern")
    do(cast, "elsie", "village_eat")
    assert state.residents["elsie"].life.meals["0:daily"].status == "eaten"
    assert sum(state.coins.values()) == sum(coins_before.values())
    assert state.coins["elsie"] == coins_before["elsie"] + 11
    go_to(cast, "bren", world.state.entities["work-horse"].location)
    do(cast, "bren", "village_unhitch")
    go_to(cast, "bren", "stable")
    do(cast, "bren", "village_release_horse")
    assert not state.wagon.hitched
    advance_to(cast, 1320)
    for rid in cast:
        resident = world.scenario.village.residents[rid]
        if state.minute < resident.bed_minute:
            advance_to(cast, resident.bed_minute)
        go_to(cast, rid, resident.home)
        do(cast, rid, "village_sleep")
    assert all(person.life.sleeping for person in state.residents.values())
    verification = world.evaluate_goal("village_restored")
    assert verification["status"] == "achieved", verification
    assert all(verification["evidence"]["checks"].values())
    earliest = min(person.life.sleep_until for person in state.residents.values())
    selected = village_turns.next_actor(world)
    assert state.minute == earliest and not state.residents[selected].life.sleeping
    assert all(not person.life.fatigue_days for person in state.residents.values())


@pytest.mark.asyncio
async def test_controller_chains_free_looks_with_metadata_without_paid_models(configuration):
    data, config = configuration
    value = scenario()
    value.village.start_minute = 480
    write_json(data / "scenarios" / "millhaven-expanded.json", value.model_dump(mode="json"))
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [
        ("look", {"target": "square-cottages"}), ("look", {"target": "square-cottages"}), ("village_wait", {"minutes": 5})])
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in range(3):
            await controller.command("step")
            assert controller.run["actor_id"] == "torvin"
        requests = [event for event in controller.events.events if event["kind"] == "action_request"]
        results = [event for event in controller.events.events if event["kind"] == "action_result"]
        assert [event["free_action"] for event in requests] == [True, True, False]
        assert [event["free_action"] for event in results] == [True, True, False]
        assert controller.status()["world"]["village"]["minute"] == 480
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_controller_rejects_blocked_production_before_world_dispatch(configuration):
    data, config = configuration
    value = scenario()
    value.village.start_minute = 1200
    value.village.breakdown_minute = 1320
    value.village.goal_deadlines = {}
    write_json(data / "scenarios" / "millhaven-expanded.json", value.model_dump(mode="json"))
    config.world.scenario = "millhaven-expanded"
    controller = Controller(ROOT, data, config)
    proxy = TestProxy(controller.proxy, [("village_work", {}), ("village_wait", {"minutes": 5})])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        await controller.command("step")
        requests = [event["payload"]["action_id"] for event in controller.events.events
                    if event["kind"] == "action_request"]
        assert requests == ["village_wait"]
        assert any(event["kind"] == "validation" and not event["payload"]["valid"]
                   for event in controller.events.events)
        assert "Do not attempt unavailable work" in proxy.contexts[1]["validation_error"]
    finally:
        await controller.shutdown()


def test_sleepers_do_not_receive_routes_or_unwitnessed_deadline_outcomes(cast):
    world = cast["iona"]
    advance_to(cast, 1320)
    do(cast, "iona", "village_sleep")
    village_goods.complete_deadline(world, "bread_delivery", "nella")
    assert "bread_delivery" not in world.observe()["village"]["personal"]["deadlines"]
    go_to(cast, "torvin", "cottages")
    advance_to(cast, cast["torvin"].state.village.residents["torvin"].available_at)
    assert act(cast["torvin"], "village_share_route", target="iona", destination="forge")["code"] == "RESIDENT_ASLEEP"
    advance_to(cast, world.state.village.residents["iona"].life.sleep_until)
    go_to(cast, "iona", "village-square")
    do(cast, "iona", "read", target="noticeboard")
    assert world.observe()["village"]["personal"]["deadlines"]["bread_delivery"]["completed_at"] == 1320

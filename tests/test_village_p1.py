from npc_sim import village, village_turns
from npc_sim.world import World
from test_village_life import advance_to, cast, do, walk
from test_world import act


def repair_and_collect(cast):
    do(cast, "torvin", "village_work")
    do(cast, "torvin", "village_fit_wagon")
    assert not cast["torvin"].state.village.wagon.repaired
    walk(cast, "nella", "square-store", "square-forge")
    advance_to(cast, 515)
    assert cast["nella"].state.village.wagon.repaired
    do(cast, "nella", "village_drive_wagon")


def test_real_parts_complete_real_wagon_and_repair_blocks_leaving(cast):
    world = cast["torvin"]
    assert act(world, "village_fit_wagon")["outcome"] == "REJECTED"
    do(cast, "torvin", "village_work")
    do(cast, "torvin", "village_fit_wagon")
    assert world.state.village.stocks["forge"]["wagon-parts"] == 0
    assert act(world, "go", target="square-forge")["code"] in {"ACTOR_BUSY", "WORK_MUST_FINISH"}
    advance_to(cast, 510)
    assert not world.state.village.wagon.repaired
    advance_to(cast, 515)
    assert world.state.village.wagon.repaired
    assert any(notice.action == "wagon_ready" for notice in world.state.village.notices)
    assert act(world, "village_fit_wagon")["outcome"] == "REJECTED"


def test_wagon_accepts_six_mixed_units_in_one_action_and_settles_one_unload(cast):
    repair_and_collect(cast)
    walk(cast, "nella", "square-forge", "square-store")
    world = cast["nella"]
    state = world.state.village
    before_coins = sum(state.coins.values())
    advance_to(cast, state.residents["nella"].available_at)
    revision = world.revision()
    request = {"goods": {"grain": 3, "flour": 2, "bread": 1}, "destination": "tavern"}
    result = world.act("mixed", revision, "village_load_wagon", request)
    assert result["outcome"] == "APPLIED"
    assert world.act("mixed", revision, "village_load_wagon", request) == result
    assert sum(item.quantity for item in state.wagon.loads) == 6
    advance_to(cast, state.minute + 5)
    assert act(world, "village_load_wagon", goods={"bread": 1}, destination="tavern")["outcome"] == "REJECTED"
    assert state.stocks["general-store"] == {"grain": 0, "flour": 0, "bread": 1}
    walk(cast, "nella", "square-store", "square-tavern")
    assert world.state.entities["wagon"].location == "tavern"
    do(cast, "nella", "village_unload_wagon")
    assert state.wagon.loads == []
    assert state.stocks["tavern"] == {"grain": 3, "flour": 2, "bread": 13, "meals": 0}
    assert sum(state.coins.values()) == before_coins
    assert state.coins["nella"] == 90 and state.coins["elsie"] == 140


def test_unaffordable_bulk_unloading_is_atomic(cast):
    repair_and_collect(cast)
    walk(cast, "nella", "square-forge", "square-bakery")
    do(cast, "nella", "village_load_wagon", goods={"bread": 4}, destination="tavern")
    walk(cast, "nella", "bakery-tavern")
    world = cast["nella"]
    state = world.state.village
    state.coins["elsie"] = 11
    advance_to(cast, state.residents["nella"].available_at)
    before = state.model_copy(deep=True)
    result = act(world, "village_unload_wagon")
    assert result["outcome"] == "REJECTED" and "cannot pay" in result["explanation"]
    assert state.coins == before.coins and state.stocks == before.stocks
    assert state.wagon.loads == before.wagon.loads


def test_standing_route_persists_moves_real_driver_and_delivers_before_repair(cast):
    repair_and_collect(cast)
    world = cast["nella"]
    state = world.state.village
    total_stock = sum(sum(stock.values()) for stock in state.stocks.values())
    total_coins = sum(state.coins.values())
    do(cast, "nella", "village_start_route")
    restored = World(world.scenario, world.path, "life", "nella", "session-nella")
    village.advance(restored, 100)
    current = restored.state.village
    assert current.wagon.automatic and current.bread_deliveries == 4
    assert not current.breakdown and current.bread_after_repair == 0
    assert current.stocks["tavern"]["bread"] == 16
    assert current.role_contributions["nella"] > 0
    assert restored.actor.location_id == restored.state.entities["wagon"].location
    assert sum(current.coins.values()) == total_coins
    assert sum(sum(stock.values()) for stock in current.stocks.values()) + sum(
        item.quantity for item in current.wagon.loads) == total_stock
    assert {"wagon_load", "wagon_unload", "wagon_drive"} <= {event.action for event in current.events}
    assert all(item.quantity <= 6 for item in current.wagon.loads)


def test_auto_driver_skips_ai_turns_until_meal_parks_wagon(cast):
    repair_and_collect(cast)
    do(cast, "nella", "village_start_route")
    for rid in cast:
        cast[rid].state.village.residents[rid].available_at = 1140
    selected = village_turns.next_actor(cast["torvin"])
    assert selected in cast and cast["torvin"].state.village.minute == 1140
    assert not cast["nella"].state.village.wagon.automatic
    assert cast["nella"].state.village.wagon.driver is None
    assert cast["nella"].actor.location_id == cast["nella"].state.entities["wagon"].location
    assert "19:00" in cast["nella"].state.village.wagon.blocker


def test_free_noticeboard_map_and_bell_news_reach_all_awake_villagers(cast):
    walk(cast, "hobb", "square-workshop")
    world = cast["hobb"]
    before = world.state.village.minute
    result = act(world, "read", target="noticeboard")
    assert result["outcome"] == "APPLIED" and world.state.village.minute == before
    assert "Village map (free)" in result["explanation"]
    for route in ["farm-mill", "mill-bakery", "bakery-tavern", "mill-shed-path"]:
        assert route in world.state.village.residents["hobb"].known_routes
    assert world.state.village.residents["garrick"].known_routes == []
    advance_to(cast, 840)
    notice = world.state.village.notices[-1]
    assert notice.action == "breakdown" and notice.minute == 840
    assert set(cast) <= set(notice.witnesses)
    assert "14:00" in notice.result and "Hobb and Mara" in notice.result
    assert any(event["action"] == "breakdown" for event in cast["hobb"].observe()["village"]["notices"])


def test_important_tool_discovery_tells_owner_and_meetings_name_the_site(cast):
    walk(cast, "bren", "stable-shed")
    state = cast["bren"].state.village
    belief = state.residents["torvin"].beliefs["shovel"]
    assert belief.location == "mill-shed" and belief.source == "bell:bren"
    assert any(notice.action == "discovery" and notice.target == "shovel" for notice in state.notices)
    do(cast, "bren", "village_meeting", project="dig_well")
    assert state.meetings["dig_well"] == ["bren"]
    assert cast["bren"].state.entities["tavern-yard"].name in state.notices[-1].result
    assert "torvin" in state.notices[-1].witnesses
    assert do(cast, "torvin", "village_meeting", project="dig_well")["code"] == "MEETING_AGREED"
    assert set(state.meetings["dig_well"]) == {"torvin", "bren"}


def test_sleeping_villager_can_learn_missed_bell_notices_by_reading_board_later(cast):
    advance_to(cast, 1320)
    do(cast, "iona", "village_sleep")
    world = cast["iona"]
    village.announce(cast["torvin"], "torvin", "meeting", "dig_well", "The agreed meeting is at the well site.")
    assert "iona" not in world.state.village.notices[-1].witnesses
    advance_to(cast, 1920)
    walk(cast, "iona", "square-cottages")
    do(cast, "iona", "read", target="noticeboard")
    notices = world.observe()["village"]["notices"]
    assert any("agreed meeting" in notice["result"] for notice in notices)
    assert "iona" in world.state.village.notices[-1].witnesses


def test_answers_use_target_identity_and_do_not_quote_audit_logs(cast):
    walk(cast, "torvin", "square-forge", "square-stable")
    world = cast["torvin"]
    village.event(world, "bren", "go", "stable", "Go: Tavern yard gate")
    village.event(world, "bren", "learn", "shovel", "Believes its location is mill-shed; source: observation.")
    result = do(cast, "torvin", "village_ask", target="bren", topic="work")
    assert 'Bren says: "I am the stable keeper.' in result["explanation"]
    result = do(cast, "torvin", "village_ask", target="bren", topic="news")
    assert "Go:" not in result["explanation"] and "Believes" not in result["explanation"]


def test_recovery_accepts_bread_delivered_before_repair_but_waits_for_batches(cast):
    state = cast["torvin"].state.village
    state.shovel_recovered = state.well_dug = state.repaired = state.wagon.repaired = True
    state.bread_deliveries = 1
    state.role_contributions = {rid: 1 for rid in cast}
    do(cast, "jory", "village_work")
    assert cast["torvin"].evaluate_goal("village_restored")["status"] == "active"
    advance_to(cast, 510)
    assert state.bread_after_repair == 0
    result = cast["torvin"].evaluate_goal("village_restored")
    assert result["status"] == "active"
    assert result["evidence"]["checks"]["committed_batches_finished"]
    assert not result["evidence"]["checks"]["evening_reached"]


def test_full_village_recovery_evening_meal_and_overnight_scheduler(cast):
    repair_and_collect(cast)
    world = cast["torvin"]
    state = world.state.village
    for rid in ["bren", "mara", "elsie", "hobb", "jory", "ada", "rowan", "iona", "finn"]:
        do(cast, rid, "village_work")
    do(cast, "garrick", "village_patrol")
    do(cast, "nella", "village_start_route")
    for item in ["well-timber", "well-rope"]:
        do(cast, "torvin", "take", target=item)
    walk(cast, "torvin", "square-forge", "square-stable", "stable-shed")
    do(cast, "torvin", "take", target="shovel")
    walk(cast, "torvin", "stable-shed", "square-stable", "square-tavern", "tavern-yard-gate")
    walk(cast, "bren", "square-stable", "square-tavern", "tavern-yard-gate")
    for rid in ["torvin", "bren"] * 3:
        do(cast, rid, "village_project", project="dig_well")
    assert state.well_dug and state.shovel_recovered
    for item in ["mill-beam", "joiner-hammer"]:
        do(cast, "hobb", "take", target=item)
    walk(cast, "hobb", "square-workshop", "square-bridge", "bridge-mill")
    advance_to(cast, 840)
    assert state.breakdown and state.bread_deliveries > 0 and state.bread_after_repair == 0
    for rid in ["hobb", "mara"] * 3:
        do(cast, rid, "village_project", project="repair_mill")
    assert world.evaluate_goal("village_restored")["status"] == "active"
    assert set(state.role_contributions) == set(cast)
    assert all(state.role_contributions[rid] > 0 for rid in cast)

    def go_to(rid, destination):
        actor = cast[rid]
        path = village.route(actor, actor.actor.location_id, destination)
        assert path is not None
        for location in path:
            door = next(eid for eid, entity in actor.state.entities.items()
                        if entity.kind == "door" and {
                            entity.location, entity.destination} == {actor.actor.location_id, location})
            do(cast, rid, "go", target=door)

    advance_to(cast, 1140)
    for rid in cast:
        go_to(rid, "tavern")
        do(cast, rid, "village_eat")
        assert state.residents[rid].life.meals["0:daily"].status == "eaten"
        assert state.residents[rid].life.missed_meals == 0
    advance_to(cast, 1320)
    for rid in cast:
        resident = world.scenario.village.residents[rid]
        if state.minute < resident.bed_minute:
            advance_to(cast, resident.bed_minute)
        go_to(rid, resident.home)
        do(cast, rid, "village_sleep")
    wake = min(person.life.sleep_until for person in state.residents.values())
    assert all(person.life.sleeping for person in state.residents.values())
    selected = village_turns.next_actor(world)
    assert state.minute == wake and not state.residents[selected].life.sleeping
    assert all(person.life.missed_meals == 0 for person in state.residents.values())

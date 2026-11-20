import json
from pathlib import Path

from npc_sim import village, village_ai, village_economy, village_fallback, village_goods, village_depot
from npc_sim.living_village import build_generation_17
from npc_sim.storage import read_json
from npc_sim.models import Scenario
from npc_sim.world import World
from test_world import act


ROOT = Path(__file__).resolve().parents[1]


def make_world(tmp_path):
    scenario = Scenario.model_validate(json.loads(
        (ROOT / "data" / "scenarios" / "millhaven-resilient.json").read_text(encoding="utf-8")))
    scenario.village.start_minute = 600
    scenario.village.breakfast = False
    for agent in scenario.agents:
        agent.start_location = scenario.village.residents[agent.id].work
    world = World(scenario, tmp_path / "resilient.json", "world", "ada", "session-ada")
    for agent in scenario.agents:
        world.bind(agent.id, f"session-{agent.id}")
    return world


def applied(world, action, **args):
    result = act(world, action, **args)
    assert result["outcome"] == "APPLIED", (result["code"], result["explanation"])
    return result


def test_complete_scenario_combines_legacy_and_managed_storage(tmp_path):
    scenario = Scenario.model_validate(json.loads(
        (ROOT / "data" / "scenarios" / "millhaven-complete.json").read_text(encoding="utf-8")))
    assert scenario.village.legacy is not None
    scenario.village.start_minute = 600
    scenario.village.breakfast = False
    for agent in scenario.agents:
        agent.start_location = scenario.village.residents[agent.id].work
    world = World(scenario, tmp_path / "complete.json", "complete", "ada", "session-ada")
    for agent in scenario.agents:
        world.bind(agent.id, f"session-{agent.id}")
    assert village_depot.enabled(world)
    assert world.state.village.legacy is not None
    assert world.state.village.depot_lots["farm-feed-store"]["ada"]["grain"][0].quantity == 4
    assert {"village_store_feed", "village_depot_load", "village_read_chronicle"} <= {
        action["id"] for action in world.actions()}
    applied(world, "village_store_feed", goods={"hay": 1})
    world.actor.location_id = "farm-feed-store"
    village.advance(world, 5)
    applied(world, "village_unload_goods")
    assert world.state.village.stocks["farm-feed-store"]["hay"] == 4
    assert village_ai.observation(world)["village"]["stored_owners"]["ada"]["hay"] == 4
    world.actor.location_id = "village-square"
    village.advance(world, world.state.village.residents["ada"].available_at - world.state.village.minute)
    assert applied(world, "village_read_chronicle")["code"] == "CHRONICLE_READ"
    world.save()
    restored = World(scenario, world.path, "complete", "ada", "session-ada")
    assert restored.state.village.depot_lots == world.state.village.depot_lots
    assert restored.state.village.legacy.learned["ada"] == world.state.village.legacy.learned["ada"]


def test_generation_17_is_cumulative_scenario():
    complete = Scenario.model_validate(read_json(
        ROOT / "data" / "scenarios" / "millhaven-complete.json"))
    living = Scenario.model_validate(read_json(
        ROOT / "data" / "scenarios" / "millhaven-living.json"))

    generated = build_generation_17(complete, living)

    assert generated.id == "millhaven-generation-17"
    assert generated.village.legacy == complete.village.legacy
    assert generated.village.equipment == living.village.equipment
    assert generated.village.firewood == living.village.firewood
    assert generated.village.social == living.village.social
    assert generated.village.events == living.village.events
    assert set(complete.entities) <= set(generated.entities)
    assert set(complete.village.stocks) <= set(generated.village.stocks)
    assert {"bridge-inspection", "road-inspection", "millrace-cleaning"} <= set(
        generated.village.maintenance.tasks)
    assert {"cottage-upkeep", "farm-railings"} <= set(generated.village.maintenance.tasks)
    assert {"north-fields", "north-pasture", "farm-cistern", "supply-depot",
            "communal-granary", "communal-kitchen", "farm-feed-store"} <= set(generated.entities)
    for site in ("north-fields", "north-pasture", "farm-cistern", "supply-depot",
                 "communal-granary", "communal-kitchen", "farm-feed-store"):
        assert generated.entities[site].kind == "location"
    assert generated.entities["farm-emergency-cistern"].destination == "farm-cistern"
    assert set(generated.village.stocks["communal-granary"]) == {"grain", "flour", "hay"}
    assert set(generated.village.stocks["supply-depot"]) == {"timber", "firewood"}
    assert generated.village.economy.reserve_targets["watermill"]["flour"] == 8
    assert generated.village.economy.reserve_targets["farm"]["grain"] == 10
    assert generated.village.economy.reserve_targets["herbal-workshop"]["herbs"] == 8
    assert generated.village.economy.reserve_targets["riverbank"]["preserved-fish"] == 3
    assert generated.village.economy.reserve_targets["woods"] == {"logs": 10, "timber": 6}
    assert generated.village.economy.stock_capacities["general-store"]["bread"] == 8
    assert generated.village.economy.supplier_alternatives["bread"] == ["nella"]
    assert generated.village.economy.meal_plans[-1].recipe_portions == {
        "bread": 16}
    assert generated.village.economy.meal_plans[-1].additional_inputs == {"fish": 8}
    breakfast = next(plan for plan in generated.village.economy.meal_plans if plan.id == "breakfast")
    assert breakfast.additional_inputs == {"eggs": 16}
    assert breakfast.resident_portions == 12 and breakfast.visitor_portions == 4
    assert generated.village.economy.production_targets["farm"]["eggs"] == 16
    assert generated.village.stocks["tavern"]["eggs"] == 16
    assert generated.village.livestock.hens == 16
    assert generated.village.livestock.cows == 2
    assert generated.village.livestock.sheep == 6
    assert generated.village.livestock.pigs == 3
    assert {"farm-cow", "farm-cow-02", "farm-sheep-01", "farm-pig-01",
            "pig-pen", "sheep-pen"} <= set(generated.entities)
    assert generated.village.economy.stock_capacities["riverbank"]["preserved-fish"] == 6


def walk_to(world, destination):
    while world.actor.location_id != destination:
        wait = world.state.village.residents[world.actor_id].available_at - world.state.village.minute
        if wait > 0:
            village.advance(world, wait)
        origin = world.actor.location_id
        next_site = village.route(world, origin, destination)[0]
        door = next(key for key, entity in world.state.entities.items()
                    if entity.kind == "door" and {entity.location, entity.destination} == {origin, next_site}
                    and entity.open and not entity.locked and not entity.broken)
        applied(world, "go", target=door)
        village.advance(world, 5)


def test_feed_store_is_finite_and_restocked_by_physical_cargo(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    assert state.stocks["farm-feed-store"] == {"grain": 4, "hay": 3}
    assert state.stocks["farm"]["grain"] == 2
    assert state.depot_lots["farm-feed-store"]["ada"]["grain"][0].quantity == 4
    before = state.stocks["farm"]["hay"]
    applied(world, "village_store_feed", goods={"hay": 1})
    assert state.stocks["farm"]["hay"] == before - 1
    assert state.stocks["farm-feed-store"]["hay"] == 3
    world.actor.location_id = "farm-feed-store"
    village.advance(world, 5)
    applied(world, "village_unload_goods")
    assert state.stocks["farm-feed-store"]["hay"] == 4
    assert sum(lot.quantity for lot in state.depot_lots["farm-feed-store"]["ada"]["hay"]) == 4
    world.save()
    restored = World(world.scenario, world.path, "world", "ada", "session-ada")
    assert restored.state.village.depot_lots == state.depot_lots


def test_ada_plans_feed_from_personally_checked_store_and_unloads_before_care(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    ada = state.residents["ada"]
    state.stocks["farm-feed-store"]["hay"] = 0
    state.depot_lots["farm-feed-store"]["ada"]["hay"] = []
    state.stock_lots["farm-feed-store"]["hay"] = []
    assert state.stocks["farm"]["hay"] == 5
    assert [item for item in village_fallback.candidates(world) if item["task_id"].startswith("feed:")][0]["task_id"] == "feed:inspect"
    assert ada.fallback.feed_store_stock == {}
    walk_to(world, "farm-feed-store")
    assert ada.fallback.feed_store_stock["hay"] == 0
    assert village_fallback.candidates(world)[0]["task_id"] == "feed:collect"
    walk_to(world, "farm")
    planned = next(item for item in village_fallback.candidates(world) if item["task_id"] == "feed:load")
    assert planned["arguments"] == {"goods": {"hay": 1}}
    assert village_fallback.observation(world)["fallback"]["feed_store_plan"]["missing"]["hay"] == 1
    applied(world, planned["action_id"], **planned["arguments"])
    assert state.stocks["farm"]["hay"] == 4
    assert state.stocks["farm-feed-store"]["hay"] == 0
    walk_to(world, "farm-feed-store")
    assert village_fallback.candidates(world)[0]["task_id"] == "feed:unload"
    applied(world, "village_unload_goods")
    assert state.stocks["farm-feed-store"]["hay"] == 1
    assert village_fallback.observation(world)["fallback"]["feed_store_plan"]["missing"]["hay"] == 0
    village_depot.reconcile(world)
    world.save()
    restored = World(world.scenario, world.path, "world", "ada", "session-ada")
    assert restored.state.village.residents["ada"].fallback.feed_store_stock["hay"] == 1


def test_feed_planner_respects_farm_reserve_and_other_cargo(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    ada = state.residents["ada"]
    ada.fallback.feed_store_checked_day = state.minute // 1440
    ada.fallback.feed_store_stock = {"grain": 0, "hay": 0}
    reserve = village_economy.protected_quantity(world, "farm", "grain")
    state.stocks["farm"]["grain"] = reserve
    state.stock_lots["farm"]["grain"] = [state.stock_lots["farm"]["grain"][0].model_copy(update={"quantity": reserve})] if reserve else []
    choice = next(item for item in village_fallback.candidates(world) if item["task_id"] == "feed:load")
    assert "grain" not in choice["arguments"]["goods"]
    assert village_fallback.observation(world)["fallback"]["feed_store_plan"]["shortage"]["grain"] == 3
    applied(world, "village_store_feed", **choice["arguments"])
    assert not [item for item in village_fallback.candidates(world) if item["task_id"] == "feed:load"]


def test_feed_planner_reports_when_trip_cannot_finish_before_rest(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    ada = state.residents["ada"]
    ada.fallback.feed_store_checked_day = state.minute // 1440
    ada.fallback.feed_store_stock = {"grain": 0, "hay": 0}
    state.minute = world.scenario.village.residents["ada"].bed_minute - 15
    assert not [item for item in village_fallback.candidates(world) if item["task_id"] == "feed:load"]
    blocker = village_fallback.observation(world)["fallback"]["feed_store_plan"]["trip_blocker"]
    assert "Not enough time" in blocker


def test_depot_preserves_owner_freshness_and_requires_funded_withdrawal(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    world = world.bind("rowan", "session-rowan")
    world.actor.location_id = "woods"
    village_goods.put(world, "woods", "timber", 5)
    applied(world, "village_depot_load", goods={"timber": 1})
    world.actor.location_id = "supply-depot"
    village.advance(world, 5)
    applied(world, "village_unload_goods")
    assert state.stocks["supply-depot"]["timber"] == 1
    assert state.depot_lots["supply-depot"]["rowan"]["timber"][0].quantity == 1
    world = world.bind("hobb", "session-hobb")
    world.actor.location_id = "supply-depot"
    price = village_economy.unit_price(world, "timber")
    state.coins["hobb"] = price - 1
    assert act(world, "village_depot_withdraw", goods={"timber": 1})["code"] == "INSUFFICIENT_COINS"
    assert state.stocks["supply-depot"]["timber"] == 1
    state.coins["hobb"] = price
    applied(world, "village_depot_withdraw", goods={"timber": 1})
    assert state.stocks["supply-depot"]["timber"] == 0
    world.actor.location_id = "workshop"
    village.advance(world, 5)
    applied(world, "village_unload_goods")
    assert state.coins["rowan"] >= price
    assert state.coins["hobb"] == 0
    assert state.stocks["workshop"]["timber"] >= 1
    assert not village_economy.reconcile_inventory(world)


def test_depot_planning_uses_real_surplus_and_affordable_workplace_need(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    rowan = world.bind("rowan", "session-rowan")
    rowan.actor.location_id = "woods"
    state.economy.config.reserve_targets.setdefault("woods", {})["timber"] = 2
    state.economy.config.production_targets.setdefault("woods", {})["timber"] = 0
    village_goods.put(rowan, "woods", "timber", 5)
    deposit = village_fallback._depot_choice(rowan)
    assert deposit["task_id"] == "depot:deposit:timber"
    assert deposit["arguments"] == {"goods": {"timber": 3}}

    hobb = world.bind("hobb", "session-hobb")
    hobb.actor.location_id = "supply-depot"
    state.economy.config.production_targets.setdefault("workshop", {})["timber"] = 2
    state.stocks["workshop"]["timber"] = 0
    state.stock_lots["workshop"]["timber"] = []
    state.stocks["supply-depot"]["timber"] = 1
    state.stock_lots["supply-depot"]["timber"] = [state.stock_lots["woods"]["timber"][0].model_copy(
        update={"quantity": 1})]
    state.depot_lots.setdefault("supply-depot", {}).setdefault("rowan", {})["timber"] = [
        state.stock_lots["supply-depot"]["timber"][0].model_copy(deep=True)]
    state.coins["hobb"] = village_economy.unit_price(hobb, "timber")
    withdrawal = village_fallback._depot_choice(hobb)
    assert withdrawal["task_id"] == "depot:withdraw:timber"
    assert withdrawal["arguments"] == {"goods": {"timber": 1}}


def test_instant_granary_transfers_real_grain_without_a_feed_store(tmp_path):
    scenario = Scenario.model_validate(json.loads(
        (ROOT / "data" / "scenarios" / "millhaven-instant.json").read_text(encoding="utf-8")))
    scenario.village.start_minute = 600
    scenario.village.breakfast = False
    for agent in scenario.agents:
        agent.start_location = scenario.village.residents[agent.id].work
    farm = World(scenario, tmp_path / "instant.json", "instant", "ada", "session-ada")
    mill = farm.bind("mara", "session-mara")
    state = farm.state.village
    depot = village_depot.DEPOT
    def walk(resident, destination):
        person = state.residents[resident.actor_id]
        if wait := max(0, person.available_at - state.minute):
            village.advance(resident, wait)
        applied(resident, "village_plan_walk", destination=destination, purpose="Haul grain")
        while plan := person.walking_plan:
            if wait := max(0, person.available_at - state.minute):
                village.advance(resident, wait)
            applied(resident, "go", target=plan.doors[plan.next_step])
        if wait := max(0, person.available_at - state.minute):
            village.advance(resident, wait)
        assert resident.actor.location_id == destination

    assert not any(state.stocks[depot].values())
    assert "village_store_feed" not in {action["id"] for action in farm.actions()}
    village_goods.put(farm, "farm", "grain", 5)
    applied(farm, "village_depot_load", goods={"grain": 1})
    assert state.stocks[depot]["grain"] == 0
    walk(farm, depot)
    applied(farm, "village_unload_goods")
    assert state.depot_lots[depot]["ada"]["grain"][0].quantity == 1
    walk(mill, depot)
    price = village_economy.unit_price(mill, "grain")
    ada_balance = state.coins["ada"]
    mill_grain = state.stocks["watermill"]["grain"]
    applied(mill, "village_depot_withdraw", goods={"grain": 1})
    walk(mill, "watermill")
    applied(mill, "village_unload_goods")
    assert state.coins["ada"] == ada_balance + price
    assert state.stocks["watermill"]["grain"] == mill_grain + 1
    mill.save()
    restored = World(scenario, mill.path, "instant", "mara", "session-mara")
    assert restored.state.village.stocks[depot]["grain"] == 0
    village_depot.reconcile(restored)


def test_instant_depot_firewood_withdrawal_can_supply_shared_cottage_hearths(tmp_path):
    scenario = Scenario.model_validate(json.loads(
        (ROOT / "data" / "scenarios" / "millhaven-instant.json").read_text(encoding="utf-8")))
    scenario.village.start_minute = 600
    scenario.village.breakfast = False
    for agent in scenario.agents:
        agent.start_location = scenario.village.residents[agent.id].work
    rowan = World(scenario, tmp_path / "fuel.json", "instant", "rowan", "session-rowan")
    iona = rowan.bind("iona", "session-iona")
    state = rowan.state.village
    depot = village_depot.DEPOT
    village_goods.take(rowan, "cottages", "firewood", 1)
    village_goods.put(rowan, "woods", "firewood", 10)
    applied(rowan, "village_depot_load", goods={"firewood": 1})
    rowan.actor.location_id = depot
    village.advance(rowan, 5)
    applied(rowan, "village_unload_goods")
    iona.actor.location_id = depot
    applied(iona, "village_depot_withdraw", goods={"firewood": 1})
    iona.actor.location_id = "cottages"
    village.advance(iona, 5)
    opening_fuel = state.stocks["cottages"]["firewood"]
    applied(iona, "village_unload_goods")
    assert state.stocks["cottages"]["firewood"] == opening_fuel + 1
    assert state.stocks[depot]["firewood"] == 0


def test_livestock_consumes_shed_feed_not_farm_feed(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    applied(world, "take", target="livestock-bucket")
    world.actor.location_id = world.scenario.village.well_site
    state.well_dug = True
    village.advance(world, 5)
    applied(world, "village_draw_livestock_water")
    village.advance(world, 5)
    world.actor.location_id = "farm"
    farm_hay, feed_hay = state.stocks["farm"]["hay"], state.stocks["farm-feed-store"]["hay"]
    applied(world, "village_care_cow")
    assert state.stocks["farm"]["hay"] == farm_hay
    assert state.stocks["farm-feed-store"]["hay"] == feed_hay - 1
    village_depot.reconcile(world)
    village.advance(world, 15)
    farm_grain, feed_grain = state.stocks["farm"]["grain"], state.stocks["farm-feed-store"]["grain"]
    applied(world, "village_care_hens")
    assert state.stocks["farm"]["grain"] == farm_grain
    assert state.stocks["farm-feed-store"]["grain"] == feed_grain - 3
    village_depot.reconcile(world)


def test_depot_spoilage_and_return_keep_owner_ledger_in_sync(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    world = world.bind("rowan", "session-rowan")
    world.actor.location_id = "woods"
    village_goods.put(world, "woods", "timber", 5)
    applied(world, "village_depot_load", goods={"timber": 1})
    village.advance(world, 5)
    world.actor.location_id = "supply-depot"
    applied(world, "village_unload_goods")
    village.advance(world, 5)
    applied(world, "village_depot_withdraw", goods={"timber": 1})
    village.advance(world, 5)
    applied(world, "village_return_cargo", wagon=False)
    village_depot.reconcile(world)
    assert state.stocks["supply-depot"]["timber"] == 1
    state.stock_lots["supply-depot"]["timber"][0].expires_at = state.minute + 5
    state.depot_lots["supply-depot"]["rowan"]["timber"][0].expires_at = state.minute + 5
    village.advance(world, 5)
    assert state.stocks["supply-depot"]["timber"] == 0
    village_depot.reconcile(world)


def test_feed_store_return_and_local_owner_visibility(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    applied(world, "village_store_feed", goods={"hay": 1})
    assert state.stocks["farm"]["hay"] == 4
    village.advance(world, 5)
    applied(world, "village_return_cargo", wagon=False)
    assert state.stocks["farm"]["hay"] == 5
    assert state.stocks["farm-feed-store"]["hay"] == 3
    assert not state.residents["ada"].shipments
    world.actor.location_id = "farm-feed-store"
    assert village_ai.observation(world)["village"]["stored_owners"]["ada"]["hay"] == 3
    world.actor.location_id = "farm"
    assert "stored_owners" not in village_ai.observation(world)["village"]


def test_inbound_depot_fuel_reserves_capacity(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    state.stocks["supply-depot"]["firewood"] = 14
    state.stock_lots["supply-depot"]["firewood"] = [state.stock_lots["farm"]["hay"][0].model_copy(
        update={"quantity": 14})]
    state.depot_lots.setdefault("supply-depot", {}).setdefault("rowan", {})["firewood"] = [
        state.stock_lots["supply-depot"]["firewood"][0].model_copy(deep=True)]
    world = world.bind("rowan", "session-rowan")
    world.actor.location_id = "woods"
    village_goods.put(world, "woods", "firewood", 3)
    applied(world, "village_depot_load", goods={"firewood": 2})
    world = world.bind("iona", "session-iona")
    world.actor.location_id = "cottages"
    assert act(world, "village_depot_load", goods={"firewood": 1})["code"] == "FIREWOOD_STORAGE_FULL"
    assert state.stocks["cottages"]["firewood"] >= 1


def test_withdrawal_preserves_owners_distinct_freshness(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    for owner, source, expires in (("rowan", "woods", 900), ("hobb", "workshop", 800)):
        world = world.bind(owner, f"session-{owner}")
        world.actor.location_id = source
        village_goods.put(world, source, "timber", 5)
        state.stock_lots[source]["timber"][-1].expires_at = expires
        applied(world, "village_depot_load", goods={"timber": 1})
        world.actor.location_id = "supply-depot"
        village.advance(world, 5)
        applied(world, "village_unload_goods")
        village.advance(world, 5)
    world = world.bind("ada", "session-ada")
    world.actor.location_id = "supply-depot"
    applied(world, "village_depot_withdraw", goods={"timber": 1})
    shipment = state.residents["ada"].shipments[0]
    assert shipment.seller == "hobb"
    assert shipment.lots[0].expires_at == 800
    assert state.stock_lots["supply-depot"]["timber"][0].expires_at == 900
    village_depot.reconcile(world)

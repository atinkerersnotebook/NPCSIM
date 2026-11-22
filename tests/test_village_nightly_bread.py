"""Focused nighttime bread protection for actual tavern meals."""

from pathlib import Path

from npc_sim import (village, village_ai, village_communication, village_economy,
                     village_goods, village_life, village_services)
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
    state.residents["elsie"].life.last_woke = 330
    state.residents["elsie"].fallback.daily_plans.append({"day": 0})
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


def test_due_supper_can_cook_delivered_fish_with_breakfast_bread_then_replenishes(tmp_path):
    world = world_for_meal(tmp_path)
    state = world.state.village
    state.minute = 1080
    state.residents["elsie"].life.last_woke = 330
    state.residents["elsie"].fallback.daily_plans.append({"day": 0})
    required, _ = village_life.overnight_breakfast_bread_reserve(world)
    village_goods.put(world, "tavern", "bread", required)
    village_goods.put(world, "tavern", "fish", 8)
    village_goods.put(world, "tavern", "firewood", 8)

    assert village_economy.production_batch_limit(world, "village_cook_fish") > 0
    assert village_economy.production_blocker(world, "village_cook_fish") is None
    assert village_ai.production_ready(world, "village_cook_fish") is None
    result = act(world, "village_cook_fish")
    assert result["outcome"] == "APPLIED", result
    assert state.stocks["tavern"]["fish"] < 8
    assert state.stocks["tavern"]["meals"] > 0


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


def test_two_consecutive_days_of_paid_meals_with_real_cooking(tmp_path):
    world = world_for_meal(tmp_path)
    state = world.state.village
    state.minute = 300
    village_goods.put(world, "tavern", "bread", 20)
    # Provision two additional real flour at opening; the bakery's six flour
    # otherwise leave only four above its protected two-flour reserve.
    village_goods.put(world, "bakery", "flour", 2)
    opening_coins = dict(state.coins)
    for day in (0, 1):
        start = day * 1440
        for rid, person in state.residents.items():
            if day == 0:
                person.life.sleeping = False
                person.life.last_woke = world.scenario.village.residents[rid].wake_minute
            world.bind(rid, f"session-{rid}").actor.location_id = "tavern"
        for minute in (330, 335, 340):
            village.advance(world, start + minute - state.minute)
            result = act(world, "village_work")
            assert result["code"] == "WORK_COMPLETED", (day, minute, result)
        for minute in (360, 390, 450):
            village.advance(world, start + minute - state.minute)
            for rid, resident in world.scenario.village.residents.items():
                if resident.wake_minute + world.scenario.village.breakfast_delay == minute:
                    if day:
                        assert not state.residents[rid].life.sleeping
                        assert state.residents[rid].life.last_woke == start + resident.wake_minute
                    result = village_life.apply(world.bind(rid, f"session-{rid}"), "village_eat")
                    assert result[1] == "MEAL_EATEN", (day, rid, result)
        if day == 1:
            village.advance(world, start + 480 - state.minute)
            jory = world.bind("jory", "session-jory")
            world.actor.location_id = jory.actor.location_id = "village-square"
            result = village_communication.apply(world, "village_notice_post", {
                "channel": "conversation", "listener": "jory",
                "notice": {"type": "supply_request", "subject": "bread",
                           "description": "Six fresh bread for supper and the following breakfast",
                           "recipients": ["jory"], "quantity": 6, "destination": "tavern",
                           "due_at": start + 1020, "required_fresh_until": start + 1890}})
            assert result[:2] == ("APPLIED", "NOTICE_POSTED"), result
            bread_notice = next(reversed(state.communication.notices))
            village.advance(world, 5)
            result = village_communication.apply(jory, "village_respond_to_request", {
                "request_id": bread_notice, "response": "accept"})
            assert result[0] == "APPLIED", result
            bread_order = next(reversed(state.economy.orders.values()))
            jory.actor.location_id = "bakery"
            for minute in (485, 545, 605):
                village.advance(world, start + minute - state.minute)
                result = village_ai.apply(jory, "village_work", {})
                assert result[0] == "APPLIED", (minute, result)
            village.advance(world, start + 665 - state.minute)
            assert state.stocks["bakery"]["bread"] >= 6, (
                state.stocks["bakery"], bread_order.status, bread_order.production_plan)
            assert village_economy.loadable_for_order(world, bread_order) >= 3, (
                bread_order.status, bread_order.production_plan, state.stock_lots["bakery"]["bread"])
            for quantity in (3, 3):
                assert village_services.foot_load(jory, "jory", {"bread": quantity}, "tavern") is None
                jory.actor.location_id = "tavern"
                assert village_services.foot_unload(jory, "jory") is None
                jory.actor.location_id = "bakery"
            assert bread_order.status == "fulfilled" and bread_order.paid > 0
            assert bread_order.delivered_quantity == 6
            assert state.stocks["tavern"]["bread"] == 10
        village.advance(world, start + 850 - state.minute)
        finn = world.bind("finn", "session-finn")
        world.actor.location_id = finn.actor.location_id = "village-square"
        result = village_communication.apply(world, "village_notice_post", {
            "channel": "conversation", "listener": "finn",
            "notice": {"type": "supply_request", "subject": "fish",
                       "description": "Two fresh fish for supper", "recipients": ["finn"],
                       "quantity": 2, "destination": "tavern",
                       "due_at": start + 1050, "required_fresh_until": start + 1140}})
        assert result[:2] == ("APPLIED", "NOTICE_POSTED"), (day, result)
        fish_notice = next(reversed(state.communication.notices))
        village.advance(world, 5)
        result = village_communication.apply(finn, "village_respond_to_request", {
            "request_id": fish_notice, "response": "accept"})
        assert result[0] == "APPLIED", (day, result)
        fish_order = next(reversed(state.economy.orders.values()))
        finn.actor.location_id = "riverbank"
        for minute in (855, 915):
            village.advance(world, start + minute - state.minute)
            result = village_ai.apply(finn, "village_work", {})
            assert result[0] == "APPLIED", (day, minute, result)
        assert village_services.foot_load(finn, "finn", {"fish": 2}, "tavern") is None
        finn.actor.location_id = "tavern"
        assert village_services.foot_unload(finn, "finn") is None
        assert fish_order.status == "fulfilled" and fish_order.paid > 0
        assert fish_order.delivered_quantity == 2
        assert state.stocks["tavern"]["fish"] == 2
        if day == 0:
            site = world.scenario.village.mill
            hobb = world.bind("hobb", "session-hobb")
            mara = world.bind("mara", "session-mara")
            hobb.actor.location_id = "workshop"
            result = village_ai.apply(hobb, "village_work", {})
            assert result[0] == "APPLIED", result
            village_communication.authorize_repair_transfer(hobb, "repair_mill")
            assert village_services.foot_load(hobb, "hobb", {"planks": 1}, site) is None
            hobb.actor.location_id = site
            assert village_services.foot_unload(hobb, "hobb") is None
            assert state.stocks[site]["planks"] == 1
            for eid in (world.scenario.village.repair_part, world.scenario.village.hammer):
                world.state.entities[eid].location = site
                world.state.entities[eid].hidden = False
            hobb.actor.location_id = mara.actor.location_id = "village-square"
            result = village_communication.apply(hobb, "village_schedule_crew", {
                "project": "repair_mill", "earliest_start": start + 950,
                "participant_ids": ["hobb", "mara"]})
            assert result[:2] == ("APPLIED", "NOTICE_POSTED"), result
            notice_id = next(reversed(state.communication.notices))
            village.advance(world, start + 920 - state.minute)
            result = village_communication.apply(mara, "village_respond_to_request", {
                "request_id": notice_id, "response": "accept"})
            assert result[0] == "APPLIED", result
            village.advance(world, start + 950 - state.minute)
            hobb.actor.location_id = mara.actor.location_id = site
            assert village_communication.project_blocker(hobb, "repair_mill") is None
            for member in (hobb, mara) * 3:
                result = village_ai.apply(member, "village_project", {"project": "repair_mill"})
                assert result[0] == "APPLIED", result
                village_ai.after_action(member, "village_project", {"project": "repair_mill"})
                village.advance(world, 5)
            assert state.repaired
            assert state.communication.notices[notice_id][-1].status == "completed"
            assert state.crew_sessions["repair_mill"].status == "completed"
            assert state.crew_sessions["repair_mill"].contributions == {"hobb": 3, "mara": 3}
        world.actor.location_id = "tavern"
        for minute, action in ((1080, "village_work"), (1085, "village_work"),
                               (1090, "village_cook_fish")):
            village.advance(world, start + minute - state.minute)
            result = act(world, action)
            assert result["outcome"] == "APPLIED", (day, minute, result)
        village.advance(world, start + 1140 - state.minute)
        meal_payments = len(state.economy.payments)
        for rid in state.residents:
            if rid != "elsie":
                diner = world.bind(rid, f"session-{rid}")
                diner.actor.location_id = "tavern"
                result = village_life.apply(diner, "village_eat")
                assert result[1] == "MEAL_EATEN", (day, rid, result)
        village.advance(world, start + 1170 - state.minute)
        assert village_life.apply(world, "village_eat")[1] == "MEAL_EATEN"
        for person in state.residents.values():
            assert person.life.meals[f"{day}:breakfast"].status == "eaten"
            assert person.life.meals[f"{day}:daily"].status == "eaten"
        assert sum(payment.kind == "meal" and start <= payment.minute < start + 1440
                   for payment in state.economy.payments) == 22
        assert all(person.life.meals[f"{day}:{kind}"].source == "tavern:meals"
                   for person in state.residents.values() for kind in ("breakfast", "daily"))
        assert state.stocks["tavern"]["meals"] == 0
        assert state.stocks["tavern"]["bread"] == 10 - 4 * day
        assert len(state.economy.payments) >= meal_payments + 11
        if day == 0:
            for rid, resident in sorted(world.scenario.village.residents.items(),
                                        key=lambda item: item[1].bed_minute):
                world.bind(rid, f"session-{rid}").actor.location_id = resident.home
                village.advance(world, max(state.minute, resident.bed_minute) - state.minute)
                result = village_life.apply(world.bind(rid, f"session-{rid}"), "village_sleep")
                assert result[1] == "SLEEP_STARTED", (rid, result)
            world.save()
            world = World(world.scenario, world.path, "nightly-bread", "elsie", "session-elsie")
            state = world.state.village
            village.advance(world, 1740 - state.minute)
            assert all(person.life.fatigue_days == [] for person in state.residents.values())
    assert state.stocks["tavern"]["fish"] == 0
    assert sum(state.coins.values()) == sum(opening_coins.values())


def test_accelerated_grain_flour_bread_chain_without_intermediate_grants(tmp_path):
    world = world_for_meal(tmp_path)
    state = world.state.village
    state.minute = 480
    for person in state.residents.values():
        person.life.sleeping = False
    opening_stock = {site: state.stocks[site].copy() for site in ("farm", "watermill", "bakery", "tavern")}
    opening_coins = sum(state.coins.values())

    def at(rid, location):
        bound = world.bind(rid, f"session-{rid}")
        bound.actor.location_id = location
        return bound

    def request(buyer, supplier, product, quantity, deadline, fresh):
        site = world.scenario.village.residents[supplier].work
        recipient = at(supplier, site)
        sender = at(buyer, site)
        result = village_communication.apply(sender, "village_notice_post", {
            "channel": "conversation", "listener": supplier,
            "notice": {"type": "supply_request", "subject": product,
                       "description": f"{quantity} {product} for production",
                       "recipients": [supplier], "quantity": quantity,
                       "destination": world.scenario.village.residents[buyer].work,
                       "due_at": deadline, "required_fresh_until": fresh}})
        assert result[:2] == ("APPLIED", "NOTICE_POSTED"), result
        notice_id = next(reversed(state.communication.notices))
        village.advance(world, 5)
        result = village_communication.apply(recipient, "village_respond_to_request", {
            "request_id": notice_id, "response": "accept"})
        assert result[0] == "APPLIED", result
        return next(reversed(state.economy.orders.values()))

    grain = request("mara", "ada", "grain", 4, 750, 810)
    assert grain.status == "accepted_planned"
    farmer = at("ada", "farm")
    for minute in (490, 550):
        village.advance(world, minute - state.minute)
        result = village_ai.apply(farmer, "village_work", {})
        assert result[0] == "APPLIED", result
    village.advance(world, 555 - state.minute)
    for amount in (3, 1):
        assert village_services.foot_load(farmer, "ada", {"grain": amount}, "watermill") is None
        farmer.actor.location_id = "watermill"
        assert village_services.foot_unload(farmer, "ada") is None
        farmer.actor.location_id = "farm"
    assert grain.status == "fulfilled" and grain.delivered_quantity == 4

    flour = request("jory", "mara", "flour", 4, 790, 820)
    assert flour.status == "accepted_planned", flour.status
    miller = at("mara", "watermill")
    for minute in (570, 600):
        village.advance(world, minute - state.minute)
        result = village_ai.apply(miller, "village_work", {})
        assert result[0] == "APPLIED", (minute, result, flour.status,
                                         state.stocks["watermill"])
    village.advance(world, 605 - state.minute)
    for amount in (3, 1):
        assert village_services.foot_load(miller, "mara", {"flour": amount}, "bakery") is None
        miller.actor.location_id = "bakery"
        assert village_services.foot_unload(miller, "mara") is None
        miller.actor.location_id = "watermill"
    assert flour.status == "fulfilled" and flour.delivered_quantity == 4

    bread = request("elsie", "jory", "bread", 6, 1050, 1890)
    baker = at("jory", "bakery")
    village.advance(world, 680 - state.minute)
    result = village_ai.apply(baker, "village_work", {})
    assert result[0] == "APPLIED", (result, bread.status, state.stocks["bakery"])
    village.advance(world, 710 - state.minute)
    for amount in (3, 3):
        assert village_services.foot_load(baker, "jory", {"bread": amount}, "tavern") is None
        baker.actor.location_id = "tavern"
        assert village_services.foot_unload(baker, "jory") is None
        baker.actor.location_id = "bakery"
    assert bread.status == "fulfilled" and bread.delivered_quantity == 6
    assert state.stocks["tavern"]["bread"] == 6
    for site, stock in opening_stock.items():
        for product in ("grain", "flour", "bread"):
            movement = sum(entry.quantity if entry.kind in {"produced", "delivered"} else -entry.quantity
                           for entry in state.economy.movements
                           if entry.location == site and entry.product == product)
            assert state.stocks[site].get(product, 0) == stock.get(product, 0) + movement
    assert sum(state.coins.values()) == opening_coins

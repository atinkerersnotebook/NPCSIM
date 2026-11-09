from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from npc_sim import village_events
from npc_sim.models import Scenario, VillageStockLot
from npc_sim.storage import read_json
from npc_sim.village_events_models import (
    EventTemplate, MerchantProfile, VillageEventsConfig, VillageEventsState, VisitorPlan,
)
from npc_sim.world import World


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def world():
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-expanded.json"))
    scenario.village.daily_life = False
    scenario.village.start_minute = 600
    scenario.village.events = VillageEventsConfig(daily_max=0)
    for agent in scenario.agents:
        agent.start_location = scenario.village.residents[agent.id].work
    path = ROOT / f".events-test-{uuid4().hex}.json"
    value = World(scenario, path, "events-test", "torvin", "event-session")
    value.state.village.incidents = village_events.initial_state(scenario)
    try:
        yield value
    finally:
        path.unlink(missing_ok=True)


def configure(world, *templates, **settings):
    config = VillageEventsConfig(templates=list(templates), **settings)
    village_events.configure(world, config.model_dump())
    return world.state.village.incidents


def event(kind="equipment", **changes):
    data = {"id": "test", "kind": kind, "title": "Small test incident", "location": "forge",
            "residents": ["torvin"], "random": False, "scheduled_at": 600}
    if kind == "equipment":
        data["equipment"] = "smith-hammer"
    data.update(changes)
    return EventTemplate(**data)


def merchant(world, **changes):
    profile = MerchantProfile(stock={"iron": 2}, sell_prices={"iron": 3},
                              buy_prices={"wagon-parts": 2}, demand={"wagon-parts": 3},
                              funds=10, capacity=6)
    plan = VisitorPlan(name="Tamsin Reed", entry="village-square", destination="forge",
                       departure="village-square", stay_minutes=60, merchant=profile)
    template = event("merchant", visitor=plan, **changes)
    incidents = configure(world, template)
    before = {key: stock.copy() for key, stock in world.state.village.stocks.items()}
    village_events.advance(world)
    assert world.state.village.stocks == before
    visitor = next(iter(incidents.visitors.values()))
    assert visitor.status == "arriving"
    world.state.village.minute += 5
    village_events.advance(world)
    assert visitor.status == "present"
    return visitor


def stock(world, resource, amount, expires=None):
    state = world.state.village
    state.stocks.setdefault("forge", {})[resource] = amount
    state.stock_lots.setdefault("forge", {})[resource] = [
        VillageStockLot(quantity=amount, produced_at=600, expires_at=expires)] if amount else []


def trade(world, visitor, direction="export", resource="wagon-parts", quantity=1, transaction="test"):
    return village_events.apply(world, "village_merchant_trade", {
        "visitor": visitor.id, "direction": direction, "resource": resource,
        "quantity": quantity, "transaction": transaction})


@pytest.mark.parametrize("payload", [
    {"daily_max": 13}, {"daily_min": 3, "daily_max": 2}, {"enabled": "yes"}, {"code": "eval"},
])
def test_strict_config(payload):
    with pytest.raises(ValidationError):
        VillageEventsConfig.model_validate(payload)


@pytest.mark.parametrize("changes", [
    {"kind": "earthquake"}, {"outputs": {"iron": 100}, "source": "A mine"},
    {"duration": 1440}, {"window_end": 400}, {"responses": ["resolve"]},
    {"kind": "rain", "outputs": {"iron": 1}, "source": "Cloud"},
])
def test_unsupported_or_unbounded_effects(changes):
    with pytest.raises(ValidationError):
        event(**changes)


def test_unknown_references_rejected_without_changing_config(world):
    before = world.state.village.incidents.model_dump()
    for item in (event(location="imaginary"), event(residents=["nobody"]),
                 event(equipment="imaginary")):
        with pytest.raises(ValueError):
            configure(world, item)
    assert world.state.village.incidents.model_dump() == before


def test_legacy_opt_out_and_persisted_dm_config(world):
    world.scenario.village.events = None
    assert village_events.initial_state(world.scenario) is None
    config = configure(world, event(), seed=42)
    assert world.scenario.village.events is None
    restored = VillageEventsState.model_validate_json(config.model_dump_json())
    assert restored.config.seed == 42
    assert restored.configuration_history[-1]["revision"] == restored.revision


def test_delay_expires_without_permanent_breakage_and_no_duplicate_activation(world):
    incidents = configure(world, event(duration=15))
    original = world.state.entities["smith-hammer"].broken
    village_events.advance(world)
    village_events.advance(world)
    assert len(incidents.instances) == 1
    assert village_events.delay_until(world, "torvin", "forge", "smith-hammer") == 615
    assert world.state.entities["smith-hammer"].broken == original
    world.state.village.minute = 620
    village_events.advance(world)
    assert village_events.delay_until(world, "torvin", "forge", "smith-hammer") == 0
    assert next(iter(incidents.instances.values())).status == "resolved"
    assert world.state.entities["smith-hammer"].broken == original


def test_task_real_inputs_outputs_once_and_recovery(world):
    template = event("neighbour_help", inputs={"iron": 1}, outputs={"wagon-parts": 1},
                     source="One iron forged into a neighbour's spare component", intensity=2)
    incidents = configure(world, template)
    stock(world, "iron", 2)
    stock(world, "wagon-parts", 0)
    village_events.advance(world)
    iid = next(iter(incidents.instances))
    first = village_events.apply(world, "village_event_work", {"incident": iid})
    assert first[0] == "APPLIED"
    assert world.state.village.stocks["forge"]["iron"] == 2
    world.state.village.incidents = VillageEventsState.model_validate_json(incidents.model_dump_json())
    assert village_events.apply(world, "village_event_work", {"incident": iid})[0] == "REJECTED"
    world.state.village.minute += 5
    assert village_events.apply(world, "village_event_work", {"incident": iid})[0] == "APPLIED"
    assert world.state.village.stocks["forge"]["iron"] == 1
    assert world.state.village.stocks["forge"]["wagon-parts"] == 1
    assert village_events.apply(world, "village_event_work", {"incident": iid})[0] == "REJECTED"


def test_discovery_is_real_local_and_not_world_announced(world):
    template = event("discovery", discovery="smith-hammer", responses=["discover", "decline"])
    incidents = configure(world, template)
    village_events.advance(world)
    iid = next(iter(incidents.instances))
    event_index = incidents.instances[iid].evidence[0]
    assert "mara" not in world.state.village.events[event_index].witnesses
    assert village_events.apply(world, "village_event_discover", {"incident": iid})[0] == "APPLIED"
    assert world.state.village.residents["torvin"].beliefs["smith-hammer"].location == "forge"


def test_merchant_conservation_retry_capacity_budget_and_departure(world):
    visitor = merchant(world)
    stock(world, "wagon-parts", 5)
    before_money = sum(world.state.village.coins.values()) + visitor.coins
    before_parts = world.state.village.stocks["forge"]["wagon-parts"] + visitor.stock.get("wagon-parts", 0)
    assert trade(world, visitor, quantity=3)[0] == "APPLIED"
    assert sum(world.state.village.coins.values()) + visitor.coins == before_money
    assert world.state.village.stocks["forge"]["wagon-parts"] + visitor.stock["wagon-parts"] == before_parts
    snapshot = (visitor.model_dump(), world.state.village.stocks["forge"].copy(),
                world.state.village.coins.copy())
    assert trade(world, visitor, quantity=3)[1] == "TRADE_ALREADY_APPLIED"
    assert snapshot == (visitor.model_dump(), world.state.village.stocks["forge"].copy(),
                        world.state.village.coins.copy())
    assert trade(world, visitor, quantity=1, transaction="extra")[0] == "REJECTED"
    assert trade(world, visitor, direction="import", resource="iron", quantity=2, transaction="import")[0] == "APPLIED"
    assert visitor.stock["iron"] == 0
    assert sum(world.state.village.coins.values()) + visitor.coins == before_money
    world.state.village.minute = visitor.leave_at + 10
    village_events.advance(world)
    assert visitor.status == "departed"
    assert visitor.departed_at >= visitor.leave_at
    assert visitor.id in world.state.village.incidents.visitors
    assert visitor.unsatisfied == {}
    assert all(interval.start <= interval.end for interval in visitor.timeline)
    assert trade(world, visitor, transaction="too-late")[0] == "REJECTED"


def test_deadline_unmet_freshness_and_invalid_trade_no_changes(world):
    visitor = merchant(world)
    stock(world, "wagon-parts", 5, expires=visitor.leave_at + 5)
    before = world.state.village.model_dump()
    assert trade(world, visitor)[0] == "REJECTED"
    assert world.state.village.model_dump() == before
    for quantity in (-1, 0, True, 7):
        assert trade(world, visitor, quantity=quantity)[0] == "REJECTED"
    world.state.village.minute = visitor.leave_at + 10
    village_events.advance(world)
    assert visitor.unsatisfied == {"wagon-parts": 3}


def test_accepted_demand_protected_from_exports(world):
    visitor = merchant(world)
    stock(world, "wagon-parts", 3)
    template = event("traveller", id="order", demand={"wagon-parts": 2},
                     visitor=VisitorPlan(name="Iris", entry="forge", destination="forge", departure="forge"),
                     responses=["deliver", "decline"], scheduled_at=605)
    incidents = world.state.village.incidents
    # Already accepted, persisted demand is a real reservation even before supply.
    from npc_sim.village_events_models import IncidentState
    incidents.instances["order:605"] = IncidentState(id="order:605", template=template,
        activated_at=605, expires_at=650, accepted_by="torvin")
    assert trade(world, visitor, quantity=2)[0] == "REJECTED"
    assert trade(world, visitor, quantity=1)[0] == "APPLIED"


def test_random_daily_selection_reproducible_and_feasibility(world):
    templates = [event(id=f"rain-{index}", kind="rain", equipment=None,
                       random=True, scheduled_at=None) for index in range(3)]
    configure(world, *templates, seed=31, daily_min=2, daily_max=2)
    world.state.village.minute = 1070
    village_events.advance(world)
    assert not world.state.village.incidents.instances
    world.state.village.minute = 700
    saved = world.state.village.incidents.model_dump_json()
    for minute in range(700, 1000, 5):
        world.state.village.minute = minute
        village_events.advance(world)
    first = list(world.state.village.incidents.instances)
    world.state.village.incidents = VillageEventsState.model_validate_json(saved)
    for minute in range(700, 1000, 5):
        world.state.village.minute = minute
        village_events.advance(world)
    assert list(world.state.village.incidents.instances) == first
    assert len(first) <= 2


def test_catalogue_and_monitor_schema_keep_departed_history(world):
    config = village_events.recommended_config(world.scenario, initial_example=True)
    assert config["daily_min"] == 0 and config["daily_max"] == 2
    assert len(config["templates"]) == 12
    assert {item["kind"] for item in config["templates"]} == {
        "traveller", "merchant", "rain", "equipment", "discovery", "escaped_animal",
        "poor_fishing", "harvest", "misplaced_tool", "birthday", "band_absence", "neighbour_help"}
    visitor = merchant(world)
    world.state.village.minute = visitor.leave_at + 10
    village_events.advance(world)
    snapshot = village_events.monitor(world)
    person = snapshot["visitors"][0]
    assert person["status"] == "departed"
    assert type(person["arrival"]) is int and type(person["departure"]) is int
    assert set(person) == {"id", "name", "location", "arrival", "departure", "status", "activities"}
    assert set(person["activities"][0]) == {"actor", "label", "location", "start", "end", "status"}
    assert set(snapshot["incidents"][0]) == {"id", "title", "location", "start", "end", "status"}


def test_event_waiters_wake_but_production_and_routes_do_not(world):
    person = world.state.village.residents["torvin"]
    person.activity = "wait_event"
    person.available_at = 900
    configure(world, event())
    village_events.advance(world)
    assert person.available_at == 600
    world.state.village.incidents.instances.clear()
    world.state.village.incidents.scheduled_fired.clear()
    world.state.village.incidents.last_activation.clear()
    world.state.village.incidents.daily_counts.clear()
    person.available_at = 900
    person.timber_route = True
    village_events.advance(world)
    assert person.available_at == 900


def test_export_selects_only_lots_fresh_beyond_journey(world):
    visitor = merchant(world)
    stock(world, "wagon-parts", 4)
    world.state.village.stock_lots["forge"]["wagon-parts"] = [
        VillageStockLot(quantity=2, produced_at=590, expires_at=610),
        VillageStockLot(quantity=2, produced_at=600, expires_at=2000)]
    assert trade(world, visitor, quantity=2)[0] == "APPLIED"
    assert world.state.village.stock_lots["forge"]["wagon-parts"][0].expires_at == 610
    assert visitor.stock_lots["wagon-parts"][0].expires_at == 2000
    assert sum(lot.quantity for lot in world.state.village.stock_lots["forge"]["wagon-parts"]) == 2


def test_condition_and_decline_are_concrete_and_persistent(world):
    from npc_sim.village_events_models import EventCondition
    template = event("harvest", location="farm", residents=["ada"], random=False, scheduled_at=None,
                     condition=EventCondition(location="farm", resource="grain", below=100),
                     outputs={"grain": 2}, source="Seasonal field patch")
    incidents = configure(world, template)
    village_events.advance(world)
    iid = next(iter(incidents.instances))
    farmer = world.bind("ada", "events-farmer")
    before = world.state.village.stocks["farm"].copy()
    result = village_events.apply(farmer, "village_event_decline", {"incident": iid})
    assert result[0] == "APPLIED", result
    assert world.state.village.stocks["farm"] == before
    world.state.village.incidents = VillageEventsState.model_validate_json(incidents.model_dump_json())
    village_events.advance(world)
    assert len(world.state.village.incidents.instances) == 1


def test_overnight_visitor_meals_and_lodging_are_explicit_and_once(world):
    plan = VisitorPlan(name="Overnight Iris", entry="village-square", destination="tavern",
                       departure="village-square", stay_minutes=1440, funds=20,
                       meal_location="tavern", lodging_location="tavern", lodging_price=3)
    template = event("traveller", location="tavern", residents=["elsie"],
                     visitor=plan, responses=["decline"])
    incidents = configure(world, template)
    state = world.state.village
    state.stocks["tavern"]["meals"] = 2
    state.stock_lots["tavern"]["meals"] = [VillageStockLot(quantity=2, produced_at=600, expires_at=2500)]
    village_events.advance(world)
    visitor = next(iter(incidents.visitors.values()))
    state.minute = 620
    village_events.advance(world)
    assert state.stocks["tavern"]["meals"] == 2
    assert visitor.meals == [] and visitor.lodging_days == []
    host = world.bind("elsie", "events-host")
    state.minute = 1150
    village_events.advance(world)
    assert village_events.apply(host, "village_visitor_meal", {"visitor": visitor.id})[0] == "APPLIED"
    assert state.stocks["tavern"]["meals"] == 1
    after = state.coins.copy(), visitor.coins
    assert village_events.apply(host, "village_visitor_meal", {"visitor": visitor.id})[0] == "REJECTED"
    assert after == (state.coins.copy(), visitor.coins)
    state.minute = 1260
    village_events.advance(world)
    assert village_events.apply(host, "village_visitor_lodging", {"visitor": visitor.id})[0] == "APPLIED"
    after = state.coins.copy(), visitor.coins
    assert village_events.apply(host, "village_visitor_lodging", {"visitor": visitor.id})[0] == "REJECTED"
    assert after == (state.coins.copy(), visitor.coins)
    state.minute = 1500
    village_events.advance(world)
    assert visitor.status == "present"
    assert visitor.location == "tavern"
    assert any(interval.label == "Lodging" and interval.end >= 1440 for interval in visitor.timeline)


def test_real_reserve_targets_protect_merchant_surplus_and_reports(world):
    from npc_sim.village_economy_models import VillageEconomyConfig, VillageEconomyState
    visitor = merchant(world)
    stock(world, "wagon-parts", 3)
    state = world.state.village
    state.economy = VillageEconomyState(config=VillageEconomyConfig(
        reserve_targets={"forge": {"wagon-parts": 2}}), initial_coins=state.coins.copy())
    assert trade(world, visitor, quantity=2)[0] == "REJECTED"
    assert trade(world, visitor, quantity=1)[0] == "APPLIED"
    assert state.economy.payments[-1].payer == visitor.id
    assert state.economy.payments[-1].payee == "torvin"
    assert state.economy.movements[-1].kind == "exported"
    snapshot = state.economy.model_dump()
    assert trade(world, visitor, quantity=1)[1] == "TRADE_ALREADY_APPLIED"
    assert state.economy.model_dump() == snapshot


def test_equipment_blocker_matches_actual_equipment_and_allows_repair(world):
    configure(world, event(equipment="anvil", duration=15))
    village_events.advance(world)
    assert village_events.action_blocker(world, "village_work")[0] == "INCIDENT_DELAY"
    assert village_events.delay_until(world, "torvin", "forge", "anvil", kinds={"equipment"}) == 615
    assert village_events.delay_until(world, "torvin", "forge", "smith-hammer", kinds={"equipment"}) == 0
    for action in ("go", "look", "village_eat", "village_sleep", "village_event_work",
                   "village_feed_horse", "village_hitch", "village_unhitch", "village_lead_horse"):
        assert village_events.action_blocker(world, action) is None
    world.state.village.minute = 615
    # Even before lifecycle housekeeping, an expired delay cannot trap an actor.
    assert village_events.action_blocker(world, "village_work") is None


def test_rain_delays_optional_outdoor_activities_not_essential_travel(world):
    configure(world, event("rain", location="farm", residents=["ada"], duration=15))
    village_events.advance(world)
    farmer = world.bind("ada", "event-rain")
    assert village_events.action_blocker(farmer, "village_work")[0] == "INCIDENT_DELAY"
    assert village_events.action_blocker(farmer, "village_leisure")[0] == "INCIDENT_DELAY"
    assert village_events.action_blocker(world, "village_work") is None
    for action in ("go", "village_eat", "village_sleep", "village_feed_horse",
                   "village_lead_horse", "village_release_horse", "village_wait_event"):
        assert village_events.action_blocker(farmer, action) is None
    world.state.village.minute = 615
    assert village_events.action_blocker(farmer, "village_work") is None


def test_band_absence_and_fishing_delay_only_the_relevant_activity(world):
    configure(world, event("band_absence", location="riverbank", residents=["finn"]))
    village_events.advance(world)
    fisher = world.bind("finn", "events-fisher")
    assert village_events.action_blocker(fisher, "village_band_attend")[0] == "INCIDENT_DELAY"
    assert village_events.action_blocker(fisher, "village_work") is None
    assert village_events.action_blocker(fisher, "village_leisure") is None
    world.state.village.incidents.instances.clear()
    world.state.village.incidents.scheduled_fired.clear()
    world.state.village.incidents.last_activation.clear()
    world.state.village.incidents.daily_counts.clear()
    configure(world, event("poor_fishing", location="riverbank", residents=["finn"]))
    village_events.advance(world)
    assert village_events.action_blocker(fisher, "village_work")[0] == "INCIDENT_DELAY"
    assert village_events.action_blocker(fisher, "village_band_attend") is None


def test_real_parent_event_wait_activity_is_woken(world):
    person = world.state.village.residents["torvin"]
    person.activity = "Waiting for the next scheduled event at 19:00."
    person.available_at = 1140
    configure(world, event())
    village_events.advance(world)
    assert person.available_at == 600

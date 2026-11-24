"""Engine-confirmed shipment coordination, independent of model narration."""

import json

import pytest

from npc_sim import (
    village, village_ai, village_communication as comm, village_economy as economy,
    village_goods as goods, village_logistics as logistics,
    village_services, village_wagon,
)
from npc_sim.models import WorldState
from npc_sim.world import World
from test_village_communication import actor, create_world, ready, workspace
from test_village_economy import add


@pytest.fixture
def world(workspace):
    result = create_world(workspace / "logistics.json")
    state = result.state.village
    state.communication.config.instant_messages = True
    state.minute = 540
    for location, stock in state.stocks.items():
        stock.clear()
        state.stock_lots[location] = {}
    state.coins["elsie"] = 500
    ready(result)
    return result


def bread_order(world, quantity=12, stock=12, deadline=660, fresh=720):
    if stock:
        add(world, "bakery", "bread", stock, expiry=900)
    buyer = actor(world, "elsie", "tavern")
    result = comm.apply(buyer, "village_request_supply", {
        "good": "bread", "quantity": quantity, "purpose": "Brunch supplies",
        "deadline": deadline, "required_fresh_until": fresh, "preferred_supplier_id": "jory"})
    assert result[0] == "APPLIED", result
    notice = next(reversed(world.state.village.communication.notices))
    result = comm.apply(actor(world, "jory", "bakery"), "village_respond_to_request", {
        "request_id": notice, "response": "accept"})
    assert result[0] == "APPLIED", result
    return next(reversed(world.state.village.economy.orders.values()))


def assign(world, order, carrier, quantity=3, *, accept=True, **extra):
    result = logistics.apply(actor(world, order.buyer), "village_assign_shipment", {
        "order_id": order.id, "carrier_id": carrier, "quantity": quantity, **extra})
    assert result[0] == "APPLIED", result
    shipment = world.state.village.economy.shipments[json.loads(result[2])["id"]]
    if accept and carrier != order.buyer:
        result = logistics.apply(actor(world, carrier), "village_accept_shipment", {"shipment_id": shipment.id})
        assert result[0] == "APPLIED", result
    return shipment


def message_types(world):
    return [message.type for message in world.state.village.communication.logistics_messages]


def walk(world, carrier, destination):
    bound = actor(world, carrier)
    path = village.route(bound, bound.actor.location_id, destination,
                         road_only=bool(bound.state.village.wagon and bound.state.village.wagon.driver == carrier))
    assert path is not None
    for location in path:
        origin = bound.actor.location_id
        door = next(eid for eid, entity in bound.state.entities.items()
                    if entity.kind == "door" and {entity.location, entity.destination} == {origin, location})
        result = bound._apply("go", {"target": door})
        assert result[0] == "APPLIED", result
        village_ai.after_action(bound, "go", {"target": door})
        village.advance(bound, 5)
        ready(bound)
    return bound


def test_twelve_loaves_have_unique_carriers_real_travel_and_exactly_once_settlement(world):
    order = bread_order(world)
    before_coins = world.state.village.coins.copy()
    add(world, "bakery", "flour", 4)
    world.state.village.economy.config.reserve_targets = {"bakery": {"flour": 4}}
    manifest = [assign(world, order, carrier) for carrier in ("elsie", "ada", "jory", "nella")]
    assert len({shipment.id for shipment in manifest}) == 4
    assert logistics.unassigned(world, order) == 0
    assert order.in_transit == order.delivered_quantity == 0
    assert world.state.village.stocks["bakery"]["bread"] == 12
    for shipment in manifest:
        carrier = walk(world, shipment.carrier, "bakery")
        assert village_services.foot_load(carrier, shipment.carrier, {"bread": 3}, "tavern") is None
        cargo = world.state.village.residents[shipment.carrier].shipments
        assert len(cargo) == 1 and cargo[0].shipment_id == shipment.id
        assert shipment.status == "picked_up"
        assert village_services.foot_unload(carrier, shipment.carrier) is not None
        delivered_before = order.delivered_quantity
        coins_before = world.state.village.coins.copy()
        carrier = walk(world, shipment.carrier, "tavern")
        assert shipment.status == "in_transit"
        assert order.delivered_quantity == delivered_before
        assert world.state.village.coins == coins_before
        assert village_services.foot_unload(carrier, shipment.carrier) is None
        assert shipment.status == "delivered"
        confirmed = world.state.village.communication.logistics_messages[-1]
        assert confirmed.type == "GOODS_DELIVERED"
        assert confirmed.snapshot.order.delivered_quantity == order.delivered_quantity
        assert confirmed.snapshot.order.paid == order.paid
        after = world.state.model_dump(mode="json")
        assert village_services.foot_unload(carrier, shipment.carrier) is not None
        assert world.state.model_dump(mode="json") == after
    assert order.status == "fulfilled"
    assert order.delivered_quantity == 12 and order.in_transit == 0
    assert world.state.village.stocks["tavern"]["bread"] == 12
    assert world.state.village.stocks["bakery"]["bread"] == 0
    assert world.state.village.stocks["bakery"]["flour"] == 4
    assert order.paid == 12 * order.price
    assert order.fees_paid == 4 * order.delivery_fee
    assert sum(world.state.village.coins.values()) == sum(before_coins.values())
    assert world.state.village.minute <= 660
    for kind in ("GOODS_PICKED_UP", "GOODS_DELIVERED"):
        assert message_types(world).count(kind) == 4


def test_assignments_reserve_demand_capacity_and_stock_across_carriers(world):
    order = bread_order(world, quantity=3, stock=3)
    shipment = assign(world, order, "ada")
    buyer = actor(world, "elsie")
    for carrier, quantity in (("nella", 1), ("ada", 1)):
        result = logistics.apply(buyer, "village_assign_shipment", {
            "order_id": order.id, "carrier_id": carrier, "quantity": quantity})
        assert result[0] == "REJECTED"
    other = actor(world, "jory", "bakery")
    before = world.state.model_dump(mode="json")
    assert "SHIPMENT_RESERVED" in village_services.foot_load(other, "jory", {"bread": 3}, "tavern")
    assert world.state.model_dump(mode="json") == before
    assigned = actor(world, "ada", "bakery")
    assert "SHIPMENT_QUANTITY" in village_services.foot_load(assigned, "ada", {"bread": 2}, "tavern")
    assert shipment.outstanding == 3 and order.in_transit == 0
    # A different order cannot consume capacity already promised to this load.
    second = order.model_copy(update={"id": "second", "quantity": 1, "buyer": "nella",
                                     "coordination_notice_id": None})
    world.state.village.economy.orders[second.id] = second
    logistics.emit(world, "ORDER_ACCEPTED", second)
    result = logistics.apply(actor(world, "nella"), "village_assign_shipment", {
        "order_id": second.id, "carrier_id": "ada", "quantity": 1})
    assert result[0] == "REJECTED" and "capacity" in result[2]


def test_earlier_ready_assignment_can_load_while_later_carriers_wait_for_production(world):
    order = bread_order(world)
    first = assign(world, order, "ada")
    second = assign(world, order, "nella")
    goods.take(world, "bakery", "bread", 9)
    logistics.advance(world)
    assert first.status == "ready" and second.status == "assigned"
    carrier = actor(world, "ada", "bakery")
    assert village_services.foot_load(carrier, "ada", {"bread": 3}, "tavern") is None
    assert order.in_transit == 3 and world.state.village.stocks["bakery"]["bread"] == 0
    assert second.outstanding == 3 and logistics.unassigned(world, order) == 6


def test_offers_require_acceptance_and_production_does_not_create_stock(world):
    order = bread_order(world, quantity=3, stock=3)
    shipment = assign(world, order, "ada", accept=False)
    assert shipment.status == "offered"
    bound = actor(world, "ada", "bakery")
    assert village_services.foot_load(bound, "ada", {"bread": 3}, "tavern")
    goods.take(world, "bakery", "bread", 3)
    result = logistics.apply(bound, "village_accept_shipment", {"shipment_id": shipment.id})
    assert result[0] == "APPLIED"
    assert shipment.status == "assigned"
    assert "GOODS_READY" not in message_types(world)
    assert village_services.foot_load(bound, "ada", {"bread": 3}, "tavern")
    add(world, "bakery", "bread", 3, expiry=900)
    logistics.advance(world)
    assert shipment.status == "ready"
    assert message_types(world).count("GOODS_READY") == 1
    assert order.delivered_quantity == order.in_transit == 0


def test_personal_status_messages_are_persistent_private_and_deduplicated(world):
    order = bread_order(world, quantity=3, stock=3)
    shipment = assign(world, order, "ada")
    communication = world.state.village.communication
    count = len(communication.logistics_messages)
    availability = {rid: person.available_at for rid, person in world.state.village.residents.items()}
    for _ in range(3):
        logistics.advance(world)
    assert len(communication.logistics_messages) == count
    assert {rid: person.available_at for rid, person in world.state.village.residents.items()} == availability
    ready_message = next(message for message in communication.logistics_messages if message.type == "GOODS_READY")
    assert ready_message.recipients == ["ada"]
    assert "garrick" not in communication.logistics_knowledge
    assert logistics.observation(actor(world, "garrick"))["logistics"]["orders"] == []
    assert logistics.apply(actor(world, "garrick"), "village_shipment_status", {"order_id": order.id})[0] == "REJECTED"
    result = logistics.apply(actor(world, "jory"), "village_shipment_status", {"order_id": order.id})
    assert json.loads(result[2])["shipments"][0]["carrier"] == "ada"
    assert len(communication.logistics_messages) == count
    restored_state = WorldState.model_validate(world.state.model_dump(mode="json"))
    assert restored_state.village.economy.shipments[shipment.id].carrier == "ada"
    world.save()
    restored = World(world.scenario, world.path, "communication", "jory", "session-jory")
    logistics.advance(restored)
    assert logistics.unassigned(restored, restored.state.village.economy.orders[order.id]) == 0
    assert len(restored.state.village.communication.logistics_messages) == count
    assert "village_assign_shipment" in {action["id"] for action in restored.actions()}


def test_reassignment_and_release_never_teleport_loaded_cargo(world):
    order = bread_order(world, quantity=3, stock=3)
    shipment = assign(world, order, "ada")
    result = logistics.apply(actor(world, "elsie"), "village_reassign_shipment", {
        "shipment_id": shipment.id, "carrier_id": "nella"})
    assert result[0] == "APPLIED"
    assert shipment.id in world.state.village.economy.shipments and shipment.status == "offered"
    assert shipment.reassignments[-1].model_dump() == {
        "from_carrier": "ada", "to_carrier": "nella", "reassigned_at": 540,
        "reassigned_by": "elsie", "reason": "Carrier reassigned",
    }
    assert message_types(world)[-1] == "SHIPMENT_REASSIGNED"
    assert logistics.apply(actor(world, "nella"), "village_accept_shipment", {"shipment_id": shipment.id})[0] == "APPLIED"
    carrier = actor(world, "nella", "bakery")
    assert village_services.foot_load(carrier, "nella", {"bread": 3}, "tavern") is None
    for action, args in (
        ("village_release_shipment", {"reason": "Someone else can help"}),
        ("village_reassign_shipment", {"carrier_id": "ada"}),
    ):
        result = logistics.apply(actor(world, "elsie"), action, {"shipment_id": shipment.id, **args})
        assert result[0] == "REJECTED"
    new = actor(world, "ada", "bakery")
    result = logistics.apply(new, "village_reassign_shipment", {"shipment_id": shipment.id, "carrier_id": "ada"})
    assert result[0] == "APPLIED", result
    assert not world.state.village.residents["nella"].shipments
    assert world.state.village.residents["ada"].shipments[0].shipment_id == shipment.id
    assert order.in_transit == 3 and order.delivered_quantity == 0
    assert economy.apply(new, "village_return_cargo", {})[0] == "APPLIED"
    assert shipment.status == "released" and shipment.returned_quantity == 3
    assert logistics.unassigned(world, order) == 3
    assert world.state.village.stocks["bakery"]["bread"] == 3
    assert order.paid == 0


def test_matching_carrier_assignments_are_consolidated(world):
    order = bread_order(world, quantity=3, stock=3)
    first = assign(world, order, "ada", quantity=2)
    second = assign(world, order, "ada", quantity=1)

    assert second.id == first.id
    assert first.quantity == 3
    assert len(world.state.village.economy.shipments) == 1
    assert logistics.unassigned(world, order) == 0


def test_failed_unloading_changes_no_stock_payment_or_shipment(world):
    order = bread_order(world, quantity=3, stock=3)
    shipment = assign(world, order, "ada")
    carrier = actor(world, "ada", "bakery")
    assert village_services.foot_load(carrier, "ada", {"bread": 3}, "tavern") is None
    carrier.actor.location_id = "tavern"
    world.state.village.coins["elsie"] = 0
    before = world.state.model_dump(mode="json")
    assert village_services.foot_unload(carrier, "ada").startswith("INSUFFICIENT_COINS")
    assert world.state.model_dump(mode="json") == before
    assert shipment.status == "picked_up" and order.delivered_quantity == 0


def test_late_and_spoiled_shipments_block_once_and_keep_surviving_cargo(world):
    order = bread_order(world, quantity=3, stock=3)
    shipment = assign(world, order, "ada")
    carrier = actor(world, "ada", "bakery")
    assert village_services.foot_load(carrier, "ada", {"bread": 3}, "tavern") is None
    ready(world, order.deadline + 5)
    logistics.advance(world)
    logistics.advance(world)
    assert shipment.status == "blocked"
    assert message_types(world).count("SHIPMENT_BLOCKED") == 1
    assert order.in_transit == 3
    ready(world, 905)
    goods.advance(world)
    logistics.advance(world)
    assert shipment.spoiled_quantity == 3 and order.in_transit == 0
    assert not world.state.village.residents["ada"].shipments
    assert order.paid == 0


def test_wagon_cannot_take_a_foot_carriers_reserved_goods(world):
    order = bread_order(world, quantity=3, stock=3)
    assign(world, order, "ada")
    driver = actor(world, "nella", "bakery")
    wagon = world.state.village.wagon
    wagon.repaired = wagon.hitched = True
    wagon.driver = "nella"
    world.state.entities["wagon"].location = "bakery"
    before = world.state.model_dump(mode="json")
    assert village_wagon.load(driver, {"bread": 3}, "tavern").startswith("SHIPMENT_RESERVED")
    assert world.state.model_dump(mode="json") == before
    assert village_wagon.ready_orders(driver) == []


def test_snapshot_brunch_uses_confirmed_assignments_and_keeps_cook_available(workspace):
    from test_village_snapshot import scenario

    world = create_world(workspace / "brunch.json", communication=False, value=scenario(), start_minute=540)
    state = world.state.village
    # Hold public map knowledge constant; this regression tests freight, not exploration.
    for rid in ("elsie", "jory", "mara", "ada", "nella"):
        state.residents[rid].known_routes = [
            eid for eid, entity in world.state.entities.items() if entity.kind == "door" and not entity.hidden]
    ready(world)
    jory = actor(world, "jory", "bakery")
    result = comm.apply(jory, "village_request_supply", {
        "good": "flour", "quantity": 1, "purpose": "Complete the brunch bread above protected flour",
        "deadline": 575, "required_fresh_until": 660, "preferred_supplier_id": "mara"})
    assert result[0] == "APPLIED", result
    notice = next(reversed(state.communication.notices))
    result = comm.apply(actor(world, "mara"), "village_respond_to_request", {
        "request_id": notice, "response": "accept"})
    assert result[0] == "APPLIED", result
    order = bread_order(world, stock=0, deadline=650, fresh=660)
    assert order.status == "accepted_planned"
    initial_flour = state.stocks["watermill"]["flour"] + state.stocks["bakery"]["flour"]
    attempts = []
    for _ in range(24):
        for rid in ("elsie", "jory", "mara", "ada", "nella"):
            bound = actor(world, rid)
            person = state.residents[rid]
            if person.available_at > state.minute or person.life and (person.life.sleeping or person.life.work):
                continue
            if not any(plan["day"] == 0 for plan in person.fallback.daily_plans):
                action, args = "village_daily_plan", {}
            elif (rid == "jory" and economy.loadable_for_order(bound, order) < order.loadable
                  and village_ai.production_ready(bound, "village_work") is None
                  and bound._village_action_blocker("village_work", {}) is None):
                action, args = "village_work", {}
            else:
                choice = logistics.freight_choice(bound)
                if choice is None:
                    continue
                action, args = choice["action_id"], choice["arguments"]
            result = bound.act(f"logistics-{len(attempts)}", bound.revision(), action, args)
            attempts.append((state.minute, rid, action, result["code"]))
            assert result["outcome"] == "APPLIED", (
                f"{state.minute} {rid} {action} {args}: {result['code']} {result['explanation']}; {attempts}")
            assert not state.residents["elsie"].shipments
            assert world.state.actors["elsie"].location_id == "tavern"
        if order.status == "fulfilled":
            break
        village.advance(world, 5)
    assert order.status == "fulfilled", (
        f"{state.minute} {attempts}; "
        f"{[shipment.model_dump() for shipment in state.economy.shipments.values()]}")
    assert state.minute <= 660
    assert state.stocks["tavern"]["bread"] == 12
    assert state.stocks["bakery"]["flour"] >= 2
    assert state.stocks["watermill"]["flour"] >= 8
    assert state.stocks["watermill"]["flour"] + state.stocks["bakery"]["flour"] == initial_flour - 2
    result = world.evaluate_goal("village_snapshot_completed")
    assert result["status"] == "achieved", result
    loads = [event for event in state.communication.logistics_messages
             if event.type == "GOODS_PICKED_UP" and event.order_id == order.id]
    assert sum(state.economy.shipments[event.shipment_id].quantity for event in loads) == 12
    assert len({event.shipment_id for event in loads}) == len(loads)
    assert not any("exceeds confirmed outstanding demand" in event.result for event in state.events)


def test_fixed_duty_includes_return_preparation_and_can_be_explicitly_overridden(world):
    from npc_sim.village_communication_models import Appointment, NoticeKnowledge, NoticeTerms, NoticeVersion

    order = bread_order(world, quantity=3, stock=3)
    state = world.state.village
    appointment = Appointment(id="crew", version=1, project="dig_well", members=["elsie", "ada"],
                              accepted=["elsie", "ada"], place="tavern", due_at=560, end_at=580,
                              status="confirmed")
    notice = NoticeVersion(
        id="duty", version=1, author="ada", terms=NoticeTerms(
            type="meeting", subject="Fixed duty", description="Be ready at the tavern"),
        created_at=540, updated_at=540, appointment=appointment)
    state.communication.knowledge.setdefault("elsie", {})[notice.id] = NoticeKnowledge(
        snapshot=notice, heard_at=540, source_type="tin_can")
    buyer = actor(world, "elsie", "tavern")
    conflict = logistics.apply(buyer, "village_assign_shipment", {
        "order_id": order.id, "carrier_id": "elsie", "quantity": 3})
    assert conflict[0] == "REJECTED" and "preparation" in conflict[2]
    assert not state.economy.shipments
    allowed = logistics.apply(buyer, "village_assign_shipment", {
        "order_id": order.id, "carrier_id": "elsie", "quantity": 3, "allow_duty_conflict": True})
    assert allowed[0] == "APPLIED", allowed


def test_fallback_uses_carrier_assignments_without_purposeful_downtime(workspace):
    from npc_sim import village_fallback
    from npc_sim.models import VillageDayPolicy
    from test_village_snapshot import scenario

    world = create_world(workspace / "fallback.json", communication=False, value=scenario(), start_minute=540)
    ready(world)
    order = bread_order(world, quantity=3, stock=3)
    shipment = assign(world, order, "ada")
    world.state.village.day_policy = VillageDayPolicy(anti_idle=True, purposeful_downtime=False)
    carrier = actor(world, "ada", "bakery")
    assert village_ai.apply(carrier, "village_daily_plan", {})[0] == "APPLIED"
    choices = village_fallback.candidates(carrier)
    pickup = next(choice for choice in choices if choice["task_id"] == f"shipment:load:{shipment.id}")
    assert pickup["arguments"] == {"goods": {"bread": 3}, "destination": "tavern"}
    assert logistics.freight_choice(actor(world, "elsie", "tavern")) is None


def test_cancelled_unpicked_order_releases_capacity_but_not_physical_stock(world):
    order = bread_order(world, quantity=3, stock=3)
    shipment = assign(world, order, "ada")
    order.status = "cancelled"
    logistics.advance(world)
    assert shipment.status == "cancelled"
    assert logistics.capacity(world, "ada", "foot") == 3
    assert world.state.village.stocks["bakery"]["bread"] == 3
    assert order.in_transit == order.paid == order.delivered_quantity == 0
    count = len(world.state.village.communication.logistics_messages)
    logistics.advance(world)
    assert len(world.state.village.communication.logistics_messages) == count


def test_wagon_assignment_keeps_identity_through_loading_and_settlement(world):
    order = bread_order(world, quantity=6, stock=6)
    driver = actor(world, "nella", "bakery")
    wagon = world.state.village.wagon
    wagon.repaired = wagon.hitched = True
    wagon.driver = "nella"
    world.state.entities["wagon"].location = "bakery"
    world.state.entities["wagon"].health = world.state.entities["wagon"].max_health
    shipment = assign(world, order, "nella", quantity=6, mode="wagon")
    assert village_wagon.load(driver, {"bread": 6}, "tavern") is None
    assert wagon.loads[0].shipment_id == shipment.id and order.in_transit == 6
    walk(world, "nella", "tavern")
    assert village_wagon.unload(driver) is None
    assert shipment.status == "delivered" and shipment.delivered_quantity == 6
    assert not wagon.loads
    assert order.paid == order.price * 6 and order.fees_paid == order.delivery_fee


def test_legacy_pickup_is_identified_and_too_late_pickup_is_atomic(world):
    order = bread_order(world, quantity=3, stock=3)
    carrier = actor(world, "jory", "bakery")
    ready(world, order.deadline - 5)
    before = world.state.model_dump(mode="json")
    assert village_services.foot_load(carrier, "jory", {"bread": 3}, "tavern").startswith("SHIPMENT_LATE")
    assert world.state.model_dump(mode="json") == before
    ready(world, 540)
    assert village_services.foot_load(carrier, "jory", {"bread": 3}, "tavern") is None
    cargo = world.state.village.residents["jory"].shipments[0]
    assert cargo.shipment_id in world.state.village.economy.shipments
    assert message_types(world).count("SHIPMENT_ASSIGNED") == 1
    assert message_types(world).count("GOODS_PICKED_UP") == 1
    assert order.delivered_quantity == 0


def test_parked_wagon_spoilage_updates_its_original_carrier_shipment(world):
    order = bread_order(world, quantity=3, stock=3)
    driver = actor(world, "nella", "bakery")
    wagon = world.state.village.wagon
    wagon.repaired = wagon.hitched = True
    wagon.driver = "nella"
    world.state.entities["wagon"].location = "bakery"
    world.state.entities["wagon"].health = world.state.entities["wagon"].max_health
    shipment = assign(world, order, "nella", mode="wagon")
    assert village_wagon.load(driver, {"bread": 3}, "tavern") is None
    wagon.driver = None
    ready(world, 905)
    goods.advance(world)
    assert not wagon.loads and order.in_transit == 0
    assert shipment.spoiled_quantity == 3 and shipment.status == "blocked"
    assert shipment.carrier == "nella"


def test_partial_spoilage_preserves_and_settles_only_the_surviving_physical_load(world):
    order = bread_order(world, quantity=3, stock=3)
    shipment = assign(world, order, "ada")
    carrier = actor(world, "ada", "bakery")
    assert village_services.foot_load(carrier, "ada", {"bread": 3}, "tavern") is None
    cargo = world.state.village.residents["ada"].shipments[0]
    original = cargo.lots[0]
    cargo.lots = [original.model_copy(update={"quantity": 1, "expires_at": 600}),
                  original.model_copy(update={"quantity": 2})]
    ready(world, 605)
    goods.advance(world)
    assert cargo.quantity == shipment.outstanding == order.in_transit == 2
    assert shipment.spoiled_quantity == order.spoiled_quantity == 1
    walk(world, "ada", "tavern")
    assert village_services.foot_unload(carrier, "ada") is None
    assert order.delivered_quantity == 2 and order.paid == 2 * order.price
    assert order.remaining == logistics.unassigned(world, order) == 1
    assert shipment.delivered_quantity == 2 and shipment.status == "delivered"

"""Tin-can logistics: reservations and received facts, never virtual goods."""

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from npc_sim import village, village_communication, village_economy
from npc_sim.village_communication_models import LogisticsMessage, LogisticsSnapshot
from npc_sim.village_economy_models import (
    VillageDeliveryShipment, VillageShipmentReassignment, VillageSupplierOrder,
)

if TYPE_CHECKING:
    from npc_sim.models import VillageShipment
    from npc_sim.world import World


TERMINAL = {"delivered", "cancelled", "released"}
VERBS = {"village_assign_shipment", "village_accept_shipment", "village_release_shipment",
         "village_reassign_shipment", "village_shipment_status"}


class ShipmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    shipment_id: str = Field(min_length=1)


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    order_id: str = Field(min_length=1)
    carrier_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    mode: Literal["foot", "wagon"] = "foot"
    allow_duty_conflict: bool = False


class Acceptance(ShipmentRequest):
    allow_duty_conflict: bool = False


class Reassignment(Acceptance):
    carrier_id: str = Field(min_length=1)
    reason: str = Field(default="Carrier reassigned", min_length=1, max_length=500)


class Release(ShipmentRequest):
    reason: str = Field(min_length=1, max_length=500)


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    order_id: str = Field(min_length=1)


def enabled(world: "World") -> bool:
    return village_communication.instant(world) and world.state.village.economy is not None


def records(world: "World", order_id: str) -> list[VillageDeliveryShipment]:
    return [shipment for shipment in world.state.village.economy.shipments.values()
            if shipment.order_id == order_id]


def pending(shipment: VillageDeliveryShipment) -> bool:
    return shipment.status not in TERMINAL and shipment.picked_up_at is None


def unassigned(world: "World", order: VillageSupplierOrder) -> int:
    return max(0, order.loadable - sum(shipment.outstanding for shipment in records(world, order.id)
                                      if pending(shipment)))


def snapshot(world: "World", order: VillageSupplierOrder) -> LogisticsSnapshot:
    return LogisticsSnapshot(
        order=order.model_copy(deep=True),
        shipments=[shipment.model_copy(deep=True) for shipment in records(world, order.id)],
        unassigned_quantity=unassigned(world, order), confirmed_at=world.state.village.minute)


def participants(world: "World", order: VillageSupplierOrder) -> set[str]:
    communication = world.state.village.communication
    return {rid for rid in {order.buyer, order.supplier,
                           *(shipment.carrier for shipment in records(world, order.id)),
                           *(rid for rid, known in communication.logistics_knowledge.items()
                             if order.id in known)}
            if rid in world.state.village.residents}


def emit(world: "World", kind: str, order: VillageSupplierOrder,
         shipment: VillageDeliveryShipment | None = None) -> None:
    if not enabled(world):
        return
    from npc_sim.models import VillageEvent

    state = world.state.village
    communication = state.communication
    key = f"shipment:{shipment.id}" if shipment else f"{kind}:{order.id}"
    fingerprint = f"{kind}:{shipment.model_dump_json(exclude={'updated_at'})}" if shipment else order.id
    if communication.logistics_sent.get(key) == fingerprint:
        return
    communication.logistics_sent[key] = fingerprint
    current = snapshot(world, order)
    audience = participants(world, order)
    recipients = sorted({shipment.carrier} if kind == "GOODS_READY" and shipment else audience)
    message = LogisticsMessage(
        sequence=len(communication.logistics_messages) + 1, type=kind, minute=state.minute,
        order_id=order.id, shipment_id=shipment.id if shipment else None,
        recipients=recipients, snapshot=current)
    communication.logistics_messages.append(message)
    # Each involved party receives the order manifest, not remote inventory or whereabouts.
    for rid in audience:
        communication.logistics_knowledge.setdefault(rid, {})[order.id] = current.model_copy(deep=True)
    index = len(state.events)
    text = (f"{kind} {order.id}"
            + (f" {shipment.id}: {shipment.carrier}, {shipment.quantity} {order.product}, "
               f"{shipment.status}" if shipment else f": {order.quantity} {order.product}")
            + (f"; {shipment.reason}" if shipment and shipment.reason else ""))
    state.events.append(VillageEvent(
        minute=state.minute, actor="tin-can-logistics", action=kind,
        target=shipment.id if shipment else order.id, result=text,
        witnesses=recipients, details=message.model_dump(mode="json")))
    for rid in recipients:
        person = state.residents[rid]
        person.memories.append(index)
        if person.fallback.wait is not None or person.current_action == "village_wait_event":
            person.fallback.wait = None
            person.available_at = min(person.available_at, state.minute)


def known_order(world: "World", order_id: str) -> VillageSupplierOrder:
    state = world.state.village
    known = state.communication.logistics_knowledge.get(world.actor_id, {})
    learned = {order["id"] for order in village_communication.learned_orders(world)}
    if order_id not in known and order_id not in learned:
        raise ValueError("A personally received order or carrier offer is required")
    order = state.economy.orders.get(order_id)
    if order is None:
        raise ValueError("Unknown order")
    return order


def _cargo(world: "World", carrier: str, mode: str) -> list["VillageShipment"]:
    state = world.state.village
    if mode == "wagon":
        if state.wagon is None:
            raise ValueError("No physical wagon is configured")
        return state.wagon.loads
    return state.residents[carrier].shipments


def capacity(world: "World", carrier: str, mode: str, excluding: str | None = None) -> int:
    from npc_sim import village_equipment, village_services

    state = world.state.village
    if carrier not in world.state.actors or carrier in world.scenario.village.stationary_residents:
        return 0
    if mode == "wagon":
        wagon = state.wagon
        if (wagon is None or wagon.driver != carrier or not wagon.repaired or not wagon.hitched
                or world.state.entities["wagon"].broken
                or world.state.entities["wagon"].location != village.resident_location(world, carrier)):
            return 0
        limit = village_equipment.transport_capacity(world, 6)
    else:
        limit = village_services.foot_capacity(world, carrier)
    held = sum(item.quantity for item in _cargo(world, carrier, mode))
    reserved = sum(shipment.outstanding for shipment in state.economy.shipments.values()
                   if shipment.carrier == carrier and shipment.mode == mode
                   and shipment.id != excluding and pending(shipment))
    return max(0, limit - held - reserved)


def duty_conflict(world: "World", carrier: str, order: VillageSupplierOrder,
                  *, origin: str | None = None, coordination_minutes: int = 5,
                  loaded: bool = False, mode: str = "foot") -> str | None:
    """Budget delivery, return travel and preparation against fixed duties."""
    from npc_sim import village_life

    base, state = village.context(world)
    resident = base.residents[carrier]
    origin = origin or village.resident_location(world, carrier)
    pickup = [] if loaded else village.route(world, origin, order.source, road_only=mode == "wagon")
    delivery = village.route(world, origin if loaded else order.source, order.destination, road_only=mode == "wagon")
    if pickup is None or delivery is None:
        return "No real pickup and delivery route"
    person = state.residents[carrier]
    waking = person.life.sleep_until if person.life and person.life.sleeping else None
    start = max(state.minute, person.available_at, person.busy_until,
                person.life.work.finishes_at if person.life and person.life.work else 0,
                waking or 0)
    finish = start + coordination_minutes + (5 if loaded else 10) + 5 * (len(pickup) + len(delivery))
    if finish > order.deadline:
        return "Pickup and travel cannot meet the delivery deadline"
    day = state.minute // 1440 * 1440
    duties = [(day + resident.bed_minute, resident.home, 5)]
    policy = village_economy.config(world)
    if carrier == policy.innkeeper:
        duties.extend((plan["due"], resident.work,
                       5 if base.snapshot and plan["id"].endswith(f":{base.snapshot.cooking_service}")
                       else policy.cooking_lead_minutes)
                      for plan in village_economy.meal_requirements(world)
                      if plan["remaining_portions"] > plan["covered_portions"])
    communication = state.communication
    for memory in communication.knowledge.get(carrier, {}).values():
        appointment = memory.snapshot.appointment
        if appointment and appointment.status == "confirmed" and carrier in appointment.members:
            duties.append((appointment.due_at, appointment.place, 5))
    life = state.residents[carrier].life
    if life:
        meal = village_life.meal_due(life, state.minute)
        if meal:
            duties.append((meal.due, "tavern", 5))
    for due, site, preparation in sorted(duties):
        if due < state.minute:
            continue
        returning = village.route(world, order.destination, site)
        if returning is None or finish + 5 * len(returning) + preparation > due:
            return f"Allow return travel and {preparation} minutes preparation at {site} by {village.clock_time(due)}"
    return None


def _validate_carrier(world: "World", order: VillageSupplierOrder, carrier: str,
                      quantity: int, mode: str, excluding: str | None = None,
                      allow_duty_conflict: bool = False, loaded: bool = False) -> None:
    if carrier not in world.state.actors:
        raise ValueError("Choose a real active carrier")
    if order.status not in village_economy.LOADABLE or world.state.village.minute > order.deadline:
        raise ValueError("This order is closed or late")
    if world.state.village.coins.get(order.buyer, 0) < village_economy.commitments(world, order.buyer):
        raise ValueError("Buyer can no longer fund the shipment")
    if quantity < 1 or quantity > capacity(world, carrier, mode, excluding):
        raise ValueError("Carrier lacks real capacity after cargo and existing assignments")
    conflict = duty_conflict(world, carrier, order, loaded=loaded, mode=mode)
    if conflict and (not allow_duty_conflict or conflict.startswith(("No real", "Pickup and"))):
        raise ValueError(conflict + "; choose another carrier or explicitly allow a duty conflict")


def _consolidate(world: "World", order: VillageSupplierOrder, carrier: str, quantity: int,
                 mode: str, accepted: bool) -> VillageDeliveryShipment | None:
    candidates = [shipment for shipment in records(world, order.id)
                  if pending(shipment) and shipment.carrier == carrier and shipment.mode == mode
                  and shipment.status != "blocked"]
    if len(candidates) != 1:
        return None
    shipment = candidates[0]
    shipment.quantity += quantity
    shipment.updated_at = world.state.village.minute
    if accepted and shipment.accepted_at is None:
        shipment.accepted_at = world.state.village.minute
    shipment.status = "assigned" if shipment.accepted_at is not None else "offered"
    emit(world, "SHIPMENT_ASSIGNED" if shipment.accepted_at is not None else "SHIPMENT_OFFERED",
         order, shipment)
    return shipment


def _create(world: "World", order: VillageSupplierOrder, carrier: str, quantity: int,
            mode: str, accepted: bool) -> VillageDeliveryShipment:
    consolidated = _consolidate(world, order, carrier, quantity, mode, accepted)
    if consolidated is not None:
        return consolidated
    state = world.state.village
    while f"shipment-{state.economy.next_shipment}" in state.economy.shipments:
        state.economy.next_shipment += 1
    shipment = VillageDeliveryShipment(
        id=f"shipment-{state.economy.next_shipment}", order_id=order.id, carrier=carrier,
        quantity=quantity, mode=mode, status="assigned" if accepted else "offered",
        created_at=state.minute, updated_at=state.minute,
        accepted_at=state.minute if accepted else None)
    state.economy.next_shipment += 1
    state.economy.shipments[shipment.id] = shipment
    emit(world, "SHIPMENT_ASSIGNED" if accepted else "SHIPMENT_OFFERED", order, shipment)
    return shipment


def pickup_available(world: "World", order: VillageSupplierOrder, carrier: str,
                     mode: str = "foot") -> int:
    if not enabled(world):
        return order.loadable
    physical = village_economy.loadable_for_order(world, order)
    own = 0
    for shipment in records(world, order.id):
        if not pending(shipment):
            continue
        if (physical >= shipment.outstanding and shipment.carrier == carrier
                and shipment.mode == mode and shipment.accepted_at is not None):
            own += shipment.outstanding
        physical = max(0, physical - shipment.outstanding)
    return own + min(unassigned(world, order), physical)


def load_parts(world: "World", order: VillageSupplierOrder, carrier: str, amount: int,
               mode: str) -> list[tuple[int, VillageDeliveryShipment | None]]:
    own = [shipment for shipment in records(world, order.id)
           if pending(shipment) and shipment.carrier == carrier and shipment.mode == mode
           and shipment.accepted_at is not None]
    parts = []
    remaining = amount
    for shipment in own:
        if not remaining:
            break
        if shipment.outstanding > remaining:
            raise ValueError(f"SHIPMENT_QUANTITY: {shipment.id} requires its assigned {shipment.outstanding} units")
        parts.append((shipment.outstanding, shipment))
        remaining -= shipment.outstanding
    if remaining:
        if remaining > unassigned(world, order):
            raise ValueError("SHIPMENT_RESERVED: remaining order demand is assigned to another carrier")
        parts.append((remaining, None))
    return parts


def picked_up(world: "World", order: VillageSupplierOrder, carrier: str, amount: int,
              mode: str, shipment: VillageDeliveryShipment | None) -> VillageDeliveryShipment:
    shipment = shipment or _create(world, order, carrier, amount, mode, True)
    shipment.status = "picked_up"
    shipment.picked_up_at = shipment.updated_at = world.state.village.minute
    shipment.reason = None
    return shipment


def cargo_record(world: "World", item: "VillageShipment", carrier: str,
                 mode: str) -> VillageDeliveryShipment | None:
    if not enabled(world) or not item.order_id:
        return None
    state = world.state.village
    shipment = state.economy.shipments.get(item.shipment_id)
    if shipment is None and item.shipment_id is not None:
        raise ValueError("Cargo references an unknown shipment")
    if shipment is None:
        order = state.economy.orders[item.order_id]
        shipment = _create(world, order, carrier, item.quantity, mode, True)
        shipment.status = "picked_up"
        shipment.picked_up_at = state.minute
        item.shipment_id = shipment.id
    return shipment


def cargo_changed(world: "World", item: "VillageShipment", carrier: str, mode: str,
                  event: Literal["delivered", "returned", "spoiled"], quantity: int) -> None:
    shipment = cargo_record(world, item, carrier, mode)
    if shipment is None:
        return
    if event == "delivered":
        shipment.delivered_quantity += quantity
        shipment.delivered_at = world.state.village.minute
        shipment.status = "delivered"
    elif event == "returned":
        shipment.returned_quantity += quantity
        shipment.status, shipment.reason = "released", "Cargo physically returned to supplier"
    else:
        shipment.spoiled_quantity += quantity
        shipment.status, shipment.reason = "blocked", "Cargo spoiled in transit"
    shipment.updated_at = world.state.village.minute


def _handoff(world: "World", shipment: VillageDeliveryShipment, carrier: str) -> None:
    if shipment.mode != "foot":
        raise ValueError("Return wagon cargo before changing its carrier")
    state = world.state.village
    old = shipment.carrier
    if carrier != world.actor_id or village.resident_location(world, old) != world.actor.location_id:
        raise ValueError("The new carrier must accept a handoff while physically beside the current carrier")
    village_communication._ready(world, old)
    cargo = [item for item in state.residents[old].shipments if item.shipment_id == shipment.id]
    if sum(item.quantity for item in cargo) != shipment.outstanding:
        raise ValueError("Shipment and physical cargo disagree; nothing handed over")
    from npc_sim import village_goods
    for item in cargo:
        state.residents[old].shipments.remove(item)
        state.residents[carrier].shipments.append(item)
    village_goods.sync_cargo(world, old)
    village_goods.sync_cargo(world, carrier)
    state.residents[old].available_at = max(state.residents[old].available_at, state.minute + 5)


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    if not enabled(world):
        return "REJECTED", "LOGISTICS_DISABLED", "Shipment coordination requires the tin-can network and economy.", None
    models = {
        "village_assign_shipment": Assignment, "village_accept_shipment": Acceptance,
        "village_release_shipment": Release, "village_reassign_shipment": Reassignment,
        "village_shipment_status": StatusRequest,
    }
    try:
        request = models[action].model_validate(args)
        village_communication._ready(world, world.actor_id)
        state = world.state.village
        if isinstance(request, StatusRequest):
            order = known_order(world, request.order_id)
            current = snapshot(world, order)
            state.communication.logistics_knowledge.setdefault(world.actor_id, {})[order.id] = current
            return "APPLIED", "SHIPMENT_STATUS", current.model_dump_json(), None
        if isinstance(request, Assignment):
            order = known_order(world, request.order_id)
            if world.actor_id not in {order.buyer, order.supplier, request.carrier_id}:
                raise ValueError("Only a buyer, seller or the accepting carrier can assign this order")
            if request.quantity > unassigned(world, order):
                raise ValueError("Quantity exceeds unassigned outstanding order demand")
            _validate_carrier(world, order, request.carrier_id, request.quantity, request.mode,
                              allow_duty_conflict=request.allow_duty_conflict)
            shipment = _create(world, order, request.carrier_id, request.quantity, request.mode,
                               request.carrier_id == world.actor_id)
        else:
            shipment = state.economy.shipments.get(request.shipment_id)
            if shipment is None:
                raise ValueError("Unknown shipment")
            order = known_order(world, shipment.order_id)
            if shipment.status in TERMINAL:
                raise ValueError("This shipment is closed")
            if isinstance(request, Release):
                if world.actor_id not in {order.buyer, order.supplier, shipment.carrier}:
                    raise ValueError("Only the shipment's participants can release it")
                if shipment.picked_up_at is not None and shipment.outstanding:
                    raise ValueError("Physically return or hand off loaded goods before releasing them")
                shipment.status, shipment.reason = "released", request.reason
                shipment.updated_at = state.minute
                emit(world, "SHIPMENT_RELEASED", order, shipment)
            elif isinstance(request, Reassignment):
                if world.actor_id not in {order.buyer, order.supplier, request.carrier_id}:
                    raise ValueError("Only the buyer, seller or accepting new carrier can reassign it")
                if shipment.carrier == request.carrier_id:
                    raise ValueError("This carrier is already assigned")
                loaded = shipment.picked_up_at is not None
                _validate_carrier(world, order, request.carrier_id, shipment.outstanding, shipment.mode,
                                  excluding=shipment.id, allow_duty_conflict=request.allow_duty_conflict,
                                  loaded=loaded)
                if loaded:
                    _handoff(world, shipment, request.carrier_id)
                shipment.reassignments.append(VillageShipmentReassignment(
                    from_carrier=shipment.carrier, to_carrier=request.carrier_id,
                    reassigned_at=state.minute, reassigned_by=world.actor_id,
                    reason=request.reason))
                shipment.carrier = request.carrier_id
                shipment.accepted_at = state.minute if request.carrier_id == world.actor_id else None
                shipment.status = "in_transit" if loaded else "assigned" if shipment.accepted_at is not None else "offered"
                shipment.reason = request.reason
                shipment.updated_at = state.minute
                emit(world, "SHIPMENT_REASSIGNED", order, shipment)
            else:
                if world.actor_id != shipment.carrier:
                    raise ValueError("Only the named carrier can accept this shipment")
                if shipment.accepted_at is not None:
                    return "APPLIED", "SHIPMENT_UNCHANGED", "Shipment acceptance is already recorded.", None
                _validate_carrier(world, order, shipment.carrier, shipment.outstanding, shipment.mode,
                                  excluding=shipment.id, allow_duty_conflict=request.allow_duty_conflict)
                shipment.accepted_at = shipment.updated_at = state.minute
                shipment.status, shipment.reason = "assigned", None
                emit(world, "SHIPMENT_ASSIGNED", order, shipment)
        advance(world)
        return "APPLIED", "SHIPMENT_RECORDED", shipment.model_dump_json(), None
    except (ValueError, ValidationError) as error:
        return "REJECTED", "SHIPMENT_INVALID", str(error), None


def advance(world: "World") -> None:
    if not enabled(world):
        return
    state = world.state.village
    carriers = [(rid, "foot", person.shipments) for rid, person in state.residents.items()]
    if state.wagon and state.wagon.driver:
        carriers.append((state.wagon.driver, "wagon", state.wagon.loads))
    for carrier, mode, cargo in carriers:
        for item in cargo:
            cargo_record(world, item, carrier, mode)
    for order in state.economy.orders.values():
        if order.status in village_economy.COMMITTED:
            emit(world, "ORDER_ACCEPTED", order)
        available = village_economy.loadable_for_order(world, order)
        for shipment in records(world, order.id):
            if shipment.status in TERMINAL:
                continue
            if order.status == "cancelled" and pending(shipment):
                shipment.status, shipment.reason, shipment.updated_at = "cancelled", "Order cancelled", state.minute
                emit(world, "SHIPMENT_RELEASED", order, shipment)
                continue
            reason = None
            if order.status not in village_economy.LOADABLE or state.minute > order.deadline:
                reason = "Order closed or delivery deadline missed"
            elif shipment.spoiled_quantity:
                reason = "Cargo spoiled in transit"
            elif shipment.mode == "wagon" and (
                    state.wagon is None or state.wagon.driver != shipment.carrier
                    or not state.wagon.repaired or not state.wagon.hitched
                    or world.state.entities["wagon"].broken):
                reason = "The assigned wagon or driver is unavailable"
            elif village.route(world, village.resident_location(world, shipment.carrier),
                               order.destination if shipment.picked_up_at is not None else order.source,
                               road_only=shipment.mode == "wagon") is None:
                reason = "Physical delivery route is blocked"
            elif village.route(world, order.source, order.destination, road_only=shipment.mode == "wagon") is None:
                reason = "Physical delivery route is blocked"
            elif state.coins.get(order.buyer, 0) < village_economy.commitments(world, order.buyer):
                reason = "Buyer can no longer fund the shipment"
            elif shipment.accepted_at is not None:
                deadline_conflict = duty_conflict(world, shipment.carrier, order, coordination_minutes=0,
                                                  loaded=shipment.picked_up_at is not None, mode=shipment.mode)
                if deadline_conflict and deadline_conflict.startswith("Pickup and"):
                    reason = "Shipment can no longer arrive before its deadline"
            if reason:
                available = max(0, available - shipment.outstanding) if pending(shipment) else available
                if shipment.status != "blocked" or shipment.reason != reason:
                    shipment.status, shipment.reason, shipment.updated_at = "blocked", reason, state.minute
                    emit(world, "SHIPMENT_BLOCKED", order, shipment)
                continue
            if shipment.picked_up_at is not None:
                status = "in_transit" if village.resident_location(world, shipment.carrier) != order.source else "picked_up"
                if shipment.status != status or shipment.reason is not None:
                    shipment.status, shipment.reason, shipment.updated_at = status, None, state.minute
                continue
            ready = available >= shipment.outstanding
            available = max(0, available - shipment.outstanding)
            status = "offered" if shipment.accepted_at is None else "ready" if ready else "assigned"
            if shipment.status != status:
                shipment.status, shipment.reason, shipment.updated_at = status, None, state.minute
                if status == "ready":
                    emit(world, "GOODS_READY", order, shipment)
        if any(order.id in known for known in state.communication.logistics_knowledge.values()):
            current = snapshot(world, order)
            for rid in participants(world, order):
                old = state.communication.logistics_knowledge.get(rid, {}).get(order.id)
                if old is None or old.model_dump(exclude={"confirmed_at"}) != current.model_dump(exclude={"confirmed_at"}):
                    state.communication.logistics_knowledge.setdefault(rid, {})[order.id] = current.model_copy(deep=True)


def freight_choice(world: "World") -> dict[str, Any] | None:
    """Choose from received manifests; execution still checks real stock and routes."""
    from npc_sim import village_fallback

    base, state = village.context(world)
    rid, location = world.actor_id, world.actor.location_id
    known = state.communication.logistics_knowledge.get(rid, {})
    cargo = state.residents[rid].shipments
    if cargo:
        item = cargo[0]
        order = state.economy.orders.get(item.order_id)
        returning = bool(order and (order.status not in village_economy.LOADABLE or state.minute > order.deadline))
        destination = order.source if returning else item.destination
        if location != destination:
            return village_fallback._travel_choice(
                world, destination, f"shipment:carry:{item.shipment_id or item.order_id}",
                "Return closed unpaid cargo." if returning else "Deliver the identified physical shipment.", "P1")
        return {"task_id": f"shipment:unload:{item.shipment_id or item.order_id}",
                "action_id": "village_return_cargo" if returning else "village_unload_goods",
                "arguments": {"wagon": False} if returning else {},
                "priority": "P1", "reason": "Settle the physically carried shipment."}
    for memory in sorted(known.values(), key=lambda item: (item.order.deadline, item.order.id)):
        order = state.economy.orders[memory.order.id]
        for received in memory.shipments:
            if received.carrier != rid or received.mode != "foot" or received.status in TERMINAL:
                continue
            shipment = state.economy.shipments[received.id]
            if shipment.status == "blocked" and pending(shipment):
                return {"task_id": f"shipment:release:{shipment.id}", "action_id": "village_release_shipment",
                        "arguments": {"shipment_id": shipment.id, "reason": shipment.reason or "Delivery is blocked"},
                        "priority": "P1", "reason": "Release an impossible unpicked load without duplicating goods or orders."}
            if shipment.status == "offered":
                if duty_conflict(world, rid, order) is not None:
                    return {"task_id": f"shipment:decline:{shipment.id}", "action_id": "village_release_shipment",
                            "arguments": {"shipment_id": shipment.id, "reason": "Fixed duty leaves insufficient delivery and return time"},
                            "priority": "P1", "reason": "Release a conflicting offer so another carrier can accept it."}
                return {"task_id": f"shipment:accept:{shipment.id}", "action_id": "village_accept_shipment",
                        "arguments": {"shipment_id": shipment.id}, "priority": "P1",
                        "reason": "Accept the specific offered quantity before collecting it."}
            if shipment.status != "ready":
                continue
            if location != order.source:
                return village_fallback._travel_choice(
                    world, order.source, f"shipment:collect:{shipment.id}",
                    "Collect your confirmed ready shipment, not another carrier's allocation.", "P1")
            return {"task_id": f"shipment:load:{shipment.id}", "action_id": "village_load_goods",
                    "arguments": {"goods": {order.product: shipment.outstanding}, "destination": order.destination},
                    "priority": "P1", "reason": "Load exactly the assigned physical quantity."}
        if (rid not in {order.buyer, order.supplier} or order.status not in village_economy.LOADABLE
                or not memory.unassigned_quantity or state.minute >= order.deadline):
            continue
        if state.coins.get(order.buyer, 0) < village_economy.commitments(world, order.buyer):
            continue
        options = []
        for carrier in sorted(set(world.state.actors) & set(base.residents)):
            quantity = min(memory.unassigned_quantity, capacity(world, carrier, "foot"))
            if not quantity:
                continue
            person = state.residents[carrier]
            if person.life and (person.life.sleeping or person.life.work):
                continue
            if duty_conflict(world, carrier, order) is not None:
                continue
            penalty = (100 if carrier == state.economy.config.innkeeper else 0)
            penalty += 20 if carrier == order.supplier and order.production_plan else 0
            penalty += 0 if carrier == base.courier else 1
            approach = village.route(world, village.resident_location(world, carrier), order.source)
            options.append((penalty, len(approach), carrier, quantity))
        if options:
            _, _, carrier, quantity = min(options)
            if carrier == state.economy.config.innkeeper and _courier_can_return(world, order):
                continue
            return {"task_id": f"shipment:assign:{order.id}:{carrier}", "action_id": "village_assign_shipment",
                    "arguments": {"order_id": order.id, "carrier_id": carrier, "quantity": quantity},
                    "priority": "P1", "reason": "Reserve a specific load for a feasible carrier, leaving the cook time to prepare."}
    return None


def _courier_can_return(world: "World", order: VillageSupplierOrder) -> bool:
    state = world.state.village
    for shipment in state.economy.shipments.values():
        if (shipment.status in TERMINAL or shipment.status == "blocked" or shipment.mode != "foot"
                or shipment.carrier == state.economy.config.innkeeper or not shipment.outstanding):
            continue
        current = state.economy.orders[shipment.order_id]
        location = village.resident_location(world, shipment.carrier)
        route = village.route(world, location,
                              current.destination if shipment.picked_up_at is not None else current.source)
        delivery = village.route(world, current.source, current.destination)
        back = village.route(world, current.destination, order.source)
        final = village.route(world, order.source, order.destination)
        if any(path is None for path in (route, delivery, back, final)):
            continue
        start = max(state.minute, state.residents[shipment.carrier].available_at,
                    current.production_plan.get("finish", 0) if shipment.picked_up_at is None else 0)
        handling = 5 if shipment.picked_up_at is not None else 10 + (5 if shipment.accepted_at is None else 0)
        first = len(route) + (len(delivery) if shipment.picked_up_at is None else 0)
        if start + handling + 5 * (first + len(back) + len(final)) + 15 <= order.deadline:
            return True
    return False


def observation(world: "World") -> dict[str, Any]:
    if not enabled(world):
        return {}
    state = world.state.village
    known = state.communication.logistics_knowledge.get(world.actor_id, {})
    return {"logistics": {
        "protocol": "Tin-Can Logistics Protocol v1", "scope": "personally received confirmed events",
        "orders": [item.model_dump(mode="json") for item in known.values()],
        "messages": [message.model_dump(mode="json", exclude={"snapshot"})
                     for message in state.communication.logistics_messages
                     if world.actor_id in message.recipients][-12:],
    }}


def actions(world: "World") -> list[dict[str, Any]]:
    if not enabled(world):
        return []
    definitions = [
        ("village_assign_shipment", Assignment, "Reserve a specific order quantity for a carrier. Naming someone else offers it; they must accept. This never loads goods. Duty conflicts need explicit override."),
        ("village_accept_shipment", Acceptance, "Accept your named shipment offer. Carry only its assigned quantity; load, travel and unload physically."),
        ("village_release_shipment", Release, "Release an unpicked assignment. Loaded goods must first be physically returned or handed off."),
        ("village_reassign_shipment", Reassignment, "Reassign an existing shipment without a new order. Loaded foot cargo requires the new carrier to accept beside the current carrier."),
        ("village_shipment_status", StatusRequest, "Ask by tin can for the last engine-confirmed order and carrier manifest. Only personally known orders are accessible."),
    ]
    return [{"id": name, "description": text, "targets": [],
             "arguments_schema": model.model_json_schema(), "availability": "available", "reason": None}
            for name, model, text in definitions]

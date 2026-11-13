from typing import TYPE_CHECKING

from npc_sim import village, village_economy
from npc_sim.models import VillageActivityView, VillageShipment, VillageStockLot, VillageStockSnapshot

if TYPE_CHECKING:
    from npc_sim.world import World


def put(world: "World", location: str, resource: str, quantity: int,
        lots: list[VillageStockLot] | None = None, *, kind: str | None = None,
        order_id: str | None = None, ingredients: list[str] | None = None) -> None:
    config, state = village.context(world)
    movement = kind or ("delivered" if lots is not None else "produced")
    if quantity <= 0:
        raise ValueError("Stock additions must be positive")
    if resource == "firewood":
        from npc_sim import village_firewood
        available = village_firewood.capacity_available(world, location)
        if available is not None and quantity > available:
            raise ValueError("FIREWOOD_STORAGE_FULL: destination fuel stock exceeds configured capacity")
    if config.expanded_rules:
        if lots is None:
            from npc_sim import village_research
            shelf = village_research.shelf_life(world, resource)
            lots = [VillageStockLot(quantity=quantity, produced_at=state.minute,
                                    expires_at=state.minute + shelf if shelf else None,
                                    ingredients=list(ingredients or []))]
        if sum(lot.quantity for lot in lots) != quantity:
            raise ValueError("Stock quantity and freshness ledger disagree")
        state.stock_lots.setdefault(location, {}).setdefault(resource, []).extend(
            lot.model_copy(deep=True) for lot in lots)
    stock = state.stocks.setdefault(location, {})
    stock[resource] = stock.get(resource, 0) + quantity
    village_economy.record_movement(world, location, resource, quantity, movement, order_id)


def take(world: "World", location: str, resource: str, quantity: int, *,
         required_fresh_until: int | None = None, kind: str = "consumed",
         order_id: str | None = None) -> list[VillageStockLot]:
    config, state = village.context(world)
    stock = state.stocks.get(location, {})
    if quantity <= 0 or stock.get(resource, 0) < quantity:
        raise ValueError("Stock removal exceeds the real available quantity")
    result: list[VillageStockLot] = []
    if config.expanded_rules:
        lots = state.stock_lots.get(location, {}).get(resource, [])
        if sum(lot.quantity for lot in lots) != stock[resource]:
            raise ValueError(f"Freshness ledger disagrees with stock at {location}: {resource}")
        cutoff = max(state.minute, required_fresh_until if required_fresh_until is not None else state.minute)
        usable = [lot for lot in lots if lot.expires_at is None or lot.expires_at > cutoff]
        if sum(lot.quantity for lot in usable) < quantity:
            raise ValueError("Insufficient stock fresh through the required use time")
        remaining = quantity
        for lot in sorted(usable, key=lambda item: (
                item.expires_at if item.expires_at is not None else float("inf"), item.produced_at)):
            amount = min(lot.quantity, remaining)
            if amount:
                result.append(lot.model_copy(update={"quantity": amount}))
                lot.quantity -= amount
                remaining -= amount
        state.stock_lots[location][resource] = [lot for lot in lots if lot.quantity > 0]
    stock[resource] -= quantity
    village_economy.record_movement(world, location, resource, quantity, kind, order_id)
    return result


def load(world: "World", carrier: str, goods: dict[str, int], destination: str,
         shipments: list[VillageShipment], capacity: int) -> str | None:
    config, state = village.context(world)
    source = village.resident_location(world, carrier)
    if state.mining is not None and source == "iron-mine" and set(goods) == {"iron-ore"}:
        from npc_sim import village_mining
        return village_mining.load_owned_ore(world, carrier, goods, destination, shipments, capacity)
    sellers = [rid for rid, resident in config.residents.items() if resident.work == source]
    buyers = [rid for rid, resident in config.residents.items() if resident.work == destination]
    if (source == "village-square" and set(goods) == {"iron"}
            and world.state.entities["iron-trader"].location == source):
        sellers = ["iron-trader"]
    if state.livestock and state.economy and set(goods) <= {"plant-scraps", "compost-material", "compost"}:
        orders = [order for order in state.economy.orders.values() if order.status in village_economy.COMMITTED
                  and order.source == source and order.destination == destination and order.product in goods]
        if orders:
            sellers = sorted({order.supplier for order in orders})
            buyers = sorted({order.buyer for order in orders})
    from npc_sim import village_communication
    transfer = next((item for item in state.communication.transfers.values()
                     if item.status == "authorized" and item.source_site == source
                     and item.destination_site == destination and goods == {item.good: item.quantity - item.loaded}
                     and carrier in item.allowed_carriers and item.material_owner == carrier
                     and state.minute <= item.due_time), None) if village_communication.enabled(world) else None
    if transfer is None and (source == destination or len(sellers) != 1 or len(buyers) != 1):
        return "Choose different workplaces with identifiable buyers and sellers."
    from npc_sim import village_research
    if not goods or any(amount <= 0 or resource not in village_research.prices(world) for resource, amount in goods.items()):
        return "Choose positive quantities of real priced goods."
    if sum(goods.values()) + sum(item.quantity for item in shipments) > capacity:
        return f"The remaining carrying capacity is insufficient (limit {capacity} units)."
    stock = state.stocks.get(source, {})
    if any(stock.get(resource, 0) < amount for resource, amount in goods.items()):
        return "There is not enough real source stock; nothing was loaded."
    if "firewood" in goods:
        from npc_sim import village_firewood
        available = village_firewood.capacity_available(world, destination)
        inbound = sum(item.quantity for item in shipments if item.resource == "firewood"
                      and item.destination == destination)
        if available is not None and goods["firewood"] + inbound > available:
            return "FIREWOOD_STORAGE_FULL: destination fuel stock exceeds configured capacity."
    economy = getattr(state, "economy", None)
    from npc_sim import village_communication
    policy = village_economy.config(world)
    planned = []
    if transfer is not None:
        if stock.get(transfer.good, 0) - transfer.quantity < village_economy.policy_reserve(world, source, transfer.good):
            return "RAW_MATERIAL_RESERVE: protected workshop stock cannot be released for this repair."
        planned = [(transfer.good, transfer.quantity, None)]
    elif policy and economy:
        for resource, amount in goods.items():
            remaining = amount
            for order in sorted(economy.orders.values(), key=lambda order: (order.deadline, order.id)):
                if (order.status not in village_economy.COMMITTED or order.source != source or order.destination != destination
                        or order.product != resource or order.loadable <= 0):
                    continue
                if not village_communication.can_use_order(world, order):
                    continue
                if state.minute > order.deadline:
                    continue
                quantity = min(remaining, order.loadable)
                planned.append((resource, quantity, order))
                remaining -= quantity
                if not remaining:
                    break
            if remaining:
                if policy.require_orders:
                    return "ORDER_REQUIRED: loading exceeds confirmed outstanding demand; nothing was loaded."
                if stock.get(resource, 0) - amount < village_economy.protected_quantity(world, source, resource):
                    return "RAW_MATERIAL_RESERVE: unrequested cargo would consume protected supplier stock."
                planned.append((resource, remaining, None))
            if any(order and order.coordination_notice_id for product, _, order in planned if product == resource):
                reserve = policy.reserve_targets.get(source, {}).get(resource, 0)
                if stock.get(resource, 0) - amount < reserve:
                    return "RAW_MATERIAL_RESERVE: produce the promised surplus before loading protected stock."
        for buyer in {order.buyer for _, _, order in planned if order}:
            if state.coins[buyer] < village_economy.commitments(world, buyer):
                return "INSUFFICIENT_COINS: the buyer can no longer fund its accepted purchases and fees."
    else:
        planned = [(resource, amount, None) for resource, amount in goods.items()]
    # Select stricter freshness requirements first so shorter-lived eligible lots
    # are left for orders whose use is earlier.
    planned.sort(key=lambda item: item[2].required_fresh_until if item[2] else state.minute, reverse=True)
    if config.expanded_rules:
        available = {resource: [lot.model_copy(deep=True) for lot in state.stock_lots.get(source, {}).get(resource, [])]
                     for resource in goods}
        if any(sum(lot.quantity for lot in available[resource]) != stock[resource] for resource in goods):
            return "STOCK_LEDGER_MISMATCH: freshness and real source stock disagree; nothing was loaded."
        for resource, amount, order in planned:
            cutoff = max(state.minute, order.required_fresh_until if order else state.minute)
            remaining = amount
            for lot in sorted(available[resource], key=lambda lot: (
                    lot.expires_at if lot.expires_at is not None else float("inf"), lot.produced_at)):
                if lot.expires_at is not None and lot.expires_at <= cutoff:
                    continue
                moved = min(lot.quantity, remaining)
                lot.quantity -= moved
                remaining -= moved
            if remaining:
                return "STALE_GOODS: source stock will not remain fresh through the required use time."
    for resource, amount, order in planned:
        contract = next((route for route in config.deliveries if route.source == source
                         and route.destination == destination and route.resource == resource), None)
        values = dict(
            resource=resource, quantity=amount,
            seller=carrier if transfer else order.supplier if order else contract.seller if contract else sellers[0],
            buyer=carrier if transfer else order.buyer if order else contract.buyer if contract else buyers[0],
            destination=destination,
            price=order.price if order else contract.price if contract else village_economy.unit_price(world, resource),
            lots=take(world, source, resource, amount,
                      required_fresh_until=order.required_fresh_until if order else None,
                      kind="loaded", order_id=order.id if order else None))
        if transfer:
            values["owned_transfer"] = True
            transfer.loaded += amount
            transfer.status = "in_transit"
        if order:
            values["order_id"] = order.id
            order.in_transit += amount
        shipments.append(VillageShipment(**values))
        if order:
            village_communication.sync_order(world, order, "commitment_in_progress", source)
    if "firewood" in goods:
        from npc_sim import village_firewood
        village_firewood.sync_delivery_plans(world, carrier)
    return None


def unload(world: "World", carrier: str, shipments: list[VillageShipment]) -> str | None:
    config, state = village.context(world)
    location = village.resident_location(world, carrier)
    deliveries = [item for item in shipments if item.destination == location]
    if not deliveries:
        return "No cargo is intended for this destination."
    from npc_sim import village_firewood
    available = village_firewood.capacity_available(world, location)
    if available is not None and sum(item.quantity for item in deliveries
                                     if item.resource == "firewood") > available:
        return "FIREWOOD_STORAGE_FULL: destination fuel stock exceeds configured capacity."
    payments: dict[str, int] = {}
    economy = getattr(state, "economy", None)
    orders = []
    for item in deliveries:
        if item.owned_transfer:
            from npc_sim import village_communication
            repair = next((entry for entry in state.communication.transfers.values()
                           if entry.status == "in_transit" and entry.good == item.resource
                           and entry.destination_site == location and entry.material_owner == carrier
                           and carrier in entry.allowed_carriers and entry.loaded - entry.delivered >= item.quantity),
                          None) if village_communication.enabled(world) else None
            ore = (state.mining is not None and item.resource == "iron-ore" and item.order_id is None
                   and item.buyer == item.seller == carrier and carrier == state.mining.config.miner
                   and item.destination == config.residents[config.smith].work)
            if not (ore or repair and item.order_id is None and item.buyer == item.seller == carrier):
                return "OWNED_CARGO_MISMATCH: owned material needs its approved physical transfer."
            if repair and state.minute > repair.due_time:
                return "TRANSFER_EXPIRED: the repair material authorization expired before unloading."
        order = economy.orders.get(getattr(item, "order_id", None)) if economy else None
        if getattr(item, "order_id", None) and order is None:
            return "UNKNOWN_ORDER: the shipment has no matching order."
        if order:
            late_accepted = (item.resource == "firewood" and state.firewood is not None
                             and state.firewood.statuses.get(f"late:{order.id}") == "accepted"
                             and state.minute > order.deadline
                             and (order.status in village_economy.COMMITTED or
                                  order.status == "failed" and order.reason == "MISSED_DEADLINE"))
            if not late_accepted and (order.status not in village_economy.COMMITTED or state.minute > order.deadline):
                return "ORDER_CLOSED: delivery is cancelled, failed, fulfilled or late; return its unpaid cargo."
            if (item.resource != order.product or item.buyer != order.buyer or item.seller != order.supplier
                    or item.destination != order.destination or item.price != order.price):
                return "ORDER_MISMATCH: shipment does not match its accepted purchase."
            if (sum(lot.quantity for lot in item.lots) != item.quantity or any(
                    lot.expires_at is not None and lot.expires_at <= max(state.minute, order.required_fresh_until)
                    for lot in item.lots)):
                return "STALE_GOODS: shipment is not fresh through its intended use."
            orders.append(order)
        elif config.expanded_rules and (sum(lot.quantity for lot in item.lots) != item.quantity or any(
                lot.expires_at is not None and lot.expires_at <= state.minute for lot in item.lots)):
            return "STALE_GOODS: expire stale cargo before unloading."
        if not item.owned_transfer:
            payments[item.buyer] = payments.get(item.buyer, 0) + item.quantity * item.price
    for order in orders:
        quantity = sum(item.quantity for item in deliveries if getattr(item, "order_id", None) == order.id)
        if quantity > order.in_transit or quantity > order.remaining:
            return "ORDER_OVERDELIVERY: these quantities have already been delivered or are uncommitted."
    purchased = [item for item in deliveries if not item.owned_transfer]
    fee = max((order.delivery_fee for order in orders), default=(
        village_economy.delivery_fee(world) if config.expanded_rules and purchased else 0))
    fee_buyer = purchased[0].buyer if purchased else carrier
    payments[fee_buyer] = payments.get(fee_buyer, 0) + fee
    if any(state.coins[buyer] < total for buyer, total in payments.items()):
        prefix = "INSUFFICIENT_COINS: " if village_economy.config(world) else ""
        return prefix + "The buyer cannot pay for all goods and the delivery fee; nothing was unloaded."
    for item in deliveries:
        total = item.quantity * item.price
        if not item.owned_transfer:
            village_economy.record_payment(world, item.buyer, item.seller, total, "goods")
        order_id = getattr(item, "order_id", None)
        order = economy.orders.get(order_id) if economy else None
        put(world, location, item.resource, item.quantity, item.lots if config.expanded_rules else None,
            kind="delivered", order_id=order_id)
        if item.owned_transfer and item.resource == "planks" and state.communication:
            transfer = next(entry for entry in state.communication.transfers.values()
                            if entry.status == "in_transit" and entry.material_owner == carrier
                            and entry.good == item.resource and entry.destination_site == location)
            transfer.delivered += item.quantity
            if transfer.delivered == transfer.quantity:
                transfer.status = "delivered"
        if order:
            order.in_transit -= item.quantity
            order.delivered_quantity += item.quantity
            order.paid += total
            if order.remaining == 0:
                order.status = "fulfilled"
            village.event(world, carrier, "order_delivery", order.id,
                          f"Delivered {item.quantity} {item.resource}; {order.remaining} outstanding.", location)
        if item.resource == "bread" and location == "tavern":
            state.bread_deliveries += item.quantity
            if state.repaired:
                state.bread_after_repair += item.quantity
            complete_deadline(world, "bread_delivery", carrier)
    village_economy.record_payment(world, fee_buyer, carrier, fee, "delivery_fee")
    fee_order = next((order for order in orders if order.buyer == fee_buyer), None)
    if fee_order:
        fee_order.fees_paid += fee
    from npc_sim import village_communication
    for order in orders:
        village_communication.sync_order(world, order, "goods_unloaded", location)
        if (order.status == "fulfilled" and village_communication.enabled(world)
                and order.coordination_notice_id):
            current = state.communication.notices[order.coordination_notice_id][-1]
            village_communication._emit(
                world, "payment_settled", current, include_actor=False,
                listeners=[rid for rid in village.local_residents(world, location)
                           if not (state.residents[rid].life and state.residents[rid].life.sleeping)
                           and (not current.terms.private or rid in {
                               current.author, current.responder, *current.terms.recipients})])
    shipments[:] = [item for item in shipments if item.destination != location]
    if any(item.resource == "firewood" for item in deliveries):
        from npc_sim import village_firewood
        village_firewood.sync_delivery_plans(world, carrier, destination=location)
    state.role_contributions[carrier] = state.role_contributions.get(carrier, 0) + 1
    village.event(world, carrier, "delivery_payment", location,
                  f"Buyer paid for the goods and {fee} coin delivery fee to {village.resident_name(world, carrier)}.",
                  location)
    return None


def complete_deadline(world: "World", goal: str, actor: str) -> None:
    _, state = village.context(world)
    deadline = state.deadlines.get(goal)
    if deadline and deadline.completed_at is None:
        deadline.completed_at, deadline.overdue = state.minute, state.minute > deadline.due
        village.announce(world, actor, "deadline_completed", goal,
                         f"{goal.replace('_', ' ')} completed"
                         f"{' after its deadline' if deadline.overdue else ' on time'}.")


def sync_cargo(world: "World", rid: str) -> None:
    _, state = village.context(world)
    person = state.residents[rid]
    person.cargo = {}
    for item in person.shipments:
        person.cargo[item.resource] = person.cargo.get(item.resource, 0) + item.quantity


def advance(world: "World") -> None:
    config, state = village.context(world)
    if not config.expanded_rules:
        return
    from npc_sim import village_recycling
    for location, resources in list(state.stock_lots.items()):
        for resource, lots in list(resources.items()):
            expired = [lot for lot in lots if lot.expires_at is not None and lot.expires_at <= state.minute]
            amount = sum(lot.quantity for lot in expired)
            if amount:
                state.stocks[location][resource] -= amount
                resources[resource] = [lot for lot in lots if lot not in expired]
                village_recycling.recover(world, location, resource, expired)
                village_economy.record_movement(world, location, resource, amount, "spoiled")
                village.event(world, "spoilage", "stock_spoiled", resource,
                              f"{amount} {resource} spoiled at {world.state.entities[location].name}.", location)
    carriers = [("wagon", state.wagon.loads)] if state.wagon else []
    carriers += [(rid, person.shipments) for rid, person in state.residents.items()]
    for carrier, shipments in carriers:
        for item in shipments[:]:
            fresh = [lot for lot in item.lots if lot.expires_at is None or lot.expires_at > state.minute]
            quantity = sum(lot.quantity for lot in fresh)
            if quantity != item.quantity:
                location = (world.state.entities["wagon"].location if carrier == "wagon"
                            else village.resident_location(world, carrier))
                village.event(world, "spoilage", "cargo_spoiled", carrier,
                              f"{item.quantity - quantity} {item.resource} spoiled in transit.", location)
                village_recycling.recover(world, location, item.resource, [
                    lot for lot in item.lots if lot.expires_at is not None and lot.expires_at <= state.minute])
                order_id = getattr(item, "order_id", None)
                village_economy.record_movement(world, location, item.resource,
                                               item.quantity - quantity, "spoiled", order_id)
                economy = getattr(state, "economy", None)
                order = economy.orders.get(order_id) if economy else None
                if order:
                    order.in_transit -= item.quantity - quantity
                    order.spoiled_quantity += item.quantity - quantity
                    order.reason = "CARGO_SPOILED"
                if quantity:
                    item.quantity, item.lots = quantity, fresh
                else:
                    shipments.remove(item)
        if carrier != "wagon":
            sync_cargo(world, carrier)
    for goal, deadline in state.deadlines.items():
        if state.minute > deadline.due and deadline.completed_at is None and not deadline.overdue:
            deadline.overdue = True
            village.announce(world, "village-bell", "deadline_overdue", goal,
                             f"{goal.replace('_', ' ')} is overdue. Recovery remains possible.")
    if "iron-trader" in world.state.entities:
        trader = world.state.entities["iron-trader"]
        entry = "iron-mine" if "iron-mine" in world.state.entities else "bridge"
        day, clock = divmod(state.minute, 1440)
        if (clock == 715 and state.trader_day != day and trader.location is None
                and village.route(world, entry, "village-square") == ["village-square"]):
            trader.location = entry
            state.trader_activities = []
            state.trader_activities.append(VillageActivityView(
                actor="iron-trader", label="Arrival travel", location=entry,
                start=state.minute, end=state.minute + 5, status="completed"))
        if 720 <= clock < 1080 and state.trader_day != day and trader.location == entry:
            if village.route(world, entry, "village-square") == ["village-square"]:
                trader.location = "village-square"
                state.trader_day = day
                put(world, "village-square", "iron", 4, kind="imported")
                state.trader_activities.append(VillageActivityView(
                    actor="iron-trader", label="Trading", location="village-square",
                    start=state.minute, end=day * 1440 + 1080, status="active"))
                village.announce(world, "iron-trader", "trader_arrived", "iron",
                                 "The visiting trader offers four fresh iron units at the square, three coins each.")
        if 1080 <= clock < 1440 and trader.location == "village-square" and state.trader_day == day:
            if village.route(world, "village-square", entry) == [entry]:
                unsold = state.stocks.get("village-square", {}).get("iron", 0)
                if unsold:
                    take(world, "village-square", "iron", unsold, kind="exported")
                if state.trader_activities:
                    state.trader_activities[-1].status = "completed"
                village.announce(world, "iron-trader", "trader_departed", "iron",
                                 f"The trader departed with {unsold} unsold iron units.")
                trader.location = entry
                state.trader_activities.append(VillageActivityView(
                    actor="iron-trader", label="Departure travel", location="village-square",
                    start=state.minute, end=state.minute + 5, status="completed"))
        if clock >= 1085 and trader.location == entry and state.trader_day == day:
            trader.location = None


def snapshot(world: "World") -> None:
    config, state = village.context(world)
    if config.expanded_rules and state.minute % 60 == 0:
        state.stock_history.append(VillageStockSnapshot(
            minute=state.minute, stocks={key: value.copy() for key, value in state.stocks.items()},
            coins=state.coins.copy()))

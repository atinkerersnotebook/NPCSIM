from typing import TYPE_CHECKING

from npc_sim import village
from npc_sim.models import VillageShipment, VillageStockLot, VillageStockSnapshot

if TYPE_CHECKING:
    from npc_sim.world import World


def put(world: "World", location: str, resource: str, quantity: int,
        lots: list[VillageStockLot] | None = None) -> None:
    config, state = village.context(world)
    if quantity <= 0:
        raise ValueError("Stock additions must be positive")
    if config.expanded_rules:
        if lots is None:
            shelf = config.spoil_after.get(resource)
            lots = [VillageStockLot(quantity=quantity, produced_at=state.minute,
                                    expires_at=state.minute + shelf if shelf else None)]
        if sum(lot.quantity for lot in lots) != quantity:
            raise ValueError("Stock quantity and freshness ledger disagree")
        state.stock_lots.setdefault(location, {}).setdefault(resource, []).extend(
            lot.model_copy(deep=True) for lot in lots)
    stock = state.stocks.setdefault(location, {})
    stock[resource] = stock.get(resource, 0) + quantity


def take(world: "World", location: str, resource: str, quantity: int) -> list[VillageStockLot]:
    config, state = village.context(world)
    stock = state.stocks.get(location, {})
    if quantity <= 0 or stock.get(resource, 0) < quantity:
        raise ValueError("Stock removal exceeds the real available quantity")
    result: list[VillageStockLot] = []
    if config.expanded_rules:
        lots = state.stock_lots.get(location, {}).get(resource, [])
        if sum(lot.quantity for lot in lots) != stock[resource]:
            raise ValueError(f"Freshness ledger disagrees with stock at {location}: {resource}")
        remaining = quantity
        for lot in sorted(lots, key=lambda item: (item.produced_at, item.expires_at or float("inf"))):
            amount = min(lot.quantity, remaining)
            if amount:
                result.append(lot.model_copy(update={"quantity": amount}))
                lot.quantity -= amount
                remaining -= amount
        state.stock_lots[location][resource] = [lot for lot in lots if lot.quantity > 0]
    stock[resource] -= quantity
    return result


def load(world: "World", carrier: str, goods: dict[str, int], destination: str,
         shipments: list[VillageShipment], capacity: int) -> str | None:
    config, state = village.context(world)
    source = village.resident_location(world, carrier)
    sellers = [rid for rid, resident in config.residents.items() if resident.work == source]
    buyers = [rid for rid, resident in config.residents.items() if resident.work == destination]
    if (source == "village-square" and set(goods) == {"iron"}
            and world.state.entities["iron-trader"].location == source):
        sellers = ["iron-trader"]
    if source == destination or len(sellers) != 1 or len(buyers) != 1:
        return "Choose different workplaces with identifiable buyers and sellers."
    if not goods or any(amount <= 0 or resource not in config.prices for resource, amount in goods.items()):
        return "Choose positive quantities of real priced goods."
    if sum(goods.values()) + sum(item.quantity for item in shipments) > capacity:
        return f"The remaining carrying capacity is insufficient (limit {capacity} units)."
    stock = state.stocks.get(source, {})
    if any(stock.get(resource, 0) < amount for resource, amount in goods.items()):
        return "There is not enough real source stock; nothing was loaded."
    for resource, amount in goods.items():
        contract = next((route for route in config.deliveries if route.source == source
                         and route.destination == destination and route.resource == resource), None)
        shipments.append(VillageShipment(
            resource=resource, quantity=amount, seller=contract.seller if contract else sellers[0],
            buyer=contract.buyer if contract else buyers[0], destination=destination,
            price=contract.price if contract else config.prices[resource],
            lots=take(world, source, resource, amount)))
    return None


def unload(world: "World", carrier: str, shipments: list[VillageShipment]) -> str | None:
    config, state = village.context(world)
    location = village.resident_location(world, carrier)
    deliveries = [item for item in shipments if item.destination == location]
    if not deliveries:
        return "No cargo is intended for this destination."
    payments: dict[str, int] = {}
    for item in deliveries:
        payments[item.buyer] = payments.get(item.buyer, 0) + item.quantity * item.price
    fee = config.delivery_fee if config.expanded_rules else 0
    fee_buyer = deliveries[0].buyer
    payments[fee_buyer] += fee
    if any(state.coins[buyer] < total for buyer, total in payments.items()):
        return "The buyer cannot pay for all goods and the delivery fee; nothing was unloaded."
    for item in deliveries:
        total = item.quantity * item.price
        state.coins[item.buyer] -= total
        state.coins[item.seller] += total
        put(world, location, item.resource, item.quantity, item.lots if config.expanded_rules else None)
        if item.resource == "bread" and location == "tavern":
            state.bread_deliveries += item.quantity
            if state.repaired:
                state.bread_after_repair += item.quantity
            complete_deadline(world, "bread_delivery", carrier)
    state.coins[fee_buyer] -= fee
    state.coins[carrier] += fee
    shipments[:] = [item for item in shipments if item.destination != location]
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
    for location, resources in state.stock_lots.items():
        for resource, lots in resources.items():
            expired = [lot for lot in lots if lot.expires_at is not None and lot.expires_at <= state.minute]
            amount = sum(lot.quantity for lot in expired)
            if amount:
                state.stocks[location][resource] -= amount
                resources[resource] = [lot for lot in lots if lot not in expired]
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
    if state.minute % 1440 == 720 and state.trader_day != state.minute // 1440:
        state.trader_day = state.minute // 1440
        put(world, "village-square", "iron", 4)
        world.state.entities["iron-trader"].location = "village-square"
        village.announce(world, "iron-trader", "trader_arrived", "iron",
                         "The visiting trader offers four fresh iron units at the square, three coins each.")
    if state.minute % 1440 == 1080 and world.state.entities["iron-trader"].location is not None:
        unsold = state.stocks.get("village-square", {}).get("iron", 0)
        if unsold:
            take(world, "village-square", "iron", unsold)
        world.state.entities["iron-trader"].location = None
        village.announce(world, "iron-trader", "trader_departed", "iron",
                         f"The trader departed with {unsold} unsold iron units.")


def snapshot(world: "World") -> None:
    config, state = village.context(world)
    if config.expanded_rules and state.minute % 60 == 0:
        state.stock_history.append(VillageStockSnapshot(
            minute=state.minute, stocks={key: value.copy() for key, value in state.stocks.items()},
            coins=state.coins.copy()))

"""Owner-accounted, physical storage for the resilient Millhaven depot."""

from npc_sim import village, village_economy, village_goods
from npc_sim.models import VillageShipment, VillageStockLot

DEPOT = "supply-depot"
FEED_STORE = "farm-feed-store"
PRODUCTS = {"grain", "flour", "timber", "firewood", "hay"}
FUEL_CAPACITY = 16


def enabled(world) -> bool:
    return (world.scenario.id == "millhaven-resilient" and DEPOT in world.state.entities
            and FEED_STORE in world.state.entities and world.state.village is not None)


def validate_config(scenario) -> None:
    if scenario.id != "millhaven-resilient":
        return
    base = scenario.village
    if FEED_STORE not in scenario.entities and base is not None and FEED_STORE not in base.stocks:
        # Older frozen resilient episodes predate the physical feed store.
        return
    if base is None or base.livestock is None or base.economy is None or not base.economy.require_orders:
        raise ValueError("Resilient Millhaven requires livestock and a managed depot")
    if any(scenario.entities.get(site) is None or scenario.entities[site].kind != "location"
           for site in (DEPOT, FEED_STORE)):
        raise ValueError("Depot and livestock feed store must be real locations")
    if set(base.stocks.get(DEPOT, {})) != PRODUCTS:
        raise ValueError("The communal depot needs the configured priced products")
    if not {"grain", "hay"} <= set(base.stocks.get(FEED_STORE, {})):
        raise ValueError("The feed store needs real opening grain and hay stock")
    if not base.initial_stock_seed_id:
        raise ValueError("Opening feed stock requires a provenance-backed seed")
    if base.stocks[DEPOT].get("firewood", 0) > FUEL_CAPACITY:
        raise ValueError("Opening depot fuel exceeds the woodshed capacity")
    if any(product not in base.prices or quantity < 0 for site in (DEPOT, FEED_STORE)
           for product, quantity in base.stocks[site].items()):
        raise ValueError("Stored goods need real nonnegative priced stock")
    if base.stocks[FEED_STORE].get("hay", 0) < 1 or base.stocks[FEED_STORE].get("grain", 0) < 1:
        raise ValueError("Opening feed store needs real hay and grain")


def reconcile(world) -> None:
    state = world.state.village
    for location in (DEPOT, FEED_STORE):
        if any(owner not in world.scenario.village.residents for owner in state.depot_lots.get(location, {})):
            raise ValueError(f"Unknown owner in {location} ledger")
        if any(product not in state.stocks.get(location, {}) for inventory in state.depot_lots.get(location, {}).values()
               for product in inventory):
            raise ValueError(f"Unknown product in {location} owner ledger")
        for product, quantity in state.stocks.get(location, {}).items():
            owned = sum(lot.quantity for inventory in state.depot_lots.get(location, {}).values()
                        for lot in inventory.get(product, []))
            if owned != quantity:
                raise ValueError(f"Owner ledger disagrees with stock at {location}: {product}")
            if any(lot.quantity <= 0 for inventory in state.depot_lots.get(location, {}).values()
                   for lot in inventory.get(product, [])):
                raise ValueError(f"Owner ledger has invalid quantity at {location}: {product}")
            physical = sorted(lot.model_dump_json() for lot in state.stock_lots.get(location, {}).get(product, []))
            owned_lots = sorted(lot.model_dump_json() for inventory in state.depot_lots.get(location, {}).values()
                                for lot in inventory.get(product, []))
            if physical != owned_lots:
                raise ValueError(f"Owner ledger disagrees with physical lots at {location}: {product}")
        if location == DEPOT and state.stocks[location].get("firewood", 0) > FUEL_CAPACITY:
            raise ValueError("Depot firewood exceeds physical storage capacity")
        if any(product not in state.stocks.get(location, {}) for product in state.stock_lots.get(location, {})):
            raise ValueError(f"Unknown product in {location} physical ledger")


def _allocate(lots: list[VillageStockLot], quantity: int, minute: int) -> list[VillageStockLot]:
    result = []
    for lot in sorted(lots, key=lambda item: (
            item.expires_at if item.expires_at is not None else float("inf"), item.produced_at)):
        if lot.expires_at is not None and lot.expires_at <= minute:
            continue
        used = min(lot.quantity, quantity)
        if used:
            result.append(lot.model_copy(update={"quantity": used}))
            lot.quantity -= used
            quantity -= used
        if not quantity:
            break
    if quantity:
        raise ValueError("Owner ledger disagrees with available depot stock")
    return result


def _sync_lots(state, location: str, resource: str) -> None:
    state.stock_lots[location][resource] = [lot.model_copy(deep=True)
                                             for inventory in state.depot_lots.get(location, {}).values()
                                             for lot in inventory.get(resource, [])]


def return_withdrawal(world, source: str, owner: str, resource: str, lots: list[VillageStockLot]) -> None:
    state = world.state.village
    state.depot_lots.setdefault(source, {}).setdefault(owner, {}).setdefault(resource, []).extend(
        lot.model_copy(deep=True) for lot in lots)
    _sync_lots(state, source, resource)


def consume_feed(world, resource: str, quantity: int) -> None:
    reconcile(world)
    state = world.state.village
    config = world.scenario.village
    if config is None or config.livestock is None:
        raise ValueError("Livestock feed store requires a keeper")
    owner = config.livestock.keeper
    if resource not in {"grain", "hay"} or quantity < 1:
        raise ValueError("Feed store requires a positive quantity of grain or hay")
    retained = state.depot_lots.get(FEED_STORE, {}).get(owner, {}).get(resource, [])
    if sum(lot.quantity for lot in retained if lot.expires_at is None or lot.expires_at > state.minute) < quantity:
        raise ValueError("Feed store owner ledger disagrees with physical stock")
    village_goods.take(world, FEED_STORE, resource, quantity)
    _allocate(retained, quantity, state.minute)
    state.depot_lots[FEED_STORE][owner][resource] = [lot for lot in retained if lot.quantity]
    _sync_lots(state, FEED_STORE, resource)
    reconcile(world)
    fallback = state.residents[owner].fallback
    if fallback.feed_store_checked_day == state.minute // 1440:
        fallback.feed_store_stock[resource] = state.stocks[FEED_STORE][resource]


def load(world, goods: dict[str, int], withdrawal: bool, *, feed: bool = False) -> str | None:
    if not enabled(world):
        return "DEPOT_UNAVAILABLE: This scenario has no managed depot."
    config, state = village.context(world)
    reconcile(world)
    if not state.economy or not state.economy.config.require_orders:
        return "DEPOT_ECONOMY_REQUIRED: Managed storage needs the active priced economy."
    actor = world.actor_id
    store = FEED_STORE if feed else DEPOT
    if feed and (config.livestock is None or actor != config.livestock.keeper
                 or withdrawal or not set(goods) <= {"grain", "hay"}):
        return "FEED_STORE_AUTHORITY: Only the farmer may restock feed from the farm."
    source = store if withdrawal else config.residents[actor].work
    destination = config.residents[actor].work if withdrawal else store
    if world.actor.location_id != source or source == destination:
        return "DEPOT_WRONG_SITE: Deposit at your workplace or withdraw at the depot."
    if not goods or any(product not in PRODUCTS or type(quantity) is not int or quantity < 1
                        for product, quantity in goods.items()):
        return "DEPOT_INVALID_GOODS: Choose positive quantities of supported depot goods."
    from npc_sim.village_services import foot_capacity
    shipments = state.residents[actor].shipments
    if shipments or sum(goods.values()) > foot_capacity(world, actor):
        return "DEPOT_CAPACITY: Empty your cargo and respect your actual carrying capacity."
    if any(village_economy.usable_stock(world, source, product, state.minute) < quantity
           for product, quantity in goods.items()):
        return "DEPOT_STOCK_SHORTAGE: Not enough usable stock at this site."
    if not withdrawal and any(state.stocks[source].get(product, 0) - quantity
                              < village_economy.protected_quantity(world, source, product)
                              for product, quantity in goods.items()):
        return "DEPOT_RESERVED_STOCK: A committed order or reserve protects this stock."
    inbound_fuel = sum(item.quantity for person in state.residents.values() for item in person.shipments
                       if item.depot_transfer == "deposit" and item.destination == DEPOT
                       and item.resource == "firewood")
    if not withdrawal and not feed and goods.get("firewood", 0) + inbound_fuel + state.stocks[DEPOT].get("firewood", 0) > FUEL_CAPACITY:
        return "FIREWOOD_STORAGE_FULL: The depot woodshed holds at most 16 units."
    allocations: list[tuple[str, str, int]] = []
    for product, quantity in goods.items():
        if not withdrawal:
            allocations.append((product, actor, quantity))
            continue
        remaining = quantity
        owned_lots = sorted(((lot, owner) for owner, inventory in state.depot_lots.get(store, {}).items()
                             for lot in inventory.get(product, [])
                             if lot.expires_at is None or lot.expires_at > state.minute),
                            key=lambda entry: (entry[0].expires_at if entry[0].expires_at is not None
                                               else float("inf"), entry[0].produced_at, entry[1]))
        for lot, owner in owned_lots:
            amount = min(remaining, lot.quantity)
            if amount:
                allocations.append((product, owner, amount))
                remaining -= amount
        if remaining:
            return "DEPOT_OWNED_STOCK: Not enough owner-accounted stock is available."
    if not withdrawal and any(sum(lot.quantity for lot in state.stock_lots.get(source, {}).get(product, [])
                              if lot.expires_at is None or lot.expires_at > state.minute) < quantity
                              for product, quantity in goods.items()):
        return "DEPOT_STOCK_SHORTAGE: Not enough usable stock at this site."
    due = sum(amount * village_economy.unit_price(world, product)
              for product, owner, amount in allocations if withdrawal and owner != actor)
    if state.coins[actor] < due:
        return "INSUFFICIENT_COINS: The buyer cannot fund this withdrawal."
    for product, owner, amount in allocations:
        if withdrawal:
            retained = state.depot_lots[store][owner][product]
            lots = _allocate(retained, amount, state.minute)
            village_goods.take(world, source, product, amount, kind="loaded")
            state.depot_lots[store][owner][product] = [lot for lot in retained if lot.quantity]
            _sync_lots(state, store, product)
        else:
            lots = village_goods.take(world, source, product, amount, kind="loaded")
        shipments.append(VillageShipment(
            resource=product, quantity=amount, seller=owner, buyer=actor,
            destination=destination, price=village_economy.unit_price(world, product),
            lots=lots, depot_transfer="withdrawal" if withdrawal else "deposit", depot_owner=owner))
    village_goods.sync_cargo(world, actor)
    reconcile(world)
    return None


def unload(world, carrier: str, shipments: list[VillageShipment], deliveries: list[VillageShipment]) -> str | None:
    config, state = village.context(world)
    reconcile(world)
    if not state.economy:
        return "DEPOT_ECONOMY_REQUIRED: Managed storage needs the active priced economy."
    location = world.actor.location_id
    if any(item.depot_transfer is None for item in shipments):
        return "DEPOT_MIXED_CARGO: Unload depot cargo separately from ordinary deliveries."
    if any(item.destination != location for item in shipments):
        return "DEPOT_MIXED_CARGO: Unload depot cargo for one destination at a time."
    if any(item.depot_owner not in config.residents or item.seller != item.depot_owner
           or item.buyer != carrier or item.resource not in PRODUCTS
           or (item.depot_transfer == "deposit" and (location not in {DEPOT, FEED_STORE} or item.seller != carrier))
           or (item.depot_transfer == "deposit" and location == DEPOT and item.resource not in PRODUCTS)
           or (item.depot_transfer == "deposit" and location == FEED_STORE and (
               config.livestock is None or carrier != config.livestock.keeper or item.resource not in {"grain", "hay"}))
           or (item.depot_transfer == "withdrawal" and location != config.residents[carrier].work)
           or item.destination != location
           for item in deliveries):
        return "DEPOT_CARGO_MISMATCH: Invalid depot ownership or destination."
    if any(sum(lot.quantity for lot in item.lots) != item.quantity or any(
            lot.expires_at is not None and lot.expires_at <= state.minute for lot in item.lots)
           for item in deliveries):
        return "STALE_GOODS: Cargo must be fresh when unloaded."
    due = sum(item.quantity * item.price for item in deliveries
              if item.depot_transfer == "withdrawal" and item.seller != carrier)
    if state.coins[carrier] < due:
        return "INSUFFICIENT_COINS: The buyer cannot pay depot owners."
    if location == DEPOT and sum(item.quantity for item in deliveries if item.resource == "firewood") + state.stocks[DEPOT].get("firewood", 0) > FUEL_CAPACITY:
        return "FIREWOOD_STORAGE_FULL: The depot woodshed holds at most 16 units."
    for item in deliveries:
        if item.depot_transfer == "withdrawal" and item.seller != carrier:
            village_economy.record_payment(world, carrier, item.seller, item.quantity * item.price, "goods")
        village_goods.put(world, location, item.resource, item.quantity, item.lots, kind="delivered")
        if item.depot_transfer == "deposit":
            state.depot_lots.setdefault(location, {}).setdefault(item.seller, {}).setdefault(item.resource, []).extend(
                lot.model_copy(deep=True) for lot in item.lots)
            _sync_lots(state, location, item.resource)
    shipments[:] = [item for item in shipments if item not in deliveries]
    reconcile(world)
    if location == FEED_STORE and carrier == config.livestock.keeper:
        fallback = state.residents[carrier].fallback
        fallback.feed_store_stock = state.stocks[FEED_STORE].copy()
        fallback.feed_store_checked_day = state.minute // 1440
    return None

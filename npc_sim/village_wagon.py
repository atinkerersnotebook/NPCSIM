from typing import TYPE_CHECKING, Any

from npc_sim import village, village_goods, village_life
from npc_sim.models import VillageCommittedWork, VillageShipment

if TYPE_CHECKING:
    from npc_sim.world import World


ROUTE = ("farm", "watermill", "bakery", "tavern", "village-square")
VERBS = {"village_fit_wagon", "village_start_route", "village_stop_route",
         "village_load_wagon", "village_unload_wagon", "village_drive_wagon"}


def load(world: "World", goods: dict[str, int], destination: str) -> str | None:
    config, state = village.context(world)
    wagon = state.wagon
    if wagon is None:
        raise ValueError("This village has no wagon ledger")
    if config.expanded_rules:
        if wagon.driver is None:
            raise ValueError("A goods load has no active wagon driver")
        return village_goods.load(world, wagon.driver, goods, destination, wagon.loads, 6)
    source = village.resident_location(world, config.courier)
    sellers = [rid for rid, resident in config.residents.items() if resident.work == source]
    buyers = [rid for rid, resident in config.residents.items() if resident.work == destination]
    if source == destination or len(sellers) != 1 or len(buyers) != 1:
        return "Choose a different workplace with an identifiable buyer and seller."
    if any(resource not in config.prices or amount <= 0 for resource, amount in goods.items()) or not goods:
        return "Choose positive quantities of actual priced goods."
    if sum(goods.values()) + sum(item.quantity for item in wagon.loads) > 6:
        return "The wagon holds at most six units, including mixed goods already aboard."
    stock = state.stocks.get(source, {})
    if any(stock.get(resource, 0) < amount for resource, amount in goods.items()):
        return "The source does not have the requested stock; nothing was loaded."
    for resource, amount in goods.items():
        stock[resource] -= amount
        contract = next((route for route in config.deliveries if route.source == source
                         and route.destination == destination and route.resource == resource), None)
        wagon.loads.append(VillageShipment(
            resource=resource, quantity=amount, seller=contract.seller if contract else sellers[0],
            buyer=contract.buyer if contract else buyers[0], destination=destination,
            price=contract.price if contract else config.prices[resource]))
    return None


def unload(world: "World") -> str | None:
    config, state = village.context(world)
    wagon = state.wagon
    if wagon is None:
        raise ValueError("This village has no wagon ledger")
    if config.expanded_rules:
        if wagon.driver is None:
            raise ValueError("A goods unloading has no active wagon driver")
        return village_goods.unload(world, wagon.driver, wagon.loads)
    location = village.resident_location(world, config.courier)
    deliveries = [item for item in wagon.loads if item.destination == location]
    if not deliveries:
        return "There is no cargo for this destination."
    payments: dict[str, int] = {}
    for item in deliveries:
        payments[item.buyer] = payments.get(item.buyer, 0) + item.quantity * item.price
    if any(state.coins[buyer] < total for buyer, total in payments.items()):
        return "The buyer cannot pay for the whole unloading; the cargo and all coins remain unchanged."
    stock = state.stocks.setdefault(location, {})
    for item in deliveries:
        total = item.quantity * item.price
        state.coins[item.buyer] -= total
        state.coins[item.seller] += total
        stock[item.resource] = stock.get(item.resource, 0) + item.quantity
        if item.resource == "bread" and location == "tavern":
            state.bread_deliveries += item.quantity
            if state.repaired:
                state.bread_after_repair += item.quantity
    wagon.loads = [item for item in wagon.loads if item.destination != location]
    state.role_contributions[config.courier] = state.role_contributions.get(config.courier, 0) + 1
    return None


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    config, state = village.context(world)
    wagon = state.wagon
    if wagon is None:
        raise ValueError("Wagon action used without the wagon feature")
    rid = world.actor_id

    def reject(text: str) -> tuple[str, str, str, None]:
        return "REJECTED", "WAGON_UNAVAILABLE", text, None

    def done(code: str, text: str) -> tuple[str, str, str, None]:
        state.residents[rid].activity = text
        village.event(world, rid, action, "wagon", text)
        return "APPLIED", code, text, None

    if world.actor.location_id != world.state.entities["wagon"].location:
        return reject("Walk to the actual wagon first.")
    if action == "village_fit_wagon":
        if (rid != config.smith or "smithing" not in world.actor.body.capabilities
                or world.actor.location_id != config.residents[rid].work):
            return reject("Torvin must fit the parts at his forge.")
        if wagon.repaired:
            return reject("The wagon is already repaired.")
        stock = state.stocks[world.actor.location_id]
        life = state.residents[rid].life
        if life is None:
            raise ValueError("Wagon repair requires daily-life state")
        if stock.get("wagon-parts", 0) < 1:
            return reject("Make one real unit of wagon parts before fitting it.")
        village_goods.take(world, world.actor.location_id, "wagon-parts", 1)
        life.work = VillageCommittedWork(location=world.actor.location_id, finishes_at=state.minute + 30,
                                         outputs={}, kind="wagon_repair")
        return done("WAGON_REPAIR_STARTED", "Fitting one unit of wagon parts; the wagon will be usable in 30 minutes.")
    drivers = {config.courier, "ada", "jory"} if config.expanded_rules else {config.courier}
    if rid not in drivers or (not config.expanded_rules and "trade" not in world.actor.body.capabilities):
        return reject("Nella is the wagon's driver and merchant.")
    if config.expanded_rules:
        if wagon.driver not in {None, rid}:
            return reject("The other driver must park the wagon before you take over.")
        if not wagon.hitched:
            return reject("Bren must hitch the fed horse to the wagon before it can move or deliver.")
        if rid != config.courier and wagon.driver is None:
            merchant = state.residents[config.courier]
            if (village.resident_location(world, config.courier) == world.actor.location_id
                    and merchant.available_at <= state.minute and merchant.life
                    and not merchant.life.sleeping and not merchant.life.work):
                return reject("Nella is available here; backup drivers step in when she is busy or away.")
    if not wagon.repaired:
        return reject("Torvin must finish fitting the wagon parts before the wagon can be used.")
    if action == "village_start_route":
        wagon.driver, wagon.automatic = rid, True
        wagon.route_index, wagon.phase = 0, "travel"
        wagon.next_event_at, wagon.blocker = state.minute + 5, None
        return done("ROUTE_STARTED", "Standing order accepted: farm -> mill -> bakery -> tavern -> square -> farm. "
                    "Drive, load and unload real stock until a meal, rest or a delivery problem interrupts the route.")
    if action == "village_stop_route":
        wagon.automatic, wagon.driver = False, None
        return done("ROUTE_STOPPED", "Parked the wagon here; cargo remains aboard.")
    if action == "village_drive_wagon":
        wagon.driver, wagon.automatic, wagon.blocker = rid, False, None
        return done("WAGON_DRIVER_READY", "Ready to drive manually. Moving through a connection brings the wagon with you.")
    previous_driver, wagon.driver = wagon.driver, rid
    reason = load(world, args["goods"], args["destination"]) if action == "village_load_wagon" else unload(world)
    if reason:
        wagon.driver = previous_driver
        return reject(reason)
    wagon.driver = rid
    return done("WAGON_LOADED" if action == "village_load_wagon" else "WAGON_UNLOADED",
                "Loaded the selected goods in one action." if action == "village_load_wagon"
                else "Unloaded all cargo for this destination in one action and settled the actual payments.")


def advance(world: "World") -> None:
    config, state = village.context(world)
    wagon = state.wagon
    if wagon is None or not wagon.automatic or state.minute < wagon.next_event_at:
        return
    rid = wagon.driver if config.expanded_rules else config.courier
    if rid is None:
        raise ValueError("An automatic wagon has no driver")
    driver = world.state.actors.get(rid)
    if driver is None or wagon.driver != rid or driver.location_id != world.state.entities["wagon"].location:
        raise ValueError("The standing wagon route lost its physical driver")
    person = state.residents[rid]

    def pause(reason: str) -> None:
        wagon.automatic, wagon.driver, wagon.blocker = False, None, reason
        person.available_at, person.activity = state.minute, reason
        village.announce(world, rid, "wagon_paused", "wagon",
                         f"{village.resident_name(world, rid)} parked the wagon at "
                         f"{world.state.entities[driver.location_id].name}: {reason}")

    blocked = village_life.action_blocker(world, "village_start_route", rid)
    if blocked:
        pause(blocked[1])
        return
    if config.expanded_rules and not wagon.hitched:
        pause("The horse is no longer hitched.")
        return
    capabilities = {"move", "manipulate"} | (set() if config.expanded_rules else {"trade"})
    if not capabilities <= set(driver.body.capabilities):
        pause("The driver cannot currently move and handle the wagon.")
        return
    wagon.next_event_at = person.available_at = state.minute + 5
    for _ in range(4):
        destination = ROUTE[wagon.route_index]
        location = driver.location_id
        if location != destination:
            path = village.route(world, location, destination, road_only=config.expanded_rules)
            if not path:
                pause(f"No passable route from {world.state.entities[location].name} to "
                      f"{world.state.entities[destination].name}.")
                return
            driver.location_id = path[0]
            if config.expanded_rules:
                village.remember_step(world, rid, location, path[0])
            world.state.entities["wagon"].location = path[0]
            if config.expanded_rules:
                world.state.entities["work-horse"].location = path[0]
            if path[0] not in driver.visited_locations:
                driver.visited_locations.append(path[0])
            person.activity = f"Driving to {world.state.entities[path[0]].name}."
            village.event(world, rid, "wagon_drive", path[0], person.activity, path[0])
            village.observe_local(world, rid)
            wagon.phase = "unload"
            return
        if wagon.phase == "travel":
            wagon.phase = "unload"
        if wagon.phase == "unload":
            wagon.phase = "load"
            if any(item.destination == location for item in wagon.loads):
                reason = unload(world)
                if reason:
                    pause(reason)
                    return
                person.activity = "Unloaded the wagon and settled the delivery."
                village.event(world, rid, "wagon_unload", location, person.activity, location)
                return
        route = next((route for route in config.deliveries if route.source == location), None)
        wagon.phase = "travel"
        wagon.route_index = (wagon.route_index + 1) % len(ROUTE)
        if route:
            amount = min(state.stocks.get(location, {}).get(route.resource, 0),
                         6 - sum(item.quantity for item in wagon.loads))
            if amount:
                reason = load(world, {route.resource: amount}, route.destination)
                if reason:
                    pause(reason)
                    return
                person.activity = f"Loaded {amount} {route.resource} in one action."
                village.event(world, rid, "wagon_load", location, person.activity, location)
                return
    raise ValueError("The wagon route made no progress")

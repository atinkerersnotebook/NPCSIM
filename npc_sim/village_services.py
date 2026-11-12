from typing import TYPE_CHECKING, Any

from npc_sim import village, village_economy, village_goods, village_life

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"village_load_goods", "village_unload_goods", "village_fertilize", "village_fertilize_compost", "village_drink_tea",
         "village_buy_iron", "village_lead_horse", "village_release_horse", "village_hitch",
         "village_unhitch", "village_start_timber_route", "village_stop_timber_route", "village_urgent_work"}
JOBS = {"village_grow_hay": ("farming", "hay", "sickle"),
        "village_gather_herbs": ("herbalism", "herbs", None),
        "village_fell_logs": ("forestry", "logs", "wood-axe")}


def foot_capacity(world: "World", rid: str) -> int:
    actor = world.state.actors[rid]
    if "hand" not in actor.body.grips:
        return 0
    tools = sum(world.state.entities[eid].inventory_units for eid in actor.inventory)
    capacity = min(3, actor.body.capacity) - tools
    if actor.body.max_held_items is not None:
        capacity = min(capacity, actor.body.max_held_items - len(actor.inventory))
    return max(0, capacity)


def known_routes(world: "World", rid: str) -> list[dict[str, Any]]:
    _, state = village.context(world)
    person = state.residents[rid]
    origin = village.resident_location(world, rid)
    destinations = {end for eid in person.known_routes
                    for end in (world.state.entities[eid].location, world.state.entities[eid].destination) if end}
    result = []
    for destination in sorted(destinations - {origin}):
        path = village.route(world, origin, destination, known_routes=person.known_routes,
                             road_only=bool(state.wagon and state.wagon.driver == rid))
        if path is not None:
            result.append({"destination": destination, "path": path, "steps": len(path)})
    return result


def foot_load(world: "World", rid: str, goods: dict[str, int], destination: str) -> str | None:
    _, state = village.context(world)
    person = state.residents[rid]
    reason = village_economy.load_blocker(world, village.resident_location(world, rid), destination, goods)
    if reason is None and destination == "watermill" and goods == {"planks": 1}:
        from npc_sim import village_communication
        if village_communication.enabled(world):
            try:
                village_communication.authorize_repair_transfer(world, "repair_mill")
            except ValueError as exc:
                reason = str(exc)
    if reason is None:
        reason = village_goods.load(world, rid, goods, destination, person.shipments, foot_capacity(world, rid))
    if reason is None:
        village_goods.sync_cargo(world, rid)
    return reason


def foot_unload(world: "World", rid: str) -> str | None:
    _, state = village.context(world)
    reason = village_goods.unload(world, rid, state.residents[rid].shipments)
    if reason is None:
        village_goods.sync_cargo(world, rid)
    return reason


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    config, state = village.context(world)
    rid, location = world.actor_id, world.actor.location_id
    person = state.residents[rid]

    def reject(text: str) -> tuple[str, str, str, None]:
        return "REJECTED", "VILLAGE_REQUIREMENT", text, None

    def done(code: str, text: str) -> tuple[str, str, str, None]:
        person.activity = text
        village.event(world, rid, action, location, text)
        return "APPLIED", code, text, None

    if action in {"village_load_goods", "village_buy_iron", "village_unload_goods"}:
        if action == "village_unload_goods":
            reason = foot_unload(world, rid)
        else:
            goods = {"iron": args["quantity"]} if action == "village_buy_iron" else args["goods"]
            destination = config.residents[config.smith].work if action == "village_buy_iron" else args["destination"]
            if action == "village_buy_iron" and location != "village-square":
                return reject("Collect the trader's real iron at the village square.")
            policy = village_economy.config(world)
            if action == "village_buy_iron" and policy and policy.require_orders:
                return purchase_iron(world, args["quantity"])
            reason = foot_load(world, rid, goods, destination)
        if reason:
            if village_economy.config(world) and ": " in reason:
                code, text = reason.split(": ", 1)
                if code.isupper():
                    return "REJECTED", code, text, None
            return reject(reason)
        return done("GOODS_TRANSFERRED", "Transferred the selected goods in one action; actual payments occur on unloading.")
    if action in {"village_fertilize", "village_fertilize_compost"}:
        if location != "farm" or "farming" not in world.actor.body.capabilities:
            return reject("The farmer must fertilize at the farm.")
        resource = "compost" if action == "village_fertilize_compost" else "manure"
        if action == "village_fertilize_compost" and not (state.livestock and state.livestock.config.enabled):
            return reject("Farm food recycling is not enabled.")
        if village_economy.usable_stock(world, "farm", resource, state.minute) < 1:
            return reject(f"Bring one real {resource} unit to the farm.")
        village_goods.take(world, "farm", resource, 1)
        person.fertilized_harvests += 3
        return done("FARM_FERTILIZED", f"Used {resource}; the next three grain harvests take half the usual cooldown.")
    if action == "village_drink_tea":
        if person.life is None:
            raise ValueError("Drinking tea requires daily-life state")
        if person.life.tea_day == state.minute // 1440:
            return reject("You have already used tea for tiredness today.")
        if state.stocks.get(location, {}).get("tea", 0) < 1:
            return reject("There is no actual tea here to drink.")
        owner = next((other for other, resident in config.residents.items() if resident.work == location), None)
        if owner is None:
            return reject("There is no local tea seller.")
        price = village_economy.unit_price(world, "tea") if owner != rid else 0
        if state.coins[rid] < price:
            return reject("You cannot afford this cup of tea.")
        village_goods.take(world, location, "tea", 1)
        village_economy.record_payment(world, rid, owner, price, "tea")
        person.life.tea_day = state.minute // 1440
        return done("TEA_DRUNK", "Drank real tea; today's tiredness slowdown is relieved, but hunger and the need to sleep remain.")
    if action == "village_urgent_work":
        project = args["project"]
        crew = {config.smith, config.helper} if project == "dig_well" else {config.carpenter, config.miller}
        if rid not in crew or person.life is None:
            return reject("Only a project's own crew may choose this bedtime override.")
        if (state.well_dug if project == "dig_well" else state.repaired):
            return reject("The project is already complete.")
        if state.minute % 1440 < config.residents[rid].wake_minute and state.minute % 1440 >= 120:
            return reject("The overnight override has ended; sleep before continuing.")
        day = state.minute // 1440 - (1 if state.minute % 1440 < 120 else 0)
        person.life.urgent_project, person.life.urgent_until = project, (day + 1) * 1440 + 120
        return done("URGENT_WORK_CHOSEN", "Explicitly chose urgent project work until 02:00. "
                    "Missing sleep by then doubles the next waking day's work cooldown.")
    if action in {"village_start_timber_route", "village_stop_timber_route"}:
        if "forestry" not in world.actor.body.capabilities:
            return reject("The forester carries the foot-only timber route.")
        if action == "village_start_timber_route":
            if location != "woods" or person.shipments:
                return reject("Start at the woodlot with no outstanding foot shipments.")
            person.timber_route, person.route_next_at = True, state.minute + 5
            return done("TIMBER_ROUTE_STARTED", "Accepted a standing foot route: carry up to three timber to the workshop, then return.")
        person.timber_route = False
        return done("TIMBER_ROUTE_STOPPED", "Stopped the timber route here, retaining all cargo.")
    if rid != config.helper or "animal_care" not in world.actor.body.capabilities:
        return reject("Bren cares for and hitches the working horse.")
    horse, wagon = world.state.entities["work-horse"], state.wagon
    if wagon is None:
        raise ValueError("Horse care requires the wagon")
    if horse.location != location:
        return reject("Walk to the actual horse first.")
    if action == "village_lead_horse":
        if wagon.hitched or state.horse_leader not in {None, rid}:
            return reject("The horse is already hitched or being led.")
        state.horse_leader = rid
        return done("HORSE_LED", "Leading the horse; it will accompany your actual movement.")
    if action == "village_release_horse":
        if location != "stable" or state.horse_leader != rid or wagon.hitched:
            return reject("Return the led horse to the stable before releasing it.")
        state.horse_leader = None
        return done("HORSE_STABLED", "Released the horse safely at the stable.")
    if world.state.entities["wagon"].location != location:
        return reject("The wagon and horse must both be physically here.")
    if action == "village_hitch":
        if wagon.hitched or state.horse_fed_day != state.minute // 1440:
            return reject("Feed the horse today before hitching; it cannot already be hitched.")
        wagon.hitched, state.horse_leader = True, None
        return done("HORSE_HITCHED", "Hitched the fed horse to the wagon for the day's deliveries.")
    if not wagon.hitched:
        return reject("The horse is not hitched.")
    wagon.hitched, wagon.automatic, wagon.driver, state.horse_leader = False, False, None, rid
    return done("HORSE_UNHITCHED", "Unhitched the horse; lead it back to the stable for the night.")


def purchase_iron(world: "World", quantity: int) -> tuple[str, str, str, None]:
    from npc_sim.village_economy_models import VillageSupplierOrder
    base, state = village.context(world)
    economy = state.economy
    if economy is None:
        raise ValueError("An ordered iron purchase requires economy state")
    trader = world.state.entities["iron-trader"]
    if world.actor.location_id != "village-square" or trader.location != "village-square" or trader.hidden:
        return "REJECTED", "TRADER_NOT_PRESENT", "Meet the actual iron trader at the square between noon and 18:00.", None
    if type(quantity) is not int or not 1 <= quantity <= foot_capacity(world, world.actor_id):
        return "REJECTED", "IRON_CARRYING_CAPACITY", "Choose a positive iron quantity that fits your actual free carrying capacity.", None
    destination = base.residents[base.smith].work
    existing = any(order.status == "accepted" and order.supplier == "iron-trader"
                   and order.buyer == base.smith and order.product == "iron" and order.loadable >= quantity
                   and order.deadline >= state.minute for order in economy.orders.values())
    if existing:
        reason = foot_load(world, world.actor_id, {"iron": quantity}, destination)
        if reason:
            return "REJECTED", "IRON_COLLECTION_BLOCKED", reason, None
        village.event(world, world.actor_id, "iron_collected", "iron", "Collected a previously funded iron consignment in person.")
        return "APPLIED", "IRON_COLLECTED", "Carry the ordered iron physically to the forge and unload it to settle payment.", None
    if world.actor_id != base.smith:
        return "REJECTED", "BUYER_MUST_ORDER", "The smith must meet the trader and place the funded purchase first; carriers may then collect that order.", None
    if village_economy.usable_stock(world, "village-square", "iron", state.minute) < quantity:
        return "REJECTED", "TRADER_STOCK_SHORTAGE", "The trader's finite iron consignment is insufficient; wait for another delivery or mine ore.", None
    price, fee = village_economy.unit_price(world, "iron"), village_economy.delivery_fee(world)
    if state.coins[base.smith] - village_economy.commitments(world, base.smith) < quantity * (price + fee):
        return "REJECTED", "INSUFFICIENT_COINS", "The forge cannot fund this iron order after existing commitments and delivery fees.", None
    identifier = f"order-{economy.next_order}"
    order = VillageSupplierOrder(
        id=identifier, request_key=f"iron-trader:{identifier}", buyer=base.smith, supplier="iron-trader",
        source="village-square", destination=destination, product="iron", quantity=quantity,
        price=price, delivery_fee=fee, created_at=state.minute,
        deadline=(state.minute // 1440 + 1) * 1440, required_fresh_until=(state.minute // 1440 + 1) * 1440,
        status="accepted")
    economy.orders[identifier] = order
    reason = foot_load(world, world.actor_id, {"iron": quantity}, destination)
    if reason:
        del economy.orders[identifier]
        return "REJECTED", "IRON_COLLECTION_BLOCKED", reason, None
    economy.next_order += 1
    village.event(world, world.actor_id, "iron_order_placed", identifier,
                  f"Ordered {quantity} iron face-to-face from the visiting trader at {price} coins per unit, "
                  f"plus the agreed delivery fee; collected real cargo for the forge.")
    return "APPLIED", "IRON_ORDER_COLLECTED", "The trader accepted the funded purchase in person. Carry it to the forge and unload to pay.", None


def advance(world: "World") -> None:
    config, state = village.context(world)
    for rid, person in state.residents.items():
        if not person.timber_route or state.minute < person.route_next_at:
            continue
        actor = world.state.actors[rid]

        def pause(reason: str) -> None:
            person.timber_route, person.available_at, person.activity = False, state.minute, reason
            village.announce(world, rid, "timber_route_paused", "timber", reason)

        blocked = village_life.action_blocker(world, "village_start_timber_route", rid)
        if blocked:
            pause(blocked[1])
            continue
        if not {"move", "manipulate", "forestry"} <= set(actor.body.capabilities):
            pause("The forester cannot currently carry timber along the route.")
            continue
        destination = "workshop" if person.shipments else "woods"
        location = actor.location_id
        person.route_next_at = person.available_at = state.minute + 5
        if location == destination:
            if person.shipments:
                reason = foot_unload(world, rid)
            else:
                amount = min(foot_capacity(world, rid), state.stocks["woods"].get("timber", 0))
                reason = foot_load(world, rid, {"timber": amount}, "workshop") if amount else "No timber is ready at the woodlot."
            if reason:
                pause(reason)
            else:
                village.event(world, rid, "timber_transfer", destination,
                              "Transferred the foot-route timber in one action.", location)
            continue
        path = village.route(world, location, destination)
        if not path:
            pause("The timber foot route is blocked.")
            continue
        actor.location_id = path[0]
        village.remember_step(world, rid, location, path[0])
        if path[0] not in actor.visited_locations:
            actor.visited_locations.append(path[0])
        village.event(world, rid, "timber_walk", path[0], f"Carried timber toward {world.state.entities[path[0]].name}.", path[0])
        village.observe_local(world, rid)

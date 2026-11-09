"""Demand planning, explicit supplier commitments, and cash/stock accounting."""

from math import ceil
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from npc_sim import village
from npc_sim.village_economy_models import (
    VillageEconomyConfig, VillageEconomyMovement, VillageEconomyPayment,
    VillageEconomyState, VillageSupplierOrder,
)

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"village_plan_supplies", "village_propose_order", "village_accept_order",
         "village_reject_order", "village_defer_order", "village_cancel_order",
         "village_return_cargo"}
ACTIVE = {"proposed", "accepted", "deferred"}


def example_config(scenario: Any) -> dict[str, Any]:
    """Return a fresh Millhaven policy without changing scenario or real funds.

    Breakfast is included only when its daily-life schedule is enabled. The
    courier funds a one-coin meal subsidy from its existing finite balance;
    exhausted funding is an explicit unaffordable meal, never free money.
    """
    base = scenario.village
    if base is None:
        raise ValueError("The economy example requires a village scenario")
    innkeeper = next((rid for rid, resident in base.residents.items()
                      if "hospitality" in resident.skills), None)
    if innkeeper is None or base.residents[innkeeper].job is None:
        raise ValueError("The economy example requires a real innkeeper and cooking recipe")
    job = base.residents[innkeeper].job
    diners = len(base.residents)
    bread_yield = job.outputs.get("meals", 2)
    fish_portions = min(diners, (diners // 3) // bread_yield * bread_yield)
    plans = []
    if getattr(base, "breakfast", False):
        use_minute = max(resident.wake_minute for resident in base.residents.values()) + base.breakfast_delay
        plans.append({"id": "breakfast", "use_minute": use_minute, "portions": diners,
                      "recipe_portions": {"bread": diners}})
    plans.append({"id": "supper", "use_minute": 1140, "portions": diners,
                  "recipe_portions": {"bread": diners - fish_portions, "fish": fish_portions},
                  "tea_portions": diners})
    suppliers = {product: rid for rid, resident in base.residents.items() if resident.job
                 for product in resident.job.outputs if product in base.prices and rid != innkeeper}
    for product, skill in (("hay", "farming"), ("herbs", "herbalism"), ("logs", "forestry")):
        supplier = next((rid for rid, resident in base.residents.items() if skill in resident.skills), None)
        if supplier and product in base.prices:
            suppliers[product] = supplier
    raw_reserves = {"grain": 4, "flour": 2, "iron": 2, "logs": 4,
                    "hay": 2, "herbs": 2, "timber": 2}
    reserves = {location: {product: raw_reserves[product] for product in stock if product in raw_reserves}
                for location, stock in base.stocks.items()}
    reserves = {location: stock for location, stock in reserves.items() if stock}
    targets = {}
    for resident in base.residents.values():
        if resident.job:
            outputs = resident.job.outputs
            maintenance = {product: amount for product, amount in (("planks", 2), ("wagon-parts", 1))
                           if product in outputs}
            if maintenance:
                targets[resident.work] = maintenance
    policy = VillageEconomyConfig.model_validate({
        "innkeeper": innkeeper,
        "recipes": {"bread": {"inputs": dict(job.inputs), "portions": bread_yield},
                    "fish": {"inputs": {"fish": 1}, "portions": bread_yield}},
        "meal_plans": plans, "suppliers": suppliers, "stock_buffer": {"bread": 1},
        "reserve_targets": reserves, "production_targets": targets,
        "prices": {product: base.prices[product] for product in ("bread", "fish", "tea") if product in base.prices},
        "meal_price": 2, "delivery_fee": 1,
        "subsidy_per_meal": 1, "subsidy_payer": base.courier,
        "cooking_lead_minutes": 60, "delivery_lead_minutes": 30, "require_orders": True,
    })
    wrapped = SimpleNamespace(**base.model_dump())
    wrapped.residents, wrapped.economy = base.residents, policy
    validate_config(SimpleNamespace(village=wrapped))
    return policy.model_dump(mode="json")


def config(world: "World") -> VillageEconomyConfig | None:
    state = getattr(world.state.village, "economy", None)
    return state.config if state and state.config is not None else getattr(world.scenario.village, "economy", None)


def _state(world: "World") -> VillageEconomyState | None:
    return getattr(world.state.village, "economy", None)


def validate_config(scenario: Any) -> None:
    base = scenario.village
    policy = getattr(base, "economy", None)
    if policy is None:
        return
    if not base.expanded_rules:
        raise ValueError("The economy requires expanded rules and real freshness ledgers")
    if policy.innkeeper not in base.residents:
        raise ValueError("Economy innkeeper must be a real resident")
    accounts = set(base.coins)
    if policy.subsidy_payer is not None and policy.subsidy_payer not in accounts:
        raise ValueError("Meal subsidies must name an existing funded account")
    prices = {**base.prices, **policy.prices}
    products = set(prices)
    if any(resource not in products for recipe in policy.recipes.values() for resource in recipe.inputs):
        raise ValueError("Recipe inputs must name real priced products")
    if any(resource not in products for resource in policy.stock_buffer):
        raise ValueError("Stock buffers must name real priced products")
    for resource, supplier in policy.suppliers.items():
        if resource not in products or supplier not in base.residents:
            raise ValueError("Suppliers must name real products and residents")
    for targets in (policy.reserve_targets, policy.production_targets):
        for location, quantities in targets.items():
            if location not in base.stocks or any(resource not in products for resource in quantities):
                raise ValueError("Reserve/production targets must name real stock locations and products")


def initial_state(scenario: Any) -> VillageEconomyState | None:
    policy = getattr(scenario.village, "economy", None)
    if policy is None:
        return None
    return VillageEconomyState(config=policy.model_copy(deep=True),
                               initial_coins=scenario.village.coins.copy())


def configure(world: "World", values: dict[str, Any]) -> None:
    policy = VillageEconomyConfig.model_validate(values)
    base = world.scenario.village
    wrapped = SimpleNamespace(**base.model_dump())
    wrapped.residents, wrapped.economy = base.residents, policy
    from npc_sim import village_research
    wrapped.prices = village_research.prices(world)
    validate_config(SimpleNamespace(village=wrapped))
    state = _state(world)
    if state is None:
        world.state.village.economy = VillageEconomyState(config=policy,
                                                        initial_coins=world.state.village.coins.copy())
    else:
        if any(order.status in ACTIVE or order.in_transit for order in state.orders.values()):
            raise ValueError("Resolve outstanding orders and cargo before changing economy policy")
        state.config = policy


def unit_price(world: "World", resource: str) -> int:
    from npc_sim import village_research
    policy = config(world)
    values = village_research.prices(world)
    return policy.prices.get(resource, values.get(resource, 0)) if policy else values.get(resource, 0)


def delivery_fee(world: "World") -> int:
    policy = config(world)
    return (policy.delivery_fee if policy and policy.delivery_fee is not None
            else world.scenario.village.delivery_fee)


def meal_policy(world: "World") -> tuple[int, str | None, int]:
    policy = config(world)
    return ((policy.meal_price if policy.meal_price is not None else world.scenario.village.meal_price),
            policy.subsidy_payer, policy.subsidy_per_meal) if policy else (
                world.scenario.village.meal_price, None, 0)


def record_payment(world: "World", payer: str, payee: str, amount: int, kind: str) -> bool:
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise ValueError("Payments require nonnegative integer amounts")
    state = world.state.village
    entry = VillageEconomyPayment(minute=state.minute, payer=payer, payee=payee,
                                  amount=amount, kind=kind)
    if payer not in state.coins or payee not in state.coins or state.coins[payer] < amount:
        return False
    state.coins[payer] -= amount
    state.coins[payee] += amount
    economy = _state(world)
    if economy is not None and amount:
        economy.payments.append(entry)
    return True


def record_external_payment(world: "World", payer: str, payee: str, amount: int,
                            kind: str = "goods") -> None:
    """Ledger only: visitor trading already transfers its separate real purse."""
    economy = _state(world)
    entry = VillageEconomyPayment(minute=world.state.village.minute, payer=payer, payee=payee,
                                  amount=amount, kind=kind)
    if not ({payer, payee} & set(world.state.village.coins)):
        raise ValueError("An external payment must involve a real village account")
    if economy is not None and amount:
        economy.payments.append(entry)


def meal_affordable(world: "World", payer: str) -> bool:
    price, funder, subsidy = meal_policy(world)
    policy = config(world)
    payee = policy.innkeeper if policy else next(
        rid for rid, resident in world.scenario.village.residents.items() if resident.work == "tavern")
    obligations: dict[str, int] = {}
    if payer != payee:
        obligations[payer] = price
    if funder and subsidy:
        obligations[funder] = obligations.get(funder, 0) + subsidy
    return all(world.state.village.coins.get(account, -1) >= amount for account, amount in obligations.items())


def meal_charge(world: "World", payer: str) -> bool:
    """Check both funded legs before moving any coins; caller consumes food afterward."""
    if not meal_affordable(world, payer):
        return False
    price, funder, subsidy = meal_policy(world)
    policy = config(world)
    payee = policy.innkeeper if policy else next(
        rid for rid, resident in world.scenario.village.residents.items() if resident.work == "tavern")
    if payer != payee:
        record_payment(world, payer, payee, price, "meal")
    if funder and subsidy:
        record_payment(world, funder, payee, subsidy, "subsidy")
    return True


def record_movement(world: "World", location: str, product: str, quantity: int,
                    kind: str, order_id: str | None = None) -> None:
    economy = _state(world)
    if economy is not None and quantity:
        economy.movements.append(VillageEconomyMovement(
            minute=world.state.village.minute, location=location, product=product,
            quantity=quantity, kind=kind, order_id=order_id))


def usable_stock(world: "World", location: str, product: str, use_at: int) -> int:
    state = world.state.village
    if world.scenario.village.expanded_rules:
        return sum(lot.quantity for lot in state.stock_lots.get(location, {}).get(product, [])
                   if lot.expires_at is None or lot.expires_at > max(state.minute, use_at))
    return state.stocks.get(location, {}).get(product, 0)


def commitments(world: "World", buyer: str, excluding: str | None = None) -> int:
    state = _state(world)
    return sum(order.commitment for order in state.orders.values()
               if order.buyer == buyer and order.id != excluding) if state else 0


def protected_quantity(world: "World", location: str, product: str) -> int:
    policy, state = config(world), _state(world)
    if policy is None or state is None:
        return 0
    return policy.reserve_targets.get(location, {}).get(product, 0) + sum(
        order.loadable for order in state.orders.values()
        if order.status == "accepted" and order.source == location and order.product == product)


def _incoming(world: "World", destination: str, product: str, use_at: int,
              include_proposed: bool = False) -> int:
    state = _state(world)
    if state is None:
        return 0
    statuses = ACTIVE if include_proposed else {"accepted"}
    return sum(order.remaining for order in state.orders.values()
               if order.status in statuses and order.destination == destination
               and order.product == product and order.deadline <= use_at
               and order.required_fresh_until >= use_at)


def _wip(world: "World", location: str, product: str, use_at: int) -> int:
    from npc_sim import village_research
    shelf = village_research.shelf_life(world, product)
    return sum(person.life.work.outputs.get(product, 0)
               for person in world.state.village.residents.values()
               if person.life and person.life.work and person.life.work.location == location
               and person.life.work.finishes_at <= use_at
               and (shelf is None or person.life.work.finishes_at + shelf > use_at))


def _supplier(world: "World", product: str, destination: str) -> str | None:
    from npc_sim import village_firewood
    if product == "firewood" and village_firewood.enabled(world):
        return world.state.village.firewood.config.supplier
    from npc_sim import village_livestock
    if world.state.village.livestock and product in {"milk", "eggs", "compost"}:
        return world.state.village.livestock.config.keeper
    if world.state.village.livestock and product in {"plant-scraps", "compost-material"}:
        from npc_sim import village_recycling
        return village_recycling.supplier(world, product)
    from npc_sim import village_research
    discovery = village_research.discoveries(world).get(product)
    if discovery:
        return discovery.owner
    policy = config(world)
    base, _ = village.context(world)
    if policy and product in policy.suppliers:
        return policy.suppliers[product]
    route = next((route for route in base.deliveries
                  if route.destination == destination and route.resource == product), None)
    if route:
        return route.seller
    candidates = [rid for rid, person in base.residents.items()
                  if person.work != destination and person.job and product in person.job.outputs]
    return candidates[0] if len(candidates) == 1 else None


def _supply_buckets(world: "World", location: str, product: str,
                    include_orders: bool = False, *, personal: bool = False) -> list[dict[str, Any]]:
    state = world.state.village
    buckets = [{"quantity": lot.quantity, "available_at": state.minute,
                "expires_at": lot.expires_at, "confirmed": False}
               for lot in state.stock_lots.get(location, {}).get(product, [])]
    from npc_sim import village_research
    shelf = village_research.shelf_life(world, product)
    for person in state.residents.values():
        work = person.life.work if person.life else None
        if work and work.location == location and product in work.outputs:
            buckets.append({"quantity": work.outputs[product], "available_at": work.finishes_at,
                            "expires_at": work.finishes_at + shelf if shelf else None, "confirmed": False})
    economy = _state(world)
    if include_orders and economy:
        for order in _personal_orders(world) if personal else economy.orders.values():
            if order.status in ACTIVE and order.destination == location and order.product == product:
                buckets.append({"quantity": order.remaining, "available_at": order.deadline,
                                "expires_at": order.required_fresh_until + 1,
                                "confirmed": order.status == "accepted"})
    return buckets


def _allocate(buckets: list[dict[str, Any]], quantity: int, use_at: int) -> tuple[int, int]:
    taken, confirmed = 0, 0
    for bucket in sorted(buckets, key=lambda bucket: (
            bucket["expires_at"] if bucket["expires_at"] is not None else float("inf"))):
        if bucket["available_at"] > use_at or (
                bucket["expires_at"] is not None and bucket["expires_at"] <= use_at):
            continue
        amount = min(quantity - taken, bucket["quantity"])
        bucket["quantity"] -= amount
        taken += amount
        if bucket["confirmed"]:
            confirmed += amount
        if taken == quantity:
            break
    return taken, confirmed


def meal_requirements(world: "World") -> list[dict[str, Any]]:
    policy = config(world)
    if policy is None:
        return []
    state, base = world.state.village, world.scenario.village
    location = base.residents[policy.innkeeper].work
    day = state.minute // 1440
    result = []
    # Allocate existing portions once, chronologically, rather than covering both
    # breakfast and supper with the same physical meals.
    meals = _supply_buckets(world, location, "meals")
    for plan in sorted(policy.meal_plans, key=lambda plan: plan.use_minute):
        due = day * 1440 + plan.use_minute
        if due < state.minute:
            continue
        meal_kind = "breakfast" if plan.id == "breakfast" else "daily"
        eaten = sum(1 for person in state.residents.values() if person.life
                    for meal in person.life.meals.values()
                    if meal.kind == meal_kind and meal.due // 1440 == day and meal.status == "eaten")
        remaining_portions = max(0, plan.portions - eaten)
        covered, _ = _allocate(meals, remaining_portions, due)
        inputs: dict[str, int] = {}
        unmet_recipes: dict[str, int] = {}
        remaining_covered = covered + eaten
        for name, portions in plan.recipe_portions.items():
            recipe = policy.recipes[name]
            reused = min(portions, remaining_covered)
            remaining_covered -= reused
            unmet_recipes[name] = portions - reused
            batches = ceil((portions - reused) / recipe.portions)
            for product, amount in recipe.inputs.items():
                inputs[product] = inputs.get(product, 0) + batches * amount
        if plan.bread_service:
            inputs["bread"] = inputs.get("bread", 0) + max(0, plan.bread_service - eaten)
        if plan.tea_portions:
            inputs["tea"] = inputs.get("tea", 0) + max(0, plan.tea_portions - eaten)
        inputs = {product: amount + policy.stock_buffer.get(product, 0)
                  for product, amount in inputs.items() if amount or policy.stock_buffer.get(product, 0)}
        result.append({"id": f"{day}:{plan.id}", "due": due, "portions": plan.portions,
                       "eaten_portions": eaten, "remaining_portions": remaining_portions,
                       "covered_portions": covered, "inputs": inputs,
                       "unmet_recipe_portions": unmet_recipes,
                       "recipe_portions": plan.recipe_portions.copy()})
    return result


def _personal_orders(world: "World") -> list[VillageSupplierOrder]:
    from npc_sim import village_communication
    economy = _state(world)
    if economy is None:
        return []
    if not village_communication.enabled(world):
        return list(economy.orders.values())
    return [order for order in economy.orders.values() if not order.coordination_notice_id] + [
        VillageSupplierOrder.model_validate(order) for order in village_communication.learned_orders(world)]


def suggestions(world: "World", buyer: str | None = None, *, personal: bool = False) -> list[dict[str, Any]]:
    policy = config(world)
    if policy is None:
        return []
    base, state = world.scenario.village, world.state.village
    buyer = buyer or world.actor_id
    destination = base.residents[buyer].work
    requests: list[tuple[str, int, dict[str, int]]] = []
    if buyer == policy.innkeeper:
        requests += [(plan["id"], plan["due"], plan["inputs"]) for plan in meal_requirements(world)]
    economy = _state(world)
    job = base.residents[buyer].job
    if job and job.inputs and buyer != policy.innkeeper and economy:
        accepted = [order for order in (_personal_orders(world) if personal else economy.orders.values())
                    if order.status == "accepted" and order.supplier == buyer and order.loadable]
        if accepted:
            batches = max((ceil(max(0, sum(order.loadable for order in accepted if order.product == product)
                                    - usable_stock(world, destination, product,
                                                   max(order.required_fresh_until for order in accepted))
                                    - sum(person.life.work.outputs.get(product, 0)
                                          for person in state.residents.values()
                                          if person.life and person.life.work
                                          and person.life.work.location == destination)) / amount)
                           for product, amount in job.outputs.items()), default=0)
            use_at = max(state.minute, min(order.deadline for order in accepted)
                         - ceil(batches / job.batch_size) * max(job.interval, job.duration))
            if batches:
                requests.append((f"{state.minute // 1440}:production", use_at,
                                 {product: amount * batches for product, amount in job.inputs.items()}))
    reserves = policy.reserve_targets.get(destination, {})
    if reserves:
        requests.append((f"{state.minute // 1440}:reserve", (state.minute // 1440 + 1) * 1440, reserves))
    from npc_sim import village_research
    requests.extend(village_research.requests(world, buyer))
    from npc_sim import village_mining
    requests.extend(village_mining.fuel_requests(world, buyer))
    from npc_sim import village_livestock
    requests.extend(village_livestock.requests(world, buyer))
    from npc_sim import village_firewood
    requests.extend(village_firewood.requests(world, buyer))
    result = []
    supplies: dict[str, list[dict[str, Any]]] = {}
    existing: dict[str, list[dict[str, Any]]] = {}
    for key, use_at, inputs in requests:
        for product, required in inputs.items():
            stock = existing.setdefault(product, _supply_buckets(world, destination, product))
            supply = supplies.setdefault(product, _supply_buckets(world, destination, product, include_orders=True,
                                                                 personal=personal))
            available, _ = _allocate(stock, required, use_at)
            covered, incoming = _allocate(supply, required, use_at)
            quantity = max(0, required - covered)
            if product == "firewood" and village_firewood.enabled(world):
                cost = unit_price(world, product) + delivery_fee(world)
                quantity = min(quantity, max(0, state.coins[buyer] - commitments(world, buyer)) // cost)
            supplier = _supplier(world, product, destination)
            if quantity and supplier:
                deadline = max(state.minute, use_at - policy.delivery_lead_minutes)
                result.append({"buyer": buyer, "supplier": supplier, "destination": destination,
                               "product": product, "quantity": quantity, "price": unit_price(world, product),
                               "deadline": deadline, "required_fresh_until": use_at,
                               "request_key": f"{key}:{buyer}:{product}", "usable": available,
                               "confirmed_incoming": incoming,
                               "commitment": quantity * (unit_price(world, product) + delivery_fee(world))})
    return result


def _speaker_blocker(world: "World", rid: str) -> tuple[str, str] | None:
    _, state = village.context(world)
    person = state.residents[rid]
    if person.life and person.life.sleeping:
        return ("ASLEEP" if rid == world.actor_id else "RESIDENT_ASLEEP",
                f"{village.resident_name(world, rid)} is sleeping; wait until they wake before speaking.")
    if (person.life and person.life.work or person.busy_until > state.minute
            or person.available_at > state.minute):
        return ("ACTOR_BUSY" if rid == world.actor_id else "RESIDENT_BUSY",
                f"{village.resident_name(world, rid)} is occupied; wait until they are available to speak.")
    return None


def _conversation_blocker(world: "World", supplier: str) -> tuple[str, str] | None:
    base, _ = village.context(world)
    if supplier not in base.residents or supplier == world.actor_id:
        return "INVALID_SUPPLIER", "Choose a different real supplier to speak to."
    own = _speaker_blocker(world, world.actor_id)
    if own:
        return own
    if supplier not in village.local_residents(world, world.actor.location_id):
        return "OUT_OF_REACH", f"Find {village.resident_name(world, supplier)} and speak to them in person; orders are not sent remotely."
    return _speaker_blocker(world, supplier)


def _contact_attempt(world: "World", supplier: str) -> tuple[str, str] | None:
    blocked = _conversation_blocker(world, supplier)
    from npc_sim import village_communication
    if village_communication.enabled(world) and blocked and blocked[0] == "OUT_OF_REACH":
        state = world.state.village.communication
        key = f"{world.actor_id}:{supplier}:{world.actor.location_id}"
        evidence = str(sorted((nid, memory.snapshot.version)
                              for nid, memory in state.knowledge.get(world.actor_id, {}).items()
                              if memory.snapshot.author == supplier or supplier in memory.snapshot.terms.recipients))
        if state.failed_contacts.get(key) == evidence:
            return "CONTACT_ALREADY_FAILED", "No new local evidence; post one physical notice and continue useful work."
        state.failed_contacts[key] = evidence
    return blocked


def _propose(world: "World", args: dict[str, Any]) -> tuple[str, str, str, None]:
    economy = _state(world)
    if economy is None:
        return "REJECTED", "ECONOMY_DISABLED", "The economy is not configured.", None
    base, state = village.context(world)
    buyer = world.actor_id
    product, supplier = args.get("product"), args.get("supplier")
    quantity = args.get("quantity")
    deadline, fresh = args.get("deadline"), args.get("required_fresh_until")
    from npc_sim import village_research
    if (supplier not in base.residents or supplier == buyer or product not in village_research.prices(world)
            or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0
            or isinstance(deadline, bool) or not isinstance(deadline, int)
            or isinstance(fresh, bool) or not isinstance(fresh, int)
            or deadline < state.minute or fresh < deadline):
        return "REJECTED", "INVALID_ORDER", "Choose real supplier, product, quantity and future deadline/use.", None
    blocked = _contact_attempt(world, supplier)
    if blocked:
        return "REJECTED", blocked[0], blocked[1], None
    destination = base.residents[buyer].work
    key = args.get("request_key", f"manual:{buyer}:{supplier}:{product}:{deadline}:{fresh}")
    existing = next((order for order in economy.orders.values()
                     if order.request_key == key and order.status not in {"cancelled", "failed", "rejected"}), None)
    if existing:
        return "APPLIED", "ORDER_EXISTS", f"Existing order {existing.id}; no duplicate purchase.", None
    from npc_sim import village_communication
    if village_communication.enabled(world):
        return village_communication.apply(world, "village_notice_post", {
            "channel": "conversation", "listener": supplier,
            "notice": {"type": "supply_request", "subject": product,
                       "description": f"Please supply {quantity} {product} for my workplace.",
                       "recipients": [supplier], "quantity": quantity, "destination": destination,
                       "due_at": deadline, "required_fresh_until": fresh}})
    price = unit_price(world, product)
    fee = delivery_fee(world)
    source = base.residents[supplier].work
    if state.livestock and world.actor.location_id != destination and product in {"plant-scraps", "compost-material", "compost"} and usable_stock(
            world, world.actor.location_id, product, state.minute) > 0:
        source = world.actor.location_id
    order = VillageSupplierOrder(
        id=f"order-{economy.next_order}", request_key=key, buyer=buyer, supplier=supplier,
        source=source, destination=destination, product=product,
        quantity=quantity, price=price, delivery_fee=fee, deadline=deadline,
        required_fresh_until=fresh, created_at=state.minute)
    economy.next_order += 1
    economy.orders[order.id] = order
    if state.coins[buyer] - commitments(world, buyer) < quantity * (price + fee):
        order.status, order.reason = "rejected", "INSUFFICIENT_COINS"
        village.event(world, buyer, "order_rejected", order.id, "Insufficient funds including reserved delivery fees.",
                      world.actor.location_id)
        return "REJECTED", "INSUFFICIENT_COINS", f"Order {order.id} is explicitly rejected as unaffordable.", None
    village.event(world, buyer, "order_proposed", order.id,
                  f'{village.resident_name(world, buyer)} tells {village.resident_name(world, supplier)}: '
                  f'"I need {quantity} {product} delivered to {world.state.entities[destination].name} '
                  f'by {village.clock_time(deadline)} (minute {deadline}), at {price} coins each '
                  f'plus the agreed {fee}-coin delivery fee. Keep it fresh through '
                  f'{village.clock_time(fresh)} (minute {fresh}). Will you supply it?"',
                  world.actor.location_id)
    return "APPLIED", "ORDER_PROPOSED", f"Proposed {order.id}; supplier acceptance is required.", None


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    policy, economy = config(world), _state(world)
    if policy is None or economy is None:
        return "REJECTED", "ECONOMY_DISABLED", "The economy is not configured.", None
    _, state = village.context(world)
    rid = world.actor_id
    if action == "village_propose_order":
        return _propose(world, args)
    if action == "village_plan_supplies":
        supplier = args.get("supplier")
        if supplier is None:
            blocked = _speaker_blocker(world, rid)
            if blocked:
                return "REJECTED", blocked[0], blocked[1], None
            requests = suggestions(world, personal=True)
            from npc_sim import village_communication
            lead = "Post one physical request addressed to" if village_communication.enabled(world) else "Find"
            tail = "" if village_communication.enabled(world) else " and ask in person"
            text = "; ".join(f"{lead} {village.resident_name(world, request['supplier'])}{tail} "
                             f"for {request['quantity']} {request['product']} by "
                             f"{village.clock_time(request['deadline'])}."
                             for request in requests) or "Usable stock and existing commitments cover demand."
            return "APPLIED", "SUPPLIES_PLANNED", text + " No orders were placed.", None
        blocked = _contact_attempt(world, supplier)
        if blocked:
            return "REJECTED", blocked[0], blocked[1], None
        requests = [request for request in suggestions(world, personal=True) if request["supplier"] == supplier]
        from npc_sim import village_communication
        if village_communication.enabled(world):
            requests = requests[:1]
        proposed = [_propose(world, request) for request in requests]
        failed = [result for result in proposed if result[0] == "REJECTED"]
        text = "; ".join(result[2] for result in proposed) or "There is no unmet demand for this supplier."
        village.event(world, rid, action, rid, text)
        return ("REJECTED" if failed else "APPLIED",
                failed[0][1] if failed else "SUPPLIES_PLANNED", text, None)
    if action == "village_return_cargo":
        return _return_cargo(world, args)
    order = economy.orders.get(args.get("order_id", ""))
    if order is None:
        return "REJECTED", "UNKNOWN_ORDER", "Choose an existing supplier order.", None
    from npc_sim import village_communication
    if village_communication.enabled(world) and order.coordination_notice_id:
        return "REJECTED", "PHYSICAL_REPLY_REQUIRED", "Use a notice reply/update through a real channel and received version.", None
    if action not in VERBS:
        return "REJECTED", "UNKNOWN_ACTION", "Unknown economy action.", None
    if (rid != order.buyer if action == "village_cancel_order" else rid != order.supplier):
        return "REJECTED", "ORDER_AUTHORITY", "Only the buyer cancels; only its supplier responds.", None
    if action != "village_cancel_order":
        blocked = _speaker_blocker(world, rid)
        if blocked:
            return "REJECTED", blocked[0], blocked[1], None
    if action == "village_accept_order":
        if order.status == "accepted":
            return "APPLIED", "ORDER_ALREADY_ACCEPTED", "Acceptance is already recorded.", None
        if order.status not in {"proposed", "deferred"}:
            return "REJECTED", "ORDER_CLOSED", "The order is already closed.", None
        if state.minute > order.deadline:
            return "REJECTED", "ORDER_OVERDUE", "The delivery deadline has passed.", None
        if order.deferred_until is not None and state.minute < order.deferred_until:
            return "REJECTED", "ORDER_DEFERRED", "Wait until the supplier's explicit deferral time.", None
        if state.coins[order.buyer] - commitments(world, order.buyer) < order.remaining * (order.price + order.delivery_fee):
            order.status, order.reason = "rejected", "INSUFFICIENT_COINS"
            village.event(world, rid, "order_rejected", order.id, "Buyer cannot fund this additional commitment.")
            return "REJECTED", "INSUFFICIENT_COINS", "Buyer cannot fund goods and delivery fees.", None
        order.status, order.reason = "accepted", None
    elif action == "village_defer_order":
        until = args.get("until")
        if (order.status not in {"proposed", "deferred"} or isinstance(until, bool)
                or not isinstance(until, int) or not state.minute < until <= order.deadline):
            return "REJECTED", "INVALID_DEFERRAL", "Defer a proposal until a future time no later than its deadline.", None
        order.status, order.deferred_until = "deferred", until
    else:
        if order.status not in ACTIVE:
            return "REJECTED", "ORDER_CLOSED", "The order is already closed.", None
        if action == "village_reject_order" and order.status == "accepted":
            return "REJECTED", "ORDER_COMMITTED", "An accepted order must be cancelled by its buyer.", None
        order.status = "cancelled" if action == "village_cancel_order" else "rejected"
        order.reason = str(args.get("reason", order.status))
    village.event(world, rid, action, order.id, f"{order.id}: {order.status}. {order.reason or ''}")
    return "APPLIED", f"ORDER_{order.status.upper()}", f"{order.id} is {order.status}.", None


def advance(world: "World") -> None:
    economy = _state(world)
    if economy is None:
        return
    for order in economy.orders.values():
        if order.status in ACTIVE and world.state.village.minute > order.deadline:
            order.status, order.reason = "failed", "MISSED_DEADLINE"
            from npc_sim import village_communication
            if village_communication.enabled(world) and order.coordination_notice_id:
                village_communication.sync_order(world, order, "notice_expired", order.destination)
            else:
                village.event(world, order.buyer, "order_failed", order.id,
                              f"Delivery deadline missed; {order.remaining} {order.product} outstanding.",
                              order.destination)


def _return_cargo(world: "World", args: dict[str, Any]) -> tuple[str, str, str, None]:
    from npc_sim import village_goods
    state = world.state.village
    wagon = bool(args.get("wagon", False))
    if wagon:
        if state.wagon is None or state.wagon.driver != world.actor_id:
            return "REJECTED", "WAGON_DRIVER_REQUIRED", "Only the actual driver can return wagon cargo.", None
        if world.state.entities["wagon"].location != world.actor.location_id:
            return "REJECTED", "WAGON_NOT_HERE", "Walk with the actual wagon first.", None
        shipments = state.wagon.loads
    else:
        shipments = state.residents[world.actor_id].shipments
    economy = _state(world)
    returned = []
    for item in shipments[:]:
        order = economy.orders.get(getattr(item, "order_id", None)) if economy else None
        source = order.source if order else world.scenario.village.residents.get(item.seller)
        if source is not None and not isinstance(source, str):
            source = source.work
        if source != world.actor.location_id:
            continue
        if any(lot.expires_at is not None and lot.expires_at <= state.minute for lot in item.lots):
            return "REJECTED", "STALE_CARGO", "Expire stale cargo before returning its surviving lots.", None
        village_goods.put(world, source, item.resource, item.quantity, item.lots,
                          kind="returned", order_id=getattr(item, "order_id", None))
        if order:
            order.in_transit -= item.quantity
            order.returned_quantity += item.quantity
        shipments.remove(item)
        returned.append(f"{item.quantity} {item.resource}")
    if not returned:
        return "REJECTED", "WRONG_RETURN_SOURCE", "Bring cargo physically back to its original supplier.", None
    if not wagon:
        village_goods.sync_cargo(world, world.actor_id)
    village.event(world, world.actor_id, "cargo_returned", world.actor.location_id,
                  "Returned unpaid cargo: " + ", ".join(returned))
    return "APPLIED", "CARGO_RETURNED", "Returned unpaid real cargo with original freshness and ownership.", None


def production_targets(world: "World", location: str, *, personal: bool = False) -> dict[str, int]:
    policy, economy = config(world), _state(world)
    if policy is None or economy is None:
        return {}
    from npc_sim import village_research
    targets = {product: 0 for product in village_research.prices(world)}
    targets.update(policy.production_targets.get(location, {}))
    for product, amount in policy.reserve_targets.get(location, {}).items():
        targets[product] = max(targets.get(product, 0), amount)
    from npc_sim import village_firewood
    firewood_source = village_firewood.enabled(world) and (
        location == world.scenario.village.residents[world.state.village.firewood.config.supplier].work)
    if firewood_source:
        targets["firewood"] = max(targets["firewood"], village_firewood.source_reserve(world))
    for order in _personal_orders(world) if personal else economy.orders.values():
        if order.status == "accepted" and order.source == location:
            targets[order.product] = targets.get(order.product, 0) + order.loadable
    if firewood_source:
        targets["logs"] = village_firewood.log_target(world, location, targets)
    from npc_sim import village_livestock
    if location == "farm":
        for product, amount in village_livestock.feed_needs(world).items():
            targets[product] = targets.get(product, 0) + amount
    base, state = world.scenario.village, world.state.village
    if location == base.residents[base.helper].work and state.horse_fed_day < state.minute // 1440:
        # Feeding the real working horse remains a daily need, not an order
        # for an unwanted manure byproduct.
        targets["manure"] = max(targets.get("manure", 0), state.stocks.get(location, {}).get("manure", 0) + 1)
    inn = world.scenario.village.residents[policy.innkeeper].work
    if location == inn:
        plans = meal_requirements(world)
        targets["meals"] = sum(plan["remaining_portions"] for plan in plans
                               if world.state.village.minute >= plan["due"] - policy.cooking_lead_minutes)
        targets["tea"] = max(targets.get("tea", 0), sum(
            plan["inputs"].get("tea", 0) for plan in plans))
    return targets


def _production(world: "World", action: str) -> tuple[dict[str, int], dict[str, int], int]:
    from npc_sim import village_life
    if action not in village_life.PRODUCTION:
        return {}, {}, 1
    if action == "village_split_firewood":
        from npc_sim import village_firewood
        job = village_firewood.production_job(world)
        return (job.inputs, job.outputs, job.batch_size) if (
            job and world.actor.location_id == job.location) else ({}, {}, 1)
    resident = world.scenario.village.residents[world.actor_id]
    job = resident.job
    if job is None:
        return {}, {}, 1
    if action == "village_cook_fish":
        return {"fish": 1}, {"meals": 2}, job.batch_size
    from npc_sim import village_services
    if action in village_services.JOBS:
        return {}, {village_services.JOBS[action][1]: 2}, 1
    return job.inputs, job.outputs, job.batch_size


def production_batch_limit(world: "World", action: str, *, personal: bool = False) -> int:
    policy = config(world)
    inputs, outputs, batch_size = _production(world, action)
    if policy is None:
        return batch_size
    location, now = world.actor.location_id, world.state.village.minute
    targets = production_targets(world, location, personal=personal)
    limits = [batch_size]
    economy = _state(world)
    from npc_sim import village_livestock
    livestock_feed = village_livestock.feed_needs(world) if location == "farm" else {}
    for product, amount in outputs.items():
        if product not in targets:
            continue
        due = min((plan["due"] for plan in meal_requirements(world)), default=now) if (
            product == "meals" and location == world.scenario.village.residents[policy.innkeeper].work) else now
        stock = usable_stock(world, location, product, due)
        if product == "meals" and location == world.scenario.village.residents[policy.innkeeper].work:
            recipe = "fish" if action == "village_cook_fish" else "bread"
            recipe_demand = sum(plan["unmet_recipe_portions"].get(recipe, 0)
                                for plan in meal_requirements(world)
                                if now >= plan["due"] - policy.cooking_lead_minutes)
            limits.append(recipe_demand // amount)
        committed = sum(person.life.work.outputs.get(product, 0)
                        for person in world.state.village.residents.values()
                        if person.life and person.life.work and person.life.work.location == location)
        short = max(0, targets[product] - stock - committed)
        orders = sorted((order for order in (_personal_orders(world) if personal else economy.orders.values())
                         if order.status == "accepted" and order.source == location
                         and order.product == product and order.loadable),
                        key=lambda order: order.required_fresh_until) if economy else []
        if orders and product != "meals":
            buckets = _supply_buckets(world, location, product)
            accepted_shortfall = 0
            for order in orders:
                covered, _ = _allocate(buckets, order.loadable, order.required_fresh_until)
                accepted_shortfall += order.loadable - covered
            reserve = max(policy.reserve_targets.get(location, {}).get(product, 0),
                          policy.production_targets.get(location, {}).get(product, 0))
            if product == "firewood":
                from npc_sim import village_firewood
                if village_firewood.enabled(world):
                    reserve = max(reserve, village_firewood.source_reserve(world))
            reserve += livestock_feed.get(product, 0)
            available_reserve = sum(bucket["quantity"] for bucket in buckets
                                    if bucket["expires_at"] is None or bucket["expires_at"] > now)
            short = accepted_shortfall + max(0, reserve - available_reserve)
        limits.append(ceil(short / amount) if (
            product in livestock_feed and livestock_feed[product] or action == "village_split_firewood"
        ) else short // amount)
    return min(limits)


def production_blocker(world: "World", action: str, *, personal: bool = False) -> tuple[str, str] | None:
    policy = config(world)
    if policy is None:
        return None
    inputs, outputs, _ = _production(world, action)
    if not outputs:
        return None
    location = world.actor.location_id
    inn = world.scenario.village.residents[policy.innkeeper].work
    if location == inn and "meals" in outputs:
        plans = meal_requirements(world)
        if not any(plan["due"] - policy.cooking_lead_minutes <= world.state.village.minute <= plan["due"]
                   for plan in plans):
            return "OUTSIDE_PREPARATION_WINDOW", "Cook near the next planned service, not all day."
    if production_batch_limit(world, action, personal=personal) == 0:
        return "DEMAND_MET", "Fresh stock and committed production cover accepted demand and reserve targets."
    reserves = policy.reserve_targets.get(location, {})
    economy = _state(world)
    accepted = any(order.status == "accepted" and order.source == location and order.product in outputs
                   and order.loadable > 0 for order in (_personal_orders(world) if personal else economy.orders.values())) if economy else False
    for product, amount in inputs.items():
        if (product in reserves and not accepted and usable_stock(world, location, product,
                world.state.village.minute) - amount * production_batch_limit(world, action, personal=personal) < reserves[product]):
            return "RAW_MATERIAL_RESERVE", f"Preserve the configured {product} reserve; no accepted demand justifies conversion."
    if economy:
        for product in outputs:
            shelf = world.scenario.village.spoil_after.get(product)
            orders = [order for order in (_personal_orders(world) if personal else economy.orders.values()) if order.status == "accepted"
                      and order.source == location and order.product == product and order.loadable]
            if shelf and orders and all(
                    world.state.village.minute + world.scenario.village.residents[world.actor_id].job.duration
                    + shelf <= order.required_fresh_until for order in orders):
                return "TOO_EARLY_FOR_FRESHNESS", f"Wait: newly produced {product} would expire before its ordered use."
    return None


def delivery_runs(world: "World", carrier: str | None = None) -> list[dict[str, Any]]:
    from npc_sim.village_services import foot_capacity
    state, economy = world.state.village, _state(world)
    if economy is None:
        return []
    carrier = carrier or world.actor_id
    wagon = bool(state.wagon and state.wagon.driver == carrier)
    loads = state.wagon.loads if wagon else state.residents[carrier].shipments
    capacity = (6 if wagon else foot_capacity(world, carrier)) - sum(item.quantity for item in loads)
    location = village.resident_location(world, carrier)
    runs: dict[str, dict[str, Any]] = {}
    for order in sorted(economy.orders.values(), key=lambda order: (order.deadline, order.id)):
        if order.status != "accepted" or order.source != location or order.loadable <= 0:
            continue
        run = runs.setdefault(order.destination, {"destination": order.destination,
                              "goods": {}, "orders": {}, "capacity": max(0, capacity),
                              "delivery_fee": order.delivery_fee})
        remaining = capacity - sum(run["goods"].values())
        used_product = run["goods"].get(order.product, 0)
        amount = min(order.loadable, max(0, remaining),
                     max(0, usable_stock(world, location, order.product, order.required_fresh_until) - used_product))
        if amount:
            run["goods"][order.product] = used_product + amount
            run["orders"][order.id] = amount
    return [run for run in runs.values() if run["goods"]]


def _report(world: "World") -> dict[str, Any]:
    policy, economy = config(world), _state(world)
    if policy is None or economy is None:
        return {}
    state = world.state.village
    products: dict[str, dict[str, dict[str, int]]] = {}
    for entry in economy.movements:
        counts = products.setdefault(entry.location, {}).setdefault(entry.product, {})
        counts[entry.kind] = counts.get(entry.kind, 0) + entry.quantity
    inventory = {}
    for location, stock in state.stocks.items():
        inventory[location] = {product: {"remaining": amount,
            "usable_now": usable_stock(world, location, product, state.minute),
            "expires_before_next_service": max(0, amount - usable_stock(
                world, location, product, min((plan["due"] for plan in meal_requirements(world)),
                                              default=state.minute))),
            "inventory_value": amount * unit_price(world, product)}
            for product, amount in stock.items()}
    cash = {}
    for account, balance in state.coins.items():
        counts = {"gross_sales": 0, "purchases": 0, "delivery_fees_received": 0,
                  "delivery_fees_paid": 0, "subsidies_received": 0, "subsidies_paid": 0}
        for payment in economy.payments:
            if payment.kind in {"goods", "meal", "tea"}:
                if payment.payee == account:
                    counts["gross_sales"] += payment.amount
                if payment.payer == account:
                    counts["purchases"] += payment.amount
            elif payment.kind == "delivery_fee":
                if payment.payee == account:
                    counts["delivery_fees_received"] += payment.amount
                if payment.payer == account:
                    counts["delivery_fees_paid"] += payment.amount
            elif payment.kind == "subsidy":
                if payment.payee == account:
                    counts["subsidies_received"] += payment.amount
                if payment.payer == account:
                    counts["subsidies_paid"] += payment.amount
        cash[account] = {**counts, "balance": balance, "committed": commitments(world, account),
                         "net_cash_change": balance - economy.initial_coins.get(account, 0)}
    cargo = []
    carriers = [(rid, person.shipments) for rid, person in state.residents.items()]
    if state.wagon:
        carriers.append(("wagon", state.wagon.loads))
    for carrier, shipments in carriers:
        for shipment in shipments:
            cargo.append({"carrier": carrier, **shipment.model_dump(),
                          "inventory_value": shipment.quantity * unit_price(world, shipment.resource)})
    shortages = {location: {product: max(0, amount - usable_stock(world, location, product, state.minute))
                           for product, amount in targets.items()}
                 for location, targets in policy.reserve_targets.items()}
    return {"orders": [{**order.model_dump(), "remaining": order.remaining,
                         "loadable": order.loadable, "commitment": order.commitment}
                        for order in economy.orders.values()],
            "suggestions": suggestions(world), "meal_coverage": meal_requirements(world),
            "delivery_runs": delivery_runs(world), "products": products, "inventory": inventory,
            "cargo": cargo, "cash": cash, "reserve_shortages": shortages,
            "production_targets": {location: production_targets(world, location) for location in state.stocks},
            "valuation_basis": "Current configured product unit prices; inventory value is not profit.",
            "overproduction": [{"location": location, "product": product, "surplus": amount - target}
                               for location, stock in state.stocks.items()
                               for product, amount in stock.items()
                               if (target := production_targets(world, location).get(product)) is not None
                               and amount > target]}


def observation(world: "World") -> dict[str, Any]:
    report = _report(world)
    if not report:
        return {}
    rid = world.actor_id
    policy = config(world)
    if policy is None:
        return {}
    base, state = village.context(world)
    wagon = state.wagon
    from npc_sim import village_communication
    if village_communication.enabled(world):
        report["orders"] = [{**order.model_dump(), "remaining": order.remaining, "loadable": order.loadable,
                             "commitment": order.commitment} for order in _personal_orders(world)]
        report["suggestions"] = suggestions(world, personal=True)
        report["delivery_runs"] = []
        report["production_targets"] = {}
        report["overproduction"] = []
        if rid in report["cash"]:
            report["cash"][rid]["committed"] = sum(
                order.commitment for order in _personal_orders(world) if order.buyer == rid)
        report["cargo"] = [item for item in report["cargo"] if item["carrier"] == rid or (
            item["carrier"] == "wagon" and wagon and wagon.driver == rid)]
    locations = ({world.actor.location_id} if village_communication.enabled(world)
                 else {world.actor.location_id, base.residents[rid].work})
    report["orders"] = [order for order in report["orders"] if rid in {order["buyer"], order["supplier"]}]
    visible_ids = {order["id"] for order in report["orders"]}
    for run in report["delivery_runs"]:
        run["orders"] = {oid: quantity for oid, quantity in run["orders"].items() if oid in visible_ids}
    for name in ("inventory", "products", "reserve_shortages", "production_targets"):
        report[name] = {location: values for location, values in report[name].items() if location in locations}
    report["cash"] = {rid: report["cash"][rid]} if rid in report["cash"] else {}
    report["cargo"] = [item for item in report["cargo"] if rid in {
        item["carrier"], item["buyer"], item["seller"]} or (
            item["carrier"] == "wagon" and wagon is not None and wagon.driver == rid)]
    report["overproduction"] = [item for item in report["overproduction"] if item["location"] in locations]
    if rid != policy.innkeeper:
        report["meal_coverage"] = []
    for request in report["suggestions"]:
        blocked = _conversation_blocker(world, request["supplier"])
        request["order_availability"] = "blocked" if blocked else "available"
        request["order_reason"] = blocked[1] if blocked else None
        request["order_code"] = blocked[0] if blocked else None
    report["ordering_guidance"] = village_communication.GUIDANCE if village_communication.enabled(world) else (
        "Planning alone places no orders. Find each supplier and speak face-to-face using "
        "village_plan_supplies with supplier, or village_propose_order. Suppliers respond "
        "separately to their own proposals; upstream input suggestions also require an in-person request.")
    return report


def monitor(world: "World") -> dict[str, Any]:
    """Flatten persistent accounting for the operator dashboard, not NPC knowledge."""
    report = _report(world)
    if not report:
        return {"orders": [], "products": [], "accounts": []}
    policy = config(world)
    state = world.state.village
    orders = [{"id": order["id"], "buyer": order["buyer"], "seller": order["supplier"],
               "product": order["product"], "quantity": order["quantity"],
               "delivered": order["delivered_quantity"], "status": order["status"],
               "deadline": order["deadline"]} for order in report["orders"]]
    products = [{"location": location, "product": product, "quantity": values["remaining"],
                 "usable": values["usable_now"], "expiring": values["expires_before_next_service"],
                 "reserve": policy.reserve_targets.get(location, {}).get(product, 0)}
                for location, stock in report["inventory"].items() for product, values in stock.items()]
    accounts = []
    for owner, cash in report["cash"].items():
        locations = {resident.work for rid, resident in world.scenario.village.residents.items() if rid == owner}
        if owner == "iron-trader":
            locations.add("village-square")
        value = sum(stock["inventory_value"] for location in locations
                    for stock in report["inventory"].get(location, {}).values())
        value += sum(item["inventory_value"] for item in report["cargo"] if item["seller"] == owner)
        accounts.append({"owner": owner, "sales": cash["gross_sales"], "purchases": cash["purchases"],
                         "fees": cash["delivery_fees_received"] - cash["delivery_fees_paid"],
                         "subsidies": cash["subsidies_received"] - cash["subsidies_paid"],
                         "net_cash": cash["net_cash_change"], "inventory_value": value})
    return {"orders": orders, "products": products, "accounts": accounts}


def actions(world: "World") -> list[dict[str, Any]]:
    from npc_sim import village_research
    economy = _state(world)
    if config(world) is None or economy is None:
        return []
    base, state = village.context(world)
    result = []

    def add(verb: str, text: str, properties: dict[str, Any], *,
            required: list[str] | None = None, blocked: tuple[str, str] | None = None) -> None:
        result.append({"id": verb, "description": text, "targets": [],
                       "arguments_schema": {"type": "object", "properties": properties,
                                            "required": list(properties) if required is None else required,
                                            "additionalProperties": False},
                       "availability": "blocked" if blocked else "available",
                       "reason": blocked[1] if blocked else None})

    suppliers = sorted(set(base.residents) - {world.actor_id})
    nearby = [rid for rid in suppliers if rid in village.local_residents(world, world.actor.location_id)]
    ready = [rid for rid in nearby if _conversation_blocker(world, rid) is None]
    manual_blocked = (_speaker_blocker(world, world.actor_id) or (
        _conversation_blocker(world, nearby[0]) if nearby else (
            "OUT_OF_REACH", "Find a supplier and speak face-to-face; no supplier is here."))) if not ready else None
    add("village_plan_supplies", "Read demand suggestions without ordering. With supplier, tell only that present supplier your planned requirements.",
        {"supplier": {"type": "string", "enum": suppliers}}, required=[],
        blocked=_speaker_blocker(world, world.actor_id))
    add("village_propose_order", "Tell a present, awake, available supplier the quantity, price and deadline; supplier accepts separately.",
        {"supplier": {"type": "string", "enum": ready or suppliers},
         "product": {"type": "string", "enum": sorted(village_research.prices(world))},
         "quantity": {"type": "integer", "minimum": 1},
         "deadline": {"type": "integer", "minimum": state.minute},
         "required_fresh_until": {"type": "integer", "minimum": state.minute}},
        blocked=manual_blocked)
    for verb in ("village_accept_order", "village_reject_order", "village_defer_order", "village_cancel_order"):
        from npc_sim import village_communication
        ids = [order.id for order in economy.orders.values()
               if not (village_communication.enabled(world) and order.coordination_notice_id)
               if order.status in ACTIVE and (
                   order.buyer == world.actor_id if verb == "village_cancel_order" else (
                       order.supplier == world.actor_id and (
                           verb == "village_accept_order" or order.status != "accepted")))]
        if ids:
            blockers: dict[str, tuple[str, str] | None] = {}
            for oid in ids:
                order = economy.orders[oid]
                blocked = _speaker_blocker(world, world.actor_id) if verb != "village_cancel_order" else None
                if blocked is None and verb == "village_accept_order" and order.status != "accepted":
                    if state.minute > order.deadline:
                        blocked = "ORDER_OVERDUE", "The delivery deadline has passed."
                    elif order.deferred_until is not None and state.minute < order.deferred_until:
                        blocked = "ORDER_DEFERRED", "Wait until the explicit supplier deferral time."
                    elif (state.coins[order.buyer] - commitments(world, order.buyer)
                          < order.remaining * (order.price + order.delivery_fee)):
                        blocked = "INSUFFICIENT_COINS", "Buyer cannot fund goods and delivery fees."
                blockers[oid] = blocked
            ready_ids = [oid for oid in ids if blockers[oid] is None]
            blocked = None if ready_ids else blockers[ids[0]]
            ids = ready_ids or ids
            properties = {"order_id": {"type": "string", "enum": ids}}
            if verb == "village_defer_order":
                properties["until"] = {"type": "integer", "minimum": state.minute + 1}
            add(verb, "Respond separately to your own existing order; this does not create an upstream order.",
                properties, blocked=blocked)
    person = state.residents[world.actor_id]
    if person.shipments or (state.wagon and state.wagon.driver == world.actor_id and state.wagon.loads):
        add("village_return_cargo", "Physically return unpaid cargo to its original supplier.",
            {"wagon": {"type": "boolean"}})
    return result

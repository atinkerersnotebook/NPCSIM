"""Conservative food provenance and finite farm recycling; enabled with livestock."""

from math import ceil

from npc_sim import village, village_economy
from npc_sim.models import Entity, VillageStockLot

SCRAPS, MATERIAL, COMPOST = "plant-scraps", "compost-material", "compost"
PRICES = {SCRAPS: 1, MATERIAL: 1, COMPOST: 1}
PLANTS = {"grain", "flour", "bread", "herbs", "tea"}
ANIMAL = {"fish", "milk", "eggs", "manure", "hay"}


def fixtures():
    return {
        "compost-pile": Entity(name="Farm compost pile", kind="tool", location="farm", max_health=100,
                              description="Processes actual spoiled-food material or safe plant scraps into compost fertilizer."),
        "compost-fork": Entity(name="Compost fork", kind="tool", location="farm", takable=True, max_health=100,
                              description="A real hand-held tool for turning the farm compost pile."),
    }


def enabled(world):
    state = village.context(world)[1].livestock
    return bool(state and state.config.enabled)


def food(world, resource):
    from npc_sim import village_research
    project = village_research.discoveries(world).get(resource)
    return resource in PLANTS | {"fish", "milk", "eggs", "meals", SCRAPS} or (
        project is not None and project.proposal.kind in {"food", "crop"})


def plant_recipe(world, resource, seen=None):
    if resource in ANIMAL or resource in {MATERIAL, COMPOST, "meals"}:
        return False
    if resource in PLANTS or resource == SCRAPS:
        return True
    from npc_sim import village_research
    seen = set(seen or [])
    if resource in seen:
        return False
    project = village_research.discoveries(world).get(resource)
    return bool(project and project.proposal.kind in {"food", "crop"} and project.proposal.inputs
                and all(plant_recipe(world, key, seen | {resource}) for key in project.proposal.inputs))


def safe_lot(world, resource, lot):
    minute = village.context(world)[1].minute
    if lot.expires_at is not None and lot.expires_at <= minute or resource in ANIMAL:
        return False
    if resource == SCRAPS and not lot.ingredients:
        return False
    if resource == "meals":
        return bool(lot.ingredients and all(plant_recipe(world, item) for item in lot.ingredients))
    return plant_recipe(world, resource) and all(plant_recipe(world, item) for item in lot.ingredients)


def protected(world, location, product):
    quantity = village_economy.protected_quantity(world, location, product)
    base, _ = village.context(world)
    policy = village_economy.config(world)
    if policy and location == base.residents[policy.innkeeper].work:
        quantity += sum(plan["inputs"].get(product, 0) for plan in village_economy.meal_requirements(world))
        if product == "meals":
            quantity += sum(plan["remaining_portions"] for plan in village_economy.meal_requirements(world))
    if location == "farm" and product == "grain":
        from npc_sim import village_livestock
        quantity += village_livestock.feed_needs(world).get("grain", 0)
    if not policy or location != base.residents[policy.innkeeper].work:
        for resident in base.residents.values():
            job = resident.job
            if job and job.location == location and product in job.inputs:
                targets = village_economy.production_targets(world, location)
                batches = max((ceil(max(0, targets.get(output, 0) - village_economy.usable_stock(
                    world, location, output, village.context(world)[1].minute)) / amount)
                               for output, amount in job.outputs.items()), default=0)
                quantity += batches * job.inputs[product]
    return quantity


def recover(world, location, resource, lots):
    if not enabled(world) or not food(world, resource):
        return
    from npc_sim import village_goods
    recovered = [VillageStockLot(quantity=lot.quantity, produced_at=village.context(world)[1].minute,
                                 ingredients=[resource], source_resource=resource) for lot in lots]
    village_goods.put(world, location, MATERIAL, sum(lot.quantity for lot in lots), recovered, kind="produced")


def supplier(world, product):
    base, state = village.context(world)
    candidates = [rid for rid, person in base.residents.items()
                  if village_economy.usable_stock(world, person.work, product, state.minute) > 0
                  and person.work != "farm"]
    return sorted(candidates)[0] if candidates else None


def requests(world, actor):
    state = village.context(world)[1]
    livestock = state.livestock
    if not enabled(world) or livestock is None or actor != livestock.config.keeper:
        return []
    required = {}
    if not livestock.hens.fed or livestock.hens.day != state.minute // 1440:
        required[SCRAPS] = ceil(livestock.config.hens / 4) // 3
    compost = village_economy.usable_stock(world, "farm", COMPOST, state.minute)
    active = sum(chunk.operation == "compost" for chunk in livestock.chunks if chunk.status == "active")
    required[MATERIAL] = max(0, 2 - compost - active) * 2
    return [(f"{state.minute // 1440}:recycling", state.minute + 180, required)] if any(required.values()) else []


def hen_ration(world):
    state = village.context(world)[1].livestock
    if state is None:
        raise ValueError("Livestock is not enabled")
    amount = ceil(state.config.hens / 4)
    scraps = min(amount // 3, max(0, village_economy.usable_stock(
        world, "farm", SCRAPS, village.context(world)[1].minute) -
        village_economy.protected_quantity(world, "farm", SCRAPS)))
    # Only deliberately validated scraps enter this resource; still verify provenance.
    lots = village.context(world)[1].stock_lots.get("farm", {}).get(SCRAPS, [])
    safe = sum(lot.quantity for lot in lots if safe_lot(world, SCRAPS, lot))
    scraps = min(scraps, safe)
    return amount - scraps, scraps

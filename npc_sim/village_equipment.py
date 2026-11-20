"""Generic location-owned production and communal capital assets."""

from math import ceil
from typing import TYPE_CHECKING, Any

from npc_sim import village, village_goods, village_life
from npc_sim.village_equipment_models import (
    VillageEquipmentAsset, VillageEquipmentAssetState, VillageEquipmentConfig,
    VillageEquipmentExpenditure, VillageEquipmentProject, VillageEquipmentState,
)

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"village_upgrade_equipment"}


def recommended_config(scenario: Any) -> dict[str, Any]:
    base = scenario.village
    if base is None or not base.expanded_rules:
        raise ValueError("Village equipment upgrades require an expanded village")
    smith, miller = base.smith, base.miller
    baker = next(rid for rid, resident in base.residents.items() if "baking" in resident.skills)
    assets = {
        "mill-drive": {
            "entity": base.mill_equipment, "location": base.mill, "owner": miller,
            "category": "production", "initial_tier": 0, "tiers": [
                {"level": 0, "name": "Worn drive"},
                {"level": 1, "name": "Balanced drive", "duration": 30, "cost": 12,
                 "supplies": {"iron": 1}, "duration_factor": 0.75, "batch_size": 3},
            ],
        },
        "bakery-oven": {
            "entity": "oven", "location": base.residents[baker].work, "owner": baker,
            "category": "production", "tiers": [
                {"level": 0, "name": "Brick oven"},
                {"level": 1, "name": "Drafted oven", "duration": 45, "cost": 15,
                 "supplies": {"planks": 1}, "duration_factor": 0.8, "batch_size": 3,
                 "fuel_factor": 0.75},
            ],
        },
        "village-wagon": {
            "entity": "wagon", "location": base.residents[smith].work,
            "category": "transport", "communal": True,
            "users": [base.courier, smith], "funders": [base.courier, smith],
            "tiers": [
                {"level": 0, "name": "Working wagon", "capacity": 6},
                {"level": 1, "name": "Reinforced wagon", "duration": 60, "cost": 20,
                 "supplies": {"wagon-parts": 1, "iron": 1}, "capacity": 8},
            ],
        },
        "village-pump": {
            "entity": "village-pump", "location": "village-square", "category": "water",
            "communal": True, "users": [rid for rid in base.residents],
            "funders": [base.courier, base.smith],
            "tiers": [
                {"level": 0, "name": "Hand pump", "capacity": 4},
                {"level": 1, "name": "Deepened pump", "duration": 60, "cost": 25,
                 "supplies": {"planks": 1, "iron": 1}, "capacity": 8},
            ],
        },
        "village-well": {
            "entity": base.well_site, "location": base.well_site, "category": "water",
            "communal": True, "users": [rid for rid in base.residents],
            "funders": [base.courier, smith],
            "tiers": [
                {"level": 0, "name": "Lined village well", "capacity": 6},
                {"level": 1, "name": "Covered deep well", "duration": 60, "cost": 30,
                 "supplies": {"planks": 1, "iron": 1}, "capacity": 12},
            ],
        },
    }
    if "smelting-furnace" in scenario.entities:
        assets["smelting-furnace"] = {
            "entity": "smelting-furnace", "location": base.residents[smith].work,
            "category": "production", "owner": smith, "tiers": [
                {"level": 0, "name": "Bloomery furnace"},
                {"level": 1, "name": "Efficient furnace", "duration": 45, "cost": 18,
                 "supplies": {"iron": 2}, "duration_factor": 0.75, "fuel_factor": 0.75},
            ],
        }
    return {"assets": assets}


def _validate(scenario: Any, config: VillageEquipmentConfig) -> None:
    base = scenario.village
    if config.enabled and base is not None and not (base.ai_controlled and base.daily_life and base.time_based_turns):
        raise ValueError("Equipment upgrades require AI-controlled timed daily-life villages")
    entities: dict[str, str] = {}
    for identifier, asset in config.assets.items():
        if not identifier.strip():
            raise ValueError("Equipment asset identifiers cannot be blank")
        if asset.entity not in scenario.entities:
            raise ValueError(f"Unknown equipment entity: {asset.entity}")
        if asset.location not in scenario.entities or scenario.entities[asset.location].kind != "location":
            raise ValueError(f"Unknown equipment location: {asset.location}")
        entity = scenario.entities[asset.entity]
        if entity.kind != "location" and entity.location != asset.location:
            raise ValueError(f"Equipment {asset.entity} must start at {asset.location}")
        if entity.kind == "location" and asset.entity != asset.location:
            raise ValueError("Location assets must name their own location")
        if asset.entity in entities:
            raise ValueError(f"Equipment entity {asset.entity} is configured more than once")
        entities[asset.entity] = identifier
        if base is not None and asset.owner is not None and asset.owner not in base.residents:
            raise ValueError("Equipment owner must be a resident")
        if base is not None and any(actor not in base.residents for actor in asset.users + asset.funders):
            raise ValueError("Equipment users and funders must be residents")
        for tier in asset.tiers[1:]:
            if base is not None and (not tier.duration or tier.duration % 5):
                raise ValueError("Every upgrade needs positive five-minute work duration")
            if base is not None and any(resource not in base.prices for resource in tier.supplies):
                raise ValueError("Upgrade supplies must be real priced resources")
            elif base is None and tier.supplies:
                raise ValueError("Adventure asset tiers cannot consume village-priced supplies")


def validate_config(scenario: Any) -> None:
    config = scenario.equipment or (scenario.village.equipment if scenario.village else None)
    if config is not None:
        _validate(scenario, config)


def initial_state(scenario: Any) -> VillageEquipmentState | None:
    config = scenario.village.equipment if scenario.village else None
    if config is None:
        return None
    return VillageEquipmentState(config=config.model_copy(deep=True), assets={
        identifier: VillageEquipmentAssetState(tier=asset.initial_tier)
        for identifier, asset in config.assets.items()
    })


def initial_world_state(scenario: Any) -> VillageEquipmentState | None:
    config = scenario.equipment
    if config is None:
        return None
    _validate(scenario, config)
    return VillageEquipmentState(config=config.model_copy(deep=True), assets={
        identifier: VillageEquipmentAssetState(tier=definition.initial_tier)
        for identifier, definition in config.assets.items()
    })


def configure(world: "World", value: dict[str, Any]) -> None:
    config = VillageEquipmentConfig.model_validate(value)
    _validate(world.scenario, config)
    _, state = village.context(world)
    if state.equipment and any(project.status == "active" for project in state.equipment.projects.values()):
        raise ValueError("Finish active equipment upgrades before reconfiguring capital assets")
    preserved = state.equipment.assets if state.equipment else {}
    state.equipment = VillageEquipmentState(config=config, assets={
        identifier: preserved.get(identifier, VillageEquipmentAssetState(tier=asset.initial_tier))
        for identifier, asset in config.assets.items()
    })


def _state(world: "World") -> VillageEquipmentState:
    state = world.state.equipment
    if state is None and world.state.village is not None:
        state = world.state.village.equipment
    if state is None:
        raise ValueError("Village equipment upgrades are not enabled")
    return state


def enabled(world: "World") -> bool:
    state = world.state.equipment
    if state is None and world.state.village is not None:
        state = world.state.village.equipment
    return state is not None and state.config.enabled


def asset(world: "World", entity: str) -> tuple[str, VillageEquipmentAsset, VillageEquipmentAssetState] | None:
    if not enabled(world):
        return None
    state = _state(world)
    for identifier, definition in state.config.assets.items():
        if definition.entity == entity:
            return identifier, definition, state.assets[identifier]
    return None


def tier(world: "World", entity: str):
    found = asset(world, entity)
    return found[1].tiers[found[2].tier] if found else None


def production_profile(world: "World", entity: str | None, duration: int, batch_size: int) -> tuple[int, int]:
    current = tier(world, entity) if entity else None
    if current is None:
        return duration, batch_size
    adjusted = max(5, int(ceil(duration * current.duration_factor / 5) * 5))
    return adjusted, max(batch_size, current.batch_size)


def fuel_units(world: "World", entity: str | None, units: int) -> int:
    current = tier(world, entity) if entity else None
    if current is None or not units:
        return units
    return max(1, int(ceil(units * current.fuel_factor)))


def transport_capacity(world: "World", default: int) -> int:
    found = tier(world, "wagon")
    return found.capacity if found and found.capacity else default


def water_capacity(world: "World", default: int = 0) -> int:
    found = tier(world, "village-pump")
    return found.capacity if found and found.capacity else default


def blocked_until(world: "World", actor: str) -> int | None:
    if not enabled(world):
        return None
    state = _state(world)
    return next((project.finishes_at for project in state.projects.values()
                 if project.worker == actor and project.status == "active"), None)


def equipment_reserved(world: "World", entity: str) -> bool:
    return enabled(world) and any(project.asset == identifier and project.status == "active"
                                 for identifier, _definition in _state(world).config.assets.items()
                                 if _definition.entity == entity
                                 for project in _state(world).projects.values())


def _authorized(asset: VillageEquipmentAsset, actor: str) -> bool:
    return asset.owner == actor if not asset.communal else actor in asset.users


def _blocker(world: "World", identifier: str, payer: str | None = None) -> tuple[str, str] | None:
    if world.state.village is None:
        return "UPGRADE_NOT_AVAILABLE", "This world asset has no village-funded upgrade policy."
    config, state = village.context(world)
    equipment = _state(world)
    definition = equipment.config.assets.get(identifier)
    if definition is None:
        return "UNKNOWN_EQUIPMENT", "Choose configured village equipment."
    if not _authorized(definition, world.actor_id):
        return "NOT_AUTHORIZED", "This resident may not upgrade that asset."
    current = equipment.assets[identifier].tier
    if current + 1 >= len(definition.tiers):
        return "MAXIMUM_TIER", "This equipment is already at its maximum tier."
    if equipment_reserved(world, definition.entity):
        return "EQUIPMENT_RESERVED", "This asset already has an active upgrade."
    if world.actor.location_id != definition.location:
        return "NOT_PRESENT", "Travel to the actual equipment location before upgrading it."
    entity = world.state.entities[definition.entity]
    if entity.kind == "location" and identifier == "village-well" and not state.well_dug:
        return "WELL_INCOMPLETE", "Complete the actual communal well before upgrading it."
    if entity.broken or entity.hidden or (entity.kind != "location" and entity.location != definition.location):
        return "EQUIPMENT_UNAVAILABLE", "Repair and return the actual equipment before upgrading it."
    for resident, other in state.residents.items():
        if (other.life and other.life.work and other.life.work.equipment == definition.entity
                or resident != world.actor_id and other.available_at > state.minute
                and config.residents[resident].job
                and config.residents[resident].job.equipment == definition.entity):
            return "EQUIPMENT_IN_USE", "Finish active production at this asset before upgrading it."
    if (definition.entity == "wagon" and state.wagon is not None
            and (state.wagon.driver is not None or state.wagon.loads or state.wagon.automatic)):
        return "WAGON_IN_USE", "Unload and park the communal wagon before upgrading it."
    upgrade = definition.tiers[current + 1]
    account = definition.owner if definition.owner is not None else payer
    if account not in state.coins or (definition.communal and account not in definition.funders):
        return "INVALID_FUNDER", "Choose an authorized village funder."
    if state.coins[account] < upgrade.cost + definition.minimum_reserve:
        return "INSUFFICIENT_COINS", "The funder must retain the configured cash reserve."
    for resource, quantity in upgrade.supplies.items():
        if state.stocks.get(definition.location, {}).get(resource, 0) < quantity:
            return "MISSING_SUPPLIES", f"Needs {quantity} {resource} at the equipment location."
    person = state.residents[world.actor_id]
    if person.life and (person.life.sleeping or person.life.work):
        return "WORK_COMMITTED", "Finish existing sleep or committed production first."
    return None


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    if action != "village_upgrade_equipment":
        raise ValueError(f"Unsupported equipment action: {action}")
    identifier, payer = args["asset"], args["payer"]
    blocker = _blocker(world, identifier, payer)
    if blocker:
        return "REJECTED", blocker[0], blocker[1], None
    config, state = village.context(world)
    equipment = _state(world)
    definition = equipment.config.assets[identifier]
    runtime = equipment.assets[identifier]
    upgrade = definition.tiers[runtime.tier + 1]
    account = definition.owner if definition.owner is not None else payer
    for resource, quantity in upgrade.supplies.items():
        village_goods.take(world, definition.location, resource, quantity)
    state.coins[account] -= upgrade.cost
    finishes_at = state.minute + upgrade.duration * village_life.cooldown_factor(world, world.actor_id)
    project = VillageEquipmentProject(
        id=f"equipment-upgrade:{len(equipment.projects) + 1}", asset=identifier,
        worker=world.actor_id, payer=account, from_tier=runtime.tier, to_tier=runtime.tier + 1,
        started_at=state.minute, finishes_at=finishes_at, cost=upgrade.cost,
        supplies=upgrade.supplies.copy())
    equipment.projects[project.id] = project
    equipment.expenditures.append(VillageEquipmentExpenditure(
        minute=state.minute, asset=identifier, payer=account, cost=upgrade.cost,
        to_tier=project.to_tier))
    person = state.residents[world.actor_id]
    person.available_at = finishes_at
    person.activity = f"Upgrading {world.state.entities[definition.entity].name}"
    village.event(world, world.actor_id, action, definition.entity,
                  f"{person.activity} until {village.clock_time(finishes_at)}; {account} pays {upgrade.cost} coins and supplies are reserved.")
    return "APPLIED", "EQUIPMENT_UPGRADE_STARTED", person.activity, None


def advance(world: "World") -> None:
    if not enabled(world):
        return
    _, state = village.context(world)
    equipment = _state(world)
    for project in equipment.projects.values():
        if project.status != "active" or state.minute < project.finishes_at:
            continue
        runtime = equipment.assets[project.asset]
        if runtime.tier != project.from_tier:
            raise ValueError("Equipment tier changed during an active upgrade")
        definition = equipment.config.assets[project.asset]
        runtime.tier = project.to_tier
        project.status = "completed"
        person = state.residents[project.worker]
        person.available_at = min(person.available_at, state.minute)
        state.role_contributions[project.worker] = state.role_contributions.get(project.worker, 0) + 1
        village.announce(world, project.worker, "equipment_upgraded", definition.entity,
                         f"{world.state.entities[definition.entity].name} was upgraded to "
                         f"{definition.tiers[runtime.tier].name} with real funded work at "
                         f"{world.state.entities[definition.location].name}.")


def actions(world: "World") -> list[dict[str, Any]]:
    if not enabled(world) or blocked_until(world, world.actor_id) is not None:
        return []
    available = []
    for identifier, definition in _state(world).config.assets.items():
        if not _authorized(definition, world.actor_id):
            continue
        funders = [definition.owner] if definition.owner else definition.funders
        for payer in funders:
            if payer and _blocker(world, identifier, payer) is None:
                available.append((identifier, payer))
    if not available:
        return []
    return [{
        "id": "village_upgrade_equipment",
        "description": "Fund and perform a complete real-time upgrade at the physical asset location.",
        "targets": [],
        "arguments_schema": {"type": "object", "properties": {
            "asset": {"type": "string", "enum": sorted({value for value, _ in available})},
            "payer": {"type": "string", "enum": sorted({value for _, value in available if value})},
        }, "required": ["asset", "payer"], "additionalProperties": False},
        "availability": "available", "reason": None,
    }]


def observation(world: "World") -> dict[str, Any]:
    if not enabled(world):
        return {}
    equipment = _state(world)
    local = []
    for identifier, definition in equipment.config.assets.items():
        if definition.location != world.actor.location_id and definition.owner != world.actor_id:
            continue
        runtime = equipment.assets[identifier]
        current, next_tier = definition.tiers[runtime.tier], (
            definition.tiers[runtime.tier + 1] if runtime.tier + 1 < len(definition.tiers) else None)
        project = next((item for item in equipment.projects.values()
                        if item.asset == identifier and item.status == "active"), None)
        local.append({
            "id": identifier, "entity": definition.entity, "location": definition.location,
            "category": definition.category, "communal": definition.communal,
            "owner": definition.owner, "tier": runtime.tier, "tier_name": current.name,
            "duration_factor": current.duration_factor, "batch_size": current.batch_size,
            "capacity": current.capacity, "fuel_factor": current.fuel_factor,
            "next_tier": next_tier.name if next_tier else None,
            "upgrade_cost": next_tier.cost if next_tier else None,
            "upgrade_supplies": next_tier.supplies if next_tier else {},
            "upgrade_finishes_at": project.finishes_at if project else None,
        })
    return {"equipment": {
        "enabled": True, "assets": local,
        "policy": "Capital remains at its real location. Upgrades consume actual supplies and retained funds, "
                  "reserve equipment for committed work and improve throughput without creating free outputs.",
    }}


def monitor(world: "World") -> dict[str, Any]:
    if not enabled(world):
        return {"equipment": [], "activities": []}
    equipment = _state(world)
    views = []
    for identifier, definition in equipment.config.assets.items():
        runtime = equipment.assets[identifier]
        project = next((item for item in equipment.projects.values()
                        if item.asset == identifier and item.status == "active"), None)
        current = definition.tiers[runtime.tier]
        views.append({
            "id": identifier, "entity": definition.entity, "location": definition.location,
            "category": definition.category, "communal": definition.communal,
            "owner": definition.owner, "tier": runtime.tier, "tier_name": current.name,
            "duration_factor": current.duration_factor, "batch_size": current.batch_size,
            "capacity": current.capacity, "fuel_factor": current.fuel_factor,
            "upgrade_worker": project.worker if project else None,
            "upgrade_finishes_at": project.finishes_at if project else None,
        })
    activities = [{"actor": project.worker, "label": f"Upgrading {project.asset}",
                   "location": equipment.config.assets[project.asset].location,
                   "start": project.started_at, "end": project.finishes_at,
                   "status": project.status}
                  for project in equipment.projects.values()]
    return {"equipment": views, "activities": activities}


def action_blocker(world: "World", action: str, actor: str) -> tuple[str, str] | None:
    if not enabled(world):
        return None
    active = blocked_until(world, actor)
    if active is not None and action not in {
            "look", "read", "say", "village_observe", "village_wait", "village_upgrade_equipment"}:
        return "EQUIPMENT_UPGRADE_COMMITTED", (
            f"Finish the active equipment upgrade at {village.clock_time(active)} before other work.")
    return None

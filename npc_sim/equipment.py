from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"plug", "unplug", "turn_on", "turn_off", "place"}


def is_powered(world: "World", eid: str) -> bool:
    entity = world.state.entities[eid]
    outlet = world.state.entities.get(entity.plugged_into) if entity.plugged_into else None
    return bool(entity.power == "on" and not entity.broken and outlet is not None
                and outlet.kind == "outlet" and not outlet.broken
                and entity.plugged_into is not None
                and world.in_current_room(eid) and world.in_current_room(entity.plugged_into))


def actions(world: "World") -> list[dict[str, Any]]:
    local = [eid for eid in world.visible_ids() if world.in_current_room(eid)]
    visible = [eid for eid in local if not world.state.entities[eid].broken]
    all_appliances = [eid for eid in local if world.state.entities[eid].power is not None]
    appliances = [eid for eid in visible if world.state.entities[eid].power is not None]
    outlets = [eid for eid in visible if world.state.entities[eid].kind == "outlet"]
    result = []

    def add(aid: str, description: str, targets: list[str], extra: dict[str, Any] | None = None,
            blocked: str | None = None) -> None:
        if not targets:
            return
        properties = {"target": {"type": "string", "enum": targets}, **(extra or {})}
        result.append({"id": aid, "description": description,
                       "arguments_schema": {"type": "object", "properties": properties,
                                            "required": list(properties), "additionalProperties": False},
                       "targets": targets, "availability": "blocked" if blocked else "available", "reason": blocked})

    if outlets:
        add("plug", "Connect a local switched-off appliance to a free outlet. Each outlet has one socket.",
            appliances, {"outlet": {"type": "string", "enum": outlets}})
    add("unplug", "Disconnect a local appliance after switching it off.",
        [eid for eid in all_appliances if world.state.entities[eid].plugged_into])
    off = [eid for eid in appliances if world.state.entities[eid].power == "off"]
    add("turn_on", "Switch on a local appliance connected to an intact outlet.", off,
        blocked=None if any(world.state.entities[eid].plugged_into for eid in off)
        else "Connect the appliance to the outlet first.")
    add("turn_off", "Switch off a local electrical appliance.",
        [eid for eid in all_appliances if world.state.entities[eid].power == "on"])
    stoves = [eid for eid in appliances if world.state.entities[eid].kind == "stove"]
    if stoves:
        add("place", "Place a carried intact open container on a local stove, freeing your carrying capacity.",
            [eid for eid in world.actor.inventory if eid in visible
             and world.state.entities[eid].kind == "container" and world.state.entities[eid].open],
            {"surface": {"type": "string", "enum": stoves}})
    return result


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    target = args["target"]
    entity = world.state.entities[target]

    def reject(code: str, explanation: str) -> tuple[str, str, str, None]:
        return "REJECTED", code, explanation, None

    if not world.in_current_room(target) or (entity.broken and action not in {"turn_off", "unplug"}):
        return reject("OUT_OF_REACH", "The equipment must be intact and physically in your current room.")
    if action == "place":
        surface = args["surface"]
        if target not in world.actor.inventory:
            return reject("NOT_CARRIED", "Carry the container before placing it on the stove.")
        if surface == target or not world.in_current_room(surface) or world.state.entities[surface].broken:
            return reject("INVALID_TARGET", "Use an intact local stove.")
        entity.location = surface
        world.actor.inventory.remove(target)
        return "APPLIED", "PLACED", f"You place the {entity.name.lower()} on the {world.state.entities[surface].name.lower()}.", None
    if action == "plug":
        outlet_id = args["outlet"]
        outlet = world.state.entities[outlet_id]
        if outlet.kind != "outlet" or outlet.broken or not world.in_current_room(outlet_id):
            return reject("INVALID_OUTLET", "Use an intact local outlet.")
        if entity.power != "off":
            return reject("POWER_ON", "Switch the appliance off before connecting it.")
        if entity.plugged_into == outlet_id:
            return "NO_CHANGE", "NO_CHANGE", "The appliance is already connected to that outlet.", None
        if entity.plugged_into is not None:
            return reject("ALREADY_CONNECTED", "Unplug the appliance before changing outlets.")
        if any(other.plugged_into == outlet_id for other in world.state.entities.values()):
            return reject("OUTLET_OCCUPIED", "The outlet's only socket is occupied. Switch off and unplug that appliance first.")
        entity.plugged_into = outlet_id
    elif action == "unplug":
        if entity.power != "off":
            return reject("POWER_ON", "Switch the appliance off before unplugging it.")
        entity.plugged_into = None
    elif action == "turn_on":
        outlet = world.state.entities.get(entity.plugged_into) if entity.plugged_into else None
        if (entity.plugged_into is None or outlet is None or outlet.kind != "outlet"
                or outlet.broken or not world.in_current_room(entity.plugged_into)):
            return reject("NO_POWER", "Connect the appliance to an intact local outlet first.")
        entity.power = "on"
    elif action == "turn_off":
        entity.power = "off"
    else:
        raise ValueError(f"Unsupported equipment action {action}")
    descriptions = {"plug": "plug in", "unplug": "unplug", "turn_on": "switch on", "turn_off": "switch off"}
    return "APPLIED", "POWER_CHANGED", f"You {descriptions[action]} the {entity.name.lower()}.", None

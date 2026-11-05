from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"teleport", "recruit", "sail", "wait_tide", "dig", "pace"}
MOVEMENT_VERBS = {"teleport", "sail"}


def tide(world: "World") -> tuple[str, int]:
    config, state = world.scenario.voyage, world.state.voyage
    if config is None or state is None:
        raise ValueError("Missing voyage configuration or state")
    return ("low" if (state.turns // config.turns_per_tide) % 2 == 0 else "high",
            config.turns_per_tide - state.turns % config.turns_per_tide)


def advance(world: "World", action: str) -> None:
    if world.state.voyage is not None and action not in {"look", "read", "wait_tide"}:
        world.state.voyage.turns += 1


def carried(world: "World", eid: str) -> bool:
    return eid in world.actor.inventory and not world.state.entities[eid].broken


def observation(world: "World") -> dict[str, Any]:
    config = world.scenario.voyage
    if config is None or world.actor.location_id not in config.tide_visible_locations:
        return {}
    phase, remaining = tide(world)
    return {"voyage": {
        "tide": phase, "turns_until_change": remaining,
        "policy": "Successful physical actions advance one turn. Reading, inspecting, rejected actions and model calls do not. Wait for the next tide explicitly.",
        **({"seaworthy": world.state.entities[config.ship_id].preparation.get("seaworthy") == "yes",
            "crew_aboard": world.state.entities[config.crew_id].location == config.cabin_id
            and not world.state.entities[config.crew_id].broken}
           if world.actor.location_id in [*config.docks, config.cabin_id] else {}),
    }}


def actions(world: "World") -> list[dict[str, Any]]:
    config = world.scenario.voyage
    if config is None:
        return []
    local = {eid for eid in world.visible_ids()
             if world.in_current_room(eid) and not world.state.entities[eid].broken}
    result: list[dict[str, Any]] = []

    def add(verb: str, description: str, properties: dict[str, Any],
            reason: str | None = None) -> None:
        result.append({
            "id": verb, "description": description,
            "arguments_schema": {"type": "object", "properties": properties,
                                 "required": list(properties), "additionalProperties": False},
            "targets": [value for rule in properties.values() for value in rule.get("enum", [])
                        if value in local],
            "availability": "blocked" if reason else "available", "reason": reason,
        })

    if world.actor.location_id in config.tide_visible_locations:
        add("wait_tide", "Wait safely until the tide changes to its next phase. No real-time delay.", {})
    if (config.teleport_item in world.actor.read_entities
            and world.actor.location_id in config.teleport_locations):
        add("teleport", "Speak the learned word while carrying the intact journal to travel between its two resonant sites.",
            {"word": {"type": "string", "enum": [config.teleport_word]}},
            None if carried(world, config.teleport_item) else "Carry the intact journal.")
    if config.crew_id in local and world.state.entities[config.crew_id].preparation.get("hired") != "yes":
        add("recruit", "Offer the navigator the requested supplies; they join the ship and stay aboard for return trips.",
            {"target": {"type": "string", "enum": [config.crew_id]},
             "item": {"type": "string", "enum": [config.crew_offer]}},
            None if carried(world, config.crew_offer) else "Carry the supplies requested by the navigator.")
    if world.actor.location_id == config.cabin_id:
        add("sail", "Sail to the other island at high tide with a seaworthy ship, hired navigator and carried chart. Leave the resonant journal ashore.",
            {}, sailing_block(world))
    for eid, site in config.dig_sites.items():
        if eid not in local or world.state.entities[eid].open:
            continue
        if site.paces is not None:
            add("pace", "Measure a bearing at this site using the number learned from the chart before digging.",
                {"target": {"type": "string", "enum": [eid]},
                 "count": {"type": "integer", "minimum": 1, "maximum": 1000}})
        reason = None
        if not carried(world, site.tool_id):
            reason = "Carry the intact digging tool."
        elif site.low_tide and tide(world)[0] != "low":
            reason = "This site is exposed only at low tide."
        add("dig", "Excavate a local buried cache with its carried tool; some sites require low tide or a measured bearing.",
            {"target": {"type": "string", "enum": [eid]},
             "item": {"type": "string", "enum": [site.tool_id]}}, reason)
    return result


def sailing_block(world: "World") -> str | None:
    config = world.scenario.voyage
    if config is None:
        raise ValueError("Missing voyage configuration")
    ship, crew = world.state.entities[config.ship_id], world.state.entities[config.crew_id]
    if ship.broken or ship.preparation.get("seaworthy") != "yes":
        return "Finish building the seaworthy ship."
    if crew.broken or crew.location != config.cabin_id or crew.preparation.get("hired") != "yes":
        return "Recruit the navigator before sailing."
    if not carried(world, config.chart_id):
        return "Carry the intact navigation chart."
    # Leaving a carried container on deck must not smuggle the journal aboard.
    if world.in_current_room(config.teleport_item):
        return "The resonant journal interferes with navigation. Leave it ashore."
    if tide(world)[0] != "high":
        return "The ship can leave only at high tide."
    return None


def movement_block(world: "World", destination: str, exit_id: str | None = None) -> str | None:
    config = world.scenario.voyage
    if (config is not None and destination in config.tidal_locations
            and tide(world)[0] == "high" and not carried(world, config.flotation_id)):
        return "The flooded route requires the carried flotation belt at high tide. Wait safely here for low tide."
    rule = config.passage_rules.get(exit_id) if config is not None and exit_id is not None else None
    if rule is not None:
        if any(not carried(world, eid) for eid in rule.carried):
            return "This passage requires its described intact equipment to be carried."
        if any(eid not in world.state.entities or world.state.entities[eid].broken or any(
                world.state.entities[eid].preparation.get(key) != value for key, value in values.items())
               for eid, values in rule.requires.items()):
            return "The obstacle described at this passage has not been resolved."
        if rule.max_item_units is not None and any(
                world.state.entities[eid].inventory_units > rule.max_item_units for eid in world.actor.inventory):
            return "A carried item is too bulky for this narrow passage. Use the wider route."
    return None


def move(world: "World", destination: str) -> None:
    world.actor.location_id = destination
    if destination not in world.actor.visited_locations:
        world.actor.visited_locations.append(destination)


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    config, state = world.scenario.voyage, world.state.voyage
    if config is None or state is None:
        raise ValueError("Missing voyage configuration or state")

    def reject(code: str, text: str) -> tuple[str, str, str, None]:
        return "REJECTED", code, text, None

    if action in MOVEMENT_VERBS and any(world.need_effects(name) for name in world.actor.needs):
        return reject("NEED_CONSTRAINT", "Food or sleep recovery is required before movement.")
    if action == "wait_tide":
        _, remaining = tide(world)
        state.turns += remaining
        return "APPLIED", "TIDE_CHANGED", f"You wait on safe ground. The tide is now {tide(world)[0]}.", None
    if action == "teleport":
        if not carried(world, config.teleport_item):
            return reject("NOT_CARRIED", "Carry the intact journal to use its word.")
        destination = next(eid for eid in config.teleport_locations if eid != world.actor.location_id)
        blocked = movement_block(world, destination)
        if blocked:
            return reject("TIDE_BLOCKED", blocked)
        move(world, destination)
        return "APPLIED", "TELEPORTED", "You speak the journal's word. The air shimmers and you arrive at the other resonant site.", None
    if action == "recruit":
        if not carried(world, config.crew_offer):
            return reject("NOT_CARRIED", "Carry the navigator's requested supplies.")
        world.actor.inventory.remove(config.crew_offer)
        world.state.entities[config.crew_offer].location = None
        crew = world.state.entities[config.crew_id]
        crew.preparation["hired"] = "yes"
        crew.location = config.cabin_id
        return "APPLIED", "CREW_RECRUITED", "You give the navigator fresh supplies. They agree to crew both voyages and head to the ship.", None
    if action == "sail":
        reason = sailing_block(world)
        if reason:
            return reject("SAILING_BLOCKED", reason)
        ship = world.state.entities[config.ship_id]
        destination = next(eid for eid in config.docks if eid != ship.location)
        ship.location = destination
        world.state.entities[config.boarding_exit].location = destination
        return "APPLIED", "SAILED", f"You and the navigator sail across the strait and moor at {world.state.entities[destination].name.lower()}. You remain aboard; use the gangway to land.", None
    target = args["target"]
    site = config.dig_sites[target]
    if action == "pace":
        if state.paced_sites.get(target) == args["count"]:
            return "NO_CHANGE", "NO_CHANGE", "This bearing has already been measured at that distance.", None
        state.paced_sites[target] = args["count"]
        return "APPLIED", "PACED", f"You mark a bearing of {args['count']} paces at the site.", None
    if not carried(world, site.tool_id):
        return reject("NOT_CARRIED", "Carry the intact digging tool.")
    if site.low_tide and tide(world)[0] != "low":
        return reject("TIDE_BLOCKED", "The digging site is submerged until low tide.")
    if site.paces is not None and state.paced_sites.get(target) != site.paces:
        return reject("BEARING_REQUIRED", "No cache here: measure the bearing described on the navigation chart first.")
    world.state.entities[target].jammed = False
    world.state.entities[target].open = True
    return "APPLIED", "EXCAVATED", "You dig carefully and expose the buried cache. Its contents are now reachable.", None


def evaluate_goal(world: "World") -> dict[str, Any]:
    config = world.scenario.voyage
    if config is None:
        raise ValueError("Missing voyage configuration")
    checks = {
        eid: not world.state.entities[eid].broken
        and world.state.entities[eid].location == world.scenario.exit_location
        for eid in config.treasures}
    home = world.actor.location_id == world.scenario.exit_location
    return {
        "status": "achieved" if home and all(checks.values()) else "active",
        "reason": "",
        "evidence": {"actor_id": world.actor_id, "checks": {"returned_home": home, **checks}},
        "observation_revision": world.revision(),
    }

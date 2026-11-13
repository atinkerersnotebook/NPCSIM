"""Committed, shortest-hop walking itineraries for Living Millhaven residents."""

from collections import deque
from typing import TYPE_CHECKING, Any

from npc_sim import village, village_life
from npc_sim.models import VillageWalkingPlan

if TYPE_CHECKING:
    from npc_sim.world import World


def enabled(world: "World") -> bool:
    config = world.scenario.village
    return bool(config and config.ai_controlled and config.time_based_turns
                and world.scenario.id == "millhaven-living")


def _status(world: "World") -> tuple[str, bool, bool, str]:
    config, state = village.context(world)
    resident = config.residents[world.actor_id]
    life = state.residents[world.actor_id].life
    routine = next(entry for entry in reversed(resident.schedule)
                   if entry.minute <= state.minute % 1440)
    return (str(world.actor.messages[-1]) if world.actor.messages else "",
            bool(life and village_life.meal_due(life, state.minute, include_missed=True)),
            bool(life and village_life.rest_due(resident, life, state.minute)),
            routine.location)


def _path(world: "World", destination: str) -> tuple[list[str], list[str]] | None:
    origin = world.actor.location_id
    state = world.state.village
    known_routes = state.residents[world.actor_id].known_routes
    known = {eid for eid, entity in world.state.entities.items()
             if entity.kind == "door" and not entity.hidden
             and (not known_routes or eid in known_routes)}
    road_only = bool(state.wagon and state.wagon.driver == world.actor_id)
    queue = deque([(origin, [], [])])
    visited = {origin}
    while queue:
        location, locations, doors = queue.popleft()
        if location == destination:
            return locations, doors
        for eid in sorted(known):
            door = world.state.entities.get(eid)
            if (door is None or door.kind != "door" or door.hidden or not door.open
                    or door.locked or door.broken or world.lock_for(door).locked
                    or world.lock_for(door).broken or door.traversal_capabilities
                    or door.required_challenges or door.max_body_size == "small"
                    or road_only and not door.village_road):
                continue
            neighbor = (door.destination if door.location == location else
                        door.location if door.destination == location else None)
            if neighbor is not None and neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, [*locations, neighbor], [*doors, eid]))
    return None


def _interruption(world: "World", plan: VillageWalkingPlan) -> bool:
    message, meal, rest, routine = _status(world)
    return (message != plan.last_message and bool(message) or meal and not plan.meal_due
            or rest and not plan.rest_due or routine != plan.routine_location)


def _blocked_step(world: "World", plan: VillageWalkingPlan) -> bool:
    if plan.next_step >= len(plan.doors):
        return True
    door = world.state.entities.get(plan.doors[plan.next_step])
    return (door is None or door.kind != "door" or not door.open or door.locked
            or door.broken or door.hidden or world.lock_for(door).locked
            or world.lock_for(door).broken)


def check_plan(world: "World", destination: str) -> tuple[str, str] | None:
    if destination not in world.state.entities or world.state.entities[destination].kind != "location":
        return "INVALID_DESTINATION", "Choose a real village location."
    if destination == world.actor.location_id:
        return "ALREADY_THERE", "You are already at that destination."
    plan = world.state.village.residents[world.actor_id].walking_plan
    if plan and not _interruption(world, plan) and not _blocked_step(world, plan):
        return "ROUTE_COMMITTED", "Finish the current route before choosing another destination."
    if _path(world, destination) is None:
        return "NO_KNOWN_ROUTE", "No open, traversable route to that destination is known; check blocked or unknown doors and choose another open destination."
    return None


def plan(world: "World", args: dict[str, Any]) -> tuple[str, str, str, None]:
    destination = args["destination"]
    blocked = check_plan(world, destination)
    if blocked:
        return "REJECTED", *blocked, None
    _, state = village.context(world)
    locations, doors = _path(world, destination)
    message, meal, rest, routine = _status(world)
    origin = world.actor.location_id
    state.residents[world.actor_id].walking_plan = VillageWalkingPlan(
        origin=origin, destination=destination, locations=locations, doors=doors,
        purpose=args["purpose"], last_message=message, meal_due=meal,
        rest_due=rest, routine_location=routine)
    itinerary = " -> ".join([origin, *locations])
    village.event(world, world.actor_id, "village_plan_walk", destination,
                  f"Planned walk for {args['purpose']}: {itinerary}.")
    return "APPLIED", "ROUTE_PLANNED", f"Planned shortest walk: {itinerary}.", None


def check_step(world: "World", args: dict[str, Any]) -> tuple[str, str] | None:
    door = world.state.entities.get(args.get("target"))
    if door is None or door.kind != "door":
        return None
    person = world.state.village.residents[world.actor_id]
    plan = person.walking_plan
    if plan is None:
        return "ROUTE_REQUIRED", "Plan a destination with village_plan_walk before moving."
    if _blocked_step(world, plan):
        return "ROUTE_BLOCKED", "The planned route is blocked; plan a new traversable route."
    if args["target"] != plan.doors[plan.next_step] or world.actor.location_id != plan.origin:
        return "ROUTE_DEVIATION", "Continue the planned route; a new task requires a justified replan."
    return None


def advance(world: "World", origin: str, destination: str) -> None:
    person = world.state.village.residents[world.actor_id]
    plan = person.walking_plan
    if plan is None:
        return
    if (origin != plan.origin or plan.next_step >= len(plan.locations)
            or destination != plan.locations[plan.next_step]):
        person.walking_plan = None
        return
    plan.origin = destination
    plan.next_step += 1
    if destination == plan.destination:
        person.walking_plan = None

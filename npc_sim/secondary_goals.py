import re
from typing import TYPE_CHECKING, Any

from npc_sim.models import Entity, SecondaryCondition, SecondaryGoalSpec

if TYPE_CHECKING:
    from npc_sim.world import World


def validate_goals(world: "World", goals: dict[str, SecondaryGoalSpec], entities: dict[str, Entity]) -> None:
    for gid, goal in goals.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", gid) or gid in world.state.secondary_goals:
            raise ValueError("Side-goal IDs must be unique simple names; existing goals cannot be overwritten.")
        if not set(goal.assigned_actor_ids) <= world.state.actors.keys():
            raise ValueError("Assign side goals only to existing NPCs; expand team assignments into their actor IDs.")
        for condition in goal.conditions:
            target = entities.get(condition.target)
            if target is None:
                raise ValueError(f"Unknown side-goal target: {condition.target}")
            if condition.actor_id is not None and condition.actor_id not in world.state.actors:
                raise ValueError("A completion condition references an unknown NPC.")
            if condition.kind == "at_location" and target.kind != "location":
                raise ValueError("Arrival conditions must target a location.")
            if condition.kind == "carrying" and not target.takable:
                raise ValueError("Carrying conditions require a portable item.")
            if condition.kind == "opened" and target.open is None:
                raise ValueError("Opening conditions require an openable object.")
            if condition.kind == "unlocked" and target.locked is None and target.lock_id is None:
                raise ValueError("Unlocking conditions require a lockable object.")
            if condition.kind == "read" and target.readable_text is None:
                raise ValueError("Reading conditions require readable text.")
            if condition.kind == "item_at":
                destination = entities.get(condition.location_id) if condition.location_id is not None else None
                if not target.takable or destination is None or destination.kind not in {"location", "container"}:
                    raise ValueError("Delivery conditions need a portable item and a room/container destination.")


def condition_met(world: "World", goal: SecondaryGoalSpec, condition: SecondaryCondition) -> bool:
    entity = world.state.entities.get(condition.target)
    if condition.kind == "opened":
        return entity is not None and entity.open is True
    if condition.kind == "unlocked":
        if entity is None:
            return False
        lock = world.lock_for(entity)
        return not entity.broken and not lock.broken and lock.locked is False
    if condition.kind == "item_at":
        return entity is not None and entity.location == condition.location_id
    actors = [world.state.actors[aid] for aid in (
        [condition.actor_id] if condition.actor_id is not None else goal.assigned_actor_ids)]
    checks = [
        condition.target in actor.discovered_entities if condition.kind == "discovered" else
        condition.target in actor.inventory if condition.kind == "carrying" else
        actor.location_id == condition.target if condition.kind == "at_location" else
        condition.target in actor.read_entities
        for actor in actors
    ]
    return all(checks) if condition.quantifier == "all" else any(checks)


def refresh(world: "World") -> bool:
    changed = False
    achieved = []
    for goal in world.state.secondary_goals.values():
        if goal.status == "achieved":
            continue
        checks = [condition_met(world, goal, condition) for condition in goal.conditions]
        if checks != goal.condition_met:
            goal.condition_met = checks
            changed = True
        if all(checks):
            goal.status = "achieved"
            achieved.append(goal)
            changed = True
    if changed:
        world.state.revision += 1
        for goal in achieved:
            goal.completed_revision = world.revision()
    return changed


def condition_text(condition: SecondaryCondition, entities: dict[str, Entity]) -> str:
    target = entities[condition.target].name if condition.target in entities else condition.target
    who = condition.actor_id or ("Every assigned NPC" if condition.quantifier == "all" else "Any assigned NPC")
    if condition.kind == "discovered":
        return f"{who} has discovered {target} through observation."
    if condition.kind == "carrying":
        return f"{who} is carrying {target}."
    if condition.kind == "at_location":
        return f"{who} is at {target}."
    if condition.kind == "read":
        return f"{who} has read {target}."
    if condition.kind == "item_at":
        destination = entities.get(condition.location_id) if condition.location_id is not None else None
        return f"{target} is placed at {destination.name if destination else condition.location_id}."
    return f"{target} is {'open' if condition.kind == 'opened' else 'unlocked'}."


def observation_goals(world: "World") -> list[dict[str, Any]]:
    return [{"id": gid, "title": goal.title, "description": goal.description, "status": goal.status,
             "conditions": [condition_text(condition, world.state.entities) for condition in goal.conditions]}
            for gid, goal in world.state.secondary_goals.items() if world.actor_id in goal.assigned_actor_ids]

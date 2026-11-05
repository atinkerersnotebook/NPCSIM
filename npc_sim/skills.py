from typing import TYPE_CHECKING, Any

from npc_sim.models import Entity, SkillChallenge
from npc_sim import handling

if TYPE_CHECKING:
    from npc_sim.world import World


def barrier_reason(world: "World", entity: Entity, moving: bool = False) -> str | None:
    if moving:
        missing = set(entity.traversal_capabilities) - set(world.actor.body.capabilities)
        if missing:
            return "Your body lacks the route's required abilities: " + ", ".join(sorted(missing)) + "."
        blocked = handling.passage_reason(world.actor.body, entity)
        if blocked:
            return blocked
    pending = [aid for aid in entity.required_challenges if aid not in world.state.skill_uses]
    if pending:
        return "Required cooperative work is unfinished: " + ", ".join(pending) + "."
    return None


def reason(world: "World", challenge: SkillChallenge) -> str | None:
    if challenge.capability not in world.actor.body.capabilities:
        return f"Requires {challenge.capability}; your body does not have this skill. Ask a teammate."
    pending = [aid for aid in challenge.requires if aid not in world.state.skill_uses]
    if pending:
        return "Prior work still needed: " + ", ".join(pending) + "."
    blocked = handling.challenge_reason(world, challenge)
    if blocked:
        return blocked
    missing = [eid for eid in challenge.carried
               if eid not in world.actor.inventory or world.state.entities[eid].broken]
    if missing:
        return "Carry these intact tools or supplies: " + ", ".join(missing) + "."
    return None


def local_challenges(world: "World") -> list[SkillChallenge]:
    visible = set(world.visible_ids())
    return [challenge for challenge in world.scenario.skill_challenges
            if challenge.target in visible and world.in_current_room(challenge.target)
            and not world.state.entities[challenge.target].broken]


def observation(world: "World") -> dict[str, Any]:
    if not world.scenario.skill_challenges:
        return {}
    return {"skill_challenges": [
        {"id": challenge.id, "description": challenge.description, "target": challenge.target,
         "capability": challenge.capability, "completed_by": world.state.skill_uses.get(challenge.id),
         "reason": None if challenge.id in world.state.skill_uses else reason(world, challenge)}
        for challenge in local_challenges(world)]}


def actions(world: "World") -> list[dict[str, Any]]:
    result = []
    for challenge in local_challenges(world):
        if challenge.id in world.state.skill_uses:
            continue
        blocked = reason(world, challenge)
        result.append({
            "id": challenge.id, "description": challenge.description,
            "arguments_schema": {
                "type": "object", "properties": {"target": {"type": "string", "enum": [challenge.target]}},
                "required": ["target"], "additionalProperties": False},
            "targets": [challenge.target], "required_capability": challenge.capability,
            "availability": "blocked" if blocked else "available", "reason": blocked})
    return result


def apply(world: "World", action: str) -> tuple[str, str, str, None]:
    challenge = next(challenge for challenge in local_challenges(world) if challenge.id == action)
    blocked = reason(world, challenge)
    if blocked:
        code = ("BODY_CAPABILITY_REQUIRED" if challenge.capability not in world.actor.body.capabilities
                else "PREREQUISITE_REQUIRED" if any(aid not in world.state.skill_uses for aid in challenge.requires)
                else "BODY_HANDLING_REQUIRED" if handling.challenge_reason(world, challenge)
                else "NOT_CARRIED")
        return "REJECTED", code, blocked, None
    world.state.skill_uses[challenge.id] = world.actor_id
    target = world.state.entities[challenge.target]
    target.preparation[challenge.id] = f"completed by {world.actor_id}"
    for eid in challenge.installs:
        world.actor.inventory.remove(eid)
        item = world.state.entities[eid]
        item.location, item.takable = challenge.target, False
        item.preparation["installed"] = "yes"
    for eid in challenge.opens:
        entity = world.state.entities[eid]
        entity.open = True
        if entity.locked is not None:
            entity.locked = False
        lock = world.lock_for(entity)
        if lock.locked is not None:
            lock.locked = False
    return "APPLIED", "SKILL_APPLIED", f"You use {challenge.capability}: {challenge.description}", None

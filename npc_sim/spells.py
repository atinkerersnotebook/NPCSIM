from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from npc_sim.world import World


CAPABILITIES = {
    "cast_unlock": "unlock_magic",
    "cast_shrink": "shrink_magic",
    "cast_restore_size": "shrink_magic",
}


def unlock_block(world: "World", target: str) -> tuple[str, str] | None:
    entity = world.state.entities[target]
    lock_id = entity.lock_id or target
    related = {target, lock_id, *(eid for eid, other in world.state.entities.items()
                                 if other.lock_id == lock_id)}
    if (any(world.state.entities[eid].required_challenges for eid in related)
            or any(challenge.target in related or set(challenge.opens).intersection(related)
                   for challenge in world.scenario.skill_challenges)):
        return "SPELL_PROTECTED", "Unlock cannot bypass protected puzzle mechanisms or their required work."
    lock = world.lock_for(entity)
    if entity.broken or lock.broken:
        return "LOCK_BROKEN", "Unlock cannot repair a broken object or lock."
    if entity.jammed or lock.jammed:
        return "JAMMED", "Unlock cannot clear a physically jammed mechanism."
    return None


def actions(world: "World") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for action, capability in CAPABILITIES.items():
        if capability not in world.actor.body.capabilities:
            continue
        targets = []
        properties = {}
        reason = None
        if action == "cast_unlock":
            targets = [eid for eid in world.visible_ids()
                       if world.in_current_room(eid)
                       and world.state.entities[eid].kind in {"door", "lock", "container"}
                       and (world.state.entities[eid].locked is not None
                            or world.state.entities[eid].lock_id is not None
                            or world.state.entities[eid].required_challenges)]
            if not targets:
                continue
            properties = {"target": {"type": "string", "enum": targets}}
            requirements = {eid: blocked[1] if (blocked := unlock_block(world, eid)) else None
                            for eid in targets}
            if all(requirements.values()):
                reason = "; ".join(dict.fromkeys(value for value in requirements.values() if value))
            description = ("Speak Unlock at a visible local ordinary lock without a key. This only unlocks; "
                           "it does not open doors, repair damage, clear jams or bypass protected puzzle mechanisms.")
        elif action == "cast_shrink":
            description = ("Speak Shrink to make your own body and carried equipment small. Your abilities, grips "
                           "and carrying limits stay unchanged; this does not grant flight or swimming.")
            if world.actor.body.size == "small":
                reason = "Your body is already at the smallest supported size."
        else:
            description = "Speak Restore Size to return only yourself and your equipment to your original body size."
            if world.actor.body.size == world.body_for(world.actor_id).size:
                reason = "Your body is already its original size."
        descriptor = {
            "id": action, "description": description, "targets": targets,
            "arguments_schema": {"type": "object", "properties": properties,
                                 "required": list(properties), "additionalProperties": False},
            "availability": "blocked" if reason else "available", "reason": reason,
        }
        if action == "cast_unlock":
            descriptor["target_requirements"] = requirements
        result.append(descriptor)
    return result


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    capability = CAPABILITIES[action]
    if capability not in world.actor.body.capabilities:
        return "REJECTED", "BODY_CAPABILITY_REQUIRED", f"Your body lacks {capability}.", None
    if action == "cast_unlock":
        target = args["target"]
        blocked = unlock_block(world, target)
        if blocked:
            return "REJECTED", blocked[0], blocked[1], None
        entity = world.state.entities[target]
        if not world.lock_for(entity).locked:
            return "NO_CHANGE", "NO_CHANGE", "The lock is already unlocked.", None
        world.unlock_entity(target)
        return ("APPLIED", "SPELL_UNLOCKED",
                f"You use Unlock on the {entity.name.lower()}. Its lock is unlocked; it must still be opened.", None)
    size = "small" if action == "cast_shrink" else world.body_for(world.actor_id).size
    if world.actor.body.size == size:
        return "NO_CHANGE", "NO_CHANGE", f"Your body is already {size}.", None
    world.actor.body.size = size
    name = "Shrink" if action == "cast_shrink" else "Restore Size"
    return ("APPLIED", "SPELL_RESIZED",
            f"You use {name}. Your body and carried equipment are now {size}; abilities and carrying limits are unchanged.", None)

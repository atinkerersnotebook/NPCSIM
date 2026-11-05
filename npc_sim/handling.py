from typing import TYPE_CHECKING, Any

from npc_sim.models import Body, Entity, SkillChallenge

if TYPE_CHECKING:
    from npc_sim.world import World


def grip_reason(body: Body, entity: Entity, *, using: bool = False) -> str | None:
    required = entity.use_grips if using else entity.carry_grips
    if set(body.grips).intersection(required):
        return None
    verb = "operate" if using else "carry"
    accepted = ", ".join(required) or "no supported"
    actual = ", ".join(body.grips) or "no"
    return (f"The {entity.name.lower()} requires {accepted} grip to {verb}; "
            f"your body has {actual} grips. Strength, carrying capacity and tools do not change your anatomy.")


def passage_reason(body: Body, entity: Entity) -> str | None:
    sizes = ("small", "medium", "large")
    if entity.max_body_size is not None and sizes.index(body.size) > sizes.index(entity.max_body_size):
        return (f"The {entity.name.lower()} admits at most a {entity.max_body_size} body; "
                f"your body is {body.size} and cannot fit. Abilities do not reduce body size.")
    return None


def carry_reason(body: Body, entity: Entity, held: int) -> str | None:
    if "manipulate" not in body.capabilities:
        return "Your body lacks the manipulate ability needed to hold an object."
    blocked = grip_reason(body, entity)
    if blocked:
        return blocked
    if body.max_held_items is not None and held >= body.max_held_items:
        return (f"Your available grips can hold at most {body.max_held_items} objects; "
                f"you already hold {held}. Put something down first.")
    return None


def use_reason(world: "World", ids: list[str]) -> str | None:
    for eid in dict.fromkeys(ids):
        blocked = grip_reason(world.actor.body, world.state.entities[eid], using=True)
        if blocked:
            return blocked
    return None


def challenge_reason(world: "World", challenge: SkillChallenge) -> str | None:
    return use_reason(world, [challenge.target, *challenge.carried])


def action_reason(world: "World", action: str, args: dict[str, Any]) -> str | None:
    if action == "take":
        return carry_reason(world.actor.body, world.state.entities[args["target"]], len(world.actor.inventory))
    if action == "give":
        recipient = world.state.actors[args["recipient"]]
        blocked = carry_reason(recipient.body, world.state.entities[args["target"]], len(recipient.inventory))
        return f"{recipient.actor_id} cannot receive this item: {blocked}" if blocked else None
    if action in {"look", "read", "say", "go", "drop", "wait", "wait_tide", "pace", "teleport",
                  "play_rps", "play_tic_tac_toe"}:
        return None
    if action in {recovery.action_id for recovery in world.scenario.recovery.values()}:
        return None
    recipe = next((recipe for recipe in world.scenario.recipes
                   if recipe.action_id == action and recipe.arguments == args), None)
    if recipe:
        return use_reason(world, [*recipe.arguments.values(), *recipe.carried, *recipe.placements.values()])
    if action == "sail" and world.scenario.voyage:
        return use_reason(world, [world.scenario.voyage.ship_id, world.scenario.voyage.chart_id])
    keys = (("item",) if action in {"recruit", "hit", "dig"} else ("target",)
            if action in {"serve", "place", "plug"} else ("target", "item", "equipment", "ingredient"))
    return use_reason(world, [value for key in keys
                             if isinstance(value := args.get(key), str) and value in world.state.entities])


def annotate(world: "World", actions: list[dict[str, Any]]) -> None:
    special = set(world.scenario.recipe_actions) | {challenge.id for challenge in world.scenario.skill_challenges}
    for action in actions:
        aid = action["id"]
        if aid in special or aid in {"cast_unlock", "cast_shrink", "cast_restore_size",
                                     "look", "search", "read", "say", "go", "drop", "wait", "wait_tide", "pace",
                                     "teleport", "play_rps", "play_tic_tac_toe"}:
            continue
        if aid in {recovery.action_id for recovery in world.scenario.recovery.values()}:
            continue
        properties = action["arguments_schema"]["properties"]
        if "target" not in properties:
            blocked = action_reason(world, aid, {})
            if blocked:
                action["availability"], action["reason"] = "blocked", blocked
            continue
        requirements = dict(action.get("target_requirements", {}))
        for target in properties["target"].get("enum", []):
            args = {"target": target}
            alternative = "recipient" if aid == "give" else "item" if aid in {"hit", "unlock", "dig", "recruit"} else None
            if alternative:
                reasons = [action_reason(world, aid, {**args, alternative: value})
                           for value in properties.get(alternative, {}).get("enum", [])]
                blocked = next((reason for reason in reasons if reason), None) if reasons and all(reasons) else None
            else:
                blocked = action_reason(world, aid, args)
                if not blocked:
                    for key, schema in properties.items():
                        if key == "target":
                            continue
                        choices = [value for value in schema.get("enum", [])
                                   if isinstance(value, str) and value in world.state.entities]
                        reasons = [action_reason(world, aid, {**args, key: value}) for value in choices]
                        if reasons and all(reasons):
                            blocked = next(reason for reason in reasons if reason)
                            break
            if not blocked and aid == "take":
                entity = world.state.entities[target]
                if world.inventory_units_used() + entity.inventory_units > world.actor.body.capacity:
                    blocked = "This item exceeds your remaining inventory-unit capacity."
            if blocked:
                requirements[target] = blocked
        if requirements:
            action["target_requirements"] = requirements
            if all(requirements.get(target) for target in properties["target"].get("enum", [])):
                action["availability"] = "blocked"
                action["reason"] = "; ".join(dict.fromkeys(reason for reason in requirements.values() if reason))

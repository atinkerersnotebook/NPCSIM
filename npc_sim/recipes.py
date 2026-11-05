from typing import TYPE_CHECKING, Any

from npc_sim.models import Nutrition, Recipe
from npc_sim import equipment, handling

if TYPE_CHECKING:
    from npc_sim.world import World


def candidates(world: "World", action_id: str) -> list[Recipe]:
    visible = set(world.visible_ids())
    result = []
    for recipe in world.scenario.recipes:
        if recipe.action_id != action_id:
            continue
        if any(eid not in visible or not world.in_current_room(eid)
               or world.state.entities[eid].broken for eid in recipe.arguments.values()):
            continue
        if any(any(world.state.entities[eid].preparation.get(key) != value
                   for key, value in requirements.items())
               for eid, requirements in recipe.requires.items()):
            continue
        if any(eid in world.state.entities for eid in recipe.outputs):
            continue
        if any(not equipment.is_powered(world, eid) for eid in recipe.powered):
            continue
        if any(world.state.entities[eid].location != surface for eid, surface in recipe.placements.items()):
            continue
        ingredients = {label for eid in recipe.consumes for label in world.state.entities[eid].ingredients}
        if any(not ingredients.intersection(group) for group in recipe.ingredient_requirements):
            continue
        if any(output.location is not None and output.location != world.actor.location_id and (
                output.location not in visible or not world.in_current_room(output.location)
                or not world.state.entities[output.location].open
                or world.state.entities[output.location].broken)
               for output in recipe.outputs.values()):
            continue
        result.append(recipe)
    return result


def actions(world: "World") -> list[dict[str, Any]]:
    result = []
    for aid, description in world.scenario.recipe_actions.items():
        recipes = candidates(world, aid)
        if not recipes:
            continue
        reasons = [handling.action_reason(world, aid, recipe.arguments) for recipe in recipes]
        ready = any(set(recipe.carried) <= set(world.actor.inventory) and not reason
                    for recipe, reason in zip(recipes, reasons))
        properties = {
            key: {"type": "string", "enum": sorted({recipe.arguments[key] for recipe in recipes})}
            for key in recipes[0].arguments
        }
        result.append({
            "id": aid, "description": description,
            "arguments_schema": {"type": "object", "properties": properties,
                                 "required": list(properties), "additionalProperties": False},
            "targets": sorted({eid for recipe in recipes for eid in recipe.arguments.values()}),
            "availability": "available" if ready else "blocked",
            "reason": None if ready else "; ".join(dict.fromkeys(reason for reason in reasons if reason))
            if all(reasons) else "Carry the required intact tool; ingredients can stay where they are.",
        })
    return result


def apply(world: "World", action_id: str, arguments: dict[str, Any]) -> tuple[str, str, str, None]:
    recipe = next((recipe for recipe in candidates(world, action_id)
                   if recipe.arguments == arguments), None)
    if recipe is None:
        return "REJECTED", "RECIPE_MISMATCH", "These ingredients or preparation states do not match an available recipe.", None
    if not set(recipe.carried) <= set(world.actor.inventory):
        return "REJECTED", "NOT_CARRIED", "You must carry the required intact tool.", None
    foods = [world.state.entities[eid] for eid in recipe.consumes if world.state.entities[eid].food]
    ingredients = list(dict.fromkeys(label for food in foods for label in (food.ingredients or [food.name])))
    nutrition: Nutrition | None = None
    known_nutrition = [food.nutrition for food in foods if food.nutrition is not None]
    if foods and len(known_nutrition) == len(foods):
        nutrition = known_nutrition[0].model_copy(deep=True)
        for values in known_nutrition[1:]:
            nutrition = nutrition.plus(values)
    for eid in recipe.consumes:
        world.state.entities[eid].location = None
        if eid in world.actor.inventory:
            world.actor.inventory.remove(eid)
    for eid, updates in recipe.updates.items():
        world.state.entities[eid].preparation.update(updates)
    if recipe.nutrition_target is not None:
        target = world.state.entities[recipe.nutrition_target]
        target.ingredients = list(dict.fromkeys([*(target.ingredients or [target.name]), *ingredients]))
        target.nutrition = target.nutrition.plus(nutrition) if target.nutrition is not None and nutrition is not None else None
    food_outputs = sum(output.food for output in recipe.outputs.values())
    for eid, prototype in recipe.outputs.items():
        output = prototype.model_copy(deep=True)
        output.location = output.location or world.actor.location_id
        if output.food:
            output.ingredients = output.ingredients or ingredients.copy()
            output.nutrition = nutrition.scaled(1 / food_outputs) if nutrition is not None else None
        world.state.entities[eid] = output
    detail = ""
    if recipe.nutrition_target is not None:
        detail = " Ingredients now present: " + ", ".join(world.state.entities[recipe.nutrition_target].ingredients) + "."
    elif any(output.food for output in recipe.outputs.values()):
        detail = " Prepared ingredients: " + ", ".join(dict.fromkeys(
            label for eid in recipe.outputs for label in world.state.entities[eid].ingredients)) + "."
    return "APPLIED", "PREPARED", recipe.explanation + detail, None

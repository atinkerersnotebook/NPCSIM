from typing import TYPE_CHECKING, Any

from npc_sim.equipment import is_powered
from npc_sim.models import BreadBatch

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"measure", "mix_dough", "rise_dough", "shape_dough", "bake_bread",
         "unload_bread", "serve_bread", "wait"}
TIMINGS = {
    "mixing": (600, "mixed", None),
    "rising": (7200, "risen", 1800),
    "proofing": (2700, "proofed", 1200),
    "baking": (1800, "baked", 600),
    "cooling": (1200, "cooled", None),
}
ACTION_SECONDS = {"look": 0, "read": 0, "wait": 0, "measure": 60, "mix_dough": 60, "shape_dough": 180}
TIMED_ACTIONS = {"mix_dough", "rise_dough", "shape_dough", "bake_bread", "unload_bread"}


def action_seconds(action: str) -> int:
    return ACTION_SECONDS.get(action, 30)


def charge_action(world: "World", action: str, arguments: dict[str, Any]) -> int:
    clock = world.state.clock
    if clock is None or clock.mode != "decision_paused":
        return 0
    duration = action_seconds(action)
    clock.seconds += duration
    if action in TIMED_ACTIONS:
        batch = world.state.batches[arguments["target"]]
        if batch.ready_at is not None:
            batch.ready_at += duration
        if batch.deadline is not None:
            batch.deadline += duration
    advance(world)
    return duration


def advance(world: "World") -> bool:
    clock = world.state.clock
    if clock is None:
        return False
    changed = False
    for bowl, batch in world.state.batches.items():
        if batch.stage in {"ruined", "served"}:
            continue
        machine = world.state.entities.get(batch.equipment) if batch.equipment else None
        disconnected = machine is not None and (
            machine.broken or machine.power != "on" or not machine.plugged_into
            or world.state.entities[machine.plugged_into].broken)
        if world.state.entities[bowl].broken or disconnected:
            batch.stage, batch.failure = "ruined", "The bowl or active equipment became unusable."
            batch.equipment = None
            changed = True
        elif batch.deadline is not None and clock.seconds > batch.deadline:
            batch.stage, batch.failure = "ruined", "Missed the handling deadline: dough overproofed or bread burned."
            batch.equipment = None
            changed = True
        elif batch.ready_at is not None and clock.seconds >= batch.ready_at and batch.stage in TIMINGS:
            batch.stage = TIMINGS[batch.stage][1]
            changed = True
        world.state.entities[bowl].preparation = {"bread_stage": batch.stage}
    return changed


def observation(world: "World") -> dict[str, Any]:
    visible = set(world.visible_ids())
    return {
        "clock": world.state.clock.model_dump() if world.state.clock else None,
        "bread_batches": {bowl: batch.model_dump() for bowl, batch in world.state.batches.items()
                         if bowl in visible or bowl in world.actor.examined},
        "ingredient_stock": {eid: count for eid, count in world.state.ingredient_stock.items() if eid in visible},
    }


def actions(world: "World") -> list[dict[str, Any]]:
    config = world.scenario.bakery
    if config is None:
        return []
    local = [eid for eid in world.visible_ids()
             if world.in_current_room(eid) and not world.state.entities[eid].broken]
    bowls = [eid for eid in config.bowls if eid in local]
    result: list[dict[str, Any]] = []

    def add(verb: str, description: str, targets: list[str], extra: dict[str, Any] | None = None) -> None:
        if not targets:
            return
        properties = {"target": {"type": "string", "enum": targets}, **(extra or {})}
        result.append({"id": verb, "description": description, "targets": targets,
                       "arguments_schema": {"type": "object", "properties": properties,
                                            "required": list(properties), "additionalProperties": False},
                       "availability": "available", "reason": None})

    ingredients = [eid for eid in config.ingredients if eid in local and world.state.ingredient_stock[eid] > 0]
    if ingredients:
        add("measure", "Measure one batch portion of an ingredient into a local open bowl. Each bowl needs "
            "one portion each of flour, water, yeast and salt; stocks are finite.", bowls,
            {"ingredient": {"type": "string", "enum": ingredients}})
    for verb, stages, description, machines in [
        ("mix_dough", {"measuring"}, "Mix a fully measured batch for 10 minutes using a free powered mixer.", config.mixers),
        ("rise_dough", {"mixed"}, "Release the mixer and leave the dough on the bench to rise for 2 hours. "
         "Shape within 30 minutes of readiness. Work on another batch while it rises.", []),
        ("shape_dough", {"risen"}, "Shape risen dough and proof it on the bench for 45 minutes. "
         "Load an oven within 20 minutes of proof readiness.", []),
        ("bake_bread", {"proofed"}, "Load a free powered oven. Bake 30 minutes, then unload within "
         "10 minutes or the loaf burns. The oven remains occupied until unloading.", config.ovens),
        ("unload_bread", {"baked"}, "Unload baked bread onto the cooling rack; cool for 20 minutes.", []),
        ("serve_bread", {"cooled"}, "Move a cooled loaf to the display. All three batches must be served.", []),
    ]:
        targets = [eid for eid in bowls if world.state.batches.get(eid, BreadBatch()).stage in stages]
        options = [eid for eid in machines if eid in local]
        if machines and not options:
            continue
        add(verb, description, targets,
            {"equipment": {"type": "string", "enum": options}} if machines else None)
    result.append({
        "id": "wait", "description": "Wait up to the requested simulated seconds without advancing time instantly. "
        "The controller wakes at the next batch readiness or deadline, whichever comes first. "
        "Prefer interleaving useful work on other batches over waiting.",
        "targets": [], "availability": "available", "reason": None,
        "arguments_schema": {"type": "object", "properties": {
            "seconds": {"type": "number", "minimum": 1, "maximum": 7200}},
            "required": ["seconds"], "additionalProperties": False}})
    return result


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    config, clock = world.scenario.bakery, world.state.clock
    if config is None or clock is None:
        raise ValueError("Bakery actions require a timed bakery")
    if action == "wait":
        return "NO_CHANGE", "WAITING", f"You wait up to {args['seconds']} simulated seconds, watching batch timers.", None
    bowl = args["target"]
    entity = world.state.entities[bowl]
    batch = world.state.batches.get(bowl, BreadBatch())

    def reject(code: str, text: str) -> tuple[str, str, str, None]:
        return "REJECTED", code, text, None

    if not entity.open or entity.broken or not world.in_current_room(bowl):
        return reject("BOWL_UNAVAILABLE", "Use an intact, open, local bowl.")
    if action == "measure":
        ingredient = args["ingredient"]
        if batch.stage != "measuring" or ingredient in batch.ingredients:
            return reject("ALREADY_MEASURED", "Only add each ingredient once before mixing.")
        if not world.in_current_room(ingredient) or world.state.ingredient_stock[ingredient] <= 0:
            return reject("INGREDIENT_UNAVAILABLE", "The ingredient must be reachable and in stock.")
        batch.ingredients.append(ingredient)
        world.state.batches[bowl] = batch
        world.state.ingredient_stock[ingredient] -= 1
        return "APPLIED", "MEASURED", f"You measure {ingredient} into {entity.name}.", None
    required = {"mix_dough": "measuring", "rise_dough": "mixed", "shape_dough": "risen",
                "bake_bread": "proofed", "unload_bread": "baked", "serve_bread": "cooled"}
    if batch.stage != required[action]:
        return reject("BATCH_NOT_READY", f"This batch is {batch.stage}; {action} requires {required[action]}.")
    if action == "mix_dough" and set(batch.ingredients) != set(config.ingredients):
        return reject("INGREDIENTS_MISSING", "Measure flour, water, yeast and salt before mixing.")
    equipment = args.get("equipment")
    if equipment:
        if not is_powered(world, equipment):
            return reject("NO_POWER", "Use an intact local powered appliance.")
        if any(other.equipment == equipment for other in world.state.batches.values()):
            return reject("EQUIPMENT_BUSY", "Another batch occupies this appliance. Interleave another task.")
    destination = equipment or (
        config.rack if action == "unload_bread" else config.display if action == "serve_bread" else config.bench)
    if not world.in_current_room(destination) or world.state.entities[destination].broken:
        return reject("SURFACE_UNAVAILABLE", "The destination must be an intact local surface.")
    batch.equipment = equipment
    entity.location = destination
    if bowl in world.actor.inventory:
        world.actor.inventory.remove(bowl)
    stage = {"mix_dough": "mixing", "rise_dough": "rising", "shape_dough": "proofing",
             "bake_bread": "baking", "unload_bread": "cooling", "serve_bread": "served"}[action]
    batch.stage = stage
    batch.ready_at, batch.deadline = None, None
    if stage in TIMINGS:
        duration, _, window = TIMINGS[stage]
        batch.ready_at = clock.seconds + duration
        batch.deadline = batch.ready_at + window if window else None
    entity.preparation = {"bread_stage": stage}
    return "APPLIED", "BATCH_UPDATED", f"{entity.name}: {stage}. " + (
        f"Ready at bakery time {batch.ready_at:.0f}s." if batch.ready_at is not None else "The loaf is on display."), None

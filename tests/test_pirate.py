import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from npc_sim.contracts import observation
from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.world import World
from test_world import act


ROOT = Path(__file__).resolve().parent.parent

LOCATIONS = [
    "sail-loft", "tide-cellar", "hidden-cove", "wreck-beach", "mangrove-walk",
    "smugglers-camp", "cliff-stairs", "ruined-watchtower", "lantern-gallery",
    "echo-cavern", "captains-study", "treasure-vault", "escape-quay",
]
ROUTE = [
    "floor-hatch", "shore-gate", "salvage-gate", "rope-crossing", "thorn-curtain",
    "watch-turnstile", "missing-span", "dark-stair", "beam-shutter",
    "resonance-gate", "bearing-door", "quay-portcullis",
]


@pytest.fixture
def world(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "saltglass-passage.json"))
    return World(scenario, tmp_path / "world-state.json", "world", "npc", "session")


def craft_mallet(world):
    assert act(world, "open", target="supply-locker")["outcome"] == "APPLIED"
    assert act(world, "take", target="marlinspike")["outcome"] == "APPLIED"
    assert act(world, "lash", target="hardwood-billet", binding="sail-cord",
               item="marlinspike")["outcome"] == "APPLIED"
    assert act(world, "take", target="rigging-mallet")["outcome"] == "APPLIED"


def enter_cellar(world):
    assert act(world, "open", target="floor-hatch")["outcome"] == "APPLIED"
    assert act(world, "go", target="floor-hatch")["outcome"] == "APPLIED"


def apply(world, action, **arguments):
    result = act(world, action, **arguments)
    assert result["outcome"] == "APPLIED", result
    assert world.inventory_units_used() <= 8
    return result


def walk_to(world, destination):
    current, end = LOCATIONS.index(world.actor.location_id), LOCATIONS.index(destination)
    while current != end:
        index = current if current < end else current - 1
        apply(world, "go", target=ROUTE[index])
        current += 1 if current < end else -1


def release(world, door, item):
    assert act(world, "go", target=door)["code"] == "EXIT_BLOCKED"
    apply(world, "unlock", target=door, item=item)
    apply(world, "open", target=door)
    apply(world, "go", target=door)


def crack(world, target):
    for _ in range(2):
        apply(world, "hit", target=target, item="rigging-mallet")


def reach_cove(world):
    craft_mallet(world)
    apply(world, "take", target="spare-rope")
    enter_cellar(world)
    crack(world, "signal-float")
    apply(world, "take", target="shore-key")
    release(world, "shore-gate", "shore-key")


def test_exactly_ten_new_locations_and_larger_bounded_inventory(world):
    assert {eid for eid, entity in world.scenario.entities.items()
            if entity.kind == "location"} == set(LOCATIONS)
    assert len(LOCATIONS) == 13
    assert world.actor.body.capacity == 8
    reach_cove(world)
    release(world, "salvage-gate", "shore-key")
    crack(world, "cargo-crate")
    for eid in ("iron-hook", "rusty-blade", "copper-wire", "lamp-oil"):
        apply(world, "take", target=eid)
    assert world.inventory_units_used() == 8
    walk_to(world, "sail-loft")
    assert act(world, "take", target="canvas-strip")["code"] == "CAPACITY_EXCEEDED"
    apply(world, "drop", target="lamp-oil")
    apply(world, "take", target="canvas-strip")
    assert world.inventory_units_used() == 8


def test_opening_and_crafting_are_discovered_without_hidden_room_spoilers(world):
    initial = observation(world.observe())
    assert set(world.visible_ids()) == {"supply-locker", "shipwright-notebook", "floor-hatch"}
    assert not any(spoiler in json.dumps(initial) for spoiler in
                   ("tide-cellar", "hidden-cove", "shore-key", "signal-float", "rigging-mallet"))
    assert "lash" not in {action["id"] for action in initial["actions"]}
    assert act(world, "read", target="shipwright-notebook")["outcome"] == "APPLIED"
    assert act(world, "open", target="supply-locker")["outcome"] == "APPLIED"
    crafting = next(action for action in world.observe()["actions"] if action["id"] == "lash")
    assert crafting["availability"] == "blocked"
    assert act(world, "lash", target="hardwood-billet", binding="sail-cord",
               item="marlinspike")["outcome"] == "REJECTED"
    assert world.state.entities["hardwood-billet"].location == "supply-locker"
    assert "rigging-mallet" not in world.state.entities


def test_crafting_consumes_materials_once_and_can_carry_both_tools(world):
    act(world, "open", target="supply-locker")
    act(world, "take", target="marlinspike")
    request = ("lash-once", world.revision(), "lash", {
        "target": "hardwood-billet", "binding": "sail-cord", "item": "marlinspike"})
    result = world.act(*request)
    assert result["outcome"] == "APPLIED"
    assert world.act(*request) == result
    assert all(world.state.entities[eid].location is None for eid in ("hardwood-billet", "sail-cord"))
    assert world.state.entities["rigging-mallet"].location == "sail-loft"
    assert "lash" not in {action["id"] for action in world.actions()}
    assert act(world, "take", target="rigging-mallet")["outcome"] == "APPLIED"
    assert world.actor.inventory == ["marlinspike", "rigging-mallet"]


def test_original_opening_is_preserved_but_cove_no_longer_completes_adventure(world):
    craft_mallet(world)
    enter_cellar(world)
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "active"
    assert "shore-key" not in world.visible_ids()
    assert "shore-lock" not in world.visible_ids()
    assert act(world, "open", target="signal-float")["code"] == "JAMMED"
    assert act(world, "open", target="shore-gate")["code"] == "LOCKED"
    assert act(world, "hit", target="signal-float", item="rigging-mallet")["outcome"] == "APPLIED"
    assert world.state.entities["signal-float"].health == 15
    assert "shore-key" not in world.visible_ids()
    assert act(world, "hit", target="signal-float", item="rigging-mallet")["outcome"] == "APPLIED"
    assert "shore-key" in world.visible_ids()
    assert act(world, "take", target="shore-key")["outcome"] == "APPLIED"
    assert {"rigging-mallet", "marlinspike", "shore-key"} <= set(world.actor.inventory)
    assert act(world, "unlock", target="shore-gate", item="shore-key")["outcome"] == "APPLIED"
    act(world, "open", target="shore-gate")
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "active"
    assert "hidden-cove" not in json.dumps(world.observe())
    assert act(world, "go", target="shore-gate")["outcome"] == "APPLIED"
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "active"
    assert world.actor.visited_locations == ["sail-loft", "tide-cellar", "hidden-cove"]


def test_early_exploration_allows_backtracking_but_not_remote_crafting(world):
    act(world, "open", target="supply-locker")
    act(world, "take", target="marlinspike")
    enter_cellar(world)
    assert "lash" not in {action["id"] for action in world.actions()}
    assert act(world, "lash", target="hardwood-billet", binding="sail-cord",
               item="marlinspike")["outcome"] == "REJECTED"
    assert act(world, "hit", target="signal-float", item="marlinspike")["outcome"] != "APPLIED"
    assert world.state.entities["signal-float"].health == 30
    assert act(world, "go", target="floor-hatch")["outcome"] == "APPLIED"
    assert act(world, "lash", target="hardwood-billet", binding="sail-cord",
               item="marlinspike")["outcome"] == "APPLIED"


def test_crafted_tool_and_discoveries_survive_world_reload(world):
    craft_mallet(world)
    enter_cellar(world)
    restored = World(world.scenario, world.path, "world", "npc", "session")
    assert restored.actor.inventory == ["marlinspike", "rigging-mallet"]
    assert restored.actor.body.capacity == 8
    assert restored.actor.location_id == "tide-cellar"
    assert restored.state.entities["sail-cord"].location is None
    assert restored.state.entities["rigging-mallet"].damage == 15
    assert "shore-key" not in restored.visible_ids()
    assert restored.evaluate_goal(restored.scenario.goal_type)["status"] == "active"


def test_later_crossing_requires_a_carried_crafted_item_not_just_an_old_key(world):
    reach_cove(world)
    release(world, "salvage-gate", "shore-key")
    assert "grappling-line" not in world.state.entities
    assert act(world, "unlock", target="rope-crossing", item="shore-key")["code"] == "WRONG_ITEM"
    assert act(world, "go", target="rope-crossing")["code"] == "EXIT_BLOCKED"
    crack(world, "cargo-crate")
    apply(world, "rig_line", target="spare-rope", hook="iron-hook", item="marlinspike")
    assert act(world, "unlock", target="rope-crossing", item="grappling-line")["code"] == "INVALID_ARGUMENTS"
    apply(world, "take", target="grappling-line")
    release(world, "rope-crossing", "grappling-line")
    assert world.state.entities["spare-rope"].location is None
    assert "grappling-line" in world.actor.inventory


def test_undeclared_crafted_keys_and_invalid_scenario_budgets_are_rejected(world):
    data = world.scenario.model_dump(mode="json")
    data["entities"]["rope-crossing"]["key_id"] = "nonexistent-tool"
    with pytest.raises(ValidationError, match="Unknown entity reference"):
        Scenario.model_validate(data)
    data = world.scenario.model_dump(mode="json")
    data["budgets"]["max_actions"] = 0
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_full_thirteen_location_route_reuses_early_items_and_finishes_skiff(world):
    reach_cove(world)
    release(world, "salvage-gate", "shore-key")
    crack(world, "cargo-crate")
    for eid in ("iron-hook", "rusty-blade", "copper-wire", "lamp-oil"):
        apply(world, "take", target=eid)
    apply(world, "rig_line", target="spare-rope", hook="iron-hook", item="marlinspike")
    apply(world, "take", target="grappling-line")
    apply(world, "drop", target="copper-wire")
    release(world, "rope-crossing", "grappling-line")
    apply(world, "take", target="whetstone")
    apply(world, "hone", target="rusty-blade", item="whetstone")
    apply(world, "take", target="brush-knife")
    apply(world, "drop", target="whetstone")
    release(world, "thorn-curtain", "brush-knife")

    apply(world, "look", target="pay-chest")
    apply(world, "unlock", target="pay-lock", item="shore-key")
    apply(world, "open", target="pay-chest")
    for eid in ("watch-token", "flint"):
        apply(world, "take", target=eid)
    apply(world, "drop", target="rigging-mallet")
    apply(world, "take", target="tin-lantern")
    apply(world, "light_lantern", target="tin-lantern", fuel="lamp-oil", item="flint")
    apply(world, "take", target="lit-lantern")
    apply(world, "drop", target="flint")
    release(world, "watch-turnstile", "watch-token")

    apply(world, "look", target="signal-box")
    apply(world, "unlock", target="signal-latch", item="marlinspike")
    apply(world, "open", target="signal-box")
    apply(world, "take", target="signal-lens")
    release(world, "missing-span", "grappling-line")
    apply(world, "look", target="keeper-cabinet")
    apply(world, "unlock", target="keeper-lock", item="watch-token")
    apply(world, "open", target="keeper-cabinet")
    apply(world, "drop", target="watch-token")
    apply(world, "drop", target="shore-key")

    walk_to(world, "sail-loft")
    for eid in ("reflector-foil", "canvas-strip"):
        apply(world, "take", target=eid)
    walk_to(world, "ruined-watchtower")
    release(world, "dark-stair", "lit-lantern")
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "active"
    assert act(world, "align_prism", target="prism-jig", lens="signal-lens",
               backing="reflector-foil", item="brush-knife")["outcome"] == "REJECTED"
    apply(world, "align_prism", target="prism-jig", lens="signal-lens",
          backing="reflector-foil", item="marlinspike")
    apply(world, "take", target="aligned-prism")
    release(world, "beam-shutter", "aligned-prism")

    walk_to(world, "smugglers-camp")
    apply(world, "take", target="rigging-mallet")
    walk_to(world, "wreck-beach")
    apply(world, "take", target="copper-wire")
    walk_to(world, "echo-cavern")
    crack(world, "clay-urn")
    apply(world, "tune_fork", target="fork-blank", wire="copper-wire", item="brush-knife")
    apply(world, "take", target="tuning-fork")
    apply(world, "drop", target="rigging-mallet")
    release(world, "resonance-gate", "tuning-fork")
    apply(world, "drop", target="tuning-fork")
    apply(world, "drop", target="canvas-strip")

    walk_to(world, "smugglers-camp")
    for eid in ("cipher-sheet", "seal-wax"):
        apply(world, "take", target=eid)
    walk_to(world, "ruined-watchtower")
    apply(world, "take", target="signet-mould")
    walk_to(world, "captains-study")
    assert "cast_seal" not in {action["id"] for action in world.actions()}
    apply(world, "decode_chart", target="navigation-desk", chart="salt-chart",
          cipher="cipher-sheet", item="aligned-prism")
    apply(world, "take", target="decoded-chart")
    apply(world, "drop", target="aligned-prism")
    release(world, "bearing-door", "decoded-chart")
    apply(world, "cast_seal", target="seal-press", wax="seal-wax",
          mould="signet-mould", item="lit-lantern")
    apply(world, "take", target="captains-seal")
    apply(world, "drop", target="signet-mould")
    apply(world, "drop", target="decoded-chart")
    release(world, "quay-portcullis", "captains-seal")
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "active"
    assert "rig_skiff" not in {action["id"] for action in world.actions()}

    walk_to(world, "captains-study")
    apply(world, "take", target="canvas-strip")
    walk_to(world, "escape-quay")
    apply(world, "drop", target="brush-knife")
    final_args = {"target": "skiff-hull", "sail": "canvas-strip", "rigging": "grappling-line",
                  "item": "marlinspike", "knife": "brush-knife"}
    assert act(world, "rig_skiff", **final_args)["code"] == "NOT_CARRIED"
    apply(world, "take", target="brush-knife")
    request = ("finish-skiff", world.revision(), "rig_skiff", final_args)
    result = world.act(*request)
    assert result["outcome"] == "APPLIED"
    assert world.act(*request) == result
    assert world.state.entities["ready-skiff"].location == "launch-slip"
    assert all(world.state.entities[eid].location is None
               for eid in ("canvas-strip", "grappling-line", "skiff-hull"))
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "achieved"
    assert world.actor.visited_locations == LOCATIONS
    assert world.actor.action_clock < world.scenario.budgets.max_actions
    restored = World(world.scenario, world.path, "world", "npc", "session")
    assert restored.evaluate_goal(restored.scenario.goal_type)["status"] == "achieved"

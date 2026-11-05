import pytest
from pydantic import ValidationError

from npc_sim.models import Entity, WorldState
from test_world import act, world


def test_capacity_sums_unit_costs_instead_of_counting_items(world):
    world.actor.body.capacity = 3
    world.state.entities["rock"].inventory_units = 2
    act(world, "take", target="rock")
    assert world.observe()["self"]["body"]["carrying"] == {"rule": "inventory_units", "limit": 3, "used": 2}
    act(world, "open", target="box")
    assert act(world, "take", target="key")["outcome"] == "APPLIED"
    assert len(world.actor.inventory) == 2
    assert world.inventory_units_used() == 3
    assert world.observe()["self"]["inventory"][0]["inventory_units"] == 2
    take = next(action for action in world.actions() if action["id"] == "take")
    assert take["availability"] == "blocked"
    clock = world.actor.action_clock
    assert act(world, "take", target="box")["code"] == "CAPACITY_EXCEEDED"
    assert world.state.entities["box"].location == "room"
    assert world.actor.action_clock == clock
    act(world, "drop", target="rock")
    assert world.inventory_units_used() == 1
    assert act(world, "take", target="box")["outcome"] == "APPLIED"
    assert world.inventory_units_used() == 2


def test_oversized_local_items_cannot_be_taken_even_with_empty_inventory(world):
    world.state.entities["rock"].inventory_units = 2
    assert act(world, "take", target="rock")["code"] == "CAPACITY_EXCEEDED"
    assert world.actor.inventory == []
    assert world.actor.action_clock == 0
    assert world.state.entities["rock"].location == "room"
    assert next(action for action in world.actions() if action["id"] == "take")["availability"] == "available"
    world.state.entities["box"].inventory_units = 2
    assert next(action for action in world.actions() if action["id"] == "take")["availability"] == "blocked"


def test_discovered_items_in_another_room_cannot_be_picked_up(world):
    act(world, "open", target="box")
    act(world, "take", target="key")
    act(world, "unlock", target="door", item="key")
    act(world, "open", target="door")
    act(world, "drop", target="key")
    act(world, "go", target="door")
    assert world.actor.location_id == "outside"
    clock = world.actor.action_clock
    assert act(world, "take", target="key")["outcome"] == "REJECTED"
    assert world._apply("take", {"target": "key"})[1] == "OUT_OF_REACH"
    assert world.actor.inventory == []
    assert world.actor.action_clock == clock
    assert world.state.entities["key"].location == "room"
    act(world, "go", target="door")
    assert act(world, "take", target="key")["outcome"] == "APPLIED"


def test_nested_local_container_contents_remain_reachable(world):
    world.state.entities["box"].open = True
    world.state.entities["inner"] = Entity(
        name="Inner box", kind="container", description="An inner box.",
        location="box", open=True, takable=True)
    world.state.entities["key"].location = "inner"
    assert world.in_current_room("key")
    assert act(world, "take", target="key")["outcome"] == "APPLIED"
    assert world.inventory_units_used() == 1


def test_old_json_defaults_missing_unit_costs_to_one(world):
    saved = world.state.model_dump(mode="json")
    del saved["entities"]["rock"]["inventory_units"]
    assert WorldState.model_validate(saved).entities["rock"].inventory_units == 1


@pytest.mark.parametrize("units", [0, -1, 1.5, True, "1"])
def test_invalid_inventory_unit_costs_are_rejected(units):
    with pytest.raises(ValidationError):
        Entity(name="Item", kind="item", description="An item.", inventory_units=units)

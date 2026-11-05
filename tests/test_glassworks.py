import json

import pytest
from pydantic import ValidationError

from npc_sim.controller import Controller
from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.world import World
from test_controller import ROOT, TestProxy, configuration
from test_world import act


ROUTE = [
    ("read", {"target": "work-order"}),
    ("open", {"target": "drawers"}),
    ("read", {"target": "tooling-card"}),
    ("go", {"target": "connecting-door"}),
    ("read", {"target": "reference-card"}),
    ("go", {"target": "connecting-door"}),
    ("plug", {"target": "bonding-lamp", "outlet": "workshop-socket"}),
    ("turn_on", {"target": "bonding-lamp"}),
    ("bond_tool", {"disc": "glass-disc", "arbor": "arbor", "machine": "bonding-lamp"}),
    ("turn_off", {"target": "bonding-lamp"}),
    ("unplug", {"target": "bonding-lamp"}),
    ("plug", {"target": "forming-press", "outlet": "workshop-socket"}),
    ("turn_on", {"target": "forming-press"}),
    ("form_rotor", {"blank": "rotor-blank", "support": "forming-mandrel", "machine": "forming-press"}),
    ("strip_tool", {"target": "tooled-rotor"}),
    ("turn_off", {"target": "forming-press"}),
    ("unplug", {"target": "forming-press"}),
    ("take", {"target": "driver"}),
    ("fit_spring", {"rotor": "rotor", "spring": "spring-dull", "tool": "driver"}),
    ("drop", {"target": "driver"}),
    ("take", {"target": "glass-gauge"}),
    ("go", {"target": "connecting-door"}),
    ("plug", {"target": "calibrator", "outlet": "reference-socket"}),
    ("turn_on", {"target": "calibrator"}),
    ("zero_reference", {"instrument": "calibrator", "gauge": "glass-gauge", "coupon": "zero-coupon"}),
    ("drop", {"target": "glass-gauge"}),
    ("go", {"target": "connecting-door"}),
    ("take", {"target": "open-movement"}),
    ("go", {"target": "connecting-door"}),
    ("drop", {"target": "open-movement"}),
    ("take", {"target": "glass-gauge"}),
    ("calibrate_movement", {"target": "open-movement", "instrument": "calibrator",
                            "gauge": "glass-gauge", "coupon": "test-coupon", "surface": "reference-table"}),
    ("drop", {"target": "glass-gauge"}),
    ("take", {"target": "calibrated-movement"}),
    ("go", {"target": "connecting-door"}),
    ("drop", {"target": "calibrated-movement"}),
    ("plug", {"target": "bonding-lamp", "outlet": "workshop-socket"}),
    ("turn_on", {"target": "bonding-lamp"}),
    ("take", {"target": "driver"}),
    ("seal_relay", {"movement": "calibrated-movement", "housing": "housing", "seal": "seal",
                    "tool": "driver", "machine": "bonding-lamp"}),
    ("drop", {"target": "driver"}),
    ("take", {"target": "sealed-relay"}),
    ("go", {"target": "connecting-door"}),
    ("commission_relay", {"target": "sealed-relay", "fixture": "acceptance-cradle"}),
]


@pytest.fixture
def world(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "glassworks-relay.json"))
    return World(scenario, tmp_path / "world-state.json", "world", "npc", "session")


def perform(world, route):
    for action, arguments in route:
        result = act(world, action, **arguments)
        assert result["outcome"] == "APPLIED", (action, result)


def test_valid_route_requires_commissioning_and_survives_reload(world):
    initial = json.dumps(world.observe())
    for hidden in ("spring-dull", "reference-card", "wrong-spring-movement", "forming-mandrel"):
        assert hidden not in initial
    for action, arguments in ROUTE[:-1]:
        perform(world, [(action, arguments)])
        assert world.evaluate_goal("entity_prepared")["status"] == "active"
    perform(world, ROUTE[-1:])
    assert world.evaluate_goal("entity_prepared")["status"] == "achieved"
    assert world.state.entities["spring-bright"].location == "drawers"
    assert world.state.entities["glass-gauge"].location == "metrology-room"
    reloaded = World(world.scenario, world.path, "world", "npc", "session")
    assert reloaded.evaluate_goal("entity_prepared")["status"] == "achieved"
    assert len(ROUTE) <= world.scenario.budgets.max_actions


def test_power_capacity_and_remote_access_are_hard_constraints(world):
    perform(world, ROUTE[:8])
    assert act(world, "plug", target="forming-press", outlet="workshop-socket")["code"] == "OUTLET_OCCUPIED"
    assert act(world, "unplug", target="bonding-lamp")["code"] == "POWER_ON"
    perform(world, ROUTE[8:19])
    assert act(world, "take", target="open-movement")["code"] == "CAPACITY_EXCEEDED"
    perform(world, ROUTE[19:24])
    assert act(world, "fit_spring", rotor="rotor", spring="spring-dull", tool="driver")["outcome"] == "REJECTED"
    assert world.evaluate_goal("entity_prepared")["status"] == "active"


@pytest.mark.parametrize("trap", [
    "wrong-spring", "early-seal", "unset-zero", "bare-press", "discard-disc", "discard-gauge",
])
def test_irreversible_mistakes_consume_resources_and_persist_failure(world, trap):
    if trap == "discard-disc":
        perform(world, ROUTE[:2])
        action, arguments = "discard_reference", {"target": "glass-disc"}
        consumed = ["glass-disc"]
    elif trap == "bare-press":
        perform(world, ROUTE[:2])
        perform(world, [
            ("plug", {"target": "forming-press", "outlet": "workshop-socket"}),
            ("turn_on", {"target": "forming-press"}),
        ])
        action, arguments = "form_rotor", {"blank": "rotor-blank", "support": "arbor", "machine": "forming-press"}
        consumed = ["rotor-blank", "arbor"]
    elif trap == "discard-gauge":
        perform(world, ROUTE[:15])
        action, arguments = "discard_reference", {"target": "glass-gauge"}
        consumed = ["glass-gauge"]
    elif trap == "wrong-spring":
        perform(world, ROUTE[:18])
        action, arguments = "fit_spring", {"rotor": "rotor", "spring": "spring-bright", "tool": "driver"}
        consumed = ["rotor", "spring-bright"]
    elif trap == "early-seal":
        perform(world, ROUTE[:19])
        perform(world, [
            ("plug", {"target": "bonding-lamp", "outlet": "workshop-socket"}),
            ("turn_on", {"target": "bonding-lamp"}),
        ])
        action, arguments = "seal_relay", {
            "movement": "open-movement", "housing": "housing", "seal": "seal",
            "tool": "driver", "machine": "bonding-lamp",
        }
        consumed = ["open-movement", "housing", "seal"]
    else:
        perform(world, ROUTE[:24])
        perform(world, ROUTE[25:31])
        action, arguments = ROUTE[31]
        consumed = ["open-movement", "test-coupon"]
    result = act(world, action, **arguments)
    assert result["outcome"] == "APPLIED"
    assert all(world.state.entities[eid].location is None for eid in consumed)
    failure = world.evaluate_goal("entity_prepared")
    assert failure["status"] == "failed"
    assert failure["reason"]
    assert failure["evidence"]["irreversible_output"] in world.scenario.failure_outputs
    assert act(world, action, **arguments)["outcome"] == "REJECTED"
    assert "commissioned-relay" not in world.state.entities
    reloaded = World(world.scenario, world.path, "world", "npc", "session")
    assert reloaded.evaluate_goal("entity_prepared") == failure


@pytest.mark.parametrize("target", ["glass-disc", "driver", "forming-press", "test-coupon"])
def test_breaking_irreplaceable_parts_fails_even_before_goal_discovery(world, target):
    world.state.entities[target].health = 0
    result = world.evaluate_goal("entity_prepared")
    assert result["status"] == "failed"
    assert result["evidence"]["broken_required_entity"] == target


@pytest.mark.parametrize("field,value", [
    ("failure_outputs", {"not-an-output": "Invalid reference"}),
    ("failure_outputs", {"crushed-blank": " "}),
    ("failure_outputs", {"commissioned-relay": "Cannot also fail"}),
    ("required_intact_entities", ["not-an-entity"]),
])
def test_failure_configuration_validates_references(world, field, value):
    data = world.scenario.model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_controller_stops_on_authoritative_success_or_irreversible_failure(configuration, fail):
    data, config = configuration
    route = (ROUTE[:2] + [("discard_reference", {"target": "glass-disc"})]) if fail else ROUTE
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, route)
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "glassworks-relay"})
        for _ in route:
            await controller.command("step")
        assert controller.run["status"] == ("blocked" if fail else "succeeded")
        assert controller.run["verification"]["status"] == ("failed" if fail else "achieved")
        assert controller.run["actions"] == len(route)
        if fail:
            assert "reference disc" in controller.run["verification"]["reason"]
            assert controller.run["completion_analysis"]["verdict"] == "fail"
    finally:
        await controller.shutdown()

import copy
import json

import pytest
from pydantic import ValidationError

from npc_sim.contracts import observation
from npc_sim.controller import Controller
from npc_sim.interactions import WorldEditPlan, apply_edit, validate_edit
from npc_sim.models import Decision, SecondaryCondition, SecondaryGoalSpec
from npc_sim.storage import read_json
from npc_sim.world import World
from test_controller import SOLUTION, assessment_response
from test_interactions import ROOT, InteractionProxy, act, configuration, nook_plan, world


def goal_plan(kind="discovered", target="key", actor_ids=None, **condition):
    return WorldEditPlan(summary="Add an optional, independently verified side goal.", secondary_goals={
        "find-key": SecondaryGoalSpec(
            title="Find the key", description="Find the key without replacing the main objective.",
            assigned_actor_ids=actor_ids or ["npc"],
            conditions=[SecondaryCondition(kind=kind, target=target, **condition)])
    })


def test_goal_only_preview_does_not_change_main_goal_or_world(world):
    plan = goal_plan()
    before = world.snapshot()
    primary = world.scenario.model_dump()
    validate_edit(world, plan)
    assert world.snapshot() == before
    revision = world.revision()
    result = apply_edit(world, "side-goal", revision, plan)
    assert result["added_goal_ids"] == ["find-key"]
    assert result["added_ids"] == []
    assert world.scenario.model_dump() == primary
    assert world.evaluate_goal(world.scenario.goal_type)["status"] == "active"
    assert world.state.secondary_goals["find-key"].status == "active"
    assert apply_edit(world, "side-goal", revision, plan) == result


def test_find_goal_uses_observations_not_instructions_and_stays_achieved(world):
    apply_edit(world, "side-goal", world.revision(), goal_plan())
    observed = observation(world.observe())
    assert observed["secondary_goals"][0]["status"] == "active"
    assert "key" not in world.actor.discovered_entities
    act(world, "look", target="box")
    assert world.state.secondary_goals["find-key"].status == "active"
    act(world, "open", target="box")
    goal = world.state.secondary_goals["find-key"]
    assert goal.status == "achieved"
    assert goal.condition_met == [True]
    assert goal.completed_revision
    act(world, "take", target="key")
    act(world, "unlock", target="door", item="key")
    act(world, "open", target="door")
    act(world, "go", target="door")
    assert world.state.secondary_goals["find-key"].status == "achieved"
    loaded = World(world.scenario, world.path, "world", "npc", "session")
    assert loaded.state.secondary_goals["find-key"] == goal
    assert "key" in loaded.actor.discovered_entities


def test_concealed_new_goal_target_is_not_discovered_by_receiving_goal(world):
    plan = nook_plan()
    plan.secondary_goals = goal_plan(target="spare-key").secondary_goals
    apply_edit(world, "side-goal", world.revision(), plan)
    observed = world.observe()
    assert "rescue-nook" not in json.dumps(observed)
    assert "discovery_target" not in json.dumps(observed)
    assert world.state.secondary_goals["find-key"].status == "active"
    act(world, "search", target="rescue-wall")
    assert world.state.secondary_goals["find-key"].status == "active"
    act(world, "open", target="rescue-nook")
    assert world.state.secondary_goals["find-key"].status == "achieved"


def test_assignments_are_private_and_any_all_semantics_are_explicit(world):
    other = world.bind("other", "other-session")
    all_plan = goal_plan(target="key", actor_ids=["npc", "other"], quantifier="all")
    any_plan = goal_plan(target="key", actor_ids=["npc", "other"])
    all_plan.secondary_goals["any-find"] = any_plan.secondary_goals["find-key"]
    apply_edit(world, "team-goals", world.revision(), all_plan)
    act(world, "open", target="box")
    assert world.state.secondary_goals["any-find"].status == "achieved"
    assert world.state.secondary_goals["find-key"].status == "active"
    other.observe()
    assert world.state.secondary_goals["find-key"].status == "achieved"
    private = goal_plan(kind="carrying", target="rock")
    private.secondary_goals["only-npc"] = private.secondary_goals.pop("find-key")
    apply_edit(world, "private-goal", world.revision(), private)
    assert "only-npc" in [goal["id"] for goal in world.observe()["secondary_goals"]]
    assert "only-npc" not in [goal["id"] for goal in other.observe()["secondary_goals"]]


def test_delivery_to_recipient_and_multiple_simultaneous_conditions(world):
    other = world.bind("other", "other-session")
    plan = goal_plan(kind="carrying", actor_id="other")
    plan.secondary_goals["find-key"].conditions.append(SecondaryCondition(kind="at_location", target="room", actor_id="other"))
    apply_edit(world, "delivery", world.revision(), plan)
    act(world, "open", target="box")
    act(world, "take", target="key")
    assert world.state.secondary_goals["find-key"].condition_met == [False, True]
    assert act(world, "give", target="key", recipient="other")["outcome"] == "APPLIED"
    assert world.state.secondary_goals["find-key"].status == "achieved"
    assert "key" in other.actor.inventory


@pytest.mark.parametrize("kind,target,action,args", [
    ("opened", "box", "open", {"target": "box"}),
    ("carrying", "rock", "take", {"target": "rock"}),
    ("item_at", "rock", "drop", {"target": "rock"}),
    ("unlocked", "door", "unlock", {"target": "door", "item": "key"}),
    ("at_location", "outside", "go", {"target": "door"}),
])
def test_world_verifies_supported_condition_types(world, kind, target, action, args):
    if kind == "item_at":
        act(world, "take", target="rock")
    if kind in {"unlocked", "at_location"}:
        act(world, "open", target="box")
        act(world, "take", target="key")
    if kind == "at_location":
        act(world, "unlock", target="door", item="key")
        act(world, "open", target="door")
    plan = goal_plan(kind=kind, target=target, **({"location_id": "room"} if kind == "item_at" else {}))
    apply_edit(world, "condition", world.revision(), plan)
    assert world.state.secondary_goals["find-key"].status == "active"
    assert act(world, action, **args)["outcome"] == "APPLIED"
    assert world.state.secondary_goals["find-key"].status == "achieved"


def test_reading_is_verified_separately_from_seeing_text(world):
    from npc_sim.models import Entity
    plan = goal_plan(kind="read", target="side-note")
    plan.additions["side-note"] = Entity(name="Note", kind="item", description="A note.", location="room",
                                          readable_text="Look for a way outside.")
    apply_edit(world, "reading", world.revision(), plan)
    world.observe()
    assert world.state.secondary_goals["find-key"].status == "active"
    act(world, "read", target="side-note")
    assert world.state.secondary_goals["find-key"].status == "achieved"

def test_broken_attached_lock_does_not_count_as_verified_unlocking(world):
    lock = world.lock_for(world.state.entities["door"])
    lock.health = 0
    lock.locked = False
    apply_edit(world, "unlock-goal", world.revision(), goal_plan(kind="unlocked", target="door"))
    assert world.state.secondary_goals["find-key"].status == "active"


@pytest.mark.parametrize("failure", ["actor", "target", "duplicate", "not-portable", "not-location", "not-openable",
                                   "not-lockable", "not-readable", "destination"])
def test_invalid_goals_are_rejected_atomically(world, failure):
    plan = goal_plan()
    goal = plan.secondary_goals["find-key"]
    if failure == "actor":
        goal.assigned_actor_ids = ["unknown"]
    elif failure == "target":
        goal.conditions[0].target = "unknown"
    elif failure == "duplicate":
        apply_edit(world, "first", world.revision(), plan)
    elif failure == "not-portable":
        goal.conditions = [SecondaryCondition(kind="carrying", target="room")]
    elif failure == "not-location":
        goal.conditions = [SecondaryCondition(kind="at_location", target="key")]
    elif failure == "not-openable":
        goal.conditions = [SecondaryCondition(kind="opened", target="key")]
    elif failure == "not-lockable":
        goal.conditions = [SecondaryCondition(kind="unlocked", target="key")]
    elif failure == "not-readable":
        goal.conditions = [SecondaryCondition(kind="read", target="key")]
    else:
        goal.conditions = [SecondaryCondition(kind="item_at", target="key", location_id="unknown")]
    before = world.snapshot()
    with pytest.raises(ValueError):
        apply_edit(world, "invalid", world.revision(), plan)
    assert world.snapshot() == before


def test_invalid_or_empty_condition_schemas_cannot_claim_completion():
    with pytest.raises(ValidationError):
        WorldEditPlan(summary="Do nothing")
    with pytest.raises(ValidationError):
        SecondaryCondition(kind="run_code", target="key")
    with pytest.raises(ValidationError):
        SecondaryCondition(kind="item_at", target="key")
    with pytest.raises(ValidationError):
        SecondaryCondition(kind="opened", target="box", actor_id="npc")
    with pytest.raises(ValidationError):
        WorldEditPlan.model_validate({"summary": "Grant success", "secondary_goals": {
            "cheat": {**goal_plan().secondary_goals["find-key"].model_dump(), "status": "achieved"}}})


class GoalProxy(InteractionProxy):
    def __init__(self, real, plan):
        super().__init__(real)
        self.plan = plan
        self.actions = iter(SOLUTION)

    async def propose_world_edit(self, *args):
        return self.plan, {}

    def context(self, *args):
        return self.real.context(*args)

    async def choose(self, *args):
        action, arguments = next(self.actions)
        return Decision(action_id=action, arguments=arguments,
                        justification="Follow the test plan.", expected_outcome="World-verified progress."), {}

    def completion_context(self, payload):
        return self.real.completion_context(payload)

    async def assess(self, model, messages, correlation_id, timeout):
        return assessment_response(messages), {}


@pytest.mark.asyncio
async def test_real_mcp_approval_progress_audit_and_main_goal_independence(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    plan = goal_plan(actor_ids=["npc-001"])
    plan.secondary_goals["optional-rock"] = SecondaryGoalSpec(
        title="Collect a rock", description="Optional collection.",
        assigned_actor_ids=["npc-001"], conditions=[SecondaryCondition(kind="carrying", target="rock")])
    controller.proxy = GoalProxy(controller.proxy, plan)
    await controller.start_worker()
    try:
        initial = await controller.command("start")
        controller.run["dungeon_master"]["config"]["enabled"] = False
        primary = copy.deepcopy(controller.run["goal"])
        run_id = controller.run["id"]
        proposed = await controller.command("propose_edit", {"run_id": run_id, "instruction": "Add optional goals."})
        assert proposed["world"] == initial["world"]
        proposal = next(iter(proposed["run"]["world_edit_proposals"].values()))
        planner_input = json.loads(controller.proxy.plans[0][1]["content"])
        assert planner_input["world"]["actor_names"]["npc-001"] == controller.run["agent_profiles"]["npc-001"]["name"]
        approved = await controller.command("approve_edit", {"run_id": run_id, "proposal_id": proposal["id"]})
        assert controller.run["goal"] == primary
        assert approved["world"]["secondary_goals"]["find-key"]["status"] == "active"
        assert len(approved["observation"]["secondary_goals"]) == 2
        for _ in SOLUTION:
            await controller.command("step")
        state = controller.status()
        assert state["run"]["status"] == "succeeded"
        assert state["world"]["secondary_goals"]["find-key"]["status"] == "achieved"
        assert state["world"]["secondary_goals"]["optional-rock"]["status"] == "active"
        achieved = [event for event in controller.events.events if event["kind"] == "secondary_goal_achieved"]
        assert len(achieved) == 1
        assert achieved[0]["payload"]["title"] == "Find the key"
        assert achieved[0]["payload"]["verification_revision"]
        controller.checkpoint()
        assert len([event for event in controller.events.events if event["kind"] == "secondary_goal_achieved"]) == 1
        persisted = read_json(controller.world.state_path)
        assert persisted["secondary_goals"]["find-key"]["status"] == "achieved"
    finally:
        await controller.shutdown()

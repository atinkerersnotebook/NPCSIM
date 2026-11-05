import copy
import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from npc_sim.controller import Controller
from npc_sim.interactions import NpcReply, WorldEditPlan, apply_edit, validate_edit
from npc_sim.models import AppConfig, Entity, Scenario
from npc_sim.storage import read_json
from npc_sim.world import World


ROOT = Path(__file__).resolve().parent.parent


def nook_plan():
    return WorldEditPlan(summary="Add a searchable wall, concealed nook and spare door key.", additions={
        "rescue-wall": Entity(name="Cracked wall", kind="wall", description="A wall with weathered mortar.", location="room"),
        "rescue-nook": Entity(name="Concealed nook", kind="container", description="A small recess with a stone lid.",
                             location="room", hidden=True, discovery_target="rescue-wall", open=False),
        "spare-key": Entity(name="Spare door key", kind="item", description="A small brass key.",
                            location="rescue-nook", takable=True),
    }, key_bindings={"door": "spare-key"})


@pytest.fixture
def world(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "escape-room.json"))
    return World(scenario, tmp_path / "world.json", "world", "npc", "session")


def act(world, action, **arguments):
    return world.act(uuid4().hex, world.revision(), action, arguments)


def test_hidden_nook_requires_search_then_open_without_action_leaks(world):
    before = world.snapshot()
    plan = nook_plan()
    validate_edit(world, plan)
    assert world.snapshot() == before
    result = apply_edit(world, "edit", world.revision(), plan)
    assert result["added_ids"] == list(plan.additions)
    observed = world.observe()
    assert "rescue-wall" in world.visible_ids()
    assert "rescue-nook" not in json.dumps(observed)
    assert "spare-key" not in json.dumps(observed)
    assert "discovery_target" not in json.dumps(observed)
    assert act(world, "search", target="rescue-nook")["outcome"] == "REJECTED"
    assert act(world, "open", target="rescue-nook")["outcome"] == "REJECTED"
    assert act(world, "search", target="box")["outcome"] == "NO_CHANGE"
    assert act(world, "search", target="rescue-wall")["code"] == "FEATURE_DISCOVERED"
    assert "rescue-nook" in world.visible_ids()
    assert "spare-key" not in world.visible_ids()
    assert act(world, "open", target="rescue-nook")["outcome"] == "APPLIED"
    assert "spare-key" in world.visible_ids()
    assert act(world, "take", target="spare-key")["outcome"] == "APPLIED"
    assert act(world, "unlock", target="door", item="spare-key")["outcome"] == "APPLIED"
    assert not world.state.entities["door"].locked
    lock = world.lock_for(world.state.entities["door"])
    assert lock.key_id == "key"
    assert lock.alternative_key_ids == ["spare-key"]
    assert plan.additions["rescue-nook"].hidden


def test_original_key_still_works_and_spare_prevents_false_defeat(world):
    apply_edit(world, "edit", world.revision(), nook_plan())
    world.state.entities["key"].health = 0
    assert not world.unusable_exit(world.state.entities["door"])
    world.state.entities["spare-key"].health = 0
    assert world.unusable_exit(world.state.entities["door"])
    world.state.entities["key"].health = 100
    act(world, "open", target="box")
    act(world, "take", target="key")
    assert act(world, "unlock", target="door", item="key")["outcome"] == "APPLIED"


def test_apply_is_atomic_revision_checked_and_idempotent(world, monkeypatch):
    revision = world.revision()
    plan = nook_plan()
    before = world.snapshot()
    with pytest.raises(ValueError, match="world changed"):
        apply_edit(world, "edit", "old", plan)
    assert world.snapshot() == before
    def fail():
        raise OSError("Disk unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(world, "save", fail)
        with pytest.raises(OSError, match="Disk unavailable"):
            apply_edit(world, "edit", revision, plan)
    assert world.snapshot() == before
    result = apply_edit(world, "edit", revision, plan)
    applied = world.snapshot()
    assert apply_edit(world, "edit", revision, plan) == result
    assert world.snapshot() == applied
    changed = plan.model_copy(deep=True)
    changed.summary = "Different plan"
    with pytest.raises(ValueError, match="reused"):
        apply_edit(world, "edit", revision, changed)


@pytest.mark.parametrize("failure", ["overwrite", "inventory", "cycle", "anchor", "remote", "protected", "jammed", "body", "reference"])
def test_invalid_proposals_never_mutate_world(world, failure):
    plan = nook_plan()
    if failure == "overwrite":
        plan.additions["box"] = plan.additions.pop("rescue-wall")
    elif failure == "inventory":
        plan.additions["spare-key"].location = world.actor_id
    elif failure == "cycle":
        plan.additions["rescue-nook"].location = "spare-key"
    elif failure == "anchor":
        plan.additions["rescue-nook"].discovery_target = "rescue-nook"
    elif failure == "remote":
        plan.additions["rescue-wall"].location = "outside"
    elif failure == "protected":
        world.state.entities["door"].required_challenges = ["protected"]
    elif failure == "jammed":
        world.state.entities["door"].jammed = True
    elif failure == "body":
        plan.additions["npc"] = Entity(name="Actor impostor", kind="item", description="Invalid.", location="room")
    else:
        plan.additions["rescue-nook"].discovery_target = "unknown"
    before = world.snapshot()
    with pytest.raises(ValueError):
        apply_edit(world, "edit", world.revision(), plan)
    assert world.snapshot() == before


def test_neutral_search_exists_without_secret_and_anatomy_still_applies(world):
    search = next(action for action in world.actions() if action["id"] == "search")
    assert "room" in search["arguments_schema"]["properties"]["target"]["enum"]
    assert act(world, "search", target="room")["outcome"] == "NO_CHANGE"
    apply_edit(world, "edit", world.revision(), nook_plan())
    world.actor.body.grips = ["mouth"]
    world.actor.body.capabilities = ["move", "manipulate"]
    assert act(world, "search", target="rescue-wall")["code"] == "FEATURE_DISCOVERED"
    assert act(world, "open", target="rescue-nook")["code"] == "BODY_HANDLING_REQUIRED"
    world.state.entities["rescue-nook"].open = True
    assert act(world, "take", target="spare-key")["code"] == "BODY_HANDLING_REQUIRED"


class InteractionProxy:
    def __init__(self, real):
        self.real = real
        self.questions = []
        self.plans = []
        self.fail = False

    def question_context(self, *args):
        result = self.real.question_context(*args)
        self.questions.append(result)
        return result

    def world_edit_context(self, *args):
        result = self.real.world_edit_context(*args)
        self.plans.append(result)
        return result

    async def answer_question(self, *args):
        if self.fail:
            raise RuntimeError("Proxy unavailable")
        return NpcReply(answer="I can search the walls, but I have not found a key."), {}

    async def propose_world_edit(self, *args):
        return nook_plan(), {}


@pytest.fixture
def configuration(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", data / "scenarios")
    config = AppConfig.model_validate(read_json(ROOT / "data" / "config.json"))
    config.proxy.model = "test-model"
    return data, config


@pytest.mark.asyncio
async def test_proposal_and_approval_over_real_mcp_are_separate_and_persisted(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    proxy = InteractionProxy(controller.proxy)
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        run_id = controller.run["id"]
        before = controller.status()["world"]
        state = await controller.command("propose_edit", {"run_id": run_id, "instruction": "Add a hidden nook with a spare key."})
        assert state["world"] == before
        proposal = next(iter(state["run"]["world_edit_proposals"].values()))
        assert proposal["status"] == "pending"
        assert state["run"]["model_calls"] == 1
        state = await controller.command("approve_edit", {"run_id": run_id, "proposal_id": proposal["id"]})
        assert state["run"]["world_edit_proposals"][proposal["id"]]["status"] == "applied"
        assert state["world"]["entities"]["rescue-nook"]["hidden"]
        assert "rescue-nook" not in json.dumps(state["observation"])
        assert state["run"]["actions"] == 0
        assert state["run"]["cycles"] == 0
        assert state["run"]["model_calls"] == 1
        assert "operator:" + proposal["id"] not in json.dumps(state["observation"])
        saved = read_json(controller.events.directory / "run.json")
        assert saved["world_edit_proposals"][proposal["id"]]["status"] == "applied"
        with pytest.raises(ValueError, match="pending proposal"):
            await controller.command("approve_edit", {"run_id": run_id, "proposal_id": proposal["id"]})
        assert state["world"] == controller.status()["world"]
        with pytest.raises(ValueError, match="current active"):
            await controller.command("npc_chat", {"run_id": "foreign-run", "actor_id": "npc-001", "question": "Hello?"})
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_chat_is_private_remembered_and_restores_actor_even_on_failure(configuration):
    data, config = configuration
    config.world.scenario = "storm-signal"
    controller = Controller(ROOT, data, config)
    proxy = InteractionProxy(controller.proxy)
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        run_id, active = controller.run["id"], controller.run["actor_id"]
        controller.agents["badger"]["memory"]["only_badger_knows"] = "PRIVATE_BADGER_FACT"
        before = controller.status()["world"]
        original_memories = {actor: copy.deepcopy(saved["memory"]) for actor, saved in controller.agents.items()}
        state = await controller.command("npc_chat", {"run_id": run_id, "actor_id": "otter", "question": "What could you try?"})
        assert state["run"]["actor_id"] == active
        assert state["world"] == before
        assert state["run"]["actions"] == 0 and state["run"]["cycles"] == 0
        assert not state["in_flight"]
        assert "operator_conversation" in controller.agents["otter"]["memory"]
        for actor in ["raven", "badger"]:
            assert controller.agents[actor]["memory"] == original_memories[actor]
        payload = json.loads(proxy.questions[0][1]["content"])
        assert payload["observation"]["self"]["actor_id"] == "otter"
        assert "PRIVATE_BADGER_FACT" not in json.dumps(proxy.questions[0])
        assert "world_edit_proposals" not in json.dumps(proxy.questions[0])
        assert (await controller.world.operation("observe", {}))["self"]["actor_id"] == active
        event = next(event for event in controller.events.events if event["kind"] == "npc_chat")
        assert event["actor_id"] == "otter"
        assert event["session_id"] == controller.agents["otter"]["session_id"]
        proxy.fail = True
        memory = copy.deepcopy(controller.agents["otter"]["memory"])
        with pytest.raises(RuntimeError, match="Proxy unavailable"):
            await controller.command("npc_chat", {"run_id": run_id, "actor_id": "otter", "question": "Again?"})
        assert controller.agents["otter"]["memory"] == memory
        assert not controller.in_flight
        assert (await controller.world.operation("observe", {}))["self"]["actor_id"] == active
        assert controller.events.events[-1]["kind"] == "operator_interaction_error"
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_chat_pauses_running_adventure_and_enforces_budget(configuration):
    data, config = configuration
    controller = Controller(ROOT, data, config)
    proxy = InteractionProxy(controller.proxy)
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        controller.run["status"] = "running"
        controller._activate_clock()
        request = {"run_id": controller.run["id"], "actor_id": controller.run["actor_id"], "question": "Can you help?"}
        await controller.command("npc_chat", request)
        assert controller.run["status"] == "paused"
        assert controller.started_clock is None
        assert controller.run["actions"] == 0
        assert controller.run["model_calls"] == 1
        controller.run["model_calls"] = controller.budgets.max_model_calls
        with pytest.raises(ValueError, match="budget is exhausted"):
            await controller.command("npc_chat", request)
        assert len(controller.memory["operator_conversation"]) == 1
        assert not controller.in_flight
    finally:
        await controller.shutdown()

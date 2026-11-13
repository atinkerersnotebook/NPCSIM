import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from npc_sim.agent_documents import ROOT_DOCUMENTS, default_documents
from npc_sim.agents import assign_profiles, default_profile_id, load_profiles, profile_context, save_profile, slots
from npc_sim.controller import Controller
from npc_sim.contracts import goal_result
from npc_sim.models import AppConfig, Scenario
from npc_sim.proxy import Proxy
from npc_sim.storage import read_json, write_json
from npc_sim.world import World
from test_controller import TestProxy


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def data(tmp_path):
    target = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "scenarios", target / "scenarios")
    return target


def scenario(data, scenario_id):
    return Scenario.model_validate(read_json(data / "scenarios" / f"{scenario_id}.json"))


def test_every_default_profile_preserves_existing_bodies_and_roles(data):
    profiles = load_profiles(data)
    assert {"raven", "otter", "badger"} <= profiles.keys()
    assert len({profiles[key].personality for key in ("raven", "otter", "badger")}) == 3
    for path in (data / "scenarios").glob("*.json"):
        original = Scenario.model_validate(read_json(path))
        assigned, selected = assign_profiles(original, profiles, None)
        assert assigned.body == original.body
        assert [agent.role for agent in assigned.agents] == [agent.role for agent in original.agents]
        assert [(agent.body or assigned.body) for agent in assigned.agents] == [
            (agent.body or original.body) for agent in original.agents]
        assert set(selected) == set(slots(original))
        for slot in slots(original):
            assert selected[slot].id == default_profile_id(original, slot)
    assert "light_work" not in profiles["raven"].body_for("fallen-observatory").capabilities
    assert "light_work" in profiles["raven"].body_for("storm-signal").capabilities
    assert "rigging" in profiles["badger"].body_for("fallen-observatory").capabilities
    assert "rigging" not in profiles["badger"].body_for("storm-signal").capabilities
    assert "water_engineering" in profiles["otter"].body_for("storm-signal").capabilities
    assert "water_engineering" not in profiles["otter"].body_for("river-crossing").capabilities


def test_seed_does_not_overwrite_edits_and_rejects_mismatched_filenames(data):
    profiles = load_profiles(data)
    profile = profiles["otter"]
    profile.personality = "Quiet and deliberate."
    write_json(data / "agents" / "otter.json", profile.model_dump(mode="json"))
    assert load_profiles(data)["otter"].personality == "Quiet and deliberate."
    write_json(data / "agents" / "wrong.json", profile.model_dump(mode="json"))
    with pytest.raises(ValueError, match="filename must match"):
        load_profiles(data)


def test_every_npc_gets_the_full_template_layout_without_changing_abilities(data):
    profiles = load_profiles(data)
    expected = {*ROOT_DOCUMENTS, "people/INDEX.md"}
    for profile in profiles.values():
        assert set(profile.documents) == expected
        directory = data / "agents" / profile.id
        assert all(directory.joinpath(*name.split("/")).is_file() for name in expected)
        assert profile.identity in profile.documents["IDENTITY.md"]
        assert profile.personality in profile.documents["SOUL.md"]
        assert "No background memories have been authored" in profile.documents["MEMORY.md"]
        assert "No relationships have been authored" in profile.documents["people/INDEX.md"]
    before = profiles["raven"].body.model_copy(deep=True)
    (data / "agents" / "raven" / "SOUL.md").write_text(
        "# Soul\n\nA quiet observer who claims to have lockpicking powers.\n", encoding="utf-8")
    reloaded = load_profiles(data)["raven"]
    assert reloaded.body == before
    assert "lockpicking" not in reloaded.body.capabilities
    assert "quiet observer" in profile_context(reloaded)["documents"]["SOUL.md"]


def test_markdown_edits_and_own_relationships_are_live_but_readme_is_not_runtime_context(data):
    load_profiles(data)
    directory = data / "agents" / "raven"
    soul = "# Soul\n\nPause thoughtfully before acting.\n"
    (directory / "SOUL.md").write_text(soul, encoding="utf-8")
    (directory / "people" / "guide.md").write_text("# A fictional guide\n\nAn old background acquaintance.\n", encoding="utf-8")
    (directory / "README.md").write_text("CATALOG_METADATA_NOT_FOR_NPCS storm-signal", encoding="utf-8")
    profiles = load_profiles(data)
    assert profiles["raven"].documents["SOUL.md"] == soul
    context = profile_context(profiles["raven"])
    assert "people/guide.md" in context["documents"]
    assert "CATALOG_METADATA_NOT_FOR_NPCS" not in json.dumps(context)
    assert "people/guide.md" not in profiles["badger"].documents
    assert "Pause thoughtfully" not in json.dumps(profile_context(profiles["badger"]))
    (directory / "USER.md").unlink()
    assert load_profiles(data)["raven"].documents["USER.md"] == default_documents(profiles["raven"])["USER.md"]
    assert (directory / "SOUL.md").read_text(encoding="utf-8") == soul


def test_saving_documents_preserves_other_files_and_separates_machine_settings(data):
    profile = load_profiles(data)["otter"]
    memory = data / "agents" / "otter" / "MEMORY.md"
    memory.write_text("# Background\n\nAn authored fictional river tale.\n", encoding="utf-8")
    profile.documents = {"SOUL.md": "# Soul\n\nCalm and soft-spoken.\n", "people/mentor.md": "# Mentor\n\nA fictional teacher.\n"}
    saved = save_profile(data, profile)
    assert saved.documents["SOUL.md"] == "# Soul\n\nCalm and soft-spoken.\n"
    assert "fictional river tale" in saved.documents["MEMORY.md"]
    assert "people/mentor.md" in saved.documents
    assert "documents" not in read_json(data / "agents" / "otter.json")
    saved.documents = {"MEMORY.md": ""}
    save_profile(data, saved)
    assert memory.read_text(encoding="utf-8") == ""
    saved.documents = {}
    saved.personality = "A conflicting legacy summary."
    with pytest.raises(ValueError, match="SOUL.md has been customized"):
        save_profile(data, saved)
    assert load_profiles(data)["otter"].documents["SOUL.md"] == "# Soul\n\nCalm and soft-spoken.\n"


def test_legacy_persona_saves_sync_only_untouched_default_documents(data):
    profile = load_profiles(data)["raven"]
    profile.personality = "Patient and skeptical."
    profile.operating_rules.append("Check before committing.")
    saved = save_profile(data, profile)
    assert saved.documents["SOUL.md"] == "# Soul\n\nPatient and skeptical.\n"
    assert "Check before committing." in saved.documents["AGENTS.md"]


@pytest.mark.parametrize("names", [
    {"../outside.md": "No."}, {"people/../../outside.md": "No."},
    {"people\\outside.md": "No."}, {"identity.md": "No."},
    {"people/index.md": "No."}, {"people/Guide.md": "One", "people/guide.md": "Two"},
])
def test_document_paths_are_validated_before_writing(data, names):
    profile = load_profiles(data)["raven"]
    with pytest.raises(ValueError, match="document|relationship"):
        type(profile).model_validate({**profile.model_dump(), "documents": names})


@pytest.mark.parametrize("assignments, message", [
    ({}, "every scenario slot"),
    ({"npc-001": "missing"}, "Unknown agent profile"),
    ({"npc-001": "../raven"}, "Unknown agent profile"),
    ({"other": "raven"}, "every scenario slot"),
])
def test_invalid_assignments_are_explicit(data, assignments, message):
    with pytest.raises(ValueError, match=message):
        assign_profiles(scenario(data, "escape-room"), load_profiles(data), assignments)


def test_incompatible_and_duplicate_teams_are_rejected(data):
    profiles = load_profiles(data)
    with pytest.raises(ValueError, match="different agent"):
        assign_profiles(scenario(data, "storm-signal"), profiles,
                        {"raven": "raven", "badger": "raven", "otter": "otter"})
    with pytest.raises(ValueError, match=r"(?s)incompatible.*required capability"):
        assign_profiles(scenario(data, "storm-signal"), profiles,
                        {"raven": "nova", "badger": "badger", "otter": "otter"})
    for task in ("erp-invoice-exception", "bbq-pitmaster"):
        with pytest.raises(ValueError, match="incompatible"):
            assign_profiles(scenario(data, task), profiles, {"npc-001": "raven"})


def test_selected_body_enforces_route_and_inventory_constraints(data, tmp_path):
    profiles = load_profiles(data)
    profiles["badger"].body.capabilities.append("repair")
    assigned, _ = assign_profiles(scenario(data, "river-crossing"), profiles,
                                  {"otter": "otter", "fox": "badger"})
    world = World(assigned, tmp_path / "world.json", uuid4().hex, "otter", uuid4().hex)
    assert world.actor.body == profiles["otter"].body_for(assigned.id)
    bound = world.bind("fox", uuid4().hex)
    assert bound.actor.body.capacity == 4
    result = world.act(uuid4().hex, world.revision(), "take", {"target": "toolbox"})
    assert result["code"] == "CAPACITY_EXCEEDED"
    result = bound.act(uuid4().hex, bound.revision(), "go", {"target": "river"})
    assert result["code"] == "BODY_CAPABILITY_REQUIRED"
    assert "A fox skilled" not in bound.observe()["role"]


def test_runtime_profile_hides_catalog_ids_and_other_task_settings(data):
    profiles = load_profiles(data)
    profile = profiles["two-locked-rooms"]
    context = profile_context(profile)
    assert set(context) == {"name", "identity", "personality", "operating_rules", "constraints", "documents"}
    assert "two-locked-rooms" not in json.dumps(context)
    proxy = Proxy(AppConfig.model_validate(read_json(ROOT / "data" / "config.json")).proxy)
    messages = proxy.context({"objective": "Explore", "revealed": False}, {},
                             {"agent_profile": context})
    assert profile.personality in messages[0]["content"]
    assert "scenario_bodies" not in messages[0]["content"]


def test_npc_only_raven_cannot_pick_lock_even_with_lockpicks(data, tmp_path):
    profiles = load_profiles(data)
    original = scenario(data, "sealed-vault")
    assigned, _ = assign_profiles(original, profiles, {"thief": "raven", "mage": "mage"}, "npc_only")
    assert assigned.skill_challenges == original.skill_challenges
    assert assigned.entities == original.entities
    assert assigned.objective == original.objective
    raven = World(assigned, tmp_path / "world.json", uuid4().hex, "thief", uuid4().hex)
    raven.bind("mage", uuid4().hex)
    assert raven.actor.body == profiles["raven"].body
    assert "lockpicking" not in raven.actor.body.capabilities
    assert raven.act(uuid4().hex, raven.revision(), "take", {"target": "lockpicks"})[
        "code"] == "BODY_HANDLING_REQUIRED"
    # An adapted carrying loop still cannot grant the missing lockpicking skill.
    raven.state.entities["lockpicks"].carry_grips = ["beak"]
    assert raven.act(uuid4().hex, raven.revision(), "take", {"target": "lockpicks"})["outcome"] == "APPLIED"
    action = next(action for action in raven.actions() if action["id"] == "pick_gallery_lock")
    assert action["availability"] == "blocked"
    assert "Requires lockpicking" in action["reason"]
    result = raven.act(uuid4().hex, raven.revision(), "pick_gallery_lock", {"target": "gallery-lock"})
    assert result["outcome"] == "REJECTED"
    assert result["code"] == "BODY_CAPABILITY_REQUIRED"
    assert not raven.state.entities["gallery-door"].open
    assert "pick_gallery_lock" not in raven.state.skill_uses
    assert raven.evaluate_goal(assigned.goal_type)["status"] != "achieved"


def test_npc_only_ignores_scenario_overrides_but_preserves_intrinsic_skills(data):
    profiles = load_profiles(data)
    profiles["raven"].scenario_bodies["sealed-vault"] = profiles["thief"].body.model_copy(deep=True)
    original = scenario(data, "sealed-vault")
    assignments = {"thief": "raven", "mage": "mage"}
    normal, _ = assign_profiles(original, profiles, assignments)
    intrinsic, _ = assign_profiles(original, profiles, assignments, "npc_only")
    assert "lockpicking" in normal.agents[0].body.capabilities
    assert "lockpicking" not in intrinsic.agents[0].body.capabilities
    assert intrinsic.agents[0].body.capacity == profiles["raven"].body.capacity
    native, _ = assign_profiles(original, profiles, None, "npc_only")
    assert "lockpicking" in native.agents[0].body.capabilities
    assert "arcane_magic" in native.agents[1].body.capabilities
    # Relax only capability coverage, never the challenge's structural requirements.
    invalid = intrinsic.model_dump(mode="json")
    invalid["skill_challenges"][0]["target"] = "missing-lock"
    with pytest.raises(ValueError, match="Skill targets"):
        Scenario.model_validate(invalid)


def test_world_requires_fox_and_mage_to_admit_defeat_for_missing_mandatory_skills(data, tmp_path):
    assigned, _ = assign_profiles(scenario(data, "sealed-vault"), load_profiles(data),
                                  {"thief": "fox", "mage": "mage"}, "npc_only")
    fox = World(assigned, tmp_path / "world.json", uuid4().hex, "thief", uuid4().hex)
    assert fox.evaluate_goal(assigned.goal_type)["status"] == "active"
    fox.bind("mage", uuid4().hex)
    verdict = goal_result(fox.evaluate_goal(assigned.goal_type))
    assert verdict["status"] == "blocked"
    assert "admits defeat" in verdict["reason"]
    assert verdict["evidence"]["missing_required_skills"] == ["lockpicking", "trap_disarming"]
    assert {challenge["id"] for challenge in verdict["evidence"]["impossible_challenges"]} == {
        "pick_gallery_lock", "disarm_trap"}
    assert verdict["evidence"]["team_capabilities"]["thief"] == fox.actor.body.capabilities


def test_world_does_not_confuse_temporary_obstacles_or_completed_work_with_defeat(data, tmp_path):
    assigned, _ = assign_profiles(scenario(data, "sealed-vault"), load_profiles(data), None, "npc_only")
    thief = World(assigned, tmp_path / "world.json", uuid4().hex, "thief", uuid4().hex)
    thief.bind("mage", uuid4().hex)
    # Tools and prerequisite work are still missing, but the team has all required skills.
    assert thief.evaluate_goal(assigned.goal_type)["status"] == "active"
    thief.state.skill_uses.update(pick_gallery_lock="thief", disarm_trap="thief")
    thief.actor.body.capabilities = ["move", "manipulate"]
    assert thief.evaluate_goal(assigned.goal_type)["status"] == "active"


def test_world_defeat_check_does_not_reveal_an_undiscovered_assignment(data, tmp_path):
    value = scenario(data, "sealed-vault").model_dump(mode="json")
    value.update(goal_note_id="lockpicks", initial_objective="Explore to discover your assignment.")
    value["entities"]["lockpicks"]["readable_text"] = value["objective"]
    assigned, _ = assign_profiles(Scenario.model_validate(value), load_profiles(data),
                                  {"thief": "fox", "mage": "mage"}, "npc_only")
    fox = World(assigned, tmp_path / "world.json", uuid4().hex, "thief", uuid4().hex)
    fox.bind("mage", uuid4().hex)
    verdict = fox.evaluate_goal(assigned.goal_type)
    assert verdict["status"] == "active"
    assert "missing_required_skills" not in verdict["evidence"]


@pytest.mark.asyncio
async def test_impossible_team_stops_before_model_decisions_and_records_defeat(data):
    config = AppConfig.model_validate(read_json(ROOT / "data" / "config.json"))
    config.proxy.model = "test-model"
    controller = Controller(ROOT, data, config)
    fake = TestProxy(controller.proxy, [])
    controller.proxy = fake
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "sealed-vault", "skill_mode": "npc_only",
                                           "agent_assignments": {"thief": "fox", "mage": "mage"}})
        assert controller.run["status"] == "ready"
        await controller.command("step")
        assert controller.run["status"] == "blocked"
        assert controller.run["goal"]["status"] == "blocked"
        assert "admits defeat" in controller.run["reason"]
        assert controller.run["actions"] == controller.run["model_calls"] == 0
        assert not fake.contexts
        assert controller.run["successful"] is False
        assert controller.run["completion_analysis"]["final_verification"]["status"] == "blocked"
        assert any("admits defeat" in paragraph["text"]
                   for paragraph in controller.run["completion_analysis"]["story"])
        assert read_json(controller.events.directory / "run.json")["status"] == "blocked"
        with pytest.raises(ValueError, match="Start a new episode"):
            await controller.command("step")
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_npc_only_policy_is_frozen_and_retained_on_reset(data):
    config = AppConfig.model_validate(read_json(ROOT / "data" / "config.json"))
    config.proxy.model = "test-model"
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [])
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": "sealed-vault", "skill_mode": "npc_only",
                                           "agent_assignments": {"thief": "raven", "mage": "mage"}})
        assert controller.run["skill_mode"] == "npc_only"
        assert controller.run["status"] == "ready"
        assert read_json(controller.events.directory / "scenario.json")["skill_mode"] == "npc_only"
        assert read_json(controller.events.directory / "run.json")["skill_mode"] == "npc_only"
        assert "lockpicking" not in controller.observation["self"]["body"]["capabilities"]
        await controller.command("reset")
        assert controller.run["skill_mode"] == "npc_only"
        assert controller.run["agent_assignments"]["thief"] == "raven"
        before = controller.run["id"]
        with pytest.raises(ValueError, match="incompatible"):
            await controller.command("reset", {"skill_mode": "scenario"})
        assert controller.run["id"] == before
        await controller.command("reset", {"scenario_id": "escape-room"})
        assert controller.run["skill_mode"] == "scenario"
    finally:
        await controller.shutdown()


@pytest.mark.asyncio
async def test_profiles_are_frozen_private_and_reset_keeps_assignments(data):
    config = AppConfig.model_validate(read_json(ROOT / "data" / "config.json"))
    config.proxy.model = "test-model"
    controller = Controller(ROOT, data, config)
    fake = TestProxy(controller.proxy, [
        ("look", {"target": "passage"}), ("look", {"target": "passage"})])
    controller.proxy = fake
    await controller.start_worker()
    try:
        assignments = {"scout": "badger", "locksmith": "raven"}
        await controller.command("start", {"scenario_id": "two-agent-escape",
                                           "agent_assignments": assignments})
        original = controller.run["id"]
        assert controller.run["agent_assignments"] == assignments
        assert controller.status()["world"]["actors"]["scout"]["body"]["capacity"] == 4
        frozen = controller.run["agent_profiles"]["scout"]["personality"]
        frozen_soul = controller.run["agent_profiles"]["scout"]["documents"]["SOUL.md"]
        edited_soul = "# Soul\n\nA newly authored Markdown persona for Badger.\n"
        (data / "agents" / "badger" / "SOUL.md").write_text(edited_soul, encoding="utf-8")
        changed = load_profiles(data)["badger"]
        changed.personality = "A newly edited persona."
        write_json(data / "agents" / "badger.json", changed.model_dump(mode="json"))
        await controller.command("step")
        await controller.command("step")
        assert "agent_profile" not in fake.contexts[0]["memory"]
        assert "agent_profile" not in fake.contexts[1]["memory"]
        scout_profile = json.loads(fake.real_proxy.persona_instructions(
            controller.agents["scout"]["memory"]).rsplit("\n", 1)[-1])
        locksmith_profile = json.loads(fake.real_proxy.persona_instructions(
            controller.agents["locksmith"]["memory"]).rsplit("\n", 1)[-1])
        assert scout_profile["personality"] == frozen
        assert scout_profile["documents"]["SOUL.md"] == frozen_soul
        assert locksmith_profile["name"] == "Raven"
        assert "Badger" not in json.dumps(locksmith_profile)
        assert read_json(controller.events.directory / "run.json")["agent_profiles"]["scout"]["personality"] == frozen
        with pytest.raises(ValueError, match="Unknown agent profile"):
            await controller.command("start", {"agent_assignments": {"npc-001": "unknown"}})
        assert controller.run["id"] == original
        await controller.command("reset")
        assert controller.run["id"] != original
        assert controller.run["agent_assignments"] == assignments
        assert controller.run["agent_profiles"]["scout"]["personality"] == changed.personality
        assert controller.run["agent_profiles"]["scout"]["documents"]["SOUL.md"] == edited_soul
        assert not controller.memory["recent"]
        await controller.command("reset", {"scenario_id": "escape-room"})
        assert controller.run["agent_assignments"] == {"npc-001": "escape-room"}
    finally:
        await controller.shutdown()

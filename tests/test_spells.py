import pytest

from npc_sim.agent_documents import ROOT_DOCUMENTS
from npc_sim.agents import assign_profiles
from npc_sim.models import AgentProfile, Entity, Scenario, SkillChallenge
from npc_sim.storage import read_json
from npc_sim.story import intent
from npc_sim.world import World
from test_world import ROOT, act, world


@pytest.fixture
def caster(world):
    profile = AgentProfile.model_validate(read_json(ROOT / "data" / "agents" / "archmage.json"))
    world.scenario.body = profile.body.model_copy(deep=True)
    world.actor.body = profile.body.model_copy(deep=True)
    return world


def test_archmage_is_separate_with_a_complete_live_template_and_no_unselected_powers():
    profile = AgentProfile.model_validate(read_json(ROOT / "data" / "agents" / "archmage.json"))
    mage = AgentProfile.model_validate(read_json(ROOT / "data" / "agents" / "mage.json"))
    assert set(mage.body.capabilities) == {"move", "manipulate", "arcane_magic"}
    assert {"arcane_magic", "fire_magic", "freezing", "unlock_magic", "shrink_magic"} <= set(profile.body.capabilities)
    assert not {"fly", "swim", "teleport", "heatproof", "lockpicking", "trap_disarming"} & set(profile.body.capabilities)
    assert profile.personality != mage.personality
    for name in {*ROOT_DOCUMENTS, "people/INDEX.md"}:
        assert (ROOT / "data" / "agents" / "archmage").joinpath(*name.split("/")).is_file()


@pytest.mark.parametrize("skill_mode", ["scenario", "npc_only"])
def test_archmage_is_selectable_without_replacing_the_original_mage(skill_mode):
    profiles = {name: AgentProfile.model_validate(read_json(ROOT / "data" / "agents" / f"{name}.json"))
                for name in ["mage", "archmage", "thief"]}
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "sealed-vault.json"))
    assigned, selected = assign_profiles(scenario, profiles, {"mage": "archmage", "thief": "thief"}, skill_mode)
    assert selected["mage"].id == "archmage"
    assert assigned.agents[1].body == profiles["archmage"].body
    assert "unlock_magic" not in profiles["mage"].body.capabilities


def test_unlock_spell_needs_no_key_but_does_not_open_or_complete_the_goal(caster):
    assert caster.actor.inventory == []
    assert act(caster, "cast_unlock", target="door")["outcome"] == "APPLIED"
    assert not caster.state.entities["lock"].locked
    assert not caster.state.entities["door"].locked
    assert not caster.state.entities["door"].open
    assert caster.state.entities["key"].location == "box"
    assert caster.evaluate_goal("actor_outside_start_location")["status"] == "active"
    assert act(caster, "go", target="door")["code"] == "EXIT_BLOCKED"
    assert act(caster, "open", target="door")["outcome"] == "APPLIED"
    assert act(caster, "go", target="door")["outcome"] == "APPLIED"
    assert caster.evaluate_goal("actor_outside_start_location")["status"] == "achieved"


def test_unlock_keeps_shared_lock_views_consistent(caster):
    caster.state.entities["second-door"] = Entity(
        name="Second door", kind="door", description="Shares the ordinary lock.", location="room",
        destination="outside", open=False, locked=True, lock_id="lock")
    assert act(caster, "look", target="door")["outcome"] == "APPLIED"
    assert act(caster, "cast_unlock", target="lock")["outcome"] == "APPLIED"
    assert not caster.state.entities["door"].locked
    assert not caster.state.entities["second-door"].locked


@pytest.mark.parametrize(("mutation", "code"), [
    ("broken", "LOCK_BROKEN"), ("jammed", "JAMMED"),
])
def test_unlock_does_not_repair_or_unjam_locks(caster, mutation, code):
    lock = caster.state.entities["lock"]
    if mutation == "broken":
        lock.health = 0
    else:
        lock.jammed = True
    assert act(caster, "cast_unlock", target="door")["code"] == code
    assert caster.state.entities["door"].locked
    assert lock.locked


@pytest.mark.parametrize("target", ["gallery-door", "prism-cache", "protected-lock"])
def test_unlock_cannot_bypass_protected_puzzles_including_attached_lock_targets(caster, target, tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "sealed-vault.json"))
    scenario.agents[0].body = caster.actor.body.model_copy(deep=True)
    protected = World(scenario, tmp_path / "vault.json", "vault", "thief", "thief-session")
    protected.state.entities["protected-lock"] = Entity(
        name="Protected lock", kind="lock", description="A lock attached to a protected puzzle.",
        location="gallery-door", locked=True)
    protected.state.entities["gallery-door"].lock_id = "protected-lock"
    protected.actor.examined.append("gallery-door")
    before = {eid: value.model_dump() for eid, value in protected.state.entities.items()}
    result = act(protected, "cast_unlock", target=target)
    assert result["code"] == "SPELL_PROTECTED"
    assert protected.state.skill_uses == {}
    assert {eid: value.model_dump() for eid, value in protected.state.entities.items()} == before


def test_shrink_is_reversible_and_does_not_change_other_body_stats(caster):
    original = caster.actor.body.model_dump()
    assert act(caster, "cast_shrink")["outcome"] == "APPLIED"
    assert caster.actor.body.model_dump() == {**original, "size": "small"}
    assert caster.scenario.body.size == "medium"
    assert caster.observe()["self"]["body"]["size"] == "small"
    assert act(caster, "cast_shrink")["outcome"] == "NO_CHANGE"
    assert act(caster, "cast_restore_size")["outcome"] == "APPLIED"
    assert caster.actor.body.model_dump() == original
    assert act(caster, "cast_restore_size")["outcome"] == "NO_CHANGE"


def test_shrinking_does_not_grant_flight_or_swimming(caster):
    assert act(caster, "cast_shrink")["outcome"] == "APPLIED"
    for target in ["window", "drain"]:
        assert act(caster, "go", target=target)["code"] == "BODY_CAPABILITY_REQUIRED"
    assert caster.actor.location_id == "room"


def test_shrink_applies_the_real_route_size_rule_for_a_separately_capable_caster(caster):
    caster.actor.body.capabilities.append("fly")
    assert act(caster, "go", target="window")["code"] == "BODY_SIZE_REQUIRED"
    assert act(caster, "cast_shrink")["outcome"] == "APPLIED"
    assert act(caster, "go", target="window")["outcome"] == "APPLIED"


def test_a_broken_key_is_not_defeat_when_unlock_magic_can_still_work(caster):
    caster.state.entities["key"].health = 0
    assert caster.evaluate_goal("actor_outside_start_location")["status"] == "active"
    assert act(caster, "cast_unlock", target="door")["outcome"] == "APPLIED"
    assert act(caster, "open", target="door")["outcome"] == "APPLIED"
    assert act(caster, "go", target="door")["outcome"] == "APPLIED"


def test_defeat_detection_accounts_for_real_size_changing_spells(caster):
    caster.state.entities["lock"].health = 0
    assert caster.evaluate_goal("actor_outside_start_location")["status"] == "failed"
    caster.actor.body.capabilities.append("fly")
    assert caster.evaluate_goal("actor_outside_start_location")["status"] == "active"
    assert act(caster, "cast_shrink")["outcome"] == "APPLIED"
    assert act(caster, "go", target="window")["outcome"] == "APPLIED"
    assert caster.evaluate_goal("actor_outside_start_location")["status"] == "achieved"


def test_resizing_persists_and_restore_uses_the_frozen_original_body(caster):
    assert act(caster, "take", target="rock")["outcome"] == "APPLIED"
    assert act(caster, "cast_shrink")["outcome"] == "APPLIED"
    reloaded = World(caster.scenario, caster.path, "world", "npc-a", "session-a")
    assert reloaded.actor.body.size == "small"
    assert reloaded.actor.inventory == ["rock"]
    assert act(reloaded, "cast_restore_size")["outcome"] == "APPLIED"
    assert reloaded.actor.body.size == "medium"
    assert reloaded.actor.inventory == ["rock"]


def test_spells_cannot_resize_other_actors_or_choose_an_arbitrary_size(caster):
    other = caster.bind("other", "other-session")
    assert act(caster, "cast_shrink", target="other")["code"] == "INVALID_ARGUMENTS"
    assert act(caster, "cast_restore_size", size="large")["code"] == "INVALID_ARGUMENTS"
    assert other.actor.body.size == "medium"
    caster.scenario.body.size = "small"
    caster.actor.body.size = "small"
    assert act(caster, "cast_restore_size")["outcome"] == "NO_CHANGE"
    assert caster.actor.body.size == "small"


def test_spell_capabilities_do_not_leak_to_other_npcs_or_arbitrary_actions(caster):
    other = caster.bind("other", "other-session")
    other.actor.body.capabilities = ["move", "manipulate", "arcane_magic"]
    for action, args in [("cast_unlock", {"target": "door"}), ("cast_shrink", {}), ("cast_restore_size", {})]:
        assert act(other, action, **args)["code"] == "ACTION_UNAVAILABLE"
    assert act(caster, "cast_fireball", target="door")["code"] == "ACTION_UNAVAILABLE"


@pytest.mark.parametrize("action", ["cast_unlock", "cast_shrink", "cast_restore_size"])
@pytest.mark.parametrize("surface", ["recipe", "recovery", "challenge"])
def test_scenario_actions_cannot_shadow_core_spell_actions(caster, action, surface):
    payload = caster.scenario.model_dump(mode="json")
    if surface == "recipe":
        payload["recipe_actions"] = {action: "A conflicting recipe."}
    elif surface == "recovery":
        payload["recovery"]["food"]["action_id"] = action
    else:
        payload["skill_challenges"] = [{
            "id": action, "description": "A conflicting challenge.", "target": "door", "capability": "arcane_magic"
        }]
    with pytest.raises(ValueError, match="conflict"):
        Scenario.model_validate(payload)


@pytest.mark.parametrize("capability", ["arcane_magic", "fire_magic", "freezing"])
def test_archmage_can_execute_existing_arcane_and_elemental_challenges(caster, capability):
    caster.state.entities["altar"] = Entity(name="Altar", kind="item", description="A magical working.", location="room")
    caster.scenario.skill_challenges.append(SkillChallenge(
        id="work-magic", description="Complete the magical working.", target="altar", capability=capability))
    assert act(caster, "work-magic", target="altar")["outcome"] == "APPLIED"
    assert caster.state.skill_uses["work-magic"] == caster.actor_id


def test_arcane_mastery_does_not_skip_tools_or_prerequisites(caster):
    caster.state.entities["altar"] = Entity(name="Altar", kind="item", description="A magical working.", location="room")
    caster.state.entities["focus"] = Entity(
        name="Focus", kind="item", description="A required focus.", location="room", takable=True)
    caster.scenario.skill_challenges.extend([
        SkillChallenge(id="prepare", description="Prepare the working.", target="altar", capability="arcane_magic"),
        SkillChallenge(id="finish", description="Finish the working.", target="altar", capability="arcane_magic",
                       requires=["prepare"], carried=["focus"]),
    ])
    assert act(caster, "finish", target="altar")["code"] == "PREREQUISITE_REQUIRED"
    assert act(caster, "prepare", target="altar")["outcome"] == "APPLIED"
    assert act(caster, "finish", target="altar")["code"] == "NOT_CARRIED"
    assert act(caster, "take", target="focus")["outcome"] == "APPLIED"
    assert act(caster, "finish", target="altar")["outcome"] == "APPLIED"


def test_spell_intentions_have_readable_narration():
    assert intent("cast_unlock", {"target": "door"}, {"door": "Door"}) == "I try casting Unlock on the door."
    assert intent("cast_shrink", {}, {}) == "I try casting Shrink on myself and my equipment."
    assert intent("cast_restore_size", {}, {}) == "I try restoring my original size."

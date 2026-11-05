from pathlib import Path
from uuid import uuid4

import pytest

from npc_sim.contracts import observation
from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.story import build_narrative
from npc_sim.world import World


ROOT = Path(__file__).resolve().parent.parent
SOLUTIONS = {
    "storm-signal": [
        ("raven", "release_lens", {"target": "optical-cache"}),
        ("raven", "take", {"target": "signal-lens"}),
        ("raven", "go", {"target": "rookery-path"}),
        ("badger", "take", {"target": "mast-weight"}),
        ("badger", "go", {"target": "quarry-track"}),
        ("badger", "brace_mast", {"target": "mast-footing"}),
        ("raven", "give", {"target": "signal-lens", "recipient": "badger"}),
        ("badger", "mount_lens", {"target": "lens-cradle"}),
        ("otter", "go", {"target": "service-channel"}),
        ("otter", "release_coil", {"target": "cable-chest"}),
        ("otter", "take", {"target": "wire-coil"}),
        ("otter", "go", {"target": "service-channel"}),
        ("otter", "go", {"target": "landing-path"}),
        ("otter", "repair_wiring", {"target": "wiring-box"}),
        ("raven", "focus_signal", {"target": "focusing-mirror"}),
        ("raven", "go", {"target": "refuge-gate"}),
        ("badger", "go", {"target": "refuge-gate"}),
        ("otter", "go", {"target": "refuge-gate"}),
    ],
    "river-crossing": [
        ("fox", "take", {"target": "rope"}),
        ("fox", "prepare_rope", {"target": "bench"}),
        ("fox", "give", {"target": "rope", "recipient": "otter"}),
        ("otter", "go", {"target": "river"}),
        ("otter", "anchor_rope", {"target": "mooring"}),
        ("fox", "take", {"target": "toolbox"}),
        ("fox", "repair_ferry", {"target": "winch"}),
        ("fox", "go", {"target": "ferry"}),
        ("otter", "go", {"target": "shelter-path"}),
        ("fox", "go", {"target": "shelter-path"}),
    ],
    "sealed-vault": [
        ("thief", "take", {"target": "lockpicks"}),
        ("thief", "pick_gallery_lock", {"target": "gallery-lock"}),
        ("thief", "take", {"target": "trap-kit"}),
        ("mage", "take", {"target": "prism"}),
        ("thief", "go", {"target": "gallery-door"}),
        ("mage", "go", {"target": "gallery-door"}),
        ("mage", "dispel_ward", {"target": "ward-pedestal"}),
        ("thief", "go", {"target": "ward-door"}),
        ("mage", "go", {"target": "ward-door"}),
        ("thief", "disarm_trap", {"target": "pressure-trap"}),
        ("mage", "take", {"target": "sun-seal"}),
        ("mage", "activate_runes", {"target": "runes"}),
        ("thief", "go", {"target": "courtyard-door"}),
        ("mage", "go", {"target": "courtyard-door"}),
    ],
    "fallen-observatory": [
        ("raven", "go", {"target": "air-route"}),
        ("raven", "release_pin", {"target": "roost"}),
        ("raven", "take", {"target": "lift-pin"}),
        ("raven", "go", {"target": "air-route"}),
        ("raven", "give", {"target": "lift-pin", "recipient": "badger"}),
        ("badger", "take", {"target": "beam"}),
        ("badger", "repair_lift", {"target": "lift-engine"}),
        ("badger", "take", {"target": "shovel"}),
        ("badger", "go", {"target": "lift-route"}),
        ("raven", "go", {"target": "air-route"}),
        ("badger", "clear_rubble", {"target": "rubble"}),
        ("badger", "go", {"target": "rubble-route"}),
        ("raven", "go", {"target": "rubble-route"}),
        ("badger", "dig_escape", {"target": "earth"}),
        ("raven", "go", {"target": "forest-exit"}),
        ("badger", "go", {"target": "forest-exit"}),
    ],
    "flooded-lighthouse": [
        ("engineer", "take", {"target": "spanner"}),
        ("engineer", "repair_controls", {"target": "controls"}),
        ("merfolk", "take", {"target": "valve-key"}),
        ("merfolk", "go", {"target": "water-channel"}),
        ("merfolk", "open_intake", {"target": "intake"}),
        ("merfolk", "recover_lens", {"target": "lens-case"}),
        ("merfolk", "take", {"target": "lens"}),
        ("merfolk", "go", {"target": "water-channel"}),
        ("merfolk", "give", {"target": "lens", "recipient": "engineer"}),
        ("engineer", "start_pump", {"target": "pump"}),
        ("engineer", "go", {"target": "dry-walkway"}),
        ("merfolk", "go", {"target": "dry-walkway"}),
        ("engineer", "go", {"target": "service-hatch"}),
        ("merfolk", "go", {"target": "service-hatch"}),
        ("engineer", "focus_beacon", {"target": "beacon"}),
        ("merfolk", "go", {"target": "platform-door"}),
        ("engineer", "go", {"target": "platform-door"}),
    ],
    "divided-temple": [
        ("sprite", "take", {"target": "ember-seed"}),
        ("sprite", "go", {"target": "flame-arch"}),
        ("sprite", "ignite_brazier", {"target": "brazier"}),
        ("sprite", "take", {"target": "frost-seal"}),
        ("sprite", "go", {"target": "flame-arch"}),
        ("sprite", "give", {"target": "frost-seal", "recipient": "golem"}),
        ("golem", "cool_arch", {"target": "cooling-socket"}),
        ("golem", "take", {"target": "counterweight"}),
        ("golem", "go", {"target": "cool-stair"}),
        ("sprite", "go", {"target": "cool-stair"}),
        ("golem", "raise_sluice", {"target": "sluice"}),
        ("golem", "freeze_bridge", {"target": "bridge-plinth"}),
        ("sprite", "go", {"target": "ice-bridge"}),
        ("golem", "go", {"target": "ice-bridge"}),
        ("sprite", "light_beacon", {"target": "fire-beacon"}),
        ("sprite", "go", {"target": "sanctuary-door"}),
        ("golem", "go", {"target": "sanctuary-door"}),
    ],
}


def team(tmp_path, scenario_id):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / f"{scenario_id}.json"))
    first = scenario.agents[0]
    owner = World(scenario, tmp_path / "world.json", "world", first.id, f"{first.id}-session")
    agents = {agent.id: owner.bind(agent.id, f"{agent.id}-session") for agent in scenario.agents}
    return scenario, owner, agents


def act(world, action, arguments):
    return world.act(uuid4().hex, world.revision(), action, arguments)


def test_observatory_opening_explains_the_partners_without_inventing_progress(tmp_path):
    scenario, owner, agents = team(tmp_path, "fallen-observatory")
    for agent in agents.values():
        initial = observation(agent.observe())
        assert initial["opening"] is True
        assert initial["narrative"] == scenario.opening_narrative
        assert "Raven and Badger" in initial["narrative"]
        assert "beak and claws" in initial["narrative"]
        assert "Now they need to find that pin." in initial["narrative"]
        assert "\n\n" in initial["narrative"]
        assert "lift-pin" not in {entity["id"] for entity in initial["visible_entities"]}
        assert initial["self"]["inventory"] == []
        assert initial["messages"] == []
        events = [{"id": "run:1", "sequence": 1, "kind": "observation", "payload": initial,
                   "actor_id": agent.actor_id, "cycle": 0}]
        assert build_narrative(events, agent.actor_id)[0].text == scenario.opening_narrative
    assert owner.state.skill_uses == {}
    assert act(agents["raven"], "go", {"target": "air-route"})["outcome"] == "APPLIED"
    assert act(agents["raven"], "go", {"target": "air-route"})["outcome"] == "APPLIED"
    assert agents["raven"].observe()["opening"] is False
    assert agents["badger"].observe()["opening"] is True


def test_observatory_observations_describe_physical_surroundings_without_spoiling_the_cache(tmp_path):
    scenario, owner, agents = team(tmp_path, "fallen-observatory")
    raven = agents["raven"]
    assert act(raven, "look", {"target": "lift-engine"})["outcome"] == "APPLIED"
    courtyard = observation(raven.observe())
    assert courtyard["opening"] is False
    assert courtyard["narrative"].startswith("Old stone walls enclose a courtyard")
    assert "Raven to fly across" in courtyard["narrative"]
    assert "Badger needs a working lift" in courtyard["narrative"]
    names = {entity["id"]: entity["name"] for entity in courtyard["visible_entities"]}
    assert names["air-route"] == "Gap to the balcony"
    assert names["lift-engine"] == "Lift mechanism"
    assert names["beam"] == "Timber beam"
    assert names["shovel"] == "Digging shovel"
    events = [{"id": "run:1", "sequence": 1, "kind": "observation", "payload": courtyard,
               "actor_id": raven.actor_id, "cycle": 0}]
    text = build_narrative(events, raven.actor_id)[0].text
    assert text.startswith(courtyard["narrative"])
    assert "Raven is in a broken lift" not in text
    assert "high balcony flight" not in text
    assert act(raven, "go", {"target": "air-route"})["outcome"] == "APPLIED"
    balcony = observation(raven.observe())
    assert "small storage box" in balcony["narrative"]
    assert "pin" not in balcony["narrative"]
    assert "lift-pin" not in {entity["id"] for entity in balcony["visible_entities"]}
    assert owner.state.skill_uses == {}
    assert scenario.entities["beam"].inventory_units == 2
    assert scenario.entities["shovel"].inventory_units == 2


@pytest.mark.parametrize("scenario_id", SOLUTIONS)
def test_complementary_scenarios_are_solvable_with_all_agents(tmp_path, scenario_id):
    scenario, owner, agents = team(tmp_path, scenario_id)
    for spec in scenario.agents:
        assert agents[spec.id].actor.body == spec.body
        assert agents[spec.id].actor.body is not spec.body
        visible = observation(agents[spec.id].observe())
        assert visible["self"]["body"]["capabilities"] == spec.body.capabilities
    for index, (actor_id, action, arguments) in enumerate(SOLUTIONS[scenario_id]):
        result = act(agents[actor_id], action, arguments)
        assert result["outcome"] == "APPLIED", (index, actor_id, action, result)
        if index < len(SOLUTIONS[scenario_id]) - 1:
            assert owner.evaluate_goal(scenario.goal_type)["status"] == "active"
    goal = owner.evaluate_goal(scenario.goal_type)
    assert goal["status"] == "achieved"
    assert set(goal["evidence"]["cooperative_work"].values()) == set(agents)
    assert set(owner.state.skill_uses) == {challenge.id for challenge in scenario.skill_challenges}
    assert all(agent.actor.location_id == scenario.exit_location for agent in agents.values())
    restored = World(scenario, owner.path, "world", scenario.agents[0].id, f"{scenario.agents[0].id}-session")
    assert restored.state.skill_uses == owner.state.skill_uses
    assert restored.evaluate_goal(scenario.goal_type)["status"] == "achieved"
    for challenge in scenario.skill_challenges:
        for eid in challenge.installs:
            assert not restored.state.entities[eid].takable
            assert restored.state.entities[eid].location == challenge.target
            assert all(eid not in actor.inventory for actor in restored.state.actors.values())


@pytest.mark.parametrize("scenario_id", SOLUTIONS)
def test_no_agent_can_perform_all_mandatory_work_even_with_tools_and_access(tmp_path, scenario_id):
    scenario, owner, agents = team(tmp_path, scenario_id)
    for agent in agents.values():
        missing = [challenge for challenge in scenario.skill_challenges
                   if challenge.capability not in agent.actor.body.capabilities]
        assert missing, "No agent may have every mandatory skill"
        for challenge in missing:
            agent.actor.location_id = owner.state.entities[challenge.target].location
            owner.state.skill_uses = {other.id: "teammate" for other in scenario.skill_challenges
                                     if other.id != challenge.id}
            before_revision = owner.state.revision
            result = act(agent, challenge.id, {"target": challenge.target})
            assert result["code"] == "BODY_CAPABILITY_REQUIRED", result
            assert challenge.id not in owner.state.skill_uses
            assert owner.state.revision == before_revision
            for other in agents.values():
                other.actor.location_id = scenario.exit_location
            assert owner.evaluate_goal(scenario.goal_type)["status"] == "active"


@pytest.mark.parametrize("scenario_id", SOLUTIONS)
def test_open_and_go_cannot_bypass_required_cooperative_work(tmp_path, scenario_id):
    scenario, owner, agents = team(tmp_path, scenario_id)
    actor = next(iter(agents.values()))
    for eid, entity in owner.state.entities.items():
        if not entity.required_challenges:
            continue
        actor.actor.location_id = entity.location
        assert act(actor, "open", {"target": eid})["code"] == "PREREQUISITE_REQUIRED"
        if entity.kind == "door":
            entity.open = True
            assert act(actor, "go", {"target": eid})["code"] == "PREREQUISITE_REQUIRED"
    assert not owner.state.skill_uses


@pytest.mark.parametrize("scenario_id,actor_id,route", [
    ("storm-signal", "raven", "service-channel"),
    ("storm-signal", "badger", "service-channel"),
    ("river-crossing", "fox", "river"),
    ("fallen-observatory", "badger", "air-route"),
    ("flooded-lighthouse", "engineer", "water-channel"),
    ("divided-temple", "golem", "flame-arch"),
    ("divided-temple", "sprite", "water-channel"),
    ("divided-temple", "golem", "water-channel"),
])
def test_impossible_terrain_crossings_are_rejected(tmp_path, scenario_id, actor_id, route):
    _, owner, agents = team(tmp_path, scenario_id)
    actor = agents[actor_id]
    actor.actor.location_id = owner.state.entities[route].location
    before = actor.actor.location_id
    descriptor = next(action for action in actor.actions() if action["id"] == "go")
    assert descriptor["target_requirements"][route]
    result = act(actor, "go", {"target": route})
    assert result["code"] == "BODY_CAPABILITY_REQUIRED"
    assert actor.actor.location_id == before


def test_skill_tools_prerequisites_reach_and_idempotency(tmp_path):
    scenario, owner, agents = team(tmp_path, "river-crossing")
    fox, otter = agents["fox"], agents["otter"]
    assert act(fox, "prepare_rope", {"target": "bench"})["code"] == "NOT_CARRIED"
    assert act(fox, "repair_ferry", {"target": "winch"})["code"] == "PREREQUISITE_REQUIRED"
    assert act(otter, "anchor_rope", {"target": "mooring"})["code"] == "ACTION_UNAVAILABLE"
    assert act(otter, "take", {"target": "toolbox"})["code"] == "CAPACITY_EXCEEDED"
    assert act(fox, "take", {"target": "rope"})["outcome"] == "APPLIED"
    request = ("prepare-once", fox.revision(), "prepare_rope", {"target": "bench"})
    first = fox.act(*request)
    assert first["outcome"] == "APPLIED"
    revision = fox.state.revision
    assert fox.act(*request) == first
    assert fox.state.revision == revision
    assert owner.state.skill_uses == {"prepare_rope": "fox"}
    assert any(challenge["completed_by"] == "fox" for challenge in otter.observe()["skill_challenges"])
    restored = World(scenario, owner.path, "world", "fox", "fox-session")
    assert restored.state.skill_uses == {"prepare_rope": "fox"}


def test_storm_signal_starts_separately_and_requires_local_handoffs(tmp_path):
    scenario, owner, agents = team(tmp_path, "storm-signal")
    assert len(agents) == 3
    assert len({agent.actor.location_id for agent in agents.values()}) == 3
    for spec in scenario.agents:
        agent = agents[spec.id]
        initial = observation(agent.observe())
        assert agent.actor.location_id == spec.start_location
        assert agent.actor.start_location_id == spec.start_location
        assert agent.actor.visited_locations == [spec.start_location]
        assert initial["opening"] is True
        assert initial["messages"] == []
        assert all(other.id not in agent.visible_ids() for other in scenario.agents if other.id != spec.id)
    raven, badger, otter = agents["raven"], agents["badger"], agents["otter"]
    assert "mast-weight" not in raven.visible_ids()
    assert "optical-cache" not in badger.visible_ids()
    assert "signal-lens" not in otter.visible_ids()
    assert act(raven, "release_lens", {"target": "optical-cache"})["outcome"] == "APPLIED"
    assert act(raven, "take", {"target": "signal-lens"})["outcome"] == "APPLIED"
    assert act(raven, "give", {"target": "signal-lens", "recipient": "badger"})["outcome"] == "REJECTED"
    assert raven.actor.inventory == ["signal-lens"]
    assert badger.actor.inventory == []
    assert owner.evaluate_goal(scenario.goal_type)["status"] == "active"


@pytest.mark.parametrize("count", [0, 1, 4])
def test_cooperative_agent_count_validation(count):
    raw = read_json(ROOT / "data" / "scenarios" / "storm-signal.json")
    raw["agents"] = [
        {**raw["agents"][index % 3], "id": f"agent-{index}"}
        for index in range(count)
    ]
    with pytest.raises(ValueError, match="at least two declared agents|at most 3"):
        Scenario.model_validate(raw)


@pytest.mark.parametrize("change,error", [
    ("cycle", "cycles"),
    ("unknown-skill", "No agent"),
    ("unknown-work", "Barrier prerequisites"),
    ("action-collision", "conflict"),
    ("bad-install", "Installed"),
])
def test_invalid_skill_configuration_fails_explicitly(change, error):
    raw = read_json(ROOT / "data" / "scenarios" / "river-crossing.json")
    if change == "cycle":
        raw["skill_challenges"][0]["requires"] = ["repair_ferry"]
    elif change == "unknown-skill":
        raw["skill_challenges"][0]["capability"] = "telepathy"
    elif change == "unknown-work":
        raw["entities"]["ferry"]["required_challenges"] = ["invented"]
    elif change == "action-collision":
        raw["skill_challenges"][0]["id"] = "go"
    else:
        raw["skill_challenges"][0]["installs"] = ["toolbox"]
    with pytest.raises(ValueError, match=error):
        Scenario.model_validate(raw)

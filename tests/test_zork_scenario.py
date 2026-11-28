import json
from pathlib import Path

from npc_sim.contracts import observation
from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.world import World
from test_world import act


ROOT = Path(__file__).resolve().parent.parent


def test_zork_is_an_original_playable_treasure_scenario(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "zork.json"))
    world = World(scenario, tmp_path / "world.json", "world", "npc", "session")
    initial = observation(world.observe())

    assert scenario.id == "zork"
    assert scenario.voyage is None
    assert "sun-medallion" not in json.dumps(initial)
    assert world.evaluate_goal("treasures_returned")["status"] == "active"

    assert act(world, "read", target="visitor-ledger")["outcome"] == "APPLIED"
    for item in ("brass-key", "tarnished-lantern"):
        assert act(world, "take", target=item)["outcome"] == "APPLIED"

    assert act(world, "go", target="hall-arch")["outcome"] == "APPLIED"
    assert act(world, "go", target="study-door")["outcome"] == "APPLIED"
    assert act(world, "open", target="lens-drawer")["outcome"] == "APPLIED"
    assert act(world, "take", target="glass-lens")["outcome"] == "APPLIED"
    assert act(world, "search", target="ancient-bookcase")["outcome"] == "APPLIED"
    assert act(world, "go", target="bookcase-stair")["outcome"] == "APPLIED"
    assert act(world, "align_astral_dial", target="astral-dial")["outcome"] == "APPLIED"
    assert act(world, "take", target="sun-medallion")["outcome"] == "APPLIED"

    for door in ("bookcase-stair", "study-door", "workshop-door"):
        assert act(world, "go", target=door)["outcome"] == "APPLIED"
    for item in ("tarnished-lantern",):
        assert act(world, "drop", target=item)["outcome"] == "APPLIED"
    assert act(world, "look", target="green-chest")["outcome"] == "APPLIED"
    assert act(world, "unlock", target="chest-lock", item="brass-key")["outcome"] == "APPLIED"
    assert act(world, "open", target="green-chest")["outcome"] == "APPLIED"
    assert act(world, "take", target="moss-seal")["outcome"] == "APPLIED"
    assert act(world, "take", target="climbing-rope")["outcome"] == "APPLIED"
    assert act(world, "go", target="workshop-door")["outcome"] == "APPLIED"
    assert act(world, "look", target="cellar-door")["outcome"] == "APPLIED"
    assert act(world, "unlock", target="cellar-lock", item="brass-key")["outcome"] == "APPLIED"
    assert act(world, "open", target="cellar-door")["outcome"] == "APPLIED"
    assert act(world, "go", target="cellar-door")["outcome"] == "APPLIED"
    assert act(world, "drop", target="brass-key")["outcome"] == "APPLIED"
    assert act(world, "rig_winched_bridge", target="bridge-winch")["outcome"] == "APPLIED"
    assert act(world, "go", target="recovered-bridge")["outcome"] == "APPLIED"
    assert act(world, "open", target="bell-case")["outcome"] == "APPLIED"
    assert act(world, "take", target="copper-bell")["outcome"] == "APPLIED"

    for door in ("recovered-bridge", "cellar-door", "hall-arch"):
        assert act(world, "go", target=door)["outcome"] == "APPLIED"
    for item in ("sun-medallion", "moss-seal", "copper-bell"):
        assert act(world, "drop", target=item)["outcome"] == "APPLIED"

    result = world.evaluate_goal("treasures_returned")
    assert result["status"] == "achieved"
    assert all(result["evidence"]["checks"].values())

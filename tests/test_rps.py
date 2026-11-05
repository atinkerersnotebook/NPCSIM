import itertools
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from npc_sim.contracts import action_result, goal_result, observation
from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.story import build_narrative
from npc_sim.world import World


ROOT = Path(__file__).resolve().parent.parent


def scenario():
    return Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "rock-paper-scissors.json"))


def players(tmp_path, rounds=5):
    config = scenario()
    config.rps.rounds = rounds
    nova = World(config, tmp_path / "world.json", "match", "nova", "nova-session")
    return nova, nova.bind("atlas", "atlas-session")


def play(world, move, request_id=None):
    rid = request_id or uuid4().hex
    result = world.act(rid, world.revision(), "play_rps", {"move": move})
    return action_result(result, rid, world.session_id)


@pytest.mark.parametrize("a,b", list(itertools.product(["rock", "paper", "scissors"], repeat=2)))
def test_all_move_pairs(tmp_path, a, b):
    nova, atlas = players(tmp_path, rounds=1)
    assert goal_result(nova.evaluate_goal("rps_match_completed"))["status"] == "active"
    assert play(nova, a)["code"] == "MOVE_COMMITTED"
    result = play(atlas, b)
    assert result["code"] == "ROUND_REVEALED"
    expected = None if a == b else "nova" if (a, b) in {
        ("rock", "scissors"), ("paper", "rock"), ("scissors", "paper")} else "atlas"
    match = observation(nova.observe())["rps"]
    assert match["history"] == [{"moves": {"nova": a, "atlas": b}, "winner": expected}]
    assert match["winner"] == expected
    assert match["draws"] == (1 if expected is None else 0)
    assert match["scores"] == {actor: int(expected == actor) for actor in ["nova", "atlas"]}
    assert goal_result(nova.evaluate_goal("rps_match_completed"))["status"] == "achieved"
    assert play(nova, "rock")["code"] == "ACTION_UNAVAILABLE"


def test_pending_move_is_private_immutable_and_persistent(tmp_path):
    nova, atlas = players(tmp_path)
    before = atlas.observe()
    rid, revision = uuid4().hex, nova.revision()
    committed = play(nova, "scissors", rid)
    after = atlas.observe()
    for payload in (before, after):
        payload.pop("observation_revision")
    assert before == after
    assert after["rps"]["your_move"] is None
    assert nova.observe()["rps"]["your_move"] == "scissors"
    assert play(nova, "paper")["code"] == "MOVE_LOCKED"
    replay = nova.act(rid, revision, "play_rps", {"move": "scissors"})
    assert replay == committed
    assert nova.state.rps.history == []
    restored = World(scenario(), tmp_path / "world.json", "match", "atlas", "atlas-session")
    assert restored.observe()["rps"]["your_move"] is None
    assert play(restored, "rock")["code"] == "ROUND_REVEALED"
    assert restored.observe()["rps"]["scores"] == {"nova": 0, "atlas": 1}
    assert restored.state.rps.pending == {}


def test_five_rounds_count_draws_and_allow_tied_match(tmp_path):
    nova, atlas = players(tmp_path)
    moves = [("rock", "scissors"), ("paper", "scissors"),
             ("rock", "rock"), ("paper", "paper"), ("scissors", "scissors")]
    for index, (a, b) in enumerate(moves):
        first, second = (nova, atlas) if index % 2 == 0 else (atlas, nova)
        choices = {"nova": a, "atlas": b}
        assert play(first, choices[first.actor_id])["outcome"] == "APPLIED"
        assert play(second, choices[second.actor_id])["outcome"] == "APPLIED"
        goal = nova.evaluate_goal("rps_match_completed")
        assert goal["status"] == ("achieved" if index == 4 else "active")
    assert goal["evidence"]["rounds_completed"] == 5
    assert goal["evidence"]["scores"] == {"nova": 1, "atlas": 1}
    assert goal["evidence"]["draws"] == 3
    assert goal["evidence"]["winner"] is None
    assert "tied match" in nova.observe()["narrative"]
    assert "tied match" in atlas.observe()["narrative"]
    saved = read_json(tmp_path / "world.json")
    assert len(saved["rps"]["history"]) == 5


def test_story_records_revealed_moves_score_and_match_winner(tmp_path):
    nova, atlas = players(tmp_path, rounds=1)
    events = []

    def emit(kind, payload, actor):
        sequence = len(events) + 1
        events.append({"id": f"run:{sequence}", "sequence": sequence, "cycle": sequence,
                       "kind": kind, "payload": payload, "actor_id": actor})

    for player in [nova, atlas]:
        emit("observation", player.observe(), player.actor_id)
    for player, move in [(nova, "paper"), (atlas, "rock")]:
        emit("action_request", {"action_id": "play_rps", "arguments": {"move": move}}, player.actor_id)
        emit("action_result", play(player, move), player.actor_id)
    text = "\n".join(paragraph.text for paragraph in build_narrative(events))
    assert "Nova and Atlas face each other" in text
    assert "committing paper privately" in text
    assert "nova played paper, atlas played rock" in text
    assert "Score: nova 1, atlas 0" in text
    assert "nova wins the match" in text
    assert "play rps" not in text


def test_invalid_move_and_stale_request_do_not_commit(tmp_path):
    nova, atlas = players(tmp_path)
    revision = atlas.revision()
    assert play(nova, "lizard")["code"] == "INVALID_ARGUMENTS"
    assert nova.state.rps.pending == {}
    play(nova, "paper")
    stale = atlas.act(uuid4().hex, revision, "play_rps", {"move": "rock"})
    assert stale["code"] == "STALE_OBSERVATION"
    assert nova.state.rps.pending == {"nova": "paper"}


@pytest.mark.parametrize("change", [
    {"agents": [{"id": "nova", "role": "Player"}]},
    {"rps": None},
    {"goal_type": "actor_at_exit_location"},
    {"rps": {"rounds": 0}},
    {"rps": {"rounds": True}},
])
def test_invalid_match_configuration(change):
    raw = json.loads(scenario().model_dump_json())
    raw.update(change)
    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)

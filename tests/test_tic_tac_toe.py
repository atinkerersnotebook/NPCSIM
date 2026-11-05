import itertools
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from npc_sim.contracts import action_result, goal_result, observation
from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.story import build_narrative
from npc_sim.tic_tac_toe import WINNING_LINES
from npc_sim.world import World
from test_controller import TestProxy, configuration
from npc_sim.controller import Controller


ROOT = Path(__file__).resolve().parent.parent


def scenario():
    return Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "tic-tac-toe.json"))


def players(tmp_path):
    nova = World(scenario(), tmp_path / "world.json", "match", "nova", "nova-session")
    return nova, nova.bind("atlas", "atlas-session")


def play(world, cell, request_id=None):
    rid = request_id or uuid4().hex
    return action_result(world.act(rid, world.revision(), "play_tic_tac_toe", {"cell": cell}),
                         rid, world.session_id)


@pytest.mark.parametrize("line", WINNING_LINES)
@pytest.mark.parametrize("winner", ["nova", "atlas"])
def test_every_winning_line_for_both_players(tmp_path, line, winner):
    nova, atlas = players(tmp_path)
    remaining = [cell for cell in range(1, 10) if cell not in line]
    filler = next(cells for cells in itertools.combinations(remaining, 3)
                  if not any(set(winning) <= set(cells) for winning in WINNING_LINES))
    moves = [cell for pair in zip(line, filler) for cell in pair][:5] if winner == "nova" else [
        cell for pair in zip(filler, line) for cell in pair]
    for index, cell in enumerate(moves):
        assert nova.evaluate_goal("tic_tac_toe_completed")["status"] == "active"
        result = play((nova, atlas)[index % 2], cell)
        assert result["outcome"] == "APPLIED"
    goal = goal_result(nova.evaluate_goal("tic_tac_toe_completed"))
    assert goal["status"] == "achieved"
    assert goal["evidence"]["winner"] == winner
    assert goal["evidence"]["winning_cells"] == list(line)
    assert goal["evidence"]["draw"] is False
    assert goal["evidence"]["next_player"] is None
    assert goal["evidence"]["available_cells"] == []
    assert play(nova, remaining[-1])["code"] == "ACTION_UNAVAILABLE"


def test_draw_and_persistent_public_board(tmp_path):
    nova, atlas = players(tmp_path)
    moves = [1, 2, 3, 5, 4, 6, 8, 7, 9]
    for index, cell in enumerate(moves):
        assert play((nova, atlas)[index % 2], cell)["code"] == "MARK_PLACED"
        assert observation(nova.observe())["tic_tac_toe"] == observation(atlas.observe())["tic_tac_toe"]
    goal = nova.evaluate_goal("tic_tac_toe_completed")
    assert goal["status"] == "achieved"
    assert goal["evidence"]["draw"] is True
    assert goal["evidence"]["winner"] is None
    assert goal["evidence"]["board"] == ["X", "O", "X", "X", "O", "O", "O", "X", "X"]
    restored = World(scenario(), tmp_path / "world.json", "match", "atlas", "atlas-session")
    assert restored.observe()["tic_tac_toe"] == goal["evidence"]
    assert "Match complete: draw" in restored.observe()["narrative"]


def test_turns_invalid_cells_stale_requests_and_idempotency(tmp_path):
    nova, atlas = players(tmp_path)
    assert play(atlas, 1)["code"] == "NOT_YOUR_TURN"
    for cell in (0, 10, True, "1", 1.5, None):
        assert play(nova, cell)["code"] == "INVALID_ARGUMENTS"
    assert nova.state.tic_tac_toe.history == []
    revision, rid = nova.revision(), uuid4().hex
    committed = play(nova, 5, rid)
    assert nova.act(rid, revision, "play_tic_tac_toe", {"cell": 5}) == committed
    assert play(nova, 1)["code"] == "NOT_YOUR_TURN"
    assert play(atlas, 5)["code"] == "CELL_OCCUPIED"
    assert atlas.act(uuid4().hex, revision, "play_tic_tac_toe", {"cell": 1})["code"] == "STALE_OBSERVATION"
    restored = World(scenario(), tmp_path / "world.json", "match", "atlas", "atlas-session")
    assert restored.observe()["tic_tac_toe"]["next_player"] == "atlas"
    assert play(restored, 1.0)["code"] == "MARK_PLACED"
    assert len(nova.state.tic_tac_toe.history) == 1
    assert len(restored.state.tic_tac_toe.history) == 2


@pytest.mark.parametrize("change", [
    {"agents": [{"id": "nova", "role": "Player"}]},
    {"agents": [{"id": "nova", "role": "Player"}, {"id": "atlas", "role": "Player", "start_location": "elsewhere"}]},
    {"rps": {"rounds": 5}},
    {"recipe_actions": {"play_tic_tac_toe": "Conflicting action"}},
])
def test_invalid_game_configuration(change):
    raw = scenario().model_dump()
    raw.update(change)
    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


@pytest.mark.asyncio
async def test_controller_completes_game_over_mcp_and_archives_story(configuration):
    data, config = configuration
    config.world.scenario = "tic-tac-toe"
    controller = Controller(ROOT, data, config)
    controller.proxy = TestProxy(controller.proxy, [
        ("play_tic_tac_toe", {"cell": cell}) for cell in [1, 4, 2, 5, 3]])
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in range(5):
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.run["successful"] is True
        assert controller.run["verification"]["evidence"]["winner"] == "nova"
        text = "\n".join(paragraph.text for paragraph in build_narrative(controller.events.events))
        assert "Nova tries placing Nova's mark in cell 1" in text
        assert "Nova wins as X" in text
        assert "play tic tac toe" not in text
        saved = read_json(data / "runs" / controller.run["id"] / "run.json")
        assert saved["successful"] is True
    finally:
        await controller.shutdown()

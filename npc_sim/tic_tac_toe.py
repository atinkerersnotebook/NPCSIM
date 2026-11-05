from typing import TYPE_CHECKING, Any

from npc_sim.models import TicTacToeMove

if TYPE_CHECKING:
    from npc_sim.world import World


WINNING_LINES = (
    (1, 2, 3), (4, 5, 6), (7, 8, 9),
    (1, 4, 7), (2, 5, 8), (3, 6, 9),
    (1, 5, 9), (3, 5, 7),
)


def summary(world: "World") -> dict[str, Any]:
    state = world.state.tic_tac_toe
    if state is None:
        raise ValueError("Missing tic-tac-toe match state")
    players = {agent.id: mark for agent, mark in zip(world.scenario.agents, ("X", "O"))}
    board: list[str | None] = [None] * 9
    for move in state.history:
        board[move.cell - 1] = players[move.actor_id]
    winning_cells = next((list(line) for line in WINNING_LINES
                          if board[line[0] - 1] is not None
                          and len({board[cell - 1] for cell in line}) == 1), [])
    winner = next((actor for actor, mark in players.items()
                   if winning_cells and board[winning_cells[0] - 1] == mark), None)
    complete = winner is not None or len(state.history) == 9
    return {"board": board, "players": players, "moves_played": len(state.history),
            "next_player": None if complete else world.scenario.agents[len(state.history) % 2].id,
            "available_cells": [] if complete else [cell for cell in range(1, 10) if board[cell - 1] is None],
            "complete": complete, "winner": winner, "draw": complete and winner is None,
            "winning_cells": winning_cells, "history": [move.model_dump() for move in state.history]}


def result_text(result: dict[str, Any]) -> str:
    if result["winner"]:
        return f"Match complete. {result['winner']} wins as {result['players'][result['winner']]}."
    if result["draw"]:
        return "Match complete: draw. Neither player has three in a row."
    return f"{result['next_player']}'s turn ({result['players'][result['next_player']]})."


def observation(world: "World") -> dict[str, Any]:
    if world.state.tic_tac_toe is None:
        return {}
    result = summary(world)
    board = result["board"]
    rows = [" | ".join(mark or str(cell) for cell, mark in enumerate(board, 1)
                       if start <= cell < start + 3) for start in (1, 4, 7)]
    text = "Tic-tac-toe. " + ", ".join(f"{actor} is {mark}" for actor, mark in result["players"].items()) + ".\n"
    text += "\n".join(rows) + "\n" + result_text(result)
    if not result["complete"]:
        text += " Place your mark in an empty numbered cell on your turn (1-9, left to right, top to bottom)."
    if world.scenario.opening_narrative and not result["history"] and world.actor.action_clock == 0:
        text = world.scenario.opening_narrative + "\n\n" + text
    return {"tic_tac_toe": result, "narrative": text}


def actions(world: "World") -> list[dict[str, Any]]:
    if world.state.tic_tac_toe is None:
        return []
    result = summary(world)
    if result["complete"]:
        return []
    waiting = result["next_player"] != world.actor_id
    return [{
        "id": "play_tic_tac_toe",
        "description": "Place your mark in an empty cell, numbered 1-9 left to right, top to bottom. X starts; alternate turns. Three matching marks in a row, column or diagonal wins.",
        "arguments_schema": {"type": "object", "properties": {
            "cell": {"type": "integer", "minimum": 1, "maximum": 9}},
            "required": ["cell"], "additionalProperties": False},
        "targets": [], "availability": "blocked" if waiting else "available",
        "reason": "Wait for the other player's move." if waiting else None}]


def apply(world: "World", cell: int) -> tuple[str, str, str, None]:
    state = world.state.tic_tac_toe
    if state is None:
        raise ValueError("Missing tic-tac-toe match state")
    result = summary(world)
    if result["next_player"] != world.actor_id:
        return "REJECTED", "NOT_YOUR_TURN", "Wait for the other player's move.", None
    if cell not in result["available_cells"]:
        return "REJECTED", "CELL_OCCUPIED", "That cell is occupied. Choose an empty cell.", None
    state.history.append(TicTacToeMove(actor_id=world.actor_id, cell=cell))
    text = f"{world.actor_id} places {result['players'][world.actor_id]} in cell {cell}. "
    return "APPLIED", "MARK_PLACED", text + result_text(summary(world)), None


def evaluate_goal(world: "World") -> dict[str, Any]:
    result = summary(world)
    return {"status": "achieved" if result["complete"] else "active", "reason": "",
            "evidence": result, "observation_revision": world.revision()}

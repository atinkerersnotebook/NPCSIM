from typing import TYPE_CHECKING, Any

from npc_sim.models import RPSMove, RPSRound

if TYPE_CHECKING:
    from npc_sim.world import World


BEATS: dict[RPSMove, RPSMove] = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def summary(world: "World") -> dict[str, Any]:
    state, config = world.state.rps, world.scenario.rps
    if state is None or config is None:
        raise ValueError("Missing rock-paper-scissors match state or configuration")
    scores = {agent.id: sum(round.winner == agent.id for round in state.history)
              for agent in world.scenario.agents}
    complete = len(state.history) == config.rounds
    leaders = [actor for actor, score in scores.items() if score == max(scores.values())]
    return {"rounds_completed": len(state.history), "rounds_total": config.rounds,
            "scores": scores, "draws": sum(round.winner is None for round in state.history),
            "complete": complete, "winner": leaders[0] if complete and len(leaders) == 1 else None,
            "history": [round.model_dump() for round in state.history]}


def round_text(number: int, round: RPSRound) -> str:
    moves = ", ".join(f"{actor} played {move}" for actor, move in round.moves.items())
    return f"Round {number}: {moves}. " + (
        f"{round.winner} wins the round." if round.winner else "The round is a draw.")


def observation(world: "World") -> dict[str, Any]:
    state = world.state.rps
    if state is None:
        return {}
    public = summary(world)
    score = ", ".join(f"{actor} {points}" for actor, points in public["scores"].items())
    text = f"Rock-paper-scissors: {public['rounds_completed']} of {public['rounds_total']} rounds completed. Score: {score}."
    if state.history:
        text += " " + round_text(len(state.history), state.history[-1])
    if public["complete"]:
        text += f" Match complete. {public['winner']} wins the match." if public["winner"] else " Match complete: tied match."
    elif world.actor_id in state.pending:
        text += " Your move is locked in. The opponent's current move remains private until both commit."
    else:
        text += " Choose rock, paper or scissors with the private move action. Both moves are revealed only after both players commit."
    if (world.scenario.opening_narrative and world.actor.action_clock == 0
            and not world.actor.examined and not world.actor.read_entities and not state.history):
        text = world.scenario.opening_narrative + "\n\n" + text
    return {"rps": {**public, "your_move": state.pending.get(world.actor_id)},
            "narrative": text}


def actions(world: "World") -> list[dict[str, Any]]:
    state = world.state.rps
    if state is None or summary(world)["complete"]:
        return []
    locked = world.actor_id in state.pending
    return [{
        "id": "play_rps",
        "description": "Privately lock in one move for this round. Rock beats scissors, scissors beats paper, paper beats rock. Moves cannot be changed; both reveal together.",
        "arguments_schema": {"type": "object", "properties": {
            "move": {"type": "string", "enum": list(BEATS)}},
            "required": ["move"], "additionalProperties": False},
        "targets": [], "availability": "blocked" if locked else "available",
        "reason": "Your move is already locked; wait for the other player." if locked else None}]


def apply(world: "World", move: RPSMove) -> tuple[str, str, str, None]:
    state = world.state.rps
    if state is None:
        raise ValueError("Missing rock-paper-scissors match state")
    if world.actor_id in state.pending:
        return "REJECTED", "MOVE_LOCKED", "Your move is already locked for this round.", None
    state.pending[world.actor_id] = move
    if len(state.pending) < 2:
        return "APPLIED", "MOVE_COMMITTED", "Your private move is locked in; awaiting the other player.", None
    first, second = [agent.id for agent in world.scenario.agents]
    a, b = state.pending[first], state.pending[second]
    winner = None if a == b else first if BEATS[a] == b else second
    round = RPSRound(moves=state.pending.copy(), winner=winner)
    state.history.append(round)
    state.pending.clear()
    text = round_text(len(state.history), round)
    result = summary(world)
    text += " Score: " + ", ".join(f"{actor} {points}" for actor, points in result["scores"].items()) + "."
    if result["complete"]:
        text += f" Match complete. {result['winner']} wins the match." if result["winner"] else " Match complete: tied match."
    return "APPLIED", "ROUND_REVEALED", text, None


def evaluate_goal(world: "World") -> dict[str, Any]:
    result = summary(world)
    return {"status": "achieved" if result["complete"] else "active", "reason": "",
            "evidence": result, "observation_revision": world.revision()}

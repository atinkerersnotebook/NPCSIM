import argparse
import logging
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from npc_sim.models import Scenario
from npc_sim.storage import read_json
from npc_sim.world import World
from npc_sim.interactions import WorldEditPlan, apply_edit, validate_edit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--world-id", required=True)
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    world = World(Scenario.model_validate(read_json(Path(args.scenario))), Path(args.state),
                  args.world_id, args.actor_id, args.session_id)
    bindings = {world.actor_id: world}
    for agent in world.scenario.agents:
        saved = world.state.actors.get(agent.id)
        bindings[agent.id] = world.bind(agent.id, saved.session_id if saved else uuid4().hex)
    server = FastMCP("NPC Adventure World")

    @server.tool()
    def world_select_actor(actor_id: str, session_id: str) -> dict[str, Any]:
        """Select a pre-bound actor for the owning local controller's next turn."""
        nonlocal world
        bound = bindings.get(actor_id)
        if bound is None or bound.session_id != session_id:
            raise ValueError("Unknown actor binding or mismatched session")
        world = bound
        return world.observe()

    @server.tool()
    def world_next_village_actor(stop_at_minute: int | None = None) -> dict[str, Any]:
        """Privileged controller scheduler; advance only to the next available village turn."""
        from npc_sim import village_turns
        with world._lock:
            if world.state.village is None:
                raise ValueError("This world does not have a village scheduler")
            before = world.state.model_copy(deep=True)
            try:
                actor_id = village_turns.next_actor(world, stop_at_minute)
                return {"actor_id": actor_id, "minute": world.state.village.minute}
            except (OSError, ValueError):
                world.state = before
                raise

    @server.tool()
    def world_manifest() -> dict[str, Any]:
        """Read the versioned world interface contract, not hidden scenario data."""
        return {
            "contract_version": "1.0",
            "operations": {"observe": "world_observe", "act": "world_act",
                           "evaluate_goal": "world_evaluate_goal"},
            "goal_types": [world.scenario.goal_type],
            "idempotent_actions": True,
            "admin_inspection": "local_json",
            "actor_sessions": {actor_id: bound.session_id for actor_id, bound in bindings.items()},
            "actor_selection": "world_select_actor",
            "admin_intervention": "world_apply_edit",
            "admin_validate_intervention": "world_validate_edit",
            "village_scheduler": "world_next_village_actor"
            if world.scenario.village and world.scenario.village.time_based_turns else None,
            "village_full_rules": bool(world.scenario.village and world.scenario.village.expanded_rules),
        }

    @server.tool()
    def world_observe() -> dict[str, Any]:
        """Observe your own self and visible surroundings, including contextual actions."""
        return world.observe()

    @server.tool()
    def world_act(request_id: str, expected_observation_revision: str,
                  action_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute one advertised action as the actor bound to this MCP connection."""
        return world.act(request_id, expected_observation_revision, action_id, arguments)

    @server.tool()
    def world_evaluate_goal(goal_type: str) -> dict[str, Any]:
        """Verify whether your actor has achieved a supported goal."""
        return world.evaluate_goal(goal_type)

    @server.tool()
    def world_validate_edit(expected_revision: str, plan: dict[str, Any]) -> dict[str, Any]:
        """Privileged operator-only validation. Never an NPC action or a world mutation."""
        with world._lock:
            world.tick()
            if expected_revision != world.revision():
                raise ValueError("The world changed. Request a fresh proposal.")
            validate_edit(world, WorldEditPlan.model_validate(plan))
            return {"revision": world.revision()}

    @server.tool()
    def world_apply_edit(request_id: str, expected_revision: str, plan: dict[str, Any]) -> dict[str, Any]:
        """Apply only the local operator's approved, revision-checked intervention."""
        return apply_edit(world, request_id, expected_revision, WorldEditPlan.model_validate(plan))

    stopped = threading.Event()

    def clock_worker() -> None:
        while not stopped.wait(0.25):
            try:
                world.tick()
            except Exception:
                logging.exception("Bakery clock failed")
                stopped.set()

    ticker = threading.Thread(target=clock_worker, name="bakery-clock", daemon=True)
    if world.state.clock is not None:
        ticker.start()
    try:
        server.run(transport="stdio")
    finally:
        stopped.set()
        if ticker.is_alive():
            ticker.join()


if __name__ == "__main__":
    main()

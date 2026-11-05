from pathlib import Path

import pytest

from npc_sim.contracts import action_result, observation
from npc_sim.mcp_client import WorldClient
from npc_sim.models import WorldConfig


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_real_stdio_mcp_roundtrip(tmp_path):
    client = WorldClient(WorldConfig(), ROOT, ROOT / "data" / "scenarios" / "escape-room.json",
                         tmp_path / "world-state.json", "world", "npc", "session")
    try:
        await client.connect()
        assert "world_act" in client.tools
        assert not any("admin" in name for name in client.tools)
        initial = observation(await client.operation("observe", {}), "session")
        assert "key" not in [entity["id"] for entity in initial["visible_entities"]]
        assert initial["self"]["body"]["carrying"] == {"rule": "inventory_units", "limit": 1, "used": 0}
        request = {"request_id": "open-box", "expected_observation_revision": initial["observation_revision"],
                   "action_id": "open", "arguments": {"target": "box"}}
        result = action_result(await client.operation("act", request), "open-box", "session")
        assert result["outcome"] == "APPLIED"
        assert "key" in [entity["id"] for entity in result["observation"]["visible_entities"]]
        assert await client.operation("act", request) == result
        take = {"request_id": "take-key", "expected_observation_revision": result["observation"]["observation_revision"],
                "action_id": "take", "arguments": {"target": "key"}}
        carried = action_result(await client.operation("act", take), "take-key", "session")
        assert carried["observation"]["self"]["body"]["carrying"]["used"] == 1
        assert carried["observation"]["self"]["inventory"] == [{"id": "key", "name": "Brass key", "inventory_units": 1}]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_two_actors_share_one_mcp_process_and_saved_bindings(tmp_path):
    path = tmp_path / "world-state.json"
    scenario = ROOT / "data" / "scenarios" / "two-agent-escape.json"
    client = WorldClient(WorldConfig(), ROOT, scenario, path, "world", "scout", "scout-session")
    try:
        await client.connect()
        observed = observation(await client.select_actor("locksmith"))
        locksmith_session = observed["session_id"]
        assert observed["self"]["actor_id"] == "locksmith"
        assert len(client.manifest["actor_sessions"]) == 2
        with pytest.raises(ValueError, match="mismatched"):
            await client.call("world_select_actor", {"actor_id": "scout", "session_id": locksmith_session})
        observed = observation(await client.select_actor("scout"), "scout-session")
        await client.operation("act", {
            "request_id": "open", "expected_observation_revision": observed["observation_revision"],
            "action_id": "open", "arguments": {"target": "supply-box"}})
        other = observation(await client.select_actor("locksmith"), locksmith_session)
        assert "passage-key" in {entity["id"] for entity in other["visible_entities"]}
        assert other["self"]["inventory"] == []
    finally:
        await client.close()
    restored = WorldClient(WorldConfig(), ROOT, scenario, path, "world", "locksmith", locksmith_session)
    try:
        await restored.connect()
        assert restored.manifest["actor_sessions"]["scout"] == "scout-session"
        assert restored.manifest["actor_sessions"]["locksmith"] == locksmith_session
        assert (await restored.select_actor("scout"))["self"]["actor_id"] == "scout"
    finally:
        await restored.close()

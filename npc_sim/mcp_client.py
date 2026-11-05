import asyncio
import json
import sys
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any

from jsonschema import validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from npc_sim.models import WorldConfig


class WorldClient:
    def __init__(self, config: WorldConfig, root: Path, scenario_path: Path,
                 state_path: Path, world_id: str, actor_id: str, session_id: str):
        self.config = config
        self.root = root
        self.scenario_path = scenario_path
        self.state_path = state_path
        self.world_id = world_id
        self.actor_id = actor_id
        self.session_id = session_id
        self.stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.tools: dict[str, Any] = {}
        self.manifest: dict[str, Any] = {}

    async def connect(self) -> None:
        if self.config.transport == "stdio":
            command = self.config.command or sys.executable
            bindings = {"scenario": str(self.scenario_path), "state": str(self.state_path),
                        "world_id": self.world_id, "actor_id": self.actor_id, "session_id": self.session_id}
            arguments = [argument.format_map(bindings) for argument in self.config.args] if self.config.command else [
                "-m", "npc_sim.world_server", "--scenario", str(self.scenario_path),
                "--state", str(self.state_path), "--world-id", self.world_id,
                "--actor-id", self.actor_id, "--session-id", self.session_id,
            ]
            read, write = await self.stack.enter_async_context(stdio_client(
                StdioServerParameters(command=command, args=arguments, cwd=str(self.root))))
        else:
            if not self.config.url:
                raise ValueError("Streamable HTTP world requires a URL")
            read, write, _ = await self.stack.enter_async_context(streamable_http_client(self.config.url))
        self.session = await self.stack.enter_async_context(ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=15)))
        await self.session.initialize()
        discovered = await self.session.list_tools()
        self.tools = {tool.name: tool for tool in discovered.tools}
        self.manifest = await self.call("world_manifest", {})
        if self.manifest.get("contract_version") != "1.0":
            raise ValueError("Unsupported world MCP contract")
        for operation in ("observe", "act", "evaluate_goal"):
            name = self.manifest["operations"][operation]
            if name not in self.tools:
                raise ValueError(f"World manifest references missing tool {name}")

    async def close(self) -> None:
        await self.stack.aclose()
        self.session = None

    async def select_actor(self, actor_id: str) -> dict[str, Any]:
        sessions = self.manifest.get("actor_sessions", {})
        if actor_id not in sessions or self.manifest.get("actor_selection") not in self.tools:
            raise ValueError("World does not support the requested shared-world actor binding")
        session_id = sessions[actor_id]
        result = await self.call(self.manifest["actor_selection"], {
            "actor_id": actor_id, "session_id": session_id})
        if result.get("session_id") != session_id or result.get("self", {}).get("actor_id") != actor_id:
            raise ValueError("World selected an unexpected actor binding")
        self.actor_id, self.session_id = actor_id, session_id
        return result
    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.session is None or name not in self.tools:
            raise ValueError(f"MCP tool is not connected/discovered: {name}")
        validate(arguments, self.tools[name].inputSchema)
        result = await self.session.call_tool(name, arguments)
        if result.isError:
            text = "; ".join(content.text for content in result.content if content.type == "text")
            raise ValueError(f"MCP tool {name} failed: {text}")
        if result.structuredContent is not None:
            payload = result.structuredContent
        else:
            text = next((content.text for content in result.content if content.type == "text"), None)
            if text is None:
                raise ValueError("MCP response has no structured JSON content")
            payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("MCP world response must be a JSON object")
        return payload

    async def operation(self, name: str, arguments: dict[str, Any], timeout: float = 15) -> dict[str, Any]:
        return await asyncio.wait_for(self.call(self.manifest["operations"][name], arguments), timeout)

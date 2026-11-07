import asyncio
import copy
import json
import logging
import math
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Literal, TypeVar
from uuid import uuid4

import httpx
from jsonschema import ValidationError as SchemaError, validate
from pydantic import ValidationError

from npc_sim.contracts import action_result, goal_result, observation
from npc_sim.mcp_client import WorldClient
from npc_sim.models import AppConfig, BudgetExtension, Budgets, ClockMode, ClockRequest, Decision, ReopenRequest, Scenario, SkillMode, WorldState
from npc_sim.proxy import Proxy
from npc_sim.prompt_context import REVIEW_EVENTS
from npc_sim.storage import EventStore, read_json, write_json
from npc_sim.assessment import CompletionAnalysis, grade, load_memory, record_feedback, remove_feedback
from npc_sim.story import build_narrative
from npc_sim.agents import assign_profiles, load_profiles, profile_context
from npc_sim.dungeon_master import DungeonMasterConfig, DungeonMasterReview, load_config as load_dungeon_master, validate_review


LOGGER = logging.getLogger(__name__)
TERMINAL = {"succeeded", "blocked", "stalled", "budget_exceeded", "cancelled", "error"}
ProxyResult = TypeVar("ProxyResult")


class LLMUnavailable(RuntimeError):
    def __init__(self, message: str, phase: Literal["decision", "dungeon_master"]):
        super().__init__(message)
        self.phase = phase


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Controller:
    def __init__(self, root: Path, data: Path, config: AppConfig):
        self.root, self.data, self.config = root, data, config
        self.proxy = Proxy(config.proxy)
        self.world: WorldClient | None = None
        self.run: dict[str, Any] | None = None
        self.observation: dict[str, Any] | None = None
        self.memory: dict[str, Any] = {"schema_version": "1.0", "recent": [], "discoveries": [], "plan": [], "subgoal": ""}
        self.events: EventStore | None = None
        self.queue: asyncio.Queue[tuple[str, dict[str, Any], asyncio.Future[Any]]] = asyncio.Queue()
        self.worker: asyncio.Task[None] | None = None
        self.started_clock: float | None = None
        self.patterns: list[str] = []
        self.replan_count = 0
        self.in_flight = False
        self.wait_until: float | None = None
        self.wait_revision: str | None = None
        self.agents: dict[str, dict[str, Any]] = {}
        self._snapshot_key: tuple[Path, int, int] | None = None
        self._world_snapshot: dict[str, Any] | None = None

    def _save_agent(self) -> None:
        if self.run and self.agents:
            self.agents[self.run["actor_id"]].update(
                memory=self.memory, observation=self.observation, patterns=self.patterns,
                replan_count=self.replan_count, wait_until=self.wait_until,
                wait_revision=self.wait_revision, goal=self.run["goal"])

    async def _select_turn(self) -> bool:
        if not self.agents or not self.run or not self.world:
            return True
        self._save_agent()
        ids = list(self.agents)
        scheduler = self.world.manifest.get("village_scheduler") if self.run["status"] != "ready" else None
        retry_actor = self.run.pop("pending_actor_id", None) or self.run.pop("pending_budget_actor", None)
        if retry_actor:
            index = ids.index(retry_actor)
        elif scheduler:
            stop = self.run.get("village_stop_minute")
            chosen = await self.world.call(scheduler, {} if stop is None else {"stop_at_minute": stop})
            actor_id = chosen.get("actor_id")
            if actor_id is None and stop is not None and chosen.get("minute") == stop:
                self.observation = observation(await self.world.operation("observe", {}), self.world.session_id)
                self.emit("observation", self.observation)
                self.run.pop("village_stop_minute")
                self.run.update(status="paused", reason="Reached midnight (00:00). Continue when ready.")
                self._pause_budget()
                self.emit("control", {"command": "pause", "source": "midnight",
                                      "reason": self.run["reason"]})
                return False
            if actor_id not in self.agents:
                raise ValueError("Village scheduler returned an unknown actor")
            index = ids.index(actor_id)
        else:
            index = self.run.get("next_agent_index", 0) % len(ids)
        for offset in range(len(ids)):
            candidate = (index + offset) % len(ids)
            saved = self.agents[ids[candidate]]
            if (self.run["goal"]["type"] == "all_actors_at_exit_location"
                    and (saved.get("observation") or {}).get("self", {}).get("location_id") == self.run["exit_location"]):
                continue
            index = candidate
            break
        actor_id = ids[index]
        saved = self.agents[actor_id]
        observed = observation(await self.world.select_actor(actor_id), saved["session_id"])
        self.run.update(actor_id=actor_id, session_id=saved["session_id"],
                        goal=saved["goal"], next_agent_index=(index + 1) % len(ids))
        self.memory, self.observation = saved["memory"], observed
        self.patterns, self.replan_count = saved["patterns"], saved["replan_count"]
        self.wait_until, self.wait_revision = saved["wait_until"], saved["wait_revision"]
        return True

    @property
    def budgets(self) -> Budgets:
        return Budgets.model_validate(self.run["config"]["budgets"]) if self.run else self.config.budgets

    def _continuous_clock(self) -> bool:
        clock = self.observation.get("clock") if self.observation else None
        return clock is not None and clock.get("mode", "continuous") == "continuous"

    def _write_clock(self, running: bool, rate: float | None = None, mode: ClockMode | None = None) -> bool:
        if not self.world or not self.run:
            return False
        snapshot = self.status()["world"]
        if snapshot is None or snapshot["clock"] is None:
            return False
        write_json(self.world.state_path.parent / "clock-command.json", {
            "id": uuid4().hex, "world_id": self.world.world_id,
            "running": running, "rate": rate if rate is not None else snapshot["clock"]["rate"],
            "mode": mode if mode is not None else snapshot["clock"]["mode"]})
        return True

    async def _set_clock(self, running: bool, rate: float | None = None, mode: ClockMode | None = None) -> None:
        if self._write_clock(running, rate, mode) and self.world:
            self.observation = observation(await self.world.operation("observe", {}), self.world.session_id)
            if self.wait_until is not None:
                self.wait_revision = self.observation["observation_revision"]

    def _pause_budget(self) -> None:
        if self.run and self.started_clock is not None:
            self.run["elapsed_seconds"] = time.monotonic() - self.started_clock
            self.started_clock = None

    async def start_worker(self) -> None:
        self._restore_history()
        self.worker = asyncio.create_task(self._worker(), name="npc-loop")

    def _restore_history(self) -> None:
        paths = sorted((self.data / "runs").glob("*/run.json"), key=lambda path: path.stat().st_mtime)
        if not paths:
            return
        self.run = read_json(paths[-1])
        if self.run.get("schema_version") != "1.0":
            raise ValueError("Unsupported saved run schema")
        self.events = EventStore(paths[-1].parent, self.run["id"])
        self.memory = read_json(paths[-1].parent / "npc-memory.json")
        self.agents = self.run.get("agents", {})
        self.observation = self.run.get("observation")
        saved = self.agents.get(self.run["actor_id"]) or self.run.get("turn_state")
        if saved:
            self.patterns, self.replan_count = saved["patterns"], saved["replan_count"]
            self.wait_until, self.wait_revision = saved["wait_until"], saved["wait_revision"]
        if self.run["status"] not in TERMINAL:
            self.run["status"] = "error"
            self.run["reason"] = "Interrupted by process restart; inspect saved requests before starting a fresh episode."
            self.emit("interrupted", {"reason": self.run["reason"]})
            self._summary()
        if not self.run.get("completion_analysis") or self.run["completion_analysis"]["status"] in {"pending", "running"}:
            self.run["completion_analysis"] = CompletionAnalysis().model_dump(mode="json")
            self.run["successful"] = None
        self.checkpoint()

    async def shutdown(self, *, preserve_paused: bool = False) -> None:
        if self.worker:
            await self.command("shutdown", {"preserve_paused": preserve_paused})
            await self.worker

    async def command(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((name, arguments or {}, future))
        return await asyncio.shield(future)

    async def _worker(self) -> None:
        while True:
            if self.run and self.run["status"] in TERMINAL and self.run.get("completion_analysis", {}).get("status") == "pending":
                await self.assess_completion()
            automatic = self.run is not None and self.run["status"] == "running"
            try:
                if automatic:
                    item = await asyncio.wait_for(self.queue.get(), timeout=0.05)
                else:
                    item = await self.queue.get()
            except TimeoutError:
                await self.cycle()
                continue
            name, arguments, future = item
            previous_run_id = self.run["id"] if self.run else None
            try:
                if name == "shutdown":
                    if (self.run and self.run["status"] not in TERMINAL
                            and not (arguments.get("preserve_paused") and self.run["status"] == "paused")):
                        self.finish("cancelled", "Simulator shut down.")
                        await self.assess_completion()
                    if self.world:
                        await self.world.close()
                    future.set_result(None)
                    return
                result = await self._command(name, arguments)
                if self.run and self.run["status"] in TERMINAL and self.run.get("completion_analysis", {}).get("status") == "pending":
                    await self.assess_completion()
                    result = self.status()
                future.set_result(result)
            except Exception as error:
                LOGGER.exception("Control operation %s failed", name)
                if self.run and name in {"start", "reset"} and self.run["id"] != previous_run_id:
                    self.finish("error", self.safe_error(error))
                future.set_exception(error)

    async def _command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in {"npc_chat", "propose_edit", "approve_edit"}:
            from npc_sim.interactions import handle
            await handle(self, name, args)
        elif name in {"start", "reset"}:
            scenario_id = args.get("scenario_id")
            if name == "reset" and scenario_id is None and self.run:
                scenario_id = self.run["scenario_id"]
            assignments = args.get("agent_assignments")
            skill_mode = args.get("skill_mode")
            if (name == "reset" and assignments is None and self.run
                    and scenario_id == self.run["scenario_id"]):
                assignments = self.run.get("agent_assignments")
            if (name == "reset" and skill_mode is None and self.run
                    and scenario_id == self.run["scenario_id"]):
                skill_mode = self.run.get("skill_mode")
            await self.new_run(args.get("objective"), scenario_id, assignments, skill_mode)
        elif name == "assess":
            if not self.run or self.run["status"] not in TERMINAL:
                raise ValueError("End the attempt before assessing its completed story.")
            await self.assess_completion()
        elif name == "extend_budget":
            await self.extend_budget(BudgetExtension.model_validate(args))
        elif name == "reopen":
            await self.reopen(ReopenRequest.model_validate(args))
        elif name == "configure":
            if self.run and self.run["status"] not in TERMINAL | {"ready", "paused"}:
                raise ValueError("Pause the episode before changing proxy configuration.")
            config = AppConfig.model_validate(args)
            if config.world != self.config.world:
                raise ValueError("Change world transport/scenario in data/config.json and restart.")
            write_json(self.data / "config.json", config.model_dump(mode="json"))
            self.config, self.proxy = config, Proxy(config.proxy)
            if self.run:
                episode_budgets = self.run["config"]["budgets"]
                self.run["config"] = config.model_dump(mode="json")
                frozen_path = self.events.directory / "scenario.json"
                scenario_budgets = read_json(frozen_path).get("budgets") if frozen_path.exists() else None
                if scenario_budgets is not None or self.run["scenario_id"] == "bread-bakery":
                    self.run["config"]["budgets"] = episode_budgets
                self.run["config"]["world"]["scenario"] = self.run["scenario_id"]
                self.run["model"] = config.proxy.model
                self.emit("configuration", {"config": config.model_dump(mode="json")})
                self.checkpoint()
        elif name == "clock":
            if not self.world or not self.run or self.run["status"] in TERMINAL:
                raise ValueError("Start an active timed episode before changing its clock.")
            if self.config.world.command or self.world.manifest.get("admin_inspection") != "local_json":
                raise ValueError("Clock controls require the built-in local world adapter.")
            request = ClockRequest.model_validate(args)
            snapshot = self.status()["world"]
            if snapshot is None or snapshot["clock"] is None:
                raise ValueError("This scenario does not have a real-time clock.")
            mode = request.mode or snapshot["clock"]["mode"]
            running = self.run["status"] == "running" and (mode == "continuous" or self.wait_until is not None)
            await self._set_clock(running, request.rate, mode)
            self.emit("clock_rate", request.model_dump())
            self.checkpoint()
        elif name == "need":
            if not self.world or not self.run or self.run["status"] in TERMINAL:
                raise ValueError("Start an active episode before changing needs.")
            if self.world.manifest.get("admin_inspection") != "local_json" or self.config.world.command:
                raise ValueError("This world requires its own compatible privileged admin adapter.")
            snapshot = self.status()["world"]
            if snapshot is None or args["actor_id"] not in snapshot["actors"]:
                raise ValueError("Unknown actor")
            if args["need"] not in snapshot["actors"][args["actor_id"]]["needs"]:
                raise ValueError("Unknown need")
            write_json(self.world.state_path.parent / "needs-command.json", {
                "schema_version": "1.0", "id": uuid4().hex, "world_id": self.world.world_id,
                "actor_id": args["actor_id"], "need": args["need"], "enabled": args["enabled"],
            })
            self.observation = observation(await self.world.operation("observe", {}), self.world.session_id)
            self.emit("need_toggle", args)
            self.emit("observation", self.observation)
            self.checkpoint()
        else:
            if not self.run or self.run["status"] in TERMINAL or not self.world:
                raise ValueError("Start a new episode first.")
            if name in {"run", "resume"}:
                if self.run["status"] == "running":
                    raise ValueError("The episode is already running.")
                self._activate_clock()
                self.run["status"] = "running"
                self.run["reason"] = ""
                await self._set_clock(self._continuous_clock() or self.wait_until is not None)
                self.emit("control", {"command": name})
            elif name == "pause":
                self.run["status"] = "paused"
                await self._set_clock(False)
                self._pause_budget()
                self.emit("control", {"command": name})
            elif name == "step":
                if self.run["status"] == "running":
                    raise ValueError("Pause before stepping.")
                self._activate_clock()
                self.run["status"] = "paused"
                self.run["reason"] = ""
                await self._set_clock(self._continuous_clock())
                await self.cycle()
                await self._set_clock(False)
                self._pause_budget()
            elif name == "cancel":
                self.finish("cancelled", "Cancelled by administrator.")
            else:
                raise ValueError(f"Unknown command: {name}")
            self.checkpoint()
        return self.status()

    async def extend_budget(self, request: BudgetExtension) -> None:
        if not self.run or request.run_id != self.run["id"]:
            raise ValueError("The adventure changed. Refresh before extending its budget.")
        if self.run["status"] not in {"ready", "running", "paused", "budget_exceeded"}:
            raise ValueError("Only active or budget-exhausted adventures can be extended.")
        previous = self.budgets.model_dump(mode="json")
        updated = Budgets.model_validate({
            **previous,
            "max_actions": previous["max_actions"] + request.add_actions,
            "max_model_calls": previous["max_model_calls"] + request.add_model_calls,
            "max_seconds": previous["max_seconds"] + request.add_seconds,
        })
        if not math.isfinite(updated.max_seconds):
            raise ValueError("The extended time limit must be finite.")
        reopening = self.run["status"] == "budget_exceeded"
        if reopening:
            exhausted = [
                label for label, used, limit in (
                    ("actions", self.run["actions"], updated.max_actions),
                    ("model calls", self.run["model_calls"], updated.max_model_calls),
                    ("active seconds", self.run["elapsed_seconds"], updated.max_seconds),
                ) if used >= limit
            ]
            if exhausted:
                raise ValueError(f"Also extend the exhausted limits before continuing: {', '.join(exhausted)}.")
        if self.world is None:
            world_config = AppConfig.model_validate(self.run["config"]).world
            if not reopening or world_config.command or world_config.transport != "stdio":
                raise ValueError("This adventure's live world is unavailable; its budget cannot be extended.")
            await self._connect_saved_world(require_frozen=True)
        if reopening:
            self._reopen_attempt("Budget extended. Continue when ready.")
        self.run["config"]["budgets"] = updated.model_dump(mode="json")
        self.emit("budget_extended", {
            "previous": previous, "budgets": self.run["config"]["budgets"],
            "additions": request.model_dump(exclude={"run_id"}), "reopened": reopening,
        })
        self.checkpoint()
        self._summary()

    def _reopen_attempt(self, reason: str, *, reset_achieved: bool = False) -> None:
        if not self.run:
            raise ValueError("No adventure to reopen.")
        remove_feedback(self.data, self.run["scenario_id"], self.run["id"])
        analysis = self.run.pop("completion_analysis", {})
        self.run["previous_review_model_calls"] = (
            self.run.get("previous_review_model_calls", 0) + analysis.get("model_calls", 0))
        for memory in [self.memory, *(agent["memory"] for agent in self.agents.values())]:
            if memory.get("completion_feedback", {}).get("source_run_id") == self.run["id"]:
                memory.pop("completion_feedback")
        for goal in [self.run["goal"], *(agent["goal"] for agent in self.agents.values())]:
            if goal["status"] == "failed" or (reset_achieved and goal["status"] == "achieved"):
                goal["status"] = "active" if goal.get("revealed", True) else "undiscovered"
        self.started_clock = None
        self.run.update(status="paused", reason=reason, successful=None)

    async def reopen(self, request: ReopenRequest) -> None:
        if not self.run or request.run_id != self.run["id"]:
            raise ValueError("The adventure changed. Refresh before reopening it.")
        if not self.events:
            raise ValueError("Saved adventure history is unavailable.")
        analysis = self.run.get("completion_analysis", {})
        previous_status = self.run["status"]
        timeout_recovery = (
            previous_status == "error"
            and self.run.get("reason", "").startswith("Timed out while waiting for response to ClientRequest.")
        )
        premature = (previous_status == "succeeded" and analysis.get("status") == "completed"
                     and analysis.get("verdict") == "fail")
        if self.run["goal"]["type"] != "village_restored" or not (premature or timeout_recovery):
            raise ValueError("Only a prematurely completed village adventure or a confirmed free-turn MCP timeout can be reopened.")
        config = AppConfig.model_validate(self.run["config"]).world
        if config.command or config.transport != "stdio":
            raise ValueError("Reopening requires the built-in saved local world.")
        retry_actor = None
        stop_at_minute = None
        if timeout_recovery or request.until_midnight:
            state_path = self.data / "worlds" / self.run["world_id"] / "world-state.json"
            saved_world = WorldState.model_validate(read_json(state_path))
            if saved_world.world_id != self.run["world_id"] or saved_world.village is None:
                raise ValueError("The saved village identity does not match this adventure.")
            if request.until_midnight:
                stop_at_minute = (saved_world.village.minute // 1440 + 1) * 1440
            if timeout_recovery:
                results = {event["payload"]["request_id"] for event in self.events.events
                           if event["kind"] == "action_result"}
                last_action = next((event for event in reversed(self.events.events)
                                    if event["kind"] == "action_result"), None)
                pending = any(event["payload"]["request_id"] not in results for event in self.events.events
                              if event["kind"] == "action_request")
                retry_actor = saved_world.village.free_turn_actor
                free_turn = (last_action is not None and last_action.get("free_action")
                             and retry_actor == last_action["actor_id"])
                scheduled_actor = saved_world.village.last_scheduled_actor
                selected_turn = (last_action is not None and scheduled_actor != last_action["actor_id"]
                                 and self.run.get("actor_id") == last_action["actor_id"]
                                 and saved_world.village.free_turn_actor is None
                                 and not last_action.get("free_action"))
                if not free_turn and selected_turn:
                    retry_actor = scheduled_actor
                if pending or not (free_turn or selected_turn) or retry_actor not in self.agents:
                    raise ValueError("The timed-out turn is not a confirmed free-action boundary or saved next turn; inspect outstanding requests before recovery.")
        if self.world is None:
            await self._connect_saved_world(require_frozen=True)
        if self.world is None:
            raise ValueError("The saved world is unavailable.")
        if request.until_midnight and not self.world.manifest.get("village_scheduler"):
            raise ValueError("Midnight stopping requires timed village turns.")
        verification = goal_result(await self.world.operation(
            "evaluate_goal", {"goal_type": self.run["goal"]["type"]}, 5))
        if (verification["status"] != "active"
                or verification.get("evidence", {}).get("completion_scope") != "full_village_day"):
            raise ValueError("The world does not confirm unfinished daily-life requirements; no result was changed.")
        self._reopen_attempt("Saved village recovered. Continue when ready." if timeout_recovery else
                             "Daily routines are unfinished. Continue when ready.", reset_achieved=True)
        self.run["verification"] = verification
        if stop_at_minute is not None:
            self.run["village_stop_minute"] = stop_at_minute
        if retry_actor:
            self.run["pending_actor_id"] = retry_actor
        elif self.agents:
            last_action = next((event for event in reversed(self.events.events)
                                if event["kind"] == "action_result"), None)
            if (last_action is None or last_action["cycle"] != self.run["cycles"]
                    or last_action["actor_id"] != self.run["actor_id"]):
                self.run["pending_actor_id"] = self.run["actor_id"]
        self.emit("adventure_reopened", {"previous_status": previous_status, "verification": verification,
                                         "stop_at_minute": self.run.get("village_stop_minute")})
        self.checkpoint()
        self._summary()

    async def new_run(self, objective: str | None, scenario_id: str | None = None,
                      agent_assignments: dict[str, str] | None = None,
                      skill_mode: SkillMode | None = None) -> None:
        scenario_id = scenario_id or self.config.world.scenario
        if not scenario_id or any(not (character.isascii() and (character.isalnum() or character in "_-")) for character in scenario_id):
            raise ValueError("Invalid scenario ID")
        scenario_path = self.data / "scenarios" / f"{scenario_id}.json"
        if not scenario_path.exists():
            raise ValueError(f"Unknown scenario: {scenario_id}")
        scenario = Scenario.model_validate(read_json(scenario_path))
        scenario, profiles = assign_profiles(scenario, load_profiles(self.data), agent_assignments, skill_mode)
        dungeon_master = load_dungeon_master(self.data)
        if ((agent_assignments is not None or scenario.skill_mode == "npc_only")
                and (self.config.world.command or self.config.world.transport != "stdio")):
            raise ValueError("Agent assignment requires the built-in local stdio world adapter.")
        if len(scenario.agents) > 1 and (self.config.world.command or self.config.world.transport != "stdio"):
            raise ValueError("Multi-agent episodes require the built-in single-process stdio world adapter.")
        load_memory(self.data, scenario.id)
        if objective and objective.strip() != scenario.objective:
            raise ValueError(f"This scenario verifies '{scenario.objective}'; arbitrary objectives need a world-supported evaluator.")
        if self.run and self.run["status"] not in TERMINAL:
            self.finish("cancelled", "Replaced by a fresh episode.")
            await self.assess_completion()
        if self.world:
            await self.world.close()
            self.world = None
        self.agents = {}
        rid, world_id, session_id = uuid4().hex, uuid4().hex, uuid4().hex
        actor_id = scenario.agents[0].id if scenario.agents else "npc-001"
        directory = self.data / "runs" / rid
        self.run = {
            "schema_version": "1.0", "id": rid, "world_id": world_id,
            "session_id": session_id, "actor_id": actor_id, "scenario_id": scenario.id,
            "title": scenario.title, "status": "ready", "reason": "",
            "created_at": utc_now(), "actions": 0, "model_calls": 0, "cycles": 0,
            "model": self.config.proxy.model, "elapsed_seconds": 0,
            "successful": None,
            "goal": {"id": f"{rid}-goal",
                     "objective": scenario.initial_objective if scenario.goal_note_id is not None else scenario.objective,
                     "type": scenario.goal_type,
                     "status": "undiscovered" if scenario.goal_note_id is not None else "active",
                     "revealed": scenario.goal_note_id is None},
            "config": self.config.model_dump(mode="json"),
            "agent_assignments": {slot: profile.id for slot, profile in profiles.items()},
            "skill_mode": scenario.skill_mode,
            "agent_profiles": {slot: profile.model_dump(mode="json") for slot, profile in profiles.items()},
            "dungeon_master": {"config": dungeon_master.model_dump(mode="json"), "model_calls": 0,
                               "last_review_action": 0},
            **({"village_minute": scenario.village.start_minute} if scenario.village else {}),
        }
        self.events = EventStore(directory, rid)
        self.memory = {"schema_version": "1.0", "recent": [], "discoveries": [], "plan": [], "subgoal": ""}
        self.memory["agent_profile"] = profile_context(profiles[actor_id])
        previous = load_memory(self.data, scenario.id)
        self.memory["previous_attempt_feedback"] = {
            "kind": "previous_experience_not_current_discoveries",
            "failures": ([failure.model_dump(mode="json") for failure in previous.failures[-12:]]
                         if self.run["goal"]["revealed"] else []),
        }
        self.observation, self.started_clock = None, None
        self.wait_until, self.wait_revision = None, None
        self.patterns, self.replan_count = [], 0
        state_path = self.data / "worlds" / world_id / "world-state.json"
        world_config = self.config.world.model_copy(update={"scenario": scenario.id})
        self.run["config"]["world"]["scenario"] = scenario.id
        if scenario.budgets is not None:
            self.run["config"]["budgets"] = scenario.budgets.model_dump()
        elif scenario.bakery:
            self.run["config"]["budgets"] = Budgets(
                max_actions=100, max_model_calls=120, max_seconds=600).model_dump()
        frozen_scenario = directory / "scenario.json"
        write_json(frozen_scenario, scenario.model_dump(mode="json"))
        self.world = WorldClient(world_config, self.root, frozen_scenario, state_path,
                                 world_id, actor_id, session_id)
        await self.world.connect()
        if scenario.goal_type not in self.world.manifest["goal_types"]:
            raise ValueError("World does not support the configured goal evaluator.")
        if self.world.manifest.get("admin_inspection") != "local_json":
            raise ValueError("Admin-enabled MVP requires a local JSON snapshot adapter.")
        self.observation = observation(await self.world.operation("observe", {}), session_id)
        if self.observation["world_id"] != world_id or self.observation["self"]["actor_id"] != actor_id:
            raise ValueError("World response does not match its configured world/actor binding.")
        if not state_path.exists():
            raise ValueError("World advertised local JSON admin inspection but did not supply its snapshot.")
        self.world.session_id = self.observation["session_id"]
        self.run["session_id"] = self.world.session_id
        self.run["world_id"] = self.observation["world_id"]
        self.run["actor_id"] = self.observation["self"]["actor_id"]
        if len(scenario.agents) > 1:
            self.run["exit_location"] = scenario.exit_location
            for agent in scenario.agents:
                self.agents[agent.id] = {
                    "session_id": self.world.manifest["actor_sessions"][agent.id],
                    "role": agent.role, "memory": copy.deepcopy(self.memory),
                    "observation": None, "patterns": [], "replan_count": 0,
                    "wait_until": None, "wait_revision": None, "goal": copy.deepcopy(self.run["goal"])}
                self.agents[agent.id]["memory"]["agent_profile"] = profile_context(profiles[agent.id])
            for agent in scenario.agents:
                self.run["actor_id"] = agent.id
                self.run["session_id"] = self.agents[agent.id]["session_id"]
                self.memory = self.agents[agent.id]["memory"]
                self.run["goal"] = self.agents[agent.id]["goal"]
                self.observation = observation(await self.world.select_actor(agent.id), self.run["session_id"])
                self.remember(self.observation)
                self.emit("observation", self.observation)
                self._save_agent()
            self.run["next_agent_index"] = 0
            await self._select_turn()
            self.run["next_agent_index"] = 0
        self.remember(self.observation)
        self.emit("started", {"goal": self.run["goal"].copy(), "tools": list(self.world.tools)})
        self.emit("observation", self.observation)
        self.checkpoint()

    def _activate_clock(self) -> None:
        if self.started_clock is None:
            self.started_clock = time.monotonic() - (self.run["elapsed_seconds"] if self.run else 0)

    def remember(self, value: dict[str, Any]) -> None:
        location = value["self"]["location_id"]
        self.memory.setdefault("visited_locations", {})[location] = value["location"]["description"]
        known = self.memory.setdefault("known_entities", {})
        for entity in value["visible_entities"]:
            known[entity["id"]] = {**entity, "last_seen_location": location}
            if entity["id"] not in self.memory["discoveries"]:
                self.memory["discoveries"].append(entity["id"])
        discovered = value.get("discovered_goal")
        if self.run and discovered is not None:
            source = known.get(discovered["source_id"])
            if source is None or not source.get("text"):
                raise ValueError("A discovered goal must be supported by a previously observed, read note")
            if discovered["type"] != self.run["goal"]["type"]:
                raise ValueError("The discovered goal uses an unsupported evaluator")
            if not self.run["goal"].get("revealed", True):
                self.run["goal"].update(objective=discovered["objective"], revealed=True, status="active")
                self.memory["plan"], self.memory["subgoal"] = [], ""
                previous = load_memory(self.data, self.run["scenario_id"])
                self.memory["previous_attempt_feedback"]["failures"] = [
                    failure.model_dump(mode="json") for failure in previous.failures[-12:]]
                self.emit("goal_discovered", {"goal": self.run["goal"].copy(), "source_id": discovered["source_id"]})
            elif discovered["objective"] != self.run["goal"]["objective"]:
                raise ValueError("The discovered goal conflicts with the assigned objective")

    def remaining(self) -> float:
        elapsed = time.monotonic() - self.started_clock if self.started_clock is not None else 0
        reserve = min(5, self.budgets.max_seconds * 0.1)
        return max(0, self.budgets.max_seconds - elapsed - reserve)

    async def verify(self, final: bool = False) -> bool:
        if not self.world or not self.run or not self.events:
            raise ValueError("No active world")
        elapsed = time.monotonic() - self.started_clock if self.started_clock is not None else 0
        deadline_remaining = self.budgets.max_seconds - elapsed
        if deadline_remaining <= 0:
            raise TimeoutError("No time remains for authoritative goal verification.")
        timeout = min(5, deadline_remaining) if final else min(15, max(0.1, self.remaining()))
        result = goal_result(await self.world.operation(
            "evaluate_goal", {"goal_type": self.run["goal"]["type"]}, timeout))
        self.run["verification"] = result
        self.emit("verification", result)
        if result["status"] == "achieved":
            stop = self.run.get("village_stop_minute")
            if stop is not None and self.run.get("village_minute", 0) < stop:
                return False
            reason = ("The world verified that this NPC reached the final exit."
                      if self.run["goal"]["type"] in {"actor_outside_start_location", "actor_at_exit_location"}
                      else "The world verified that this NPC achieved its goal.")
            self.finish("succeeded", reason)
            return True
        if result["status"] in {"blocked", "failed"}:
            self.dungeon_master_ruling("episode", "defeat", result.get("reason") or
                                       "The world verified that this objective is blocked.",
                                       [self.events.events[-1]["id"]], "world")
            self.finish("blocked", result.get("reason") or "The world reported the goal cannot currently be completed.")
            return True
        return False

    async def cycle(self) -> None:
        if self.in_flight:
            raise RuntimeError("A cycle is already executing")
        self.in_flight = True
        actions_before = self.run["actions"] if self.run else 0
        selected_turn = False
        try:
            if self.run and self.run.get("pending_dungeon_master_review"):
                await self.review_team_progress()
                if self.run["status"] in TERMINAL:
                    return
                self.run.pop("pending_dungeon_master_review")
            if not await self._select_turn():
                return
            selected_turn = True
            await self._cycle()
        except LLMUnavailable as error:
            LOGGER.warning("Pausing adventure: %s", error, exc_info=True)
            if not self.run:
                raise
            self.run["status"] = "paused"
            self.run["reason"] = f"{error} Adventure paused; resume when the LLM is available."
            if error.phase == "decision" and self.agents:
                self.run["next_agent_index"] = list(self.agents).index(self.run["actor_id"])
            elif error.phase == "dungeon_master":
                self.run["pending_dungeon_master_review"] = True
            await self._set_clock(False)
            self._pause_budget()
            self.emit("control", {"command": "pause", "source": "llm_unavailable",
                                  "reason": self.run["reason"]})
        except Exception as error:
            LOGGER.exception("NPC decision/action cycle failed")
            self.emit("error", {"kind": type(error).__name__, "message": self.safe_error(error)})
            self.finish("error", self.safe_error(error))
        finally:
            if (selected_turn and self.run and self.run["status"] == "budget_exceeded" and self.agents
                    and self.run["actions"] == actions_before):
                self.run["next_agent_index"] = list(self.agents).index(self.run["actor_id"])
                self.run["pending_actor_id"] = self.run["actor_id"]
            self.in_flight = False
            self.checkpoint()

    async def _call_proxy(self, request: Awaitable[ProxyResult],
                          phase: Literal["decision", "dungeon_master"] = "decision") -> ProxyResult:
        try:
            return await request
        except (httpx.RequestError, httpx.HTTPStatusError) as error:
            if (isinstance(error, httpx.HTTPStatusError)
                    and error.response.status_code not in {408, 429}
                    and error.response.status_code < 500):
                raise
            raise LLMUnavailable(self.safe_error(error), phase) from error

    async def _cycle(self) -> None:
        if not self.run or not self.world or not self.events:
            raise ValueError("No active episode")
        if self.remaining() <= 0 or self.run["actions"] >= self.budgets.max_actions:
            if not await self.verify(final=True):
                self.finish("budget_exceeded", "Episode action or wall-time budget exhausted.")
            return
        self.observation = observation(await self.world.operation(
            "observe", {}, min(15, max(0.1, self.remaining()))), self.world.session_id)
        clock = self.observation.get("clock")
        if (self.wait_until is not None and clock is not None and clock["seconds"] < self.wait_until
                and self.observation["observation_revision"] == self.wait_revision):
            await asyncio.sleep(0.25)
            return
        self.wait_until, self.wait_revision = None, None
        if clock is not None and clock.get("mode") == "decision_paused" and clock["running"]:
            await self._set_clock(False)
        self.run["cycles"] += 1
        self.emit("observation", self.observation)
        self.remember(self.observation)
        if await self.verify():
            return
        if not self.run["model"]:
            models = await self._call_proxy(self.proxy.models())
            if len(models) != 1:
                raise ValueError("Multiple models are available. Select one in the admin panel before running.")
            self.run["model"] = models[0]
        repair: str | None = None
        decision: Decision | None = None
        for attempt in range(2):
            if self.run["model_calls"] >= self.budgets.max_model_calls or self.remaining() <= 0:
                if not await self.verify(final=True):
                    self.finish("budget_exceeded", "Model-call or wall-time budget exhausted.")
                return
            messages = self.proxy.context(self.run["goal"], self.observation, self.memory, repair)
            self.emit("proxy_request", {"model": self.run["model"], "messages": messages, "repair": bool(repair)})
            self.run["model_calls"] += 1
            self.checkpoint()
            try:
                started = time.monotonic()
                decision, metadata = await self._call_proxy(self.proxy.choose(
                    self.run["model"], messages, f"{self.run['id']}:{self.run['cycles']}:{attempt}",
                    max(0.1, self.remaining())))
                self.emit("decision", {**decision.model_dump(mode="json"), **metadata,
                                       "latency_ms": round((time.monotonic() - started) * 1000)})
                descriptor = next((a for a in self.observation["actions"] if a["id"] == decision.action_id), None)
                if descriptor is None:
                    raise ValueError("Select an action from the current advertised action IDs.")
                if (self.world.manifest.get("village_full_rules") and descriptor["availability"] == "blocked"
                        and decision.action_id in {"village_work", "village_cook_fish", "village_grow_hay",
                                                  "village_gather_herbs", "village_fell_logs"}):
                    raise ValueError(f"Do not attempt unavailable work: {descriptor['reason']}. Prefer a clear wait or resolve the prerequisite.")
                validate(decision.arguments, descriptor["arguments_schema"])
                self.emit("validation", {"valid": True, "action_id": decision.action_id})
                break
            except (ValidationError, SchemaError, ValueError, KeyError, IndexError) as error:
                repair = self.safe_error(error)
                self.emit("validation", {"valid": False, "error": repair, "attempt": attempt + 1})
                self.dungeon_master_ruling("action", "rejected", repair, [self.events.events[-1]["id"]], "validation")
                if attempt == 1:
                    raise ValueError(f"Decision remained invalid after one repair: {repair}") from error
        if decision is None:
            raise ValueError("No valid model decision")
        if self.remaining() <= 0:
            if not await self.verify(final=True):
                self.finish("budget_exceeded", "Wall-time budget exhausted before action dispatch.")
            return
        request = {"request_id": uuid4().hex,
                   "expected_observation_revision": self.observation["observation_revision"],
                   "action_id": decision.action_id, "arguments": decision.arguments}
        self.emit("action_request", request)
        goal_was_revealed = self.run["goal"].get("revealed", True)
        self.run["actions"] += 1
        self.checkpoint()
        result = None
        for attempt in range(3):
            try:
                result = action_result(await self.world.operation(
                    "act", request, min(15, max(0.1, self.remaining()))),
                    request["request_id"], self.world.session_id)
                break
            except TimeoutError:
                if attempt == 2 or not self.world.manifest.get("idempotent_actions") or self.remaining() <= 0:
                    raise
                self.emit("retry", {"request_id": request["request_id"], "attempt": attempt + 1})
        if result is None:
            raise ValueError("World returned no action result")
        self.emit("action_result", result)
        self.dungeon_master_ruling(
            "action", "rejected" if result["outcome"] == "REJECTED" else "allowed",
            result["explanation"], [self.events.events[-1]["id"]], "world")
        self.observation = result["observation"]
        self.remember(self.observation)
        if result["code"] == "WAITING" and self.observation.get("clock") is not None:
            now = self.observation["clock"]["seconds"]
            wakeups = [now + decision.arguments["seconds"]]
            for batch in self.observation["bread_batches"].values():
                wakeups.extend(value for value in (batch["ready_at"], batch["deadline"])
                               if value is not None and value > now and batch["stage"] not in {"served", "ruined"})
            self.wait_until = min(wakeups)
            self.wait_revision = self.observation["observation_revision"]
        self.memory["recent"] = (self.memory["recent"] + [{
            "action_id": decision.action_id, "arguments": decision.arguments,
            "outcome": result["outcome"], "code": result["code"],
            "explanation": result["explanation"],
        }])[-12:]
        if goal_was_revealed or not self.run["goal"].get("revealed", True):
            self.memory["plan"], self.memory["subgoal"] = decision.plan, decision.subgoal
        if await self.verify(final=self.remaining() <= 0):
            return
        await self.review_team_progress()
        if self.run["status"] in TERMINAL:
            return
        pattern = json.dumps([decision.action_id, decision.arguments, result["outcome"], result["code"]], sort_keys=True)
        if result["outcome"] == "APPLIED" or result["code"] in {"WAITING", "STALE_OBSERVATION"}:
            self.patterns = []
        else:
            self.patterns.append(pattern)
        if Counter(self.patterns)[pattern] >= self.budgets.repetition_window:
            self.patterns = []
            self.replan_count += 1
            self.memory["replan_warning"] = "Repeated non-progress. Choose another way to advance or learn."
            self.emit("replan", {"reason": self.memory["replan_warning"]})
            if self.replan_count >= 2:
                self.finish("stalled", "Two repeated non-progress windows exhausted.")
        if self.run["status"] not in TERMINAL and (
                self.remaining() <= 0 or self.run["actions"] >= self.budgets.max_actions):
            self.finish("budget_exceeded", "Episode budget exhausted without verified completion.")
        if self.run["status"] == "running" and self.wait_until is not None and not self._continuous_clock():
            await self._set_clock(True)

    def dungeon_master_ruling(self, scope: str, verdict: str, reason: str,
                              evidence_ids: list[str], source: str) -> None:
        if not self.run or not self.run.get("dungeon_master"):
            return
        config = DungeonMasterConfig.model_validate(self.run["dungeon_master"]["config"])
        if not config.enabled or (scope == "action" and "adjudicate_actions" not in config.capabilities):
            return
        self.emit("dungeon_master_ruling", {
            "name": config.name, "scope": scope, "verdict": verdict,
            "reason": reason, "evidence_ids": evidence_ids, "source": source})

    async def review_team_progress(self) -> None:
        if not self.run or not self.events or not self.run.get("dungeon_master"):
            return
        supervisor = self.run["dungeon_master"]
        config = DungeonMasterConfig.model_validate(supervisor["config"])
        if not config.enabled or "review_progress" not in config.capabilities:
            return
        interval = max(1, len(self.agents))
        if (self.run["actions"] - supervisor["last_review_action"] < interval
                or not self.run["goal"].get("revealed", True)
                or any(not agent["goal"].get("revealed", True) for agent in self.agents.values())):
            return
        self._save_agent()
        evidence = [event for event in self.events.events
                    if event["kind"] in {"observation", "action_result", "verification"}][-REVIEW_EVENTS:]
        team = ({actor_id: {"observation": agent["observation"], "role": agent["role"]}
                 for actor_id, agent in self.agents.items()} if self.agents else {
                     self.run["actor_id"]: {"observation": self.observation}})
        payload = {"config": config.model_dump(mode="json"), "goal": self.run["goal"],
                   "team": team, "evidence": evidence}
        repair = None
        review: DungeonMasterReview | None = None
        metadata: dict[str, Any] = {}
        for attempt in range(2):
            if self.remaining() <= 0:
                self.run["pending_dungeon_master_review"] = True
                self.finish("budget_exceeded", "Episode wall-time budget exhausted before Dungeon Master review.")
                return
            messages = self.proxy.dungeon_master_context(payload, repair)
            self.emit("dungeon_master_request", {"model": self.run["model"], "messages": messages})
            supervisor["model_calls"] += 1
            self.checkpoint()
            try:
                review, metadata = await self._call_proxy(self.proxy.review_progress(
                    self.run["model"], messages, f"{self.run['id']}:dungeon-master:{self.run['actions']}:{attempt}",
                    max(0.1, self.remaining())), "dungeon_master")
                validate_review(review, evidence, config)
                break
            except (ValidationError, ValueError) as error:
                repair = self.safe_error(error)
                self.emit("dungeon_master_validation", {"valid": False, "error": repair, "attempt": attempt + 1})
                if attempt == 1:
                    raise ValueError(f"Dungeon Master ruling remained invalid after one repair: {repair}") from error
        if review is None:
            raise ValueError("Dungeon Master returned no valid progress ruling")
        supervisor["last_review_action"] = self.run["actions"]
        self.emit("dungeon_master_review", {**review.model_dump(mode="json"), **metadata})
        if review.verdict == "defeat" and await self.verify(final=self.remaining() <= 0):
            return
        reason = " ".join(finding.reason for finding in review.findings)
        self.dungeon_master_ruling("episode", review.verdict, reason,
                                  list(dict.fromkeys(eid for finding in review.findings for eid in finding.evidence_ids)), "ai")
        if review.verdict == "defeat":
            self.finish("blocked", f"{config.name} rules that the team must admit defeat. {reason}")

    def safe_error(self, error: Exception) -> str:
        if isinstance(error, httpx.HTTPStatusError):
            return f"LLM proxy returned HTTP {error.response.status_code}; no direct-provider fallback."
        if isinstance(error, httpx.RequestError):
            return f"LLM proxy at {self.config.proxy.base_url} is unreachable or timed out ({type(error).__name__})."
        text = str(error)
        if self.config.proxy.token_environment:
            import os
            token = os.environ.get(self.config.proxy.token_environment)
            if token:
                text = text.replace(token, "[REDACTED]")
        return text[:2000] or type(error).__name__

    def emit(self, kind: str, payload: Any, actor_id: str | None = None) -> None:
        if self.events and self.run:
            actor_id = actor_id or self.run["actor_id"]
            session_id = self.agents.get(actor_id, {}).get("session_id", self.run["session_id"])
            metadata = {}
            if self.run["scenario_id"] == "millhaven-expanded":
                observed = payload.get("observation") if isinstance(payload, dict) else None
                observed = (payload if kind == "observation" else observed) or self.observation or {}
                if "village" in observed:
                    self.run["village_minute"] = observed["village"]["minute"]
                if "village_minute" in self.run:
                    metadata = {"village_minute": self.run["village_minute"],
                                "actor_name": self.run["agent_profiles"][actor_id]["name"]}
                    if self.world and self.world.manifest.get("village_full_rules"):
                        if kind == "action_request":
                            metadata["free_action"] = payload["action_id"] in {"look", "read"}
                        elif kind == "action_result":
                            request = next((event["payload"] for event in reversed(self.events.events)
                                            if event["kind"] == "action_request" and event["actor_id"] == actor_id
                                            and event["payload"]["request_id"] == payload["request_id"]), None)
                            if request is not None:
                                metadata["free_action"] = request["action_id"] in {"look", "read"}
            self.events.append({"kind": kind, "timestamp": utc_now(), **metadata,
                                "cycle": self.run["cycles"], "session_id": session_id,
                                "actor_id": actor_id, "payload": payload})

    def finish(self, status: str, reason: str) -> None:
        if not self.run:
            return
        self._write_clock(False)
        if self.started_clock is not None:
            self.run["elapsed_seconds"] = round(time.monotonic() - self.started_clock, 3)
            self.started_clock = None
        self.run["status"], self.run["reason"] = status, reason
        self.run["completion_analysis"] = CompletionAnalysis().model_dump(mode="json")
        self.run["successful"] = None
        if status == "succeeded":
            self.run["goal"]["status"] = "achieved"
        elif status in {"blocked", "stalled", "error", "budget_exceeded"}:
            self.run["goal"]["status"] = "blocked" if status in {"blocked", "stalled"} else "failed"
        self.emit("finished", {"status": status, "reason": reason})
        self.checkpoint()
        self._summary()

    async def _connect_saved_world(self, *, require_frozen: bool = False) -> None:
        if not self.run or not self.events:
            raise ValueError("No saved adventure is available.")
        state_path = self.data / "worlds" / self.run["world_id"] / "world-state.json"
        if not state_path.exists():
            raise ValueError("The saved world is missing.")
        scenario_path = self.events.directory / "scenario.json"
        if not scenario_path.exists():
            if require_frozen:
                raise ValueError("The frozen scenario is missing; this adventure cannot safely continue.")
            scenario_path = self.data / "scenarios" / f"{self.run['scenario_id']}.json"
            self.emit("assessment_warning", {
                "reason": "Legacy attempt has no frozen scenario; its existing final world will be checked against the current scenario definition."})
        world_config = AppConfig.model_validate(self.run["config"]).world
        world = WorldClient(world_config, self.root, scenario_path, state_path,
                            self.run["world_id"], self.run["actor_id"], self.run["session_id"])
        try:
            await world.connect()
        except Exception:
            await world.close()
            raise
        self.world = world

    async def assess_completion(self) -> None:
        if not self.run or not self.events or self.run["status"] not in TERMINAL:
            raise ValueError("Only ended attempts can be assessed")
        analysis = CompletionAnalysis(status="running")
        self.run["completion_analysis"] = analysis.model_dump(mode="json")
        self.run["successful"] = None
        self.in_flight = True
        started = time.monotonic()
        self.emit("assessment_started", {"status": "running"})
        self.checkpoint()
        try:
            if self.world is None:
                await self._connect_saved_world()
            final_observation = observation(await self.world.operation("observe", {}, 5), self.world.session_id)
            self.observation = final_observation
            self.remember(final_observation)
            self.emit("observation", final_observation)
            verification = goal_result(await self.world.operation(
                "evaluate_goal", {"goal_type": self.run["goal"]["type"]}, 5))
            self.emit("completion_verification", verification)
            verification_id = self.events.events[-1]["id"]
            analysis.final_verification = verification
            if self.agents:
                analysis.story = build_narrative(self.events.events)
            else:
                analysis.story = build_narrative(self.events.events, self.run["actor_id"])
            model = self.config.proxy.model or self.run["model"]
            if not model:
                models = await self.proxy.models()
                if len(models) != 1:
                    raise ValueError("Select a model before reviewing the completed story.")
                model = models[0]
            analysis.model = model
            messages = self.proxy.completion_context({
                "objective": self.run["goal"]["objective"],
                "attempt_outcome": {"status": self.run["status"], "reason": self.run["reason"]},
                "story": [paragraph.model_dump() for paragraph in analysis.story],
                "final_observation": {key: final_observation[key] for key in ("self", "location", "visible_entities")},
                "final_verification": {"event_id": verification_id, "result": verification},
            })
            self.emit("assessment_request", {"model": model, "messages": messages})
            analysis.model_calls = 1
            self.run["completion_analysis"] = analysis.model_dump(mode="json")
            self.checkpoint()
            review, metadata = await self.proxy.assess(
                model, messages, f"{self.run['id']}:assessment:{verification_id}", self.config.proxy.timeout_seconds)
            analysis.story_review = review
            analysis.verdict, analysis.findings = grade(review, verification, verification_id, analysis.story)
            analysis.status = "completed"
            analysis.completed_at = utc_now()
            record_feedback(self.data, self.run["scenario_id"], self.run["id"], self.run["goal"]["objective"], analysis)
            self.memory["completion_feedback"] = {
                "verdict": analysis.verdict, "findings": [finding.model_dump() for finding in analysis.findings],
                "source_run_id": self.run["id"],
            }
            self.run["successful"] = analysis.verdict == "pass"
            self.emit("assessment_result", {**analysis.model_dump(mode="json"), **metadata})
        except Exception as error:
            LOGGER.exception("Completion assessment failed")
            analysis.status = "error"
            analysis.verdict = None
            analysis.error = self.safe_error(error)
            self.emit("assessment_error", {"message": analysis.error})
        finally:
            analysis.elapsed_seconds = round(time.monotonic() - started, 3)
            self.run["completion_analysis"] = analysis.model_dump(mode="json")
            self.in_flight = False
            self.checkpoint()
            self._summary()

    def _summary(self) -> None:
        if self.run and self.events:
            write_json(self.events.directory / "summary.json", self.run)

    def checkpoint(self) -> None:
        if self.run and self.events:
            snapshot = self.status()["world"]
            if snapshot is not None:
                announced = {event["payload"]["goal_id"] for event in self.events.events
                             if event["kind"] == "secondary_goal_achieved"}
                for gid, goal in snapshot["secondary_goals"].items():
                    if goal["status"] == "achieved" and gid not in announced:
                        self.emit("secondary_goal_achieved", {
                            "goal_id": gid, "title": goal["title"],
                            "reason": "The world verified every completion condition.",
                            "conditions": goal["conditions"], "condition_met": goal["condition_met"],
                            "verification_revision": goal["completed_revision"]})
            self._save_agent()
            if not self.agents:
                self.run["turn_state"] = {
                    "patterns": self.patterns, "replan_count": self.replan_count,
                    "wait_until": self.wait_until, "wait_revision": self.wait_revision,
                }
            if self.agents:
                self.run["agents"] = self.agents
            if self.started_clock is not None and self.run["status"] not in TERMINAL:
                self.run["elapsed_seconds"] = round(time.monotonic() - self.started_clock, 3)
            self.run["observation"] = self.observation
            write_json(self.events.directory / "run.json", self.run)
            write_json(self.events.directory / "goal.json", {
                "schema_version": "1.0", "run_id": self.run["id"], **self.run["goal"]})
            write_json(self.events.directory / "npc-memory.json", self.memory)

    def status(self) -> dict[str, Any]:
        snapshot = None
        if self.run:
            path = self.data / "worlds" / self.run["world_id"] / "world-state.json"
            if path.exists():
                stat = path.stat()
                key = (path, stat.st_mtime_ns, stat.st_size)
                if key != self._snapshot_key:
                    saved = read_json(path)
                    saved.pop("requests", None)
                    saved.pop("admin_requests", None)
                    self._world_snapshot = WorldState.model_validate(saved).model_dump(
                        mode="json", exclude={"requests", "admin_requests"})
                    self._snapshot_key = key
                snapshot = copy.deepcopy(self._world_snapshot)
        return {"run": self.run, "observation": self.observation,
                "world": snapshot, "memory": self.memory, "in_flight": self.in_flight,
                "event_count": len(self.events.events) if self.events else 0,
                "config": self.config.model_dump(mode="json")}

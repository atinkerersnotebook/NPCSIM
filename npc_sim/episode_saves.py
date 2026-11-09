import copy
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

from npc_sim.assessment import CompletionAnalysis
from npc_sim.contracts import observation
from npc_sim.models import AppConfig, EpisodeSave, EpisodeSaveRequest
from npc_sim.storage import read_json, write_json
from npc_sim.world_storage import WorldStorage


ACTIVE = {"ready", "running", "paused"}


def validate_save(save: EpisodeSave) -> None:
    if save.world.request_history_id is not None:
        raise ValueError("Episode saves must include their retry history, not a local database reference.")
    run = save.run
    required = {"id", "world_id", "actor_id", "session_id", "scenario_id", "title", "status",
                "created_at", "actions", "model_calls", "cycles", "elapsed_seconds", "goal",
                "config", "observation", "model"}
    if not required <= run.keys():
        raise ValueError("The saved episode is missing its controller state.")
    EpisodeSaveRequest(run_id=run["id"], name=save.name)
    if run.get("schema_version") != "1.0":
        raise ValueError("Unsupported saved episode version.")
    if run["status"] not in ACTIVE | {"succeeded", "blocked", "stalled", "budget_exceeded", "cancelled", "error"}:
        raise ValueError("Invalid saved episode status.")
    for field in ("actions", "model_calls", "cycles"):
        if type(run[field]) is not int or run[field] < 0:
            raise ValueError(f"Invalid saved episode counter: {field}.")
    elapsed = run["elapsed_seconds"]
    if type(elapsed) not in {int, float} or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("Invalid saved episode elapsed time.")
    goal = run["goal"]
    if (not isinstance(goal, dict) or not {"id", "objective", "type", "status"} <= goal.keys()
            or goal["type"] != save.scenario.goal_type):
        raise ValueError("The saved objective does not match its scenario.")
    config = AppConfig.model_validate(run["config"])
    if config.world.transport != "stdio" or config.world.command:
        raise ValueError("Episode saves require the built-in local world.")
    if (run["world_id"] != save.world.world_id or run["scenario_id"] != save.scenario.id
            or save.world.scenario_id != save.scenario.id):
        raise ValueError("The saved episode, world and frozen scenario do not match.")
    if save.world.clock and save.world.clock.running:
        raise ValueError("Save points must have a paused simulation clock.")
    actor = save.world.actors.get(run["actor_id"])
    if actor is None or actor.session_id != run["session_id"]:
        raise ValueError("The saved active NPC binding does not match its world.")
    sessions = [actor.session_id for actor in save.world.actors.values()]
    if any(len(identity) != 32 or any(char not in "0123456789abcdef" for char in identity)
           for identity in [save.world.world_id, *sessions]) or len(sessions) != len(set(sessions)):
        raise ValueError("Saved world/NPC session identities are invalid or duplicated.")
    observation(run["observation"], run["session_id"])
    if run["observation"]["world_id"] != run["world_id"]:
        raise ValueError("The saved observation belongs to a different world.")
    for actor_id, agent in run.get("agents", {}).items():
        actor = save.world.actors.get(actor_id)
        if actor is None or actor.session_id != agent["session_id"]:
            raise ValueError("A saved NPC binding does not match its world.")
        if agent.get("observation") is not None:
            observation(agent["observation"], agent["session_id"])
    results = set()
    requests = set()
    for sequence, event in enumerate(save.events, 1):
        if (event.get("schema_version") != "1.0" or event.get("run_id") != run["id"]
                or event.get("sequence") != sequence or event.get("id") != f"{run['id']}:{sequence}"):
            raise ValueError("The saved transcript is incomplete or belongs to another episode.")
        if not isinstance(event.get("payload"), dict) or not isinstance(event.get("kind"), str):
            raise ValueError("The saved transcript contains an invalid event.")
        if event["kind"] == "action_request":
            requests.add(event["payload"]["request_id"])
        elif event["kind"] == "action_result":
            results.add(event["payload"]["request_id"])
    if save.resumable and (run["status"] not in ACTIVE or requests - results):
        raise ValueError("This save has no confirmed safe boundary for resuming.")


def summary(save: EpisodeSave) -> dict[str, Any]:
    return {
        "id": save.id, "name": save.name, "saved_at": save.saved_at,
        "source_run_id": save.run["id"], "title": save.run["title"],
        "scenario_id": save.run["scenario_id"], "status": save.run["status"],
        "actions": save.run["actions"], "model_calls": save.run["model_calls"],
        "resumable": save.resumable,
        "village_minute": save.world.village.minute if save.world.village else None,
        "simulation_seconds": save.world.clock.seconds if save.world.clock else None,
    }


def write_save(data: Path, save: EpisodeSave) -> dict[str, Any]:
    validate_save(save)
    path = data / "saves" / f"{save.id}.json"
    if path.exists():
        raise ValueError("This save point already exists.")
    write_json(path, save.model_dump(mode="json"))
    return summary(save)


def read_save(data: Path, save_id: str) -> EpisodeSave:
    if len(save_id) != 32 or any(char not in "0123456789abcdef" for char in save_id):
        raise ValueError("Invalid save point ID.")
    path = data / "saves" / f"{save_id}.json"
    if not path.exists():
        raise ValueError("Save point not found.")
    save = EpisodeSave.model_validate(read_json(path))
    if save.id != save_id:
        raise ValueError("The save point identity does not match its file.")
    validate_save(save)
    return save


def list_saves(data: Path) -> list[dict[str, Any]]:
    saves = [summary(read_save(data, path.stem)) for path in (data / "saves").glob("*.json")]
    return sorted(saves, key=lambda item: item["saved_at"], reverse=True)


def _remap(value: Any, identities: dict[str, str]) -> Any:
    if isinstance(value, str):
        for original, replacement in identities.items():
            value = value.replace(original, replacement)
        return value
    if isinstance(value, list):
        return [_remap(item, identities) for item in value]
    if isinstance(value, dict):
        return {_remap(key, identities): _remap(item, identities) for key, item in value.items()}
    return value


def branch(save: EpisodeSave, created_at: str) -> dict[str, Any]:
    identities = {save.run["id"]: uuid4().hex, save.world.world_id: uuid4().hex}
    identities.update({actor.session_id: uuid4().hex for actor in save.world.actors.values()})
    result = _remap(copy.deepcopy(save.model_dump(mode="json")), identities)
    run = result["run"]
    run["created_at"] = created_at
    run["snapshot_questions"] = True
    run["safe_checkpoint"] = save.resumable
    run["loaded_from_save"] = {
        "id": save.id, "name": save.name, "saved_at": save.saved_at,
        "source_run_id": save.run["id"], "source_world_id": save.world.world_id,
    }
    if save.resumable:
        run.update(status="paused", reason="Save point loaded. Resume or ask the NPCs questions.")
        run.pop("completion_analysis", None)
        run["successful"] = None
    elif run["status"] in ACTIVE:
        run.update(status="error", reason="Loaded for questions only: the saved action boundary is uncertain.")
    if not save.resumable and run.get("completion_analysis", {}).get("status") in {None, "pending", "running"}:
        run["completion_analysis"] = CompletionAnalysis(
            status="error", error="No completed review was captured in this save point."
        ).model_dump(mode="json")
    return result


def stage_branch(data: Path, copied: dict[str, Any]) -> tuple[Path, Path]:
    run = copied["run"]
    directory = data / "runs" / run["id"]
    world_directory = data / "worlds" / run["world_id"]
    if directory.exists() or world_directory.exists():
        raise ValueError("The restored episode identity already exists; no files were changed.")
    write_json(directory / "scenario.json", copied["scenario"])
    storage = WorldStorage(world_directory / "world-state.json")
    write_json(storage.path, copied["world"])
    storage.save(storage.load())
    for event in copied["events"]:
        write_json(directory / "events" / f"{event['sequence']:06}.json", event)
    return directory, world_directory

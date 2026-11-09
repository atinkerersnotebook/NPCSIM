import argparse
import copy
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen
from uuid import uuid4

from npc_sim import episode_saves, village, village_economy, village_events, village_extensions, village_social, village_turns, village_maintenance, village_research, village_mining, village_livestock
from npc_sim.living_village import build_scenario
from npc_sim.models import EpisodeSave, Scenario, VillageDayPolicy, VillageEventsConfig, VillageTimeUse, WorldState
from npc_sim.storage import EventStore, read_json, write_json
from npc_sim.world import World
from npc_sim.world_storage import WorldStorage


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def capture_midnight(data: Path, live: dict[str, Any]) -> tuple[EpisodeSave, Path]:
    run = copy.deepcopy(live["run"])
    observed = live["observation"]
    if run["status"] != "paused" or run["goal"]["type"] != "village_restored" or live.get("in_flight"):
        raise ValueError("Pause the village at its midnight boundary before capturing it.")
    minute = observed["village"]["minute"]
    if minute <= 0 or minute % 1440:
        raise ValueError("This migration requires an observed midnight boundary.")
    directory = data / "runs" / run["id"]
    source_world = data / "worlds" / run["world_id"] / "world-state.json"
    scenario = Scenario.model_validate(read_json(directory / "scenario.json"))
    storage = WorldStorage(source_world)
    checkpoint = storage.load()
    state = storage.export(checkpoint)
    if state.village is None or not 0 <= minute - state.village.minute <= 60:
        raise ValueError("The saved village clock cannot be safely reconciled with the observation.")
    backup = data / "backups" / f"millhaven-upgrade-{uuid4().hex}"
    shutil.copytree(directory, backup / "original-run")
    shutil.copy2(source_world, backup / "original-world-state.json")
    storage.backup_history(checkpoint, backup / "original-world-state.json")
    write_json(backup / "live-observation.json", observed)
    reconciled_path = backup / "reconciled-world-state.json"
    write_json(reconciled_path, state.model_dump(mode="json"))
    world = World(scenario, reconciled_path, run["world_id"], run["actor_id"], run["session_id"])
    if state.village.minute < minute:
        if village_turns.next_actor(world, stop_at_minute=minute) is not None:
            raise ValueError("Reconciliation encountered a runnable NPC; no turn may be invented or skipped.")
    actual = world.observe()
    if actual["village"]["minute"] != minute:
        raise ValueError("The copied clock did not reach the confirmed boundary.")
    for key in ("location_id", "inventory"):
        if actual["self"][key] != observed["self"][key]:
            raise ValueError(f"Midnight reconciliation disagrees with the observed NPC {key}.")
    for key in ("stocks", "coins", "resource_inventory"):
        if actual["village"][key] != observed["village"][key]:
            raise ValueError(f"Midnight reconciliation disagrees with observed {key}.")
    for key in ("sleeping", "sleep_until", "missed_meals"):
        if actual["village"]["personal"]["life"][key] != observed["village"]["personal"]["life"][key]:
            raise ValueError(f"Midnight reconciliation disagrees with observed {key}.")
    run["observation"] = actual
    run["village_minute"] = minute
    if run.get("agents"):
        run["agents"][run["actor_id"]]["observation"] = actual
    events = [read_json(path) for path in sorted((directory / "events").glob("*.json"))]
    saved = EpisodeSave(
        id=uuid4().hex, name="Millhaven original midnight - before Living upgrade", saved_at=now(),
        resumable=True, run=run, memory=copy.deepcopy(live["memory"]), scenario=scenario,
        world=world.export_state(), events=events,
    )
    episode_saves.write_save(data, saved)
    return saved, backup


def upgrade_midnight(saved: EpisodeSave, *, livestock: bool = False) -> EpisodeSave:
    episode_saves.validate_save(saved)
    state = saved.world.model_copy(deep=True)
    if (not saved.resumable or state.village is None or state.village.minute <= 0
            or state.village.minute % 1440):
        raise ValueError("Upgrade a resumable village save at midnight.")
    if any(value is not None for value in (
            state.village.economy, state.village.social, state.village.incidents, state.village.maintenance, state.village.research, state.village.mining, state.village.livestock)):
        raise ValueError("This village already has living systems; do not reinitialize their progress.")
    minute = state.village.minute
    scenario = build_scenario(saved.scenario, livestock=livestock)
    for identifier in village_social.example_fixtures():
        if identifier in state.entities:
            scenario.entities[identifier] = state.entities[identifier].model_copy(deep=True)
        else:
            state.entities[identifier] = scenario.entities[identifier].model_copy(deep=True)
    if scenario.village is None or scenario.village.social is None:
        raise ValueError("The living scenario did not supply its social configuration.")
    for quest in scenario.village.social.quests.values():
        quest.offer_at += minute
        if quest.expires_at is not None:
            quest.expires_at += minute
    events = village_events.recommended_config(scenario, initial_example=False)
    for template in events["templates"]:
        if template["id"] in {"visiting-merchant", "passing-traveller"}:
            template["random"] = False
            template["scheduled_at"] = minute + (570 if template["id"] == "visiting-merchant" else 780)
    scenario.village.events = VillageEventsConfig.model_validate(events)
    scenario = Scenario.model_validate(scenario.model_dump(mode="json"))
    config = scenario.village
    if (config is None or config.economy is None or config.events is None
            or config.social is None or config.maintenance is None or config.research is None or config.mining is None):
        raise ValueError("The living scenario is missing an enabled system.")
    state.scenario_id = scenario.id
    with tempfile.TemporaryDirectory(prefix="npcsim-upgrade-") as temporary:
        path = Path(temporary) / "world.json"
        write_json(path, state.model_dump(mode="json"))
        world = World(scenario, path, state.world_id, saved.run["actor_id"], saved.run["session_id"])
        village_economy.configure(world, config.economy.model_dump(mode="json"))
        village_events.configure(world, config.events.model_dump(mode="json"))
        village_social.configure(world, config.social.model_dump(mode="json"))
        village_maintenance.configure(world, config.maintenance.model_dump(mode="json"))
        village_research.configure(world, config.research.model_dump(mode="json"))
        village_mining.configure(world, config.mining.model_dump(mode="json"))
        if livestock and config.livestock:
            village_livestock.configure(world, config.livestock.model_dump(mode="json"))
        upgraded = world.state.village
        if upgraded is None or upgraded.social is None or upgraded.incidents is None:
            raise ValueError("Living system initialization was incomplete.")
        upgraded.day_policy = VillageDayPolicy.model_validate(
            config.model_dump(include=set(VillageDayPolicy.model_fields)))
        upgraded.incidents.daily_targets[str(minute // 1440)] = 0
        village.event(world, "dungeon-master", "village_upgraded", "village",
                      "Living village systems begin today; prior work, goods, money and memories are retained.")
        world.state.revision += 1
        upgraded.social.processed_event_count = len(upgraded.events)
        for actor_id in upgraded.residents:
            upgraded.time_use.setdefault(actor_id, VillageTimeUse(since=minute))
        village_extensions.refresh(world)
        run = copy.deepcopy(saved.run)
        run["scenario_id"], run["title"] = scenario.id, scenario.title
        run["config"]["world"]["scenario"] = scenario.id
        run["goal"].update(objective=scenario.objective, status="active")
        run["village_stop_minute"] = minute + 1440
        run.pop("verification", None)
        run.pop("completion_analysis", None)
        run["successful"] = None
        run["reason"] = "Living village upgrade prepared at midnight. Continue when ready."
        for agent in scenario.agents:
            bound = world.bind(agent.id, state.actors[agent.id].session_id)
            if run.get("agents"):
                run["agents"][agent.id]["role"] = agent.role
                run["agents"][agent.id]["goal"].update(objective=scenario.objective, status="active")
                run["agents"][agent.id]["observation"] = bound.observe()
        run["observation"] = world.observe()
        result = EpisodeSave(
            id=uuid4().hex, name=f"Millhaven Living - Day {minute // 1440 + 1} upgraded copy",
            saved_at=now(), resumable=True, run=run, memory=copy.deepcopy(saved.memory),
            scenario=scenario, world=world.export_state(), events=copy.deepcopy(saved.events),
        )
    episode_saves.validate_save(result)
    return result


def publish_copy(data: Path, saved: EpisodeSave) -> dict[str, Any]:
    copied = episode_saves.branch(saved, now())
    run = copied["run"]
    directory, _ = episode_saves.stage_branch(data, copied)
    events = EventStore(directory, run["id"])
    events.append({
        "session_id": run["session_id"], "actor_id": run["actor_id"], "kind": "episode_loaded",
        "cycle": run["cycles"], "timestamp": now(), "village_minute": run["village_minute"],
        "payload": run["loaded_from_save"],
    })
    write_json(directory / "npc-memory.json", copied["memory"])
    write_json(directory / "goal.json", {"schema_version": "1.0", "run_id": run["id"], **run["goal"]})
    write_json(directory / "summary.json", run)
    write_json(directory / "run.json", run)
    return {"run_id": run["id"], "world_id": run["world_id"], "save_id": saved.id,
            "minute": run["village_minute"], "status": run["status"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up and prepare a paused Living Millhaven copy; never run a model.")
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    parser.add_argument("--livestock", action="store_true", help="Opt into physical cow/hen installation on the upgraded copy")
    args = parser.parse_args()
    with urlopen("http://127.0.0.1:8000/api/state", timeout=30) as response:
        live = json.load(response)
    original, backup = capture_midnight(args.data, live)
    upgraded = upgrade_midnight(original, livestock=args.livestock)
    episode_saves.write_save(args.data, upgraded)
    result = publish_copy(args.data, upgraded)
    print(json.dumps({**result, "original_save_id": original.id, "backup": str(backup)}))


if __name__ == "__main__":
    main()

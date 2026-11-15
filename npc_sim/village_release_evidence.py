"""Offline, reproducible village baseline and failure-state evidence capture."""

import argparse
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from npc_sim import village_economy, village_life
from npc_sim.models import Scenario
from npc_sim.storage import read_json, write_json
from npc_sim.world import World


def capture(world: World, diagnostic: dict[str, Any] | None = None) -> dict[str, Any]:
    state = world.state.village
    if state is None:
        raise ValueError("Village evidence requires a village world")
    day = state.minute // 1440
    return {
        "minute": state.minute,
        "day": day,
        "repairs": {"well": state.well_dug, "mill": state.repaired,
                    "wagon": state.wagon.repaired if state.wagon else None},
        "research": ({"projects": {key: project.model_dump(mode="json")
                                    for key, project in state.research.projects.items()},
                      "known": {rid: products.copy() for rid, products in state.research.known.items()}}
                     if state.research else None),
        "lessons": ({rid: [lesson.model_dump(mode="json") for lesson in entries]
                     for rid, entries in state.legacy.lessons.items()} if state.legacy else None),
        "livestock_care": ({name: getattr(state.livestock, name).model_dump(mode="json")
                            for name in ("cow", "hens")} if state.livestock else None),
        "stocks": {site: goods.copy() for site, goods in state.stocks.items()},
        "stock_lots": {site: {good: [lot.model_dump(mode="json") for lot in lots]
                              for good, lots in goods.items()} for site, goods in state.stock_lots.items()},
        "coins": state.coins.copy(),
        "orders": {key: order.model_dump(mode="json") for key, order in state.economy.orders.items()}
                  if state.economy else {},
        "meals": {rid: {key: meal.model_dump(mode="json") for key, meal in person.life.meals.items()}
                  for rid, person in state.residents.items() if person.life},
        "meal_reservations": {kind: village_life.meal_plan(world, kind, day)
                              for kind in ("breakfast", "daily")},
        "meal_supply_forecast": village_economy.meal_supply_forecast(world),
        "meal_readiness": village_economy.meal_readiness(world),
        "resident_locations": {rid: world.state.actors[rid].location_id
                               for rid in state.residents if rid in world.state.actors},
        "recent_actions": [event.model_dump(mode="json") for event in state.events[-30:]],
        "crew_sessions": {key: session.model_dump(mode="json")
                          for key, session in state.crew_sessions.items()},
        "cargo": {rid: {"loose": person.cargo.copy(),
                         "shipments": [shipment.model_dump(mode="json") for shipment in person.shipments]}
                  for rid, person in state.residents.items()},
        "wagon": state.wagon.model_dump(mode="json") if state.wagon else None,
        "diagnostic": diagnostic,
    }


def create_baseline(scenario_path: Path, output: Path) -> dict[str, Any]:
    source = scenario_path.read_bytes()
    scenario = Scenario.model_validate(read_json(scenario_path))
    if scenario.village is None or not scenario.agents:
        raise ValueError("A village baseline requires a village scenario with agents")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite an existing baseline: {output}")
    output.mkdir(parents=True, exist_ok=True)
    first = scenario.agents[0].id
    world = World(scenario, output / "baseline.json", "g8-baseline", first, f"baseline-{first}")
    for agent in scenario.agents[1:]:
        world.bind(agent.id, f"baseline-{agent.id}")
    world.save()
    (output / "scenario.json").write_bytes(source)
    baseline = output / "baseline.json"
    manifest = {
        "scenario_sha256": hashlib.sha256(source).hexdigest(),
        "baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
        "scenario_id": scenario.id,
        "model_calls": 0,
        "model_settings": "none: deterministic initial engine state only",
        "starting_state": capture(world),
    }
    write_json(output / "evidence.json", manifest)
    return manifest


def capture_failure(baseline_dir: Path, world_path: Path, output: Path,
                    diagnostic: dict[str, Any]) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite evidence: {output}")
    manifest = read_json(baseline_dir / "evidence.json")
    scenario_path = baseline_dir / "scenario.json"
    baseline_path = baseline_dir / "baseline.json"
    for path, key in ((scenario_path, "scenario_sha256"), (baseline_path, "baseline_sha256")):
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest[key]:
            raise ValueError(f"Baseline hash mismatch: {path}")
    scenario = Scenario.model_validate(read_json(scenario_path))
    if world_path.resolve() == baseline_path.resolve():
        raise ValueError("Failure state must not overwrite the baseline")
    if output.resolve() in {world_path.resolve(), scenario_path.resolve(), baseline_path.resolve(),
                            (baseline_dir / "evidence.json").resolve()}:
        raise ValueError("Evidence output must not overwrite a source file")
    source = world_path.read_bytes()
    saved = read_json(world_path)
    if saved["scenario_id"] != scenario.id or saved["village"] is None:
        raise ValueError("Failure state does not match the baseline village scenario")
    if saved["village"]["minute"] < manifest["starting_state"]["minute"]:
        raise ValueError("Failure state predates the baseline")
    if not saved["actors"]:
        raise ValueError("Failure state has no bound actor")
    first = next(iter(saved["actors"].values()))
    with TemporaryDirectory() as temporary:
        copy_path = Path(temporary) / "world.json"
        copy_path.write_bytes(source)
        world = World(scenario, copy_path, saved["world_id"], first["actor_id"], first["session_id"])
        snapshot = capture(world, diagnostic)
    evidence = {"baseline_sha256": manifest["baseline_sha256"],
                "state_sha256": hashlib.sha256(source).hexdigest(), "state": snapshot}
    write_json(output, evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--world", type=Path)
    parser.add_argument("--diagnostic", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.scenario and not any((args.baseline, args.world, args.diagnostic)):
        create_baseline(args.scenario, args.output)
    elif all((args.baseline, args.world, args.diagnostic)) and not args.scenario:
        capture_failure(args.baseline, args.world, args.output, read_json(args.diagnostic))
    else:
        parser.error("Provide --scenario for a baseline, or --baseline, --world and --diagnostic for a failure")


if __name__ == "__main__":
    main()

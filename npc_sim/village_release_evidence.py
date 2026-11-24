"""Offline, reproducible village baseline and failure-state evidence capture."""

import argparse
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from npc_sim import village_depot, village_economy, village_life
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
        "initial_coins": state.economy.initial_coins.copy() if state.economy else {},
        "movements": ([entry.model_dump(mode="json") for entry in state.economy.movements]
                      if state.economy else []),
        "payments": ([entry.model_dump(mode="json") for entry in state.economy.payments]
                     if state.economy else []),
        "inventory_discrepancies": village_economy.reconcile_inventory(world) if state.economy else None,
        "depot_owners": ({site: {owner: {good: [lot.model_dump(mode="json") for lot in lots]
                                           for good, lots in goods.items()}
                                  for owner, goods in owners.items()}
                          for site, owners in state.depot_lots.items()}),
        "orders": {key: order.model_dump(mode="json") for key, order in state.economy.orders.items()}
                  if state.economy else {},
        "shipments": {key: shipment.model_dump(mode="json")
                      for key, shipment in state.economy.shipments.items()} if state.economy else {},
        "meals": {rid: {key: meal.model_dump(mode="json") for key, meal in person.life.meals.items()}
                  for rid, person in state.residents.items() if person.life},
        "meal_reservations": {kind: village_life.meal_plan(world, kind, day)
                              for kind in ("breakfast", "daily")},
        "meal_supply_forecast": village_economy.meal_supply_forecast(world),
        "meal_readiness": village_economy.meal_readiness(world),
        "resident_locations": {rid: world.state.actors[rid].location_id
                               for rid in state.residents if rid in world.state.actors},
        "recent_actions": [event.model_dump(mode="json") for event in state.events[-30:]],
        "event_count": len(state.events),
        "action_count": sum(event.action not in {"stock_spoiled", "cargo_spoiled", "firewood_burned",
                                                   "wake", "missed_meal"}
                            and event.actor in state.residents for event in state.events),
        "crew_sessions": {key: session.model_dump(mode="json")
                          for key, session in state.crew_sessions.items()},
        "cargo": {rid: {"loose": person.cargo.copy(),
                         "shipments": [shipment.model_dump(mode="json") for shipment in person.shipments]}
                  for rid, person in state.residents.items()},
        "wagon": state.wagon.model_dump(mode="json") if state.wagon else None,
        "maintenance": state.maintenance.model_dump(mode="json") if state.maintenance else None,
        "diagnostic": diagnostic,
    }


def continuity_ledger(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Reconcile three consecutive, active days from an opening and midnight saves."""
    if len(snapshots) != 4:
        raise ValueError("Continuity requires an opening and three consecutive midnight snapshots")
    opening_minute = snapshots[0]["minute"]
    if opening_minute % 1440 == 0:
        raise ValueError("Opening snapshot must precede the first midnight")
    first_midnight = (opening_minute // 1440 + 1) * 1440
    initial_movements = snapshots[0]["movements"]
    if not initial_movements or not any(item["kind"] == "imported" for item in initial_movements):
        raise ValueError("Opening snapshot needs a provenance-backed inventory ledger")
    opening_coins = snapshots[0]["initial_coins"]
    if not opening_coins or any(snapshot["initial_coins"] != opening_coins for snapshot in snapshots):
        raise ValueError("Initial funds ledger changed across checkpoints")
    days = []
    for index in range(3):
        before, after = snapshots[index:index + 2]
        if after["minute"] != first_midnight + index * 1440 or before["minute"] >= after["minute"]:
            raise ValueError("Snapshots must end at three consecutive midnights")
        for ledger in ("movements", "payments"):
            if after[ledger][:len(before[ledger])] != before[ledger]:
                raise ValueError(f"{ledger} history is not continuous across checkpoints")
        movements = after["movements"][len(before["movements"]):]
        payments = after["payments"][len(before["payments"]):]
        rows = []
        keys = {(site, good) for snapshot in (before, after)
                for site, goods in snapshot["stocks"].items() for good in goods}
        keys.update((item["location"], item["product"]) for item in movements)
        for site, good in sorted(keys):
            quantities = {kind: sum(item["quantity"] for item in movements
                                    if item["location"] == site and item["product"] == good
                                    and item["kind"] == kind)
                          for kind in ("produced", "delivered", "returned", "imported",
                                       "consumed", "loaded", "spoiled", "exported")}
            start = before["stocks"].get(site, {}).get(good, 0)
            end = after["stocks"].get(site, {}).get(good, 0)
            expected = start + sum(quantities[kind] for kind in ("produced", "delivered", "returned", "imported")) - sum(
                quantities[kind] for kind in ("consumed", "loaded", "spoiled", "exported"))
            if start or end or any(quantities.values()):
                rows.append({"site": site, "product": good, "opening": start, **quantities,
                             "expected": expected, "closing": end, "balanced": expected == end})
        coins = {}
        for account in sorted(before["coins"].keys() | after["coins"].keys()):
            start = before["coins"].get(account, 0)
            end = after["coins"].get(account, 0)
            transfers = sum(payment["amount"] * (1 if payment["payee"] == account else -1)
                            for payment in payments if payment["payer"] != payment["payee"]
                            and account in (payment["payer"], payment["payee"]))
            coins[account] = {"opening": start, "recorded_net": transfers,
                              "unattributed_net": end - start - transfers, "closing": end}
        activity = after["action_count"] > before["action_count"]
        imports = [item for item in movements if item["kind"] == "imported"]
        day = {"day": after["minute"] // 1440, "opening_minute": before["minute"],
               "closing_minute": after["minute"], "active_evidence": activity,
               "inventory": rows, "coins": coins, "payments": payments,
               "recorded_imports": imports,
               "orders": after["orders"], "expired_orders": [key for key, order in after["orders"].items()
                                                          if order["status"] == "expired"],
               "cargo": after["cargo"], "wagon": after["wagon"],
               "depot_owners": after["depot_owners"], "livestock_care": after["livestock_care"],
               "maintenance": after["maintenance"], "meals": after["meals"],
               "meal_reservations": after["meal_reservations"],
               "inventory_discrepancies": after["inventory_discrepancies"],
               "balanced": all(row["balanced"] for row in rows) and not after["inventory_discrepancies"]}
        days.append(day)
    return {"days": days, "engine_continuity_pass": all(day["balanced"] and day["active_evidence"]
                                                       for day in days),
            "autonomous_release_verified": False}


def capture_continuity(scenario_path: Path, world_paths: list[Path], output: Path) -> dict[str, Any]:
    """Read frozen world checkpoints without touching the saved originals."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite evidence: {output}")
    if len(world_paths) != 4 or len({path.resolve() for path in world_paths}) != 4:
        raise ValueError("Provide four distinct opening/midnight world files")
    if output.resolve() in {scenario_path.resolve(), *(path.resolve() for path in world_paths)}:
        raise ValueError("Evidence output must not overwrite an input")
    source = scenario_path.read_bytes()
    scenario = Scenario.model_validate(read_json(scenario_path))
    if scenario.village is None or scenario.village.economy is None:
        raise ValueError("Continuity requires a village with a recorded economy")
    snapshots = []
    hashes = []
    identity = None
    for path in world_paths:
        contents = path.read_bytes()
        saved = read_json(path)
        if saved["scenario_id"] != scenario.id or saved["village"] is None or not saved["actors"]:
            raise ValueError(f"World checkpoint does not match the village scenario: {path}")
        if identity is None:
            identity = saved["world_id"]
        if saved["world_id"] != identity:
            raise ValueError("Continuity checkpoints must come from the same world")
        first = next(iter(saved["actors"].values()))
        with TemporaryDirectory() as temporary:
            copy_path = Path(temporary) / "world.json"
            copy_path.write_bytes(contents)
            world = World(scenario, copy_path, saved["world_id"], first["actor_id"], first["session_id"])
            if village_depot.enabled(world):
                village_depot.reconcile(world)
            snapshots.append(capture(world))
        hashes.append(hashlib.sha256(contents).hexdigest())
    result = {"scenario_id": scenario.id, "scenario_sha256": hashlib.sha256(source).hexdigest(),
              "world_id": identity, "checkpoint_sha256": hashes,
              "evidence_type": "saved-world engine continuity (not model replay)",
              **continuity_ledger(snapshots)}
    write_json(output, result)
    return result


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
    parser.add_argument("--checkpoints", type=Path, nargs=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.scenario and args.checkpoints and not any((args.baseline, args.world, args.diagnostic)):
        capture_continuity(args.scenario, args.checkpoints, args.output)
    elif args.scenario and not any((args.baseline, args.world, args.diagnostic, args.checkpoints)):
        create_baseline(args.scenario, args.output)
    elif all((args.baseline, args.world, args.diagnostic)) and not args.scenario:
        capture_failure(args.baseline, args.world, args.output, read_json(args.diagnostic))
    else:
        parser.error("Provide --scenario for a baseline, --scenario and four --checkpoints for continuity, "
                     "or --baseline, --world and --diagnostic for a failure")


if __name__ == "__main__":
    main()

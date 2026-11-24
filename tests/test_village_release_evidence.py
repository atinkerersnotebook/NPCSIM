import hashlib

import pytest

from npc_sim import village, village_economy, village_goods
from npc_sim.models import Scenario
from npc_sim.storage import read_json, write_json
from npc_sim.village_release_evidence import (capture, capture_continuity, capture_failure,
                                              continuity_ledger, create_baseline)
from npc_sim.world import World
from test_village_expanded import scenario


def test_baseline_is_reproducible_and_does_not_change_scenario(tmp_path):
    source = tmp_path / "source.json"
    write_json(source, scenario().model_dump(mode="json"))
    before = source.read_bytes()
    first = create_baseline(source, tmp_path / "first")
    second = create_baseline(source, tmp_path / "second")
    assert source.read_bytes() == before
    assert first == second
    assert (tmp_path / "first" / "baseline.json").read_bytes() == (tmp_path / "second" / "baseline.json").read_bytes()
    assert first["scenario_sha256"] == hashlib.sha256(before).hexdigest()
    assert first["starting_state"]["minute"] == scenario().village.start_minute
    assert first["starting_state"]["stocks"] == scenario().village.stocks
    assert first["starting_state"]["orders"] == {}
    assert first["starting_state"]["shipments"] == {}
    assert first["starting_state"]["meal_reservations"]["daily"]["expected"] == 12
    with pytest.raises(FileExistsError):
        create_baseline(source, tmp_path / "first")


def test_failure_capture_preserves_timestamped_state_and_inputs(tmp_path):
    source = tmp_path / "source.json"
    value = scenario()
    write_json(source, value.model_dump(mode="json"))
    baseline = tmp_path / "baseline"
    manifest = create_baseline(source, baseline)
    world_path = tmp_path / "failure-world.json"
    world_path.write_bytes((baseline / "baseline.json").read_bytes())
    world = World(value, world_path, "g8-baseline", value.agents[0].id,
                  f"baseline-{value.agents[0].id}")
    world.state.village.minute += 5
    world.state.village.stocks["tavern"]["bread"] = 1
    world.save()
    before = world_path.read_bytes()
    diagnostic = {"code": "NO_FOOD", "request_id": "case-1"}
    result = capture_failure(baseline, world_path, tmp_path / "failure.json", diagnostic)
    assert world_path.read_bytes() == before
    assert result["baseline_sha256"] == manifest["baseline_sha256"]
    assert result["state_sha256"] == hashlib.sha256(before).hexdigest()
    assert result["state"]["minute"] == value.village.start_minute + 5
    assert result["state"]["stocks"]["tavern"]["bread"] == 1
    assert result["state"]["meal_reservations"]["daily"]["prepared"] >= 1
    assert "meal_supply_forecast" in result["state"]
    assert "meal_readiness" in result["state"]
    assert result["state"]["resident_locations"]["elsie"] == "tavern"
    assert result["state"]["diagnostic"] == diagnostic
    assert read_json(tmp_path / "failure.json") == result
    with pytest.raises(FileExistsError):
        capture_failure(baseline, world_path, tmp_path / "failure.json", diagnostic)
    (baseline / "scenario.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        capture_failure(baseline, world_path, tmp_path / "other.json", diagnostic)


def test_day_two_snapshot_preserves_progress_without_reseeding(tmp_path):
    from test_village_expanded import ROOT

    value = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-living.json"))
    path = tmp_path / "day-two.json"
    world = World(value, path, "day-two", "elsie", "session-elsie")
    state = world.state.village
    state.well_dug = state.repaired = state.wagon.repaired = True
    state.research.known["elsie"] = ["crisp-flatbread"]
    opening = state.stocks["tavern"].get("bread", 0)
    if opening:
        from npc_sim import village_goods
        village_goods.take(world, "tavern", "bread", 1)
    village.advance(world, 1440 - state.minute)
    world.save()
    restored = World(value, path, "day-two", "elsie", "session-elsie")
    after = restored.state.village
    assert after.minute == 1440
    assert capture(restored)["repairs"] == {"well": True, "mill": True, "wagon": True}
    assert capture(restored)["research"]["known"]["elsie"] == ["crisp-flatbread"]
    assert after.stocks["tavern"].get("bread", 0) <= opening
    assert village_economy.reconcile_inventory(restored) == []


def test_three_active_days_reconcile_without_reseeding_and_preserve_sources(tmp_path):
    from test_village_expanded import ROOT

    source = ROOT / "data" / "scenarios" / "millhaven-resilient.json"
    value = Scenario.model_validate(read_json(source))
    path = tmp_path / "working.json"
    world = World(value, path, "continuity", "ada", "session-ada")
    checkpoints = []

    def checkpoint(name):
        world.save()
        target = tmp_path / name
        target.write_bytes(path.read_bytes())
        checkpoints.append(target)

    checkpoint("opening.json")
    original = world.state.village.stocks["farm"]["hay"]
    for day in range(3):
        village.event(world, "ada", "controlled_feed_check", "farm",
                      "Recorded controlled farm activity.", "farm")
        village_goods.take(world, "farm", "hay", 1)
        if day == 1:
            village_goods.put(world, "farm", "hay", 2)
        state = world.state.village
        village.advance(world, (state.minute // 1440 + 1) * 1440 - state.minute)
        checkpoint(f"midnight-{day}.json")
        world = World(value, path, "continuity", "ada", "session-ada")
    before = [item.read_bytes() for item in checkpoints]
    result = capture_continuity(source, checkpoints, tmp_path / "continuity.json")
    assert [item.read_bytes() for item in checkpoints] == before
    assert result["engine_continuity_pass"]
    assert not result["autonomous_release_verified"]
    assert [day["inventory"][next(i for i, row in enumerate(day["inventory"])
                                    if row["site"] == "farm" and row["product"] == "hay")]["closing"]
            for day in result["days"]] == [original - 1, original, original - 1]
    assert all(day["active_evidence"] and day["balanced"] and day["depot_owners"]
               for day in result["days"])
    assert all("unattributed_net" in funds for day in result["days"]
               for funds in day["coins"].values())
    assert all(any(item["location"] == "village-square" and item["product"] == "iron"
                   for item in day["recorded_imports"]) for day in result["days"])
    assert read_json(tmp_path / "continuity.json") == result
    with pytest.raises(FileExistsError):
        capture_continuity(source, checkpoints, tmp_path / "continuity.json")
    with pytest.raises(ValueError, match="four distinct"):
        capture_continuity(source, checkpoints[:2] + [checkpoints[1], checkpoints[3]], tmp_path / "invalid.json")
    alternate = read_json(checkpoints[3])
    alternate["world_id"] = "different-world"
    write_json(tmp_path / "alternate.json", alternate)
    with pytest.raises(ValueError, match="same world"):
        capture_continuity(source, checkpoints[:3] + [tmp_path / "alternate.json"], tmp_path / "invalid.json")
    with pytest.raises(ValueError, match="consecutive midnights"):
        altered = read_json(checkpoints[2])
        altered["village"]["minute"] -= 5
        write_json(tmp_path / "early.json", altered)
        capture_continuity(source, checkpoints[:2] + [tmp_path / "early.json", checkpoints[3]],
                           tmp_path / "invalid.json")


def test_town_supply_separates_physical_prepared_meals_by_service(tmp_path):
    from npc_sim import town_supply
    from test_village_expanded import ROOT

    value = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-generations.json"))
    world = World(value, tmp_path / "meal-supply.json", "meal-supply", "elsie", "session-elsie")
    state = world.state.village
    state.minute = 480
    village_goods.put(world, "tavern", "meals", 10)
    report = town_supply.report(world.state, world.scenario.village)
    meals = next(row for row in report["inventory"]
                 if row["location"] == "tavern" and row["product"] == "meals")
    allocation = meals["meal_allocations"]
    assert allocation["prepared"] == 10
    assert allocation["services"][0]["service"] == "breakfast"
    assert allocation["services"][-1]["service"] == "supper"
    assert sum(item["reserved"] for item in allocation["services"]) + allocation["unallocated"] == 10


def test_continuity_rejects_paused_and_unbalanced_days(tmp_path):
    from test_village_expanded import ROOT

    value = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-resilient.json"))
    world = World(value, tmp_path / "world.json", "continuity", "ada", "session-ada")
    opening = capture(world)
    snapshots = [opening]
    for _ in range(3):
        state = world.state.village
        village.advance(world, (state.minute // 1440 + 1) * 1440 - state.minute)
        snapshots.append(capture(world))
    assert not continuity_ledger(snapshots)["engine_continuity_pass"]
    snapshots[2]["stocks"]["tavern"]["bread"] += 1
    assert not continuity_ledger(snapshots)["days"][1]["balanced"]
    with pytest.raises(ValueError, match="continuous"):
        snapshots[2]["movements"].insert(0, {"minute": 1440, "location": "farm", "product": "hay",
                                             "quantity": 1, "kind": "produced"})
        continuity_ledger(snapshots)

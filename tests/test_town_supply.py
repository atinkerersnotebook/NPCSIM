from fastapi.testclient import TestClient

import npc_sim.app as application
from npc_sim.controller import Controller
from npc_sim.models import AppConfig, Scenario, VillageCommittedWork, VillageDashboard, VillageStockLot
from npc_sim.storage import read_json, write_json
from npc_sim.town_supply import report
from npc_sim.village_communication_models import NoticeTerms, NoticeVersion
from npc_sim.village_economy_models import VillageSupplierOrder
from npc_sim.world import World
from test_village_expanded import ROOT


def town(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-complete.json"))
    world = World(scenario, tmp_path / "worlds" / "town" / "world-state.json", "town", "elsie", "session")
    return scenario, world


def test_forecast_does_not_double_count_batches_or_allocate_expired_or_reserved_stock(tmp_path):
    scenario, world = town(tmp_path)
    state = world.state.village
    state.minute = 480
    scenario.village.spoil_after["bread"] = 720
    state.stocks = {"bakery": {"bread": 2, "flour": 2}, "tavern": {"bread": 0}, "farm": {"grain": 4}}
    state.stock_lots = {"bakery": {"bread": [VillageStockLot(quantity=2, produced_at=480, expires_at=520)]}}
    state.economy.config.reserve_targets = {"bakery": {"flour": 1}, "farm": {"grain": 2}}
    state.economy.config.production_targets = {}
    state.residents["jory"].life.work = VillageCommittedWork(location="bakery", finishes_at=540,
                                                            outputs={"bread": 2}, ingredients=["flour"])
    world.bind("jory", "session-jory").actor.location_id = "bakery"

    def order(key, source, destination, product, quantity, status, plan=None):
        state.economy.orders[key] = VillageSupplierOrder(id=key, request_key=key, source=source,
            destination=destination, buyer="elsie", supplier="jory", product=product, quantity=quantity,
            price=1, created_at=480, status=status, deadline=600, required_fresh_until=620,
            production_plan=plan or {})

    order("bread", "bakery", "tavern", "bread", 3, "in_production", {"output": 3, "finish": 550, "input:flour": 2})
    order("flour", "bakery", "tavern", "flour", 1, "accepted_planned", {"output": 1, "finish": 550, "input:flour": 2})
    order("grain-a", "farm", "tavern", "grain", 2, "accepted")
    order("grain-b", "farm", "tavern", "grain", 2, "accepted")
    order("proposed", "farm", "bakery", "grain", 1, "proposed")
    before = state.model_dump()
    snapshot = report(world.state, scenario.village)
    rows = {(row["location"], row["product"]): row for row in snapshot["inventory"]}
    assert rows["tavern", "bread"]["projected"][0] == 2
    assert rows["tavern", "bread"]["projected"][2] == 0
    assert rows["bakery", "flour"]["projected"][0] == 1
    assert rows["farm", "grain"]["projected"] == [2, 2, 2]
    assert ("bakery", "grain") not in rows
    assert {(risk["id"], risk["kind"], risk["missing"]) for risk in snapshot["risks"]} == {
        ("bread", "delivery", 1), ("flour", "production input", 1), ("grain-b", "delivery", 2)}
    assert next(item["inputs"] for item in snapshot["production"] if item["id"] == "bread") == {"flour": 2}
    assert state.model_dump() == before


def test_api_uses_saved_revision_and_shows_all_maintenance_and_physical_note_versions(tmp_path, monkeypatch):
    scenario, world = town(tmp_path)
    state = world.state.village
    board = state.communication.config.board_id
    terms = NoticeTerms(type="supply_request", subject="Flour", description="Original board copy")
    first = NoticeVersion(id="notice-1", version=1, author="elsie", terms=terms, created_at=480, updated_at=480)
    latest = first.model_copy(update={"version": 2, "status": "accepted",
                                     "terms": terms.model_copy(update={"description": "Updated by conversation"})})
    state.communication.notices[first.id] = [first, latest]
    state.communication.placements[board] = {first.id: 1}
    state.dashboard = VillageDashboard(maintenance=[{"id": f"job-{n}", "title": "Clear the road",
        "assignee": "elsie", "location": "village-square", "day": 1, "release_at": 480, "due_at": 500,
        "worked_minutes": 15, "required_minutes": 15, "supplies": {}, "status": "completed"} for n in range(35)])
    write_json(world.path, world.state.model_dump(mode="json"))
    write_json(tmp_path / "scenarios" / f"{scenario.id}.json", scenario.model_dump(mode="json"))
    config = AppConfig.model_validate(read_json(ROOT / "data" / "config.json"))
    controller = Controller(ROOT, tmp_path, config)
    monkeypatch.setattr(application.app.state, "controller", controller, raising=False)
    client = TestClient(application.app)
    assert client.get("/api/town-supply").json() is None
    controller.run = {"world_id": "town", "scenario_id": scenario.id, "title": scenario.title}
    response = client.get("/api/town-supply")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    snapshot = response.json()
    paper = next(note for note in snapshot["notes"] if note["id"] == first.id)
    assert (paper["text"], paper["version"], paper["latest_status"]) == ("Original board copy", 1, "accepted")
    assert paper["location"] == world.state.entities[board].location
    assert snapshot["requests"][0]["detail"] == "Updated by conversation"
    assert len(snapshot["maintenance"]) == 35
    assert any(project["id"] == "repair_mill" for project in snapshot["projects"])
    world.state.revision += 1
    state.repaired = True
    write_json(world.path, world.state.model_dump(mode="json"))
    updated = client.get("/api/town-supply").json()
    assert updated["revision"] == snapshot["revision"] + 1
    assert next(project for project in updated["projects"] if project["id"] == "repair_mill")["status"] == "completed"

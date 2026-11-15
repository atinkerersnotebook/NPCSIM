from npc_sim import village_fallback
from npc_sim.models import VillageSupplyNeed
from npc_sim.world import World
from test_village_communication import actor, create_world, flour, post, ready


def test_personally_posted_need_persists_without_leaking_to_other_residents(tmp_path):
    world = create_world(tmp_path / "supply-needs.json")
    jory = actor(world, "jory", "village-square")
    actor(world, "mara", "bakery")
    ready(world)
    nid = post(jory, flour(), channel="board")
    village_fallback.update_supply_needs(jory)
    state = world.state.village
    needs = state.residents["jory"].fallback.supply_needs
    assert len(needs) == 1
    need = next(iter(needs.values()))
    assert (need.notice_id, need.status, need.supplier_id) == (nid, "awaiting-response", "mara")
    assert state.residents["mara"].fallback.supply_needs == {}
    world.save()
    reloaded = World(world.scenario, world.path, "communication", "jory", "session-jory")
    assert next(iter(reloaded.state.village.residents["jory"].fallback.supply_needs.values())) == need
    village_fallback.update_supply_needs(jory)
    assert len(needs) == 1


def test_supply_need_record_has_explicit_next_follow_up_time():
    need = VillageSupplyNeed(owner_id="nella", good="grain", quantity_needed=1,
                             purpose="store-reserve", destination_id="general-store",
                             supplier_id="ada", next_check_after=840)
    assert need.status == "identified"
    assert need.next_check_after == 840


def test_current_tasks_move_delivered_need_out_of_commitments(tmp_path):
    world = create_world(tmp_path / "tasks.json")
    world.scenario.village.anti_idle = True
    jory = actor(world, "jory", "village-square")
    ready(world)
    nid = post(jory, flour(), channel="board")
    pending = village_fallback.observation(jory)["fallback"]["current_tasks"]
    assert any(item["task"] == nid for item in pending["commitments"])
    notice = world.state.village.communication.knowledge["jory"][nid].snapshot
    world.state.village.communication.knowledge["jory"][nid].snapshot = notice.model_copy(
        update={"status": "delivered"})
    done = village_fallback.observation(jory)["fallback"]["current_tasks"]
    assert any(item["task"] == nid for item in done["completed"])
    assert all(item["task"] != nid for item in done["commitments"])
    world.save()
    restored = World(world.scenario, world.path, "communication", "jory", "session-jory")
    assert village_fallback.observation(restored)["fallback"]["current_tasks"]["completed"] == done["completed"]


def test_current_tasks_do_not_reveal_unread_order(tmp_path):
    from npc_sim.village_economy_models import VillageSupplierOrder

    world = create_world(tmp_path / "private-tasks.json")
    world.scenario.village.anti_idle = True
    jory = actor(world, "jory", "bakery")
    order = VillageSupplierOrder(id="private", request_key="private", buyer="jory", supplier="mara",
                                 source="watermill", destination="bakery", product="flour", quantity=1,
                                 price=1, deadline=900, required_fresh_until=960, created_at=480,
                                 coordination_notice_id="unread", status="accepted")
    world.state.village.economy.orders[order.id] = order
    tasks = village_fallback.observation(jory)["fallback"]["current_tasks"]
    assert all(item["task"] != order.id for group in tasks.values() for item in group)


def test_current_tasks_reset_completed_care_at_midnight(tmp_path):
    from test_village_livestock import make_world

    world = make_world(tmp_path)
    state = world.state.village
    state.livestock.cow.day = state.minute // 1440
    state.livestock.cow.cleaned = True
    today = village_fallback.current_tasks(world)
    assert {item["task"] for item in today["completed"]} >= {"care:cow"}
    state.minute = 1440
    tomorrow = village_fallback.current_tasks(world)
    assert "care:cow" in {item["task"] for item in tomorrow["commitments"]}
    assert "care:cow" not in {item["task"] for item in tomorrow["completed"]}

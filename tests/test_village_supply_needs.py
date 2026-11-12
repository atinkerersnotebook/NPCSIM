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

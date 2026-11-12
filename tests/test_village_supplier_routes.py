from npc_sim import village_fallback, village_goods
from npc_sim.world import World
from test_village_communication import actor, create_world, flour, post, ready
from test_world import act


def travel(world, rid, location, exit_id):
    bound = actor(world, rid, location)
    ready(world)
    result = act(bound, "go", target=exit_id)
    assert result["outcome"] == "APPLIED", result
    return bound


def test_repeated_supplier_route_tracks_personal_need_and_persists(tmp_path):
    world = create_world(tmp_path / "route.json")
    jory = actor(world, "jory", "village-square")
    post(jory, flour(), channel="board")
    village_fallback.update_supply_needs(jory)
    for exit_id in ("square-bakery", "mill-bakery", "mill-bakery", "square-bakery"):
        location = world.actor.location_id
        travel(world, "jory", location, exit_id)
    fallback = world.state.village.residents["jory"].fallback
    assert len(fallback.recent_locations) == 4
    assert [(step.origin, step.destination) for step in fallback.recent_locations[-2:]] == [
        ("watermill", "bakery"), ("bakery", "village-square")]
    assert not village_fallback.repeated_supplier_searches(jory)
    travel(world, "jory", "village-square", "square-bakery")
    travel(world, "jory", "bakery", "mill-bakery")
    flagged = village_fallback.repeated_supplier_searches(jory)
    assert flagged
    observed = jory.observe()["village"]
    assert observed["personal"]["recent_locations"][-1]["destination"] == "watermill"
    assert len([event for event in world.state.village.events
                if event.action == "SUPPLIER_SEARCH_LOOP" and event.actor == "jory"]) == 1
    assert world.state.village.residents["mara"].fallback.recent_locations == []
    world.save()
    reloaded = World(world.scenario, world.path, "communication", "jory", "session-jory")
    assert village_fallback.repeated_supplier_searches(reloaded) == village_fallback.repeated_supplier_searches(jory)
    village_goods.put(jory, "watermill", "flour", 1)
    assert not village_fallback.repeated_supplier_searches(jory)


def test_optional_search_defers_repeated_travel_until_personal_evidence_changes(tmp_path):
    world = create_world(tmp_path / "optional-route.json")
    finn = actor(world, "finn", "village-square")
    village_fallback.plan_optional_route(finn, "share:river-observation", "tavern", "Share an observation when someone is present")
    finn = travel(world, "finn", "village-square", "square-tavern")
    goal = world.state.village.residents["finn"].fallback.route_goal
    assert goal.blocked_reason
    finn = travel(world, "finn", "tavern", "square-tavern")
    ready(world)
    assert act(finn, "go", target="square-tavern")["code"] == "ROUTE_GOAL_DEFERRED"
    finn.actor.messages.append({"actor_id": "jory", "message": "I can meet now."})
    assert act(finn, "go", target="square-tavern")["outcome"] == "APPLIED"
    assert finn.observe()["village"]["personal"]["route_goal"]["goal_id"] == "share:river-observation"


def test_supplier_loop_clears_on_personal_need_change_and_does_not_block_ordinary_travel(tmp_path):
    world = create_world(tmp_path / "route-change.json")
    jory = actor(world, "jory", "village-square")
    post(jory, flour(), channel="board")
    village_fallback.update_supply_needs(jory)
    for exit_id in ("square-bakery", "mill-bakery", "mill-bakery", "square-bakery",
                    "square-bakery", "mill-bakery"):
        travel(world, "jory", jory.actor.location_id, exit_id)
    assert village_fallback.repeated_supplier_searches(jory)
    need = next(iter(world.state.village.residents["jory"].fallback.supply_needs.values()))
    need.status = "delivered"
    assert not village_fallback.repeated_supplier_searches(jory)
    ready(world)
    assert act(jory, "go", target="mill-bakery")["outcome"] == "APPLIED"

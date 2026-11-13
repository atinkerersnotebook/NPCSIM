from npc_sim import village_walking


def step(world, action, **args):
    world.state.village.minute = max(world.state.village.minute,
                                     world.state.village.residents[world.actor_id].available_at)
    return act(world, action, **args)
from npc_sim.world import World
from test_village_expanded import scenario
from test_world import act


def make_world(tmp_path):
    value = scenario()
    value.id = "millhaven-living"
    value.village.daily_life = True
    value.village.start_minute = 540
    value.village.goal_deadlines = {}
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    return World(value, tmp_path / "walking.json", "walking", "nella", "session-nella")


def test_destination_is_planned_before_step_and_cannot_ping_pong(tmp_path):
    world = make_world(tmp_path)
    person = world.state.village.residents["nella"]
    person.known_routes = ["square-store", "square-tavern"]
    assert world.actor.location_id == "general-store"
    assert step(world, "go", target="square-store")["code"] == "ROUTE_REQUIRED"
    result = step(world, "village_plan_walk", destination="tavern", purpose="Visit Elsie's tavern")
    assert result["code"] == "ROUTE_PLANNED", result
    plan = person.walking_plan
    assert plan.locations == ["village-square", "tavern"]
    assert plan.doors == ["square-store", "square-tavern"]
    assert [action for action in world.actions() if action["id"] == "go"][0]["availability"] != "blocked"
    assert world.observe()["village"]["personal"]["walking_plan"]["destination"] == "tavern"
    assert step(world, "go", target="square-store")["outcome"] == "APPLIED"
    assert step(world, "go", target="square-store")["code"] == "ROUTE_DEVIATION"
    assert step(world, "village_plan_walk", destination="general-store", purpose="Check store again")["code"] in {"ACTION_UNAVAILABLE", "ROUTE_COMMITTED"}
    assert step(world, "go", target="square-tavern")["outcome"] == "APPLIED"
    assert person.walking_plan is None


def test_route_minimizes_stops_and_survives_reload(tmp_path):
    world = make_world(tmp_path)
    world.state.village.residents["nella"].known_routes = ["square-store", "square-bakery"]
    assert step(world, "village_plan_walk", destination="bakery", purpose="Collect bread")["code"] == "ROUTE_PLANNED"
    plan = world.state.village.residents["nella"].walking_plan
    assert len(plan.doors) == 2
    world.save()
    restored = World(world.scenario, world.path, "walking", "nella", "session-nella")
    assert restored.state.village.residents["nella"].walking_plan == plan
    assert step(restored, "go", target=plan.doors[0])["outcome"] == "APPLIED"
    assert restored.state.village.residents["nella"].walking_plan.doors == plan.doors
    assert restored.state.village.residents["nella"].walking_plan.next_step == 1


def test_advertised_destinations_use_only_known_traversable_doors(tmp_path):
    world = make_world(tmp_path)
    world.state.village.residents["nella"].known_routes = ["square-store", "square-tavern"]
    action = next(item for item in world.actions() if item["id"] == "village_plan_walk")
    assert "tavern" in action["arguments_schema"]["properties"]["destination"]["enum"]
    assert "bakery" not in action["arguments_schema"]["properties"]["destination"]["enum"]
    assert step(world, "village_plan_walk", destination="bakery", purpose="Pick up bread")["code"] == "INVALID_ARGUMENTS"


def test_fresh_task_message_permits_reroute(tmp_path):
    world = make_world(tmp_path)
    world.state.village.residents["nella"].known_routes = ["square-store", "square-tavern"]
    assert step(world, "village_plan_walk", destination="tavern", purpose="Visit tavern")["code"] == "ROUTE_PLANNED"
    assert step(world, "go", target="square-store")["outcome"] == "APPLIED"
    world.actor.messages.append({"actor_id": "elsie", "text": "Please bring the store ledger."})
    assert step(world, "village_plan_walk", destination="general-store", purpose="Collect ledger for Elsie")["code"] == "ROUTE_PLANNED"
    assert world.state.village.residents["nella"].walking_plan.doors == ["square-store"]
    assert world.state.village.residents["nella"].walking_plan.next_step == 0
    assert step(world, "go", target="square-store")["outcome"] == "APPLIED"


def test_blocked_edge_allows_detour_without_moving_through_it(tmp_path):
    world = make_world(tmp_path)
    world.state.village.residents["nella"].known_routes = ["square-store", "square-tavern"]
    assert step(world, "village_plan_walk", destination="tavern", purpose="Visit tavern")["code"] == "ROUTE_PLANNED"
    world.state.entities["square-store"].open = False
    assert step(world, "go", target="square-store")["code"] == "ROUTE_BLOCKED"
    assert village_walking.check_plan(world, "tavern")[0] == "NO_KNOWN_ROUTE"
    assert world.state.village.residents["nella"].walking_plan is not None

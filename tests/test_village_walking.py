from npc_sim import village_walking
from npc_sim.village_communication_models import (NoticeKnowledge, NoticeTerms, NoticeVersion,
                                                 VillageCommunicationConfig, VillageCommunicationState)


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


def test_planned_contact_waits_for_public_wake_time(tmp_path):
    world = make_world(tmp_path)
    world.state.village.minute = 400
    world.state.village.residents["finn"].life.sleeping = True
    world.state.village.residents["finn"].life.sleep_until = 420
    world.state.village.residents["nella"].available_at = 400
    world.state.village.residents["nella"].known_routes = list(world.state.village.residents["nella"].known_routes)
    assert step(world, "village_plan_walk", destination="riverbank",
                purpose="Find Finn for a fish order")["code"] == "RESIDENT_ASLEEP"
    world.state.village.minute = world.scenario.village.residents["finn"].wake_minute
    world.state.village.residents["finn"].life.sleeping = False
    assert step(world, "village_plan_walk", destination="riverbank",
                purpose="Find Finn for a fish order")["code"] == "ROUTE_PLANNED"


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
    assert result["observation"]["village"]["minute"] == world.scenario.village.start_minute
    assert step(world, "go", target="square-store")["outcome"] == "APPLIED"
    assert step(world, "go", target="square-store")["code"] == "ROUTE_DEVIATION"
    assert step(world, "village_plan_walk", destination="general-store", purpose="Check store again")["code"] in {"ACTION_UNAVAILABLE", "ROUTE_COMMITTED"}
    assert step(world, "go", target="square-tavern")["outcome"] == "APPLIED"
    assert person.walking_plan is None


def test_snapshot_uses_committed_route_planning(tmp_path):
    from npc_sim.models import Scenario
    from npc_sim.storage import read_json
    from pathlib import Path

    value = Scenario.model_validate(read_json(Path(__file__).resolve().parent.parent /
                                              "data" / "scenarios" / "millhaven-snapshot-i.json"))
    world = World(value, tmp_path / "snapshot-walking.json", "snapshot-walking", "nella", "session-nella")
    world.state.village.residents["nella"].known_routes = ["square-store", "square-tavern", "square-bakery"]
    world.actor.location_id = "general-store"

    assert step(world, "go", target="square-store")["code"] == "ROUTE_REQUIRED"
    assert step(world, "village_plan_walk", destination="tavern", purpose="Deliver bread")["code"] == "ROUTE_PLANNED"
    assert world.state.village.residents["nella"].walking_plan.locations == ["village-square", "tavern"]
    assert village_walking.check_plan(world, "tavern")[0] == "ROUTE_COMMITTED"
    assert "village_plan_walk" not in {action["id"] for action in world.actions()}


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


def test_personally_received_notice_version_permits_reroute_without_remote_knowledge(tmp_path):
    world = make_world(tmp_path)
    person = world.state.village.residents["nella"]
    person.known_routes = ["square-store", "square-tavern"]
    world.state.village.communication = VillageCommunicationState(config=VillageCommunicationConfig())
    assert step(world, "village_plan_walk", destination="tavern", purpose="Visit tavern")["code"] == "ROUTE_PLANNED"
    notice = NoticeVersion(id="notice-1", version=1, author="elsie",
                           terms=NoticeTerms(type="work_request", subject="bread", description="Need bread"),
                           created_at=world.state.village.minute, updated_at=world.state.village.minute)
    world.state.village.communication.notices[notice.id] = [notice]
    assert village_walking.check_plan(world, "village-square")[0] == "ROUTE_COMMITTED"
    world.state.village.communication.knowledge["nella"] = {
        notice.id: NoticeKnowledge(snapshot=notice, heard_at=world.state.village.minute,
                                   source_type="conversation")}
    assert step(world, "village_plan_walk", destination="village-square", purpose="Answer new request")["code"] == "ROUTE_PLANNED"
    assert person.walking_plan.known_notice_versions == {notice.id: 1}
    world.save()
    restored = World(world.scenario, world.path, "walking", "nella", "session-nella")
    assert restored.state.village.residents["nella"].walking_plan.known_notice_versions == {notice.id: 1}


def test_blocked_edge_allows_detour_without_moving_through_it(tmp_path):
    world = make_world(tmp_path)
    world.state.village.residents["nella"].known_routes = ["square-store", "square-tavern"]
    assert step(world, "village_plan_walk", destination="tavern", purpose="Visit tavern")["code"] == "ROUTE_PLANNED"
    world.state.entities["square-store"].open = False
    assert step(world, "go", target="square-store")["code"] == "ROUTE_BLOCKED"
    assert village_walking.check_plan(world, "tavern")[0] == "NO_KNOWN_ROUTE"
    assert world.state.village.residents["nella"].walking_plan is not None

from uuid import uuid4

from npc_sim import village, village_extensions, village_precheck
from test_village_engine_backlog import make_world
from npc_sim.world import World


def act(world, action, **args):
    return world.act(uuid4().hex, world.revision(), action, args)


def test_patrol_precheck_and_valid_action(tmp_path):
    world = make_world(tmp_path).bind("garrick", "session-garrick")
    resident = world.scenario.village.residents["garrick"]
    world.actor.location_id = "bakery"
    before = world.state.village.minute
    result = act(world, "village_patrol")
    assert result["code"] == "WRONG_PATROL_LOCATION"
    assert world.state.village.minute == before
    routine = next(entry for entry in reversed(resident.schedule) if entry.minute <= before % 1440)
    world.actor.location_id = routine.location
    world.state.village.residents["garrick"].next_work = before + 30
    assert act(world, "village_patrol")["code"] == "WORK_COOLDOWN"
    world.state.village.residents["garrick"].next_work = before
    assert act(world, "village_patrol")["code"] == "PATROL_COMPLETED"


def test_evaluate_action_reports_personal_limits_without_hidden_stock(tmp_path):
    world = make_world(tmp_path).bind("garrick", "session-garrick")
    world.actor.location_id = "bakery"
    result = village_precheck.evaluate_action(world, "village_patrol", {})
    assert result["allowed"] is False
    assert result["required_location"] in {"bridge", "village-square"}
    assert result["available_quantity"] is None
    assert result["minimum_inputs"] is None
    assert result["reason"]
    assert village_precheck.evaluate_action(world, "village_patrol", {}) == result
    assert any(item["id"] == "village_patrol" and not item["precheck"]["allowed"]
               for item in village_precheck.get_available_actions(world))
    world.actor.location_id = result["required_location"]
    assert village_precheck.evaluate_action(world, "village_patrol", {})["allowed"]


def test_capacity_precheck_does_not_consume_stock(tmp_path):
    world = make_world(tmp_path)
    world.actor.body.capacity = 1
    start = world.state.village.minute
    assert act(world, "village_load_goods", goods={"grain": 2}, destination="bakery")["code"] == "CAPACITY_EXCEEDED"
    assert world.state.village.minute == start


def test_daily_telemetry_rollover_persists_and_rejects_are_not_useful(tmp_path):
    from npc_sim.living_village import build_scenario
    from test_village_expanded import scenario
    value = build_scenario(scenario())
    value.village.start_minute = 480
    world = World(value, tmp_path / "telemetry.json", "telemetry", "torvin", "session-torvin")
    state = world.state.village
    state.minute = 1435
    village.advance(world, 10)
    assert state.daily_time_use[0]["torvin"].idle + state.daily_time_use[0]["torvin"].sleep == 5
    assert state.daily_time_use[1]["torvin"].idle + state.daily_time_use[1]["torvin"].sleep == 5
    assert state.time_use["torvin"].idle + state.time_use["torvin"].sleep == 10
    world.save()
    restored = World(world.scenario, world.path, "telemetry", "torvin", "session-torvin")
    assert (restored.state.village.daily_time_use[0]["torvin"].idle
            + restored.state.village.daily_time_use[0]["torvin"].sleep) == 5
    assert act(restored, "village_patrol")["outcome"] == "REJECTED"
    assert restored.state.village.time_use["torvin"].useful_actions == 0
    assert 0 in restored.state.village.dashboard.daily_time_use


def test_physical_channel_precheck_and_valid_read(tmp_path):
    from npc_sim.living_village import build_scenario
    from test_village_expanded import scenario
    value = build_scenario(scenario())
    value.village.start_minute = 480
    world = World(value, tmp_path / "channel.json", "channel", "torvin", "session-torvin")
    assert world.state.village.communication is not None
    world.actor.location_id = "bakery"
    before = world.state.village.minute
    assert act(world, "village_notice_read")["code"] == "CHANNEL_UNAVAILABLE"
    assert world.state.village.minute == before
    world.actor.location_id = world.state.entities[world.state.village.communication.config.board_id].location
    assert village_precheck.check(world, "village_notice_read", {}) is None
    person = world.state.village.residents["torvin"]
    person.available_at = world.state.village.minute
    person.busy_until = world.state.village.minute
    assert act(world, "village_notice_read")["outcome"] == "APPLIED"


def test_completed_patrol_counts_as_useful_work(tmp_path):
    from npc_sim.living_village import build_scenario
    from test_village_expanded import scenario
    value = build_scenario(scenario())
    value.village.start_minute = 480
    world = World(value, tmp_path / "work.json", "work", "garrick", "session-garrick")
    minute = world.state.village.minute
    routine = next(entry for entry in reversed(value.village.residents["garrick"].schedule)
                   if entry.minute <= minute % 1440)
    world.actor.location_id = routine.location
    person = world.state.village.residents["garrick"]
    person.available_at = minute
    person.busy_until = minute
    person.next_work = minute
    assert act(world, "village_patrol")["code"] == "PATROL_COMPLETED"
    village.advance(world, 5)
    usage = world.state.village.time_use["garrick"]
    daily = world.state.village.daily_time_use[minute // 1440]["garrick"]
    assert usage.useful_actions == daily.useful_actions == 1
    assert usage.useful_minutes == daily.useful_minutes == 5
    assert usage.work == 5


def test_reversed_travel_is_wasted_until_useful_interaction(tmp_path):
    from npc_sim.living_village import build_scenario
    from test_village_expanded import scenario
    value = build_scenario(scenario())
    value.village.start_minute = 480
    world = World(value, tmp_path / "travel.json", "travel", "torvin", "session-torvin")
    state = world.state.village
    world.actor.location_id = "village-square"
    person = state.residents["torvin"]
    person.available_at = state.minute
    person.busy_until = state.minute
    assert act(world, "go", target="square-tavern")["outcome"] == "APPLIED"
    village.advance(world, 5)
    person.available_at = state.minute
    person.busy_until = state.minute
    assert act(world, "go", target="square-tavern")["outcome"] == "APPLIED"
    village.advance(world, 5)
    usage = state.time_use["torvin"]
    daily = state.daily_time_use[state.minute // 1440]["torvin"]
    assert usage.travel >= 10
    assert usage.wasted_travel == 5
    assert daily.wasted_travel == 5
    assert usage.useful_actions == 0


def test_route_annotation_only_marks_tracked_exit(tmp_path):
    from npc_sim import village_fallback
    from test_village_communication import actor, create_world, ready
    world = create_world(tmp_path / "route-annotation.json")
    finn = actor(world, "finn", "village-square")
    ready(world)
    world.state.village.residents["finn"].known_routes.append("square-tavern")
    village_fallback.plan_optional_route(finn, "share:observation", "tavern", "Optional visit")
    goal = world.state.village.residents["finn"].fallback.route_goal
    goal.blocked_reason = "Repeated optional route without a new observation or useful interaction."
    before = goal.model_dump()
    go = next(action for action in finn.actions() if action["id"] == "go")
    assert go["target_requirements"]["square-tavern"]
    assert go["availability"] != "blocked"  # Other exits remain independently usable.
    assert goal.model_dump() == before
    assert act(finn, "go", target="square-tavern")["code"] == "ROUTE_GOAL_DEFERRED"
    assert act(finn, "go", target="square-bakery")["outcome"] == "APPLIED"


def test_known_research_hours_and_patrol_priority(tmp_path):
    from test_village_research import make_world as research_world, propose
    world = research_world(tmp_path, actor="garrick")
    assert world.state.village.research is not None
    assert village_precheck.check(world, "village_research", {"project": "hidden-project"}) is None
    assert act(world, "village_patrol")["outcome"] == "APPLIED"
    village.advance(world, 5)
    key = propose(world, title="Watchpost kit", description="A small wooden watchpost kit.",
                  skill="patrol", kind="tool", inputs={"planks": 1}, equipment=[], duration=15)
    assert key in world.state.village.research.known["garrick"]
    minute = world.state.village.minute
    world.state.village.minute = world.state.village.residents["garrick"].next_work - 5
    assert village_precheck.check(world, "village_research", {"project": key})[0] == "PATROL_DUE"
    world.state.village.minute = minute // 1440 * 1440 + 1015
    world.state.village.residents["garrick"].next_work = world.state.village.minute + 60
    assert village_precheck.check(world, "village_research", {"project": key})[0] == "OUTSIDE_RESEARCH_HOURS"

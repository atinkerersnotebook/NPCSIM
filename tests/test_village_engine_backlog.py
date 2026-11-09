import pytest

from npc_sim import village, village_extensions, village_life, village_turns
from npc_sim.dungeon_master import DungeonMasterConfig, DungeonMasterReview, validate_review
from npc_sim.models import VillageDayPolicy
from npc_sim.world import World
from test_village_expanded import scenario
from test_world import act


def make_world(tmp_path, **policy):
    value = scenario()
    value.village.start_minute = 480
    for field, setting in policy.items():
        setattr(value.village, field, setting)
    for agent in value.agents:
        agent.start_location = "tavern"
    world = World(value, tmp_path / "backlog.json", "backlog", "torvin", "session-torvin")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    return world


def test_breakfast_consumes_and_pays_once_without_satisfying_supper(tmp_path):
    world = make_world(tmp_path, breakfast=True)
    village.advance(world, 30)
    state = world.state.village
    bread = state.stocks["tavern"]["bread"]
    coins = state.coins["torvin"]
    result = act(world, "village_eat")
    assert result["code"] == "MEAL_EATEN"
    assert state.residents["torvin"].life.meals["0:breakfast"].status == "eaten"
    assert state.stocks["tavern"]["bread"] == bread - 1
    assert state.coins["torvin"] == coins - world.scenario.village.meal_price
    assert act(world, "village_eat")["code"] == "ACTOR_BUSY"
    village.advance(world, 660)
    assert state.residents["torvin"].life.meals["0:daily"].status == "pending"
    assert village_life.meal_due(state.residents["torvin"].life, state.minute).kind == "daily"


def test_legacy_scenario_keeps_single_meal_and_no_extension_actions(tmp_path):
    world = make_world(tmp_path)
    assert world.state.village.residents["torvin"].life.meals == {}
    assert village_extensions.actions(world) == []
    assert act(world, "village_eat")["code"] == "NO_MEAL_DUE"


def test_missed_breakfast_keeps_its_effects_without_blocking_work_forever(tmp_path):
    world = make_world(tmp_path, breakfast=True)
    village.advance(world, 120)
    life = world.state.village.residents["torvin"].life
    assert life.meals["0:breakfast"].status == "missed"
    assert life.missed_meals == 1
    assert village_life.action_blocker(world, "village_grow_hay") is None
    world.state.village.minute = 1440 + 480
    assert village_life.cooldown_factor(world, "torvin") == 2
    assert life.meals["0:breakfast"].status == "missed"


def test_supplier_order_requires_travel_to_actual_person_and_records_requirements(tmp_path):
    from npc_sim.living_village import build_scenario

    value = build_scenario(scenario())
    value.village.start_minute = 480
    value.village.social = None
    value.village.events = None
    value.village.breakfast = False
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
        if agent.id == "jory":
            agent.start_location = "watermill"
    world = World(value, tmp_path / "orders.json", "orders", "elsie", "session-elsie")
    cast = {agent.id: world.bind(agent.id, f"session-{agent.id}") for agent in value.agents}
    state = world.state.village
    coins = dict(state.coins)
    request = {"supplier": "jory", "product": "bread", "quantity": 1,
               "deadline": 1080, "required_fresh_until": 1140}

    assert act(world, "village_plan_supplies")["outcome"] == "APPLIED"
    assert not state.economy.orders
    village.advance(world, max(5, state.residents["elsie"].available_at - state.minute))
    assert act(world, "village_propose_order", **request)["outcome"] == "REJECTED"
    assert not state.economy.orders and state.coins == coins

    # Visiting the supplier's shop is insufficient when the person is elsewhere.
    assert act(world, "go", target="bakery-tavern")["outcome"] == "APPLIED"
    village.advance(world, state.residents["elsie"].available_at - state.minute)
    assert world.actor.location_id == "bakery"
    assert act(world, "village_propose_order", **request)["outcome"] == "REJECTED"
    assert not state.economy.orders
    assert act(world, "go", target="mill-bakery")["outcome"] == "APPLIED"
    village.advance(world, state.residents["elsie"].available_at - state.minute)
    assert world.actor.location_id == cast["jory"].actor.location_id == "watermill"

    revision = world.revision()
    result = world.act("spoken-order", revision, "village_propose_order", request)
    assert result["outcome"] == "APPLIED", result
    assert world.act("spoken-order", revision, "village_propose_order", request) == result
    assert not state.economy.orders
    notice = next(iter(state.communication.notices.values()))[-1]
    assert (notice.author, notice.terms.recipients, notice.terms.subject, notice.terms.quantity) == (
        "elsie", ["jory"], "bread", 1)
    assert notice.status == "posted"
    assert notice.terms.due_at == 1080 and notice.terms.required_fresh_until == 1140
    assert notice.terms.unit_price is not None
    spoken = state.events[-1]
    assert spoken.actor == "elsie"
    assert {"elsie", "jory"} <= set(spoken.witnesses)
    assert "bread" in spoken.result.lower()
    assert cast["jory"].observe()["village"]["communication"]["notices"]
    assert not cast["torvin"].observe()["village"]["orders"]
    assert not state.dashboard.orders
    village.advance(world, state.residents["jory"].available_at - state.minute)
    response = act(cast["jory"], "village_notice_reply", notice_id=notice.id, expected_version=1,
                   response="accept", channel="conversation", listener="elsie")
    assert response["outcome"] == "APPLIED", response
    order = next(iter(state.economy.orders.values()))
    assert (order.buyer, order.supplier, order.product, order.quantity) == ("elsie", "jory", "bread", 1)
    assert order.status == "accepted"
    assert cast["jory"].observe()["village"]["orders"]
    assert len(state.dashboard.orders) == 1
    loaded = World(value, world.path, "orders", "elsie", "session-elsie")
    assert len(loaded.state.village.economy.orders) == 1


def test_event_wait_reserves_until_personal_deadline_without_advancing_world(tmp_path):
    world = make_world(tmp_path, event_driven_waits=True)
    person = world.state.village.residents["torvin"]
    person.next_work = 540
    result = act(world, "village_wait_event")
    assert result["code"] == "EVENT_WAIT_STARTED"
    assert world.state.village.minute == 480
    assert person.available_at == 540
    assert village_turns.next_actor(world) != "torvin"


def test_event_waiters_wake_only_for_witnessed_news_without_interrupting_sleep(tmp_path):
    world = make_world(tmp_path, event_driven_waits=True)
    person = world.state.village.residents["torvin"]
    person.current_action = "village_wait_event"
    person.available_at = 1140
    world.actor.location_id = "cottages"
    village.event(world, "elsie", "delivery_payment", "tavern", "Fresh bread arrived.", "tavern")
    village_extensions.wake_local_waiters(world)
    assert person.available_at == 1140
    world.actor.location_id = "tavern"
    village.event(world, "elsie", "delivery_payment", "tavern", "Fresh bread arrived.", "tavern")
    village_extensions.wake_local_waiters(world)
    assert person.available_at == 485
    person.available_at = 1140
    person.life.sleeping = True
    village_extensions.wake_local_waiters(world)
    assert person.available_at == 1140


def test_day_policy_configuration_is_revision_checked_and_idempotent(tmp_path):
    world = make_world(tmp_path)
    revision = world.revision()
    patch = {"day_policy": VillageDayPolicy(breakfast=True, require_tea_use=False).model_dump()}
    result = village_extensions.configure(world, "policy-1", revision, patch)
    assert world.state.village.day_policy.breakfast
    assert village_extensions.configure(world, "policy-1", revision, patch) == result
    with pytest.raises(ValueError, match="reused"):
        village_extensions.configure(world, "policy-1", revision, {"day_policy": {"breakfast": False}})
    with pytest.raises(ValueError, match="changed"):
        village_extensions.configure(world, "policy-2", revision, patch)
    loaded = World(world.scenario, world.path, "backlog", "torvin", "session-torvin")
    assert loaded.state.village.day_policy.breakfast


def test_dm_configuration_requires_explicit_authority():
    review = DungeonMasterReview(verdict="continue", findings=[{
        "reason": "A quiet day is appropriate.", "evidence_ids": ["event-1"]
    }], village_configuration={"day_policy": {"event_driven_waits": True}})
    with pytest.raises(ValueError, match="authority"):
        validate_review(review, [{"id": "event-1"}], DungeonMasterConfig())
    config = DungeonMasterConfig(capabilities=["observe_team", "review_progress", "configure_village"])
    validate_review(review, [{"id": "event-1"}], config)


def test_village_configuration_api_validates_policy_and_surfaces_conflicts(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi.testclient import TestClient
    import npc_sim.app as application

    command = AsyncMock(return_value={"configured": ["day_policy"]})
    monkeypatch.setattr(application.app.state, "controller", SimpleNamespace(command=command), raising=False)
    client = TestClient(application.app)
    body = {"run_id": "run", "request_id": "config-1", "expected_revision": "revision",
            "configuration": {"day_policy": {"breakfast": True}}}
    assert client.post("/api/village/configure", json=body).status_code == 200
    assert command.call_args.args[0] == "configure_village"
    assert command.call_args.args[1]["configuration"]["day_policy"]["breakfast"] is True
    body["configuration"]["day_policy"]["breakfast_delay"] = 7
    assert client.post("/api/village/configure", json=body).status_code == 422
    body["configuration"]["day_policy"]["breakfast_delay"] = 30
    command.side_effect = ValueError("Pause before changing village configuration")
    response = client.post("/api/village/configure", json=body)
    assert response.status_code == 409
    assert "Pause" in response.json()["detail"]


@pytest.mark.asyncio
async def test_privileged_configuration_round_trips_through_real_mcp(tmp_path):
    from pathlib import Path
    from npc_sim.mcp_client import WorldClient
    from npc_sim.models import WorldConfig
    from npc_sim.storage import read_json, write_json

    value = scenario()
    scenario_path = tmp_path / "scenario.json"
    state_path = tmp_path / "world.json"
    write_json(scenario_path, value.model_dump(mode="json"))
    client = WorldClient(WorldConfig(transport="stdio", scenario=value.id),
                         Path(__file__).resolve().parent.parent, scenario_path, state_path,
                         "configuration-mcp", "torvin", "session-torvin")
    await client.connect()
    try:
        tool = client.manifest["village_configuration"]
        assert tool == "world_configure_village"
        observed = await client.operation("observe", {})
        request = {"request_id": "dm-policy", "expected_revision": observed["observation_revision"],
                   "configuration": {"day_policy": {"event_driven_waits": True}}}
        result = await client.call(tool, request)
        assert await client.call(tool, request) == result
        saved = read_json(state_path)
        assert saved["village"]["minute"] == value.village.start_minute
        assert saved["village"]["day_policy"]["event_driven_waits"]
        assert "village_wait_event" in {
            action["id"] for action in (await client.operation("observe", {}))["actions"]
        }
    finally:
        await client.close()


def test_living_scenario_enables_every_system_without_changing_legacy(tmp_path):
    from npc_sim.living_village import build_scenario
    base = scenario()
    before = base.model_dump(mode="json")
    value = build_scenario(base)
    assert base.model_dump(mode="json") == before
    assert value.id == "millhaven-living"
    assert value.village.breakfast and value.village.event_driven_waits
    assert value.village.economy.meal_price == 2
    assert value.village.economy.subsidy_per_meal == 0
    assert all("shared physical communication policy" in agent.role for agent in value.agents)
    assert value.village.communication is not None
    assert set(value.village.social.interests) == set(value.village.residents)
    assert value.village.social.bands and len(value.village.social.quests) >= 5
    world = World(value, tmp_path / "living.json", "living", "torvin", "session-torvin")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    village.advance(world, 60)
    state = world.state.village
    assert state.economy is not None and state.social is not None and state.incidents is not None
    assert state.dashboard.orders is not None
    assert state.dashboard.projects and state.dashboard.quests
    for usage in state.dashboard.time_use.values():
        assert usage.sleep + usage.work + usage.travel + usage.wait + usage.leisure + usage.idle == 60
    assert not value.village.require_tea_use
    assert not value.village.require_manure_use

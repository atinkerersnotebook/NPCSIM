import copy

import pytest
from pydantic import ValidationError

from npc_sim import village, village_economy, village_extensions, village_goods, village_research, village_turns
from npc_sim.contracts import observation
from npc_sim.living_village import build_scenario
from npc_sim.models import VillageEconomyConfig, VillageResearchConfig
from npc_sim.village_research_models import ResearchProposal
from npc_sim.world import World
from test_controller import TestProxy, configuration
from test_village_expanded import scenario
from test_world import act


def make_world(tmp_path, actor="jory", **research):
    value = scenario()
    value.village.start_minute = 600
    value.village.research = VillageResearchConfig(**{"required_minutes": 60, "required_successes": 1, **research})
    policy = village_economy.example_config(value)
    policy.update(reserve_targets={}, production_targets={}, stock_buffer={}, meal_plans=[], require_orders=True)
    value.village.economy = VillageEconomyConfig.model_validate(policy)
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "research.json", "research", actor, f"session-{actor}")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    world.state.village.horse_fed_day = 0
    return world


def propose(world, **updates):
    proposal = {"title": "Herb loaf", "description": "A new bread recipe with local flour.",
                "skill": "baking", "kind": "food", "inputs": {"flour": 1},
                "equipment": ["oven"], "duration": 30}
    proposal.update(updates)
    result = act(world, "village_research_propose", **proposal)
    assert result["outcome"] == "APPLIED", result["explanation"]
    village.advance(world, 5)
    return next(reversed(world.state.village.research.projects))


def study(world, key, minutes=15):
    result = act(world, "village_research", project=key)
    assert result["outcome"] == "APPLIED", result["explanation"]
    village.advance(world, minutes)


def discover(world, key, monkeypatch):
    monkeypatch.setattr(village_research, "_trial_success", lambda *_: True)
    for _ in range(4):
        study(world, key)
    assert world.state.village.research.projects[key].discovered_at is not None


def test_proposals_are_occupational_validated_and_create_no_free_items(tmp_path):
    world = make_world(tmp_path)
    before = copy.deepcopy(world.state.village.stocks)
    key = propose(world)
    assert key not in world.state.entities and world.state.village.stocks == before
    assert act(world, "village_research_propose", title="Another loaf", description="An additional bread recipe.",
               skill="baking", kind="food", inputs={"flour": 1}, equipment=["oven"], duration=30)["code"] == "ACTION_UNAVAILABLE"
    assert act(world, "village_research_make", project=key)["code"] == "ACTION_UNAVAILABLE"
    assert village_research.apply(world, "village_research_make", {"project": key})[1] == "DISCOVERY_REQUIRED"
    with pytest.raises(ValidationError):
        ResearchProposal(title="Bogus", description="Some real description.", skill="baking", kind="food",
                         inputs={"flour": True}, duration=15)
    observation(world.observe())


@pytest.mark.parametrize("updates,code", [
    ({"skill": "farming", "kind": "crop"}, "INVALID_ARGUMENTS"),
    ({"inputs": {"imaginary-seed": 1}}, "UNKNOWN_RESEARCH_INPUT"),
    ({"inputs": {"iron": 1}}, "INVALID_FOOD_RECIPE"),
    ({"equipment": []}, "RESEARCH_EQUIPMENT_REQUIRED"),
    ({"title": "   "}, "INVALID_RESEARCH_NAME"),
])
def test_invalid_proposals_have_explicit_rejections(tmp_path, updates, code):
    world = make_world(tmp_path)
    args = {"title": "New loaf", "description": "A bread experiment with local ingredients.", "skill": "baking",
            "kind": "food", "inputs": {"flour": 1}, "equipment": ["oven"], "duration": 30, **updates}
    before = copy.deepcopy(world.state.village.stocks)
    assert act(world, "village_research_propose", **args)["code"] == code
    assert not world.state.village.research.projects and world.state.village.stocks == before


def test_trials_need_real_time_can_fail_and_resume_without_material_refunds(tmp_path, monkeypatch):
    world = make_world(tmp_path)
    key = propose(world)
    project = world.state.village.research.projects[key]
    flour = world.state.village.stocks["bakery"]["flour"]
    monkeypatch.setattr(village_research, "_trial_success", lambda *_: False)
    request, revision = "once", world.revision()
    result = world.act(request, revision, "village_research", {"project": key})
    assert world.act(request, revision, "village_research", {"project": key}) == result
    assert project.worked_minutes == 0 and world.state.village.stocks["bakery"]["flour"] == flour - 1
    village.advance(world, 5)
    assert act(world, "go", target="bakery-tavern")["code"] == "ACTOR_BUSY"
    assert act(world, "village_research_pause")["code"] == "RESEARCH_PAUSED"
    assert project.worked_minutes == 5
    village.advance(world, 5)
    for minutes in [15, 15, 15, 10]:
        study(world, key, minutes)
    assert project.worked_minutes == 60 and project.attempts == 1 and project.successes == 0
    assert project.discovered_at is None and world.state.village.stocks["bakery"]["flour"] == flour - 1
    assert world.state.village.time_use["jory"].work >= 60
    monkeypatch.setattr(village_research, "_trial_success", lambda *_: True)
    for _ in range(4):
        study(world, key)
    assert project.attempts == 2 and project.successes == 1 and project.discovered_at is not None
    assert world.state.village.stocks["bakery"]["flour"] == flour - 2


def test_discovery_unlocks_real_production_and_persistent_freshness(tmp_path, monkeypatch):
    world = make_world(tmp_path)
    key = propose(world)
    discover(world, key, monkeypatch)
    assert key in world.state.entities and world.state.village.stocks["bakery"].get(key, 0) == 0
    assert act(world, "village_research_make", project=key)["outcome"] == "APPLIED"
    assert world.state.village.stocks["bakery"].get(key, 0) == 0
    village.advance(world, 30)
    assert world.state.village.stocks["bakery"][key] == 1
    assert act(world, "village_research_make", project=key)["code"] == "DEMAND_MET"
    lot = world.state.village.stock_lots["bakery"][key][0]
    assert lot.expires_at == world.state.village.minute + 1440
    world.save()
    loaded = World(world.scenario, world.path, "research", "jory", "session-jory")
    assert loaded.state.village.research == world.state.village.research
    assert loaded.state.entities[key] == world.state.entities[key]
    assert village_research.prices(loaded)[key] == loaded.scenario.village.prices["flour"]


def test_discovered_food_can_be_ordered_delivered_shared_and_eaten(tmp_path, monkeypatch):
    world = make_world(tmp_path)
    key = propose(world)
    discover(world, key, monkeypatch)
    act(world, "village_research_make", project=key)
    village.advance(world, 30)
    elsie = world.bind("elsie", "session-elsie")
    assert key not in [p["id"] for p in elsie.observe()["village"]["research"]["projects"]]
    elsie.actor.location_id = "bakery"
    result = act(elsie, "village_propose_order", supplier="jory", product=key,
                 quantity=1, deadline=world.state.village.minute + 120,
                 required_fresh_until=world.state.village.minute + 120)
    assert result["outcome"] == "APPLIED", result["explanation"]
    order = next(iter(world.state.village.economy.orders))
    assert act(world, "village_accept_order", order_id=order)["outcome"] == "APPLIED"
    village.advance(world, 5)
    assert act(world, "village_research_share", project=key, target="elsie")["outcome"] == "APPLIED"
    village.advance(world, 5)
    courier = world.bind("nella", "session-nella")
    courier.actor.location_id = "bakery"
    assert village_goods.load(courier, "nella", {key: 1}, "tavern", courier.state.village.residents["nella"].shipments, 2) is None
    courier.actor.location_id = "tavern"
    assert village_goods.unload(courier, "nella", courier.state.village.residents["nella"].shipments) is None
    elsie.actor.location_id = "tavern"
    assert act(elsie, "village_research_use", project=key)["outcome"] == "APPLIED"
    village.advance(world, 15)
    assert world.state.village.stocks["tavern"]["meals"] == 2
    assert world.state.village.stocks["tavern"][key] == 0
    village.advance(world, 1170 - world.state.village.minute)
    assert act(elsie, "village_eat")["outcome"] == "APPLIED"
    assert world.state.village.stocks["tavern"]["meals"] == 1


def test_crop_and_tool_discoveries_have_executable_effects(tmp_path, monkeypatch):
    world = make_world(tmp_path, "ada")
    key = propose(world, title="Hardy grain", description="A newly studied edible grain variety.",
                  skill="farming", kind="crop", inputs={"grain": 1}, equipment=[], duration=60)
    discover(world, key, monkeypatch)
    assert act(world, "village_research_make", project=key)["outcome"] == "APPLIED"
    village.advance(world, 60)
    assert world.state.village.stocks["farm"][key] == 1
    smith = world.bind("torvin", "session-torvin")
    key = propose(smith, title="Fitting service kit", description="A new iron kit for servicing smithing tools.",
                  skill="smithing", kind="tool", inputs={"iron": 1}, equipment=["anvil"], duration=15)
    discover(smith, key, monkeypatch)
    act(smith, "village_research_make", project=key)
    village.advance(world, 15)
    world.state.entities["anvil"].health = 70
    world.state.entities["anvil"].max_health = 100
    assert act(smith, "village_research_use", project=key, target="anvil")["outcome"] == "APPLIED"
    assert world.state.entities["anvil"].health == 70
    village.advance(world, 15)
    assert world.state.entities["anvil"].health == 95


def test_research_priority_and_physical_guards(tmp_path):
    world = make_world(tmp_path)
    assert village_research.action_blocker(world, "village_wait_event", "jory")[0] == "RESEARCH_PROPOSAL_DUE"
    key = propose(world)
    assert village_research.action_blocker(world, "village_wait_event", "jory")[0] == "RESEARCH_AVAILABLE"
    world.actor.location_id = "tavern"
    assert act(world, "village_research", project=key)["code"] == "NOT_PRESENT"
    world.actor.location_id = "bakery"
    world.state.village.minute = 1015
    assert act(world, "village_research", project=key)["code"] == "OUTSIDE_RESEARCH_HOURS"
    world.state.village.minute = 605
    world.state.entities["oven"].location = "tavern"
    assert act(world, "village_research", project=key)["code"] == "MISSING_RESEARCH_TOOLS"
    world.state.entities["oven"].location = "bakery"
    world.state.village.economy.config.reserve_targets["bakery"] = {"flour": 6}
    assert act(world, "village_research", project=key)["code"] == "RESEARCH_SUPPLIES"
    suggestions = village_economy.suggestions(world)
    assert any(item["product"] == "flour" for item in suggestions)


def test_midnight_preserves_partial_research_without_duplicate_projects(tmp_path):
    world = make_world(tmp_path)
    key = propose(world)
    study(world, key)
    village.advance(world, 1440 - world.state.village.minute)
    assert list(world.state.village.research.projects) == [key]
    assert world.state.village.research.projects[key].worked_minutes == 15
    assert village_turns.next_actor(world, stop_at_minute=1440) is None
    loaded = World(world.scenario, world.path, "research", "jory", "session-jory")
    assert loaded.state.village.research.projects[key].trial_materials_consumed


def test_configuration_is_atomic_and_legacy_stays_opt_out(tmp_path):
    world = make_world(tmp_path)
    before = world.state.model_dump(mode="json")
    with pytest.raises(ValueError):
        village_extensions.configure(world, "bad-research", world.revision(), {"research": {"required_minutes": 0}})
    assert world.state.model_dump(mode="json") == before
    assert scenario().village.research is None
    assert build_scenario(scenario()).village.research.enabled


def test_default_thresholds_require_both_time_and_successes(tmp_path, monkeypatch):
    world = make_world(tmp_path, required_minutes=240, required_successes=3)
    key = propose(world)
    project = world.state.village.research.projects[key]
    monkeypatch.setattr(village_research, "_trial_success", lambda *_: True)
    for _ in range(12):
        study(world, key)
    assert project.worked_minutes == 180 and project.successes == 3 and project.discovered_at is None
    for _ in range(4):
        study(world, key)
    assert project.worked_minutes == 240 and project.discovered_at is not None


def test_real_trial_function_has_both_successes_and_failures(tmp_path):
    world = make_world(tmp_path)
    key = propose(world)
    project = world.state.village.research.projects[key]
    outcomes = []
    for attempt in range(1, 30):
        project.attempts = attempt
        outcomes.append(village_research._trial_success(world, project))
    assert any(outcomes) and not all(outcomes)
    assert village_research._trial_success(world, project) == village_research._trial_success(world, project)


def test_research_does_not_displace_maintenance_meals_orders_or_needed_work(tmp_path):
    from npc_sim.village_economy_models import VillageSupplierOrder
    from npc_sim import village_maintenance
    world = make_world(tmp_path)
    key = propose(world)
    world.state.village.economy.orders["commitment"] = VillageSupplierOrder(
        id="commitment", buyer="elsie", supplier="jory", source="bakery", destination="tavern", product="bread",
        quantity=2, price=2, delivery_fee=1, created_at=605, deadline=900, required_fresh_until=1000,
        request_key="test", status="accepted")
    assert act(world, "village_research", project=key)["code"] == "ORDER_COMMITTED"
    world.state.village.economy.orders.clear()
    village_maintenance.configure(world, {"tasks": {"oven-service": {
        "title": "Service the oven", "assignee": "jory", "location": "bakery", "equipment": ["oven"], "release_minute": 0}}})
    assert act(world, "village_research", project=key)["code"] == "MAINTENANCE_DUE"
    world.state.village.maintenance.config.enabled = False
    world.state.village.economy.config.production_targets["bakery"] = {"bread": 100}
    assert act(world, "village_research", project=key)["code"] == "WORK_DEMAND_DUE"
    world.state.village.economy.config.production_targets.clear()
    world.actor.body.capabilities.remove("manipulate")
    assert act(world, "village_research", project=key)["code"] == "MISSING_CAPABILITY"
    world.actor.body.capabilities.append("manipulate")
    village.advance(world, 1140 - world.state.village.minute)
    assert act(world, "village_research", project=key)["code"] == "MEAL_DUE"


def test_material_discoveries_can_feed_new_recipes_and_reading_teaches_real_recipe(tmp_path, monkeypatch):
    world = make_world(tmp_path, "torvin")
    key = propose(world, title="Tempered fitting", description="A studied fitting made from actual iron.",
                  skill="smithing", kind="material", inputs={"iron": 1}, equipment=["anvil"], duration=15)
    discover(world, key, monkeypatch)
    assert act(world, "village_research_make", project=key)["outcome"] == "APPLIED"
    village.advance(world, 15)
    next_key = propose(world, title="Improved service kit", description="A repair kit using the discovered fittings.",
                       skill="smithing", kind="tool", inputs={key: 1}, equipment=["anvil"], duration=15)
    study(world, next_key)
    assert world.state.village.stocks["forge"][key] == 0
    assert world.state.village.research.projects[next_key].trial_materials_consumed
    reader = world.bind("hobb", "session-hobb")
    reader.actor.location_id = "forge"
    assert act(reader, "read", target=key)["outcome"] == "APPLIED"
    assert key in world.state.village.research.known["hobb"]
    assert act(reader, "village_research_make", project=key)["code"] in {"RESEARCH_SKILL_REQUIRED", "RESEARCH_COMMITTED"}


def test_guard_research_preserves_scheduled_patrols(tmp_path):
    world = make_world(tmp_path, "garrick")
    args = {"title": "Watchpost service kit", "description": "A wooden kit for maintaining watchpost equipment.",
            "skill": "patrol", "kind": "tool", "inputs": {"planks": 1}, "equipment": [], "duration": 15}
    assert act(world, "village_research_propose", **args)["code"] == "PATROL_DUE"
    assert act(world, "village_patrol")["outcome"] == "APPLIED"
    village.advance(world, 5)
    assert act(world, "village_research_propose", **args)["outcome"] == "APPLIED"
    world.state.village.minute = world.state.village.residents["garrick"].next_work - 5
    key = next(iter(world.state.village.research.projects))
    assert act(world, "village_research", project=key)["code"] == "PATROL_DUE"


@pytest.mark.asyncio
async def test_runtime_research_survives_episode_save_load_without_model_calls(configuration):
    from npc_sim.controller import Controller
    from test_controller import ROOT
    from npc_sim.episode_saves import read_save
    data, config = configuration
    config.world.scenario = "millhaven-expanded"
    instance = Controller(ROOT, data, config)
    proxy = TestProxy(instance.proxy, [])
    instance.proxy = proxy
    await instance.start_worker()
    try:
        await instance.command("start")
        state = instance.run
        await instance.command("configure_village", {
            "run_id": state["id"], "request_id": "enable-research", "expected_revision": instance.observation["observation_revision"],
            "configuration": {"research": {"enabled": True}}})
        saved = await instance.command("save_episode", {"run_id": state["id"], "name": "Research enabled"})
        assert read_save(data, saved["save"]["id"]).world.village.research is not None
        await instance.command("load_episode", {"save_id": saved["save"]["id"], "current_run_id": state["id"]})
        assert instance.observation["village"]["research"]["enabled"]
        assert instance.run["model_calls"] == 0 and not proxy.contexts
    finally:
        await instance.shutdown()

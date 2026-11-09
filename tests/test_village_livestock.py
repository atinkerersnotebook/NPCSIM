import copy

import pytest

from npc_sim import village, village_economy, village_extensions, village_goods, village_livestock, village_services
from npc_sim.models import VillageEconomyConfig
from npc_sim.world import World
from test_village_expanded import scenario
from test_world import act
from test_controller import configuration


def make_world(tmp_path, enabled=True):
    value = scenario()
    value.village.start_minute = 600
    value.village.economy = VillageEconomyConfig.model_validate(village_economy.example_config(value))
    value.village.stocks["farm"] = {"grain": 8, "hay": 3, "manure": 0}
    value.village.stocks["stable"]["hay"] = 0
    if enabled:
        village_livestock.install_scenario(value)
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "livestock.json", "livestock", "ada", "session-ada")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    return world


def perform(world, action, minutes=5, **args):
    result = act(world, action, **args)
    assert result["outcome"] == "APPLIED", (result["code"], result["explanation"])
    village.advance(world, minutes)
    return result


def water(world):
    perform(world, "take", target=village_livestock.BUCKET)
    world.actor.location_id = world.scenario.village.well_site
    world.state.village.well_dug = True
    perform(world, "village_draw_livestock_water")
    assert world.state.village.livestock.water == 2
    world.actor.location_id = "farm"


def care(world):
    water(world)
    perform(world, "village_care_cow", minutes=15)
    perform(world, "village_care_hens", minutes=10)


def reserves(world, milk=12, eggs=12):
    world.state.village.economy.config.reserve_targets.setdefault("farm", {}).update(milk=milk, eggs=eggs)


def test_real_animals_care_clock_sessions_and_food_capacity(tmp_path):
    world = make_world(tmp_path)
    reserves(world)
    state = world.state.village
    assert len(state.residents) == 12 and len(world.state.actors) == 12
    assert len([key for key in world.state.entities if key.startswith("farm-hen-")]) == 12
    assert state.stocks["farm"]["milk"] == state.stocks["farm"]["eggs"] == 0
    assert act(world, "village_milk_cow")["code"] == "LIVESTOCK_CARE_REQUIRED"
    care(world)
    assert state.stocks["farm"]["hay"] == 2 and state.stocks["farm"]["grain"] == 5
    assert state.stocks["farm"]["manure"] == 2 and state.livestock.water == 0
    assert act(world, "village_care_cow")["code"] == "CARE_DONE"
    assert act(world, "village_milk_cow")["code"] == "LIVESTOCK_STARTED"
    assert state.stocks["farm"]["milk"] == 0
    assert act(world, "go", target="farm-mill")["code"] == "ACTOR_BUSY"
    village.advance(world, 10)
    assert state.stocks["farm"]["milk"] == 0
    village.advance(world, 5)
    assert state.stocks["farm"]["milk"] == 6
    assert act(world, "village_milk_cow")["code"] == "MILKING_SESSION_USED"
    perform(world, "village_collect_eggs", minutes=10)
    assert state.stocks["farm"]["eggs"] == 12
    assert act(world, "village_collect_eggs")["code"] == "EGGS_COLLECTED_TODAY"
    village.advance(world, 780 - state.minute)
    assert act(world, "village_milk_cow")["code"] == "MILKING_RECOVERY_TIME"
    previous = next(chunk for chunk in state.livestock.chunks if chunk.operation == "milk")
    village.advance(world, previous.start + 240 - state.minute)
    perform(world, "village_milk_cow", minutes=15)
    assert state.stocks["farm"]["milk"] == 12
    assert state.time_use["ada"].work >= 70
    world.save()
    loaded = World(world.scenario, world.path, "livestock", "ada", "session-ada")
    assert loaded.state.village.livestock == state.livestock
    assert loaded.state.village.stock_lots["farm"]["milk"] == state.stock_lots["farm"]["milk"]


def test_no_feed_water_tools_body_or_remote_work_and_horse_protection(tmp_path):
    world = make_world(tmp_path)
    assert act(world, "village_care_cow")["code"] == "WATER_BUCKET_REQUIRED"
    perform(world, "take", target=village_livestock.BUCKET)
    assert act(world, "village_care_cow")["code"] == "LIVESTOCK_WATER_REQUIRED"
    world.actor.location_id = world.scenario.village.well_site
    assert act(world, "village_draw_livestock_water")["code"] == "NO_USABLE_WATER"
    world.state.village.well_dug = True
    perform(world, "village_draw_livestock_water")
    assert act(world, "village_care_cow")["code"] == "LIVESTOCK_NOT_PRESENT"
    world.actor.location_id = "farm"
    village_goods.take(world, "farm", "hay", 2)
    assert act(world, "village_care_cow")["code"] == "LIVESTOCK_FEED_REQUIRED"
    assert world.state.village.livestock.water == 2
    village_goods.put(world, "farm", "hay", 1)
    world.state.entities[village_livestock.BRUSH].health = 0
    assert act(world, "village_care_cow")["code"] == "LIVESTOCK_TOOL_REQUIRED"
    world.state.entities[village_livestock.BRUSH].health = 100
    world.actor.body.grips = ["paw"]
    assert act(world, "village_care_cow")["code"] == "LIVESTOCK_BODY_REQUIREMENT"
    world.actor.body.grips = ["hand"]
    world.actor.body.capabilities.remove("manipulate")
    assert act(world, "village_care_cow")["code"] == "LIVESTOCK_BODY_REQUIREMENT"
    world.actor.body.capabilities.append("manipulate")
    perform(world, "village_care_cow", minutes=15)
    assert world.state.village.stocks["farm"]["hay"] == 1
    assert world.state.village.horse_fed_day < 0


def test_interrupted_care_resumes_without_duplicate_feed_or_unearned_manure(tmp_path):
    world = make_world(tmp_path)
    water(world)
    before = copy.deepcopy(world.state.village.stocks["farm"])
    act(world, "village_care_cow")
    village.advance(world, 5)
    assert act(world, "village_livestock_pause")["code"] == "LIVESTOCK_PAUSED"
    state = world.state.village
    assert state.livestock.cow.progress == 5 and state.stocks["farm"]["manure"] == 0
    village.advance(world, 5)
    perform(world, "village_care_cow", minutes=10)
    assert state.stocks["farm"]["hay"] == before["hay"] - 1
    assert state.stocks["farm"]["manure"] == 1
    assert state.livestock.water == 1
    reserves(world, milk=6)
    act(world, "village_milk_cow")
    village.advance(world, 5)
    world.state.entities["farm-cow"].location = "stable"
    village.advance(world, 5)
    assert state.livestock.chunks[-1].status == "interrupted"
    assert state.stocks["farm"]["milk"] == 0
    world.state.entities["farm-cow"].location = "farm"
    assert act(world, "village_milk_cow")["code"] == "MILKING_SESSION_USED"


def test_no_unsold_output_daily_independent_care_and_freshness(tmp_path):
    world = make_world(tmp_path)
    care(world)
    assert act(world, "village_milk_cow")["code"] == "DEMAND_MET"
    assert act(world, "village_collect_eggs")["code"] == "DEMAND_MET"
    reserves(world, milk=1, eggs=2)
    perform(world, "village_milk_cow", minutes=15)
    perform(world, "village_collect_eggs", minutes=10)
    state = world.state.village
    assert state.stocks["farm"]["milk"] == 1 and state.stocks["farm"]["eggs"] == 2
    assert state.stock_lots["farm"]["milk"][0].expires_at == state.stock_lots["farm"]["milk"][0].produced_at + 1440
    village.advance(world, 1440 - state.minute)
    assert not state.livestock.cow.fed and not state.livestock.hens.cleaned
    assert state.livestock.cow.milk_sessions == [] and state.livestock.hens.eggs == 0
    village.advance(world, 600)
    state.residents["ada"].life.last_woke = 1440 + world.scenario.village.residents["ada"].wake_minute
    assert act(world, "village_milk_cow")["code"] == "LIVESTOCK_CARE_REQUIRED"
    assert state.stocks["farm"]["milk"] <= 1


def test_config_preserves_existing_world_and_revision_and_reloads(tmp_path):
    world = make_world(tmp_path, enabled=False)
    state = world.state.village
    world.save()
    old = world.state.model_copy(deep=True)
    result = village_extensions.configure(world, "livestock-install", world.revision(),
                                           {"livestock": {"keeper": "ada", "hens": 12}})
    assert result["configured"] == ["livestock"]
    assert state.minute == old.village.minute and state.coins == old.village.coins
    assert state.residents == old.village.residents
    for key, entity in old.entities.items():
        assert world.state.entities[key] == entity
    for site, stock in old.village.stocks.items():
        for resource, quantity in stock.items():
            assert state.stocks[site][resource] == quantity
    assert state.stocks["farm"]["milk"] == state.stocks["farm"]["eggs"] == 0
    assert state.stock_lots["farm"]["milk"] == []
    loaded = World(world.scenario, world.path, "livestock", "ada", "session-ada")
    assert loaded.state.village.livestock.config.keeper == "ada"
    with pytest.raises(ValueError, match="world changed"):
        village_extensions.configure(world, "stale", old.world_id, {"livestock": {"keeper": "ada"}})


def test_in_person_funded_delivery_cooking_consumption_and_finite_coins(tmp_path):
    world = make_world(tmp_path)
    care(world)
    state = world.state.village
    inn = world.bind("elsie", "session-elsie")
    inn.actor.location_id = "farm"
    before = copy.deepcopy(state.coins)
    for product in ["milk", "eggs"]:
        perform(inn, "village_propose_order", supplier="ada", product=product, quantity=1,
                deadline=1100, required_fresh_until=1140)
        order = list(state.economy.orders.values())[-1]
        perform(world, "village_accept_order", order_id=order.id)
    perform(world, "village_milk_cow", minutes=15)
    perform(world, "village_collect_eggs", minutes=10)
    assert state.stocks["farm"]["milk"] == state.stocks["farm"]["eggs"] == 1
    courier = world.bind("nella", "session-nella")
    courier.actor.location_id = "farm"
    perform(courier, "village_load_goods", goods={"milk": 1, "eggs": 1}, destination="tavern")
    assert state.stocks["tavern"]["milk"] == 0 and state.coins == before
    courier.actor.location_id = "tavern"
    perform(courier, "village_unload_goods")
    assert all(order.status == "fulfilled" for order in state.economy.orders.values())
    assert state.coins["ada"] == before["ada"] + 4
    assert sum(state.coins.values()) == sum(before.values())
    inn.actor.location_id = "tavern"
    village.advance(world, 1080 - state.minute)
    meals_before = state.stocks["tavern"]["meals"]
    perform(inn, "village_cook_dairy", minutes=15)
    assert state.stocks["tavern"]["milk"] == state.stocks["tavern"]["eggs"] == 0
    assert state.stocks["tavern"]["meals"] == meals_before + 2
    village.advance(world, 1140 - state.minute)
    world.actor.location_id = "tavern"
    perform(world, "village_eat")
    assert sum(state.coins.values()) == sum(before.values())
    assert state.residents["ada"].life.meals["0:daily"].status == "eaten"
    assert state.stocks["tavern"]["meals"] == meals_before + 1


def test_real_bucket_and_water_source_interruption_cannot_create_water(tmp_path):
    world = make_world(tmp_path)
    perform(world, "take", target=village_livestock.BUCKET)
    world.actor.location_id = world.scenario.village.well_site
    world.state.village.well_dug = True
    assert act(world, "village_draw_livestock_water")["outcome"] == "APPLIED"
    assert world.state.village.livestock.water == 0
    world.state.village.well_dug = False
    village.advance(world, 5)
    assert world.state.village.livestock.chunks[-1].status == "interrupted"
    assert world.state.village.livestock.water == 0
    world.state.village.well_dug = True
    world.state.entities[world.scenario.village.well_site].hidden = True
    assert act(world, "village_draw_livestock_water")["code"] == "NO_USABLE_WATER"
    world.state.entities[world.scenario.village.well_site].hidden = False
    perform(world, "village_draw_livestock_water")
    assert act(world, "village_draw_livestock_water")["code"] == "BUCKET_HAS_WATER"


def test_active_reservations_missing_grain_and_clock_window(tmp_path):
    world = make_world(tmp_path)
    water(world)
    act(world, "village_care_cow")
    assert village_livestock.equipment_reserved(world, {village_livestock.BRUSH})
    helper = world.bind("torvin", "session-torvin")
    helper.actor.location_id = "farm"
    result = act(helper, "take", target=village_livestock.BRUSH)
    assert result["outcome"] == "REJECTED" and "reserved" in result["explanation"]
    assert world.state.entities[village_livestock.BRUSH].location == "farm"
    village.advance(world, 15)
    assert not village_livestock.equipment_reserved(world, {village_livestock.BRUSH})
    village_goods.take(world, "farm", "grain", 8)
    assert act(world, "village_care_hens")["code"] == "LIVESTOCK_FEED_REQUIRED"
    assert world.state.village.livestock.water == 1
    village_goods.put(world, "farm", "grain", 3)
    perform(world, "village_work")
    village.advance(world, world.state.village.residents["ada"].next_work - world.state.village.minute)
    perform(world, "village_work")
    perform(world, "village_care_hens", minutes=10)
    reserves(world)
    village.advance(world, 1080 - world.state.village.minute)
    assert act(world, "village_milk_cow")["code"] == "LIVESTOCK_SESSION_TIME"
    assert act(world, "village_collect_eggs")["code"] == "LIVESTOCK_SESSION_TIME"


def test_independent_missed_care_recovery_and_feed_planning(tmp_path):
    world = make_world(tmp_path)
    targets = village_economy.production_targets(world, "farm")
    assert targets["grain"] >= 3 and targets["hay"] >= 1
    water(world)
    perform(world, "village_care_cow", minutes=15)
    reserves(world, milk=2, eggs=2)
    perform(world, "village_milk_cow", minutes=15)
    assert act(world, "village_collect_eggs")["code"] == "LIVESTOCK_CARE_REQUIRED"
    perform(world, "village_care_hens", minutes=10)
    perform(world, "village_collect_eggs", minutes=10)
    assert world.state.village.stocks["farm"]["eggs"] == 2
    assert village_livestock.action_blocker(world, "village_research", "ada") is None
    village.advance(world, 1440 - world.state.village.minute)
    assert village_livestock.action_blocker(world, "village_research", "ada")[0] == "LIVESTOCK_CARE_DUE"
    village.advance(world, 600)
    world.state.village.residents["ada"].life.last_woke = 1440 + world.scenario.village.residents["ada"].wake_minute
    world.actor.location_id = world.scenario.village.well_site
    from npc_sim import village_life
    factor = village_life.cooldown_factor(world, "ada")
    perform(world, "village_draw_livestock_water", minutes=5 * factor)
    world.actor.location_id = "farm"
    perform(world, "village_care_cow", minutes=15 * factor)
    assert world.state.village.livestock.cow.cleaned
    assert not world.state.village.livestock.hens.cleaned


def test_configuration_is_atomic_idempotent_and_rejects_identity_conflicts(tmp_path):
    world = make_world(tmp_path, enabled=False)
    revision = world.revision()
    patch = {"livestock": {"keeper": "ada", "hens": 12}}
    result = village_extensions.configure(world, "once", revision, patch)
    before = world.state.model_copy(deep=True)
    assert village_extensions.configure(world, "once", revision, patch) == result
    assert world.state == before
    with pytest.raises(ValueError, match="flock"):
        village_extensions.configure(world, "different-flock", world.revision(),
                                     {"livestock": {"keeper": "ada", "hens": 13}})
    assert world.state == before
    perform(world, "take", target="compost-fork")
    fork = world.state.entities["compost-fork"].model_copy(deep=True)
    actors = copy.deepcopy(world.state.actors)
    village_extensions.configure(world, "disable-held-tool", world.revision(),
                                 {"livestock": {"keeper": "ada", "hens": 12, "enabled": False}})
    assert world.state.entities["compost-fork"] == fork
    assert world.state.actors == actors
    other = make_world(tmp_path / "conflict", enabled=False)
    from npc_sim.models import Entity
    other.state.entities["farm-cow"] = Entity(name="Existing crate", kind="item", location="farm", description="Original")
    old = other.state.model_copy(deep=True)
    with pytest.raises(ValueError, match="conflicts"):
        village_extensions.configure(other, "conflict", other.revision(), patch)
    assert other.state == old


@pytest.mark.asyncio
async def test_optional_runtime_livestock_survives_named_episode_save_load(configuration):
    from npc_sim.controller import Controller
    from npc_sim.episode_saves import read_save
    from npc_sim.models import WorldState
    from npc_sim.storage import read_json
    from test_controller import ROOT, TestProxy
    data, config = configuration
    config.world.scenario = "millhaven-expanded"
    instance = Controller(ROOT, data, config)
    proxy = TestProxy(instance.proxy, [])
    instance.proxy = proxy
    await instance.start_worker()
    try:
        await instance.command("start")
        await instance.command("pause")
        run = copy.deepcopy(instance.run)
        path = data / "worlds" / run["world_id"] / "world-state.json"
        original = WorldState.model_validate(read_json(path))
        actors = original.actors
        state = original.village
        request = {"run_id": run["id"], "request_id": "enable-livestock",
                   "expected_revision": instance.observation["observation_revision"],
                   "configuration": {"livestock": {"keeper": "ada", "hens": 12}}}
        await instance.command("configure_village", request)
        configured = WorldState.model_validate(read_json(path))
        assert instance.run["status"] == run["status"] == "paused"
        assert configured.actors == actors
        assert configured.village.residents == state.residents
        assert configured.village.minute == state.minute
        assert configured.village.coins == state.coins
        for field in ["well_dug", "repaired", "horse_fed_day", "tasks", "wagon", "deadlines", "maintenance", "research", "mining"]:
            assert getattr(configured.village, field) == getattr(state, field)
        saved = await instance.command("save_episode", {"run_id": run["id"], "name": "Livestock enabled"})
        assert read_save(data, saved["save"]["id"]).world.village.livestock is not None
        await instance.command("load_episode", {"save_id": saved["save"]["id"], "current_run_id": run["id"]})
        assert instance.observation["village"]["livestock"]["hen_count"] == 12
        assert instance.run["model_calls"] == 0 and not proxy.contexts
    finally:
        await instance.shutdown()


def test_funded_grain_deadline_precedes_optional_dairy_without_stealing_feed(tmp_path):
    world = make_world(tmp_path)
    care(world)
    reserves(world)
    state = world.state.village
    village_goods.take(world, "farm", "grain", 5)
    miller_id = world.scenario.village.miller
    miller = world.bind(miller_id, f"session-{miller_id}")
    miller.actor.location_id = "farm"
    perform(miller, "village_propose_order", supplier="ada", product="grain", quantity=2,
            deadline=state.minute + 30, required_fresh_until=state.minute + 60)
    order = list(state.economy.orders.values())[-1]
    perform(world, "village_accept_order", order_id=order.id)
    state.residents["ada"].next_work = state.minute + 5
    assert act(world, "village_milk_cow")["code"] == "FUNDED_GRAIN_FIRST"
    assert state.stocks["farm"]["grain"] == 0 and state.stocks["farm"]["milk"] == 0
    village.advance(world, 5)
    perform(world, "village_work")
    assert state.stocks["farm"]["grain"] == 2
    courier = world.bind("nella", "session-nella")
    courier.actor.location_id = "farm"
    perform(courier, "village_load_goods", goods={"grain": 2}, destination="watermill")
    perform(courier, "go", target="farm-mill")
    perform(courier, "village_unload_goods")
    assert order.status == "fulfilled" and state.stocks["farm"]["grain"] == 0
    perform(world, "village_milk_cow", minutes=15)
    assert state.stocks["farm"]["grain"] == 0 and state.stocks["farm"]["milk"] == 6


def test_earned_unfinished_cleaning_carries_but_new_day_needs_new_feed_and_water(tmp_path):
    from npc_sim import village_life
    world = make_world(tmp_path)
    water(world)
    act(world, "village_care_cow")
    village.advance(world, 5)
    act(world, "village_livestock_pause")
    state = world.state.village
    assert state.livestock.cow.progress == 5 and state.stocks["farm"]["manure"] == 0
    village.advance(world, 1440 - state.minute)
    assert state.livestock.cow.progress == 5
    assert not state.livestock.cow.fed and not state.livestock.cow.watered
    village.advance(world, 600)
    state.residents["ada"].life.last_woke = 1440 + world.scenario.village.residents["ada"].wake_minute
    minutes = 15 * village_life.cooldown_factor(world, "ada") - 5
    perform(world, "village_care_cow", minutes=minutes)
    assert state.livestock.cow.cleaned and state.livestock.cow.progress == 0
    assert state.stocks["farm"]["hay"] == 1 and state.livestock.water == 0
    assert state.stocks["farm"]["manure"] == 1


def test_sleep_meal_and_changed_body_interruptions_are_protected(tmp_path):
    world = make_world(tmp_path)
    care(world)
    reserves(world)
    state = world.state.village
    act(world, "village_milk_cow")
    world.actor.body.capabilities.remove("manipulate")
    village.advance(world, 5)
    assert state.livestock.chunks[-1].status == "interrupted"
    assert state.stocks["farm"]["milk"] == 0
    world.actor.body.capabilities.append("manipulate")
    village.advance(world, 1130 - state.minute)
    assert act(world, "village_collect_eggs")["code"] == "MEAL_CONFLICT"
    village.advance(world, 10)
    assert act(world, "village_collect_eggs")["code"] == "MEAL_DUE"
    state.residents["ada"].life.sleeping = True
    state.residents["ada"].life.sleep_until = 1440 + 480
    assert act(world, "village_care_cow")["code"] == "ASLEEP"


def test_daily_care_is_required_only_from_activation_day_and_not_backdated(tmp_path):
    from npc_sim import village_ai
    world = make_world(tmp_path, enabled=False)
    world.state.village.minute = 2880
    village_extensions.configure(world, "day-three", world.revision(), {"livestock": {"keeper": "ada"}})
    assert world.state.village.livestock.started_day == 2
    assert "livestock_daily_care" not in village_ai.evaluate_goal(world)["evidence"]["checks"]
    world.state.village.minute = 4020
    result = village_ai.evaluate_goal(world)
    assert result["evidence"]["checks"]["livestock_daily_care"] is False
    assert result["evidence"]["livestock_care"] == {"cow": False, "hens": False}


def test_uncommitted_horse_hay_is_planned_before_cow_feed_so_one_unit_cannot_soft_lock(tmp_path):
    world = make_world(tmp_path)
    village_goods.take(world, "farm", "hay", 2)
    assert world.state.village.stocks["farm"]["hay"] == 1
    assert village_livestock.feed_needs(world)["hay"] == 2
    assert village_economy.production_targets(world, "farm")["hay"] >= 2
    perform(world, "village_grow_hay")
    assert world.state.village.stocks["farm"]["hay"] >= 2


def test_required_hen_grain_can_finish_an_indivisible_two_unit_harvest_without_extra_batches(tmp_path):
    world = make_world(tmp_path)
    village_goods.take(world, "farm", "grain", 2)
    assert world.state.village.stocks["farm"]["grain"] == 6
    assert village_economy.production_targets(world, "farm")["grain"] == 7
    perform(world, "village_work")
    assert world.state.village.stocks["farm"]["grain"] == 8
    water(world)
    perform(world, "village_care_hens", minutes=10)
    assert world.state.village.stocks["farm"]["grain"] == 5
    village.advance(world, world.state.village.residents["ada"].next_work - world.state.village.minute)
    assert act(world, "village_work")["code"] == "DEMAND_MET"

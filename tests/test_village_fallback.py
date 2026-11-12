import copy

import pytest

from npc_sim import village, village_economy, village_extensions, village_fallback, village_goods, village_life, village_turns
from npc_sim.contracts import observation
from npc_sim.models import VillageDayPolicy, VillageEconomyConfig
from npc_sim.world import World
from test_village_expanded import scenario
from test_world import act


def make_world(tmp_path, *, minute=3480, breakfast=False, enabled=True):
    value = scenario()
    value.village.start_minute = minute
    value.village.anti_idle = enabled
    value.village.event_driven_waits = True
    value.village.breakfast = breakfast
    value.village.goal_deadlines = {}
    policy = village_economy.example_config(value)
    policy.update(reserve_targets={}, production_targets={"woods": {"timber": 2, "logs": 2}},
                  stock_buffer={}, meal_plans=[], require_orders=True)
    value.village.economy = VillageEconomyConfig.model_validate(policy)
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, tmp_path / "fallback.json", "fallback", "rowan", "session-rowan")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    state = world.state.village
    state.breakdown = True
    world.state.entities[value.village.mill_equipment].health = 0
    for stock in state.stocks.values():
        for product in stock:
            stock[product] = 0
    state.stock_lots = {}
    village_goods.put(world, "woods", "timber", 20)
    village_goods.put(world, "woods", "logs", 20)
    return world


def inspect_then_wait(world):
    assert act(world, "village_observe")["code"] == "LOCAL_TOOLS_CHECKED"
    village.advance(world, 5)
    result = act(world, "village_wait_event")
    assert result["code"] == "EVENT_WAIT_STARTED", result
    return world.state.village.residents["rowan"].fallback.wait


def test_rowan_day_three_empty_breakfast_is_attempted_once_and_penalty_is_not_duplicated(tmp_path):
    world = make_world(tmp_path, minute=3330, breakfast=True)
    world.actor.location_id = "tavern"
    life = world.state.village.residents["rowan"].life
    first = act(world, "village_eat")
    assert first["code"] == "MEAL_MISSED"
    village.advance(world, 5)
    world.state.entities["tavern-hearth"].description += " A different local observation."
    for _ in range(3):
        assert act(world, "village_eat")["code"] == "RETRY_SUPPRESSED"
    assert life.meals["2:breakfast"].status == "missed"
    assert life.missed_meals == 1
    village.advance(world, 100)
    assert life.missed_meals == 1
    assert sum(event.action == "missed_meal" and event.actor == "rowan"
               for event in world.state.village.events) == 1
    block = next(iter(world.state.village.residents["rowan"].fallback.blocked.values()))
    assert block.engine_code == "MEAL_MISSED" and block.attempt_count_since_change == 4


def test_real_local_food_reopens_meal_attempt_and_preserves_existing_late_recovery(tmp_path):
    world = make_world(tmp_path, minute=3330, breakfast=True)
    world.actor.location_id = "tavern"
    assert act(world, "village_eat")["code"] == "MEAL_MISSED"
    village.advance(world, 5)
    coins = world.state.village.coins["rowan"]
    village_goods.put(world, "tavern", "bread", 1)
    assert act(world, "village_eat")["code"] == "MEAL_EATEN"
    life = world.state.village.residents["rowan"].life
    assert life.meals["2:breakfast"].status == "eaten"
    assert life.missed_meals == 0
    assert world.state.village.coins["rowan"] < coins
    assert world.state.village.stocks["tavern"]["bread"] == 0


def test_unfunded_food_is_not_an_eligible_meal_and_funding_changes_reopen_the_check(tmp_path):
    world = make_world(tmp_path, minute=3330, breakfast=True)
    world.actor.location_id = "tavern"
    state = world.state.village
    village_goods.put(world, "tavern", "bread", 1)
    state.coins["rowan"] = 0
    assert not any(choice["action_id"] == "village_eat" for choice in village_fallback.candidates(world))
    assert act(world, "village_eat")["code"] == "MEAL_MISSED"
    block = next(iter(state.residents["rowan"].fallback.blocked.values()))
    assert block.reason_code == "PAYMENT_INSUFFICIENT"
    assert state.stocks["tavern"]["bread"] == 1
    village.advance(world, 5)
    state.coins["rowan"] = 10
    assert act(world, "village_eat")["code"] == "MEAL_EATEN"


def test_innkeeper_arrival_reopens_blocked_meal(tmp_path):
    world = make_world(tmp_path, minute=3330, breakfast=True)
    world.actor.location_id = "tavern"
    village_goods.put(world, "tavern", "bread", 1)
    world.state.actors["elsie"].location_id = "cottages"
    assert not any(choice["action_id"] == "village_eat" for choice in village_fallback.candidates(world))
    assert act(world, "village_eat")["code"] == "INN_NOT_OPEN"
    village.advance(world, 5)
    assert act(world, "village_eat")["code"] == "RETRY_SUPPRESSED"
    world.state.actors["elsie"].location_id = "tavern"
    assert any(choice["action_id"] == "village_eat" for choice in village_fallback.candidates(world))
    assert act(world, "village_eat")["code"] == "MEAL_MISSED"
    assert any(block.engine_code == "MEAL_MISSED" for block in world.state.village.residents["rowan"].fallback.blocked.values())


def test_resolved_breakfast_cutoff_does_not_prevent_legitimate_waiting(tmp_path):
    world = make_world(tmp_path, minute=3330, breakfast=True)
    world.actor.location_id = "tavern"
    assert act(world, "village_eat")["code"] == "MEAL_MISSED"
    village.advance(world, 90)
    assert act(world, "village_wait_event")["code"] == "EVENT_WAIT_STARTED"


def test_pending_local_maintenance_prevents_an_idle_wait(tmp_path):
    from test_village_maintenance import make_world as maintenance_world

    world = maintenance_world(tmp_path)
    world.scenario.village.anti_idle = True
    state = world.state.village
    state.stocks["forge"]["iron"] = 0
    job = next(iter(state.maintenance.jobs))
    choices = village_fallback.candidates(world)
    assert any(choice["action_id"] == "village_maintain" and choice["arguments"] == {"job": job}
               for choice in choices)
    assert act(world, "village_wait_event")["code"] == "ELIGIBLE_TASK_PENDING"
    assert act(world, "village_maintain", job=job)["outcome"] == "APPLIED"


def test_arriving_early_for_a_meal_allows_waiting_until_the_meal_is_due(tmp_path):
    world = make_world(tmp_path, minute=4015)
    world.actor.location_id = "tavern"
    assert act(world, "village_wait_event")["code"] == "EVENT_WAIT_STARTED"
    assert world.state.village.residents["rowan"].available_at == 4020


def test_capped_timber_and_logs_record_specific_blocks_without_creating_stock(tmp_path):
    world = make_world(tmp_path)
    before = copy.deepcopy(world.state.village.stocks)
    for action in ("village_work", "village_fell_logs"):
        assert act(world, action)["code"] == "DEMAND_MET"
        world.state.revision += 1
        village.advance(world, 5)
        assert act(world, action)["code"] == "RETRY_SUPPRESSED"
    assert world.state.village.stocks == before
    blocks = world.state.village.residents["rowan"].fallback.blocked
    assert {block.reason_code for block in blocks.values()} == {"STOCK_POLICY_PROHIBITS_PRODUCTION"}
    assert len([event for event in world.state.village.events if event.action == "TASK_BLOCKED"]) == 2


def test_inspection_is_real_bounded_and_never_improves_tools_or_produces_goods(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    candidates = world.observe()["village"]["fallback"]["eligible_candidates"]
    assert [item["action_id"] for item in candidates] == ["village_observe"]
    before = copy.deepcopy(state.stocks)
    tools = {key: entity.health for key, entity in world.state.entities.items()}
    assert act(world, "village_wait_event")["code"] == "ELIGIBLE_TASK_PENDING"
    assert act(world, "village_observe")["code"] == "LOCAL_TOOLS_CHECKED"
    assert state.residents["rowan"].available_at == state.minute + 5
    assert not any(event.action == "FALLBACK_COMPLETED" for event in state.events)
    village.advance(world, 5)
    assert act(world, "village_observe")["code"] == "INSPECTION_UNCHANGED"
    action = next(item for item in world.actions() if item["id"] == "village_observe")
    assert action["availability"] == "blocked"
    assert state.stocks == before
    assert {key: entity.health for key, entity in world.state.entities.items()} == tools
    assert {event.action for event in state.events} >= {
        "FALLBACK_SELECTED", "FALLBACK_STARTED", "FALLBACK_COMPLETED"}
    village.advance(world, 55)
    assert act(world, "village_observe")["code"] == "LOCAL_TOOLS_CHECKED"


def test_useful_inspection_becomes_eligible_again_when_a_visible_tool_changes(tmp_path):
    world = make_world(tmp_path)
    inspect_then_wait(world)
    tool = next(entity for entity in world.state.entities.values()
                if entity.location == "woods" and entity.kind != "door")
    tool.description += " A newly observed crack."
    village_extensions.advance(world)
    assert world.state.village.residents["rowan"].fallback.wait is None
    assert act(world, "village_observe")["code"] == "LOCAL_TOOLS_CHECKED"


def test_no_legal_work_allows_an_interruptible_bounded_wait_without_advancing_everyone(tmp_path):
    world = make_world(tmp_path)
    wait = inspect_then_wait(world)
    state = world.state.village
    assert state.minute == 3485
    assert wait.interruptible
    assert wait.planned_end == 3545
    assert wait.next_safety_review == wait.planned_end
    assert "local_stock_changed" in wait.wake_on
    assert state.residents["rowan"].available_at == wait.planned_end
    village.advance(world, 20)
    assert state.residents["rowan"].fallback.wait is not None
    assert state.residents["rowan"].available_at == wait.planned_end
    assert act(world, "village_wait", minutes=5)["code"] == "ACTOR_BUSY"


def test_scheduler_skips_inert_period_without_repeated_model_turns(tmp_path):
    world = make_world(tmp_path)
    wait = inspect_then_wait(world)
    state = world.state.village
    for rid, person in state.residents.items():
        if rid != "rowan":
            person.life.sleeping = True
            person.life.sleep_until = state.minute + 300
    assert village_turns.next_actor(world) == "rowan"
    assert state.minute == wait.next_safety_review
    assert state.residents["rowan"].fallback.wait is None
    assert sum(event.action == "TASK_REEVALUATED" for event in state.events) == 1


def test_local_stock_change_interrupts_wait_but_hidden_remote_stock_does_not(tmp_path):
    world = make_world(tmp_path)
    wait = inspect_then_wait(world)
    state = world.state.village
    village_goods.put(world, "tavern", "bread", 1)
    village_extensions.advance(world)
    assert state.residents["rowan"].fallback.wait == wait
    village_goods.put(world, "woods", "logs", 1)
    village_extensions.advance(world)
    assert state.residents["rowan"].fallback.wait is None
    assert state.residents["rowan"].available_at == state.minute
    event = next(event for event in reversed(state.events) if event.action == "WAIT_INTERRUPTED")
    assert event.witnesses == ["rowan"]
    assert event.details["reason_code"] == "PERSONAL_CONTEXT_CHANGED"


def test_actual_nearby_speech_wakes_a_waiter_without_creating_an_order_or_free_production(tmp_path):
    world = make_world(tmp_path)
    world.state.actors["hobb"].location_id = "woods"
    inspect_then_wait(world)
    hobb = world.bind("hobb", "session-hobb")
    assert act(hobb, "say", message="I need timber for my workshop; let us arrange a real order.")["code"] == "SPOKEN"
    assert world.state.village.residents["rowan"].fallback.wait is None
    assert world.actor.messages[-1]["actor_id"] == "hobb"
    assert world.state.village.economy.orders == {}
    assert act(world, "village_work")["code"] == "DEMAND_MET"


def test_unchanged_failed_supplier_search_is_not_repeated(tmp_path):
    world = make_world(tmp_path)
    args = {"supplier": "jory", "product": "bread", "quantity": 1,
            "deadline": 3700, "required_fresh_until": 3800}
    assert act(world, "village_propose_order", **args)["code"] == "OUT_OF_REACH"
    assert act(world, "village_propose_order", **args)["code"] == "RETRY_SUPPRESSED"
    block = next(iter(world.state.village.residents["rowan"].fallback.blocked.values()))
    assert block.reason_code == "PERSON_UNAVAILABLE"
    assert world.actor.location_id == "woods"
    assert world.state.village.economy.orders == {}


def test_short_idle_polling_is_rejected_in_favor_of_event_wait(tmp_path):
    world = make_world(tmp_path)
    assert act(world, "village_observe")["outcome"] == "APPLIED"
    village.advance(world, 5)
    assert act(world, "village_wait", minutes=5)["code"] == "EVENT_WAIT_REQUIRED"
    assert act(world, "village_wait_event")["code"] == "EVENT_WAIT_STARTED"


def test_due_needs_and_real_eligible_work_take_priority_over_fallback_inspection(tmp_path):
    world = make_world(tmp_path)
    state = world.state.village
    village_goods.take(world, "woods", "timber", 20)
    choices = village_fallback.candidates(world)
    assert choices[0]["action_id"] == "village_work"
    assert choices[0]["priority"] == "P3"
    assert act(world, "village_wait_event")["code"] == "ELIGIBLE_TASK_PENDING"
    before = state.stocks["woods"]["logs"]
    assert act(world, "village_work")["outcome"] == "APPLIED"
    assert state.stocks["woods"]["logs"] < before


def test_changed_stock_policy_revalidates_at_execution_and_does_not_bypass_resources(tmp_path):
    world = make_world(tmp_path)
    assert act(world, "village_work")["code"] == "DEMAND_MET"
    village_economy.config(world).production_targets["woods"]["timber"] = 100
    village_goods.take(world, "woods", "logs", 20)
    assert act(world, "village_work")["code"] == "STOCK_SHORTAGE"
    assert world.state.village.stocks["woods"]["timber"] == 20
    village_goods.put(world, "woods", "logs", 1)
    assert act(world, "village_work")["outcome"] == "APPLIED"
    assert world.state.village.stocks["woods"]["logs"] == 0


def test_meal_and_bedtime_bound_waits_with_real_travel_allowance(tmp_path):
    world = make_world(tmp_path, minute=4000)
    resident = world.scenario.village.residents["rowan"]
    path = village.route(world, "woods", "tavern")
    assert village_fallback.next_review(world) <= 4020 - 5 * len(path) - 5
    assert act(world, "village_wait_event")["code"] == "COMMITMENT_DUE"
    assert act(world, "village_observe")["code"] == "COMMITMENT_DUE"
    world.state.village.minute = 1440 * 2 + resident.bed_minute
    assert act(world, "village_wait_event")["code"] == "REST_DUE"


def test_retry_suppression_is_per_actor_and_survives_reload_and_request_replay(tmp_path):
    world = make_world(tmp_path)
    revision = world.revision()
    first = world.act("blocked-once", revision, "village_work", {})
    assert first["code"] == "DEMAND_MET"
    assert world.act("blocked-once", revision, "village_work", {}) == first
    restored = World(world.scenario, world.path, "fallback", "rowan", "session-rowan")
    assert act(restored, "village_work")["code"] == "RETRY_SUPPRESSED"
    assert restored.state.village.residents["hobb"].fallback.blocked == {}
    assert len([event for event in restored.state.village.events if event.action == "TASK_BLOCKED"]) == 1


def test_planning_is_private_and_contains_only_supported_actions_without_a_notice_plugin(tmp_path):
    world = make_world(tmp_path)
    assert world.state.village.communication is None
    assert act(world, "village_work")["code"] == "DEMAND_MET"
    rowan = observation(world.observe())["village"]["fallback"]
    assert rowan["blocked_tasks"][0]["retry_suppressed"]
    assert rowan["eligible_candidates"][0]["action_id"] == "village_observe"
    ids = {action["id"] for action in world.actions()}
    assert all(choice["action_id"] in ids for choice in rowan["eligible_candidates"])
    hobb = world.bind("hobb", "session-hobb").observe()["village"]["fallback"]
    assert hobb["blocked_tasks"] == []
    assert not any(event["action"] == "TASK_BLOCKED"
                   for event in world.bind("hobb", "session-hobb").observe()["village"]["events"])


def test_legacy_opt_out_and_configuration_validation(tmp_path):
    world = make_world(tmp_path, enabled=False)
    assert "fallback" not in world.observe()["village"]
    assert act(world, "village_wait", minutes=5)["code"] == "VILLAGE_WAITED"
    assert not world.state.village.residents["rowan"].fallback.blocked
    for interval in (0, 5, 14, 241):
        with pytest.raises(ValueError):
            VillageDayPolicy(idle_review_minutes=interval)


def test_runtime_configuration_enables_saved_world_without_reset_or_free_goods(tmp_path):
    world = make_world(tmp_path, enabled=False)
    before = copy.deepcopy(world.state.village.stocks)
    patch = VillageDayPolicy(anti_idle=True, event_driven_waits=True)
    village_extensions.configure(world, "enable-fallback", world.revision(), {"day_policy": patch.model_dump()})
    assert "fallback" in world.observe()["village"]
    assert world.state.village.stocks == before
    world.save()
    restored = World(world.scenario, world.path, "fallback", "rowan", "session-rowan")
    assert village_fallback.enabled(restored)

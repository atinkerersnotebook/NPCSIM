from typing import TYPE_CHECKING, Any

from npc_sim import village, village_goods
from npc_sim.models import VillageDayPolicy, VillageLifeState, VillageMeal, VillageResident

if TYPE_CHECKING:
    from npc_sim.world import World


WORK_ACTIONS = {"village_work", "village_project", "village_patrol", "village_load", "village_deliver",
                "village_fit_wagon", "village_start_route", "village_load_wagon",
                "village_unload_wagon", "village_cook_fish", "village_drive_wagon"}
WORK_ACTIONS |= {"village_grow_hay", "village_gather_herbs", "village_fell_logs",
                 "village_load_goods", "village_unload_goods", "village_buy_iron",
                 "village_fertilize", "village_hitch", "village_start_timber_route", "village_maintain"}
WORK_ACTIONS |= {"village_research", "village_research_propose", "village_research_make", "village_research_use"}
WORK_ACTIONS |= {"village_mine_ore", "village_load_ore", "village_smelt_iron"}
WORK_ACTIONS |= {"village_draw_livestock_water", "village_care_cow", "village_care_hens",
                 "village_milk_cow", "village_collect_eggs", "village_cook_dairy",
                 "village_make_plant_scraps", "village_compost", "village_fertilize_compost"}
PRODUCTION = {"village_work", "village_cook_fish", "village_grow_hay", "village_gather_herbs", "village_fell_logs",
              "village_split_firewood"}
WORK_ACTIONS.add("village_split_firewood")


def initial_state(resident: VillageResident, minute: int, policy: VillageDayPolicy | None = None) -> VillageLifeState:
    wake = minute // 1440 * 1440 + resident.wake_minute
    before_wake = minute < wake
    life = VillageLifeState(last_woke=wake - 1440 if before_wake else wake,
                            sleeping=before_wake, sleep_until=wake if before_wake else None)
    add_meals(life, resident, minute, policy)
    return life


def add_meals(life: VillageLifeState, resident: VillageResident, minute: int,
              policy: VillageDayPolicy | None = None) -> None:
    day = minute // 1440
    key = f"{day}:daily"
    if minute % 1440 >= 1140 and key not in life.meals:
        life.meals[key] = VillageMeal(kind="daily", due=day * 1440 + 1140)
        life.hungry = True
    if policy and policy.breakfast:
        due = day * 1440 + resident.wake_minute + policy.breakfast_delay
        key = f"{day}:breakfast"
        if minute >= due and key not in life.meals:
            life.meals[key] = VillageMeal(kind="breakfast", due=due)
            life.hungry = True


def cooldown_factor(world: "World", rid: str) -> int:
    life = state_for(world, rid)
    if life is None:
        return 1
    _, state = village.context(world)
    factor = 2 if any(meal.due // 1440 == state.minute // 1440 - 1 and meal.status == "missed"
                      for meal in life.meals.values()) else 1
    if state.minute // 1440 in life.fatigue_days and life.tea_day != state.minute // 1440:
        factor *= 2
    return factor


def wait_limit(world: "World") -> int:
    config, state = village.context(world)
    if not config.expanded_rules:
        return 60
    finish = [person.life.work.finishes_at - state.minute for rid, person in state.residents.items()
              if rid != world.actor_id and person.life and person.life.work]
    ongoing = (bool(finish) or bool(state.wagon and state.wagon.automatic)
               or any(person.timber_route for person in state.residents.values())
               or any(sum(crew.values()) > 0 and not (state.well_dug if project == "dig_well" else state.repaired)
                      for project, crew in state.project_contributions.items()))
    return max(5, min([15, *finish])) if ongoing else 60


def rest_due(resident: VillageResident, life: VillageLifeState, minute: int) -> bool:
    wake = minute // 1440 * 1440 + resident.wake_minute
    return (minute % 1440 >= resident.bed_minute or minute % 1440 < resident.wake_minute
            or life.last_woke < wake)


def meal_due(life: VillageLifeState, minute: int, *, include_missed: bool = False) -> VillageMeal | None:
    pending = [meal for meal in life.meals.values() if meal.status == "pending" and meal.due <= minute]
    if pending:
        return min(pending, key=lambda meal: meal.due)
    missed = [meal for meal in life.meals.values()
              if meal.status == "missed" and meal.due // 1440 == minute // 1440]
    return max(missed, key=lambda meal: meal.due) if include_missed and missed else None


def state_for(world: "World", rid: str) -> VillageLifeState | None:
    config, state = village.context(world)
    if not config.daily_life:
        return None
    life = state.residents[rid].life
    if life is None:
        raise ValueError(f"Daily-life state is missing for {rid}; start from the matching frozen scenario")
    return life


def action_blocker(world: "World", action: str, rid: str | None = None, *,
                   personal: bool = False) -> tuple[str, str] | None:
    rid = rid or world.actor_id
    life = state_for(world, rid)
    if life is None:
        return None
    config, state = village.context(world)
    from npc_sim import village_extensions, village_economy
    if state.livestock is not None:
        from npc_sim import village_livestock
        blocked = village_livestock.action_blocker(world, action, rid)
        if blocked:
            return blocked
    if state.mining is not None:
        from npc_sim import village_mining
        blocked = village_mining.action_blocker(world, action, rid)
        if blocked:
            return blocked
    if state.research is not None:
        from npc_sim import village_research
        blocked = village_research.action_blocker(world, action, rid)
        if blocked:
            return blocked
    if state.maintenance is not None:
        from npc_sim import village_maintenance
        blocked = village_maintenance.action_blocker(world, action, rid)
        if blocked:
            return blocked
    if state.social is not None:
        from npc_sim import village_social
        blocked = village_social.action_blocker(world, action, rid)
        if blocked:
            return blocked
    if state.economy is not None:
        blocked = village_economy.production_blocker(world, action, personal=personal)
        if blocked:
            return blocked
    if life.work and action not in {"village_wait", "look", "read", "say", "village_observe"}:
        return "WORK_MUST_FINISH", f"Finish the committed batch at {village.clock_time(life.work.finishes_at)} before leaving, eating or sleeping."
    if life.sleeping and action != "village_wait":
        if life.sleep_until is None:
            raise ValueError("Sleeping state is missing its wake-up time")
        return "ASLEEP", f"Sleeping until {village.clock_time(life.sleep_until)}; wait for the shared clock to reach wake-up."
    if state.incidents is not None:
        from npc_sim import village_events
        blocked = village_events.action_blocker(world, action)
        if blocked:
            return blocked
    if action in WORK_ACTIONS:
        if rest_due(config.residents[rid], life, state.minute):
            urgent = (config.expanded_rules and action == "village_project" and life.urgent_until is not None
                      and state.minute < life.urgent_until)
            if not urgent:
                return "REST_DUE", "Work can wait. Go home and use village_sleep; resume after actually waking."
        meal = meal_due(life, state.minute)
        if meal is not None:
            gathering = "the 19:00 gathering" if meal.kind == "daily" else f"breakfast at {village.clock_time(meal.due)}"
            return "MEAL_DUE", f"Pause work for {gathering}. Walk to the tavern and eat real food."
        if action in PRODUCTION and (village_extensions.modules(world) or village_extensions.policy(world).breakfast):
            job = config.residents[rid].job
            if action == "village_split_firewood" and rid == world.actor_id:
                from npc_sim import village_ai
                job = village_ai.production_job(world, action)
            if job and not job.interruptible:
                day = state.minute // 1440 * 1440
                times = [day + 1140, day + config.residents[rid].bed_minute]
                if village_extensions.policy(world).breakfast:
                    times.append(day + config.residents[rid].wake_minute + village_extensions.policy(world).breakfast_delay)
                if any(state.minute < due < state.minute + job.duration for due in times):
                    return "COMMITMENT_DUE", "This batch would delay a scheduled meal or bedtime; prepare earlier."
        if config.expanded_rules and action in PRODUCTION and not (
                config.residents[rid].wake_minute <= state.minute % 1440 <= 1170):
            return "OUTSIDE_WORK_HOURS", "Production starts only after waking and no later than the 19:30 closing grace."
    from npc_sim import village_firewood
    return village_firewood.action_blocker(world, action, personal=personal)


def personal(world: "World") -> dict[str, Any]:
    life = state_for(world, world.actor_id)
    if life is None:
        return {}
    config, state = village.context(world)
    resident = config.residents[world.actor_id]
    from npc_sim import village_extensions
    day_policy = village_extensions.policy(world)
    return {"life": {**life.model_dump(), "rest_due": rest_due(resident, life, state.minute),
                     "cooldown_factor": cooldown_factor(world, world.actor_id),
                     "wake_minute": resident.wake_minute, "bed_minute": resident.bed_minute,
                     "home": resident.home, "tavern": "tavern",
                     "breakfast": day_policy.breakfast, "breakfast_delay": day_policy.breakfast_delay}}


def apply(world: "World", action: str) -> tuple[str, str, str, None]:
    config, state = village.context(world)
    rid = world.actor_id
    resident, person = config.residents[rid], state.residents[rid]
    life = state_for(world, rid)
    if life is None:
        raise ValueError("Daily-life action used without daily-life configuration")

    def reject(code: str, text: str) -> tuple[str, str, str, None]:
        return "REJECTED", code, text, None

    def done(code: str, text: str) -> tuple[str, str, str, None]:
        person.activity = text
        village.event(world, rid, action, world.actor.location_id, text)
        return "APPLIED", code, text, None

    if action == "village_sleep":
        if config.expanded_rules and rid == config.helper and state.horse_leader == rid:
            return reject("HORSE_NOT_STABLED", "Return and release the horse at its stable before sleeping.")
        if world.actor.location_id != resident.home:
            return reject("NOT_AT_HOME", "Walk home before sleeping; no remote rest or teleportation.")
        if not rest_due(resident, life, state.minute):
            return reject("REST_NOT_DUE", "You have already rested for this waking day.")
        duration = (resident.wake_minute - resident.bed_minute) % 1440
        life.sleeping = True
        life.sleep_until = state.minute + duration
        return done("SLEEP_STARTED", f"Sleeping at home until {village.clock_time(life.sleep_until)}.")
    if action != "village_eat":
        raise ValueError(f"Unknown daily-life action: {action}")
    meal = meal_due(life, state.minute, include_missed=True)
    if meal is None:
        return reject("NO_MEAL_DUE", "No configured meal is due; supper is at 19:00.")
    if world.actor.location_id != "tavern":
        return reject("TRY_TAVERN", "Walk to the tavern for the daily meal; no remote food consumption.")
    stock = state.stocks.setdefault("tavern", {})
    if config.expanded_rules and state.economy is None and state.coins[rid] < config.meal_price:
        if meal.status == "missed":
            return reject("MEAL_UNAFFORDABLE", "You still cannot afford the tavern meal.")
        miss_meal(world, rid, meal)
        return done("MEAL_MISSED", f"Cannot pay {config.meal_price} coin for the meal; going hungry without consuming food.")
    choices = ("bread", "meals") if meal.kind == "breakfast" else ("meals", "bread")
    resource = next((name for name in choices if stock.get(name, 0) > 0), None)
    if state.economy is not None and (resource is not None or (stock.get("fish", 0) > 0 and stock.get("tea", 0) > 0)):
        from npc_sim import village_economy
        if not village_economy.meal_charge(world, rid):
            if meal.status == "missed":
                return reject("MEAL_UNAFFORDABLE", "The meal or its configured subsidy is not funded.")
            miss_meal(world, rid, meal)
            return done("MEAL_MISSED", "The meal or its configured subsidy is not funded; no food was consumed.")
    if resource:
        village_goods.take(world, "tavern", resource, 1)
        source = f"tavern:{resource}"
    elif stock.get("fish", 0) > 0 and stock.get("tea", 0) > 0:
        village_goods.take(world, "tavern", "fish", 1)
        village_goods.take(world, "tavern", "tea", 1)
        source = "tavern:fish+tea"
    else:
        if meal.status == "missed":
            return reject("NO_FOOD", "The tavern still has no food. This missed meal is already recorded.")
        miss_meal(world, rid, meal)
        return done("MEAL_MISSED", "The tavern has no meal, bread or fish with tea; going hungry. "
                    "Work remains possible, but missing today's meal doubles tomorrow's work cooldown.")
    if meal.status == "missed":
        life.missed_meals -= 1
    if config.expanded_rules and state.economy is None:
        state.coins[rid] -= config.meal_price
        state.coins["elsie"] += config.meal_price
    meal.status, meal.source = "eaten", source
    life.hungry = meal_due(life, state.minute) is not None
    return done("MEAL_EATEN", f"Ate the {meal.kind} meal from {source}; real food consumed.")


def miss_meal(world: "World", rid: str, meal: VillageMeal) -> None:
    life = state_for(world, rid)
    if life is None:
        raise ValueError("Cannot record a meal without daily-life state")
    meal.status = "missed"
    life.hungry = True
    life.missed_meals += 1
    village.event(world, rid, "missed_meal", meal.kind,
                  "Missed the daily meal; tomorrow's work cooldown is doubled unless food arrives before midnight.")


def advance(world: "World") -> None:
    config, state = village.context(world)
    if not config.daily_life:
        return
    for rid, resident in config.residents.items():
        life = state_for(world, rid)
        if life is None:
            raise ValueError("Configured resident has no daily-life state")
        person = state.residents[rid]
        if (config.expanded_rules and state.minute % 1440 == 120 and not life.sleeping
                and state.minute // 1440 not in life.fatigue_days):
            life.fatigue_days.append(state.minute // 1440)
            village.event(world, rid, "tiredness", resident.home,
                          "Still awake at 02:00; today's work cooldown is doubled by tiredness.")
        for meal in life.meals.values():
            if meal.status == "pending" and meal.due // 1440 < state.minute // 1440:
                miss_meal(world, rid, meal)
        from npc_sim import village_extensions
        day_policy = village_extensions.policy(world)
        add_meals(life, resident, state.minute, day_policy)
        if day_policy.breakfast:
            for meal in life.meals.values():
                if meal.status == "pending" and state.minute >= meal.due + day_policy.meal_window:
                    miss_meal(world, rid, meal)
        if life.work and state.minute >= life.work.finishes_at:
            work = life.work
            if (village.resident_location(world, rid) != work.location
                    or (work.equipment and world.state.entities[work.equipment].broken)):
                person.activity = "The committed batch failed; reserved ingredients were lost."
                village.event(world, rid, "batch_failed", work.location, person.activity, work.location)
            else:
                if work.kind == "wagon_repair":
                    if state.wagon is None:
                        raise ValueError("A committed wagon repair has no wagon")
                    state.wagon.repaired = True
                    village.announce(world, rid, "wagon_ready", "wagon",
                                     "The wagon repair is complete. Nella can collect the working six-unit wagon at the forge.")
                stock = state.stocks.setdefault(work.location, {})
                for resource, amount in work.outputs.items():
                    village_goods.put(world, work.location, resource, amount, ingredients=work.ingredients)
                state.role_contributions[rid] = state.role_contributions.get(rid, 0) + 1
                person.activity = f"Finished the committed batch; produced {work.outputs}."
                village.event(world, rid, "batch_completed", work.location, person.activity, work.location)
            life.work = None
        if life.sleeping and life.sleep_until is not None and state.minute >= life.sleep_until:
            life.sleeping, life.sleep_until, life.last_woke = False, None, state.minute
            person.activity = "Woke after a full rest; work may resume. The daily meal is at 19:00."
            village.event(world, rid, "wake", resident.home, person.activity)

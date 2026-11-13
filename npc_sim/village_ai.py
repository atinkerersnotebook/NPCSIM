from typing import TYPE_CHECKING, Any

from npc_sim import handling, village, village_goods, village_life, village_services, village_wagon, village_extensions, village_communication
from npc_sim.models import VillageCommittedWork, VillageCrewSession, VillageJob

if TYPE_CHECKING:
    from npc_sim.world import World


VERBS = {"village_wait", "village_ask", "village_tell", "village_observe", "village_work",
         "village_load", "village_deliver", "village_project", "village_patrol", "village_eat", "village_sleep",
         "village_meeting", "village_cook_fish", "village_share_route", "village_split_firewood",
         "village_execute_fuel_delivery", "village_plan_walk", "wait_until_next_relevant_event"} | village_wagon.VERBS | village_services.VERBS | set(village_services.JOBS) | village_extensions.VERBS


def observation(world: "World") -> dict[str, Any]:
    from npc_sim import village_fallback
    village_fallback.update_supply_needs(world)
    config, state = village.context(world)
    rid = world.actor_id
    resident, person = config.residents[rid], state.residents[rid]
    routine = next(entry for entry in reversed(resident.schedule)
                   if entry.minute <= state.minute % 1440)
    nearby = [other for other in village.local_residents(world, world.actor.location_id) if other != rid]
    public_deadlines = {}
    for key, deadline in state.deadlines.items():
        news_kind = ("deadline_completed" if deadline.completed_at is not None
                     else "deadline_overdue" if deadline.overdue else None)
        if news_kind is None or any(notice.action == news_kind and notice.target == key
                                    and rid in notice.witnesses for notice in state.notices):
            public_deadlines[key] = deadline.model_dump()
    return {"village": {
        **village_extensions.observation(world),
        "minute": state.minute, "day": state.minute // 1440 + 1,
        "time": f"{state.minute % 1440 // 60:02}:{state.minute % 60:02}",
        "residents": [{"id": other, "name": village.resident_name(world, other),
                       "occupation": config.residents[other].occupation,
                       "activity": state.residents[other].activity} for other in nearby],
        "stocks": state.stocks.get(world.actor.location_id, {}).copy(),
        "coins": state.coins[rid], "resource_inventory": person.cargo.copy(), "tasks": [],
        "events": [state.events[index].model_dump() for index in person.memories[-12:]],
        **({"notices": [notice.model_dump() for notice in state.notices if rid in notice.witnesses],
            "wagon": {**state.wagon.model_dump(), "location": world.state.entities["wagon"].location}
            if state.wagon and (rid in ({config.courier, config.helper, "ada", "jory"} if config.expanded_rules else {config.courier})
                                or world.state.entities["wagon"].location == world.actor.location_id)
            else None} if config.time_based_turns else {}),
        "personal": {
            "supply_needs": [need.model_dump() for need in person.fallback.supply_needs.values()]
            if person.fallback else [],
            "recent_locations": [step.model_dump() for step in person.fallback.recent_locations],
            "route_goal": person.fallback.route_goal.model_dump() if person.fallback.route_goal else None,
            "walking_plan": person.walking_plan.model_dump() if person.walking_plan else None,
            "occupation": resident.occupation, "personality": resident.personality,
            "skills": resident.skills, "beliefs": {key: value.model_dump() for key, value in person.beliefs.items()},
            "relationships": person.trust.copy(), "schedule": [entry.model_dump() for entry in resident.schedule],
            "routine": routine.model_dump(), "job": resident.job.model_dump() if resident.job else None,
            "next_work": person.next_work, "busy_until": person.busy_until,
            "available_at": person.available_at, "known_routes": person.known_routes.copy(),
            **({"routes": village_services.known_routes(world, rid),
                "stock_report": person.stock_report, "stock_report_at": person.stock_report_at,
                "shipments": [item.model_dump() for item in person.shipments],
                "timber_route": person.timber_route,
                "horse_location": world.state.entities["work-horse"].location if rid == config.helper else None,
                "horse_fed_today": state.horse_fed_day == state.minute // 1440 if rid == config.helper else None,
                "deadlines": public_deadlines}
               if config.expanded_rules else {}),
            "deliveries": [entry.model_dump() for entry in config.deliveries] if rid == config.courier else [],
            "active_delivery": person.delivery,
            **village_life.personal(world),
        },
        "policy": "You are this villager, not a visiting commander. Move and perform your own work with real actions. "
                  + ("Before walking, use village_plan_walk with a final destination and a concrete purpose. "
                     "Follow walking_plan.doors[walking_plan.next_step], even through intermediate stops; do not wander or "
                     "reverse course for a new whim. A new task, due meal, rest, schedule change or fresh "
                     "message permits a justified replan; a blocked route permits a detour. "
                     if world.scenario.id == "millhaven-living" and config.time_based_turns else "")
                  + "Schedules are commitments, never automatic movement. Looking and reading are free. "
                  + ("The earliest available villager acts next; work and travel reserve only that person's time. "
                     "Sleeping, committed baking and the accepted standing wagon route skip AI turns. "
                     "Use village_wait when idle; it reserves your time without delaying other villagers. "
                     "Read the free map on the square's noticeboard for every village connection. "
                     if config.time_based_turns else "Each applied action advances the shared clock five minutes. ")
                  + "All twelve villagers have separate AI contexts. "
                  "village_work performs your job at its workplace, respecting its cooldown and real inputs. "
                  + ("Torvin must make and fit wagon parts; Nella must collect the repaired wagon and accept "
                     "village_start_route. It carries six mixed units and executes batch deliveries automatically. "
                     "Bread can be delivered before the mill repair. "
                     if config.time_based_turns else
                     "village_load and village_deliver execute one configured merchant shipment; payment occurs on delivery. ")
                  +
                  "Both named project partners must be present and each must contribute village_project actions. "
                  "Gather the portable supplies at the work site or carry them there. "
                  "village_observe checks local tools; village_ask shares only the target's own knowledge. "
                  "Never quote internal action results, audit notes or belief-source codes as speech. "
                  "A personally posted open supply need already has a request: keep useful work or rest instead of "
                  "repeatedly searching for its supplier. Revisit only after new local evidence, a credible meeting, "
                  "an urgent deadline, or rejection/expiry. Check physical messages when a response is expected, "
                  "not on every short turn; never infer a remote reply from authoritative records. "
                  "Say I and my for yourself, and you and your for the person addressed. Use their actual occupation. "
                  "If a conversation reply opportunity is present, consider a brief acknowledgement, answer, question, "
                  "or polite departure before leaving. Hearing or replying never accepts a funded order; use the "
                  "actual order action for that. Do not prolong a conversation past urgent work, meals, or rest. "
                  + (" Prioritize due meals and rest over interruptible work. Eat one daily meal at the 19:00 tavern gathering. "
                     "A prepared meal, bread, or fish with tea counts; tea alone does not. No imaginary home pantry. "
                     "An empty tavern records hunger but allows work; missing today's meal doubles tomorrow's work cooldown. "
                     "At your own bedtime, walk home and village_sleep; no work before actually waking. "
                     "Well and repair progress survives pauses. Once a baking batch starts, wait for its timed "
                     "completion before leaving, eating or sleeping. Do not start another batch when meals or rest are due. "
                     "Repairs alone do not finish the adventure: everyone must eat supper, then be asleep at their own home."
                     if config.daily_life else "")
                  + (" Free look/read actions do not end your turn: follow them with useful work, travel or a clear wait. "
                     "When nothing useful can be done, prefer a clear village_wait over looking around repeatedly. "
                     "Ordinary production starts after your own wake time. Other workplaces close at 19:30; "
                     f"the inn serves until {village.clock_time(village_extensions.policy(world).inn_closing_minute)}. "
                     "Bren: feed the real horse, lead it to the wagon and hitch it in the morning; unhitch at night "
                     "and return it to the stable, released and unhitched. Stop automatic routes before everyone goes home. "
                     "Ada or Jory may drive when Nella is busy or away. Wagons use roads only. "
                     "Rowan: make timber, then accept the foot-only timber route to Hobb; fell logs when needed. "
                     "Bren may collect well timber and rope while Torvin retrieves the shovel. Hobb must also deliver "
                     "one real plank unit for the mill repair. Use manure for faster harvests, tea for tiredness, "
                     "grow hay and gather herbs to replenish inputs. The noon trader supplies paid iron. "
                     + ("Read physically posted versions, not global stock/project reports. "
                        if village_communication.enabled(world)
                        else "Read the board for stocks, project progress and deadlines. ")
                     + "Fees go to the actual carrier. "
                     "Each partner must explicitly choose village_urgent_work to override bedtime until 02:00. "
                     "Hunger and tiredness slowdowns stack; tea relieves tiredness but never replaces sleep."
                     if config.expanded_rules else ""),
    }}


def actions(world: "World") -> list[dict[str, Any]]:
    config, state = village.context(world)
    rid = world.actor_id
    resident = config.residents[rid]
    nearby = [other for other in village.local_residents(world, world.actor.location_id) if other != rid]
    result: list[dict[str, Any]] = []

    def add(aid: str, text: str, properties: dict[str, Any]) -> None:
        result.append({"id": aid, "description": text, "targets": [],
                       "arguments_schema": {"type": "object", "properties": properties,
                                            "required": list(properties), "additionalProperties": False},
                       "availability": "available", "reason": None})

    add("village_observe", "Check local tools and correct your own stale location beliefs.", {})
    if config.time_based_turns and world.scenario.id == "millhaven-living":
        from npc_sim import village_walking
        plan = state.residents[rid].walking_plan
        if not plan or village_walking._interruption(world, plan) or village_walking._blocked_step(world, plan):
            destinations = [eid for eid, entity in world.state.entities.items()
                            if entity.kind == "location" and eid != world.actor.location_id
                            and village_walking._path(world, eid) is not None]
            add("village_plan_walk", "Commit to a shortest, traversable route before walking. Replan only for a new task or interruption.",
                {"destination": {"type": "string", "enum": destinations},
                 "purpose": {"type": "string", "minLength": 3, "maxLength": 160}})
    if config.time_based_turns:
        add("wait_until_next_relevant_event", "Wait for the next personal event, bounded by the next meal, appointment or bedtime; other residents keep acting.", {})
    add("village_wait", "Wait without controlling any other villager.",
        {"minutes": {"type": "integer", "minimum": 5, "maximum": village_life.wait_limit(world), "multipleOf": 5}})
    if config.daily_life:
        add("village_eat", "Eat a due configured meal at the tavern using real local food.", {})
        add("village_sleep", "Sleep at your own home when rest is due; wake after your full scheduled rest.", {})
    if nearby:
        target = {"type": "string", "enum": nearby}
        add("village_ask", "Ask a nearby resident about their own shovel knowledge, work or witnessed news.",
            {"target": target, "topic": {"type": "string", "enum": ["shovel", "work", "news"]}})
        subjects = sorted(state.residents[rid].beliefs)
        if subjects:
            add("village_tell", "Share one of your existing tool beliefs as unverified testimony.",
                {"target": target, "subject": {"type": "string", "enum": subjects}})
    if resident.job is not None:
        add("village_work", "Perform one batch of your occupation's work at its workplace.", {})
        if config.time_based_turns and "hospitality" in resident.skills:
            add("village_cook_fish", "Cook up to two fish, each making two meals at your hearth.", {})
    if config.time_based_turns:
        if rid == config.smith:
            add("village_fit_wagon", "Fit one unit of wagon parts at the forge; finish the 30-minute repair.", {})
        if rid in ({config.courier, "ada", "jory"} if config.expanded_rules else {config.courier}):
            add("village_start_route", "Drive the automatic farm, mill, bakery, tavern, square supply circuit.", {})
            add("village_stop_route", "Park the wagon here, retaining all cargo.", {})
            add("village_drive_wagon", "Take the wagon for manual driving; go actions move both you and the wagon.", {})
            add("village_load_wagon", "Load mixed goods into the six-unit wagon in one action.",
                {"destination": {"type": "string", "enum": sorted({r.work for r in config.residents.values()})},
                 "goods": {"type": "object", "minProperties": 1,
                           "additionalProperties": {"type": "integer", "minimum": 1, "maximum": 6}}})
            add("village_unload_wagon", "Unload all cargo for this destination in one action; settle actual payments.", {})
        meetings = []
        if rid in {config.smith, config.helper}:
            meetings.append("dig_well")
        if rid in {config.carpenter, config.miller}:
            meetings.append("repair_mill")
        if meetings and not village_communication.enabled(world):
            add("village_meeting", "Agree the project's actual work-site rendezvous through a public bell message.",
                {"project": {"type": "string", "enum": meetings}})
            if config.expanded_rules:
                add("village_urgent_work", "Explicitly override bedtime for this project until 02:00; tiredness has consequences.",
                    {"project": {"type": "string", "enum": meetings}})
    elif rid == config.courier:
        routes = list(range(len(config.deliveries)))
        add("village_load", "Collect one stock unit at this delivery route's source; carry at most one shipment.",
            {"route": {"type": "integer", "enum": routes}})
        add("village_deliver", "Deliver your loaded shipment at its destination and settle the actual trade.", {})
    if config.expanded_rules:
        from npc_sim import village_firewood
        if village_firewood.enabled(world) and rid == state.firewood.config.supplier:
            routes = [plan.route_id for plan in state.firewood.delivery_plans
                      if plan.carrier == rid and plan.status in {"planned", "loaded", "in_progress"}]
            if not routes:
                candidate = village_firewood.delivery_plan(world)
                if candidate is not None:
                    routes.append(candidate.route_id)
            if routes:
                add("village_execute_fuel_delivery", "Explicitly save and advance one grouped fuel route step: "
                    "load one order, traverse one physical door, or unload and settle one stop. "
                    "Repeat after your action cooldown; it never runs automatically.",
                    {"route_id": {"type": "string", "enum": routes}})
        add("village_load_goods", "Carry up to three goods on foot; portable tools use the same real capacity.",
            {"goods": {"type": "object", "minProperties": 1,
                       "additionalProperties": {"type": "integer", "minimum": 1, "maximum": 3}},
             "destination": {"type": "string", "enum": sorted({r.work for r in config.residents.values()})}})
        add("village_unload_goods", "Unload every foot shipment for this workplace; charge actual goods and one delivery fee.", {})
        add("village_buy_iron", "Collect the visiting trader's iron consignment at noon; the forge pays three coins per unit on delivery.",
            {"quantity": {"type": "integer", "minimum": 1, "maximum": 3}})
        add("village_drink_tea", "Buy or use actual local tea to relieve today's tiredness, not hunger or the need to sleep.", {})
        for aid, (skill, resource, _) in village_services.JOBS.items():
            if skill in resident.skills:
                add(aid, f"Gather or grow two real {resource} at your workplace, respecting normal work cooldowns.", {})
        if "farming" in resident.skills:
            add("village_fertilize", "Use one manure at the farm to halve the next three grain-harvest cooldowns.", {})
            if state.livestock and state.livestock.config.enabled:
                add("village_fertilize_compost", "Use one actual compost fertilizer (not manure) for three faster grain harvests.", {})
        if "forestry" in resident.skills:
            add("village_start_timber_route", "Accept the automatic foot-only timber route from woodlot to workshop.", {})
            add("village_stop_timber_route", "Stop the timber route, retaining real cargo.", {})
        if rid == config.helper:
            for aid, text in {
                "village_lead_horse": "Lead the actual horse; it accompanies your movements.",
                "village_release_horse": "Release the led horse after returning it to the stable.",
                "village_hitch": "Hitch today's fed horse to the actual wagon in the morning.",
                "village_unhitch": "Unhitch the wagon horse at night, then lead it back to the stable."
            }.items():
                add(aid, text, {})
        if nearby and state.residents[rid].known_routes:
            add("village_share_route", "Tell a nearby villager a shortest route you actually know.",
                {"target": {"type": "string", "enum": nearby},
                 "destination": {"type": "string", "enum": sorted(
                     {end for eid in state.residents[rid].known_routes
                      for end in [world.state.entities[eid].location, world.state.entities[eid].destination] if end})}})
    if "patrol" in resident.skills:
        add("village_patrol", "Inspect your scheduled patrol location; record only things actually visible here.", {})
    projects = []
    if rid in {config.smith, config.helper} and not state.well_dug:
        projects.append("dig_well")
    if rid in {config.carpenter, config.miller} and not state.repaired:
        projects.append("repair_mill")
    if projects:
        add("village_project", "Contribute five work minutes with your partner and actual tools/materials present. "
            "A project requires six contributions, including at least two from each partner.",
            {"project": {"type": "string", "enum": projects}})
    result.extend(village_extensions.actions(world))
    return result


def production_job(world: "World", action: str) -> VillageJob | None:
    if action == "village_split_firewood":
        from npc_sim import village_firewood
        return village_firewood.production_job(world)
    config, _ = village.context(world)
    resident = config.residents[world.actor_id]
    job = resident.job
    if action == "village_cook_fish" and job and "hospitality" in resident.skills:
        job = job.model_copy(update={"inputs": {"fish": 1}})
    if action in village_services.JOBS and job:
        _, resource, equipment = village_services.JOBS[action]
        job = job.model_copy(update={"inputs": {}, "outputs": {resource: 2}, "equipment": equipment,
                                     "interruptible": True, "duration": 5, "batch_size": 1})
    return job


def production_ready(world: "World", action: str) -> tuple[str, str] | None:
    config, state = village.context(world)
    resident, person = config.residents[world.actor_id], state.residents[world.actor_id]
    job = production_job(world, action)
    if action in village_services.JOBS and village_services.JOBS[action][0] not in resident.skills:
        return "CAPABILITY_REQUIRED", "This gathering job requires your actual occupation."
    if job is None or world.actor.location_id != job.location:
        return "WRONG_WORKPLACE", "Go to your own workplace first."
    if any(skill not in world.actor.body.capabilities for skill in resident.skills):
        return "CAPABILITY_REQUIRED", "Your assigned body lacks your occupation's skills."
    if job.equipment:
        blocked = handling.use_reason(world, [job.equipment])
        if blocked:
            return "BODY_HANDLING_REQUIRED", blocked
    if not config.daily_life and not 360 <= state.minute % 1440 < 1140:
        return "OUTSIDE_WORK_HOURS", "Ordinary production runs from 06:00 to 19:00."
    if state.minute < person.next_work:
        return "WORK_COOLDOWN", f"Your next batch is ready at {village.clock_time(person.next_work)}."
    if job.equipment and world.state.entities[job.equipment].broken:
        return "EQUIPMENT_BROKEN", "The equipment needs a physical repair."
    if config.expanded_rules and world.actor_id == config.helper and world.state.entities["work-horse"].location != job.location:
        return "HORSE_NOT_HERE", "The working horse must actually be at the stable to be fed."
    stock = state.stocks.get(job.location, {})
    if any(stock.get(resource, 0) < amount for resource, amount in job.inputs.items()):
        return "STOCK_SHORTAGE", "Your workplace lacks the required input stock."
    if state.economy is not None:
        from npc_sim import village_economy
        if village_economy.production_batch_limit(world, action) <= 0:
            return "DEMAND_MET", "Usable stock and committed production already cover demand."
    return None


def apply(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str, str, None]:
    config, state = village.context(world)
    rid = world.actor_id
    resident, person = config.residents[rid], state.residents[rid]

    def reject(code: str, text: str) -> tuple[str, str, str, None]:
        return "REJECTED", code, text, None

    def done(code: str, text: str, target: str = "") -> tuple[str, str, str, None]:
        person.activity = text
        village.event(world, rid, action, target or world.actor.location_id, text)
        return "APPLIED", code, text, None

    def contribute() -> None:
        state.role_contributions[rid] = state.role_contributions.get(rid, 0) + 1

    if action == "village_plan_walk":
        from npc_sim import village_walking
        return village_walking.plan(world, args)
    if action == "wait_until_next_relevant_event":
        return village_extensions.apply(world, "village_wait_event", args)
    if action in village_extensions.VERBS:
        return village_extensions.apply(world, action, args)
    if action == "village_wait":
        if person.life and person.life.sleeping:
            if person.life.sleep_until is None:
                raise ValueError("Sleeping state is missing its wake-up time")
            return done("SLEEP_CONTINUES", f"Still sleeping until {village.clock_time(person.life.sleep_until)}.")
        if person.life and person.life.work:
            return done("BATCH_CONTINUES", f"Tending the committed batch until {village.clock_time(person.life.work.finishes_at)}.")
        from npc_sim import village_fallback
        if village_fallback.purposeful(world):
            until = village_fallback.start_wait(world)
            person.available_at = until
            return done("EVENT_WAIT_STARTED", f"Waiting for the next scheduled event at {village.clock_time(until)}.")
        return done("VILLAGE_WAITED", f"Waiting {args['minutes']} minutes.")
    if action in {"village_eat", "village_sleep"}:
        return village_life.apply(world, action)
    if action == "village_share_route":
        target, destination = args["target"], args["destination"]
        if target not in village.local_residents(world, world.actor.location_id) or target == rid:
            return reject("OUT_OF_REACH", "Speak to a different nearby villager.")
        if state.residents[target].life and state.residents[target].life.sleeping:
            return reject("RESIDENT_ASLEEP", "That resident is sleeping; share the route after they wake.")
        if village_communication.enabled(world):
            try:
                village_communication._ready(world, rid)
                village_communication._ready(world, target)
            except ValueError as error:
                return reject("RESIDENT_BUSY", str(error))
        path = village.route(world, world.actor.location_id, destination, known_routes=person.known_routes)
        if path is None:
            return reject("UNKNOWN_ROUTE", "You do not know a complete route to that place.")
        current = world.actor.location_id
        for next_location in path:
            door = next(eid for eid in person.known_routes if {
                world.state.entities[eid].location, world.state.entities[eid].destination} == {current, next_location})
            if door not in state.residents[target].known_routes:
                state.residents[target].known_routes.append(door)
            current = next_location
        if village_communication.enabled(world):
            state.residents[target].available_at = state.minute + 5
        return done("ROUTE_SHARED", f"Told {village.resident_name(world, target)} the known route to "
                    f"{world.state.entities[destination].name}.")
    if action == "village_observe":
        visible = set(world.visible_ids()) | set(world.actor.inventory)
        for subject, belief in list(person.beliefs.items()):
            if belief.location == world.actor.location_id and subject not in visible:
                village.learn(world, rid, subject, None, "observation:absent", 1)
        village.observe_local(world, rid)
        return done("LOCAL_TOOLS_CHECKED", "Checked the tools actually visible here.")
    if action in {"village_ask", "village_tell"}:
        target = args["target"]
        if target == rid or target not in village.local_residents(world, world.actor.location_id):
            return reject("OUT_OF_REACH", "The other resident must be here.")
        other = state.residents[target]
        if other.life and other.life.sleeping:
            return reject("RESIDENT_ASLEEP", "That resident is sleeping; do not interrupt their rest.")
        if village_communication.enabled(world):
            try:
                village_communication._ready(world, rid)
                village_communication._ready(world, target)
            except ValueError as error:
                return reject("RESIDENT_BUSY", str(error))
        if action == "village_tell":
            belief = person.beliefs.get(args["subject"])
            if belief is None:
                return reject("UNKNOWN_SUBJECT", "You can only share a belief you actually hold.")
            village.learn(world, target, belief.subject, belief.location, f"testimony:{rid}", 0.5)
            if village_communication.enabled(world):
                other.available_at = state.minute + 5
            return done("BELIEF_REPORTED", f"Shared a personal belief with {village.resident_name(world, target)}.", target)
        if args["topic"] == "shovel":
            belief = other.beliefs.get(config.shovel)
            if belief is None or belief.location is None:
                text = "I do not know where the shovel is."
            else:
                village.learn(world, rid, config.shovel, belief.location, f"testimony:{target}", 0.6)
                location = world.state.entities[belief.location].name
                text = f"I last knew the shovel at {location}. It may have moved."
        elif args["topic"] == "work":
            text = (f"I am the {config.residents[target].occupation}. "
                    f"My workplace is {world.state.entities[config.residents[target].work].name}.")
        else:
            news = [state.events[index].result for index in other.memories
                    if state.events[index].action in {
                        "breakdown", "discovery", "tool_collected", "meeting", "wagon_ready", "wagon_paused",
                        "project_completed", "deadline_completed", "deadline_overdue", "trader_arrived"}]
            text = " ".join(dict.fromkeys(news[-3:])) or "I have no important news I can confirm."
        if village_communication.enabled(world):
            other.available_at = state.minute + 5
        return done("VILLAGER_ANSWERED", f'{village.resident_name(world, target)} says: "{text}"', target)
    if "manipulate" not in world.actor.body.capabilities:
        return reject("CAPABILITY_REQUIRED", "This work requires manipulation.")
    if action == "village_execute_fuel_delivery":
        from npc_sim import village_firewood
        return village_firewood.execute_delivery_step(world, args["route_id"])
    if action in village_wagon.VERBS:
        return village_wagon.apply(world, action, args)
    if action in village_services.VERBS:
        return village_services.apply(world, action, args)
    if action == "village_meeting":
        if village_communication.enabled(world):
            return reject("APPOINTMENT_REQUIRED", "Use a physical meeting notice and explicit appointment acceptance.")
        project = args["project"]
        crew = [config.smith, config.helper] if project == "dig_well" else [config.carpenter, config.miller]
        site = config.well_site if project == "dig_well" else config.mill
        confirmations = state.meetings.setdefault(project, [])
        if rid not in confirmations:
            confirmations.append(rid)
        agreed = all(member in confirmations for member in crew)
        text = f"{' and '.join(village.resident_name(world, member) for member in crew)}: " \
               f"meet at {world.state.entities[site].name} after collecting the project's tools and supplies. "
        text += ("Both partners confirmed." if agreed else
                 f"{village.resident_name(world, rid)} proposed this meeting; the other partner should confirm.")
        village.announce(world, rid, "meeting", project, text)
        return done("MEETING_AGREED" if agreed else "MEETING_PROPOSED", text)
    if action == "village_project" and village_communication.enabled(world):
        blocked = village_communication.project_blocker(world, args["project"])
        if blocked:
            return reject("APPOINTMENT_REQUIRED", blocked)
    if action in village_life.PRODUCTION:
        blocked = production_ready(world, action)
        if blocked:
            return reject(*blocked)
        job = production_job(world, action)
        if job is None:
            raise ValueError("Validated production has no job")
        stock = state.stocks.setdefault(job.location, {})
        batches = min(job.batch_size, min((stock.get(resource, 0) // amount
                                           for resource, amount in job.inputs.items()), default=1))
        if state.economy is not None:
            from npc_sim import village_economy
            batches = min(batches, village_economy.production_batch_limit(world, action))
            if batches <= 0:
                return reject("DEMAND_MET", "Usable stock and committed production already cover demand.")
        inputs = {resource: amount * batches for resource, amount in job.inputs.items()}
        outputs = {resource: amount * batches for resource, amount in job.outputs.items()}
        for resource, amount in inputs.items():
            village_goods.take(world, job.location, resource, amount)
        interval = job.interval
        if config.expanded_rules and rid == config.helper:
            state.horse_fed_day = state.minute // 1440
        if config.expanded_rules and rid == "ada" and action == "village_work" and person.fertilized_harvests:
            interval = max(5, interval // 2)
            person.fertilized_harvests -= 1
        person.next_work = state.minute + interval * village_life.cooldown_factor(world, rid)
        if config.daily_life and not job.interruptible:
            if person.life is None:
                raise ValueError("Committed work requires persistent daily-life state")
            person.life.work = VillageCommittedWork(
                location=job.location, finishes_at=state.minute + job.duration,
                outputs=outputs, equipment=job.equipment, ingredients=list(job.inputs))
            return done("BATCH_STARTED", f"Started a non-interruptible batch, finishing at "
                        f"{village.clock_time(person.life.work.finishes_at)}; consumed {inputs}.")
        for resource, amount in outputs.items():
            village_goods.put(world, job.location, resource, amount, ingredients=list(job.inputs))
        contribute()
        return done("WORK_COMPLETED", f"Consumed {inputs}; produced {outputs}.")
    if action in {"village_load", "village_deliver"}:
        if rid != config.courier or "trade" not in world.actor.body.capabilities:
            return reject("CAPABILITY_REQUIRED", "Only the village merchant can settle these shipments.")
        if action == "village_load":
            if person.delivery is not None:
                return reject("CARGO_LOADED", "Deliver the current shipment first.")
            route_id = args["route"]
            route = config.deliveries[route_id]
            if world.actor.location_id != route.source:
                return reject("WRONG_SOURCE", "Go to the shipment's actual source.")
            stock = state.stocks.setdefault(route.source, {})
            if stock.get(route.resource, 0) < 1:
                return reject("STOCK_SHORTAGE", "There is no unit available to collect.")
            if (world.actor.inventory or world.actor.body.capacity < 1 or "hand" not in world.actor.body.grips
                    or world.actor.body.max_held_items == 0):
                return reject("CAPACITY_EXCEEDED", "Leave your portable tools behind before carrying a shipment.")
            stock[route.resource] -= 1
            person.cargo = {route.resource: 1}
            person.delivery = route_id
            return done("CARGO_LOADED", f"Collected one {route.resource} for delivery.", route.source)
        if person.delivery is None:
            return reject("NO_CARGO", "Collect a real shipment first.")
        route = config.deliveries[person.delivery]
        if world.actor.location_id != route.destination:
            return reject("WRONG_DESTINATION", "Walk to the shipment's actual destination.")
        if person.cargo != {route.resource: 1}:
            raise ValueError("Shipment and cargo ledger disagree")
        if state.coins[route.buyer] < route.price:
            return reject("INSUFFICIENT_COINS", "The buyer cannot pay; your shipment stays with you.")
        state.coins[route.buyer] -= route.price
        state.coins[route.seller] += route.price
        stock = state.stocks.setdefault(route.destination, {})
        stock[route.resource] = stock.get(route.resource, 0) + 1
        person.cargo = {}
        person.delivery = None
        if state.repaired and route.resource == "bread":
            state.bread_after_repair += 1
        contribute()
        return done("CARGO_DELIVERED", f"Delivered one {route.resource}; transferred {route.price} coins "
                    f"from {village.resident_name(world, route.buyer)} to {village.resident_name(world, route.seller)}.")
    if action == "village_patrol":
        routine = next(entry for entry in reversed(resident.schedule) if entry.minute <= state.minute % 1440)
        if "patrol" not in world.actor.body.capabilities:
            return reject("CAPABILITY_REQUIRED", "Patrolling requires the guard's skill.")
        if world.actor.location_id != routine.location or routine.location not in {"bridge", "village-square"}:
            return reject("WRONG_PATROL_LOCATION", "Go to the location assigned by your patrol schedule.")
        if state.minute < person.next_work:
            return reject("WORK_COOLDOWN", f"Your next patrol is due at {village.clock_time(person.next_work)}.")
        person.next_work = state.minute + 60 * village_life.cooldown_factor(world, rid)
        contribute()
        names = [world.state.entities[eid].name for eid in world.visible_ids()]
        return done("PATROL_COMPLETED", "Patrolled here; observed " + ", ".join(names) + ".")
    if action == "village_project":
        kind = args["project"]
        digging = kind == "dig_well"
        crew = {config.smith: "digging", config.helper: "digging"} if digging else {
            config.carpenter: "carpentry", config.miller: "milling"}
        site = config.well_site if digging else config.mill
        session = state.crew_sessions.get(kind)
        if session is not None and (session.status != "active" or set(session.workers) != set(crew)
                                    or session.site != site):
            return reject("CREW_RESERVED", "This project is reserved by another crew session.")
        if rid not in crew or any(
                member not in world.state.actors or village.resident_location(world, member) != site
                for member in crew):
            return reject("CREW_MISSING", "Both named partners must physically be at the work site.")
        if any(skill not in world.state.actors[member].body.capabilities
               or "manipulate" not in world.state.actors[member].body.capabilities for member, skill in crew.items()):
            return reject("CAPABILITY_REQUIRED", "Both partners need their actual project skills.")
        occupied = [(member, state.residents[member].busy_until) for member in crew
                    if state.minute < state.residents[member].busy_until]
        if occupied:
            member, until = max(occupied, key=lambda entry: entry[1])
            return reject("CREW_BUSY", f"{village.resident_name(world, member)} has an urgent prior commitment "
                          f"until {village.clock_time(until)}; remain at the site and retry then.")
        for member in crew:
            blocked = village_life.action_blocker(world, action, member)
            if blocked:
                return reject("CREW_UNAVAILABLE", f"{village.resident_name(world, member)}: {blocked[1]}")
            if config.expanded_rules and state.residents[member].life:
                own_life = state.residents[member].life
                if (village_life.rest_due(config.residents[member], own_life, state.minute)
                        and own_life.urgent_project != kind):
                    return reject("CREW_UNAVAILABLE", "Each partner must explicitly choose this project's bedtime override.")
        if (state.well_dug if digging else state.repaired):
            return reject("PROJECT_COMPLETED", "That project is already complete.")
        if not digging and not state.breakdown:
            return reject("NOT_BROKEN", "The mill has not broken down.")
        supplies = [config.shovel, config.timber, config.rope] if digging else [config.repair_part, config.hammer]
        if config.expanded_rules and not digging and state.stocks.get(site, {}).get("planks", 0) < 1:
            return reject("SUPPLIES_MISSING", "Bring one real plank unit to the watermill for the repair.")
        if any(world.state.entities[eid].location not in {site, *crew}
               or world.state.entities[eid].broken or world.state.entities[eid].hidden for eid in supplies):
            return reject("SUPPLIES_MISSING", "Bring the real, usable tools and materials to the crew.")
        for member in crew:
            for eid in supplies:
                blocked = handling.grip_reason(world.state.actors[member].body, world.state.entities[eid], using=True)
                if blocked:
                    return reject("BODY_HANDLING_REQUIRED", f"{village.resident_name(world, member)}: {blocked}")
        progress = state.project_contributions.setdefault(kind, {})
        if village_communication.enabled(world):
            plan = village_communication.confirmed_project_plan(world, kind)
            if plan is None:
                return reject("APPOINTMENT_REQUIRED", "The confirmed crew appointment is no longer available.")
            if session is None:
                session = VillageCrewSession(
                    appointment_id=plan.id, project=kind, workers=list(crew), site=site,
                    supplies=supplies, starts_at=state.minute, work_window_end=plan.end_at,
                    contributions=progress.copy())
                state.crew_sessions[kind] = session
            elif session.appointment_id != plan.id:
                return reject("CREW_RESERVED", "This project is reserved by another crew session.")
        progress[rid] = progress.get(rid, 0) + 1
        if session is not None:
            session.contributions[rid] = progress[rid]
        contribute()
        if sum(progress.values()) >= 6 and all(progress.get(member, 0) >= 2 for member in crew):
            if session is not None:
                session.status = "completed"
            for eid in ([config.timber, config.rope] if digging else [config.repair_part]):
                entity = world.state.entities[eid]
                for actor in world.state.actors.values():
                    if eid in actor.inventory:
                        actor.inventory.remove(eid)
                entity.location = None
                entity.takable = False
                entity.hidden = True
            if digging:
                state.well_dug = True
                world.state.entities[site].description = "A completed, lined village well with a secure safety rope."
            else:
                if config.expanded_rules:
                    village_goods.take(world, site, "planks", 1)
                state.repaired = True
                equipment = world.state.entities[config.mill_equipment]
                equipment.health = equipment.max_health
                equipment.description = "A repaired mill drive, turning smoothly."
            if config.expanded_rules:
                village_goods.complete_deadline(world, kind, rid)
                village.announce(world, rid, "project_completed", kind,
                                 f"The crew completed {kind.replace('_', ' ')}. All involved villagers have been notified.")
            return done("PROJECT_COMPLETED", f"The crew completed {kind.replace('_', ' ')}.", site)
        return done("PROJECT_PROGRESS", f"Contributed five work minutes to {kind.replace('_', ' ')}; "
                    f"partner contributions: {progress}.", site)
    raise ValueError(f"Unknown AI village action: {action}")


def after_action(world: "World", action: str, args: dict[str, Any]) -> int:
    config, state = village.context(world)
    person = state.residents[world.actor_id]
    if action == "go":
        from npc_sim import village_fallback
        door = world.state.entities[args["target"]]
        destination = world.actor.location_id
        origin = door.destination if door.location == destination else door.location
        if origin is not None:
            village_fallback.record_location_transition(world, origin, destination)
            if world.scenario.id == "millhaven-living" and config.time_based_turns:
                from npc_sim import village_walking
                village_walking.advance(world, origin, destination)
    if (action == "go" and state.wagon and state.wagon.driver == world.actor_id
            and not state.wagon.automatic):
        world.state.entities["wagon"].location = world.actor.location_id
    if action not in VERBS:
        target = args.get("target", world.actor.location_id)
        name = world.state.entities[target].name if target in world.state.entities else target
        person.activity = f"{action.replace('_', ' ').capitalize()}: {name}"
        if action == "say":
            person.activity = f'Said: "{args["message"]}"'
        village.event(world, world.actor_id, action, target, person.activity)
        if action == "take" and target == config.shovel and world.actor_id == config.smith:
            state.shovel_recovered = True
        if config.time_based_turns and action == "take" and target in {
                config.shovel, config.timber, config.rope, config.repair_part, config.hammer}:
            village.announce(world, world.actor_id, "tool_collected", target,
                             f"{village.resident_name(world, world.actor_id)} collected "
                             f"{world.state.entities[target].name} and is now carrying it.")
    for rid in village.local_residents(world, world.actor.location_id):
        life = state.residents[rid].life
        if life is None or not life.sleeping:
            village.observe_local(world, rid)
    minutes = args["minutes"] if action == "village_wait" else 0 if action in {"look", "read"} else 5
    if config.time_based_turns:
        if config.expanded_rules:
            state.free_turn_actor = world.actor_id if action in {"look", "read"} else None
            if action == "go":
                if args["target"] not in person.known_routes:
                    person.known_routes.append(args["target"])
                if state.horse_leader == world.actor_id or (state.wagon and state.wagon.driver == world.actor_id
                                                           and state.wagon.hitched):
                    world.state.entities["work-horse"].location = world.actor.location_id
        if action == "village_project":
            minutes *= village_life.cooldown_factor(world, world.actor_id)
        if action in {"village_wait_event", "wait_until_next_relevant_event"} or (action == "village_wait" and person.fallback.wait is not None):
            minutes = max(5, person.available_at - state.minute)
        person.available_at = state.minute + minutes
        until = village_extensions.blocked_until(world, world.actor_id)
        if until is not None:
            person.available_at = max(person.available_at, until)
        if person.life and person.life.sleeping and person.life.sleep_until is not None:
            person.available_at = person.life.sleep_until
        if person.life and person.life.work:
            person.available_at = person.life.work.finishes_at
    else:
        village.advance(world, minutes)
    village_extensions.record_action(world, action, args)
    return minutes


def evaluate_goal(world: "World") -> dict[str, Any]:
    config, state = village.context(world)
    checks = {"shovel_recovered": state.shovel_recovered, "well_dug": state.well_dug,
              "mill_repaired": state.repaired,
              "bread_supply_resumed": (state.bread_deliveries if config.time_based_turns else state.bread_after_repair) > 0,
              "every_resident_contributed": all(state.role_contributions.get(rid, 0) > 0 for rid in config.residents)}
    if config.daily_life:
        checks["committed_batches_finished"] = all(
            person.life is not None and person.life.work is None for person in state.residents.values())
    if config.time_based_turns:
        checks["working_wagon"] = state.wagon is not None and state.wagon.repaired
    evidence: dict[str, Any] = {}
    if config.daily_life:
        meal_day = (state.minute - 1140) // 1440
        evening_reached = meal_day >= config.start_minute // 1440
        home_checks = {
            rid: rid in world.state.actors and world.state.actors[rid].location_id == resident.home
            for rid, resident in config.residents.items()
        }
        meal_checks = {
            rid: bool(person.life
                      and (meal := person.life.meals.get(f"{meal_day}:daily")) is not None
                      and meal.status == "eaten")
            for rid, person in state.residents.items()
        }
        checks.update(
            evening_reached=evening_reached,
            everyone_fed=evening_reached and all(meal_checks.values()),
            everyone_home=all(home_checks.values()),
            everyone_asleep=all(person.life is not None and person.life.sleeping
                               and person.life.sleep_until is not None and person.life.sleep_until > state.minute
                               for person in state.residents.values()),
        )
        meal_deadline = meal_day * 1440 + village_extensions.policy(world).inn_closing_minute
        missed_daily_meals = [rid for rid, fed in meal_checks.items() if not fed] if evening_reached else []
        evidence.update(completion_scope="full_village_day", meal_day=meal_day,
                        residents_home=home_checks, residents_fed=meal_checks,
                        missed_daily_meals=missed_daily_meals,
                        daily_meal_deadline=meal_deadline if evening_reached else None,
                        daily_requirements_recoverable=(
                            village_extensions.policy(world).demand_led_completion
                            and (not missed_daily_meals or state.minute < meal_deadline)))
        if state.livestock and state.livestock.config.enabled and 0 <= state.livestock.started_day <= meal_day:
            care = {operation: any(chunk.operation == operation and chunk.status == "completed"
                                   and chunk.start // 1440 == meal_day for chunk in state.livestock.chunks)
                    for operation in ["cow", "hens"]}
            checks["livestock_daily_care"] = all(care.values())
            from npc_sim import village_livestock
            checks["livestock_home"] = village_livestock._animals(world, "cow") and village_livestock._animals(world, "hens")
            evidence["livestock_care"] = care
    if config.expanded_rules and config.daily_life:
        actions = {event.action for event in state.events}
        horse_home = world.scenario.entities["work-horse"].location
        checks.update(
            animals_put_away=(horse_home is not None and world.state.entities["work-horse"].location == horse_home
                             and state.horse_leader is None and state.wagon is not None
                             and not state.wagon.hitched),
            routes_stopped=(state.wagon is not None and not state.wagon.automatic
                           and state.wagon.driver is None
                           and all(not person.timber_route for person in state.residents.values())),
            hay_replenished="village_grow_hay" in actions,
            herbs_replenished="village_gather_herbs" in actions,
            logs_replenished="village_fell_logs" in actions,
            manure_used="village_fertilize" in actions,
            tea_used=any(person.life is not None and person.life.tea_day >= config.start_minute // 1440
                         for person in state.residents.values()),
        )
        if not village_extensions.policy(world).require_tea_use:
            checks.pop("tea_used")
        if not village_extensions.policy(world).require_manure_use:
            checks.pop("manure_used")
        if state.economy is not None and village_extensions.policy(world).demand_led_completion:
            village_extensions.refresh(world)
            if state.dashboard is None:
                raise ValueError("Demand-led completion requires a current economy dashboard")
            reserves = [product for product in state.dashboard.products if product.reserve > 0
                        and config.spoil_after.get(product.product, 4320) >= 4320]
            for key in ("hay_replenished", "herbs_replenished", "logs_replenished"):
                checks.pop(key)
            checks["resource_reserves_ready"] = bool(reserves) and all(
                product.usable >= product.reserve for product in reserves)
            evidence["reserve_shortages"] = [
                product.model_dump() for product in reserves if product.usable < product.reserve]
        evidence["animals_home"] = {"work-horse": {
            "location": world.state.entities["work-horse"].location, "home": horse_home,
            "hitched": state.wagon.hitched if state.wagon else None, "leader": state.horse_leader,
        }}
    missing = [key.replace("_", " ") for key, passed in checks.items() if not passed]
    return {"status": "achieved" if all(checks.values()) else "active",
            "reason": ("All required village recovery and daily-life checks are complete." if not missing else
                       f"Village recovery is still in progress: {', '.join(missing)}."),
            "evidence": {"checks": checks, "minute": state.minute,
                         "role_contributions": state.role_contributions.copy(),
                         "project_contributions": {key: value.copy() for key, value in state.project_contributions.items()},
                         **evidence},
            "observation_revision": world.revision()}

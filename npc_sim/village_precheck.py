"""Engine-side checks for constraints an actor can know before attempting an action.

This is deliberately not a replacement for the authoritative apply-time guards: remote
stock, other residents' private commitments and unseen project state stay unknown.
"""

from typing import TYPE_CHECKING, Any

from npc_sim import village

if TYPE_CHECKING:
    from npc_sim.world import World


def evaluate_action(world: "World", action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Return locally knowable constraints; execution remains authoritative."""
    blocked = check(world, action, args)
    result: dict[str, Any] = {
        "allowed": blocked is None, "reason": blocked[1] if blocked else None,
        "required_channel": None, "required_location": None,
        "earliest_start": None, "latest_start": None,
        "minimum_inputs": None, "available_quantity": None,
        "capacity_limit": None, "blocking_event": None,
        "suggested_safe_alternatives": [],
    }
    if not blocked:
        return result
    code = blocked[0]
    if code == "CAPACITY_EXCEEDED":
        from npc_sim.village_services import foot_capacity
        _, state = village.context(world)
        result["capacity_limit"] = max(0, foot_capacity(world, world.actor_id) - sum(
            shipment.quantity for shipment in state.residents[world.actor_id].shipments))
        result["suggested_safe_alternatives"] = ["Carry fewer units or unload existing cargo."]
    elif code == "CHANNEL_UNAVAILABLE":
        _, state = village.context(world)
        communication = state.communication
        channel = (args.get("channel", "board") if action in {"village_notice_post", "village_inbox_leave"}
                   else args.get("channel") if action == "village_check_messages"
                   else "inbox" if action == "village_inbox_read" else "board")
        result["required_channel"] = channel
        fixture = (communication.config.board_id if channel == "board" else
                   args.get("inbox_id") or next((box for box, owner in communication.boxes.items()
                                                  if owner == world.actor_id), None))
        if fixture in world.state.entities and (channel == "board" or fixture in world.visible_ids()
                                                 or fixture in world.actor.inspected):
            result["required_location"] = world.state.entities[fixture].location
        result["suggested_safe_alternatives"] = ["Travel to a personally known physical channel."]
    elif code == "WRONG_PATROL_LOCATION":
        config, state = village.context(world)
        schedule = config.residents[world.actor_id].schedule
        result["required_location"] = next(entry for entry in reversed(schedule)
                                           if entry.minute <= state.minute % 1440).location
    elif code == "WORK_COOLDOWN":
        result["earliest_start"] = world.state.village.residents[world.actor_id].next_work
    elif code == "OUTSIDE_RESEARCH_HOURS":
        result["latest_start"] = world.state.village.minute // 1440 * 1440 + 1020
        result["suggested_safe_alternatives"] = ["Choose available local work or rest."]
    elif code == "ROUTE_GOAL_DEFERRED":
        result["blocking_event"] = "new_personally_known_evidence"
        result["suggested_safe_alternatives"] = ["Wait for a natural encounter or confirmed appointment."]
    return result


def get_available_actions(world: "World") -> list[dict[str, Any]]:
    """Annotate advertised actions without guessing missing required arguments."""
    return [{**action, "precheck": evaluate_action(world, action["id"], {})}
            if not action["arguments_schema"].get("required") else action
            for action in world.actions()]


def check(world: "World", action: str, args: dict[str, Any]) -> tuple[str, str] | None:
    if not world.scenario.village or not world.scenario.village.ai_controlled:
        return None
    config, state = village.context(world)
    resident = config.residents[world.actor_id]
    location = world.actor.location_id
    minute = state.minute

    if action == "village_plan_walk" and world.scenario.id == "millhaven-living":
        from npc_sim import village_walking
        return village_walking.check_plan(world, args["destination"])
    if action == "go":
        from npc_sim import village_fallback, village_walking
        if village_walking.enabled(world):
            blocked = village_walking.check_step(world, args)
            if blocked:
                return blocked
        return village_fallback.route_blocker(world, args)

    if action == "village_patrol":
        routine = next(entry for entry in reversed(resident.schedule) if entry.minute <= minute % 1440)
        if "patrol" not in world.actor.body.capabilities:
            return "CAPABILITY_REQUIRED", "Patrolling requires the guard's skill."
        if location != routine.location or routine.location not in {"bridge", "village-square"}:
            return "WRONG_PATROL_LOCATION", "Go to the location assigned by your patrol schedule."
        if minute < state.residents[world.actor_id].next_work:
            return "WORK_COOLDOWN", f"Your next patrol is due at {village.clock_time(state.residents[world.actor_id].next_work)}."

    if action in {"village_load_goods", "village_buy_iron"}:
        from npc_sim.village_services import foot_capacity
        goods = {"iron": args["quantity"]} if action == "village_buy_iron" else args["goods"]
        if sum(goods.values()) > foot_capacity(world, world.actor_id) - sum(
                shipment.quantity for shipment in state.residents[world.actor_id].shipments):
            return "CAPACITY_EXCEEDED", "The selected goods exceed your remaining real carrying capacity."

    if state.communication and action in {"village_notice_post", "village_inbox_leave",
                                          "village_check_messages", "village_inbox_read",
                                          "village_notice_read"}:
        communication = state.communication
        if action in {"village_notice_post", "village_inbox_leave"}:
            channel = args.get("channel", "board")
        elif action == "village_check_messages":
            channel = args["channel"]
        elif action == "village_inbox_read":
            channel = "inbox"
        elif action == "village_notice_read":
            channel = "board"
        fixture = (args.get("inbox_id") if action in {"village_notice_post", "village_inbox_leave", "village_inbox_read"}
                   and channel == "inbox" else None)
        if action == "village_check_messages" and channel == "inbox":
            fixture = next((box for box, owner in communication.boxes.items() if owner == world.actor_id), None)
        if channel == "board":
            fixture = communication.config.board_id
        if channel in {"board", "inbox"}:
            entity = world.state.entities.get(fixture) if fixture else None
            if entity is None or entity.hidden or entity.broken or entity.location != location:
                return "CHANNEL_UNAVAILABLE", "Go to the actual physical board or inbox before using this channel."

    if state.research and action in {"village_research", "village_research_make", "village_research_use"}:
        from npc_sim import village_research
        key = args["project"]
        project = state.research.projects.get(key)
        if (project is not None and key in state.research.known.get(world.actor_id, [])
                and (project.owner == world.actor_id or project.discovered_at is not None)
                and state.research.config.enabled):
            if action != "village_research_use" and location != project.location:
                return "NOT_PRESENT", "Walk to the actual research/production site."
            if action == "village_research" and project.discovered_at is None:
                from npc_sim import village_life
                life = state.residents[world.actor_id].life
                if life is None or life.sleeping or village_life.rest_due(resident, life, minute) or life.hungry or village_life.meal_due(life, minute):
                    return None
                end = minute + 15 * village_life.cooldown_factor(world, world.actor_id)
                if not resident.wake_minute <= minute % 1440 < 1020 or end > minute // 1440 * 1440 + 1020:
                    return "OUTSIDE_RESEARCH_HOURS", "Occupational research uses free workday time, ending by 17:00."
                if "patrol" in resident.skills and (state.residents[world.actor_id].next_work <= minute
                                                      or minute < state.residents[world.actor_id].next_work < end):
                    return "PATROL_DUE", "Perform scheduled patrols before research, and leave time for the next patrol."
    return None

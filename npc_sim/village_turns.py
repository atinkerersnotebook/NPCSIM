from typing import TYPE_CHECKING

from npc_sim import village, village_extensions

if TYPE_CHECKING:
    from npc_sim.world import World


def next_actor(world: "World", stop_at_minute: int | None = None) -> str | None:
    config, state = village.context(world)
    if not config.time_based_turns:
        raise ValueError("This world does not use timed village turns")
    if stop_at_minute is not None and state.minute >= stop_at_minute:
        world.save()
        return None
    ids = list(config.residents)
    if stop_at_minute is not None and state.minute >= stop_at_minute:
        world.save()
        return None
    if config.expanded_rules and state.free_turn_actor:
        rid = state.free_turn_actor
        person = state.residents[rid]
        if (person.life and not person.life.sleeping and not person.life.work
                and village_extensions.blocked_until(world, rid) is None
                and not person.timber_route
                and not (state.wagon and state.wagon.automatic and state.wagon.driver == rid)):
            world.save()
            return rid
        state.free_turn_actor = None
    previous = state.last_scheduled_actor
    if previous:
        person = state.residents[previous]
        person.available_at = max(person.available_at, state.minute + 5)
    for _ in range(576):
        if stop_at_minute is not None and state.minute >= stop_at_minute:
            world.save()
            return None
        ready: list[str] = []
        future: list[int] = []
        for rid in ids:
            person = state.residents[rid]
            life = person.life
            activity_until = village_extensions.blocked_until(world, rid)
            if activity_until is not None and activity_until > state.minute:
                future.append(activity_until)
            elif life and life.sleeping:
                if life.sleep_until is None:
                    raise ValueError("A sleeping villager has no wake time")
                future.append(life.sleep_until)
            elif life and life.work:
                future.append(life.work.finishes_at)
            elif state.wagon and state.wagon.automatic and state.wagon.driver == rid:
                future.append(state.wagon.next_event_at)
            elif person.timber_route:
                future.append(person.route_next_at)
            elif person.available_at <= state.minute:
                ready.append(rid)
            else:
                future.append(person.available_at)
        if ready:
            earliest = min(state.residents[rid].available_at for rid in ready)
            for offset in range(len(ids)):
                index = (state.turn_cursor + offset) % len(ids)
                rid = ids[index]
                if rid in ready and state.residents[rid].available_at == earliest:
                    state.turn_cursor = (index + 1) % len(ids)
                    state.last_scheduled_actor = rid
                    world.state.revision += 1
                    world.save()
                    return rid
        if not future:
            raise ValueError("The timed village has no runnable actor or future event")
        target = max(state.minute + 5, min(future))
        if stop_at_minute is not None:
            target = min(target, stop_at_minute)
        village.advance(world, target - state.minute)
    raise ValueError("No villager became available within two village days")

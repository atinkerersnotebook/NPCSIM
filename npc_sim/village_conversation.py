from __future__ import annotations

from typing import TYPE_CHECKING

from npc_sim import village
from npc_sim.models import VillageConversation

if TYPE_CHECKING:
    from npc_sim.world import World


MAX_TURNS = 4
MAX_MINUTES = 20
COOLDOWN_MINUTES = 30


def key(first: str, second: str) -> str:
    return ":".join(sorted((first, second)))


def active(world: World) -> list[VillageConversation]:
    state = world.state.village
    if state is None:
        return []
    return [thread for thread in state.conversations.values()
            if state.minute < thread.expires_at and thread.turns < MAX_TURNS
            and world.state.actors[thread.speaker].location_id == world.state.actors[thread.listener].location_id
            and not any(state.residents[rid].life and state.residents[rid].life.sleeping
                        for rid in (thread.speaker, thread.listener))]


def end_invalid(world: World) -> None:
    state = world.state.village
    if state is None:
        return
    valid = active(world)
    for identifier, thread in list(state.conversations.items()):
        if thread not in valid:
            del state.conversations[identifier]
            state.conversation_cooldowns[identifier] = state.minute + COOLDOWN_MINUTES


def recipient(world: World, text: str, peers: list[str], requested: str | None = None) -> str | None:
    if requested is not None:
        return requested if requested in peers else None
    for rid in peers:
        name = village.resident_name(world, rid)
        if text.lower().startswith(name.lower()) and text[len(name):len(name) + 1] in {",", ":", "!", "?"}:
            return rid
    thread = pending(world)
    if thread is not None:
        partner = thread.listener if thread.speaker == world.actor_id else thread.speaker
        if partner in peers:
            return partner
    return None


def spoken(world: World, target: str) -> None:
    state = world.state.village
    if state is None or not world.scenario.village.time_based_turns:
        return
    identifier = key(world.actor_id, target)
    end_invalid(world)
    thread = state.conversations.get(identifier)
    if thread is not None:
        if thread.next_speaker != world.actor_id:
            return
        thread.turns += 1
        if thread.turns >= MAX_TURNS:
            del state.conversations[identifier]
            state.conversation_cooldowns[identifier] = state.minute + COOLDOWN_MINUTES
        else:
            thread.next_speaker = target
    elif state.minute >= state.conversation_cooldowns.get(identifier, 0):
        if any(world.actor_id in (item.speaker, item.listener) or target in (item.speaker, item.listener)
               for item in active(world)):
            return
        state.conversations[identifier] = VillageConversation(
            speaker=world.actor_id, listener=target, next_speaker=target,
            turns=1, started_at=state.minute, expires_at=state.minute + MAX_MINUTES)


def pending(world: World) -> VillageConversation | None:
    return next((thread for thread in active(world) if thread.next_speaker == world.actor_id), None)

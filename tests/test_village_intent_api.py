"""Focused physical-channel and replay tests for engine-owned notice intents."""

import uuid
from pathlib import Path

import pytest

from npc_sim import village_ai, village_communication as comm, village_economy
from npc_sim.world import World


@pytest.fixture
def world():
    from npc_sim.models import VillageEconomyConfig
    from test_village_expanded import scenario

    value = scenario()
    value.village.start_minute = 480
    value.village.goal_deadlines = {}
    value.village.economy = value.village.economy or VillageEconomyConfig()
    comm.install_scenario(value)
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    path = Path(__file__).parent / f".intent-{uuid.uuid4().hex}.json"
    try:
        result = World(value, path, "intent", "jory", "session-jory")
        for agent in value.agents:
            result.bind(agent.id, f"session-{agent.id}")
        yield result
    finally:
        path.unlink(missing_ok=True)


def actor(world, rid, location):
    result = world.bind(rid, f"session-{rid}")
    result.actor.location_id = location
    for person in result.state.village.residents.values():
        person.available_at = min(person.available_at, result.state.village.minute)
        person.busy_until = min(person.busy_until, result.state.village.minute)
    return result


def invoke(world, action, **args):
    return comm.apply(world, action, args)


def supply():
    return {"good": "flour", "quantity": 2, "purpose": "Bread for supper",
            "preferred_supplier_id": "mara", "deadline": 900, "required_fresh_until": 960}


def test_t04_physical_post_and_remote_rejection(world):
    world = actor(world, "jory", "bakery")
    before = len(world.state.village.communication.notices)
    result = invoke(world, "village_request_supply", **supply())
    assert result[:2] == ("REJECTED", "CHANNEL_UNAVAILABLE")
    assert len(world.state.village.communication.notices) == before
    assert "noticeboard" in result[2]
    world = actor(world, "jory", "village-square")
    result = invoke(world, "village_request_supply", **supply())
    assert result[:2] == ("APPLIED", "NOTICE_POSTED")
    notice = next(iter(world.state.village.communication.notices.values()))[-1]
    assert (notice.terms.destination, notice.terms.unit_price, notice.terms.recipients) == (
        "bakery", village_economy.unit_price(world, "flour"), ["mara"])


def test_t05_reply_resolves_authors_inbox_and_no_remote_reply(world):
    world = actor(world, "jory", "village-square")
    assert invoke(world, "village_request_supply", **supply())[0] == "APPLIED"
    nid = next(iter(world.state.village.communication.notices))
    world = actor(world, "mara", "village-square")
    assert invoke(world, "village_check_messages")[0] == "APPLIED"
    world = actor(world, "mara", "watermill")
    result = invoke(world, "village_respond_to_request", request_id=nid, response="reject", reason="Not enough stock")
    assert result[:2] == ("REJECTED", "CHANNEL_UNAVAILABLE")
    assert world.state.village.communication.notices[nid][-1].status == "posted"
    world = actor(world, "mara", "bakery")
    assert invoke(world, "village_respond_to_request", request_id=nid,
                  response="reject", reason="Not enough stock")[0] == "APPLIED"
    assert world.state.village.communication.placements["workplace-inbox-jory"][nid] == 2
    assert world.state.village.communication.knowledge["jory"][nid].snapshot.version == 1
    assert invoke(world, "village_respond_to_request", request_id=nid,
                  response="reject", reason="Not enough stock")[:2] == ("APPLIED", "REPLY_UNCHANGED")


def test_t06_equivalent_supply_replay_does_not_create_new_notice(world):
    world = actor(world, "jory", "village-square")
    assert invoke(world, "village_request_supply", **supply())[0] == "APPLIED"
    state = world.state.village.communication
    original_cooldown = state.cooldowns["post:jory"]
    assert invoke(world, "village_request_supply", **supply())[:2] == ("APPLIED", "NOTICE_UNCHANGED")
    assert len(state.notices) == 1
    assert state.cooldowns["post:jory"] == original_cooldown
    assert world.state.village.economy.next_order == 1


def test_acceptance_replay_creates_one_order_and_local_author_receives(world):
    world = actor(world, "jory", "village-square")
    assert invoke(world, "village_request_supply", **supply())[0] == "APPLIED"
    nid = next(iter(world.state.village.communication.notices))
    world = actor(world, "mara", "village-square")
    assert invoke(world, "village_check_messages")[0] == "APPLIED"
    assert invoke(world, "village_respond_to_request", request_id=nid, response="accept")[0] == "APPLIED"
    state = world.state.village.communication
    assert len(world.state.village.economy.orders) == 1
    assert invoke(world, "village_respond_to_request", request_id=nid,
                  response="accept")[:2] == ("APPLIED", "REPLY_UNCHANGED")
    assert len(world.state.village.economy.orders) == 1
    assert state.knowledge["jory"][nid].snapshot.status == "accepted"


def test_update_intent_resolves_known_version_and_rejects_technical_overrides(world):
    world = actor(world, "jory", "village-square")
    assert invoke(world, "village_request_supply", **supply())[0] == "APPLIED"
    nid = next(iter(world.state.village.communication.notices))
    assert invoke(world, "village_update_request", request_id=nid,
                  changes={"unit_price": 999})[0] == "REJECTED"
    assert invoke(world, "village_update_request", request_id=nid,
                  changes={"description": "Flour for tomorrow's bread"})[0] == "APPLIED"
    assert world.state.village.communication.notices[nid][-1].version == 2
    assert invoke(world, "village_update_request", request_id=nid,
                  changes={"description": "Flour for tomorrow's bread"})[:2] == ("APPLIED", "NOTICE_UNCHANGED")


def test_t07_unchanged_channel_does_not_trigger_new_read(world):
    world = actor(world, "jory", "village-square")
    assert invoke(world, "village_check_messages")[:2] == ("REJECTED", "COMMUNICATION_INVALID")
    assert invoke(world, "village_request_supply", **supply())[0] == "APPLIED"
    world = actor(world, "garrick", "village-square")
    assert invoke(world, "village_check_messages")[0] == "APPLIED"
    events = len(world.state.village.events)
    assert invoke(world, "village_check_messages")[:2] == ("REJECTED", "COMMUNICATION_INVALID")
    assert len(world.state.village.events) == events


def test_meeting_intent_engine_resolves_site_and_partner(world):
    world = actor(world, "torvin", "village-square")
    result = invoke(world, "village_propose_meeting", project_id="dig_well",
                    start=900, purpose="Dig together")
    assert result[0] == "APPLIED", result
    notice = next(iter(world.state.village.communication.notices.values()))[-1]
    assert notice.terms.destination == world.scenario.village.well_site
    assert notice.terms.recipients == [world.scenario.village.helper]


def test_busy_meeting_partner_reports_channel_unavailable_without_crashing(world):
    world = actor(world, "bren", "tavern-yard")
    world = actor(world, "torvin", "tavern-yard")
    minute = world.state.village.minute
    world.state.village.residents["bren"].available_at = minute + 15
    result = invoke(world, "village_propose_meeting", project_id="dig_well",
                    start=900, purpose="Dig together")
    assert result[:2] == ("REJECTED", "CHANNEL_UNAVAILABLE")
    assert f"bren is busy until {minute + 15}" in result[2]
    assert "noticeboard" in result[2]
    assert not world.state.village.communication.notices


def test_personal_supply_needs_observation(world):
    world = actor(world, "jory", "village-square")
    assert invoke(world, "village_request_supply", **supply())[0] == "APPLIED"
    needs = village_ai.observation(world)["village"]["personal"]["supply_needs"]
    assert any(need["good"] == "flour" and need["owner_id"] == "jory" for need in needs)

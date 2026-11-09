"""Deterministic engine evidence, not claims about unrun model behavior."""

import copy
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

from npc_sim import (
    village, village_ai, village_communication as comm, village_economy as economy,
    village_extensions, village_goods as goods, village_life,
)
from npc_sim.living_village import build_scenario
from npc_sim.models import Entity, Scenario, VillageCommittedWork, VillageEconomyConfig, WorldState
from npc_sim.prompt_context import observation_context
from npc_sim.storage import read_json
from npc_sim.village_communication_models import VillageCommunicationConfig
from npc_sim.world import World
from test_village_expanded import scenario

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workspace():
    path = ROOT / "tests" / f".communication-{uuid.uuid4().hex}"
    path.mkdir()
    yield path
    shutil.rmtree(path)


def create_world(path, *, communication=True, value=None, world_id="communication", start_minute=480):
    value = value.model_copy(deep=True) if value else scenario()
    value.village.start_minute = start_minute
    value.village.goal_deadlines = {}
    value.village.economy = value.village.economy or VillageEconomyConfig()
    if communication:
        comm.install_scenario(value)
    for agent in value.agents:
        agent.start_location = value.village.residents[agent.id].work
    world = World(value, path, world_id, "jory", "session-jory")
    for agent in value.agents:
        world.bind(agent.id, f"session-{agent.id}")
    return world


@pytest.fixture
def world(workspace):
    return create_world(workspace / "world.json")


def actor(world, rid, location=None):
    result = world.bind(rid, f"session-{rid}")
    if location:
        result.actor.location_id = location
    return result


def ready(world, minute=None):
    state = world.state.village
    if minute is not None:
        state.minute = minute
    for person in state.residents.values():
        person.available_at = min(person.available_at, state.minute)
        person.busy_until = min(person.busy_until, state.minute)


def apply(world, action, **args):
    result = comm.apply(world, action, args)
    assert result[0] == "APPLIED", result
    return result


def flour(**extra):
    return {
        "type": "supply_request", "subject": "flour", "description": "Two flour for real bakery work",
        "recipients": ["mara"], "quantity": 2, "destination": "bakery",
        "due_at": 900, "required_fresh_until": 960, **extra,
    }


def post(world, terms=None, **channel):
    apply(world, "village_notice_post", notice=terms or flour(), **channel)
    return next(reversed(world.state.village.communication.notices))


def read(world, nid=None, inbox=None):
    return apply(world, "village_inbox_read" if inbox else "village_notice_read",
                 **({"inbox_id": inbox} if inbox else {}), notice_ids=[nid] if nid else [])


def latest(world, nid):
    return world.state.village.communication.notices[nid][-1]


def known(world, rid, nid):
    return world.state.village.communication.knowledge[rid][nid]


def respond(world, nid, response="accept", **channel):
    memory = known(world, world.actor_id, nid)
    return apply(world, "village_notice_reply", notice_id=nid,
                 expected_version=memory.snapshot.version, response=response, **channel)


def test_post_read_encounter_secondhand_and_repeat_suppression(world):
    actor(world, "jory", "village-square")
    nid = post(world)
    assert "mara" not in world.state.village.communication.knowledge
    guard = actor(world, "garrick", "village-square")
    read(guard)
    first = known(world, "garrick", nid).model_copy(deep=True)
    assert comm.candidates(guard, "jory") == []  # Author already knows.
    actor(world, "mara", "watermill")
    assert comm.apply(guard, "village_notice_tell", {
        "listener": "mara", "notice_id": nid, "known_version": 1})[0] == "REJECTED"
    guard.actor.location_id = "watermill"
    assert len(comm.candidates(guard, "mara")) == 1
    apply(guard, "village_notice_tell", listener="mara", notice_id=nid, known_version=1)
    memory = known(world, "mara", nid)
    assert (memory.confidence, memory.source_actor, memory.hops) == ("secondhand", "garrick", 1)
    assert memory.last_verified_at == first.last_verified_at
    ready(world)
    assert comm.candidates(guard, "mara") == []
    assert comm.apply(guard, "village_notice_tell", {
        "listener": "mara", "notice_id": nid, "known_version": 1})[0] == "REJECTED"
    assert known(world, "garrick", nid) == first


def test_relevance_deterministic_max_three_and_no_finn_flour(world):
    actor(world, "jory", "village-square")
    notices = []
    for index in range(5):
        ready(world, 480 + index * 15)
        notices.append(post(world, flour(subject=f"warning-{index}", type="warning",
                                         quantity=None, destination=None, required_fresh_until=None)))
    guard = actor(world, "garrick", "village-square")
    read(guard)
    actor(world, "finn", "village-square")
    assert len(comm.candidates(guard, "finn")) == 3
    assert comm.candidates(guard, "finn") == comm.candidates(guard, "finn")
    ready(world, 570)
    actor(world, "jory", "village-square")
    flour_id = post(world)
    read(guard)
    assert flour_id not in {item["notice_id"] for item in comm.candidates(guard, "finn")}


@pytest.mark.parametrize("listener_state", ["asleep", "busy", "unavailable", "working", "hidden-elsewhere", "automatic-route"])
def test_real_available_listener_required(world, listener_state):
    actor(world, "jory", "village-square")
    nid = post(world)
    mara = actor(world, "mara", "village-square")
    person = world.state.village.residents["mara"]
    if listener_state == "asleep":
        person.life.sleeping = True
    elif listener_state == "busy":
        person.busy_until = 600
    elif listener_state == "unavailable":
        person.available_at = 600
    elif listener_state == "working":
        person.life.work = VillageCommittedWork(location="watermill", finishes_at=600, outputs={"flour": 1})
    elif listener_state == "automatic-route":
        person.timber_route = True
    else:
        mara.actor.location_id = "watermill"
    assert comm.candidates(world, "mara") == []
    assert comm.apply(world, "village_notice_tell", {
        "listener": "mara", "notice_id": nid, "known_version": 1})[0] == "REJECTED"
    assert "mara" not in world.state.village.communication.knowledge


def test_immutable_versions_stale_paper_no_rollback_or_unreceived_reply(world):
    actor(world, "jory", "village-square")
    nid = post(world)
    guard = actor(world, "garrick", "village-square")
    read(guard)
    original = latest(world, nid).model_dump()
    apply(world, "village_notice_update", notice_id=nid, expected_version=1, notice=flour(quantity=3))
    assert world.state.village.communication.notices[nid][0].model_dump() == original
    assert known(world, "garrick", nid).snapshot.terms.quantity == 2
    mara = actor(world, "mara", "watermill")
    guard.actor.location_id = "watermill"
    apply(guard, "village_notice_tell", listener="mara", notice_id=nid, known_version=1)
    ready(world)
    mara.actor.location_id = "village-square"
    assert comm.apply(mara, "village_notice_reply", {
        "notice_id": nid, "expected_version": 1, "response": "accept"})[0] == "REJECTED"
    assert latest(world, nid).version == 2 and not world.state.village.economy.orders
    read(mara)
    respond(mara, nid)
    assert latest(world, nid).status == "accepted"
    assert known(world, "jory", nid).snapshot.version == 2
    assert economy.observation(world)["orders"] == []
    assert economy.observation(world)["cash"]["jory"]["committed"] == 0
    assert not any(action["id"] == "village_cancel_order" for action in economy.actions(world))
    read(world)
    assert economy.observation(world)["orders"][0]["status"] == "accepted"
    assert known(world, "garrick", nid).snapshot.version == 1


def test_duplicate_needs_and_authorized_meaningful_updates(world):
    actor(world, "jory", "village-square")
    nid = post(world)
    assert comm.apply(world, "village_notice_post", {"notice": flour()})[0] == "REJECTED"
    assert comm.apply(world, "village_notice_post", {"notice": flour(quantity=4)})[0] == "REJECTED"
    assert comm.apply(world, "village_notice_update", {
        "notice_id": nid, "expected_version": 1, "notice": flour()})[0] == "REJECTED"
    mara = actor(world, "mara", "village-square")
    read(mara)
    assert comm.apply(mara, "village_notice_update", {
        "notice_id": nid, "expected_version": 1, "notice": flour(quantity=4)})[0] == "REJECTED"
    apply(world, "village_notice_close", notice_id=nid, expected_version=1, reason="Demand withdrawn")
    ready(world, 510)
    assert comm.apply(world, "village_notice_post", {"notice": flour()})[0] == "REJECTED"
    new = post(world, flour(quantity=4))
    assert new != nid and latest(world, nid).status == "cancelled"


def test_private_inboxes_are_physical_owner_only_and_no_spreading(world):
    box = "workplace-inbox-mara"
    actor(world, "jory", "bakery")
    assert comm.apply(world, "village_inbox_leave", {
        "channel": "inbox", "inbox_id": box, "notice": flour(private=True)})[0] == "REJECTED"
    world.actor.location_id = "watermill"
    nid = post(world, flour(private=True), channel="inbox", inbox_id=box)
    assert comm.apply(world, "village_inbox_read", {"inbox_id": box})[0] == "REJECTED"
    assert "mara" not in world.state.village.communication.knowledge
    mara = actor(world, "mara", "bakery")
    assert comm.apply(mara, "village_inbox_read", {"inbox_id": box})[0] == "REJECTED"
    mara.actor.location_id = "watermill"
    read(mara, inbox=box)
    guard = actor(world, "garrick", "watermill")
    assert comm.apply(mara, "village_notice_tell", {
        "notice_id": nid, "known_version": 1, "listener": "garrick"})[0] == "REJECTED"
    assert comm.apply(mara, "village_notice_reply", {
        "notice_id": nid, "expected_version": 1, "response": "accept", "channel": "board"})[0] == "REJECTED"
    assert nid not in json.dumps(comm.observation(guard))
    assert nid not in json.dumps(village_ai.observation(guard))
    assert not world.state.entities[box].takable
    assert world.state.entities[box].readable_text is None


def test_inbox_acceptance_and_return_reply_separate_visibility(world):
    actor(world, "jory", "watermill")
    nid = post(world, flour(private=True), channel="inbox", inbox_id="workplace-inbox-mara")
    mara = actor(world, "mara", "watermill")
    read(mara, inbox="workplace-inbox-mara")
    assert comm.apply(mara, "village_notice_reply", {
        "notice_id": nid, "expected_version": 1, "response": "accept",
        "channel": "inbox", "inbox_id": "workplace-inbox-jory"})[0] == "REJECTED"
    mara.actor.location_id = "bakery"
    respond(mara, nid, channel="inbox", inbox_id="workplace-inbox-jory")
    assert len(world.state.village.economy.orders) == 1
    assert economy.observation(world)["orders"] == []
    assert known(world, "jory", nid).snapshot.status == "posted"
    world.actor.location_id = "bakery"
    read(world, inbox="workplace-inbox-jory")
    assert known(world, "jory", nid).snapshot.status == "accepted"
    # Mara's original piece of paper was not changed remotely.
    assert world.state.village.communication.placements["workplace-inbox-mara"][nid] == 1


@pytest.mark.parametrize("patch", [
    {"quantity": True}, {"quantity": "2"}, {"private": "false"}, {"due_at": True},
    {"recipients": ["missing"]}, {"recipients": ["mara", "mara"]},
    {"destination": "shovel"}, {"due_at": 479}, {"required_fresh_until": 800},
    {"surprise": 3},
    {"unit_price": True}, {"unit_price": 99}, {"delivery_fee": 99},
])
def test_strict_terms_and_identity_validation(world, patch):
    actor(world, "jory", "village-square")
    assert comm.apply(world, "village_notice_post", {"notice": flour(**patch)})[0] == "REJECTED"
    assert not world.state.village.communication.notices


def test_no_virtual_goods_and_real_funding_payment_unloading(world):
    actor(world, "jory", "village-square")
    nid = post(world)
    before_stocks = copy.deepcopy(world.state.village.stocks)
    before_coins = world.state.village.coins.copy()
    mara = actor(world, "mara", "village-square")
    read(mara)
    respond(mara, nid)
    order = next(iter(world.state.village.economy.orders.values()))
    assert world.state.village.stocks == before_stocks
    assert world.state.village.coins == before_coins
    assert order.in_transit == order.delivered_quantity == order.paid == 0
    mara.actor.location_id = "watermill"
    loads = world.state.village.residents["mara"].shipments
    assert goods.load(mara, "mara", {"flour": 2}, "bakery", loads, 3) is None
    assert latest(world, nid).status == "in_progress"
    assert world.state.village.stocks["bakery"]["flour"] == before_stocks["bakery"]["flour"]
    assert known(world, "jory", nid).snapshot.status == "posted"
    mara.actor.location_id = "bakery"
    assert goods.unload(mara, "mara", loads) is None
    assert order.status == "fulfilled"
    assert world.state.village.stocks["bakery"]["flour"] == before_stocks["bakery"]["flour"] + 2
    assert sum(world.state.village.coins.values()) == sum(before_coins.values())
    assert order.paid == order.quantity * order.price and order.fees_paid == order.delivery_fee
    paid = len(world.state.village.economy.payments)
    assert goods.unload(mara, "mara", loads) is not None
    assert len(world.state.village.economy.payments) == paid
    assert [v.status for v in world.state.village.communication.notices[nid]][-2:] == ["delivered", "completed"]
    # Buyer absent from the unload stays unaware; no remote payment/status injection.
    assert known(world, "jory", nid).snapshot.status == "posted"
    assert economy.observation(world)["orders"] == []


def test_uninformed_carrier_cannot_load_until_actual_receipt(world):
    actor(world, "jory", "village-square")
    nid = post(world)
    mara = actor(world, "mara", "village-square")
    read(mara)
    respond(mara, nid)
    carrier = actor(world, "nella", "watermill")
    loads = world.state.village.residents["nella"].shipments
    assert "ORDER_REQUIRED" in goods.load(carrier, "nella", {"flour": 2}, "bakery", loads, 3)
    mara.actor.location_id = "watermill"
    assert comm.candidates(mara, "nella")
    apply(mara, "village_notice_tell", listener="nella", notice_id=nid, known_version=2)
    ready(world)
    assert goods.load(carrier, "nella", {"flour": 2}, "bakery", loads, 3) is None


def test_reserves_existing_commitments_and_funding_are_not_bypassed(world):
    actor(world, "jory", "village-square")
    nid = post(world)
    mara = actor(world, "mara", "village-square")
    read(mara)
    state = world.state.village
    state.economy.config.reserve_targets = {"watermill": {"flour": 1000, "grain": 1000}}
    assert comm.apply(mara, "village_notice_reply", {
        "notice_id": nid, "expected_version": 1, "response": "accept"})[0] == "REJECTED"
    assert not state.economy.orders
    state.economy.config.reserve_targets = {}
    state.coins["jory"] = 0
    assert comm.apply(mara, "village_notice_reply", {
        "notice_id": nid, "expected_version": 1, "response": "accept"})[0] == "REJECTED"
    assert latest(world, nid).status == "posted" and not state.economy.orders


def test_closed_verified_status_can_be_physically_published_without_retconning_old_paper(world):
    actor(world, "jory", "village-square")
    nid = post(world)
    guard = actor(world, "garrick", "village-square")
    read(guard)
    mara = actor(world, "mara", "village-square")
    read(mara)
    respond(mara, nid)
    mara.actor.location_id = "watermill"
    loads = world.state.village.residents["mara"].shipments
    assert goods.load(mara, "mara", {"flour": 2}, "bakery", loads, 3) is None
    mara.actor.location_id = "bakery"
    assert goods.unload(mara, "mara", loads) is None
    version = latest(world, nid).version
    assert latest(world, nid).status == "completed"
    assert world.state.village.communication.placements["noticeboard"][nid] == 2
    mara.actor.location_id = "village-square"
    apply(mara, "village_notice_update", notice_id=nid, expected_version=version)
    assert latest(world, nid).version == version
    assert world.state.village.communication.placements["noticeboard"][nid] == version
    assert comm.apply(mara, "village_notice_update", {
        "notice_id": nid, "expected_version": version})[0] == "REJECTED"
    assert comm.candidates(guard, "mara") == []  # Guard's old news cannot roll anything back.
    assert known(world, "jory", nid).snapshot.status == "posted"
    read(world)
    assert known(world, "jory", nid).snapshot.status == "completed"
    assert economy.observation(world)["orders"][0]["status"] == "fulfilled"


def test_reserve_loading_waits_for_real_surplus_and_failed_acceptance_needs_change(world):
    actor(world, "jory", "village-square")
    nid = post(world)
    mara = actor(world, "mara", "village-square")
    read(mara)
    policy = world.state.village.economy.config
    policy.reserve_targets = {"watermill": {"flour": 1000}}
    args = {"notice_id": nid, "expected_version": 1, "response": "accept"}
    assert comm.apply(mara, "village_notice_reply", args)[0] == "REJECTED"
    assert "already failed" in comm.apply(mara, "village_notice_reply", args)[2]
    policy.reserve_targets = {"watermill": {"flour": 1}}
    respond(mara, nid)  # Existing real grain can cover planned production, not loading protected flour.
    mara.actor.location_id = "watermill"
    loads = world.state.village.residents["mara"].shipments
    assert "RAW_MATERIAL_RESERVE" in goods.load(mara, "mara", {"flour": 2}, "bakery", loads, 3)
    assert not loads
    before_grain = world.state.village.stocks["watermill"]["grain"]
    result = village_ai.apply(mara, "village_work", {})
    assert result[0] == "APPLIED", result
    assert world.state.village.stocks["watermill"]["grain"] < before_grain
    assert goods.load(mara, "mara", {"flour": 2}, "bakery", loads, 3) is None
    assert world.state.village.stocks["watermill"]["flour"] >= 1


def test_proposed_changes_seen_then_author_revision_and_supersession(world):
    actor(world, "jory", "village-square")
    nid = post(world)
    mara = actor(world, "mara", "village-square")
    read(mara)
    respond(mara, nid, response="propose", reason="I can supply one, not two")
    assert latest(world, nid).status == "seen"
    assert not world.state.village.economy.orders
    read(world)
    apply(world, "village_notice_update", notice_id=nid, expected_version=2, notice=flour(quantity=1))
    assert latest(world, nid).status == "posted" and latest(world, nid).response is None
    assert comm.apply(mara, "village_notice_reply", {
        "notice_id": nid, "expected_version": 2, "response": "accept"})[0] == "REJECTED"
    apply(world, "village_notice_close", notice_id=nid, expected_version=3,
          reason="A materially different plan replaces this one", status="superseded")
    assert latest(world, nid).status == "superseded"
    assert world.state.village.communication.notices[nid][0].status == "posted"


def test_changed_prices_cannot_silently_change_published_terms(world):
    actor(world, "jory", "village-square")
    nid = post(world)
    quoted = latest(world, nid).terms.unit_price
    mara = actor(world, "mara", "village-square")
    read(mara)
    world.state.village.economy.config.prices["flour"] = quoted + 1
    assert comm.apply(mara, "village_notice_reply", {
        "notice_id": nid, "expected_version": 1, "response": "accept"})[0] == "REJECTED"
    assert not world.state.village.economy.orders
    apply(world, "village_notice_update", notice_id=nid, expected_version=1, notice=flour())
    assert latest(world, nid).terms.unit_price == quoted + 1
    read(mara)
    respond(mara, nid)
    assert next(iter(world.state.village.economy.orders.values())).price == quoted + 1


def test_new_face_to_face_orders_use_received_notice_replies_not_legacy_remote_updates(world):
    actor(world, "jory", "bakery")
    supplier = actor(world, "mara", "bakery")
    result = economy.apply(world, "village_propose_order", {
        "supplier": "mara", "product": "flour", "quantity": 2, "deadline": 900,
        "required_fresh_until": 960})
    assert result[:2] == ("APPLIED", "NOTICE_POSTED")
    assert not world.state.village.economy.orders
    nid = next(iter(world.state.village.communication.notices))
    assert known(world, "mara", nid).source_type == "conversation"
    assert world.state.village.residents["mara"].available_at == 485
    ready(world, 485)
    world.actor.location_id = "cottages"
    assert comm.apply(supplier, "village_notice_reply", {
        "notice_id": nid, "expected_version": 1, "response": "accept",
        "channel": "conversation", "listener": "jory"})[0] == "REJECTED"
    supplier.actor.location_id = "village-square"
    respond(supplier, nid)
    assert len(world.state.village.economy.orders) == 1
    assert next(iter(world.state.village.economy.orders.values())).coordination_notice_id == nid
    assert known(world, "jory", nid).snapshot.status == "posted"
    assert economy.observation(world)["orders"] == []


def test_changed_appointment_proposal_revokes_signature_and_old_reply(world):
    ready(world)
    base = world.scenario.village
    lead = actor(world, base.smith, "village-square")
    terms = {"type": "meeting", "subject": "Well appointment", "description": "Work with real supplies",
             "recipients": [base.helper], "destination": base.well_site, "due_at": 540, "project": "dig_well"}
    nid = post(lead, terms)
    partner = actor(world, base.helper, "village-square")
    read(partner)
    apply(lead, "village_notice_update", notice_id=nid, expected_version=1,
          notice={**terms, "due_at": 600})
    assert latest(world, nid).appointment.accepted == [base.smith]
    assert comm.apply(partner, "village_notice_reply", {
        "notice_id": nid, "expected_version": 1, "response": "accept"})[0] == "REJECTED"
    read(partner)
    respond(partner, nid)
    assert latest(world, nid).appointment.due_at == 600
    assert latest(world, nid).appointment.status == "confirmed"


def test_post_cooldown_different_author_needs_and_cross_supplier_duplicates(world):
    actor(world, "jory", "village-square")
    post(world)
    assert comm.apply(world, "village_notice_post", {"notice": flour(recipients=["nella"])})[0] == "REJECTED"
    assert comm.apply(world, "village_notice_post", {"notice": {
        "type": "general", "subject": "Other matter", "description": "A different note"}})[0] == "REJECTED"
    ready(world, 495)
    assert post(world, {"type": "general", "subject": "Other matter", "description": "A different note"})
    assert len(world.state.village.communication.notices) == 2


def test_configuration_fixture_collision_is_transactional(world):
    world.state.village.communication = None
    world.state.entities["workplace-inbox-mara"].location = "bakery"
    before = world.state.model_copy(deep=True)
    with pytest.raises(ValueError, match="Fixture collision"):
        village_extensions.configure(world, "collision", world.revision(), {"communication": {}})
    assert world.state == before


def test_authorized_private_conversation_spends_both_actors_time_and_not_bystanders(world):
    actor(world, "jory", "watermill")
    actor(world, "mara", "watermill")
    actor(world, "garrick", "watermill")
    revision = world.revision()
    result = world.act("private", revision, "village_notice_post", {
        "notice": flour(private=True), "channel": "conversation", "listener": "mara"})
    assert result["outcome"] == "APPLIED", result
    state = world.state.village
    assert state.residents["mara"].available_at == state.residents["jory"].available_at == 485
    assert "garrick" not in state.communication.knowledge
    assert not state.communication.placements
    nid = next(iter(state.communication.notices))
    assert nid not in json.dumps(village_ai.observation(actor(world, "garrick")))


@pytest.mark.parametrize("product", ["milk", "eggs"])
def test_future_livestock_notice_acceptance_requires_real_care_without_inventing_output(workspace, product):
    world = create_world(workspace / "animals.json", value=build_scenario(scenario()))
    buyer = actor(world, "elsie", "village-square")
    nid = post(buyer, {
        "type": "supply_request", "subject": product, "description": "One real cared-for farm product",
        "recipients": ["ada"], "quantity": 1, "destination": "tavern",
        "due_at": 900, "required_fresh_until": 960})
    supplier = actor(world, "ada", "village-square")
    read(supplier)
    args = {"notice_id": nid, "expected_version": 1, "response": "accept"}
    assert comm.apply(supplier, "village_notice_reply", args)[0] == "REJECTED"
    # Unit precondition: actual animals already cared for today. No milk/eggs,
    # feed, coins or virtual production are supplied by the communication layer.
    care = world.state.village.livestock.cow if product == "milk" else world.state.village.livestock.hens
    care.day, care.fed, care.watered, care.cleaned = 0, True, True, True
    before = copy.deepcopy(world.state.village.stocks)
    respond(supplier, nid)
    assert latest(world, nid).status == "accepted"
    assert world.state.village.stocks == before
    assert world.state.village.stocks["farm"][product] == 0


def test_delivery_deadline_carrying_capacity_and_frozen_fixture_locations_are_checked(world):
    actor(world, "jory", "village-square")
    nid = post(world, flour(due_at=485, required_fresh_until=490))
    mara = actor(world, "mara", "village-square")
    read(mara)
    assert comm.apply(mara, "village_notice_reply", {
        "notice_id": nid, "expected_version": 1, "response": "accept"})[0] == "REJECTED"
    assert not world.state.village.economy.orders
    # A worker's box is not a remote mailbox keyed by occupation.
    box = world.state.entities["workplace-inbox-mara"]
    box.location = "bakery"
    mara.actor.location_id = "watermill"
    assert comm.apply(mara, "village_inbox_read", {"inbox_id": "workplace-inbox-mara"})[0] == "REJECTED"


def test_deadline_and_authority_and_no_absent_conversation_signature(world):
    actor(world, "jory", "village-square")
    nid = post(world)
    mara = actor(world, "mara", "village-square")
    read(mara)
    world.actor.location_id = "bakery"
    assert comm.apply(mara, "village_notice_reply", {
        "notice_id": nid, "expected_version": 1, "response": "accept",
        "channel": "conversation", "listener": "jory"})[0] == "REJECTED"
    ready(world, 905)
    comm.advance(world)
    assert latest(world, nid).status == "expired"
    assert known(world, "mara", nid).snapshot.status == "posted"
    assert comm.apply(mara, "village_notice_reply", {
        "notice_id": nid, "expected_version": 1, "response": "accept"})[0] == "REJECTED"
    assert not world.state.village.economy.orders


def test_bounded_secondhand_hops_and_confidence_cannot_inflate(world):
    actor(world, "jory", "village-square")
    nid = post(world, {"type": "warning", "subject": "Bridge warning", "description": "Use caution"})
    previous_actor = "jory"
    for index, rid in enumerate(["garrick", "mara", "finn", "hobb", "ada", "bren", "iona", "rowan"], 1):
        ready(world, 480 + 20 * index)
        speaker = actor(world, previous_actor, "village-square")
        actor(world, rid, "village-square")
        apply(speaker, "village_notice_tell", listener=rid, notice_id=nid, known_version=1)
        memory = known(world, rid, nid)
        assert memory.hops == index and memory.confidence == "secondhand"
        assert len(memory.source_chain) <= 4 and memory.last_verified_at == 480
        previous_actor = rid
    ready(world, 700)
    speaker = actor(world, "rowan", "village-square")
    actor(world, "nella", "village-square")
    assert comm.candidates(speaker, "nella") == []
    # Direct reading can legitimately restore direct confidence, not repeated testimony.
    read(speaker)
    assert known(world, "rowan", nid).confidence == "direct"


def test_no_bell_global_location_bypass(world):
    before = len(world.state.village.residents["mara"].memories)
    actor(world, "jory", "bakery")
    village.announce(world, "jory", "discovery", "shovel", "Local discovery")
    assert len(world.state.village.residents["mara"].memories) == before
    assert not world.state.village.notices
    village.learn(world, "jory", "shovel", "bakery", "observation", 1)
    assert "shovel" not in world.state.village.residents["mara"].beliefs
    assert "Current workplace stock" not in village.read_noticeboard(actor(world, "jory", "village-square"))
    assert not any(a["id"] == "village_meeting" for a in world.actions())
    before = len(world.state.village.residents["jory"].memories)
    village.announce(world, "village-bell", "breakdown", world.scenario.village.mill_equipment, "A local failure")
    assert len(world.state.village.residents["jory"].memories) == before
    assert "mara" in world.state.village.events[-1].witnesses


def test_default_board_reads_prioritize_novel_paper_and_eventually_cover_large_boards(world):
    actor(world, "jory", "village-square")
    for index in range(16):
        ready(world, 480 + index * 15)
        post(world, {"type": "general", "subject": f"Notice {index}", "description": "A durable public note"})
    guard = actor(world, "garrick", "village-square")
    read(guard)
    assert len(world.state.village.communication.knowledge["garrick"]) == 12
    assert "notice-16" in world.state.village.communication.knowledge["garrick"]
    read(guard)
    assert len(world.state.village.communication.knowledge["garrick"]) == 16
    assert len(comm.observation(guard)["communication"]["notices"]) == 12
    assert len(observation_context(village_ai.observation(guard))["village"]["communication"]["notices"]) == 6


def test_revision_checked_empty_opt_in_preserves_world_old_orders(workspace):
    world = create_world(workspace / "legacy.json", communication=False)
    assert world.state.village.communication is None
    world.actor.location_id = world.state.actors["mara"].location_id
    assert economy.apply(world, "village_propose_order", {
        "supplier": "mara", "product": "flour", "quantity": 1, "deadline": 900,
        "required_fresh_until": 960})[0] == "APPLIED"
    order = next(iter(world.state.village.economy.orders.values()))
    assert economy.apply(actor(world, "mara"), "village_accept_order", {"order_id": order.id})[0] == "APPLIED"
    state = world.state.model_copy(deep=True)
    revision = world.revision()
    result = village_extensions.configure(world, "enable", revision, {"communication": {}})
    assert village_extensions.configure(world, "enable", revision, {"communication": {}}) == result
    with pytest.raises(ValueError):
        village_extensions.configure(world, "other", revision, {"communication": {}})
    communication = world.state.village.communication
    assert not communication.notices and not communication.knowledge and not communication.placements
    assert not communication.shares and not communication.failed_contacts
    assert state.village.stocks == world.state.village.stocks and state.village.coins == world.state.village.coins
    assert state.actors == world.state.actors and state.village.economy == world.state.village.economy
    assert comm.can_use_order(world, order)  # Existing commitments are grandfathered, not rewritten.
    assert len(communication.boxes) == 12
    for eid, value in state.entities.items():
        assert world.state.entities[eid] == value
    restarted = World(world.scenario, world.path, "communication", "jory", "session-jory")
    assert restarted.state.village.communication == communication


def test_world_action_time_idempotency_archive_and_compact_observation(world):
    actor(world, "jory", "village-square")
    request = {"notice": flour()}
    revision = world.revision()
    first = world.act("post", revision, "village_notice_post", request)
    assert first["outcome"] == "APPLIED", first
    assert world.state.village.residents["jory"].available_at == 485
    assert world.act("post", revision, "village_notice_post", request) == first
    world.state.village.minute = 485
    second_revision = world.revision()
    second = world.act("read", second_revision, "village_notice_read", {})
    assert second["outcome"] == "APPLIED", second
    assert len(world.state.requests) == 1
    assert world.act("post", revision, "village_notice_post", request) == first
    world.save()
    restored = World(world.scenario, world.path, "communication", "jory", "session-jory")
    assert restored.act("post", revision, "village_notice_post", request) == first
    compact = observation_context(restored.observe())
    assert len(compact["village"]["communication"]["notices"]) <= 6
    assert "knowledge" not in compact["village"]["communication"]


def meeting(world, project="dig_well", due_at=540):
    ready(world)
    base = world.scenario.village
    crew, site = comm._project(world, project)
    lead = actor(world, crew[0], "village-square")
    nid = post(lead, {"type": "meeting", "subject": project, "description": "Real repair appointment",
                     "recipients": [crew[1]], "destination": site, "due_at": due_at, "project": project})
    partner = actor(world, crew[1], "village-square")
    read(partner)
    respond(partner, nid)
    return lead, partner, nid


@pytest.mark.parametrize("project", ["dig_well", "repair_mill"])
def test_appointments_require_personal_confirmation_presence_time_supplies_and_each_worker(world, project):
    lead, partner, nid = meeting(world, project)
    crew, site = comm._project(world, project)
    assert latest(world, nid).appointment.status == "confirmed"
    # Acceptance doesn't teleport the confirmation to the proposer.
    ready(world, 540)
    lead.actor.location_id = partner.actor.location_id = site
    assert "personally receive" in comm.project_blocker(lead, project)
    lead.actor.location_id = "village-square"
    read(lead)
    lead.actor.location_id = site
    partner.actor.location_id = partner.scenario.village.residents[partner.actor_id].work
    if partner.actor.location_id == site:
        partner.actor.location_id = "village-square"
    assert "actual workers" in comm.project_blocker(lead, project)
    partner.actor.location_id = site
    assert comm.project_blocker(lead, project) is None
    assert village_ai.apply(lead, "village_project", {"project": project})[0] == "REJECTED"
    base = world.scenario.village
    supplies = [base.shovel, base.timber, base.rope] if project == "dig_well" else [
        base.repair_part, base.hammer]
    for eid in supplies:
        world.state.entities[eid].location = site
        world.state.entities[eid].hidden = False
    if project == "repair_mill":
        world.state.village.breakdown = True
        # Produce from real initial timber, then move that exact plank.
        world.state.village.economy.config.production_targets = {"workshop": {"planks": 1}}
        lead.actor.location_id = "workshop"
        result = village_ai.apply(lead, "village_work", {})
        assert result[0] == "APPLIED", result
        lead.actor.location_id = site
        lots = goods.take(world, "workshop", "planks", 1)
        goods.put(world, site, "planks", 1, lots)
    ready(world)
    for member in [lead, partner, lead, partner, lead, partner]:
        result = village_ai.apply(member, "village_project", {"project": project})
        assert result[0] == "APPLIED", result
    comm.advance(world)
    assert latest(world, nid).status == "completed"
    assert set(world.state.village.project_contributions[project]) == set(crew)


def test_appointment_version_change_revokes_old_signature_and_cannot_auto_travel(world):
    lead, partner, nid = meeting(world)
    positions = copy.deepcopy(world.state.actors)
    assert positions == world.state.actors
    # Committed meeting terms require explicit cancellation, not silently moving its time.
    read(lead)
    assert comm.apply(lead, "village_notice_update", {
        "notice_id": nid, "expected_version": 2,
        "notice": {**latest(world, nid).terms.model_dump(), "due_at": 600}})[0] == "REJECTED"
    ready(world, 605)
    comm.advance(world)
    assert latest(world, nid).appointment.status == "expired"
    assert known(world, partner.actor_id, nid).snapshot.appointment.status == "confirmed"
    assert world.state.actors == positions


def test_work_continues_while_waiting_and_failed_contacts_deduplicate(world):
    actor(world, "jory", "village-square")
    post(world)
    world.actor.location_id = "bakery"
    world.state.village.economy.config.production_targets = {"bakery": {"bread": 6}}
    result = village_ai.apply(world, "village_work", {})
    assert result[0] == "APPLIED", result
    # No wait-lock or artificial missing input added by posting.
    world.state.village.residents["jory"].life.work = None
    first = economy.apply(world, "village_plan_supplies", {"supplier": "mara"})
    second = economy.apply(world, "village_plan_supplies", {"supplier": "mara"})
    assert first[1] == "OUT_OF_REACH" and second[1] == "CONTACT_ALREADY_FAILED"


def test_future_scenario_default_and_legacy_load_are_separate(workspace):
    base = scenario()
    future = build_scenario(base)
    assert future.village.communication is not None
    assert base.village.communication is None
    assert len([eid for eid in future.entities if eid.startswith("workplace-inbox-")]) == 12
    value = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "millhaven-living.json"))
    assert value.village.communication is not None
    legacy = create_world(workspace / "old.json", communication=False)
    assert legacy.state.village.communication is None
    assert comm.initial_state(legacy.scenario) is None
    assert not any(action["id"] in comm.VERBS for action in legacy.actions())


class Replay:
    """Same actors, real actions and elapsed time; only coordination policy differs."""

    def __init__(self, world):
        self.world = world
        self.actions = 0
        self.movement = 0
        self.fruitless_searches = 0
        self.search_steps = 0
        self.blocked = 0

    def do(self, rid, action, *, expect="APPLIED", **args):
        bound = self.world.bind(rid, self.world.state.actors[rid].session_id)
        person = bound.state.village.residents[rid]
        if person.available_at > bound.state.village.minute:
            village.advance(bound, person.available_at - bound.state.village.minute)
        self.actions += 1
        result = bound.act(f"script-{self.actions}", bound.revision(), action, args)
        assert result["outcome"] == expect, (action, result)
        self.blocked += expect == "REJECTED"
        self.movement += action == "go"
        return result

    def walk(self, rid, destination, *, search=False):
        bound = self.world.bind(rid, self.world.state.actors[rid].session_id)
        for place in village.route(bound, bound.actor.location_id, destination):
            origin = bound.actor.location_id
            door = next(eid for eid, entity in bound.state.entities.items()
                        if entity.kind == "door" and {entity.location, entity.destination} == {origin, place})
            self.do(rid, "go", target=door)
            self.search_steps += search

    def search(self, destination):
        self.walk("jory", destination, search=True)
        self.do("jory", "village_propose_order", expect="REJECTED", supplier="mara",
                product="flour", quantity=2, deadline=3900, required_fresh_until=4020)
        self.fruitless_searches += 1

    def finish(self):
        state = self.world.state.village
        village.advance(self.world, 3960 - state.minute)  # Same real 18:00 cooking window.
        self.do("elsie", "village_work")
        self.walk("jory", "tavern")
        village.advance(self.world, 4020 - state.minute)  # Day 3, 19:00 supper.
        self.do("jory", "village_eat")
        return {
            "fruitless_search_trips": self.fruitless_searches,
            "person_search_walk_minutes": self.search_steps * 5,
            "all_walk_minutes": self.movement * 5,
            "blocked_actions": self.blocked, "world_actions": self.actions, "model_turns": 0,
            "delivered_units": sum(order.delivered_quantity for order in state.economy.orders.values()),
            "fulfilled_orders": sum(order.status == "fulfilled" for order in state.economy.orders.values()),
            "paid_for_goods": sum(order.paid for order in state.economy.orders.values()),
            "delivery_fees": sum(order.fees_paid for order in state.economy.orders.values()),
            "bread_produced": sum(m.quantity for m in state.economy.movements
                                  if m.kind == "produced" and m.product == "bread"),
            "meals_eaten": sum(meal.status == "eaten" for p in state.residents.values()
                               for meal in p.life.meals.values()),
            "missed_meals": sum(meal.status == "missed" for p in state.residents.values()
                                for meal in p.life.meals.values()),
            "coins_conserved": sum(state.coins.values()),
        }


def test_paired_day3_like_replay_identical_constraints_no_paid_ai(workspace):
    """Constructed matched fixture at Day 3 08:00, not an archived Day 3 snapshot."""
    base = scenario()
    base.village.economy = VillageEconomyConfig(production_targets={"bakery": {"bread": 6}})
    control = create_world(workspace / "control.json", value=base, communication=False, start_minute=3360)
    treatment = create_world(workspace / "treatment.json", value=base, communication=True, start_minute=3360)
    for world in (control, treatment):
        actor(world, "mara", "cottages")
        actor(world, "garrick", "village-square")
        world.save()
    # Full initial world and scenario equality except explicitly permitted empty capability/fixtures.
    initial = []
    for world in (control, treatment):
        state = world.state.model_dump(mode="json")
        state.pop("world_id")
        state["village"].pop("communication")
        state["entities"] = {key: value for key, value in state["entities"].items()
                             if not key.startswith("workplace-inbox-")}
        initial.append(state)
    assert initial[0] == initial[1]
    original_coins = sum(control.state.village.coins.values())
    old, new = Replay(control), Replay(treatment)
    old.search("watermill")
    old.walk("mara", "watermill")  # Ordinary work commute, identical in both arms.
    old.walk("garrick", "watermill")  # Ordinary patrol, not a message-delivery objective.
    old.search("cottages")
    old.walk("jory", "watermill", search=True)
    old.do("jory", "village_propose_order", supplier="mara", product="flour", quantity=2,
           deadline=3900, required_fresh_until=4020)
    oid = next(iter(control.state.village.economy.orders))
    old.do("mara", "village_accept_order", order_id=oid)
    old.walk("jory", "bakery")
    old.do("jory", "village_work")
    old.do("mara", "village_load_goods", destination="bakery", goods={"flour": 2})
    old.walk("mara", "bakery")
    old.do("mara", "village_unload_goods")
    control_metrics = old.finish()

    new.walk("jory", "village-square")
    new.do("jory", "village_notice_post", notice=flour(due_at=3900, required_fresh_until=4020))
    nid = next(iter(treatment.state.village.communication.notices))
    new.do("garrick", "village_notice_read", notice_ids=[nid])
    new.walk("jory", "bakery")
    new.do("jory", "village_work")  # Real baking continues before the answer arrives.
    new.walk("mara", "watermill")
    new.walk("garrick", "watermill")
    new.do("garrick", "village_notice_tell", listener="mara", notice_id=nid, known_version=1)
    new.walk("mara", "village-square")
    new.do("mara", "village_notice_reply", notice_id=nid, expected_version=1, response="accept")
    new.walk("mara", "watermill")
    new.do("mara", "village_load_goods", destination="bakery", goods={"flour": 2})
    new.walk("mara", "bakery")
    new.do("mara", "village_unload_goods")
    treatment_metrics = new.finish()
    assert control_metrics["fruitless_search_trips"] == 2
    assert treatment_metrics["fruitless_search_trips"] == 0
    assert treatment_metrics["person_search_walk_minutes"] < control_metrics["person_search_walk_minutes"]
    assert treatment_metrics["all_walk_minutes"] < control_metrics["all_walk_minutes"]
    for field in ("delivered_units", "fulfilled_orders", "paid_for_goods", "delivery_fees",
                  "bread_produced", "meals_eaten", "missed_meals", "coins_conserved"):
        assert control_metrics[field] == treatment_metrics[field], field
    assert treatment_metrics["delivered_units"] == 2 and treatment_metrics["bread_produced"] == 2
    assert treatment_metrics["meals_eaten"] == 1
    assert treatment_metrics["coins_conserved"] == original_coins
    print("\nPAIRED_DAY3_ENGINE_REPLAY " + json.dumps({"control": control_metrics, "treatment": treatment_metrics},
                                                    sort_keys=True))


def test_real_day3_midnight_backup_paired_supply_compatibility_replay(workspace):
    """Offline opt-in driver: read only baseline/scenario, never the large world archive."""
    supplied = os.environ.get("NPCSIM_DAY3_BASELINE")
    source = (Path(supplied) if supplied else
              ROOT / "data" / "backups" / "livestock-3b3f16b2d9cc4d3787062c60bac114c1" / "baseline.json")
    frozen_path = source.with_name("scenario.json")
    if not source.is_file() or not frozen_path.is_file():
        if supplied:
            pytest.fail("NPCSIM_DAY3_BASELINE requires baseline.json and adjacent frozen scenario.json")
        pytest.skip("Set NPCSIM_DAY3_BASELINE to an archived Day 3 midnight baseline")
    source_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in (source, frozen_path)}
    baseline = read_json(source)
    frozen = Scenario.model_validate(read_json(frozen_path))
    assert baseline["minute"] == baseline["village"]["minute"] == 2880
    template = WorldState(
        world_id=baseline["world_id"], scenario_id=frozen.id, exit_location=frozen.exit_location,
        revision=baseline["revision"], village=baseline["village"],
        actors=baseline["actors"], entities=baseline["entities"])
    assert template.village.communication is None

    def restore(name):
        path = workspace / name
        path.write_text(template.model_dump_json(), encoding="utf-8")
        return World(frozen.model_copy(deep=True), path, template.world_id, "jory",
                     template.actors["jory"].session_id)

    try:
        control, treatment = restore("midnight-control.json"), restore("midnight-treatment.json")
        initial = control.state.model_dump(mode="json")
        assert initial == treatment.state.model_dump(mode="json") == template.model_dump(mode="json")
        comm.configure(treatment, {})
        normalized = treatment.state.model_dump(mode="json")
        normalized["village"]["communication"] = None
        for eid in comm.fixture_specs(frozen):
            del normalized["entities"][eid]
        assert normalized == initial
        assert control.scenario == treatment.scenario == frozen
        for field in ("notices", "knowledge", "placements", "shares", "cooldowns",
                      "failed_contacts", "failed_replies"):
            assert not getattr(treatment.state.village.communication, field)

        # Both arms retain midnight sleep, hunger, physical inventory and old
        # contracts; time advances normally to the workers' actual waking time.
        buyer, supplier = frozen.village.smith, frozen.village.carpenter
        assert template.actors[buyer].location_id == template.actors[supplier].location_id == "cottages"
        assert template.village.stocks["workshop"]["planks"] == 2
        assert template.village.stocks["forge"]["planks"] == 2
        for world in (control, treatment):
            village.advance(world, 3390 - world.state.village.minute)
            assert world.state.actors == template.actors

        deadline = 3600
        old, new = Replay(control), Replay(treatment)
        # Honour the frozen mandatory breakfast policy: both workers physically
        # try the empty tavern, recording real missed meals rather than removing
        # the meal gate, adding food, or silently clearing hunger.
        for replay in (old, new):
            for rid in (buyer, supplier):
                replay.walk(rid, "tavern")
                result = replay.do(rid, "village_eat")
                assert result["code"] == "MEAL_MISSED"
        old.walk(supplier, "workshop")
        old.walk(buyer, "workshop")
        old.do(buyer, "village_propose_order", supplier=supplier, product="planks", quantity=1,
               deadline=deadline, required_fresh_until=deadline)
        old_id = next(oid for oid in control.state.village.economy.orders
                      if oid not in template.village.economy.orders)
        old.do(supplier, "village_accept_order", order_id=old_id)
        old.walk(buyer, "forge")
        old.do(supplier, "village_load_goods", destination="forge", goods={"planks": 1})
        old.walk(supplier, "forge")
        old.do(supplier, "village_unload_goods")

        new.walk(buyer, "village-square")
        new.do(buyer, "village_notice_post", notice={
            "type": "supply_request", "subject": "planks", "description": "One real plank for the forge",
            "recipients": [supplier], "quantity": 1, "destination": "forge",
            "due_at": deadline, "required_fresh_until": deadline})
        nid = next(iter(treatment.state.village.communication.notices))
        new.walk(supplier, "village-square")
        new.do(supplier, "village_notice_read", notice_ids=[nid])
        new.do(supplier, "village_notice_reply", notice_id=nid, expected_version=1, response="accept")
        new_id = next(oid for oid in treatment.state.village.economy.orders
                      if oid not in template.village.economy.orders)
        new.walk(buyer, "forge")
        new.walk(supplier, "workshop")
        new.do(supplier, "village_load_goods", destination="forge", goods={"planks": 1})
        new.walk(supplier, "forge")
        new.do(supplier, "village_unload_goods")

        measurements = {}
        for label, replay, oid in (("control", old, old_id), ("treatment", new, new_id)):
            world, state = replay.world, replay.world.state.village
            delivery_minute = state.minute
            village.advance(world, 3460 - state.minute)  # Same 09:40 cutoff, including action cooldowns.
            order = state.economy.orders[oid]
            assert order.status == "fulfilled" and order.delivered_quantity == 1
            assert order.quantity == 1 and order.paid > 0 and order.fees_paid > 0
            expected_stocks = copy.deepcopy(template.village.stocks)
            expected_stocks["workshop"]["planks"] -= 1
            expected_stocks["forge"]["planks"] += 1
            assert state.stocks == expected_stocks
            assert sum(state.coins.values()) == sum(template.village.coins.values())
            assert state.coins[buyer] == template.village.coins[buyer] - order.paid - order.fees_paid
            assert all(state.economy.orders[key] == value
                       for key, value in template.village.economy.orders.items())
            assert world.scenario == frozen
            assert not replay.blocked
            measurements[label] = {
                "initial_minute": 2880, "first_action_minute": 3390, "cutoff_minute": state.minute,
                "delivery_minute": delivery_minute,
                "delivered_units": order.delivered_quantity, "goods_payment": order.paid,
                "delivery_fee": order.fees_paid, "go_actions": replay.movement,
                "actions": replay.actions, "model_calls": 0,
                "participant_breakfasts": {
                    rid: [meal.status for meal in state.residents[rid].life.meals.values()
                          if meal.kind == "breakfast" and meal.due == 3390]
                    for rid in (buyer, supplier)}}
            assert measurements[label]["participant_breakfasts"] == {buyer: ["missed"], supplier: ["missed"]}
        assert control.state.village.stocks == treatment.state.village.stocks
        assert control.state.village.coins == treatment.state.village.coins
        print("\nREAL_DAY3_BASELINE_ENGINE_REPLAY " + json.dumps({
            "baseline_sha256": source_hashes[source], "scenario_sha256": source_hashes[frozen_path],
            **measurements}, sort_keys=True))
    finally:
        assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_hashes} == source_hashes


def test_appointment_integration_replay_real_routes_receipts_and_consumption(workspace):
    def run(path):
        world = create_world(path, start_minute=510)
        replay = Replay(world)
        base, state = village.context(world)
        crew = [base.smith, base.helper]
        lead, partner = (actor(world, rid) for rid in crew)
        assert lead.actor.location_id == "forge"
        assert partner.actor.location_id == "stable"
        initial = world.state.model_dump(mode="json")
        initial_supplies = {eid: world.state.entities[eid].model_dump()
                            for eid in (base.shovel, base.timber, base.rope)}
        due = 660

        replay.walk(base.smith, "village-square")
        replay.do(base.smith, "village_notice_post", notice={
            "type": "meeting", "subject": "Well crew appointment",
            "description": "Meet with the actual shovel, timber and rope",
            "recipients": [base.helper], "destination": base.well_site,
            "due_at": due, "project": "dig_well"})
        nid = next(iter(state.communication.notices))

        # The proposer leaves before the partner reads/accepts, and obtains
        # the existing shovel through ordinary movement and inventory actions.
        replay.walk(base.smith, initial_supplies[base.shovel]["location"])
        replay.do(base.smith, "take", target=base.shovel)
        replay.walk(base.helper, "village-square")
        replay.do(base.helper, "village_notice_read", notice_ids=[nid])
        replay.do(base.helper, "village_notice_reply", notice_id=nid,
                  expected_version=1, response="accept")
        assert latest(world, nid).appointment.status == "confirmed"
        assert known(world, base.smith, nid).snapshot.appointment.status == "proposed"
        assert lead.actor.location_id == initial_supplies[base.shovel]["location"]

        replay.walk(base.helper, "forge")
        replay.do(base.helper, "take", target=base.timber)
        replay.do(base.helper, "take", target=base.rope)
        replay.walk(base.smith, "village-square")
        assert state.minute < due - 5
        waiting_positions = {rid: person.location_id for rid, person in world.state.actors.items()}
        village.advance(world, due - 5 - state.minute)
        assert {rid: person.location_id for rid, person in world.state.actors.items()} == waiting_positions
        replay.walk(base.helper, "village-square")
        village.advance(world, due - state.minute)
        assert state.minute == due
        assert lead.actor.location_id == partner.actor.location_id == "village-square"

        missing_receipt = replay.do(base.smith, "village_project", project="dig_well", expect="REJECTED")
        assert missing_receipt["code"] == "APPOINTMENT_REQUIRED"
        assert "personally receive" in missing_receipt["explanation"]
        assert not state.project_contributions and not state.well_dug

        replay.do(base.helper, "village_notice_tell", listener=base.smith,
                  notice_id=nid, known_version=2)
        for rid in crew:
            plan = known(world, rid, nid).snapshot.appointment
            assert plan.status == "confirmed"
            assert plan.version == 2 and plan.due_at == due and plan.place == base.well_site
            assert set(plan.accepted) == set(crew)

        replay.walk(base.smith, base.well_site)
        missing_partner = replay.do(base.smith, "village_project", project="dig_well", expect="REJECTED")
        assert missing_partner["code"] == "APPOINTMENT_REQUIRED"
        assert "actual workers" in missing_partner["explanation"]
        assert lead.actor.location_id == base.well_site
        assert partner.actor.location_id == "village-square"
        assert not state.project_contributions and not state.well_dug
        for eid in (base.timber, base.rope):
            assert world.state.entities[eid].location == base.helper
            assert eid in partner.actor.inventory

        replay.walk(base.helper, base.well_site)
        for rid in crew * 3:
            available = max(state.residents[member].available_at for member in crew)
            if available > state.minute:
                village.advance(world, available - state.minute)
            assert due <= state.minute <= due + 60
            replay.do(rid, "village_project", project="dig_well")
        village.advance(world, 5)

        assert state.well_dug and latest(world, nid).appointment.status == "completed"
        assert latest(world, nid).status == "completed"
        assert state.project_contributions["dig_well"] == {base.smith: 3, base.helper: 3}
        for eid in (base.timber, base.rope):
            entity = world.state.entities[eid]
            assert entity.location is None and entity.hidden and not entity.takable
            assert all(eid not in person.inventory for person in world.state.actors.values())
        assert base.shovel in lead.actor.inventory
        assert world.state.entities[base.shovel].location == base.smith
        assert state.coins == initial["village"]["coins"]
        assert state.stocks == initial["village"]["stocks"]
        travel = [event for event in state.events if event.actor in crew and event.action == "go"]
        assert {event.actor for event in travel} == set(crew)
        assert replay.blocked == 2
        world.save()
        restarted = World(world.scenario, world.path, "communication", "jory", "session-jory")
        assert restarted.state.village.well_dug
        assert latest(restarted, nid).appointment.status == "completed"
        assert replay.movement == 12
        return initial, {
            "go_actions": replay.movement, "world_actions": replay.actions,
            "blocked_actions": replay.blocked, "completed_at": state.minute,
            "contributions": state.project_contributions["dig_well"],
            "consumed_supplies": [base.timber, base.rope], "model_calls": 0}

    initial_a, result_a = run(workspace / "appointment-a.json")
    initial_b, result_b = run(workspace / "appointment-b.json")
    assert initial_a == initial_b
    assert result_a == result_b
    print("\nAPPOINTMENT_ENGINE_REPLAY " + json.dumps(result_a, sort_keys=True))

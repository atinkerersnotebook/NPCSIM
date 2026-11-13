"""Regression checks for recovered and partially bound timed village schedules."""

import pytest

from npc_sim import village, village_ai, village_communication as comm, village_turns
from npc_sim.models import VillageConversation
from npc_sim.world import World
from test_village_expanded import scenario


def test_next_actor_skips_unbound_residents_and_stale_turn_hints(tmp_path):
    value = scenario()
    world = World(value, tmp_path / "world.json", "scheduler", "nella", "session-nella")
    state = world.state.village
    state.minute = 1075
    state.residents["nella"].available_at = 1075
    state.residents["nella"].life.sleeping = False
    state.residents["nella"].life.work = None
    state.turn_cursor = 9999
    state.last_scheduled_actor = "missing"
    state.free_turn_actor = "missing"
    state.conversations["stale"] = VillageConversation(
        speaker="nella", listener="missing", next_speaker="missing",
        turns=1, started_at=1075, expires_at=1095)

    assert village_turns.next_actor(world, stop_at_minute=1080) == "nella"
    assert state.conversations == {}
    assert state.last_scheduled_actor == "nella"
    assert state.turn_cursor < len(value.village.residents)
    world.save()
    restored = World(value, world.path, "scheduler", "nella", "session-nella")
    assert restored.state.village.conversations == {}


@pytest.mark.parametrize("project,lead,partner,site", [
    ("dig_well", "torvin", "bren", "tavern-yard"),
    ("repair_mill", "hobb", "mara", "watermill"),
])
def test_crew_busy_reports_actual_partner_release_without_changing_progress(
        tmp_path, project, lead, partner, site):
    value = scenario()
    world = World(value, tmp_path / "world.json", "crew-busy", lead, f"session-{lead}")
    world.bind(partner, f"session-{partner}")
    state = world.state.village
    state.minute = 600
    state.residents[partner].busy_until = 630
    world.actor.location_id = site
    world.state.actors[partner].location_id = site
    result = village_ai.apply(world, "village_project", {"project": project})
    assert result[:2] == ("REJECTED", "CREW_BUSY")
    assert village.clock_time(630) in result[2]
    assert state.project_contributions == {}


def test_active_crew_session_survives_expired_window_and_persists(tmp_path):
    value = scenario()
    comm.install_scenario(value)
    world = World(value, tmp_path / "world.json", "crew-session", "torvin", "session-torvin")
    partner = world.bind("bren", "session-bren")
    state = world.state.village
    state.minute = 540
    for rid in ("torvin", "bren"):
        state.residents[rid].life.sleeping = False
        state.residents[rid].life.work = None
        state.residents[rid].available_at = 540
        state.residents[rid].life.last_woke = 540
        state.residents[rid].life.urgent_project = "dig_well"
        state.residents[rid].life.urgent_until = 900
        world.state.actors[rid].location_id = value.village.well_site
    from npc_sim.village_communication_models import Appointment, NoticeKnowledge, NoticeTerms, NoticeVersion
    plan = Appointment(id="crew-1", version=1, project="dig_well", members=["torvin", "bren"],
                       accepted=["torvin", "bren"], place=value.village.well_site,
                       due_at=540, end_at=600, status="confirmed")
    notice = NoticeVersion(id="crew-1", version=1, author="torvin",
                           terms=NoticeTerms(type="meeting", subject="dig_well", description="Repair well",
                                             project="dig_well", recipients=["bren"],
                                             destination=value.village.well_site, due_at=540),
                           appointment=plan, status="accepted", created_at=500, updated_at=530)
    state.communication.notices["crew-1"] = [notice]
    for rid in ("torvin", "bren"):
        state.communication.knowledge[rid] = {"crew-1": NoticeKnowledge(
            snapshot=notice.model_copy(deep=True), heard_at=530, source_type="witness")}
    for eid in (value.village.shovel, value.village.timber, value.village.rope):
        world.state.entities[eid].location = value.village.well_site
        world.state.entities[eid].hidden = False
    first = village_ai.apply(world, "village_project", {"project": "dig_well"})
    assert first[:2] == ("APPLIED", "PROJECT_PROGRESS"), first
    session = state.crew_sessions["dig_well"]
    assert session.appointment_id == "crew-1"
    assert session.workers == ["torvin", "bren"]
    assert session.contributions == {"torvin": 1}
    state.minute = 725
    comm.advance(world)
    assert state.communication.notices["crew-1"][-1].appointment.status == "confirmed"
    assert comm.project_blocker(partner, "dig_well") is None
    assert village_ai.apply(partner, "village_project", {"project": "dig_well"})[:2] == ("APPLIED", "PROJECT_PROGRESS")
    assert session.contributions == {"torvin": 1, "bren": 1}
    world.save()
    restored = World(value, world.path, "crew-session", "torvin", "session-torvin")
    assert restored.state.village.crew_sessions["dig_well"].contributions == session.contributions
    restored_partner = restored.bind("bren", "session-bren")
    for actor in (restored, restored_partner, restored, restored_partner):
        restored.state.village.minute += 5
        result = village_ai.apply(actor, "village_project", {"project": "dig_well"})
    assert result[:2] == ("APPLIED", "PROJECT_COMPLETED"), result
    assert restored.state.village.well_dug
    completed = restored.state.village.crew_sessions["dig_well"]
    assert completed.status == "completed"
    assert completed.contributions == {"torvin": 3, "bren": 3}
    for eid in (value.village.timber, value.village.rope):
        assert restored.state.entities[eid].hidden
        assert restored.state.entities[eid].location is None
    assert village_ai.apply(restored, "village_project", {"project": "dig_well"})[0] == "REJECTED"
    assert completed.contributions == {"torvin": 3, "bren": 3}


def test_next_actor_empty_bindings_fails_without_indexing_or_mutation(tmp_path):
    value = scenario()
    world = World(value, tmp_path / "world.json", "scheduler-empty", "nella", "session-nella")
    world.state.actors.clear()
    before = world.state.model_dump(mode="json")
    with pytest.raises(ValueError, match="no bound residents"):
        village_turns.next_actor(world)
    assert world.state.model_dump(mode="json") == before

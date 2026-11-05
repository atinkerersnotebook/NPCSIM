import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from npc_sim.contracts import observation
from npc_sim.models import Entity, Scenario
from npc_sim.storage import read_json
from npc_sim.story import intent
from npc_sim.voyage import tide
from npc_sim.world import World
from test_world import act


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def world(tmp_path):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "moonwake-treasures.json"))
    return World(scenario, tmp_path / "world.json", "world", "npc", "session")


def apply(world, action, **arguments):
    result = act(world, action, **arguments)
    assert result["outcome"] == "APPLIED", result
    assert world.inventory_units_used() <= world.actor.body.capacity
    observation(result["observation"])
    return result


def walk(world, *exits):
    for exit_id in exits:
        apply(world, "go", target=exit_id)


def phase(world, desired):
    if tide(world)[0] != desired:
        apply(world, "wait_tide")
    assert tide(world)[0] == desired


def first_visit(world):
    apply(world, "take", target="provision-parcel")
    walk(world, "home-stairs")
    apply(world, "take", target="resonant-journal")
    apply(world, "read", target="resonant-journal")
    walk(world, "loft-arch")
    apply(world, "light_lantern", target="lantern-wick", ignition="spark-strips")
    apply(world, "take", target="storm-lantern")
    walk(world, "loft-arch")
    apply(world, "open", target="roof-door")
    walk(world, "roof-door")
    apply(world, "teleport", word="MOONWAKE")
    apply(world, "drop", target="resonant-journal")
    walk(world, "meadow-path", "hut-door")
    apply(world, "recruit", target="navigator", item="provision-parcel")
    walk(world, "hut-door", "hill-path", "narrow-fissure", "workshop-arch")
    for eid in ("claw-mallet", "cork-belt", "empty-jar"):
        apply(world, "take", target=eid)
    walk(world, "workshop-arch", "narrow-fissure", "hill-path", "meadow-path")


def open_wide_route(world):
    apply(world, "take", target="resonant-journal")
    apply(world, "teleport", word="MOONWAKE")
    walk(world, "roof-door", "home-stairs")
    apply(world, "pull_nails", target="rug-fastening", item="claw-mallet")
    apply(world, "take", target="iron-key")
    apply(world, "take", target="recovered-nails")
    walk(world, "home-stairs", "roof-door")
    apply(world, "teleport", word="MOONWAKE")
    apply(world, "drop", target="resonant-journal")
    apply(world, "drop", target="recovered-nails")
    walk(world, "meadow-path", "hut-door")
    apply(world, "look", target="chart-chest")
    apply(world, "unlock", target="chart-lock", item="iron-key")
    apply(world, "open", target="chart-chest")
    apply(world, "take", target="bearing-chart")
    apply(world, "read", target="bearing-chart")
    walk(world, "hut-door", "meadow-path", "lagoon-steps", "pool-ledge")
    apply(world, "collect_fish", target="pool-fish", jar="empty-jar")
    apply(world, "take", target="fish-jar")
    walk(world, "pool-ledge", "lagoon-steps", "meadow-path", "cave-path")
    assert act(world, "go", target="crab-crossing")["code"] == "PASSAGE_BLOCKED"
    apply(world, "feed_crabs", target="crab-colony", bait="fish-jar")
    walk(world, "crab-crossing")
    apply(world, "unlock", target="sea-door", item="iron-key")
    apply(world, "open", target="sea-door")
    walk(world, "sea-door", "workshop-arch")


def build_and_board(world):
    first_visit(world)
    open_wide_route(world)
    apply(world, "take", target="spade")
    walk(world, "workshop-arch")
    apply(world, "take", target="sailcloth")
    assert act(world, "go", target="narrow-fissure")["code"] == "PASSAGE_BLOCKED"
    walk(world, "sea-door", "crab-crossing", "cave-path", "meadow-path")
    for eid in ("sailcloth", "storm-lantern", "claw-mallet", "iron-key"):
        apply(world, "drop", target=eid)
    walk(world, "meadow-path", "cave-path", "crab-crossing", "sea-door")
    apply(world, "take", target="precut-planks")
    walk(world, "sea-door", "crab-crossing", "cave-path", "meadow-path")
    apply(world, "drop", target="precut-planks")
    walk(world, "lagoon-steps")
    phase(world, "low")
    apply(world, "dig", target="anchor-bank", item="spade")
    apply(world, "take", target="iron-anchor")
    walk(world, "lagoon-steps")
    for eid in ("iron-anchor", "spade", "cork-belt"):
        apply(world, "drop", target=eid)
    apply(world, "take", target="claw-mallet")
    apply(world, "build_cutter", target="moonwake-cutter", frame="hull-frame",
          planks="precut-planks", sails="sailcloth", nails="recovered-nails",
          anchor="iron-anchor", item="claw-mallet")
    assert world.evaluate_goal("treasures_returned")["status"] == "active"
    apply(world, "take", target="launch-key")
    apply(world, "unlock", target="gangway", item="launch-key")
    apply(world, "open", target="gangway")
    apply(world, "drop", target="claw-mallet")
    walk(world, "meadow-path", "hut-door")
    apply(world, "take", target="kite-bird")
    apply(world, "take", target="seed-lure")
    walk(world, "hut-door", "meadow-path")
    apply(world, "take", target="spade")
    walk(world, "gangway")


def test_full_round_trip_wins_only_with_both_treasures_put_down_at_home(world):
    build_and_board(world)
    phase(world, "high")
    apply(world, "sail")
    assert world.actor.location_id == "cutter-deck"
    assert world.state.entities["gangway"].location == "far-shore"
    assert "crescent-beach" not in world.visible_ids()
    walk(world, "gangway", "orchard-path", "survey-path")
    assert act(world, "dig", target="bearing-ground", item="spade")["code"] == "BEARING_REQUIRED"
    apply(world, "pace", target="bearing-ground", count=23)
    assert act(world, "dig", target="bearing-ground", item="spade")["code"] == "BEARING_REQUIRED"
    apply(world, "pace", target="bearing-ground", count=24)
    apply(world, "dig", target="bearing-ground", item="spade")
    apply(world, "take", target="star-atlas")
    assert act(world, "go", target="observatory-entry")["code"] == "PASSAGE_BLOCKED"
    apply(world, "fly_bird", target="vine-snakes", bird="kite-bird", lure="seed-lure")
    walk(world, "observatory-entry")
    apply(world, "take", target="moon-coins")
    walk(world, "observatory-entry", "survey-path", "orchard-path", "gangway")
    phase(world, "high")
    apply(world, "sail")
    assert world.state.entities["gangway"].location == "crescent-beach"
    walk(world, "gangway")
    assert world.state.entities["navigator"].location == "cutter-deck"
    for eid in ("spade", "kite-bird", "launch-key", "bearing-chart"):
        apply(world, "drop", target=eid)
    apply(world, "take", target="resonant-journal")
    apply(world, "teleport", word="MOONWAKE")
    walk(world, "roof-door", "home-stairs")
    assert world.evaluate_goal("treasures_returned")["status"] == "active"
    apply(world, "drop", target="moon-coins")
    assert world.evaluate_goal("treasures_returned")["status"] == "active"
    apply(world, "drop", target="star-atlas")
    verified = world.evaluate_goal("treasures_returned")
    assert verified["status"] == "achieved"
    assert all(verified["evidence"]["checks"].values())
    restored = World(world.scenario, world.path, "world", "npc", "session")
    assert restored.evaluate_goal("treasures_returned")["status"] == "achieved"
    assert restored.state.voyage == world.state.voyage
    assert restored.state.entities["moonwake-cutter"].location == "crescent-beach"
    assert world.actor.action_clock < world.scenario.budgets.max_actions


def test_discovery_does_not_leak_hidden_rooms_treasures_or_travel_word(world):
    initial = observation(world.observe())
    text = json.dumps(initial)
    assert not any(eid in text for eid in (
        "star-atlas", "moon-coins", "MOONWAKE", "navigator-hut", "bearing-chart", "iron-key"))
    assert "voyage" not in initial
    assert "teleport" not in {a["id"] for a in initial["actions"]}
    assert world.evaluate_goal("treasures_returned")["status"] == "active"
    assert act(world, "teleport", word="MOONWAKE")["code"] == "ACTION_UNAVAILABLE"


def test_teleport_requires_reading_carried_book_and_correct_endpoint(world):
    walk(world, "home-stairs")
    apply(world, "take", target="resonant-journal")
    apply(world, "open", target="roof-door")
    walk(world, "roof-door")
    assert act(world, "teleport", word="MOONWAKE")["code"] == "ACTION_UNAVAILABLE"
    walk(world, "roof-door")
    apply(world, "read", target="resonant-journal")
    assert act(world, "teleport", word="MOONWAKE")["code"] == "ACTION_UNAVAILABLE"
    walk(world, "roof-door")
    assert act(world, "teleport", word="WRONG")["code"] == "INVALID_ARGUMENTS"
    apply(world, "drop", target="resonant-journal")
    assert act(world, "teleport", word="MOONWAKE")["code"] == "NOT_CARRIED"
    apply(world, "take", target="resonant-journal")
    request = ("travel-once", world.revision(), "teleport", {"word": "MOONWAKE"})
    result = world.act(*request)
    turns = world.state.voyage.turns
    assert world.act(*request) == result
    assert world.state.voyage.turns == turns
    assert world.actor.location_id == "crescent-beach"
    restored = World(world.scenario, world.path, "world", "npc", "session")
    assert restored.actor.visited_locations == world.actor.visited_locations
    assert restored.state.voyage.turns == turns


def test_tides_are_turn_based_and_rejected_or_information_actions_cost_nothing(world):
    first_visit(world)
    phase(world, "high")
    turns = world.state.voyage.turns
    for _ in range(2):
        world.observe()
        world.evaluate_goal("treasures_returned")
        act(world, "look")
    apply(world, "read", target="build-slate")
    assert act(world, "teleport", word="MOONWAKE")["code"] == "NOT_CARRIED"
    assert world.state.voyage.turns == turns
    apply(world, "drop", target="cork-belt")
    phase(world, "high")
    turns = world.state.voyage.turns
    assert act(world, "go", target="lagoon-steps")["code"] == "PASSAGE_BLOCKED"
    assert world.state.voyage.turns == turns
    apply(world, "take", target="cork-belt")
    walk(world, "lagoon-steps")
    phase(world, "high")
    request = ("one-tide", world.revision(), "wait_tide", {})
    assert world.act(*request)["code"] == "TIDE_CHANGED"
    assert tide(world)[0] == "low"
    turns = world.state.voyage.turns
    world.act(*request)
    assert world.state.voyage.turns == turns


def test_digging_requires_tool_low_tide_and_persists_exposed_cache(world):
    world.actor.location_id = "tidal-lagoon"
    assert "iron-anchor" not in world.visible_ids()
    assert act(world, "open", target="anchor-bank")["code"] == "JAMMED"
    assert act(world, "dig", target="anchor-bank", item="spade")["code"] == "NOT_CARRIED"
    world.state.entities["spade"].location = world.actor.actor_id
    world.actor.inventory.append("spade")
    phase(world, "high")
    assert act(world, "dig", target="anchor-bank", item="spade")["code"] == "TIDE_BLOCKED"
    phase(world, "low")
    apply(world, "dig", target="anchor-bank", item="spade")
    assert "iron-anchor" in world.visible_ids()
    restored = World(world.scenario, world.path, "world", "npc", "session")
    assert "iron-anchor" in restored.visible_ids()
    assert act(restored, "dig", target="anchor-bank", item="spade")["code"] == "ACTION_UNAVAILABLE"


def test_sailing_cannot_skip_building_crew_chart_book_or_tide(world):
    world.actor.location_id = "cutter-deck"
    assert act(world, "sail")["code"] == "SAILING_BLOCKED"
    ship = world.state.entities["moonwake-cutter"]
    ship.preparation["seaworthy"] = "yes"
    assert "navigator" in act(world, "sail")["explanation"]
    crew = world.state.entities["navigator"]
    crew.location = "cutter-deck"
    crew.preparation["hired"] = "yes"
    assert "chart" in act(world, "sail")["explanation"]
    world.actor.inventory.append("bearing-chart")
    world.state.entities["bearing-chart"].location = world.actor_id
    world.actor.inventory.append("resonant-journal")
    world.state.entities["resonant-journal"].location = world.actor_id
    assert "journal" in act(world, "sail")["explanation"]
    apply(world, "drop", target="resonant-journal")
    assert "journal" in act(world, "sail")["explanation"]
    world.state.entities["resonant-journal"].location = "crescent-beach"
    phase(world, "low")
    assert "high tide" in act(world, "sail")["explanation"]
    phase(world, "high")
    request = ("one-voyage", world.revision(), "sail", {})
    assert world.act(*request)["outcome"] == "APPLIED"
    turns = world.state.voyage.turns
    assert world.act(*request)["outcome"] == "APPLIED"
    assert world.state.voyage.turns == turns
    assert ship.location == "far-shore"
    assert world.state.entities["gangway"].location == "far-shore"
    restored = World(world.scenario, world.path, "world", "npc", "session")
    assert restored.state.entities["gangway"].location == "far-shore"
    assert restored.state.entities["navigator"].location == "cutter-deck"


def test_book_inside_carried_or_deck_container_still_blocks_sailing(world):
    world.actor.location_id = "cutter-deck"
    world.state.entities["moonwake-cutter"].preparation["seaworthy"] = "yes"
    world.state.entities["navigator"].location = "cutter-deck"
    world.state.entities["navigator"].preparation["hired"] = "yes"
    world.state.entities["bearing-chart"].location = world.actor_id
    world.actor.inventory.append("bearing-chart")
    world.state.entities["bag"] = Entity(
        name="Bag", kind="container", description="A bag.", location=world.actor_id, takable=True, open=False)
    world.actor.inventory.append("bag")
    world.state.entities["resonant-journal"].location = "bag"
    assert "journal" in act(world, "sail")["explanation"]
    apply(world, "drop", target="bag")
    assert "journal" in act(world, "sail")["explanation"]


def test_recruitment_consumes_one_offer_and_persists_with_idempotent_retries(world):
    world.actor.location_id = "navigator-hut"
    assert act(world, "recruit", target="navigator", item="provision-parcel")["code"] == "NOT_CARRIED"
    world.actor.inventory.append("provision-parcel")
    world.state.entities["provision-parcel"].location = world.actor_id
    request = ("one-contract", world.revision(), "recruit",
               {"target": "navigator", "item": "provision-parcel"})
    result = world.act(*request)
    assert result["code"] == "CREW_RECRUITED"
    turns = world.state.voyage.turns
    assert world.act(*request) == result
    assert world.state.voyage.turns == turns
    assert "provision-parcel" not in world.actor.inventory
    assert world.state.entities["provision-parcel"].location is None
    restored = World(world.scenario, world.path, "world", "npc", "session")
    assert restored.state.entities["navigator"].preparation["hired"] == "yes"
    assert restored.state.entities["navigator"].location == "cutter-deck"


def test_treasure_goal_requires_both_intact_and_directly_on_home_floor(world):
    for eid in world.scenario.voyage.treasures:
        world.state.entities[eid].location = "harbor-home"
    assert world.evaluate_goal("treasures_returned")["status"] == "achieved"
    world.state.entities["chart-chest"].location = "harbor-home"
    world.state.entities["star-atlas"].location = "chart-chest"
    assert world.evaluate_goal("treasures_returned")["status"] == "active"
    world.state.entities["star-atlas"].location = "harbor-home"
    world.state.entities["star-atlas"].health = 0
    assert world.evaluate_goal("treasures_returned")["status"] == "active"
    world.state.entities["star-atlas"].health = None
    world.actor.location_id = "chart-study"
    assert world.evaluate_goal("treasures_returned")["status"] == "active"


def test_new_intentions_use_first_person_prose():
    assert intent("sail", {}, {}) == "I try sailing to the other island."
    assert intent("teleport", {"word": "MOONWAKE"}, {}) == "I try speaking the journal's travel word."
    assert intent("pace", {"target": "ground", "count": 24}, {}) == "I measure 24 paces at the ground."
    assert intent("dig", {"target": "ground", "item": "spade"}, {}) == "I try digging at the ground with the spade."


@pytest.mark.parametrize("field,value", [
    ("docks", ["crescent-beach", "missing"]),
    ("teleport_locations", ["roof-terrace", "roof-terrace"]),
    ("boarding_exit", "home-stairs"),
    ("treasures", ["moon-coins", "moon-coins"]),
    ("turns_per_tide", 0),
    ("crew_id", "missing"),
    ("dig_sites", {"moon-coins": {"tool_id": "spade"}}),
    ("passage_rules", {"missing": {"carried": ["storm-lantern"]}}),
])
def test_invalid_voyage_references_rejected(field, value):
    raw = read_json(ROOT / "data" / "scenarios" / "moonwake-treasures.json")
    raw["voyage"][field] = value
    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)

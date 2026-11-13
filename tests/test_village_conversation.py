from npc_sim import village_conversation, village_turns
from npc_sim.world import World
from test_village_expanded import scenario
from test_world import act


def cast(tmp_path):
    value = scenario()
    value.village.daily_life = False
    value.village.goal_deadlines = {}
    value.village.start_minute = 480
    world = World(value, tmp_path / "conversation.json", "conversation", "elsie", "session-elsie")
    actors = {agent.id: world.bind(agent.id, f"session-{agent.id}") for agent in value.agents}
    for rid in ("elsie", "ada"):
        actors[rid].actor.location_id = "tavern"
    return actors


def test_direct_speech_opens_short_optional_thread(tmp_path):
    actors = cast(tmp_path)
    elsie, ada = actors["elsie"], actors["ada"]
    assert act(elsie, "say", message="Ada, can you check the milk?")["outcome"] == "APPLIED"
    assert ada.actor.messages[-1]["text"] == "Ada, can you check the milk?"
    thread = ada.observe()["conversation"]
    assert thread["with"] == "elsie" and thread["turns_remaining"] == 3
    assert "conversation" not in elsie.observe()
    reloaded = World(elsie.scenario, elsie.path, "conversation", "ada", "session-ada")
    assert reloaded.observe()["conversation"]["with"] == "elsie"
    assert act(ada, "say", message="I heard you; I need to check first.")["outcome"] == "APPLIED"
    assert elsie.observe()["conversation"]["turns_remaining"] == 2
    elsie.state.village.minute += 5
    assert act(elsie, "say", recipient="ada", message="Thank you.")["outcome"] == "APPLIED"
    assert act(ada, "say", recipient="elsie", message="I will let you know.")["outcome"] == "APPLIED"
    assert "conversation" not in elsie.observe()
    assert "conversation" not in ada.observe()
    assert not actors["elsie"].state.village.conversations
    elsie.state.village.minute += 5
    assert act(elsie, "say", message="Ada, one more thing.")["outcome"] == "APPLIED"
    assert not actors["elsie"].state.village.conversations


def test_departure_and_time_end_conversation(tmp_path):
    actors = cast(tmp_path)
    elsie, ada = actors["elsie"], actors["ada"]
    assert act(elsie, "say", message="Ada, can we talk?")["outcome"] == "APPLIED"
    ada.actor.location_id = "village-square"
    assert "conversation" not in ada.observe()
    ada.actor.location_id = "tavern"
    elsie.state.village.minute += village_conversation.MAX_MINUTES
    assert "conversation" not in ada.observe()


def test_scheduler_prioritizes_ready_listener_at_same_minute(tmp_path):
    actors = cast(tmp_path)
    state = actors["elsie"].state.village
    assert act(actors["elsie"], "say", message="Ada, hello.")["outcome"] == "APPLIED"
    state.last_scheduled_actor = None
    state.turn_cursor = 0
    assert village_turns.next_actor(actors["elsie"]) == "ada"

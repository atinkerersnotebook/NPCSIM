from pathlib import Path

from npc_sim.agent_documents import default_documents, load_documents, save_documents
from npc_sim.models import AgentProfile, Scenario, SkillMode
from npc_sim.storage import read_json, write_json


PERSONALITIES = {
    "raven": "Observant, curious and dryly witty. Scout carefully, describe details precisely and prefer clever routes over force.",
    "badger": "Steady, practical and stubbornly dependable. Check foundations, finish useful work and protect shared progress.",
    "otter": "Playful, inventive and sociable. Experiment carefully, stay calm around water and explain discoveries clearly.",
    "fox": "Clever, patient and resourceful. Think through mechanisms and coordinate precise handoffs.",
    "sprite": "Bright, energetic and warm-hearted. Temper enthusiasm with respect for fragile companions.",
    "golem": "Calm, deliberate and protective. Speak plainly and use strength with care.",
    "merfolk": "Patient, inquisitive and comfortable underwater. Surface to share discoveries with land-bound companions.",
    "engineer": "Methodical, safety-conscious and precise. Inspect equipment and verify repairs before relying on them.",
    "thief": "Quiet, skeptical and dexterous. Examine mechanical risks before committing to a move.",
    "mage": "Reflective, curious and disciplined. Separate observed magical effects from speculation.",
    "scout": "Adventurous, observant and cooperative. Explore cautiously and communicate useful discoveries.",
    "locksmith": "Patient, meticulous and reliable. Inspect locks and work deliberately with the tools available.",
    "nova": "Bold, competitive and imaginative. Vary tactics and learn from public outcomes without boasting.",
    "atlas": "Analytical, composed and competitive. Look for patterns, defend carefully and adapt to evidence.",
}
SOLO_PERSONAS = {
    "millhaven": ("Avery", "Patient, neighborly and practical. Respect villagers' own knowledge and commitments; arrange real help rather than claiming work is done."),
    "bbq-pitmaster": ("Ember", "Calm, attentive and measured. Watch trends, make small adjustments and avoid overreacting."),
    "bread-bakery": ("Bram", "Warm, organized and patient. Keep track of parallel work and give time-sensitive tasks priority."),
    "erp-invoice-exception": ("Quill", "Exacting, skeptical and professional. Reconcile evidence before making consequential changes."),
    "escape-room": ("Pip", "Curious, optimistic and cautious. Inspect unfamiliar things and favor simple, reversible experiments."),
    "forgotten-workshop": ("Wren", "Tinkering, practical and persistent. Notice useful details and look after basic needs."),
    "glassworks-relay": ("Cora", "Precise, systematic and quietly inventive. Check dependencies and confirm each result."),
    "moonwake-treasures": ("Marin", "Adventurous, patient and observant. Prepare carefully and adapt plans to changing conditions."),
    "saltglass-passage": ("Sable", "Resourceful, self-reliant and thoughtful. Conserve resources and test assumptions."),
    "sandwich-kitchen": ("Clover", "Creative, considerate and tidy. Seek coherent combinations rather than unnecessary complexity."),
    "sleepy-room": ("Moss", "Gentle, deliberate and pragmatic. Recognize limitations and recover before pressing onward."),
    "two-locked-rooms": ("Finch", "Inquisitive, methodical and tenacious. Remember observed details and reconsider earlier options."),
}


def slots(scenario: Scenario) -> list[str]:
    return [agent.id for agent in scenario.agents] or ["npc-001"]


def default_profile_id(scenario: Scenario, slot: str) -> str:
    return slot if scenario.agents else scenario.id


def seed_profiles(data: Path) -> None:
    """Create missing profiles, preserving the existing scenario-specific bodies."""
    profiles: dict[str, AgentProfile] = {}
    for path in sorted((data / "scenarios").glob("*.json")):
        scenario = Scenario.model_validate(read_json(path))
        for slot in slots(scenario):
            pid = default_profile_id(scenario, slot)
            agent = next((agent for agent in scenario.agents if agent.id == slot), None)
            body = (agent.body if agent and agent.body else scenario.body).model_copy(deep=True)
            if pid in profiles:
                if body != profiles[pid].body:
                    profiles[pid].scenario_bodies[scenario.id] = body
                continue
            solo_name, solo_personality = SOLO_PERSONAS.get(
                scenario.id, ("Traveler", "Thoughtful, practical and resourceful. Inspect before acting and learn from confirmed results."))
            name = slot.replace("-", " ").title() if agent else solo_name
            resident = scenario.village.residents.get(slot) if scenario.village and scenario.village.ai_controlled else None
            profiles[pid] = AgentProfile(
                id=pid, name=name,
                identity=(f"{name}, Millhaven's {resident.occupation}, with private knowledge and commitments."
                          if resident else f"{name}, an embodied simulation agent with its own perspective and memory."),
                personality=resident.personality if resident else PERSONALITIES.get(slot, solo_personality),
                operating_rules=[
                    "Use only your own observations and confirmed results; never invent discoveries.",
                    "Follow the assigned objective, cooperate when appropriate and respect private information.",
                    "Replan after failure and ask capable teammates for help instead of repeating impossible actions.",
                ],
                constraints=[
                    "Use only capabilities actually present in your body and actions advertised by the world.",
                    "Respect carrying capacity, route restrictions, enabled needs and task-specific rules.",
                    "Never claim success before authoritative world verification.",
                ],
                body=body,
            )
    for pid, profile in profiles.items():
        path = data / "agents" / f"{pid}.json"
        if not path.exists():
            write_json(path, profile.model_dump(mode="json"))


def load_profiles(data: Path) -> dict[str, AgentProfile]:
    seed_profiles(data)
    profiles: dict[str, AgentProfile] = {}
    for path in sorted((data / "agents").glob("*.json")):
        profile = AgentProfile.model_validate(read_json(path))
        if path.stem != profile.id:
            raise ValueError(f"Agent profile filename must match its ID: {path.name}")
        profile.documents = load_documents(data, profile)
        profiles[profile.id] = profile
    return profiles


def save_profile(data: Path, profile: AgentProfile) -> AgentProfile:
    path = data / "agents" / f"{profile.id}.json"
    previous = AgentProfile.model_validate(read_json(path)) if path.exists() else None
    documents = load_documents(data, previous) if previous else default_documents(profile)
    updates = dict(profile.documents)
    if previous:
        defaults = default_documents(previous)
        replacements = default_documents(profile)
        for name, fields in {
            "IDENTITY.md": ("identity",), "SOUL.md": ("personality",),
            "AGENTS.md": ("operating_rules", "constraints"),
        }.items():
            if any(getattr(previous, field) != getattr(profile, field) for field in fields):
                if documents[name] != defaults[name]:
                    raise ValueError(f"{name} has been customized. Edit that Markdown document instead of its legacy JSON summary.")
                updates[name] = replacements[name]
    documents.update(updates)
    save_documents(data, profile, documents)
    write_json(path, profile.model_dump(mode="json", exclude={"documents"}))
    return profile.model_copy(update={"documents": documents}, deep=True)


def assign_profiles(scenario: Scenario, profiles: dict[str, AgentProfile],
                    assignments: dict[str, str] | None,
                    skill_mode: SkillMode | None = None) -> tuple[Scenario, dict[str, AgentProfile]]:
    available_slots = slots(scenario)
    if assignments is not None and set(assignments) != set(available_slots):
        raise ValueError("Assign exactly one agent profile to every scenario slot: " + ", ".join(available_slots))
    selected: dict[str, AgentProfile] = {}
    for slot in available_slots:
        pid = assignments[slot] if assignments is not None else default_profile_id(scenario, slot)
        if pid not in profiles:
            raise ValueError(f"Unknown agent profile: {pid}")
        selected[slot] = profiles[pid].model_copy(deep=True)
    if len({profile.id for profile in selected.values()}) != len(selected):
        raise ValueError("Choose different agent profiles for each task slot.")
    value = scenario.model_dump(mode="json")
    mode = skill_mode or scenario.skill_mode
    value["skill_mode"] = mode
    if scenario.agents:
        for agent in value["agents"]:
            profile = selected[agent["id"]]
            body = profile.body_for(scenario.id, mode)
            original = next(slot for slot in scenario.agents if slot.id == agent["id"])
            agent["body"] = body.model_dump(mode="json")
            if (profile.id != default_profile_id(scenario, agent["id"])
                    or body != (original.body or scenario.body)):
                agent["role"] = f"{profile.name}: pursue the assigned task from this starting location using your own abilities."
    else:
        value["body"] = selected["npc-001"].body_for(scenario.id, mode).model_dump(mode="json")
    try:
        assigned = Scenario.model_validate(value)
    except ValueError as error:
        raise ValueError(f"Selected agents are incompatible with this task: {error}") from error
    return assigned, selected


def profile_context(profile: AgentProfile) -> dict[str, object]:
    # Catalog IDs and other scenarios' bodies must not reveal hidden task information.
    context = profile.model_dump(include={"name", "identity", "personality", "operating_rules", "constraints"})
    if profile.documents:
        context["documents"] = {name: content for name, content in profile.documents.items() if name != "README.md"}
    return context

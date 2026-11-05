import re
from pathlib import Path
from typing import TYPE_CHECKING

from npc_sim.storage import write_text

if TYPE_CHECKING:
    from npc_sim.models import AgentProfile


ROOT_DOCUMENTS = (
    "IDENTITY.md", "SOUL.md", "AGENTS.md", "USER.md", "MEMORY.md",
    "ALIGNMENT_SYNTHESIS.md", "PROACTIVE_PREFERENCES.md", "README.md",
)


def validate_documents(documents: dict[str, str]) -> dict[str, str]:
    seen: set[str] = set()
    for name in documents:
        if name not in ROOT_DOCUMENTS and not re.fullmatch(r"people/[A-Za-z0-9_-]+\.md", name):
            raise ValueError(f"Unsupported agent document path: {name}")
        if name.casefold() in seen:
            raise ValueError("Agent document names cannot differ only in letter case.")
        if name.casefold() == "people/index.md" and name != "people/INDEX.md":
            raise ValueError("The relationship index must be named people/INDEX.md.")
        seen.add(name.casefold())
    return documents


def default_documents(profile: "AgentProfile") -> dict[str, str]:
    rules = "\n".join(f"- {rule}" for rule in profile.operating_rules)
    constraints = "\n".join(f"- {rule}" for rule in profile.constraints)
    return {
        "IDENTITY.md": f"# Identity\n\n{profile.identity}\n",
        "SOUL.md": f"# Soul\n\n{profile.personality}\n",
        "AGENTS.md": f"# Operating guidance\n\n## Operating rules\n\n{rules}\n\n## Constraints\n\n{constraints}\n",
        "USER.md": (
            "# Operator and assignment\n\n"
            "The simulation operator chooses the scenario and cast. Pursue the assignment "
            "revealed to you by the world, not a task inferred from your catalog entry or role name.\n\n"
            "No real person's identity or preferences are assumed. Respect teammates' private "
            "observations and communicate through interactions the world actually supports.\n"
        ),
        "MEMORY.md": (
            f"# Authored background memory\n\nNo background memories have been authored for {profile.name}.\n\n"
            "These notes are character background, not evidence about the current world. "
            "Verify any relevant assumption through your own observations. Live episode memory "
            "and scenario failure feedback remain separate; this file is not updated automatically.\n"
        ),
        "ALIGNMENT_SYNTHESIS.md": (
            f"# Alignment synthesis\n\nBalance {profile.name}'s individual temperament with honest, "
            "evidence-based progress toward the revealed objective. Respect actual body abilities, "
            "capacity and prerequisites even when a tempting plan suggests otherwise.\n\n"
            "Treat expected outcomes as predictions. Accept authoritative failure or defeat "
            "rather than inventing progress, abilities or victory.\n"
        ),
        "PROACTIVE_PREFERENCES.md": (
            f"# Proactive preferences\n\nAs {profile.name}:\n\n"
            "- Inspect unfamiliar surroundings before committing to irreversible work.\n"
            "- Choose useful, reversible experiments when their prerequisites are satisfied.\n"
            "- Communicate observed obstacles and request help from capable teammates when possible.\n"
            "- Replan after confirmed failure; do not retry an action that requires a missing ability.\n"
            "- Keep narration faithful to observations, attempts and confirmed outcomes.\n"
        ),
        "people/INDEX.md": (
            "# Relationships\n\nNo relationships have been authored for this character.\n\n"
            "Optional people/*.md files may describe fictional background relationships. "
            "They do not grant access to another NPC's private observations, memory, location "
            "or abilities, and do not establish who is present in the current adventure.\n"
        ),
        "README.md": (
            f"# {profile.name}: NPC configuration\n\n"
            "This folder follows the supplied multi-document agent template. Every NPC has "
            "its own files; no source-template personal data is copied.\n\n"
            "- IDENTITY.md: character identity.\n"
            "- SOUL.md: personality and temperament.\n"
            "- AGENTS.md: operating rules and behavioral constraints.\n"
            "- USER.md: fictional operator/assignment expectations, not a real user profile.\n"
            "- MEMORY.md: manually authored background, not automatic episode memory.\n"
            "- ALIGNMENT_SYNTHESIS.md: reconcile temperament, objectives and world rules.\n"
            "- PROACTIVE_PREFERENCES.md: initiative and planning preferences.\n"
            "- people/INDEX.md and optional people/*.md: fictional background relationships.\n\n"
            f"Machine settings remain in ../{profile.id}.json. Only that JSON and the world "
            "define enforceable capabilities and carrying limits. Markdown cannot grant powers.\n\n"
            "Markdown is loaded when profiles are listed or a new adventure starts and is "
            "frozen into that adventure. Editing files does not modify existing adventures. "
            "All documents except this README enter the owning NPC's persona context; no other "
            "NPC's files are loaded. Keep background free of hidden scenario solutions.\n\n"
            "Missing standard files are recreated without overwriting existing ones. Edit "
            "Markdown directly or use the admin document editor. Legacy JSON persona edits "
            "update their untouched default Markdown counterpart; customized counterparts "
            "must be edited through Markdown instead. Clearing a document leaves an empty file. "
            "Removing a document from an API payload does not delete it from disk.\n"
        ),
    }


def load_documents(data: Path, profile: "AgentProfile") -> dict[str, str]:
    directory = data / "agents" / profile.id
    for name, content in {**default_documents(profile), **profile.documents}.items():
        path = directory.joinpath(*name.split("/"))
        if not path.exists():
            write_text(path, content)
    documents = {name: (directory / name).read_text(encoding="utf-8") for name in ROOT_DOCUMENTS}
    for path in sorted((directory / "people").glob("*.md")):
        documents[f"people/{path.name}"] = path.read_text(encoding="utf-8")
    return validate_documents(documents)


def save_documents(data: Path, profile: "AgentProfile", documents: dict[str, str]) -> None:
    validate_documents(documents)
    for name, content in documents.items():
        write_text((data / "agents" / profile.id).joinpath(*name.split("/")), content)

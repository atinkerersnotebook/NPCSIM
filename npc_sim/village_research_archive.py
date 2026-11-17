"""Durable, cross-world records of verified Millhaven discoveries."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from npc_sim.models import Entity, WorldState
from npc_sim.storage import read_json
from npc_sim.village_research_models import ResearchProject

if TYPE_CHECKING:
    from npc_sim.world import World


def _database(data: Path) -> Path:
    return data / "research-archive.sqlite3"


def _recipe(project: ResearchProject) -> tuple:
    proposal = project.proposal
    return (project.owner, project.location, proposal.kind, proposal.skill,
            tuple(sorted(proposal.inputs.items())), tuple(proposal.equipment), proposal.duration)


def _valid(key: str, project: ResearchProject, entity: Entity) -> bool:
    return (project.product == key and project.discovered_at is not None
            and entity.kind == "discovery" and entity.location == project.location
            and bool(entity.readable_text))


def _connect(data: Path) -> sqlite3.Connection:
    data.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(_database(data), timeout=30)
    connection.execute("CREATE TABLE IF NOT EXISTS discoveries ("
                       "world_id TEXT NOT NULL, product TEXT NOT NULL, scenario_id TEXT NOT NULL, "
                       "project TEXT NOT NULL, entity TEXT NOT NULL, "
                       "PRIMARY KEY (world_id, product))")
    return connection


def record(data: Path, state: WorldState) -> None:
    village = state.village
    if (not state.scenario_id.startswith("millhaven-") or village is None
            or village.research is None):
        return
    completed = [(key, project) for key, project in village.research.projects.items()
                 if project.discovered_at is not None
                 and project.origin_world_id in {None, state.world_id}]
    if not completed:
        return
    with closing(_connect(data)) as connection, connection:
        for key, project in completed:
            entity = state.entities.get(key)
            if entity is None or not _valid(key, project, entity):
                raise ValueError(f"Completed discovery {key} has no readable recipe at its site")
            archived = project.model_copy(deep=True, update={
                "chunks": [], "trial_materials_consumed": False,
                "origin_world_id": project.origin_world_id or state.world_id})
            connection.execute("INSERT OR IGNORE INTO discoveries VALUES (?, ?, ?, ?, ?)", (
                state.world_id, key, state.scenario_id,
                archived.model_dump_json(), entity.model_dump_json()))


def backfill(data: Path) -> None:
    for path in sorted((data / "worlds").glob("*/world-state.json")):
        document = read_json(path)
        if (not document.get("scenario_id", "").startswith("millhaven-")
                or not isinstance(document.get("village"), dict)):
            continue
        village = document.get("village") or {}
        research = village.get("research") or {}
        completed = {key: project for key, project in research.get("projects", {}).items()
                     if project.get("discovered_at") is not None and not project.get("origin_world_id")}
        if not completed:
            continue
        with closing(_connect(data)) as connection, connection:
            existing = {row[0] for row in connection.execute(
                "SELECT product FROM discoveries WHERE world_id=?", (document["world_id"],))}
            for key, raw in completed.items():
                if key in existing:
                    continue
                project = ResearchProject.model_validate(raw)
                entity_data = document["entities"].get(key)
                if entity_data is None:
                    raise ValueError(f"Completed discovery {key} has no readable recipe at its site")
                entity = Entity.model_validate(entity_data)
                if not _valid(key, project, entity):
                    raise ValueError(f"Completed discovery {key} has no readable recipe at its site")
                archived = project.model_copy(deep=True, update={
                    "chunks": [], "trial_materials_consumed": False,
                    "origin_world_id": document["world_id"]})
                connection.execute("INSERT INTO discoveries VALUES (?, ?, ?, ?, ?)", (
                    document["world_id"], key, document["scenario_id"],
                    archived.model_dump_json(), entity.model_dump_json()))


def import_into(data: Path, world: "World") -> None:
    state = world.state
    village = state.village
    if (not state.scenario_id.startswith("millhaven-") or village is None
            or village.research is None):
        return
    with closing(_connect(data)) as connection:
        rows = connection.execute(
            "SELECT world_id, product, project, entity FROM discoveries ORDER BY rowid DESC").fetchall()
    used = {(project.owner, project.proposal.title.strip().casefold(), _recipe(project)): project.product
            for project in village.research.projects.values()}
    aliases: dict[tuple[str, str], str] = {}
    for project in village.research.projects.values():
        if project.origin_world_id:
            aliases[(project.origin_world_id, project.product)] = project.product
    catalog = {(source_id, key): ResearchProject.model_validate_json(project)
               for source_id, key, project, _ in rows}
    pending = rows
    while pending:
        deferred = []
        imported = 0
        for source_id, key, project_json, entity_json in pending:
            if _import_one(world, source_id, key, project_json, entity_json, used, aliases, catalog):
                imported += 1
            else:
                deferred.append((source_id, key, project_json, entity_json))
        if not imported:
            break
        pending = deferred


def _import_one(world: "World", source_id: str, key: str, project_json: str, entity_json: str,
                used: dict[tuple, str], aliases: dict[tuple[str, str], str],
                catalog: dict[tuple[str, str], ResearchProject]) -> bool:
    state = world.state
    village = state.village
    config = world.scenario.village
    if village is None or village.research is None or config is None:
        raise ValueError("Import requires a research-enabled village")
    if source_id == state.world_id:
        return True
    project = ResearchProject.model_validate_json(project_json)
    entity = Entity.model_validate_json(entity_json)
    if not _valid(key, project, entity):
        raise ValueError(f"Invalid archived discovery: {key}")
    source_id = project.origin_world_id or source_id
    if project.owner not in config.residents or project.location not in state.entities:
        return True
    title = (project.owner, project.proposal.title.strip().casefold(), _recipe(project))
    if title in used:
        aliases[(source_id, key)] = used[title]
        return True
    inputs = {}
    for resource, amount in project.proposal.inputs.items():
        if not resource.startswith("discovery-"):
            inputs[resource] = amount
            continue
        dependency = catalog.get((source_id, resource))
        if dependency is None:
            if resource not in village.research.projects:
                return False
            inputs[resource] = amount
            continue
        replacement = aliases.get((source_id, resource))
        if replacement is None:
            return False
        inputs[replacement] = amount
    identity = key
    if identity in village.research.projects or identity in state.entities:
        identity = "discovery-" + hashlib.sha256(f"{source_id}:{key}".encode()).hexdigest()[:16]
    if identity in village.research.projects or identity in state.entities:
        raise ValueError(f"Archived discovery identity conflict: {identity}")
    proposal = project.proposal.model_copy(deep=True, update={"inputs": inputs})
    if any(existing.owner == project.owner
           and existing.proposal.title.strip().casefold() == proposal.title.strip().casefold()
           for existing in village.research.projects.values()):
        proposal.title = f"{proposal.title[:75]} (variant {source_id[:8]})"
        entity.name = proposal.title
    village.research.projects[identity] = project.model_copy(deep=True, update={
        "product": identity, "proposal": proposal, "discovered_at": 0,
        "origin_world_id": source_id})
    if identity != key:
        entity.readable_text = (entity.readable_text or "").replace(key, identity)
    state.entities[identity] = entity
    used[title] = identity
    aliases[(source_id, key)] = identity
    return True

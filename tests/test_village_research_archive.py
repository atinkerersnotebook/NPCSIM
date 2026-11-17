from pathlib import Path

from npc_sim import village_research, village_research_archive
from npc_sim.models import Entity, Scenario
from npc_sim.storage import read_json
from npc_sim.village_research_models import ResearchProject, ResearchProposal
from npc_sim.world import World


ROOT = Path(__file__).resolve().parent.parent


def _world(data, world_id, scenario_id="millhaven-generations"):
    scenario = Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / f"{scenario_id}.json"))
    return World(scenario, data / "worlds" / world_id / "world-state.json",
                 world_id, "rowan", f"session-{world_id}")


def _complete(world, key, title, *, duration=60):
    proposal = ResearchProposal(title=title, description="Prepare real logs for durable braces.",
                                skill="forestry", kind="material", inputs={"logs": 1}, duration=duration)
    world.state.village.research.projects[key] = ResearchProject(
        owner="rowan", location="woods", proposal=proposal, product=key,
        required_minutes=240, required_successes=3, worked_minutes=240,
        attempts=4, successes=3, discovered_at=700)
    world.state.entities[key] = Entity(name=title, kind="discovery", location="woods",
                                       description=proposal.description,
                                       readable_text=f"Verified material: 1 logs -> one {key}.")
    world.save()


def test_archived_discoveries_from_independent_worlds_reach_new_world(tmp_path):
    first = _world(tmp_path, "first")
    second = _world(tmp_path, "second", "millhaven-complete")
    _complete(first, "discovery-shared", "Seasoned timber braces")
    _complete(second, "discovery-shared", "Seasoned timber braces", duration=30)
    first.state.village.research.known["rowan"] = ["discovery-shared"]
    first.state.village.stocks["woods"]["discovery-shared"] = 2
    target = _world(tmp_path, "third")
    village_research_archive.backfill(tmp_path)
    village_research_archive.import_into(tmp_path, target)
    projects = target.state.village.research.projects
    assert len(projects) == 2
    assert {project.proposal.duration for project in projects.values()} == {30, 60}
    assert all(project.discovered_at == 0 for project in projects.values())
    assert all(key in target.state.entities for key in projects)
    assert target.state.village.research.known == {}
    assert target.state.village.stocks == target.scenario.village.stocks
    assert all(key in village_research.prices(target) for key in projects)
    target.save()
    restored = _world(tmp_path, "third")
    assert len(restored.state.village.research.projects) == 2
    fourth = _world(tmp_path, "fourth")
    village_research_archive.backfill(tmp_path)
    village_research_archive.import_into(tmp_path, fourth)
    assert len(fourth.state.village.research.projects) == 2


def test_unfinished_projects_and_unrelated_worlds_are_not_archived(tmp_path):
    source = _world(tmp_path, "source")
    _complete(source, "discovery-done", "Seasoned timber braces")
    source.state.village.research.projects["discovery-pending"] = source.state.village.research.projects[
        "discovery-done"].model_copy(deep=True, update={"product": "discovery-pending", "discovered_at": None})
    source.save()
    target = _world(tmp_path, "target")
    village_research_archive.backfill(tmp_path)
    village_research_archive.import_into(tmp_path, target)
    assert list(target.state.village.research.projects) == ["discovery-done"]

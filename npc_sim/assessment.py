import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from npc_sim.models import StrictModel
from npc_sim.storage import read_json, write_json
from npc_sim.story import Paragraph


class Finding(StrictModel):
    reason: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def nonempty_text(self) -> "Finding":
        if not self.reason.strip() or any(not eid.strip() for eid in self.evidence_ids):
            raise ValueError("Assessment reasons and evidence IDs cannot be blank")
        return self


class StoryReview(StrictModel):
    verdict: Literal["pass", "fail"]
    findings: list[Finding] = Field(min_length=1)


class CompletionAnalysis(StrictModel):
    status: Literal["pending", "running", "completed", "error"] = "pending"
    verdict: Literal["pass", "fail"] | None = None
    findings: list[Finding] = Field(default_factory=list)
    story: list[Paragraph] = Field(default_factory=list)
    final_verification: dict[str, Any] | None = None
    story_review: StoryReview | None = None
    model: str | None = None
    model_calls: int = 0
    elapsed_seconds: float = 0
    completed_at: str | None = None
    error: str | None = None


class FailureFeedback(StrictModel):
    run_id: str
    objective: str
    recorded_at: str
    findings: list[Finding] = Field(min_length=1)


class ScenarioMemory(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    scenario_id: str
    failures: list[FailureFeedback] = Field(default_factory=list)


def memory_path(data: Path, scenario_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", scenario_id):
        raise ValueError("Invalid scenario memory ID")
    return data / "scenario-memory" / f"{scenario_id}.json"


def load_memory(data: Path, scenario_id: str) -> ScenarioMemory:
    path = memory_path(data, scenario_id)
    if not path.exists():
        return ScenarioMemory(scenario_id=scenario_id)
    memory = ScenarioMemory.model_validate(read_json(path))
    if memory.scenario_id != scenario_id:
        raise ValueError("Scenario memory belongs to another scenario")
    return memory


def record_feedback(data: Path, scenario_id: str, run_id: str, objective: str,
                    analysis: CompletionAnalysis) -> None:
    if analysis.status != "completed" or analysis.verdict is None or analysis.completed_at is None:
        raise ValueError("Only completed assessments can update scenario memory")
    memory = load_memory(data, scenario_id)
    memory.failures = [failure for failure in memory.failures if failure.run_id != run_id]
    if analysis.verdict == "fail":
        memory.failures.append(FailureFeedback(
            run_id=run_id, objective=objective, recorded_at=analysis.completed_at, findings=analysis.findings))
    write_json(memory_path(data, scenario_id), memory.model_dump(mode="json"))


def grade(review: StoryReview, verification: dict[str, Any], verification_event_id: str,
          story: list[Paragraph]) -> tuple[Literal["pass", "fail"], list[Finding]]:
    allowed = {paragraph.id for paragraph in story} | {verification_event_id}
    if any(eid not in allowed for finding in review.findings for eid in finding.evidence_ids):
        raise ValueError("Story reviewer cited evidence outside this actor's supplied story and final verification")
    failures = []
    if verification["status"] != "achieved":
        checks = verification.get("evidence", {}).get("checks", {})
        if checks.get("objective_discovered") is False:
            failures.append("The assigned task was not discovered by reading its note.")
        if checks.get("prepared_item") is False:
            failures.append("The required finished item was not produced intact.")
        if checks.get("served_on_target") is False:
            failures.append("The required finished item is not on the designated intact serving surface.")
        if not failures:
            failures.append(verification.get("reason") or "Final world verification did not confirm the objective.")
    findings = [Finding(reason=reason, evidence_ids=[verification_event_id]) for reason in failures]
    if review.verdict == "fail":
        findings.extend(review.findings)
    if findings:
        unique = {finding.reason.casefold(): finding for finding in findings}
        return "fail", list(unique.values())
    return "pass", review.findings

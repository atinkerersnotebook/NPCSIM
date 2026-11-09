from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from npc_sim.assessment import Finding
from npc_sim.models import StrictModel, VillageConfigurationPatch
from npc_sim.storage import read_json, write_json


class DungeonMasterConfig(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    enabled: bool = True
    name: str = Field(default="Dungeon Master", min_length=1)
    identity: str = "An independent supervisor and adjudicator, never a competing NPC."
    personality: str = "Fair, observant and decisive. Be patient with genuine exploration, but candid about futile attempts."
    capabilities: list[Literal["observe_team", "adjudicate_actions", "review_progress", "declare_defeat", "configure_village"]] = Field(
        default_factory=lambda: ["observe_team", "adjudicate_actions", "review_progress", "declare_defeat"])
    operating_rules: list[str] = Field(default_factory=lambda: [
        "Judge actions using authoritative world results, never imagined skills or outcomes.",
        "Review the whole team's observed progress after each round and cite supplied evidence.",
        "Declare defeat when evidence supports futility, not merely because the puzzle is difficult.",
    ], min_length=1)
    constraints: list[str] = Field(default_factory=lambda: [
        "Never grant abilities, bypass prerequisites or declare success. World changes require explicit authority; "
        "configure_village permits only validated village policies and supported templates.",
        "Never reveal other agents' private observations to an NPC.",
        "Missing tools, exploration, travel and legitimate timed waits are not proof of defeat.",
    ], min_length=1)

    @model_validator(mode="after")
    def valid_authority(self) -> "DungeonMasterConfig":
        if "review_progress" in self.capabilities and "observe_team" not in self.capabilities:
            raise ValueError("Progress reviews require observe_team capability")
        return self


class DungeonMasterReview(StrictModel):
    verdict: Literal["continue", "defeat"]
    findings: list[Finding] = Field(min_length=1)
    village_configuration: VillageConfigurationPatch | None = None


def load_config(data: Path) -> DungeonMasterConfig:
    path = data / "dungeon-master.json"
    if not path.exists():
        write_json(path, DungeonMasterConfig().model_dump(mode="json"))
    return DungeonMasterConfig.model_validate(read_json(path))


def validate_review(review: DungeonMasterReview, evidence: list[dict[str, Any]],
                    config: DungeonMasterConfig) -> None:
    allowed = {event["id"] for event in evidence}
    if any(eid not in allowed for finding in review.findings for eid in finding.evidence_ids):
        raise ValueError("Dungeon Master cited evidence outside the supplied world events")
    if review.verdict == "defeat" and "declare_defeat" not in config.capabilities:
        raise ValueError("Dungeon Master is not configured with declare_defeat authority")
    if review.village_configuration is not None and "configure_village" not in config.capabilities:
        raise ValueError("Dungeon Master is not configured with village configuration authority")

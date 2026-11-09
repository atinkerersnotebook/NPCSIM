from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Amount = Annotated[int, Field(gt=0, le=20)]


class VillageResearchConfig(ResearchModel):
    enabled: bool = True
    required_minutes: int = Field(default=240, ge=60, le=2880, multiple_of=15)
    required_successes: int = Field(default=3, ge=1, le=12)


class ResearchProposal(ResearchModel):
    title: str = Field(min_length=3, max_length=100)
    description: str = Field(min_length=10, max_length=400)
    skill: str
    kind: Literal["food", "crop", "material", "tool"]
    inputs: dict[str, Amount] = Field(min_length=1, max_length=4)
    equipment: list[str] = Field(default_factory=list, max_length=3)
    duration: int = Field(ge=15, le=240, multiple_of=5)


class ResearchChunk(ResearchModel):
    actor: str
    location: str
    equipment: list[str]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    minutes: int = Field(ge=0)
    operation: Literal["research", "produce", "use"]
    status: Literal["active", "completed", "interrupted"] = "active"
    succeeded: bool | None = None
    target: str | None = None


class ResearchProject(ResearchModel):
    owner: str
    location: str
    proposal: ResearchProposal
    product: str
    required_minutes: int
    required_successes: int
    worked_minutes: int = 0
    attempts: int = 0
    successes: int = 0
    trial_materials_consumed: bool = False
    discovered_at: int | None = None
    chunks: list[ResearchChunk] = Field(default_factory=list)


class VillageResearchState(ResearchModel):
    config: VillageResearchConfig
    projects: dict[str, ResearchProject] = Field(default_factory=dict)
    known: dict[str, list[str]] = Field(default_factory=dict)


class VillageResearchView(ResearchModel):
    id: str
    title: str
    description: str
    owner: str
    location: str
    product: str
    kind: Literal["food", "crop", "material", "tool"]
    inputs: dict[str, int]
    equipment: list[str]
    duration: int
    worked_minutes: int
    required_minutes: int
    attempts: int
    successes: int
    required_successes: int
    discovered_at: int | None
    status: Literal["researching", "in_progress", "blocked", "discovered"]
    blocker: str | None = None

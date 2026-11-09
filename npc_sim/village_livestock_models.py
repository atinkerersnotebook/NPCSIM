from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VillageLivestockConfig(StrictModel):
    enabled: bool = True
    keeper: str
    hens: int = Field(default=12, ge=1, le=48, strict=True)


class LivestockCare(StrictModel):
    day: int = -1
    fed: bool = False
    watered: bool = False
    cleaned: bool = False
    progress: int = 0
    eggs: int = 0
    milk_sessions: list[str] = Field(default_factory=list)


class LivestockChunk(StrictModel):
    actor: str
    operation: Literal["water", "cow", "hens", "milk", "eggs", "cook", "scraps", "compost"]
    location: str
    equipment: list[str]
    start: int
    end: int
    quantity: int = 0
    session: str = ""
    source_product: str = ""
    output_expires_at: int | None = None
    ingredients: list[str] = Field(default_factory=list)
    status: Literal["active", "completed", "interrupted"] = "active"


class VillageLivestockState(StrictModel):
    config: VillageLivestockConfig
    started_day: int = -1
    cow: LivestockCare = Field(default_factory=LivestockCare)
    hens: LivestockCare = Field(default_factory=LivestockCare)
    water: int = 0
    chunks: list[LivestockChunk] = Field(default_factory=list)

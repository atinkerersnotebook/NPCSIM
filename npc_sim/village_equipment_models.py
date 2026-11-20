"""Opt-in persistent capital equipment and upgrade projects for villages."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EquipmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class VillageEquipmentTier(EquipmentModel):
    level: int = Field(ge=0, strict=True)
    name: str = Field(min_length=1)
    duration: int = Field(default=0, ge=0, multiple_of=5, strict=True)
    cost: int = Field(default=0, ge=0, strict=True)
    supplies: dict[str, int] = Field(default_factory=dict)
    duration_factor: float = Field(default=1, gt=0, le=1)
    batch_size: int = Field(default=1, ge=1, le=10, strict=True)
    capacity: int = Field(default=0, ge=0, strict=True)
    fuel_factor: float = Field(default=1, gt=0, le=1)
    effects: dict[str, int | float | str | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_supplies(self) -> "VillageEquipmentTier":
        if any(not resource.strip() or amount <= 0 or isinstance(amount, bool)
               for resource, amount in self.supplies.items()):
            raise ValueError("Upgrade supplies require positive integer quantities")
        return self


class VillageEquipmentAsset(EquipmentModel):
    entity: str = Field(min_length=1)
    location: str = Field(min_length=1)
    category: Literal["production", "transport", "water", "service", "agriculture",
                      "environment", "puzzle", "interaction"] = "interaction"
    owner: str | None = None
    communal: bool = False
    users: list[str] = Field(default_factory=list)
    funders: list[str] = Field(default_factory=list)
    initial_tier: int = Field(default=0, ge=0, strict=True)
    minimum_reserve: int = Field(default=0, ge=0, strict=True)
    tiers: list[VillageEquipmentTier] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_asset(self) -> "VillageEquipmentAsset":
        levels = [tier.level for tier in self.tiers]
        if levels != list(range(len(levels))):
            raise ValueError("Equipment tiers must use contiguous levels starting at zero")
        if self.initial_tier >= len(self.tiers):
            raise ValueError("Initial equipment tier does not exist")
        if self.communal and self.owner is not None:
            raise ValueError("Communal equipment cannot also have a single owner")
        if not self.tiers[0].name.strip():
            raise ValueError("Initial equipment tier needs a name")
        if self.tiers[0].cost or self.tiers[0].duration or self.tiers[0].supplies:
            raise ValueError("Initial equipment tier cannot require an upgrade")
        return self


class VillageEquipmentConfig(EquipmentModel):
    enabled: bool = True
    assets: dict[str, VillageEquipmentAsset] = Field(default_factory=dict)


class VillageEquipmentAssetState(EquipmentModel):
    tier: int = Field(ge=0, strict=True)


class VillageEquipmentProject(EquipmentModel):
    id: str
    asset: str
    worker: str
    payer: str
    from_tier: int = Field(ge=0, strict=True)
    to_tier: int = Field(ge=1, strict=True)
    started_at: int = Field(ge=0)
    finishes_at: int = Field(ge=0)
    cost: int = Field(ge=0, strict=True)
    supplies: dict[str, int] = Field(default_factory=dict)
    status: Literal["active", "completed", "cancelled"] = "active"


class VillageEquipmentExpenditure(EquipmentModel):
    minute: int = Field(ge=0)
    asset: str
    payer: str
    cost: int = Field(ge=0, strict=True)
    to_tier: int = Field(ge=1, strict=True)


class VillageEquipmentState(EquipmentModel):
    config: VillageEquipmentConfig
    assets: dict[str, VillageEquipmentAssetState] = Field(default_factory=dict)
    projects: dict[str, VillageEquipmentProject] = Field(default_factory=dict)
    expenditures: list[VillageEquipmentExpenditure] = Field(default_factory=list)

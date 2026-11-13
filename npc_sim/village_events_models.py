"""Persisted, opt-in minor incidents and temporary visitors."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


Amount = Annotated[int, Field(gt=0, le=100)]
Quantity = Annotated[int, Field(ge=0, le=10000)]
EventKind = Literal[
    "traveller", "merchant", "rain", "equipment", "discovery", "escaped_animal",
    "poor_fishing", "harvest", "misplaced_tool", "birthday", "band_absence", "neighbour_help",
]
Response = Literal["work", "deliver", "discover", "decline"]


class EventCondition(EventsModel):
    resource: str
    location: str
    below: Quantity


class MerchantProfile(EventsModel):
    funds: Quantity = 40
    capacity: int = Field(default=20, ge=1, le=100)
    stock: dict[str, Quantity] = Field(default_factory=dict)
    buy_prices: dict[str, Amount] = Field(default_factory=dict)
    sell_prices: dict[str, Amount] = Field(default_factory=dict)
    demand: dict[str, Quantity] = Field(default_factory=dict)
    journey_minutes: int = Field(default=120, ge=5, le=10080)
    resale_minutes: int = Field(default=60, ge=0, le=10080)
    source: str = Field(default="Out-of-town supplier", min_length=1, max_length=120)

    @model_validator(mode="after")
    def bounded(self) -> "MerchantProfile":
        if sum(self.stock.values()) > self.capacity:
            raise ValueError("Merchant stock exceeds cargo capacity")
        if set(self.demand) - set(self.buy_prices):
            raise ValueError("Merchant demand requires explicit buy prices")
        if set(self.stock) - set(self.sell_prices):
            raise ValueError("Merchant stock requires explicit sell prices")
        return self


class VisitorPlan(EventsModel):
    name: str = Field(min_length=1, max_length=80)
    entry: str
    destination: str
    departure: str
    visits: list[str] = Field(default_factory=list, max_length=6)
    stay_minutes: int = Field(default=60, ge=15, le=2880)
    funds: Quantity = 20
    capacity: int = Field(default=6, ge=1, le=100)
    source: str = Field(default="Traveller's purse", min_length=1, max_length=120)
    meal_location: str | None = None
    lodging_location: str | None = None
    lodging_price: int = Field(default=0, ge=0, le=100)
    lodging_capacity: int = Field(default=2, ge=1, le=10)
    merchant: MerchantProfile | None = None


class EventTemplate(EventsModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    kind: EventKind
    title: str = Field(min_length=1, max_length=160)
    location: str
    residents: list[str] = Field(default_factory=list, max_length=3)
    enabled: bool = True
    random: bool = True
    scheduled_at: int | None = Field(default=None, ge=0)
    condition: EventCondition | None = None
    window_start: int = Field(default=540, ge=0, lt=1440)
    window_end: int = Field(default=1080, gt=0, le=1440)
    duration: int = Field(default=30, ge=15, le=60, multiple_of=5)
    cooldown: int = Field(default=1440, ge=60)
    intensity: int = Field(default=1, ge=1, le=3)
    responses: list[Response] = Field(default_factory=lambda: ["work", "decline"])
    inputs: dict[str, Amount] = Field(default_factory=dict)
    outputs: dict[str, Amount] = Field(default_factory=dict)
    demand: dict[str, Amount] = Field(default_factory=dict)
    equipment: str | None = None
    discovery: str | None = None
    visitor: VisitorPlan | None = None
    source: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def effect(self) -> "EventTemplate":
        if self.window_end <= self.window_start:
            raise ValueError("Event time window must be ordered")
        if len(set(self.residents)) != len(self.residents) or not self.responses:
            raise ValueError("Responses must be present and residents unique")
        if self.kind in {"traveller", "merchant"} and self.visitor is None:
            raise ValueError("Visitor event requires a physical visitor plan")
        if self.kind == "merchant" and (self.visitor is None or self.visitor.merchant is None):
            raise ValueError("Merchant event requires finite trade profile")
        if self.kind not in {"traveller", "merchant"} and self.visitor is not None:
            raise ValueError("Only visitor events may introduce visitors")
        if self.kind in {"discovery", "misplaced_tool", "escaped_animal"} and not self.discovery:
            raise ValueError("Discovery event requires an existing entity")
        if self.kind == "equipment" and not self.equipment:
            raise ValueError("Equipment delay requires existing equipment")
        if self.outputs and not self.source:
            raise ValueError("Opportunity outputs require a recorded source")
        if self.kind in {"rain", "poor_fishing", "band_absence", "equipment"} and (
            self.inputs or self.outputs or self.demand
        ):
            raise ValueError("Delay events cannot create or consume resources")
        if self.demand and "deliver" not in self.responses:
            raise ValueError("Demand requires a deliver response")
        if self.outputs and "work" not in self.responses:
            raise ValueError("Outputs require concrete work")
        if self.discovery and "discover" not in self.responses:
            raise ValueError("Discovery requires a discover response")
        if sum(self.outputs.values()) > 6 or sum(self.inputs.values()) > 6:
            raise ValueError("Minor opportunity batches are bounded to six units")
        if self.kind == "harvest" and not self.outputs:
            raise ValueError("Harvest opportunities need a bounded real yield")
        if self.kind in {"birthday", "neighbour_help"} and not (self.inputs or self.outputs):
            raise ValueError("Personal requests require concrete input or output tasks")
        if self.discovery and "work" in self.responses:
            raise ValueError("Discovery tasks resolve through real observation, not work claims")
        return self


class VillageEventsConfig(EventsModel):
    enabled: bool = True
    seed: int = 0
    daily_min: int = Field(default=0, ge=0, le=12)
    daily_max: int = Field(default=2, ge=0, le=12)
    max_active: int = Field(default=2, ge=1, le=3)
    templates: list[EventTemplate] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def limits(self) -> "VillageEventsConfig":
        if self.daily_min > self.daily_max:
            raise ValueError("daily_min exceeds daily_max")
        if len({item.id for item in self.templates}) != len(self.templates):
            raise ValueError("Event template IDs must be unique")
        return self


class IncidentState(EventsModel):
    id: str
    template: EventTemplate
    activated_at: int
    expires_at: int
    status: Literal["active", "resolved", "expired", "declined"] = "active"
    progress: int = 0
    next_work_at: int = 0
    fulfilled: dict[str, int] = Field(default_factory=dict)
    accepted_by: str | None = None
    witnesses: list[str] = Field(default_factory=list)
    evidence: list[int] = Field(default_factory=list)
    resolved_at: int | None = None
    outcome: str | None = None


class VisitorInterval(EventsModel):
    label: str
    location: str
    start: int
    end: int


class VisitorLot(EventsModel):
    quantity: int = Field(gt=0)
    produced_at: int
    expires_at: int | None = None


class TradeRecord(EventsModel):
    id: str
    visitor: str
    resident: str
    direction: Literal["export", "import", "order", "meal", "lodging"]
    resource: str
    quantity: int
    payment: int
    minute: int
    location: str
    source: str


class VisitorState(EventsModel):
    id: str
    incident: str
    name: str
    label: Literal["Visitor"] = "Visitor"
    plan: VisitorPlan
    location: str
    status: Literal["arriving", "present", "departing", "departed"] = "arriving"
    arrived_at: int | None = None
    departed_at: int | None = None
    leave_at: int
    next_step_at: int
    path: list[str] = Field(default_factory=list)
    visit_index: int = 0
    next_visit_at: int = 0
    activity: str = "Travelling"
    coins: int = Field(ge=0)
    stock: dict[str, int] = Field(default_factory=dict)
    stock_lots: dict[str, list[VisitorLot]] = Field(default_factory=dict)
    purchased: dict[str, int] = Field(default_factory=dict)
    spoiled: dict[str, int] = Field(default_factory=dict)
    meals: list[int] = Field(default_factory=list)
    missed_meals: list[int] = Field(default_factory=list)
    lodging_days: list[int] = Field(default_factory=list)
    unsatisfied: dict[str, int] = Field(default_factory=dict)
    timeline: list[VisitorInterval] = Field(default_factory=list)


class ScheduledMerchantVisit(EventsModel):
    template_id: str
    scheduled_at: int = Field(ge=0)
    retry_until: int = Field(ge=0)
    status: Literal["pending", "activated", "expired"] = "pending"
    reason: str | None = None
    last_checked: int | None = None
    activated_at: int | None = None


class VillageEventsState(EventsModel):
    config: VillageEventsConfig
    revision: int = 0
    instances: dict[str, IncidentState] = Field(default_factory=dict)
    visitors: dict[str, VisitorState] = Field(default_factory=dict)
    trades: dict[str, TradeRecord] = Field(default_factory=dict)
    daily_counts: dict[str, int] = Field(default_factory=dict)
    daily_targets: dict[str, int] = Field(default_factory=dict)
    last_activation: dict[str, int] = Field(default_factory=dict)
    scheduled_fired: list[str] = Field(default_factory=list)
    scheduled_merchants: dict[str, ScheduledMerchantVisit] = Field(default_factory=dict)
    configuration_history: list[dict] = Field(default_factory=list)

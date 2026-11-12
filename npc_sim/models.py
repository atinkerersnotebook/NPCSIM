from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, field_validator, model_validator, model_serializer

from npc_sim.agent_documents import validate_documents
from npc_sim.village_economy_models import VillageEconomyConfig, VillageEconomyState
from npc_sim.village_communication_models import VillageCommunicationConfig, VillageCommunicationState, CommunicationView
from npc_sim.village_social_models import VillageSocialConfig, VillageSocialState
from npc_sim.village_events_models import VillageEventsConfig, VillageEventsState
from npc_sim.village_research_models import VillageResearchConfig, VillageResearchState, VillageResearchView
from npc_sim.village_livestock_models import VillageLivestockConfig, VillageLivestockState


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Need(StrictModel):
    enabled: bool = False
    level: float = Field(default=0, ge=0, le=100)
    rate: float = Field(default=0, ge=0, le=100)
    threshold: float = Field(default=80, gt=0, le=100)


Grip = Literal["hand", "paw", "claw", "mouth", "beak"]
BodySize = Literal["small", "medium", "large"]


class Body(StrictModel):
    description: str
    capabilities: list[str]
    capacity: int = Field(ge=0)
    size: BodySize = "medium"
    grips: list[Grip] = Field(default_factory=lambda: ["hand"])
    max_held_items: int | None = Field(default=None, ge=0, strict=True)


SkillMode = Literal["scenario", "npc_only"]


class AgentProfile(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)
    identity: str = Field(min_length=1)
    personality: str = Field(min_length=1)
    operating_rules: list[str] = Field(min_length=1)
    constraints: list[str] = Field(min_length=1)
    body: Body
    scenario_bodies: dict[str, Body] = Field(default_factory=dict)
    documents: dict[str, str] = Field(default_factory=dict)

    @field_validator("documents")
    @classmethod
    def valid_documents(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_documents(value)

    def body_for(self, scenario_id: str, skill_mode: SkillMode = "scenario") -> Body:
        body = self.body if skill_mode == "npc_only" else self.scenario_bodies.get(scenario_id, self.body)
        return body.model_copy(deep=True)


class Nutrition(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    serving_grams: float = Field(ge=0)
    calories_kcal: float = Field(ge=0)
    protein_g: float = Field(ge=0)
    carbohydrates_g: float = Field(ge=0)
    fat_g: float = Field(ge=0)
    fiber_g: float = Field(ge=0)
    sugar_g: float = Field(ge=0)

    def scaled(self, fraction: float) -> "Nutrition":
        return Nutrition(**{key: value * fraction for key, value in self.model_dump().items()})

    def plus(self, other: "Nutrition") -> "Nutrition":
        values = other.model_dump()
        return Nutrition(**{key: value + values[key] for key, value in self.model_dump().items()})


class Entity(StrictModel):
    name: str
    kind: str
    description: str
    location: str | None = None
    open: bool | None = None
    locked: bool | None = None
    key_id: str | None = None
    alternative_key_ids: list[str] = Field(default_factory=list)
    hidden: bool = False
    discovery_target: str | None = None
    destination: str | None = None
    takable: bool = Field(default=False, validation_alias=AliasChoices("takable", "portable"))
    inventory_units: int = Field(default=1, gt=0, strict=True)
    carry_grips: list[Grip] = Field(default_factory=lambda: ["hand"])
    use_grips: list[Grip] = Field(default_factory=lambda: ["hand"])
    food: bool = False
    nutrition: Nutrition | None = None
    ingredients: list[str] = Field(default_factory=list)
    power: Literal["off", "on"] | None = None
    plugged_into: str | None = None
    readable_text: str | None = None
    preparation: dict[str, str] = Field(default_factory=dict)
    lock_id: str | None = None
    damage: int = Field(default=0, ge=0)
    health: int | None = Field(default=100, ge=0)
    max_health: int | None = Field(default=None, gt=0)
    break_into: dict[str, "Entity"] = Field(default_factory=dict)
    jammed: bool = False
    broken_name: str | None = None
    broken_description: str | None = None
    traversal_capabilities: list[str] = Field(default_factory=list)
    max_body_size: BodySize | None = None
    required_challenges: list[str] = Field(default_factory=list)
    village_road: bool = True

    @model_validator(mode="after")
    def valid_health(self) -> "Entity":
        if self.nutrition is not None and not self.food:
            raise ValueError("Only food items can have nutritional values")
        if self.ingredients and (not self.food or any(not value.strip() for value in self.ingredients)):
            raise ValueError("Ingredient labels require food and nonblank names")
        if self.plugged_into is not None and self.power is None:
            raise ValueError("Only electrical appliances can be plugged in")
        if self.kind == "location":
            self.health = None
        if self.health is not None:
            if self.max_health is None:
                self.max_health = max(100, self.health)
            if self.health > self.max_health:
                raise ValueError("Health cannot exceed maximum health")
        elif self.max_health is not None:
            raise ValueError("Indestructible entities cannot have maximum health")
        return self

    @property
    def broken(self) -> bool:
        return self.health == 0


class Recovery(StrictModel):
    action_id: str
    description: str
    recovery: float = Field(gt=0, le=100)
    arguments_schema: dict[str, Any] = Field(default_factory=lambda: {
        "type": "object", "properties": {}, "required": [], "additionalProperties": False})


class Recipe(StrictModel):
    action_id: str
    arguments: dict[str, str]
    carried: list[str] = Field(default_factory=list)
    powered: list[str] = Field(default_factory=list)
    placements: dict[str, str] = Field(default_factory=dict)
    ingredient_requirements: list[list[str]] = Field(default_factory=list)
    requires: dict[str, dict[str, str]] = Field(default_factory=dict)
    consumes: list[str]
    updates: dict[str, dict[str, str]] = Field(default_factory=dict)
    outputs: dict[str, Entity] = Field(default_factory=dict)
    nutrition_target: str | None = None
    explanation: str


ClockMode = Literal["decision_paused", "continuous"]


class SimulationClock(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    seconds: float = Field(default=0, ge=0)
    real_seconds: float = Field(default=0, ge=0)
    rate: float = Field(default=120, ge=1, le=600)
    running: bool = False
    mode: ClockMode = "continuous"


class ClockRequest(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    rate: float = Field(ge=1, le=600)
    mode: ClockMode | None = None


class BakeryConfig(StrictModel):
    bowls: list[str] = Field(min_length=1)
    ingredients: list[str] = Field(min_length=1)
    mixers: list[str] = Field(min_length=1)
    ovens: list[str] = Field(min_length=1)
    bench: str
    rack: str
    display: str
    rate: float = Field(default=120, ge=1, le=600)
    mode: ClockMode = "decision_paused"


class BreadBatch(StrictModel):
    ingredients: list[str] = Field(default_factory=list)
    stage: Literal["measuring", "mixing", "mixed", "rising", "risen", "proofing",
                   "proofed", "baking", "baked", "cooling", "cooled", "served", "ruined"] = "measuring"
    equipment: str | None = None
    ready_at: float | None = None
    deadline: float | None = None
    failure: str | None = None


class Budgets(StrictModel):
    max_actions: int = Field(default=30, gt=0)
    max_model_calls: int = Field(default=40, gt=0)
    max_seconds: float = Field(default=120, gt=0)
    repetition_window: int = Field(default=3, ge=2)


class ScenarioAgent(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    role: str = Field(min_length=1)
    start_location: str | None = None
    body: Body | None = None


class SkillChallenge(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    description: str = Field(min_length=1)
    target: str
    capability: str = Field(min_length=1)
    requires: list[str] = Field(default_factory=list)
    carried: list[str] = Field(default_factory=list)
    installs: list[str] = Field(default_factory=list)
    opens: list[str] = Field(default_factory=list)


class DigSite(StrictModel):
    tool_id: str
    low_tide: bool = False
    paces: int | None = Field(default=None, gt=0)


class PassageRule(StrictModel):
    carried: list[str] = Field(default_factory=list)
    requires: dict[str, dict[str, str]] = Field(default_factory=dict)
    max_item_units: int | None = Field(default=None, gt=0)


class VoyageConfig(StrictModel):
    teleport_item: str
    teleport_word: str = Field(min_length=1)
    teleport_locations: list[str] = Field(min_length=2, max_length=2)
    docks: list[str] = Field(min_length=2, max_length=2)
    ship_id: str
    cabin_id: str
    boarding_exit: str
    crew_id: str
    crew_offer: str
    chart_id: str
    flotation_id: str
    tidal_locations: list[str] = Field(min_length=1)
    tide_visible_locations: list[str] = Field(min_length=1)
    turns_per_tide: int = Field(default=6, ge=2)
    dig_sites: dict[str, DigSite]
    treasures: list[str] = Field(min_length=2)
    passage_rules: dict[str, PassageRule] = Field(default_factory=dict)


class VoyageState(StrictModel):
    turns: int = Field(default=0, ge=0)
    paced_sites: dict[str, int] = Field(default_factory=dict)


RPSMove = Literal["rock", "paper", "scissors"]


class RPSConfig(StrictModel):
    rounds: int = Field(default=5, ge=1, le=100, strict=True)


class RPSRound(StrictModel):
    moves: dict[str, RPSMove]
    winner: str | None = None


class RPSState(StrictModel):
    pending: dict[str, RPSMove] = Field(default_factory=dict)
    history: list[RPSRound] = Field(default_factory=list)


class TicTacToeMove(StrictModel):
    actor_id: str
    cell: int = Field(ge=1, le=9, strict=True)


class TicTacToeState(StrictModel):
    history: list[TicTacToeMove] = Field(default_factory=list, max_length=9)


class ERPPurchaseOrder(StrictModel):
    id: str = Field(pattern=r"^[A-Z0-9-]+$")
    supplier: str = Field(min_length=1)
    item: str = Field(min_length=1)
    quantity: int = Field(gt=0, strict=True)
    unit_price_cents: int = Field(gt=0, strict=True)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ERPInvoice(ERPPurchaseOrder):
    purchase_order: str = Field(min_length=1)


class ERPReceivingAdvice(StrictModel):
    id: str = Field(pattern=r"^[A-Z0-9-]+$")
    purchase_order: str = Field(min_length=1)
    supplier: str = Field(min_length=1)
    item: str = Field(min_length=1)
    quantity: int = Field(gt=0, strict=True)
    approved: bool


class ERPConfig(StrictModel):
    purchase_order: ERPPurchaseOrder
    invoice: ERPInvoice
    initial_received: int = Field(ge=0, strict=True)
    receiving_advice: list[ERPReceivingAdvice] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_documents(self) -> "ERPConfig":
        ids = [self.purchase_order.id, self.invoice.id, *(advice.id for advice in self.receiving_advice)]
        if len(ids) != len(set(ids)):
            raise ValueError("ERP document IDs must be unique")
        if self.invoice.purchase_order != self.purchase_order.id:
            raise ValueError("The ERP invoice must reference the configured purchase order")
        if self.initial_received > self.purchase_order.quantity:
            raise ValueError("Initial receipts cannot exceed the purchase order quantity")
        return self


class ERPTerminalEntry(StrictModel):
    actor_id: str
    command: str
    code: str
    output: str


class ERPState(StrictModel):
    posted_receipts: dict[str, int] = Field(default_factory=dict)
    invoice_matched: bool = False
    invoice_released: bool = False
    history: list[ERPTerminalEntry] = Field(default_factory=list)


class BBQConfig(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    target_f: float = Field(default=225, gt=75, lt=375)
    tolerance_f: float = Field(default=10, gt=0, le=25)
    duration_seconds: int = Field(default=1800, ge=60, le=3600, strict=True)
    minimum_in_band_fraction: float = Field(default=0.8, gt=0, le=1)


class BBQReading(StrictModel):
    seconds: int = Field(ge=0)
    temperature_f: float
    heat_percent: float = Field(ge=0, le=100)


class BBQState(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    seed: int = Field(ge=0)
    elapsed_seconds: int = Field(default=0, ge=0)
    temperature_f: float = 225
    heat_percent: float = Field(default=50, ge=0, le=100)
    in_band_seconds: int = Field(default=0, ge=0)
    next_flare_seconds: int = Field(gt=0)
    flare_remaining_seconds: int = Field(default=0, ge=0)
    flare_count: int = Field(default=0, ge=0)
    max_deviation_f: float = Field(default=0, ge=0)
    history: list[BBQReading] = Field(default_factory=list)


VillageQuantity = Annotated[int, Field(ge=0, strict=True)]
VillageAmount = Annotated[int, Field(gt=0, strict=True)]


class VillageBelief(StrictModel):
    subject: str
    location: str | None
    source: str
    learned_at: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)


class VillageRoutine(StrictModel):
    minute: int = Field(ge=0, lt=1440)
    location: str
    activity: str


class VillageJob(StrictModel):
    location: str
    interval: int = Field(ge=5)
    inputs: dict[str, VillageAmount] = Field(default_factory=dict)
    outputs: dict[str, VillageAmount] = Field(default_factory=dict)
    equipment: str | None = None
    duration: int = Field(default=5, ge=5, multiple_of=5)
    interruptible: bool = True
    batch_size: int = Field(default=1, ge=1, le=2)


class VillageResident(StrictModel):
    occupation: str
    personality: str
    skills: list[str]
    home: str
    work: str
    schedule: list[VillageRoutine]
    beliefs: list[VillageBelief] = Field(default_factory=list)
    relationships: dict[str, int] = Field(default_factory=dict)
    busy_until: int = Field(default=0, ge=0)
    job: VillageJob | None = None
    wake_minute: int = Field(default=480, ge=0, lt=1080, multiple_of=5)
    bed_minute: int = Field(default=1320, gt=1080, lt=1440, multiple_of=5)


class VillageDelivery(StrictModel):
    source: str
    destination: str
    resource: str
    seller: str
    buyer: str
    price: int = Field(gt=0)


class VillageMaintenanceTask(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    assignee: str
    location: str
    duration: int = Field(default=30, ge=5, le=240, multiple_of=5)
    release_minute: int = Field(default=480, ge=0, lt=1440, multiple_of=5)
    due_minute: int = Field(default=1020, ge=0, lt=1440, multiple_of=5)
    equipment: list[str] = Field(default_factory=list)
    supplies: dict[str, VillageAmount] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_schedule(self) -> "VillageMaintenanceTask":
        if self.due_minute <= self.release_minute:
            raise ValueError("Maintenance must be due after its daily release time")
        return self


class VillageMaintenanceConfig(StrictModel):
    enabled: bool = True
    tasks: dict[str, VillageMaintenanceTask] = Field(default_factory=dict)


class VillageMaintenanceChunk(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    minutes: int = Field(ge=0)
    status: Literal["active", "completed", "interrupted"] = "active"


class VillageMaintenanceJob(StrictModel):
    template: str
    definition: VillageMaintenanceTask
    day: int = Field(ge=0)
    release_at: int = Field(ge=0)
    due_at: int = Field(ge=0)
    worked_minutes: int = Field(default=0, ge=0)
    materials_consumed: bool = False
    completed_at: int | None = None
    chunks: list[VillageMaintenanceChunk] = Field(default_factory=list)


class VillageMaintenanceState(StrictModel):
    config: VillageMaintenanceConfig
    jobs: dict[str, VillageMaintenanceJob] = Field(default_factory=dict)
    last_generated_day: int = -1
    last_maintained: dict[str, int] = Field(default_factory=dict)


class VillageMaintenanceView(StrictModel):
    id: str
    title: str
    assignee: str
    location: str
    day: int
    release_at: int
    due_at: int
    worked_minutes: int
    required_minutes: int
    supplies: dict[str, int]
    status: Literal["upcoming", "pending", "in_progress", "overdue", "blocked", "completed"]
    completed_at: int | None = None
    blocker: str | None = None


class VillageDayPolicy(StrictModel):
    breakfast: bool = False
    breakfast_delay: int = Field(default=30, ge=5, le=180, multiple_of=5)
    meal_window: int = Field(default=90, ge=5, le=240, multiple_of=5)
    inn_closing_minute: int = Field(default=1320, ge=1140, lt=1440, multiple_of=5)
    event_driven_waits: bool = False
    anti_idle: bool = False
    idle_review_minutes: int = Field(default=60, ge=15, le=240, multiple_of=5)
    require_tea_use: bool = True
    require_manure_use: bool = True
    demand_led_completion: bool = False


class VillageMiningConfig(StrictModel):
    enabled: bool = True
    miner: str
    mining_minutes: int = Field(default=30, ge=5, le=240, multiple_of=5, strict=True)
    smelting_minutes: int = Field(default=30, ge=5, le=240, multiple_of=5, strict=True)


class VillageMiningChunk(StrictModel):
    actor: str
    operation: Literal["mine", "smelt"]
    location: str
    equipment: str
    start: int
    end: int
    status: Literal["active", "completed", "interrupted"] = "active"


class VillageMiningState(StrictModel):
    config: VillageMiningConfig
    chunks: list[VillageMiningChunk] = Field(default_factory=list)


class VillageFirewoodConfig(StrictModel):
    enabled: bool = True
    supplier: str
    household_buyer: str
    households: dict[str, list[str]] = Field(min_length=1)
    businesses: list[str] = Field(min_length=1)
    season: Literal["spring", "summer", "autumn", "winter"] = "autumn"
    heating_enabled: bool = False
    split_minutes: int = Field(default=15, ge=5, le=60, multiple_of=5, strict=True)
    firewood_per_log: int = Field(default=4, ge=1, le=8, strict=True)
    reserve: int = Field(default=2, ge=1, le=12, strict=True)
    site_capacities: dict[str, int] = Field(default_factory=dict)
    reorder_point: int = Field(default=2, ge=0, le=24, strict=True)
    business_buffer_days: int = Field(default=3, ge=1, le=7, strict=True)


class VillageFuelStop(StrictModel):
    destination: str
    order_id: str
    quantity: int = Field(gt=0)
    delivered_before: int = Field(default=0, ge=0)
    reason_for_stop: str
    path: list[str]


class VillageFuelDeliveryPlan(StrictModel):
    route_id: str
    carrier: str
    load_plan: dict[str, int]
    stops: list[VillageFuelStop]
    expected_departure: int = Field(ge=0)
    required_arrival: int = Field(ge=0)
    remaining_inventory: int = Field(ge=0)
    vehicle_id: str | None = None
    status: Literal["planned", "loaded", "in_progress", "completed", "cancelled"] = "planned"
    reason_for_stop: str


class VillageFirewoodState(StrictModel):
    config: VillageFirewoodConfig
    delivery_plans: list[VillageFuelDeliveryPlan] = Field(default_factory=list)
    warm_until: dict[str, int] = Field(default_factory=dict)
    statuses: dict[str, str] = Field(default_factory=dict)
    consumed: dict[str, int] = Field(default_factory=dict)


class VillageFirewoodSiteView(StrictModel):
    location: str
    buyer: str
    stock: int = Field(ge=0)
    reserve: int = Field(ge=0)
    consumed: int = Field(ge=0)
    capacity: int = Field(ge=1)
    status: Literal["normal", "low", "out"]


class VillageHearthView(StrictModel):
    id: str
    location: str
    members: list[str]
    occupied: bool
    status: Literal["warm", "cold", "unoccupied", "not_needed"]
    warm_until: int = Field(ge=0)


class VillageFirewoodView(StrictModel):
    enabled: bool
    season: Literal["spring", "summer", "autumn", "winter"]
    supplier: str
    firewood_per_log: int = Field(gt=0)
    split_minutes: int = Field(gt=0)
    sites: list[VillageFirewoodSiteView]
    households: list[VillageHearthView]
    policy: str


class VillageConfigurationPatch(StrictModel):
    firewood: VillageFirewoodConfig | None = None
    communication: VillageCommunicationConfig | None = None
    economy: VillageEconomyConfig | None = None
    social: VillageSocialConfig | None = None
    events: VillageEventsConfig | None = None
    maintenance: VillageMaintenanceConfig | None = None
    research: VillageResearchConfig | None = None
    mining: VillageMiningConfig | None = None
    livestock: VillageLivestockConfig | None = None
    day_policy: VillageDayPolicy | None = None


class VillageConfigurationRequest(StrictModel):
    run_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    expected_revision: str = Field(min_length=1)
    configuration: VillageConfigurationPatch


class VillageConfig(VillageDayPolicy):
    firewood: VillageFirewoodConfig | None = None
    communication: VillageCommunicationConfig | None = None
    ai_controlled: bool = False
    daily_life: bool = False
    time_based_turns: bool = False
    expanded_rules: bool = False
    economy: VillageEconomyConfig | None = None
    social: VillageSocialConfig | None = None
    events: VillageEventsConfig | None = None
    maintenance: VillageMaintenanceConfig | None = None
    research: VillageResearchConfig | None = None
    mining: VillageMiningConfig | None = None
    livestock: VillageLivestockConfig | None = None
    delivery_fee: int = Field(default=0, ge=0)
    meal_price: int = Field(default=0, ge=0)
    spoil_after: dict[str, int] = Field(default_factory=dict)
    goal_deadlines: dict[str, int] = Field(default_factory=dict)
    residents: dict[str, VillageResident]
    stocks: dict[str, dict[str, VillageQuantity]]
    coins: dict[str, VillageQuantity]
    prices: dict[str, VillageAmount]
    owners: dict[str, str]
    deliveries: list[VillageDelivery]
    courier: str
    shovel: str
    smith: str
    helper: str
    carpenter: str
    miller: str
    mill: str
    mill_equipment: str
    well_site: str
    timber: str
    rope: str
    repair_part: str
    hammer: str
    start_minute: int = Field(default=480, ge=0)
    breakdown_minute: int = Field(default=840, ge=0)


class VillageMeal(StrictModel):
    kind: Literal["daily", "breakfast"]
    due: int = Field(ge=0)
    status: Literal["pending", "eaten", "missed"] = "pending"
    source: str | None = None


class VillageCommittedWork(StrictModel):
    location: str
    finishes_at: int = Field(ge=0)
    outputs: dict[str, VillageAmount]
    ingredients: list[str] = Field(default_factory=list)
    equipment: str | None = None
    kind: Literal["production", "wagon_repair"] = "production"

    @model_serializer(mode="wrap")
    def serialize_work(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = handler(self)
        if not self.ingredients:
            result.pop("ingredients", None)
        return result


class VillageLifeState(StrictModel):
    hungry: bool = False
    missed_meals: int = Field(default=0, ge=0)
    meals: dict[str, VillageMeal] = Field(default_factory=dict)
    sleeping: bool = False
    sleep_until: int | None = Field(default=None, ge=0)
    last_woke: int
    work: VillageCommittedWork | None = None
    fatigue_days: list[int] = Field(default_factory=list)
    tea_day: int = -1
    urgent_project: str | None = None
    urgent_until: int | None = None


class VillageBlockedAction(StrictModel):
    action_id: str
    arguments: dict[str, Any]
    blocked_at: int
    reason_code: str
    engine_code: str
    reason: str
    context_revision: str
    recheck_on: list[str]
    attempt_count_since_change: int = 1


class VillageIdleWait(StrictModel):
    start: int
    planned_end: int
    reason_code: str = "NO_ELIGIBLE_WORK"
    interruptible: bool = True
    wake_on: list[str]
    next_safety_review: int
    context_revision: str


class VillageSupplyNeed(StrictModel):
    owner_id: str
    good: str
    quantity_needed: int = Field(gt=0)
    purpose: str
    destination_id: str
    supplier_id: str
    status: Literal["identified", "request-posted", "awaiting-response", "accepted", "conditional",
                    "delivered", "rejected", "expired", "cancelled", "blocked"] = "identified"
    notice_id: str | None = None
    last_material_change: int = 0
    next_check_after: int = 0
    blocker: str | None = None


class VillageLocationTransition(StrictModel):
    origin: str
    destination: str
    minute: int


class VillageSupplierSearch(StrictModel):
    visits: int = 0
    evidence: str = ""
    observed_stock: int = 0
    last_visit: int = 0


class VillageRouteGoal(StrictModel):
    goal_id: str
    target_location: str
    reason: str
    evidence_timestamp: int
    last_known_target_location: str
    attempted_edges: list[str] = Field(default_factory=list)
    last_progress_at: int
    blocked_reason: str
    retry_after: int
    wake_on: list[str] = Field(default_factory=list)
    evidence: str = ""


class VillageFallbackState(StrictModel):
    route_goal: VillageRouteGoal | None = None
    blocked: dict[str, VillageBlockedAction] = Field(default_factory=dict)
    supply_needs: dict[str, VillageSupplyNeed] = Field(default_factory=dict)
    recent_locations: list[VillageLocationTransition] = Field(default_factory=list)
    supplier_searches: dict[str, VillageSupplierSearch] = Field(default_factory=dict)
    inspections: dict[str, tuple[int, str]] = Field(default_factory=dict)
    wait: VillageIdleWait | None = None
    active_inspection_until: int | None = None
    active_inspection_location: str | None = None
    decision: dict[str, Any] | None = None


class VillagePersonState(StrictModel):
    beliefs: dict[str, VillageBelief] = Field(default_factory=dict)
    memories: list[int] = Field(default_factory=list)
    trust: dict[str, int] = Field(default_factory=dict)
    activity: str = "Starting the day"
    next_work: int = 0
    busy_until: int = 0
    cargo: dict[str, int] = Field(default_factory=dict)
    delivery: int | None = None
    delivery_cursor: int = 0
    last_news_day: int = -1
    life: VillageLifeState | None = None
    available_at: int = Field(default=0, ge=0)
    known_routes: list[str] = Field(default_factory=list)
    shipments: list["VillageShipment"] = Field(default_factory=list)
    timber_route: bool = False
    route_next_at: int = 0
    stock_report: dict[str, dict[str, int]] = Field(default_factory=dict)
    stock_report_at: int | None = None
    fertilized_harvests: int = Field(default=0, ge=0)
    current_action: str | None = None
    fallback: VillageFallbackState = Field(default_factory=VillageFallbackState)


class VillageStockLot(StrictModel):
    ingredients: list[str] = Field(default_factory=list)
    source_resource: str | None = None

    @model_serializer(mode="wrap")
    def serialize_lot(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = handler(self)
        if not self.ingredients:
            result.pop("ingredients", None)
        if self.source_resource is None:
            result.pop("source_resource", None)
        return result
    quantity: int = Field(gt=0)
    produced_at: int = Field(ge=0)
    expires_at: int | None = None


class VillageShipment(StrictModel):
    resource: str
    quantity: int = Field(gt=0, le=6)
    seller: str
    buyer: str
    destination: str
    price: int = Field(gt=0)
    lots: list[VillageStockLot] = Field(default_factory=list)
    order_id: str | None = None
    owned_transfer: bool = False


class VillageWagon(StrictModel):
    repaired: bool = False
    driver: str | None = None
    automatic: bool = False
    loads: list[VillageShipment] = Field(default_factory=list)
    route_index: int = Field(default=0, ge=0, le=4)
    phase: Literal["travel", "unload", "load"] = "travel"
    next_event_at: int = Field(default=0, ge=0)
    blocker: str | None = None
    hitched: bool = False


class VillageTask(StrictModel):
    kind: Literal["fetch_shovel", "dig_well", "repair_mill"]
    requester: str
    assignee: str
    rendezvous: str
    status: Literal["accepted", "in_progress", "blocked", "completed", "cancelled"] = "accepted"
    step: str = "plan"
    blocker: str | None = None
    progress: int = 0
    evidence: list[int] = Field(default_factory=list)


class VillageEvent(StrictModel):
    minute: int
    actor: str
    action: str
    target: str
    result: str
    witnesses: list[str]
    details: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def serialize_event(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = handler(self)
        if self.details is None:
            result.pop("details", None)
        return result


class VillageDeadline(StrictModel):
    due: int = Field(ge=0)
    completed_at: int | None = None
    overdue: bool = False


class VillageStockSnapshot(StrictModel):
    minute: int
    stocks: dict[str, dict[str, int]]
    coins: dict[str, int]


class VillageActivityView(StrictModel):
    actor: str
    label: str
    location: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    status: str


class VillageVisitorView(StrictModel):
    id: str
    name: str
    location: str
    arrival: int = Field(ge=0)
    departure: int | None = Field(default=None, ge=0)
    status: str
    activities: list[VillageActivityView] = Field(default_factory=list)


class VillageOrderView(StrictModel):
    id: str
    buyer: str
    seller: str
    product: str
    quantity: int = Field(ge=0)
    delivered: int = Field(ge=0)
    status: str
    deadline: int = Field(ge=0)


class VillageProductView(StrictModel):
    location: str
    product: str
    quantity: int = Field(ge=0)
    usable: int = Field(ge=0)
    expiring: int = Field(ge=0)
    reserve: int = Field(ge=0)


class VillageAccountView(StrictModel):
    owner: str
    sales: int = Field(ge=0)
    purchases: int = Field(ge=0)
    fees: int
    subsidies: int
    net_cash: int
    inventory_value: int = Field(ge=0)


class VillageProjectView(StrictModel):
    id: str
    owner: str
    title: str
    progress: int = Field(ge=0)
    required: int = Field(ge=0)
    status: str


class VillageQuestView(StrictModel):
    id: str
    actor: str
    title: str
    progress: int = Field(ge=0)
    required: int = Field(ge=0)
    status: str


class VillageIncidentView(StrictModel):
    id: str
    title: str
    location: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    status: str


class VillageTimeUse(StrictModel):
    since: int = Field(ge=0)
    sleep: int = Field(default=0, ge=0)
    work: int = Field(default=0, ge=0)
    travel: int = Field(default=0, ge=0)
    wait: int = Field(default=0, ge=0)
    leisure: int = Field(default=0, ge=0)
    idle: int = Field(default=0, ge=0)
    successful_actions: int = Field(default=0, ge=0)
    useful_actions: int = Field(default=0, ge=0)
    useful_minutes: int = Field(default=0, ge=0)
    last_useful_minute: int = -1
    wasted_travel: int = Field(default=0, ge=0)


class VillageDashboard(StrictModel):
    firewood: VillageFirewoodView | None = None
    communication: CommunicationView | None = None
    orders: list[VillageOrderView] = Field(default_factory=list)
    products: list[VillageProductView] = Field(default_factory=list)
    accounts: list[VillageAccountView] = Field(default_factory=list)
    activities: list[VillageActivityView] = Field(default_factory=list)
    projects: list[VillageProjectView] = Field(default_factory=list)
    quests: list[VillageQuestView] = Field(default_factory=list)
    visitors: list[VillageVisitorView] = Field(default_factory=list)
    incidents: list[VillageIncidentView] = Field(default_factory=list)
    maintenance: list[VillageMaintenanceView] = Field(default_factory=list)
    research: list[VillageResearchView] = Field(default_factory=list)
    time_use: dict[str, VillageTimeUse] = Field(default_factory=dict)
    daily_time_use: dict[int, dict[str, VillageTimeUse]] = Field(default_factory=dict)
    day_policy: VillageDayPolicy | None = None


class VillageState(StrictModel):
    firewood: VillageFirewoodState | None = None
    communication: VillageCommunicationState | None = None
    minute: int
    residents: dict[str, VillagePersonState]
    stocks: dict[str, dict[str, int]]
    coins: dict[str, int]
    tasks: dict[str, VillageTask] = Field(default_factory=dict)
    events: list[VillageEvent] = Field(default_factory=list)
    breakdown: bool = False
    repaired: bool = False
    well_dug: bool = False
    bread_after_repair: int = 0
    roster: dict[str, VillageResident] = Field(default_factory=dict)
    role_contributions: dict[str, int] = Field(default_factory=dict)
    project_contributions: dict[str, dict[str, int]] = Field(default_factory=dict)
    shovel_recovered: bool = False
    wagon: VillageWagon | None = None
    bread_deliveries: int = Field(default=0, ge=0)
    turn_cursor: int = Field(default=0, ge=0)
    last_scheduled_actor: str | None = None
    notices: list[VillageEvent] = Field(default_factory=list)
    meetings: dict[str, list[str]] = Field(default_factory=dict)
    free_turn_actor: str | None = None
    stock_lots: dict[str, dict[str, list[VillageStockLot]]] = Field(default_factory=dict)
    stock_history: list[VillageStockSnapshot] = Field(default_factory=list)
    deadlines: dict[str, VillageDeadline] = Field(default_factory=dict)
    horse_leader: str | None = None
    horse_fed_day: int = -1
    trader_day: int = -1
    economy: VillageEconomyState | None = None
    social: VillageSocialState | None = None
    incidents: VillageEventsState | None = None
    maintenance: VillageMaintenanceState | None = None
    research: VillageResearchState | None = None
    mining: VillageMiningState | None = None
    livestock: VillageLivestockState | None = None
    day_policy: VillageDayPolicy | None = None
    configuration_requests: dict[str, dict[str, Any]] = Field(default_factory=dict)
    dashboard: VillageDashboard | None = None
    time_use: dict[str, VillageTimeUse] = Field(default_factory=dict)
    daily_time_use: dict[int, dict[str, VillageTimeUse]] = Field(default_factory=dict)


class Scenario(StrictModel):
    schema_version: Literal["1.0"]
    id: str
    title: str
    start_location: str
    exit_location: str
    objective: str
    initial_objective: str | None = None
    goal_note_id: str | None = None
    opening_narrative: str | None = None
    goal_type: Literal["actor_outside_start_location", "actor_at_exit_location", "all_actors_at_exit_location", "entity_prepared", "bread_batches_served", "treasures_returned", "rps_match_completed", "tic_tac_toe_completed", "erp_invoice_released", "bbq_temperature_controlled", "village_restored"]
    goal_entity_id: str | None = None
    goal_location_id: str | None = None
    failure_outputs: dict[str, str] = Field(default_factory=dict)
    required_intact_entities: list[str] = Field(default_factory=list)
    body: Body
    needs: dict[str, Need]
    entities: dict[str, Entity]
    recovery: dict[str, Recovery]
    recipe_actions: dict[str, str] = Field(default_factory=dict)
    recipes: list[Recipe] = Field(default_factory=list)
    bakery: BakeryConfig | None = None
    voyage: VoyageConfig | None = None
    rps: RPSConfig | None = None
    erp: ERPConfig | None = None
    bbq: BBQConfig | None = None
    village: VillageConfig | None = None
    budgets: Budgets | None = None
    agents: list[ScenarioAgent] = Field(default_factory=list, max_length=12)
    skill_mode: SkillMode = "scenario"
    skill_challenges: list[SkillChallenge] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_references(self) -> "Scenario":
        from jsonschema import Draft202012Validator

        if self.start_location not in self.entities or self.exit_location not in self.entities:
            raise ValueError("Scenario locations must exist")
        ids = [agent.id for agent in self.agents]
        if len(ids) > 3 and not (self.village and self.village.ai_controlled):
            raise ValueError("Ordinary scenarios support at most 3 agents; AI villages support up to 12")
        if len(ids) != len(set(ids)) or set(ids) & self.entities.keys():
            raise ValueError("Agent IDs must be unique and distinct from entity IDs")
        for agent in self.agents:
            location = agent.start_location or self.start_location
            if location not in self.entities or self.entities[location].kind != "location":
                raise ValueError("Agent starting locations must name existing locations")
        if self.goal_type == "all_actors_at_exit_location" and len(self.agents) < 2:
            raise ValueError("The cooperative exit goal requires at least two declared agents")
        if self.goal_note_id is not None:
            note = self.entities.get(self.goal_note_id)
            if (note is None or not note.readable_text or not note.readable_text.strip()
                    or not self.initial_objective or not self.initial_objective.strip()):
                raise ValueError("A discoverable goal requires a readable note and an initial exploration objective")
        elif self.initial_objective is not None:
            raise ValueError("An initial exploration objective requires a goal note")
        if any(need not in {"food", "sleep"} for need in self.needs):
            raise ValueError("This world engine supports food and sleep needs")
        for need in self.needs:
            if need not in self.recovery:
                raise ValueError(f"Missing recovery mechanic for {need}")
        recovery_ids = [recovery.action_id for recovery in self.recovery.values()]
        core_ids = {"look", "search", "read", "open", "unlock", "take", "drop", "go", "hit", "serve",
                    "plug", "unplug", "turn_on", "turn_off", "place", "measure", "mix_dough",
                    "rise_dough", "shape_dough", "bake_bread", "unload_bread", "serve_bread", "wait",
                    "say", "give", "teleport", "recruit", "sail", "wait_tide", "dig", "pace", "play_rps", "play_tic_tac_toe", "erp_terminal", "adjust_bbq_heat", "monitor_bbq",
                    "cast_unlock", "cast_shrink", "cast_restore_size",
                    "village_ask", "village_request", "village_wait", "village_trade", "village_tell", "village_cancel"}
        core_ids |= {"village_work", "village_load", "village_deliver", "village_project",
                     "village_patrol", "village_observe", "village_eat", "village_sleep",
                     "village_fit_wagon", "village_start_route", "village_stop_route",
                     "village_load_wagon", "village_unload_wagon", "village_meeting", "village_cook_fish",
                     "village_drive_wagon"}
        core_ids |= {"village_load_goods", "village_unload_goods", "village_grow_hay",
                     "village_gather_herbs", "village_fell_logs", "village_fertilize",
                     "village_drink_tea", "village_buy_iron", "village_lead_horse",
                     "village_release_horse", "village_hitch", "village_unhitch",
                     "village_start_timber_route", "village_stop_timber_route",
                     "village_urgent_work", "village_share_route"}
        from npc_sim import village_economy, village_social, village_events, village_research, village_mining, village_livestock, village_communication
        core_ids |= village_economy.VERBS | village_social.VERBS | village_events.VERBS | village_research.VERBS | village_mining.VERBS | village_livestock.VERBS | village_communication.VERBS | {"village_wait_event"}
        if len(recovery_ids) != len(set(recovery_ids)) or set(recovery_ids) & core_ids:
            raise ValueError("Recovery action IDs must be unique and not conflict with core world verbs")
        if set(self.recipe_actions) & (core_ids | set(recovery_ids)):
            raise ValueError("Recipe actions must not conflict with other world verbs")
        challenge_ids = {challenge.id for challenge in self.skill_challenges}
        if (len(challenge_ids) != len(self.skill_challenges)
                or challenge_ids & (core_ids | set(recovery_ids) | set(self.recipe_actions))):
            raise ValueError("Skill challenge IDs must be unique and not conflict with other actions")
        supported = {capability for agent in self.agents
                     for capability in (agent.body or self.body).capabilities} if self.agents else set(self.body.capabilities)
        for challenge in self.skill_challenges:
            if self.skill_mode != "npc_only" and challenge.capability not in supported:
                raise ValueError(f"No agent has the required capability: {challenge.capability}")
            if challenge.target not in self.entities or not set(challenge.requires) <= challenge_ids:
                raise ValueError("Skill targets and prerequisite challenges must exist")
            if any(eid not in self.entities or not self.entities[eid].takable for eid in challenge.carried):
                raise ValueError("Skill tools must be existing portable entities")
            if not set(challenge.installs) <= set(challenge.carried):
                raise ValueError("Installed items must be required carried tools")
            if any(eid not in self.entities or self.entities[eid].kind not in {"door", "container"}
                   or self.entities[eid].open is None for eid in challenge.opens):
                raise ValueError("Skill effects must open existing doors or containers")
        pending = set(challenge_ids)
        while pending:
            ready = {challenge.id for challenge in self.skill_challenges
                     if challenge.id in pending and not set(challenge.requires) & pending}
            if not ready:
                raise ValueError("Skill challenge prerequisites must not contain cycles")
            pending -= ready
        for recovery in self.recovery.values():
            Draft202012Validator.check_schema(recovery.arguments_schema)
        prototypes = dict(self.entities)
        for recipe in self.recipes:
            for eid, output in recipe.outputs.items():
                if eid in self.entities or (eid in prototypes and prototypes[eid] != output):
                    raise ValueError(f"Conflicting recipe output {eid}")
                if output.nutrition is not None:
                    raise ValueError("Recipe output nutrition is derived from consumed food")
                prototypes[eid] = output
        if any(eid not in prototypes or eid in self.entities or not reason.strip()
               for eid, reason in self.failure_outputs.items()):
            raise ValueError("Failure outputs must name recipe outputs and provide nonblank reasons")
        if self.goal_entity_id in self.failure_outputs:
            raise ValueError("A goal output cannot also be a failure output")
        if not set(self.required_intact_entities) <= prototypes.keys():
            raise ValueError("Required intact entities must name existing entities or recipe outputs")
        for entity in self.entities.values():
            if entity.traversal_capabilities and entity.kind != "door":
                raise ValueError("Traversal capabilities apply only to doors")
            if (not set(entity.required_challenges) <= challenge_ids
                    or (entity.required_challenges and entity.kind not in {"door", "container"})):
                raise ValueError("Barrier prerequisites must name existing skill challenges")
            if entity.kind == "door" and (entity.destination is None or entity.open is None):
                raise ValueError("Door entities require destination and open state")
            if entity.locked and entity.key_id is None and entity.lock_id is None:
                raise ValueError("Locked entities require a matching key reference")
            if entity.key_id is not None and entity.key_id not in prototypes:
                raise ValueError(f"Unknown entity reference {entity.key_id}")
            if not set(entity.alternative_key_ids) <= prototypes.keys():
                raise ValueError("Alternative keys must name existing entities or recipe outputs")
            if entity.hidden and (entity.discovery_target is None or entity.discovery_target not in self.entities):
                raise ValueError("Hidden entities require an existing discovery target")
            for reference in (entity.location, entity.destination, entity.lock_id, entity.plugged_into):
                if reference is not None and reference not in self.entities:
                    raise ValueError(f"Unknown entity reference {reference}")
            if entity.lock_id and self.entities[entity.lock_id].kind != "lock":
                raise ValueError("A door's lock reference must name a lock entity")
            if entity.plugged_into and self.entities[entity.plugged_into].kind != "outlet":
                raise ValueError("Appliances must connect to an outlet")
        connections = [entity.plugged_into for entity in self.entities.values() if entity.plugged_into]
        if len(connections) != len(set(connections)):
            raise ValueError("An outlet can power only one appliance")
        for eid, entity in self.entities.items():
            if any(f"{eid}-{suffix}" in self.entities for suffix in entity.break_into):
                raise ValueError("Breakage spawn IDs conflict with existing entities")
        argument_keys: dict[str, set[str]] = {}
        for recipe in self.recipes:
            if recipe.action_id not in self.recipe_actions:
                raise ValueError("Recipe requires an action description")
            keys = set(recipe.arguments)
            if recipe.action_id in argument_keys and argument_keys[recipe.action_id] != keys:
                raise ValueError("Recipes sharing an action must use the same argument keys")
            argument_keys[recipe.action_id] = keys
            references = set(recipe.arguments.values())
            if not references <= prototypes.keys():
                raise ValueError("Recipe arguments must reference existing items or declared outputs")
            if (not recipe.consumes or len(set(recipe.consumes)) != len(recipe.consumes)
                    or not (recipe.outputs or recipe.updates)):
                raise ValueError("Recipes must consume distinct ingredients and produce a change")
            if not (set(recipe.carried) | set(recipe.powered) | set(recipe.placements)
                    | set(recipe.placements.values()) | set(recipe.consumes) | set(recipe.requires) | set(recipe.updates)) <= references:
                raise ValueError("Recipe effects and requirements must reference argument items")
            if any(prototypes[eid].power is None for eid in recipe.powered):
                raise ValueError("Powered recipe tools must be electrical appliances")
            if any(not group or any(not label.strip() for label in group) for group in recipe.ingredient_requirements):
                raise ValueError("Ingredient requirements need nonblank alternatives")
            if set(recipe.consumes) & (set(recipe.carried) | set(recipe.updates)):
                raise ValueError("Recipes cannot consume their tool or an item they update")
            for output in recipe.outputs.values():
                if output.location is not None and output.location not in self.entities:
                    raise ValueError("Recipe output locations must exist")
            if recipe.nutrition_target is not None:
                if (recipe.nutrition_target not in recipe.updates or recipe.outputs
                        or not prototypes[recipe.nutrition_target].food):
                    raise ValueError("Nutrition transfer requires one updated food target and no outputs")
            elif any(prototypes[eid].food for eid in recipe.consumes) and not any(
                    output.food for output in recipe.outputs.values()):
                raise ValueError("Consumed food must transfer nutrition to a food output or target")
        if set(argument_keys) != set(self.recipe_actions):
            raise ValueError("Every recipe action must have a recipe")
        if self.bakery is not None:
            bakery = self.bakery
            groups = [bakery.bowls, bakery.ingredients, bakery.mixers, bakery.ovens]
            references = [value for group in groups for value in group] + [
                bakery.bench, bakery.rack, bakery.display]
            if len(references) != len(set(references)) or any(value not in self.entities for value in references):
                raise ValueError("Bakery references must be distinct existing entities")
            if any(self.entities[value].kind != "container" or not self.entities[value].open
                   for value in [*bakery.bowls, bakery.bench, bakery.rack, bakery.display]):
                raise ValueError("Bakery bowls and surfaces must be open containers")
            if any(self.entities[value].power is None for value in [*bakery.mixers, *bakery.ovens]):
                raise ValueError("Bakery machines must be electrical appliances")
            if self.goal_type != "bread_batches_served":
                raise ValueError("Bakery scenarios require a bread batch goal")
        elif self.goal_type == "bread_batches_served":
            raise ValueError("Bread batch goals require bakery configuration")
        if self.goal_type == "entity_prepared":
            if (self.goal_entity_id is None or self.goal_location_id is None
                    or self.goal_entity_id not in prototypes or self.goal_entity_id in self.entities
                    or self.goal_location_id not in self.entities
                    or self.entities[self.goal_location_id].kind != "container"):
                raise ValueError("Prepared-item goals require a recipe output and a destination container")
        elif self.goal_entity_id is not None or self.goal_location_id is not None:
            raise ValueError("Prepared-item goal references require the entity_prepared goal type")
        if self.voyage is not None:
            voyage = self.voyage
            if self.goal_type != "treasures_returned" or self.agents or self.bakery:
                raise ValueError("Voyages require a single-agent treasure goal without bakery clocks")
            locations = [*voyage.teleport_locations, *voyage.docks, voyage.cabin_id,
                         *voyage.tidal_locations, *voyage.tide_visible_locations]
            if any(eid not in self.entities or self.entities[eid].kind != "location" for eid in locations):
                raise ValueError("Voyage locations must name existing locations")
            references = [voyage.teleport_item, voyage.ship_id, voyage.boarding_exit, voyage.crew_id,
                          voyage.crew_offer, voyage.chart_id, voyage.flotation_id, *voyage.treasures,
                          *voyage.dig_sites, *(site.tool_id for site in voyage.dig_sites.values())]
            if any(eid not in self.entities for eid in references):
                raise ValueError("Voyage objects must name existing entities")
            if (len(set(voyage.docks)) != 2 or len(set(voyage.teleport_locations)) != 2
                    or len(set(voyage.treasures)) != len(voyage.treasures)
                    or voyage.cabin_id in voyage.docks):
                raise ValueError("Voyage docks, teleport endpoints and treasures must be distinct")
            boarding = self.entities[voyage.boarding_exit]
            if (boarding.kind != "door" or boarding.location != voyage.docks[0]
                    or boarding.destination != voyage.cabin_id or boarding.takable
                    or self.entities[voyage.ship_id].location != voyage.docks[0]):
                raise ValueError("Voyage boarding exit and ship must start at the first dock")
            book = self.entities[voyage.teleport_item]
            if not book.takable or not book.readable_text or voyage.teleport_word not in book.readable_text:
                raise ValueError("Teleport item must be portable and explain the magic word")
            if any(not self.entities[eid].takable for eid in [
                    voyage.crew_offer, voyage.chart_id, voyage.flotation_id, *voyage.treasures]):
                raise ValueError("Voyage supplies and treasures must be portable")
            if any(self.entities[eid].takable for eid in [voyage.ship_id, voyage.crew_id]):
                raise ValueError("The ship and crew cannot be carried")
            for eid in voyage.dig_sites:
                site = self.entities[eid]
                if site.kind != "container" or site.open or site.takable or not site.jammed:
                    raise ValueError("Dig sites must be fixed, closed, buried containers")
            for eid, rule in voyage.passage_rules.items():
                if eid not in self.entities or self.entities[eid].kind != "door":
                    raise ValueError("Voyage passage rules must reference existing doors")
                if not (set(rule.carried) | set(rule.requires)) <= prototypes.keys():
                    raise ValueError("Voyage passage requirements must reference existing items or outputs")
        elif self.goal_type == "treasures_returned":
            raise ValueError("Treasure goals require voyage configuration")
        if self.rps is not None:
            if (self.goal_type != "rps_match_completed" or len(self.agents) != 2
                    or self.bakery is not None or self.voyage is not None):
                raise ValueError("Rock-paper-scissors requires two agents and a match goal without bakery or voyage")
            if any((agent.start_location or self.start_location) != self.start_location for agent in self.agents):
                raise ValueError("Rock-paper-scissors players must start together")
        elif self.goal_type == "rps_match_completed":
            raise ValueError("Match goals require rock-paper-scissors configuration")
        if self.goal_type == "tic_tac_toe_completed":
            if len(self.agents) != 2 or any((self.bakery, self.voyage, self.rps)):
                raise ValueError("Tic-tac-toe requires two agents without bakery, voyage or rock-paper-scissors")
            if any((agent.start_location or self.start_location) != self.start_location for agent in self.agents):
                raise ValueError("Tic-tac-toe players must start together")
        if self.erp is not None:
            if (self.goal_type != "erp_invoice_released" or self.agents
                    or any((self.bakery, self.voyage, self.rps, self.recipes, self.skill_challenges))
                    or self.needs or self.recovery or self.goal_note_id is not None):
                raise ValueError("ERP requires a single-agent invoice goal without physical mechanics or needs")
            if (self.start_location != self.exit_location or len(self.entities) != 1
                    or self.entities[self.start_location].kind != "location"
                    or self.body.capabilities != ["erp_terminal"] or self.body.capacity != 0):
                raise ValueError("ERP scenarios require one virtual workspace and an ERP-terminal-only body")
        elif self.goal_type == "erp_invoice_released":
            raise ValueError("ERP invoice goals require ERP configuration")
        if self.bbq is not None:
            if (self.goal_type != "bbq_temperature_controlled" or self.agents
                    or any((self.bakery, self.voyage, self.rps, self.erp, self.recipes, self.skill_challenges))
                    or self.needs or self.recovery or self.goal_note_id is not None):
                raise ValueError("BBQ requires a single-agent temperature goal without other mechanics or needs")
            if (self.start_location != self.exit_location or len(self.entities) != 1
                    or self.entities[self.start_location].kind != "location"
                    or self.body.capabilities != ["bbq_control"] or self.body.capacity != 0):
                raise ValueError("BBQ scenarios require one station and a BBQ-control-only body")
        elif self.goal_type == "bbq_temperature_controlled":
            raise ValueError("BBQ temperature goals require BBQ configuration")
        if self.village is not None:
            from npc_sim.village import validate_config
            validate_config(self)
        elif self.goal_type == "village_restored":
            raise ValueError("Village goals require village configuration")
        return self


class Actor(StrictModel):
    actor_id: str
    session_id: str
    location_id: str
    start_location_id: str
    body: Body
    inventory: list[str] = Field(default_factory=list)
    needs: dict[str, Need]
    inspected: list[str] = Field(default_factory=list)
    examined: list[str] = Field(default_factory=list)
    read_entities: list[str] = Field(default_factory=list)
    visited_locations: list[str] = Field(default_factory=list)
    action_clock: int = 0
    messages: list[dict[str, str]] = Field(default_factory=list)
    discovered_entities: list[str] = Field(default_factory=list)


class SecondaryCondition(StrictModel):
    kind: Literal["discovered", "carrying", "at_location", "item_at", "opened", "unlocked", "read"]
    target: str = Field(min_length=1)
    actor_id: str | None = None
    quantifier: Literal["any", "all"] = "any"
    location_id: str | None = None

    @model_validator(mode="after")
    def valid_condition(self) -> "SecondaryCondition":
        if (self.kind == "item_at") != (self.location_id is not None):
            raise ValueError("Only item_at conditions require a location_id.")
        if self.kind in {"item_at", "opened", "unlocked"} and (
                self.actor_id is not None or self.quantifier != "any"):
            raise ValueError("Object-state conditions do not take actor selectors.")
        if self.actor_id is not None and self.quantifier != "any":
            raise ValueError("Choose either a specific actor or an any/all assigned-actor condition.")
        return self


class SecondaryGoalSpec(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    assigned_actor_ids: list[str] = Field(min_length=1)
    conditions: list[SecondaryCondition] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def valid_goal(self) -> "SecondaryGoalSpec":
        if not self.title.strip() or not self.description.strip():
            raise ValueError("Side goals need a nonblank title and description.")
        if len(set(self.assigned_actor_ids)) != len(self.assigned_actor_ids):
            raise ValueError("Side-goal assignments must not contain duplicate actors.")
        return self


class SecondaryGoal(SecondaryGoalSpec):
    status: Literal["active", "achieved"] = "active"
    condition_met: list[bool] = Field(default_factory=list)
    completed_revision: str | None = None


class WorldState(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    world_id: str
    scenario_id: str
    exit_location: str | None = None
    revision: int = 0
    entities: dict[str, Entity]
    actors: dict[str, Actor]
    secondary_goals: dict[str, SecondaryGoal] = Field(default_factory=dict)
    skill_uses: dict[str, str] = Field(default_factory=dict)
    requests: dict[str, dict[str, Any]] = Field(default_factory=dict)
    request_history_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    admin_requests: list[str] = Field(default_factory=list)
    clock: SimulationClock | None = None
    batches: dict[str, BreadBatch] = Field(default_factory=dict)
    ingredient_stock: dict[str, int] = Field(default_factory=dict)
    voyage: VoyageState | None = None
    rps: RPSState | None = None
    tic_tac_toe: TicTacToeState | None = None
    erp: ERPState | None = None
    bbq: BBQState | None = None
    village: VillageState | None = None


class ProxyConfig(StrictModel):
    base_url: str = "http://127.0.0.1:9901/v1"
    model: str | None = None
    token_environment: str | None = None
    timeout_seconds: float = Field(default=15, gt=0, le=120)


class WorldConfig(StrictModel):
    transport: Literal["stdio", "streamable_http"] = "stdio"
    scenario: str = "escape-room"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None


class AppConfig(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    proxy: ProxyConfig
    world: WorldConfig
    budgets: Budgets


class Decision(StrictModel):
    action_id: str
    arguments: dict[str, Any]
    justification: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)
    subgoal: str = ""
    plan: list[str] = Field(default_factory=list)


class StartRequest(StrictModel):
    objective: str | None = None
    scenario_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")
    agent_assignments: dict[str, str] | None = None
    skill_mode: SkillMode | None = None


class ReopenRequest(StrictModel):
    run_id: str = Field(min_length=1)
    until_midnight: bool = False


class RunModeRequest(StrictModel):
    run_id: str = Field(min_length=1)
    run_until_complete: bool = Field(strict=True)


class EpisodeSaveRequest(StrictModel):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def nonblank_name(self) -> "EpisodeSaveRequest":
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Give the save point a name.")
        return self


class EpisodeLoadRequest(StrictModel):
    save_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    current_run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class EpisodeSave(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=120)
    saved_at: str
    resumable: bool
    run: dict[str, Any]
    memory: dict[str, Any]
    scenario: Scenario
    world: WorldState
    events: list[dict[str, Any]]


class BudgetExtension(StrictModel):
    run_id: str = Field(min_length=1)
    add_actions: int = Field(default=0, ge=0, strict=True)
    add_model_calls: int = Field(default=0, ge=0, strict=True)
    add_seconds: float = Field(default=0, ge=0, strict=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def positive_extension(self) -> "BudgetExtension":
        if not (self.add_actions or self.add_model_calls or self.add_seconds):
            raise ValueError("Add at least one action, model call, or second.")
        return self


class NeedToggle(StrictModel):
    enabled: bool

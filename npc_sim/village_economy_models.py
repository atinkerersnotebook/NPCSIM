"""Standalone, opt-in village economy configuration and persistent ledgers."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Quantity = Annotated[int, Field(ge=0, strict=True)]
PositiveQuantity = Annotated[int, Field(gt=0, strict=True)]
Minute = Annotated[int, Field(ge=0, strict=True)]


class EconomyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class VillageRecipe(EconomyModel):
    inputs: dict[str, PositiveQuantity]
    portions: PositiveQuantity = 1


class VillageMealPlan(EconomyModel):
    id: str = Field(min_length=1)
    use_minute: int = Field(ge=0, lt=1440, multiple_of=5)
    portions: Quantity
    recipe_portions: dict[str, Quantity]
    bread_service: Quantity = 0
    tea_portions: Quantity = 0
    additional_inputs: dict[str, Quantity] = Field(default_factory=dict)
    resident_portions: Quantity = 12
    visitor_portions: Quantity = 0

    @model_validator(mode="before")
    @classmethod
    def default_portion_split(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        split_supplied = "resident_portions" in values or "visitor_portions" in values
        if not split_supplied and "portions" in values:
            return {**values, "resident_portions": values["portions"], "visitor_portions": 0}
        return values

    @model_validator(mode="after")
    def check_portions(self) -> "VillageMealPlan":
        if self.resident_portions + self.visitor_portions != self.portions:
            raise ValueError("Resident and visitor portions must sum to planned portions")
        if sum(self.recipe_portions.values()) != self.portions:
            raise ValueError("Meal recipe portions must sum to planned portions")
        return self


class VillagePublicWork(EconomyModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=160)
    location: str
    workers: list[str] = Field(default_factory=list)
    materials: dict[str, PositiveQuantity]
    batch_sizes: dict[str, PositiveQuantity] = Field(default_factory=dict)
    work_minutes: int = Field(default=15, ge=5, le=120, multiple_of=5)
    starts_at: Minute = 0
    due_at: Minute | None = None

    @model_validator(mode="after")
    def check_batches(self) -> "VillagePublicWork":
        if not self.workers:
            raise ValueError("Public works require at least one assigned worker")
        if any(product not in self.materials for product in self.batch_sizes):
            raise ValueError("Public-work batch sizes must name required materials")
        if self.due_at is not None and self.due_at <= self.starts_at:
            raise ValueError("A public work must be due after it starts")
        return self


class VillagePublicCraft(EconomyModel):
    supplier: str
    product: str
    inputs: dict[str, PositiveQuantity]
    output_quantity: PositiveQuantity = 1
    minutes: int = Field(default=30, ge=5, le=240, multiple_of=5)


class VillageDailyMenu(EconomyModel):
    day: Quantity
    selected_at: Minute
    recipe_portions: dict[str, Quantity]


class VillageDungeonAppeal(EconomyModel):
    id: str
    actor: str
    request: str = Field(min_length=5, max_length=500)
    category: Literal["help", "resource", "event"]
    created_at: Minute
    expires_at: Minute
    status: Literal["pending", "answered", "declined", "timed_out"] = "pending"
    response: str | None = Field(default=None, max_length=1000)


class VillageEconomyConfig(EconomyModel):
    innkeeper: str = "elsie"
    recipes: dict[str, VillageRecipe] = Field(default_factory=lambda: {
        "bread": VillageRecipe(inputs={"bread": 1}, portions=2),
        "fish": VillageRecipe(inputs={"fish": 1}, portions=2),
    })
    meal_plans: list[VillageMealPlan] = Field(default_factory=lambda: [
        VillageMealPlan(id="supper", use_minute=1140, portions=12,
                        recipe_portions={"bread": 8, "fish": 4})
    ])
    suppliers: dict[str, str] = Field(default_factory=dict)
    supplier_alternatives: dict[str, list[str]] = Field(default_factory=dict)
    stock_buffer: dict[str, Quantity] = Field(default_factory=dict)
    reserve_targets: dict[str, dict[str, Quantity]] = Field(default_factory=dict)
    production_targets: dict[str, dict[str, Quantity]] = Field(default_factory=dict)
    stock_capacities: dict[str, dict[str, Quantity]] = Field(default_factory=dict)
    retail_markup_percent: int = Field(default=20, ge=0, le=100, strict=True)
    retail_store: str = "general-store"
    prices: dict[str, PositiveQuantity] = Field(default_factory=dict)
    delivery_fee: Quantity | None = None
    meal_price: Quantity | None = None
    subsidy_per_meal: Quantity = 0
    subsidy_payer: str | None = None
    town_treasury: Quantity = 0
    public_works: list[VillagePublicWork] = Field(default_factory=list)
    public_crafts: dict[str, VillagePublicCraft] = Field(default_factory=dict)
    cooking_lead_minutes: int = Field(default=60, ge=5, multiple_of=5)
    delivery_lead_minutes: int = Field(default=30, ge=0, multiple_of=5)
    require_orders: bool = True

    @model_validator(mode="after")
    def check_plans(self) -> "VillageEconomyConfig":
        if len({plan.id for plan in self.meal_plans}) != len(self.meal_plans):
            raise ValueError("Meal plan identifiers must be unique")
        if any(recipe not in self.recipes for plan in self.meal_plans
               for recipe in plan.recipe_portions):
            raise ValueError("Meal plans must name configured recipes")
        if self.subsidy_per_meal and self.subsidy_payer is None:
            raise ValueError("Subsidized meals require an explicit funding account")
        if len({project.id for project in self.public_works}) != len(self.public_works):
            raise ValueError("Public-work identifiers must be unique")
        if self.public_works and not self.town_treasury:
            raise ValueError("Public works require a funded town treasury")
        if any(product not in self.public_crafts or self.public_crafts[product].product != product
               for project in self.public_works for product in project.materials):
            raise ValueError("Every public-work material needs a matching public craft recipe")
        return self


class VillageSupplierOrder(EconomyModel):
    coordination_notice_id: str | None = None
    id: str
    request_key: str
    buyer: str
    supplier: str
    source: str
    destination: str
    product: str
    quantity: PositiveQuantity
    price: PositiveQuantity
    delivery_fee: Quantity = 0
    deadline: Minute
    required_fresh_until: Minute
    created_at: Minute
    status: Literal["proposed", "accepted", "accepted_planned", "in_production",
                    "ready_to_collect", "in_transit", "blocked", "expired", "declined",
                    "deferred", "rejected", "fulfilled", "cancelled", "failed"] = "proposed"
    production_plan: dict[str, int] = Field(default_factory=dict)
    delivered_quantity: Quantity = 0
    in_transit: Quantity = 0
    returned_quantity: Quantity = 0
    spoiled_quantity: Quantity = 0
    paid: Quantity = 0
    fees_paid: Quantity = 0
    public_work: str | None = None
    reason: str | None = None
    deferred_until: Minute | None = None

    @property
    def remaining(self) -> int:
        return max(0, self.quantity - self.delivered_quantity)

    @property
    def loadable(self) -> int:
        return max(0, self.remaining - self.in_transit)

    @property
    def commitment(self) -> int:
        # Reserve the worst case: each remaining unit needs a separate trip.
        return (self.remaining * (self.price + self.delivery_fee)
                if self.status in {"accepted", "accepted_planned", "in_production",
                                   "ready_to_collect", "in_transit", "blocked"} else 0)


class VillageEconomyPayment(EconomyModel):
    minute: Minute
    payer: str
    payee: str
    amount: Quantity
    kind: Literal["goods", "meal", "delivery_fee", "subsidy", "tea"]


class VillageEconomyMovement(EconomyModel):
    minute: Minute
    location: str
    product: str
    quantity: PositiveQuantity
    kind: Literal["produced", "consumed", "delivered", "loaded", "returned", "spoiled",
                  "imported", "exported"]
    order_id: str | None = None


class VillageShipmentReassignment(EconomyModel):
    from_carrier: str
    to_carrier: str
    reassigned_at: Minute
    reassigned_by: str
    reason: str = Field(min_length=1, max_length=500)


class VillageDeliveryShipment(EconomyModel):
    id: str
    order_id: str
    carrier: str
    quantity: PositiveQuantity
    mode: Literal["foot", "wagon"] = "foot"
    status: Literal["offered", "assigned", "ready", "picked_up", "in_transit",
                    "delivered", "blocked", "cancelled", "released"] = "offered"
    created_at: Minute
    updated_at: Minute
    accepted_at: Minute | None = None
    picked_up_at: Minute | None = None
    delivered_at: Minute | None = None
    delivered_quantity: Quantity = 0
    returned_quantity: Quantity = 0
    spoiled_quantity: Quantity = 0
    reason: str | None = None
    reassignments: list[VillageShipmentReassignment] = Field(default_factory=list)

    @property
    def outstanding(self) -> int:
        return max(0, self.quantity - self.delivered_quantity
                   - self.returned_quantity - self.spoiled_quantity)


class VillageEconomyState(EconomyModel):
    config: VillageEconomyConfig | None = None
    orders: dict[str, VillageSupplierOrder] = Field(default_factory=dict)
    next_order: PositiveQuantity = 1
    daily_menu: VillageDailyMenu | None = None
    initial_coins: dict[str, Quantity] = Field(default_factory=dict)
    public_work_progress: dict[str, dict[str, Quantity]] = Field(default_factory=dict)
    public_work_completed_at: dict[str, Minute] = Field(default_factory=dict)
    payments: list[VillageEconomyPayment] = Field(default_factory=list)
    movements: list[VillageEconomyMovement] = Field(default_factory=list)
    shipments: dict[str, VillageDeliveryShipment] = Field(default_factory=dict)
    next_shipment: PositiveQuantity = 1

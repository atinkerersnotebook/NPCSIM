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

    @model_validator(mode="after")
    def check_portions(self) -> "VillageMealPlan":
        if sum(self.recipe_portions.values()) != self.portions:
            raise ValueError("Alternative recipe portions must sum to planned diners")
        return self


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
    stock_buffer: dict[str, Quantity] = Field(default_factory=dict)
    reserve_targets: dict[str, dict[str, Quantity]] = Field(default_factory=dict)
    production_targets: dict[str, dict[str, Quantity]] = Field(default_factory=dict)
    prices: dict[str, PositiveQuantity] = Field(default_factory=dict)
    delivery_fee: Quantity | None = None
    meal_price: Quantity | None = None
    subsidy_per_meal: Quantity = 0
    subsidy_payer: str | None = None
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
    status: Literal["proposed", "accepted", "deferred", "rejected", "fulfilled",
                    "cancelled", "failed"] = "proposed"
    delivered_quantity: Quantity = 0
    in_transit: Quantity = 0
    returned_quantity: Quantity = 0
    spoiled_quantity: Quantity = 0
    paid: Quantity = 0
    fees_paid: Quantity = 0
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
        return self.remaining * (self.price + self.delivery_fee) if self.status == "accepted" else 0


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


class VillageEconomyState(EconomyModel):
    config: VillageEconomyConfig | None = None
    orders: dict[str, VillageSupplierOrder] = Field(default_factory=dict)
    next_order: PositiveQuantity = 1
    initial_coins: dict[str, Quantity] = Field(default_factory=dict)
    payments: list[VillageEconomyPayment] = Field(default_factory=list)
    movements: list[VillageEconomyMovement] = Field(default_factory=list)

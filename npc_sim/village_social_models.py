"""Opt-in social configuration and durable state, independent of engine models."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SocialModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


Minute = Annotated[int, Field(ge=0, multiple_of=5)]
Amount = Annotated[int, Field(gt=0)]
ActivityKind = Literal[
    "reading", "gardening", "crafting", "visiting", "games", "storytelling",
    "shared_tea", "rest", "field_observe", "organize_notes", "draft", "revise",
    "recipe_test", "instrument_repair", "fishing_survey", "share_discovery", "catch_release", "river_cleanup",
]
CAPABILITIES = frozenset({"activity", "chapter", "item_at", "production", "repair", "discovery_shared", "band"})


class ResidentInterests(SocialModel):
    solo: list[str] = Field(default_factory=list)
    social: list[str] = Field(default_factory=list)
    companions: list[str] = Field(default_factory=list)


class SocialActivity(SocialModel):
    kind: ActivityKind
    location: str
    duration: int = Field(default=30, ge=5, le=120, multiple_of=5)
    interests: list[str] = Field(default_factory=list)
    supplies: dict[str, Amount] = Field(default_factory=dict)
    equipment: list[str] = Field(default_factory=list)
    min_participants: int = Field(default=1, ge=1, le=12)
    max_participants: int = Field(default=1, ge=1, le=12)
    skill: str | None = None
    project: str | None = None
    chapter: str | None = None
    recipe: Literal["bread_test", "tea_blending", "plank_practice", "garden_tending", "furniture", "tackle", "smoked_fish", "herb_garden"] | None = None
    repair_target: str | None = None
    discovery: str | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.min_participants > self.max_participants:
            raise ValueError("Activity participant bounds are reversed")
        book = self.kind in {"field_observe", "organize_notes", "draft", "revise", "recipe_test"}
        if book != (self.project is not None and self.chapter is not None):
            raise ValueError("Book operations require a project and chapter; other activities do not")
        if (self.project is None) != (self.chapter is None):
            raise ValueError("Project and chapter must be supplied together")
        if book and self.max_participants != 1:
            raise ValueError("Verified writing operations are solo activities")
        if self.kind == "shared_tea" and not self.supplies.get("tea"):
            raise ValueError("Shared tea must consume real tea")
        if (self.kind == "instrument_repair") != (self.repair_target is not None):
            raise ValueError("Only actual instrument repairs specify a repair target")
        if self.kind == "instrument_repair" and not self.supplies:
            raise ValueError("Instrument repair requires real consumed materials")
        if (self.kind in {"fishing_survey", "share_discovery"}) != (self.discovery is not None):
            raise ValueError("Only verified survey/sharing activities specify a discovery")
        if self.kind == "catch_release" and (not self.equipment or self.max_participants != 1):
            raise ValueError("Catch-and-release requires a real rod and a solo angler")
        if self.kind == "fishing_survey" and (not self.equipment or self.max_participants != 1):
            raise ValueError("A fishing survey is solo and requires actual fishing equipment")
        if self.kind == "share_discovery" and self.min_participants < 2:
            raise ValueError("Sharing a discovery requires an actual consenting listener")
        if self.recipe is not None and self.kind not in {"crafting", "gardening", "recipe_test"}:
            raise ValueError("Material transformations require a supported practical activity")
        if self.kind == "recipe_test" and self.recipe != "bread_test":
            raise ValueError("The supported tested recipe is a real bread batch")
        return self


class HerbalChapter(SocialModel):
    site: str
    specimen: str


class HerbalProject(SocialModel):
    kind: Literal["herbal_book", "recipe"] = "herbal_book"
    owner: str
    title: str = "Local plants and village traditions"
    chapters: dict[str, HerbalChapter]


class BandMember(SocialModel):
    role: str
    instrument: str


class VillageBand(SocialModel):
    coordinator: str
    venue: str
    members: dict[str, BandMember]
    duration: int = Field(default=30, ge=5, le=120, multiple_of=5)
    earliest_minute: int = Field(default=1170, ge=1140, lt=1440, multiple_of=5)
    latest_minute: int = Field(default=1260, ge=1140, lt=1440, multiple_of=5)
    fee_per_member: int = Field(default=0, ge=0)
    payer: str | None = None
    skill: str = "music"

    @model_validator(mode="after")
    def coherent(self):
        if len(self.members) < 2:
            raise ValueError("A band needs at least two configured members")
        if self.earliest_minute > self.latest_minute:
            raise ValueError("Band time window is reversed")
        if self.fee_per_member and self.payer is None:
            raise ValueError("Performer fees require an explicit payer")
        instruments = [member.instrument for member in self.members.values()]
        if len(set(instruments)) != len(instruments):
            raise ValueError("Members cannot play the same physical instrument concurrently")
        return self


class QuestObjective(SocialModel):
    kind: Literal["activity", "chapter", "item_at", "production", "repair", "discovery_shared", "band"]
    target: str
    location: str | None = None
    project: str | None = None
    quantity: int = Field(default=1, ge=1, le=20)

    @model_validator(mode="after")
    def coherent(self):
        if (self.kind == "item_at") != (self.location is not None):
            raise ValueError("Only item_at objectives specify a location")
        if (self.kind == "chapter") != (self.project is not None):
            raise ValueError("Only chapter objectives specify a project")
        if self.kind in {"item_at", "chapter"} and self.quantity != 1:
            raise ValueError("Item and chapter objectives have quantity one")
        return self


class QuestTemplate(SocialModel):
    giver: str
    title: str
    eligible: list[str]
    objective: QuestObjective
    offer_at: Minute = 0
    expires_at: Minute
    reward: int = Field(default=0, ge=0)
    prerequisites: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self):
        if self.expires_at <= self.offer_at:
            raise ValueError("Quest expiry must follow its offer")
        if not self.eligible or len(set(self.eligible)) != len(self.eligible):
            raise ValueError("Quest eligibility must be nonempty and unique")
        return self


class VillageSocialConfig(SocialModel):
    interests: dict[str, ResidentInterests] = Field(default_factory=dict)
    activities: dict[str, SocialActivity] = Field(default_factory=dict)
    projects: dict[str, HerbalProject] = Field(default_factory=dict)
    bands: dict[str, VillageBand] = Field(default_factory=dict)
    quests: dict[str, QuestTemplate] = Field(default_factory=dict)
    # Entries are existing object IDs, never newly instantiated instruments.
    instrument_inventory: dict[str, list[str]] = Field(default_factory=dict)
    skill_cap: int = Field(default=10, ge=1, le=100)
    relationship_cap: int = Field(default=10, ge=1, le=100)
    daily_skill_limit: int = Field(default=2, ge=1, le=10)
    daily_relationship_limit: int = Field(default=2, ge=1, le=10)
    action_allowance: int = Field(default=5, ge=5, le=30, multiple_of=5)


class ChapterProgress(SocialModel):
    observed_at: Minute | None = None
    organized_at: Minute | None = None
    drafted_at: Minute | None = None
    revised_at: Minute | None = None
    evidence: list[int] = Field(default_factory=list)


class SocialPerson(SocialModel):
    skills: dict[str, int] = Field(default_factory=dict)
    enjoyment: int = Field(default=0, ge=0, le=100)
    skill_awards: dict[str, int] = Field(default_factory=dict)
    relationship_awards: dict[str, int] = Field(default_factory=dict)
    discoveries: dict[str, "SocialDiscovery"] = Field(default_factory=dict)
    garden_care: dict[str, int] = Field(default_factory=dict)
    practice_days: dict[str, int] = Field(default_factory=dict)


class SocialDiscovery(SocialModel):
    location: str
    observed_at: Minute
    shared_with: list[str] = Field(default_factory=list)
    evidence: list[int] = Field(default_factory=list)


class ActivityInterval(SocialModel):
    id: str
    activity: str
    kind: Literal[
        "reading", "gardening", "crafting", "visiting", "games", "storytelling",
        "shared_tea", "rest", "field_observe", "organize_notes", "draft", "revise",
        "rehearsal", "performance",
        "recipe_test", "instrument_repair", "fishing_survey", "share_discovery", "catch_release", "river_cleanup",
    ]
    location: str
    participants: list[str]
    starts_at: Minute
    ends_at: Minute
    actual_end: Minute | None = None
    status: Literal["active", "completed", "interrupted"] = "active"
    consumed: dict[str, int] = Field(default_factory=dict)
    produced: dict[str, int] = Field(default_factory=dict)
    equipment: list[str] = Field(default_factory=list)
    reason: str | None = None
    band_plan: str | None = None
    social_plan: str | None = None
    evidence: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("An activity must occupy a positive interval")
        if not self.participants or len(set(self.participants)) != len(self.participants):
            raise ValueError("Activity participants must be nonempty and unique")
        if self.actual_end is not None and not self.starts_at <= self.actual_end <= self.ends_at:
            raise ValueError("Actual activity end must lie inside the reserved interval")
        return self


class BandPlan(SocialModel):
    id: str
    band: str
    mode: Literal["rehearsal", "performance"]
    starts_at: Minute
    ends_at: Minute
    invited: list[str]
    accepted: list[str] = Field(default_factory=list)
    declined: list[str] = Field(default_factory=list)
    audience: list[str] = Field(default_factory=list)
    status: Literal["invited", "active", "completed", "cancelled"] = "invited"
    reason: str | None = None
    paid: bool = False


class SocialPlan(SocialModel):
    id: str
    activity: str
    organizer: str
    starts_at: Minute
    ends_at: Minute
    invited: list[str]
    accepted: list[str]
    status: Literal["invited", "active", "completed", "cancelled"] = "invited"
    reason: str | None = None


class QuestProgress(SocialModel):
    status: Literal[
        "offered", "accepted", "declined", "deferred", "completed", "abandoned",
        "expired", "failed",
    ] = "offered"
    assignee: str | None = None
    accepted_at: Minute | None = None
    defer_until: Minute | None = None
    baseline: int = 0
    progress: int = Field(default=0, ge=0)
    evidence: list[int] = Field(default_factory=list)
    reward_reserved: int = Field(default=0, ge=0)
    reward_paid: bool = False
    objective_verified: bool = False
    reason: str | None = None


class VillageSocialState(SocialModel):
    config: VillageSocialConfig | None = None
    revision: int = Field(default=0, ge=0)
    configuration_history: list[VillageSocialConfig] = Field(default_factory=list)
    residents: dict[str, SocialPerson] = Field(default_factory=dict)
    projects: dict[str, dict[str, ChapterProgress]] = Field(default_factory=dict)
    intervals: list[ActivityInterval] = Field(default_factory=list)
    band_plans: dict[str, BandPlan] = Field(default_factory=dict)
    social_plans: dict[str, SocialPlan] = Field(default_factory=dict)
    quests: dict[str, QuestProgress] = Field(default_factory=dict)
    sequence: int = Field(default=0, ge=0)
    production_counts: dict[str, int] = Field(default_factory=dict)
    processed_event_count: int = Field(default=0, ge=0)
    garden_yields: dict[str, int] = Field(default_factory=dict)
    manure_cargo: dict[str, list[dict]] = Field(default_factory=dict)

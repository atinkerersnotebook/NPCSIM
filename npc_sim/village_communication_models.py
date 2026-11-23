"""Opt-in physical communication and independently received snapshots."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from npc_sim.village_economy_models import VillageDeliveryShipment, VillageSupplierOrder

Number = Annotated[int, Field(strict=True, ge=0)]
Positive = Annotated[int, Field(strict=True, gt=0)]
Text = Annotated[str, Field(strict=True, min_length=1, max_length=500)]
NoticeType = Literal["supply_request", "work_request", "availability", "status_update",
                     "meeting", "warning", "general"]
Status = Literal["posted", "seen", "accepted", "in_progress", "delivered", "completed",
                 "rejected", "cancelled", "expired", "superseded"]


class CommunicationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, strict=True)


class VillageCommunicationConfig(CommunicationModel):
    board_id: Text = "noticeboard"
    workplace_boxes: bool = True
    instant_messages: bool = False
    max_hops: Positive = Field(default=8, le=8)
    cooldown_minutes: Positive = Field(default=15, le=1440)


class NoticeTerms(CommunicationModel):
    type: NoticeType
    subject: Text
    description: Text
    recipients: list[Text] = Field(default_factory=list, max_length=12)
    private: bool = False
    priority: Literal["normal", "high", "urgent"] = "normal"
    quantity: Positive | None = None
    destination: Text | None = None
    due_at: Number | None = None
    expires_at: Number | None = None
    required_fresh_until: Number | None = None
    unit_price: Positive | None = None
    delivery_fee: Number | None = None
    project: Literal["dig_well", "repair_mill"] | None = None


class Appointment(CommunicationModel):
    id: Text
    version: Positive
    project: Literal["dig_well", "repair_mill"]
    members: list[Text]
    accepted: list[Text]
    place: Text
    due_at: Number
    end_at: Number
    status: Literal["proposed", "confirmed", "completed", "cancelled", "expired"] = "proposed"


class NoticeVersion(CommunicationModel):
    id: Text
    version: Positive
    author: Text
    terms: NoticeTerms
    status: Status = "posted"
    created_at: Number
    updated_at: Number
    responder: Text | None = None
    response: Text | None = None
    proposed_deadline: Number | None = None
    proposed_fresh_until: Number | None = None
    revised_from_counteroffer: bool = False
    linked_order_id: Text | None = None
    order_snapshot: dict = Field(default_factory=dict)
    appointment: Appointment | None = None
    supersedes_notice_id: Text | None = None


class NoticeKnowledge(CommunicationModel):
    snapshot: NoticeVersion
    heard_at: Number
    source_type: Literal["noticeboard", "inbox", "conversation", "author", "witness", "remote", "tin_can"]
    source_actor: Text | None = None
    confidence: Literal["direct", "secondhand"] = "direct"
    source_chain: list[Text] = Field(default_factory=list, max_length=4)
    hops: Number = Field(default=0, le=8)
    last_verified_at: Number | None = None


class TransferAuthorization(CommunicationModel):
    job_id: Text
    source_site: Text
    destination_site: Text
    good: Text
    quantity: Positive
    allowed_carriers: list[Text]
    material_owner: Text
    stock_reservation_id: Text
    due_time: Number
    status: Literal["authorized", "in_transit", "delivered", "finished", "blocked", "cancelled"] = "authorized"
    loaded: Number = 0
    delivered: Number = 0
    reason: str | None = None


class LogisticsSnapshot(CommunicationModel):
    order: VillageSupplierOrder
    shipments: list[VillageDeliveryShipment] = Field(default_factory=list)
    unassigned_quantity: Number
    confirmed_at: Number


class LogisticsMessage(CommunicationModel):
    sequence: Positive
    type: Literal["ORDER_ACCEPTED", "SHIPMENT_OFFERED", "SHIPMENT_ASSIGNED", "GOODS_READY",
                  "GOODS_PICKED_UP", "GOODS_DELIVERED", "SHIPMENT_BLOCKED", "SHIPMENT_RELEASED"]
    minute: Number
    order_id: Text
    shipment_id: Text | None = None
    recipients: list[Text]
    snapshot: LogisticsSnapshot


class VillageCommunicationState(CommunicationModel):
    config: VillageCommunicationConfig
    notices: dict[str, list[NoticeVersion]] = Field(default_factory=dict)
    knowledge: dict[str, dict[str, NoticeKnowledge]] = Field(default_factory=dict)
    placements: dict[str, dict[str, int]] = Field(default_factory=dict)
    boxes: dict[str, str] = Field(default_factory=dict)
    shares: dict[str, int] = Field(default_factory=dict)
    cooldowns: dict[str, int] = Field(default_factory=dict)
    failed_contacts: dict[str, str] = Field(default_factory=dict)
    failed_replies: dict[str, str] = Field(default_factory=dict)
    recent_blockers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    next_notice: Positive = 1
    transfers: dict[str, TransferAuthorization] = Field(default_factory=dict)
    logistics_knowledge: dict[str, dict[str, LogisticsSnapshot]] = Field(default_factory=dict)
    logistics_messages: list[LogisticsMessage] = Field(default_factory=list)
    logistics_sent: dict[str, str] = Field(default_factory=dict)


class Channel(CommunicationModel):
    channel: Literal["board", "inbox", "conversation", "tin_can", "remote"] = "board"
    inbox_id: Text | None = None
    listener: Text | None = None


class PostNotice(Channel):
    notice: NoticeTerms


class ReadNotices(CommunicationModel):
    notice_ids: list[Text] = Field(default_factory=list, max_length=12)


class ReadInbox(ReadNotices):
    inbox_id: Text


class ChangeNotice(Channel):
    notice_id: Text
    expected_version: Positive


class UpdateNotice(ChangeNotice):
    notice: NoticeTerms | None = None


class ReplyNotice(ChangeNotice):
    response: Literal["accept", "reject", "propose"]
    reason: Text | None = None
    proposed_deadline: Number | None = None
    proposed_fresh_until: Number | None = None


class TellNotice(CommunicationModel):
    listener: Text
    notice_id: Text
    known_version: Positive


class CloseNotice(ChangeNotice):
    reason: Text
    status: Literal["cancelled", "superseded"] = "cancelled"


class SupplyIntent(CommunicationModel):
    good: Text
    quantity: Positive
    purpose: Text
    deadline: Number
    required_fresh_until: Number
    preferred_supplier_id: Text
    destination_id: Text | None = None


class MeetingIntent(CommunicationModel):
    project_id: Literal["dig_well", "repair_mill"]
    start: Number
    end: Number | None = None
    purpose: Text


class CrewIntent(CommunicationModel):
    project: Literal["dig_well", "repair_mill"] | None = None
    earliest_start: Number | None = None
    participant_ids: list[Text] | None = None
    project_id: Literal["dig_well", "repair_mill"] | None = None
    start: Number | None = None
    end: Number | None = None
    purpose: Text = "Gather the crew and project supplies at the work site"


class RespondIntent(CommunicationModel):
    request_id: Text
    response: Literal["accept", "reject", "propose"]
    reason: Text | None = None
    proposed_deadline: Number | None = None
    proposed_fresh_until: Number | None = None


class UpdateIntent(CommunicationModel):
    request_id: Text
    changes: dict[str, Any]


class CheckMessagesIntent(CommunicationModel):
    channel: Literal["board", "inbox"] = "board"


class ShareIntent(CommunicationModel):
    listener_id: Text
    notice_id: Text


class CommitmentView(CommunicationModel):
    status: str | None
    delivered: Number
    paid: Number
    supplier: str
    buyer: str


class CommunicationNoticeView(CommunicationModel):
    id: str
    supersedes_notice_id: Text | None = None
    version: Positive
    author: str
    recipients: list[str]
    recipient_ids: list[str] = Field(default_factory=list)
    subject: str
    description: str
    type: NoticeType
    status: Status
    quantity: Positive | None
    unit_price: Positive | None = None
    delivery_fee: Number | None = None
    destination: str | None
    destination_id: str | None = None
    due_at: Number | None
    updated_at: Number
    private: bool
    response: str | None
    source: str
    learned_at: Number | None
    last_verified_at: Number | None = None
    confidence: str | None
    hops: Number
    source_chain: list[str]
    freshness: str
    appointment: Appointment | None
    commitment: CommitmentView | None


class InboxView(CommunicationModel):
    id: str
    owner: str
    place: str
    here: bool
    can_read: bool
    notes: Number | None = None


class ShareCandidate(CommunicationModel):
    notice_id: str
    known_version: Positive
    subject: str
    learned_at: Number
    freshness: str


class ListenerCandidates(CommunicationModel):
    listener: str
    listener_name: str
    candidates: list[ShareCandidate]


class CommunicationNetwork(CommunicationModel):
    name: str
    hub: str
    stations: list[str]
    lines: Number


class CommunicationView(CommunicationModel):
    enabled: bool
    scope: str
    policy: str
    network: CommunicationNetwork | None = None
    notices: list[CommunicationNoticeView]
    boxes: list[InboxView]
    share_candidates: list[ListenerCandidates]

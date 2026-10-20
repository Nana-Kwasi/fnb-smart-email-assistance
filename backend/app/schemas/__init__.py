"""Request and response shapes for the API. Every input has length limits."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
Example = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


# -- auth and users ----------------------------------------------------------------------------


class LoginIn(BaseModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class UserOut(ORM):
    id: uuid.UUID
    username: str
    display_name: str
    email: str | None
    role: str


class MeUserOut(UserOut):
    previous_login_at: datetime | None


class FeaturesOut(BaseModel):
    customer_data_answers: bool
    max_data_sources: int | None  # None = no limit
    require_second_person: bool
    max_upload_mb: int


class MeOut(BaseModel):
    user: MeUserOut
    permissions: list[str]
    csrf_token: str
    features: FeaturesOut


class UserAdminOut(UserOut):
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class UserUpdateIn(BaseModel):
    role: Literal["officer", "supervisor", "auditor", "admin"] | None = None
    is_active: bool | None = None


# -- data sources ------------------------------------------------------------------------------


class DataSourceIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_ -]{2,64}$")]
    kind: Literal["oracle", "sqlserver", "postgresql", "mysql", "sqlite"]
    host: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] = ""
    port: int | None = Field(default=None, ge=1, le=65535)
    database: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] = ""
    db_schema: Annotated[str, StringConstraints(strip_whitespace=True, max_length=128)] | None = None
    username: Annotated[str, StringConstraints(strip_whitespace=True, max_length=128)] = ""
    # Leave empty when editing to keep the saved password.
    password: Annotated[str, StringConstraints(max_length=512)] | None = None
    is_active: bool = True


class DataSourceOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    host: str
    port: int | None
    database: str
    db_schema: str | None
    username: str
    has_password: bool
    is_active: bool
    last_check_ok: bool | None
    last_check_at: datetime | None
    last_error: str | None
    updated_at: datetime


class DataSourceNameOut(BaseModel):
    name: str
    kind: str


class ColumnOut(BaseModel):
    name: str
    type: str


# -- topics ------------------------------------------------------------------------------------


class ConditionIn(BaseModel):
    column: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    source: Literal["fixed", "sender_email", "email_value"]
    value: Annotated[str, StringConstraints(max_length=500)] | None = None
    value_kind: Literal["account_number", "phone_number", "card_last4", "custom"] | None = None
    pattern: Annotated[str, StringConstraints(max_length=300)] | None = None
    label: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None


class OrderIn(BaseModel):
    column: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    direction: Literal["asc", "desc"] = "desc"


class BuilderIn(BaseModel):
    table: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    columns: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]] = (
        Field(min_length=1, max_length=30)
    )
    conditions: list[ConditionIn] = Field(default_factory=list, max_length=5)
    order_by: OrderIn | None = None
    many: bool = False  # reply with several records (a list) instead of one


class TopicIn(BaseModel):
    title: ShortText
    examples: list[Example] = Field(min_length=1, max_length=50)
    data_source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    builder: BuilderIn | None = None
    query: Annotated[str, StringConstraints(strip_whitespace=True, max_length=8_000)] | None = None
    extractors: dict[
        Annotated[str, StringConstraints(max_length=41)],
        Annotated[str, StringConstraints(min_length=1, max_length=500)],
    ] = Field(default_factory=dict, max_length=10)
    max_rows: int = Field(default=10, ge=1, le=100)
    reply_template: LongText
    row_template: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2_000)] | None = None
    require_verified_sender: bool = True


class SampleEmailIn(BaseModel):
    subject: Annotated[str, StringConstraints(max_length=998)] = ""
    body: Annotated[str, StringConstraints(max_length=20_000)] = ""
    sender_email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=320)] = ""
    sender_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] = ""
    verified: bool = True


class TryTopicIn(BaseModel):
    topic: TopicIn
    sample: SampleEmailIn


class TryTopicOut(BaseModel):
    text: str | None
    reason: str


class ReviewIn(BaseModel):
    note: Note = ""


class ActiveIn(BaseModel):
    active: bool


class RevisionOut(ORM):
    id: uuid.UUID
    title: str
    examples: list[str]
    data_source: str
    builder: dict[str, Any] | None
    query: str | None
    extractors: dict[str, str]
    max_rows: int
    reply_template: str
    row_template: str | None
    require_verified_sender: bool
    status: str
    submitted_by: UserOut
    submitted_at: datetime
    reviewed_by: UserOut | None
    reviewed_at: datetime | None
    review_note: str | None


class TopicSummaryOut(BaseModel):
    id: uuid.UUID
    title: str
    data_source: str
    is_active: bool
    is_live: bool
    has_pending: bool
    example_count: int
    updated_at: datetime
    answered_7d: int = 0


class TopicDetailOut(BaseModel):
    id: uuid.UUID
    is_active: bool
    live: RevisionOut | None
    pending: RevisionOut | None
    history: list[RevisionOut]


# -- emails ------------------------------------------------------------------------------------


class ReplyOut(ORM):
    id: uuid.UUID
    kind: str
    body_text: str
    succeeded: bool
    error: str | None
    created_at: datetime
    sent_by: UserOut | None = None
    draft_id: uuid.UUID | None = None


class EmailSummaryOut(ORM):
    id: uuid.UUID
    reference: str
    from_address: str
    from_name: str
    subject: str
    received_at: datetime
    status: str
    status_reason: str | None
    reason_code: str | None
    priority: str
    ack_status: str
    match_score: float | None
    is_follow_up: bool
    assigned_to: UserOut | None
    urgent_alert_at: datetime | None
    overdue_alert_at: datetime | None
    handled_via: str | None
    handled_at: datetime | None
    category: str | None = None
    urgency: str | None = None
    language: str | None = None
    department: str | None = None
    case_id: uuid.UUID | None = None


class SuggestedTopicOut(BaseModel):
    topic_id: uuid.UUID
    title: str


class KnowledgeUsedOut(BaseModel):
    """The knowledge base answer that was sent, or that is suggested to staff."""

    title: str
    content: str
    document_id: uuid.UUID
    document: str
    category: str
    version: str


class AttachmentOut(ORM):
    id: uuid.UUID
    name: str
    content_type: str
    size_bytes: int
    scan_status: str
    scan_detail: str | None
    extracted_text: str | None
    problems: list[str]


class DraftOut(ORM):
    id: uuid.UUID
    status: str
    body_text: str
    edited_text: str | None
    citations: list[dict]
    validation: list[dict]
    passed: bool
    model: str
    policy_version: int
    created_at: datetime
    submitted_by: UserOut | None
    submitted_at: datetime | None
    decided_by: UserOut | None
    decided_at: datetime | None
    reject_reason: str | None


class CaseBriefOut(BaseModel):
    id: uuid.UUID
    reference: str
    status: str
    emails: int
    reopened_count: int
    sla_due_at: datetime | None
    possible_duplicate_of: str | None


class EmailDetailOut(EmailSummaryOut):
    body_text: str
    web_link: str | None
    sender_verified: bool
    handled_by: UserOut | None
    handled_note: str | None
    replies: list[ReplyOut]
    suggested: SuggestedTopicOut | None = None
    knowledge: KnowledgeUsedOut | None = None
    intent: str | None = None
    risk: str | None = None
    sentiment: str | None = None
    ai_summary: str | None = None
    classification: dict | None = None
    classified_by: str | None = None
    decision: dict | None = None
    attachments: list[AttachmentOut] = []
    drafts: list[DraftOut] = []
    case: CaseBriefOut | None = None
    # Who may send: "officer", or "supervisor" when the category needs a supervisor's approval.
    approval: str = "officer"


RequestId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,64}$")]


class SendIn(BaseModel):
    text: LongText
    # Chosen by the browser for this one send; repeating it never sends twice.
    request_id: RequestId
    draft_id: uuid.UUID | None = None


class SubmitIn(BaseModel):
    text: LongText
    draft_id: uuid.UUID | None = None


class RejectIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class SuggestionOut(BaseModel):
    """The database answer a person can copy into their Outlook reply."""

    text: str | None
    reason: str


class AssignIn(BaseModel):
    user_id: uuid.UUID | None


class ResolveIn(BaseModel):
    outcome: Literal["replied", "closed"]
    note: Note = ""


class ReasonCount(BaseModel):
    code: str
    label: str
    count: int


class AttentionOut(BaseModel):
    page: Page[EmailSummaryOut]
    reasons: list[ReasonCount]
    overdue: int
    overdue_after_hours: int


# -- test area ---------------------------------------------------------------------------------


class TestMatchIn(SampleEmailIn):
    # Reading real data is allowed only for staff with the test:run-queries permission.
    run_query: bool = False


class ScoredOut(BaseModel):
    topic_id: uuid.UUID
    title: str
    score: float
    best_example: str
    best_sentence: str


class TestMatchOut(BaseModel):
    would_auto_answer: bool
    decision: str
    sensitive_keywords: list[str]
    matched: ScoredOut | None
    ranking: list[ScoredOut]
    acknowledgement_preview: str
    answer_preview: str | None
    answer_reason: str | None


# -- settings ----------------------------------------------------------------------------------

DigestTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class CategoryIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)]
    auto: bool


class SettingsOut(ORM):
    ack_enabled: bool
    auto_reply_enabled: bool
    confidence_threshold: float
    ack_template: str
    answer_header: str
    answer_footer: str
    sensitive_keywords: list[str]
    suggest_threshold: float
    knowledge_categories: list[dict[str, Any]]
    escalation_to: str | None
    escalation_copy_admins: bool
    max_upload_mb: int
    require_second_person: bool
    max_auto_answer_chars: int
    follow_up_days: int
    page_size: int
    session_idle_minutes: int
    retention_months: int
    notify_recipients: list[str]
    notify_urgent: bool
    notify_overdue: bool
    overdue_after_hours: int
    notify_digest: bool
    digest_time: str
    notify_system: bool
    updated_at: datetime
    mailbox: str
    graph_configured: bool
    internal_domains: list[str]


class SettingsIn(BaseModel):
    ack_enabled: bool
    auto_reply_enabled: bool
    confidence_threshold: float = Field(ge=0.3, le=0.99)
    ack_template: LongText
    answer_header: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2_000)]
    answer_footer: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2_000)]
    sensitive_keywords: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)]
    ] = Field(max_length=200)
    suggest_threshold: float = Field(default=0.4, ge=0.2, le=0.99)
    knowledge_categories: list[CategoryIn] | None = Field(default=None, max_length=40)  # None = keep
    escalation_to: Annotated[str, StringConstraints(strip_whitespace=True, max_length=320)] | None = None
    escalation_copy_admins: bool = False
    # Optional so an older screen that leaves them out keeps the saved values.
    max_upload_mb: int | None = Field(default=None, ge=1, le=50)
    require_second_person: bool | None = None
    max_auto_answer_chars: int | None = Field(default=None, ge=200, le=10_000)
    follow_up_days: int | None = Field(default=None, ge=0, le=30)
    page_size: int | None = Field(default=None, ge=5, le=100)
    session_idle_minutes: int | None = Field(default=None, ge=5, le=480)
    retention_months: int | None = Field(default=None, ge=0, le=120)
    notify_recipients: list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=320)]] = Field(
        default_factory=list, max_length=30
    )
    notify_urgent: bool = True
    notify_overdue: bool = True
    overdue_after_hours: int = Field(default=4, ge=1, le=72)
    notify_digest: bool = True
    digest_time: DigestTime = "16:00"
    notify_system: bool = True


# -- overview, search, audit -------------------------------------------------------------------


class Kpi(BaseModel):
    value: float | None
    previous: float | None  # the same measure for the previous period, for the trend arrow
    spark: list[float]  # last 7 days, oldest first


class TopicCount(BaseModel):
    title: str
    count: int


class HealthItem(BaseModel):
    name: str
    ok: bool | None
    detail: str


class RecentEmail(BaseModel):
    id: uuid.UUID
    reference: str
    from_name: str
    subject: str
    received_at: datetime
    status: str
    priority: str


class OverviewOut(BaseModel):
    briefing: list[str]
    received: Kpi
    auto_rate: Kpi
    waiting: Kpi
    avg_ack_seconds: Kpi
    waiting_high_priority: int
    overdue: int
    overdue_after_hours: int
    trend: list[dict]  # one entry per day: date, received, auto, people
    reasons_7d: list[ReasonCount]
    top_topics_7d: list[TopicCount]
    topic_gaps: list[TopicCount]
    recent: list[RecentEmail]
    health: list[HealthItem]
    pending_approvals: int
    auto_reply_enabled: bool
    operations: dict | None = None
    automation_mode: str | None = None
    kill_switch: bool = False


class SearchHit(BaseModel):
    kind: Literal["email", "document", "topic", "user", "page"]
    id: str
    title: str
    subtitle: str
    url: str


class SearchOut(BaseModel):
    hits: list[SearchHit]


class AuditOut(ORM):
    id: uuid.UUID
    at: datetime
    actor_username: str
    action: str
    entity_type: str | None
    entity_id: str | None
    detail: dict
    ip_address: str | None


class NotificationOut(ORM):
    id: uuid.UUID
    kind: str
    subject: str
    recipients: list[str]
    email_count: int
    succeeded: bool
    error: str | None
    sent_at: datetime

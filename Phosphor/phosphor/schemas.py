"""Pydantic request/response models for the JSON API."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Auth ────────────────────────────────────────────────────────────────────
class SetupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10)
    display_name: str = ""


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: int
    email: str
    display_name: str
    is_admin: bool
    created_at: dt.datetime
    last_login_at: dt.datetime | None = None


# ── Domains ─────────────────────────────────────────────────────────────────
class DomainCreate(BaseModel):
    name: str
    dkim_selector: str = "phosphor"


class DomainOut(ORMModel):
    id: int
    name: str
    active: bool
    dkim_selector: str
    dkim_txt_value: str
    catch_all_mailbox_id: int | None
    dns_status: dict
    dns_last_checked_at: dt.datetime | None
    created_at: dt.datetime


class DNSRecord(BaseModel):
    kind: str
    host: str
    type: str
    value: str
    priority: int | None = None
    required: bool = True
    ok: bool | None = None
    detail: str = ""


class DomainDNS(BaseModel):
    domain: str
    records: list[DNSRecord]


# ── Mailboxes / aliases ─────────────────────────────────────────────────────
class MailboxCreate(BaseModel):
    domain_id: int
    local_part: str
    password: str = Field(min_length=8)
    display_name: str = ""
    quota_mb: int = 2048


class MailboxOut(ORMModel):
    id: int
    domain_id: int
    address: str
    local_part: str
    display_name: str
    quota_bytes: int
    active: bool
    created_at: dt.datetime


class AliasCreate(BaseModel):
    domain_id: int
    source: str
    destination: EmailStr


class AliasOut(ORMModel):
    id: int
    domain_id: int
    source: str
    destination: str
    active: bool


# ── Webmail ─────────────────────────────────────────────────────────────────
class MessageOut(ORMModel):
    id: int
    folder: str
    message_id: str
    from_addr: str
    from_name: str
    to_addrs: list
    cc_addrs: list
    subject: str
    snippet: str
    size_bytes: int
    is_read: bool
    is_flagged: bool
    spam_score: float
    received_at: dt.datetime


class MessageDetail(MessageOut):
    body_html: str = ""
    body_text: str = ""
    headers: dict = {}
    attachments: list = []


class ComposeRequest(BaseModel):
    from_mailbox_id: int
    to: list[EmailStr]
    cc: list[EmailStr] = []
    bcc: list[EmailStr] = []
    subject: str = ""
    body_text: str = ""
    body_html: str = ""
    in_reply_to: str = ""
    references: str = ""


# ── Contacts ────────────────────────────────────────────────────────────────
class ContactCreate(BaseModel):
    email: EmailStr
    first_name: str = ""
    last_name: str = ""
    company: str = ""
    title: str = ""
    phone: str = ""
    website: str = ""
    custom: dict = {}
    notes: str = ""


class ContactUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    title: str | None = None
    phone: str | None = None
    website: str | None = None
    custom: dict | None = None
    status: str | None = None
    notes: str | None = None


class ContactOut(ORMModel):
    id: int
    email: str
    first_name: str
    last_name: str
    company: str
    title: str
    phone: str
    website: str
    custom: dict
    status: str
    source: str
    notes: str
    created_at: dt.datetime


class ContactListCreate(BaseModel):
    name: str
    description: str = ""


class ContactListOut(ORMModel):
    id: int
    name: str
    description: str
    created_at: dt.datetime
    member_count: int = 0


class ImportResult(BaseModel):
    created: int
    updated: int
    skipped: int
    errors: list[str]
    list_id: int | None = None


# ── Templates ───────────────────────────────────────────────────────────────
class TemplateCreate(BaseModel):
    name: str
    subject: str = ""
    body_html: str = ""
    body_text: str = ""


class TemplateOut(ORMModel):
    id: int
    name: str
    subject: str
    body_html: str
    body_text: str
    created_at: dt.datetime
    updated_at: dt.datetime


class RenderPreviewRequest(BaseModel):
    subject: str = ""
    body_html: str = ""
    body_text: str = ""
    contact_id: int | None = None
    sample: dict = {}


class RenderPreviewResponse(BaseModel):
    subject: str
    body_html: str
    body_text: str
    missing_tags: list[str]


# ── Campaigns ───────────────────────────────────────────────────────────────
class SequenceStepIn(BaseModel):
    step_order: int
    template_id: int | None = None
    subject: str = ""
    body_html: str = ""
    body_text: str = ""
    wait_days: int = 0
    condition: str = "if_no_reply"
    same_thread: bool = True


class SequenceStepOut(SequenceStepIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    campaign_id: int


class CampaignCreate(BaseModel):
    name: str
    from_mailbox_id: int | None = None
    reply_to: str = ""
    list_id: int | None = None
    timezone: str = "UTC"
    send_days: list[int] = [0, 1, 2, 3, 4]
    window_start_hour: int = 9
    window_end_hour: int = 17
    daily_cap: int = 40
    min_delay_seconds: int = 90
    max_delay_seconds: int = 300
    track_opens: bool = True
    track_clicks: bool = True
    stop_on_reply: bool = True
    start_at: dt.datetime | None = None


class CampaignUpdate(BaseModel):
    name: str | None = None
    from_mailbox_id: int | None = None
    reply_to: str | None = None
    list_id: int | None = None
    timezone: str | None = None
    send_days: list[int] | None = None
    window_start_hour: int | None = None
    window_end_hour: int | None = None
    daily_cap: int | None = None
    min_delay_seconds: int | None = None
    max_delay_seconds: int | None = None
    track_opens: bool | None = None
    track_clicks: bool | None = None
    stop_on_reply: bool | None = None
    start_at: dt.datetime | None = None


class CampaignOut(ORMModel):
    id: int
    name: str
    status: str
    from_mailbox_id: int | None
    reply_to: str
    list_id: int | None
    timezone: str
    send_days: list
    window_start_hour: int
    window_end_hour: int
    daily_cap: int
    min_delay_seconds: int
    max_delay_seconds: int
    track_opens: bool
    track_clicks: bool
    stop_on_reply: bool
    start_at: dt.datetime | None
    created_at: dt.datetime
    steps: list[SequenceStepOut] = []


class CampaignStats(BaseModel):
    campaign_id: int
    recipients_total: int
    recipients_active: int
    recipients_completed: int
    sent: int
    delivered: int
    opens: int
    unique_opens: int
    clicks: int
    unique_clicks: int
    replies: int
    bounces: int
    unsubscribes: int
    open_rate: float
    click_rate: float
    reply_rate: float
    bounce_rate: float


# ── Suppression ─────────────────────────────────────────────────────────────
class SuppressionCreate(BaseModel):
    email: EmailStr
    reason: str = "manual"
    note: str = ""


class SuppressionOut(ORMModel):
    id: int
    email: str
    reason: str
    note: str
    created_at: dt.datetime


# ── Dashboard ───────────────────────────────────────────────────────────────
class DashboardStats(BaseModel):
    domains: int
    mailboxes: int
    contacts: int
    campaigns_running: int
    outbound_queued: int
    outbound_sent_24h: int
    outbound_failed_24h: int
    inbound_24h: int
    replies_7d: int
    suppressed: int

"""ORM models. Message *bodies* live on disk as Maildir / spool files; these
tables hold metadata, routing, campaign state and analytics."""
from __future__ import annotations

import datetime as dt
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
#  Accounts / auth
# ─────────────────────────────────────────────────────────────────────────────
class User(Base):
    """An operator of the Phosphor control panel."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Mail domains & mailboxes
# ─────────────────────────────────────────────────────────────────────────────
class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    dkim_selector: Mapped[str] = mapped_column(String(63), default="phosphor")
    dkim_private_key: Mapped[str] = mapped_column(Text, default="")   # PEM (PKCS8)
    dkim_public_key: Mapped[str] = mapped_column(Text, default="")    # base64 DER, for the TXT record
    dkim_txt_value: Mapped[str] = mapped_column(Text, default="")

    catch_all_mailbox_id: Mapped[int | None] = mapped_column(
        ForeignKey("mailboxes.id", ondelete="SET NULL"), nullable=True
    )
    dns_last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dns_status: Mapped[dict] = mapped_column(JSON, default=dict)   # {"mx": true, "spf": false, ...}

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    mailboxes: Mapped[list["Mailbox"]] = relationship(
        back_populates="domain",
        cascade="all, delete-orphan",
        foreign_keys="Mailbox.domain_id",
    )
    aliases: Mapped[list["Alias"]] = relationship(back_populates="domain", cascade="all, delete-orphan")


class Mailbox(Base):
    __tablename__ = "mailboxes"
    __table_args__ = (UniqueConstraint("domain_id", "local_part", name="uq_mailbox_addr"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)
    local_part: Mapped[str] = mapped_column(String(64), index=True)
    address: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255), default="")  # for SMTP submission / future IMAP
    quota_bytes: Mapped[int] = mapped_column(Integer, default=2 * 1024 * 1024 * 1024)
    maildir_path: Mapped[str] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    domain: Mapped[Domain] = relationship(back_populates="mailboxes", foreign_keys=[domain_id])
    messages: Mapped[list["Message"]] = relationship(back_populates="mailbox", cascade="all, delete-orphan")


class Alias(Base):
    """Forward mail for `source` to one or more `destination` addresses."""

    __tablename__ = "aliases"
    __table_args__ = (UniqueConstraint("source", "destination", name="uq_alias_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(320), index=True)
    destination: Mapped[str] = mapped_column(String(320))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    domain: Mapped[Domain] = relationship(back_populates="aliases")


# ─────────────────────────────────────────────────────────────────────────────
#  Stored mail (webmail view)
# ─────────────────────────────────────────────────────────────────────────────
class Folder(str, Enum):
    INBOX = "INBOX"
    SENT = "Sent"
    ARCHIVE = "Archive"
    TRASH = "Trash"
    SPAM = "Spam"
    DRAFTS = "Drafts"


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("mailbox_id", "maildir_key", name="uq_msg_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("mailboxes.id", ondelete="CASCADE"), index=True)
    maildir_key: Mapped[str] = mapped_column(String(255))   # mailbox.Maildir key
    folder: Mapped[str] = mapped_column(String(32), default=Folder.INBOX.value, index=True)

    message_id: Mapped[str] = mapped_column(String(998), default="", index=True)
    in_reply_to: Mapped[str] = mapped_column(String(998), default="", index=True)
    references: Mapped[str] = mapped_column(Text, default="")
    from_addr: Mapped[str] = mapped_column(String(320), default="", index=True)
    from_name: Mapped[str] = mapped_column(String(255), default="")
    to_addrs: Mapped[list] = mapped_column(JSON, default=list)
    cc_addrs: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(998), default="")
    snippet: Mapped[str] = mapped_column(String(280), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    spam_score: Mapped[float] = mapped_column(Float, default=0.0)

    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    mailbox: Mapped[Mailbox] = relationship(back_populates="messages")


# ─────────────────────────────────────────────────────────────────────────────
#  Outbound queue
# ─────────────────────────────────────────────────────────────────────────────
class OutboundStatus(str, Enum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    DEFERRED = "deferred"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    envelope_from: Mapped[str] = mapped_column(String(320), index=True)
    envelope_to: Mapped[str] = mapped_column(String(320), index=True)
    raw_path: Mapped[str] = mapped_column(String(500))   # spool file (already MIME-built, unsigned)
    message_id: Mapped[str] = mapped_column(String(998), default="", index=True)
    subject: Mapped[str] = mapped_column(String(998), default="")

    status: Mapped[str] = mapped_column(String(16), default=OutboundStatus.QUEUED.value, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    next_attempt_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    # Optional campaign linkage
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True)
    sequence_step_id: Mapped[int | None] = mapped_column(ForeignKey("sequence_steps.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Contacts / lists
# ─────────────────────────────────────────────────────────────────────────────
class ContactStatus(str, Enum):
    ACTIVE = "active"
    UNSUBSCRIBED = "unsubscribed"
    BOUNCED = "bounced"
    REPLIED = "replied"
    COMPLAINED = "complained"


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(120), default="")
    last_name: Mapped[str] = mapped_column(String(120), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    website: Mapped[str] = mapped_column(String(255), default="")
    custom: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=ContactStatus.ACTIVE.value, index=True)
    source: Mapped[str] = mapped_column(String(120), default="manual")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list["ContactListMembership"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )


class ContactList(Base):
    __tablename__ = "contact_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list["ContactListMembership"]] = relationship(
        back_populates="contact_list", cascade="all, delete-orphan"
    )


class ContactListMembership(Base):
    __tablename__ = "contact_list_memberships"
    __table_args__ = (UniqueConstraint("list_id", "contact_id", name="uq_list_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("contact_lists.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    contact_list: Mapped[ContactList] = relationship(back_populates="memberships")
    contact: Mapped[Contact] = relationship(back_populates="memberships")


# ─────────────────────────────────────────────────────────────────────────────
#  Templates
# ─────────────────────────────────────────────────────────────────────────────
class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    subject: Mapped[str] = mapped_column(String(500), default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ─────────────────────────────────────────────────────────────────────────────
#  Campaigns & sequences
# ─────────────────────────────────────────────────────────────────────────────
class CampaignStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(16), default=CampaignStatus.DRAFT.value, index=True)

    from_mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailboxes.id", ondelete="SET NULL"), nullable=True)
    reply_to: Mapped[str] = mapped_column(String(320), default="")
    list_id: Mapped[int | None] = mapped_column(ForeignKey("contact_lists.id", ondelete="SET NULL"), nullable=True)

    # scheduling / throttle knobs
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    send_days: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4])  # Mon=0
    window_start_hour: Mapped[int] = mapped_column(Integer, default=9)
    window_end_hour: Mapped[int] = mapped_column(Integer, default=17)
    daily_cap: Mapped[int] = mapped_column(Integer, default=40)
    min_delay_seconds: Mapped[int] = mapped_column(Integer, default=90)
    max_delay_seconds: Mapped[int] = mapped_column(Integer, default=300)
    start_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    track_opens: Mapped[bool] = mapped_column(Boolean, default=True)
    track_clicks: Mapped[bool] = mapped_column(Boolean, default=True)
    stop_on_reply: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    steps: Mapped[list["SequenceStep"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="SequenceStep.step_order"
    )
    recipients: Mapped[list["CampaignRecipient"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class StepCondition(str, Enum):
    ALWAYS = "always"
    IF_NO_REPLY = "if_no_reply"
    IF_NO_OPEN = "if_no_open"


class SequenceStep(Base):
    __tablename__ = "sequence_steps"
    __table_args__ = (UniqueConstraint("campaign_id", "step_order", name="uq_step_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    step_order: Mapped[int] = mapped_column(Integer, default=1)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("templates.id", ondelete="SET NULL"), nullable=True)

    # Inline copy (used when template_id is null, or to override)
    subject: Mapped[str] = mapped_column(String(500), default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")

    wait_days: Mapped[int] = mapped_column(Integer, default=0)     # delay AFTER the previous step
    condition: Mapped[str] = mapped_column(String(16), default=StepCondition.IF_NO_REPLY.value)
    same_thread: Mapped[bool] = mapped_column(Boolean, default=True)  # reply into the step-1 thread

    campaign: Mapped[Campaign] = relationship(back_populates="steps")


class RecipientStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    STOPPED_REPLIED = "stopped_replied"
    STOPPED_BOUNCED = "stopped_bounced"
    STOPPED_UNSUBSCRIBED = "stopped_unsubscribed"
    STOPPED_ERROR = "stopped_error"


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (UniqueConstraint("campaign_id", "contact_id", name="uq_campaign_contact"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)

    status: Mapped[str] = mapped_column(String(24), default=RecipientStatus.PENDING.value, index=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)   # 0 = nothing sent yet
    next_action_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    thread_message_id: Mapped[str] = mapped_column(String(998), default="")  # Message-ID of step 1
    thread_subject: Mapped[str] = mapped_column(String(998), default="")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    campaign: Mapped[Campaign] = relationship(back_populates="recipients")
    contact: Mapped[Contact] = relationship()


# ─────────────────────────────────────────────────────────────────────────────
#  Analytics events & suppression
# ─────────────────────────────────────────────────────────────────────────────
class EventType(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    OPEN = "open"
    CLICK = "click"
    REPLY = "reply"
    BOUNCE = "bounce"
    UNSUBSCRIBE = "unsubscribe"
    COMPLAINT = "complaint"
    FAILED = "failed"


class EmailEvent(Base):
    __tablename__ = "email_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(16), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True, index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True, index=True)
    outbound_message_id: Mapped[int | None] = mapped_column(ForeignKey("outbound_messages.id", ondelete="SET NULL"), nullable=True)
    step_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str] = mapped_column(Text, default="")
    user_agent: Mapped[str] = mapped_column(String(500), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Suppression(Base):
    """Global do-not-contact list. Checked before every campaign send."""

    __tablename__ = "suppressions"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(32), default="manual")   # unsubscribe|bounce|complaint|manual
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrackingToken(Base):
    """Opaque token embedded in an open pixel or wrapped link."""

    __tablename__ = "tracking_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(8))   # "open" | "click"
    target_url: Mapped[str] = mapped_column(Text, default="")
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True, index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True, index=True)
    outbound_message_id: Mapped[int | None] = mapped_column(ForeignKey("outbound_messages.id", ondelete="SET NULL"), nullable=True)
    step_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SettingKV(Base):
    """Small mutable key/value store for things the operator edits at runtime."""

    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

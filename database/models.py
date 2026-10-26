from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from .session import Base


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class ProviderConfiguration(Base):
    """A durable override marker: an empty managed pool stays empty."""
    __tablename__ = "provider_configurations"
    provider: Mapped[str] = mapped_column(String(24), primary_key=True)
    enabled: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    keyless_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(24), index=True)
    label: Mapped[str] = mapped_column(String(60))
    encrypted_key: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    masked_key: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[str] = mapped_column(String(16), default="admin")
    status: Mapped[str] = mapped_column(String(24), default="unchecked")
    detail: Mapped[str] = mapped_column(String(200), default="Quota pas encore vérifié.")
    quota_json: Mapped[str] = mapped_column(Text, default="{}")
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("provider", "fingerprint", name="uq_provider_credential_fingerprint"),)


class WebSession(Base):
    __tablename__ = "web_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256, never the cookie
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    web_account_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    pair_token: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, unique=True)
    pair_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    pair_command_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)


class WebCommand(Base):
    __tablename__ = "web_commands"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    profile_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    result: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True, index=True, nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    premium_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    filters: Mapped[list[Filter]] = relationship("Filter", back_populates="user", cascade="all, delete-orphan")


class WebAccount(Base):
    __tablename__ = "web_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, index=True)
    login: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class WebRequest(Base):
    __tablename__ = "web_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(80))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(180))
    action: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Filter(Base):
    __tablename__ = "filters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="default")
    price_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    surface_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rooms_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    property_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # studio|appartement|maison
    budget_max_with_charges: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    radius_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship("User", back_populates="filters")


class Listing(Base):
    __tablename__ = "listings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)  # leboncoin|seloger|pap
    external_id: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    search_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    surface_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_per_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    rooms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    images: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array; legacy comma-separated URLs remain readable
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    inventory_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True, index=True)
    canonical_signature: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    quality_flags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_city: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    normalized_postal_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    charges_included: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    duplicate_of_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    details_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    geo_precision: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    removed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_external"),
    )


class ListingReport(Base):
    __tablename__ = "listing_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("listings.id"), index=True)
    note: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", "listing_id", name="uq_report_user_listing"),)


class ListingRead(Base):
    __tablename__ = "listing_reads"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True, index=True)
    read_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ListingPublication(Base):
    """Every portal identity points to one property, including merged duplicates."""
    __tablename__ = "listing_publications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("listings.id"), index=True)
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(Text)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    availability_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    availability_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    availability_detail: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    absence_count: Mapped[int] = mapped_column(Integer, default=0)
    absence_first_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_publication_source_external"),)


class ListingPriceHistory(Base):
    __tablename__ = "listing_price_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("listings.id"), index=True)
    publication_id: Mapped[int] = mapped_column(Integer, ForeignKey("listing_publications.id"), index=True)
    price: Mapped[int] = mapped_column(Integer)
    previous_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    announced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MemberListingNote(Base):
    __tablename__ = "member_listing_notes"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("listings.id"), primary_key=True)
    note: Mapped[str] = mapped_column(Text, default="")
    search_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stage: Mapped[str] = mapped_column(String(24), default="to_contact")
    visit_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    reminder_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    commute_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    commute_destination: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    commute_mode: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    parking: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    accessibility: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Paris")
    quiet_start: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    quiet_end: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    daily_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    summary_time: Mapped[str] = mapped_column(String(5), default="08:00")
    price_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    last_summary_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    retry_after: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MemberNotification(Base):
    __tablename__ = "member_notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("listings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    event_key: Mapped[str] = mapped_column(String(100))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    not_before: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    telegram_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", "event_key", name="uq_member_notification_event"),)


class PerformanceSample(Base):
    __tablename__ = "performance_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), index=True)
    value_ms: Mapped[float] = mapped_column(Float)
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class SentAlert(Base):
    __tablename__ = "sent_alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    filter_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    filter_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    score_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    match_reasons: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "listing_id", name="uq_user_listing_sent"),
    )


class PendingAlert(Base):
    __tablename__ = "pending_alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    not_before: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|canceled
    filter_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    filter_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    score_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    match_reasons: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "listing_id", name="uq_user_listing_pending"),
    )


class Favorite(Base):
    __tablename__ = "favorites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("user_id", "listing_id", name="uq_user_listing_favorite"),
    )


class SourceRun(Base):
    __tablename__ = "source_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)  # success|partial|failed|skipped
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    locations_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_count: Mapped[int] = mapped_column(Integer, default=0)
    normalized_count: Mapped[int] = mapped_column(Integer, default=0)
    dropped_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    timeout_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    disabled_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    diagnostics_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

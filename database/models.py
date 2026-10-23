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


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    premium_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    filters: Mapped[list[Filter]] = relationship("Filter", back_populates="user", cascade="all, delete-orphan")


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
    price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    surface_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_per_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    rooms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    images: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # comma-separated URLs
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    canonical_signature: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    quality_flags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_city: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    normalized_postal_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    charges_included: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_external"),
    )


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

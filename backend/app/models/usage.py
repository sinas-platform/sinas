"""Durable usage snapshots for operations metering.

One row per (instance_id, period_id), upserted by the scheduler snapshot job
from the live Redis counters. Redis is the authoritative live counter; this
row is the crash-recovery record (Redis is re-seeded from it on startup) and
the source for the cumulative heartbeat pushed to the platform.
"""
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, updated_at, uuid_pk


class UsagePeriod(Base):
    __tablename__ = "usage_periods"

    id: Mapped[uuid_pk]
    instance_id: Mapped[str] = mapped_column(String(255), nullable=False)
    period_id: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "2026-08"
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    by_kind: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_op_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Incremented per push; part of the Idempotency-Key and the platform's
    # monotonicity backstop (a decreasing seq or total flags tampering).
    snapshot_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reported_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    __table_args__ = (
        Index("uq_usage_periods_instance_period", "instance_id", "period_id", unique=True),
    )

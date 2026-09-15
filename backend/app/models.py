from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Indicator(Base):
    __tablename__ = "indicators"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32))  # index / forex / commodity / macro
    unit: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(32), default="akshare")
    frequency: Mapped[str] = mapped_column(String(16), default="unknown")
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    region: Mapped[str] = mapped_column(String(16), default="GLOBAL")  # CN / US / JP / GLOBAL

    data_points: Mapped[list["DataPoint"]] = relationship(
        back_populates="indicator", cascade="all, delete-orphan"
    )
    data_point_vintages: Mapped[list["DataPointVintage"]] = relationship(
        back_populates="indicator", cascade="all, delete-orphan"
    )


class DataPoint(Base):
    __tablename__ = "data_points"
    __table_args__ = (UniqueConstraint("indicator_code", "date", name="uq_indicator_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    indicator_code: Mapped[str] = mapped_column(ForeignKey("indicators.code"))
    date: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Numeric(18, 6))
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="published")
    formula_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    indicator: Mapped["Indicator"] = relationship(back_populates="data_points")
    vintages: Mapped[list["DataPointVintage"]] = relationship(
        back_populates="data_point", cascade="all, delete-orphan", order_by="DataPointVintage.version"
    )


class DataPointVintage(Base):
    """Append-only values as they were known at each ingestion time.

    ``DataPoint`` remains the fast current-value table used by the existing API.
    A vintage row is appended only when the value or publication metadata changes,
    so the six-hour refresh job does not create duplicate snapshots.
    """

    __tablename__ = "data_point_vintages"
    __table_args__ = (
        UniqueConstraint("data_point_id", "version", name="uq_data_point_vintage_version"),
        Index(
            "ix_vintages_as_of_lookup",
            "indicator_code",
            "available_at",
            "date",
            "version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    data_point_id: Mapped[int] = mapped_column(
        ForeignKey("data_points.id", ondelete="CASCADE"), index=True
    )
    indicator_code: Mapped[str] = mapped_column(ForeignKey("indicators.code"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    value: Mapped[float] = mapped_column(Numeric(18, 6))
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="published")
    formula_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer)

    data_point: Mapped["DataPoint"] = relationship(back_populates="vintages")
    indicator: Mapped["Indicator"] = relationship(back_populates="data_point_vintages")


class ReleaseEvidence(Base):
    """Immutable official-release evidence, independent of the current snapshot.

    Unlike ``DataPointVintage``, rows in this table may be discovered and stored
    in any historical order.  ``version`` is therefore an append sequence for
    one indicator/observation pair; chronology is carried by ``available_at``.
    """

    __tablename__ = "release_evidence"
    __table_args__ = (
        UniqueConstraint("evidence_key", name="uq_release_evidence_key"),
        UniqueConstraint(
            "indicator_code",
            "date",
            "version",
            name="uq_release_evidence_observation_version",
        ),
        Index(
            "ix_release_evidence_as_of_lookup",
            "indicator_code",
            "available_at",
            "date",
            "version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    evidence_key: Mapped[str] = mapped_column(String(64), nullable=False)
    indicator_code: Mapped[str] = mapped_column(
        ForeignKey("indicators.code"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    release_date: Mapped[date] = mapped_column(Date, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="published", nullable=False)
    formula_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provenance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class RefreshRun(Base):
    """One manual, scheduled, or startup refresh attempt."""

    __tablename__ = "refresh_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_indicators: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    no_change_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    results: Mapped[list["RefreshResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RefreshResult.id"
    )


class RefreshResult(Base):
    """Per-indicator outcome, including validation failures that were not written."""

    __tablename__ = "refresh_results"
    __table_args__ = (
        UniqueConstraint("run_id", "indicator_code", name="uq_refresh_result_run_indicator"),
        Index("ix_refresh_results_indicator_finished", "indicator_code", "finished_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("refresh_runs.id", ondelete="CASCADE"), index=True
    )
    indicator_code: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_issues: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["RefreshRun"] = relationship(back_populates="results")

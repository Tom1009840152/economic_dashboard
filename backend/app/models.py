from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
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
    version: Mapped[int] = mapped_column(Integer)

    data_point: Mapped["DataPoint"] = relationship(back_populates="vintages")
    indicator: Mapped["Indicator"] = relationship(back_populates="data_point_vintages")

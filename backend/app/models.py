from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Indicator(Base):
    __tablename__ = "indicators"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32))  # index / forex / commodity / macro
    unit: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(32), default="akshare")
    sort_order: Mapped[int] = mapped_column(default=0)
    region: Mapped[str] = mapped_column(String(16), default="GLOBAL")  # CN / US / JP / GLOBAL

    data_points: Mapped[list["DataPoint"]] = relationship(
        back_populates="indicator", cascade="all, delete-orphan"
    )


class DataPoint(Base):
    __tablename__ = "data_points"
    __table_args__ = (UniqueConstraint("indicator_code", "date", name="uq_indicator_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    indicator_code: Mapped[str] = mapped_column(ForeignKey("indicators.code"))
    date: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Numeric(18, 6))

    indicator: Mapped["Indicator"] = relationship(back_populates="data_points")

from uuid import uuid4
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, CheckConstraint, Date, Float, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import expression
from sqlalchemy.types import DateTime
from database import Base

if TYPE_CHECKING:
    from models import CaseStep
    
class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('won','settled','lost','dismissed','ongoing')",
            name="ck_case_outcome",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    jurisdiction: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    legal_domain: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    case_type: Mapped[str] = mapped_column(String(100), nullable=True)
    situation_summary: Mapped[str] = mapped_column(Text, nullable=False)
    key_facts: Mapped[dict] = mapped_column(JSON, nullable=True, default=list)
    timeline: Mapped[dict] = mapped_column(JSON, nullable=True, default=list)
    steps_taken: Mapped[dict] = mapped_column(JSON, nullable=True, default=list)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome_summary: Mapped[str] = mapped_column(Text, nullable=True)
    outcome_date: Mapped[Date] = mapped_column(Date, nullable=True)
    settlement_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=True)
    anonymized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=expression.true(), default=True
    )
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=expression.false(), default=False
    )
    quality_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, server_default="0.5"
    )

    # Relationships
    steps: Mapped[list["CaseStep"]] = relationship(  # noqa: F821
        "CaseStep", back_populates="case", cascade="all, delete-orphan",
        order_by="CaseStep.step_number",
    )

    def __repr__(self) -> str:
        return (
            f"<Case id={self.id!r} jurisdiction={self.jurisdiction!r} "
            f"outcome={self.outcome!r} quality={self.quality_score!r}>"
        )
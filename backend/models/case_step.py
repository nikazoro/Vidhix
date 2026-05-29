from uuid import uuid4
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime
from database import Base

if TYPE_CHECKING:
        from models import Case
class CaseStep(Base):
    __tablename__ = "case_steps"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    case_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=True)
    deadline_days: Mapped[int] = mapped_column(Integer, nullable=True)
    was_effective: Mapped[bool] = mapped_column(Boolean, nullable=True)
    documents_used: Mapped[list] = mapped_column(JSON, nullable=True, default=list)

    # Relationships
    case: Mapped["Case"] = relationship(  # noqa: F821
        "Case", back_populates="steps"
    )

    def __repr__(self) -> str:
        return (
            f"<CaseStep id={self.id!r} case_id={self.case_id!r} "
            f"step_number={self.step_number!r}>"
        )
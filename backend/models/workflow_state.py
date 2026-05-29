from uuid import uuid4

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime
from typing import TYPE_CHECKING
from database import Base

if TYPE_CHECKING:
    from models import Session
class WorkflowState(Base):
    __tablename__ = "workflow_states"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    procedure_id: Mapped[str] = mapped_column(String(100), nullable=False)
    current_step_id: Mapped[str] = mapped_column(String(100), nullable=True)
    completed_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    declared_facts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Relationships
    session: Mapped["Session"] = relationship(  # noqa: F821
        "Session", back_populates="workflow_states"
    )

    def __repr__(self) -> str:
        return (
            f"<WorkflowState id={self.id!r} session_id={self.session_id!r} "
            f"procedure_id={self.procedure_id!r} current_step={self.current_step_id!r}>"
        )
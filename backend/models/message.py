from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import expression
from sqlalchemy.types import DateTime, String
from typing import TYPE_CHECKING
from database import Base
if TYPE_CHECKING:
    from models import Session, ResponseFeedback

class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name="ck_message_role"
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=True)
    escalation_triggered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=expression.false(), default=False
    )
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=True)

    # Relationships
    session: Mapped["Session"] = relationship(  # noqa: F821
        "Session", back_populates="messages"
    )
    feedbacks: Mapped[list["ResponseFeedback"]] = relationship(  # noqa: F821
        "ResponseFeedback", back_populates="message"
    )

    def __repr__(self) -> str:
        preview = self.content[:60].replace("\n", " ") if self.content else ""
        return (
            f"<Message id={self.id!r} role={self.role!r} "
            f"session_id={self.session_id!r} preview={preview!r}>"
        )
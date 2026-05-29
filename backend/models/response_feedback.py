from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime
from typing import TYPE_CHECKING
from database import Base

if TYPE_CHECKING:
    from models import Session, Message
    
class ResponseFeedback(Base):
    __tablename__ = "response_feedbacks"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_feedback_rating"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    message_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    feedback_type: Mapped[str] = mapped_column(String(50), nullable=True)
    contributed_case_ids: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    contributed_source_ids: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )

    # Relationships
    session: Mapped["Session"] = relationship(  # noqa: F821
        "Session", back_populates="feedbacks"
    )
    message: Mapped["Message"] = relationship(  # noqa: F821
        "Message", back_populates="feedbacks"
    )

    def __repr__(self) -> str:
        return (
            f"<ResponseFeedback id={self.id!r} rating={self.rating!r} "
            f"message_id={self.message_id!r}>"
        )
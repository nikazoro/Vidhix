from uuid import uuid4

from sqlalchemy import Boolean, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import expression
from sqlalchemy.types import DateTime
from typing import TYPE_CHECKING
from database import Base

if TYPE_CHECKING:
    from models import Message, Document, WorkflowState, ResponseFeedback
    
class Session(Base):
    __tablename__ = "sessions"

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
    jurisdiction: Mapped[str] = mapped_column(String(10), nullable=True)
    legal_domain: Mapped[str] = mapped_column(String(50), nullable=True)
    session_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=expression.true(), default=True
    )

    # Relationships
    messages: Mapped[list["Message"]] = relationship(  # noqa: F821
        "Message", back_populates="session", cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(  # noqa: F821
        "Document", back_populates="session"
    )
    workflow_states: Mapped[list["WorkflowState"]] = relationship(  # noqa: F821
        "WorkflowState", back_populates="session", cascade="all, delete-orphan"
    )
    feedbacks: Mapped[list["ResponseFeedback"]] = relationship(  # noqa: F821
        "ResponseFeedback", back_populates="session"
    )

    def __repr__(self) -> str:
        return (
            f"<Session id={self.id!r} jurisdiction={self.jurisdiction!r} "
            f"is_active={self.is_active!r}>"
        )
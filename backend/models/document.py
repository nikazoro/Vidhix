from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime
from typing import TYPE_CHECKING
from database import Base
if TYPE_CHECKING:
    from models import Session

class Document(Base):
    __tablename__ = "documents"

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
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ocr_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    extracted_text: Mapped[str] = mapped_column(Text, nullable=True)
    document_type: Mapped[str] = mapped_column(String(100), nullable=True)
    extracted_entities: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )   

    # Relationships
    session: Mapped["Session"] = relationship(  # noqa: F821
        "Session", back_populates="documents"
    )

    def __repr__(self) -> str:
        return (
            f"<Document id={self.id!r} filename={self.original_filename!r} "
            f"ocr_status={self.ocr_status!r}>"
        )
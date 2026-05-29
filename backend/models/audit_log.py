from sqlalchemy import BigInteger, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=True, index=True
    )
    audit_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id!r} event_type={self.event_type!r} "
            f"session_id={self.session_id!r}>"
        )
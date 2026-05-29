from uuid import uuid4

from sqlalchemy import Date, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from database import Base


class CorpusDocument(Base):
    __tablename__ = "corpus_documents"

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
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    legal_domain: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    document_type: Mapped[str] = mapped_column(String(100), nullable=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=True)
    source_authority: Mapped[str] = mapped_column(String(256), nullable=True)
    version_date: Mapped[Date] = mapped_column(Date, nullable=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=True, unique=True)
    ingestion_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=True, default=0)

    def __repr__(self) -> str:
        return (
            f"<CorpusDocument id={self.id!r} title={self.title!r} "
            f"jurisdiction={self.jurisdiction!r} status={self.ingestion_status!r}>"
        )
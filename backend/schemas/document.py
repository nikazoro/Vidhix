from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentUploadResponse(BaseModel):
    document_id: str
    status: str
    polling_url: str
    original_filename: str
    size_bytes: int


class ExtractedEntity(BaseModel):
    entity_type: str
    value: str
    context: str | None = None


class DocumentAnalysis(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    original_filename: str
    ocr_status: str
    document_type: str | None
    jurisdiction: str | None = None
    legal_domain: str | None = None
    key_dates: list[dict] = Field(default_factory=list)
    parties: list[str] = Field(default_factory=list)
    amounts: list[dict] = Field(default_factory=list)
    extracted_entities: dict = Field(default_factory=dict)
    summary: str | None = None
    created_at: datetime


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    mime_type: str
    size_bytes: int
    ocr_status: str
    document_type: str | None
    created_at: datetime
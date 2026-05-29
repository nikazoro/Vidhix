from pydantic import BaseModel, ConfigDict, Field


class FeedbackSubmit(BaseModel):
    message_id: str
    session_id: str | None = None
    rating: int = Field(..., ge=1, le=5)
    feedback_type: str | None = Field(None, max_length=50)
    notes: str | None = Field(None, max_length=1000)
    contributed_case_ids: list[str] = Field(default_factory=list)
    contributed_source_ids: list[str] = Field(default_factory=list)


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    message_id: str | None
    rating: int
    feedback_type: str | None
    success: bool = True
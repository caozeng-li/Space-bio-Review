"""An explicit question about a retained source image."""

from pydantic import BaseModel, Field


class VisualRequest(BaseModel):
    source_id: str
    visual_id: str
    question: str = Field(min_length=1)

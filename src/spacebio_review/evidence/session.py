from pydantic import BaseModel, Field

from spacebio_review.models.provider import ModelError


class SessionModel:
    def __init__(self, session):
        self.session = session
        self.config = session.model.config

    async def complete(self, *args, **kwargs):
        if self.session.claim_model_turn and not self.session.claim_model_turn():
            raise ModelError("inquiry_model_budget_reserved")
        return await self.session.model.complete(*args, **kwargs)


class VisualRequest(BaseModel):
    source_id: str
    visual_id: str
    question: str = Field(min_length=1)

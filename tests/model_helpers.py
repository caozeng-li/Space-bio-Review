import httpx
from openai import AsyncOpenAI

from spacebio_review.models.messages import MessagesModel
from spacebio_review.models.provider import DashScopeModel


def offline_model(config, respond):
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    if config.request_protocol == "messages":
        return MessagesModel(config, client=client)
    return DashScopeModel(
        config,
        client=AsyncOpenAI(
            api_key="offline",
            base_url="https://example.invalid/v1",
            max_retries=0,
            http_client=client,
        ),
    )

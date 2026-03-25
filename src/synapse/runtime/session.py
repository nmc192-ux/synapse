from pydantic import BaseModel, Field

from synapse.models.browser import PageData


class BrowserSession(BaseModel):
    session_id: str
    current_url: str | None = None
    page: PageData | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

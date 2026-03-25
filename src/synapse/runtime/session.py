from pydantic import BaseModel, Field

from synapse.models.browser import StructuredPageModel


class BrowserSession(BaseModel):
    session_id: str
    current_url: str | None = None
    page: StructuredPageModel | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

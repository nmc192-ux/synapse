from pydantic import BaseModel, Field, HttpUrl


class PageElement(BaseModel):
    tag: str
    role: str | None = None
    text: str | None = None
    selector_hint: str | None = None
    href: str | None = None
    input_type: str | None = None
    visible: bool = True


class PageData(BaseModel):
    url: str
    title: str
    text_excerpt: str = ""
    links: list[str] = Field(default_factory=list)
    elements: list[PageElement] = Field(default_factory=list)


class BrowserState(BaseModel):
    session_id: str
    page: PageData
    metadata: dict[str, object] = Field(default_factory=dict)


class OpenRequest(BaseModel):
    session_id: str
    url: HttpUrl


class ClickRequest(BaseModel):
    session_id: str
    selector: str


class TypeRequest(BaseModel):
    session_id: str
    selector: str
    text: str


class ExtractRequest(BaseModel):
    session_id: str
    selector: str
    attribute: str | None = None


class ScreenshotRequest(BaseModel):
    session_id: str


class ExtractedElement(BaseModel):
    selector: str
    text: str | None = None
    attribute: str | None = None
    attribute_value: str | None = None
    visible: bool = True


class ExtractionResult(BaseModel):
    session_id: str
    matches: list[ExtractedElement] = Field(default_factory=list)
    page: PageData


class ScreenshotResult(BaseModel):
    session_id: str
    image_base64: str
    format: str = "png"
    page: PageData

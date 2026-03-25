from pydantic import BaseModel, Field, HttpUrl


class PageSection(BaseModel):
    heading: str | None = None
    text: str = ""
    selector_hint: str | None = None


class PageButton(BaseModel):
    text: str = ""
    selector_hint: str | None = None
    role: str | None = None
    disabled: bool = False


class PageInput(BaseModel):
    name: str | None = None
    input_type: str | None = None
    placeholder: str | None = None
    selector_hint: str | None = None
    value: str | None = None


class PageFormField(BaseModel):
    name: str | None = None
    field_type: str | None = None
    selector_hint: str | None = None


class PageForm(BaseModel):
    name: str | None = None
    selector_hint: str | None = None
    method: str | None = None
    action: str | None = None
    fields: list[PageFormField] = Field(default_factory=list)


class PageTable(BaseModel):
    selector_hint: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class PageLink(BaseModel):
    text: str = ""
    href: str | None = None
    selector_hint: str | None = None


class PageElementMatch(BaseModel):
    element_type: str
    text: str
    selector_hint: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class PageInspection(BaseModel):
    selector: str
    text: str | None = None
    html_tag: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    is_visible: bool = True
    bounding_box: dict[str, float] | None = None


class StructuredPageModel(BaseModel):
    title: str
    url: str
    sections: list[PageSection] = Field(default_factory=list)
    buttons: list[PageButton] = Field(default_factory=list)
    inputs: list[PageInput] = Field(default_factory=list)
    forms: list[PageForm] = Field(default_factory=list)
    tables: list[PageTable] = Field(default_factory=list)
    links: list[PageLink] = Field(default_factory=list)


class BrowserState(BaseModel):
    session_id: str
    page: StructuredPageModel
    metadata: dict[str, object] = Field(default_factory=dict)


class OpenRequest(BaseModel):
    session_id: str
    agent_id: str | None = None
    url: HttpUrl


class ClickRequest(BaseModel):
    session_id: str
    agent_id: str | None = None
    selector: str


class TypeRequest(BaseModel):
    session_id: str
    agent_id: str | None = None
    selector: str
    text: str


class ExtractRequest(BaseModel):
    session_id: str
    agent_id: str | None = None
    selector: str
    attribute: str | None = None


class ScreenshotRequest(BaseModel):
    session_id: str
    agent_id: str | None = None


class LayoutRequest(BaseModel):
    session_id: str
    agent_id: str | None = None


class FindElementRequest(BaseModel):
    session_id: str
    agent_id: str | None = None
    type: str
    text: str


class InspectRequest(BaseModel):
    session_id: str
    agent_id: str | None = None
    selector: str


class ExtractedElement(BaseModel):
    selector: str
    text: str | None = None
    attribute: str | None = None
    attribute_value: str | None = None
    visible: bool = True


class ExtractionResult(BaseModel):
    session_id: str
    matches: list[ExtractedElement] = Field(default_factory=list)
    page: StructuredPageModel


class ScreenshotResult(BaseModel):
    session_id: str
    image_base64: str
    format: str = "png"
    page: StructuredPageModel

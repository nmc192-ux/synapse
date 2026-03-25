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


class SemanticRegionSummary(BaseModel):
    region_type: str
    label: str | None = None
    selector_hint: str | None = None
    summary: str = ""
    actionable_count: int = 0


class RepetitiveElementGroup(BaseModel):
    element_type: str
    group_label: str
    count: int = 0
    sample_texts: list[str] = Field(default_factory=list)
    sample_selectors: list[str] = Field(default_factory=list)
    summary: str = ""


class CompactActionableElement(BaseModel):
    element_type: str
    action: str
    label: str
    selector_hint: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class CompactTableSummary(BaseModel):
    selector_hint: str | None = None
    headers: list[str] = Field(default_factory=list)
    row_count: int = 0
    sample_rows: list[list[str]] = Field(default_factory=list)


class CompactFormSummary(BaseModel):
    name: str | None = None
    selector_hint: str | None = None
    method: str | None = None
    action: str | None = None
    field_count: int = 0
    field_names: list[str] = Field(default_factory=list)


class CompactStructuredPageModel(BaseModel):
    title: str
    url: str
    page_summary: str = ""
    semantic_regions: list[SemanticRegionSummary] = Field(default_factory=list)
    grouped_elements: list[RepetitiveElementGroup] = Field(default_factory=list)
    actionable_elements: list[CompactActionableElement] = Field(default_factory=list)
    table_summaries: list[CompactTableSummary] = Field(default_factory=list)
    form_summaries: list[CompactFormSummary] = Field(default_factory=list)


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
    full_spm: dict[str, object] = Field(default_factory=dict)
    compact_spm: CompactStructuredPageModel | None = None


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


class DismissRequest(BaseModel):
    session_id: str
    agent_id: str | None = None


class UploadRequest(BaseModel):
    session_id: str
    agent_id: str | None = None
    selector: str
    file_paths: list[str] = Field(default_factory=list)


class DownloadRequest(BaseModel):
    session_id: str
    agent_id: str | None = None
    trigger_selector: str | None = None
    timeout_ms: int = 15000


class ScrollExtractRequest(BaseModel):
    session_id: str
    agent_id: str | None = None
    selector: str
    attribute: str | None = None
    max_scrolls: int = 8
    scroll_step: int = 700


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


class DownloadArtifact(BaseModel):
    suggested_filename: str | None = None
    path: str | None = None
    url: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    status: str = "completed"


class DownloadResult(BaseModel):
    session_id: str
    artifact: DownloadArtifact
    page: StructuredPageModel
    metadata: dict[str, object] = Field(default_factory=dict)


class UploadResult(BaseModel):
    session_id: str
    uploaded_files: list[str] = Field(default_factory=list)
    page: StructuredPageModel
    metadata: dict[str, object] = Field(default_factory=dict)


class ScrollExtractResult(BaseModel):
    session_id: str
    matches: list[ExtractedElement] = Field(default_factory=list)
    page: StructuredPageModel
    metadata: dict[str, object] = Field(default_factory=dict)

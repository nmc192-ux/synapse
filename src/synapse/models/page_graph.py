from __future__ import annotations

from pydantic import BaseModel, Field


class PageNode(BaseModel):
    node_id: str
    node_type: str
    label: str = ""
    text: str = ""
    selector_reference: str | None = None
    region: str | None = None
    confidence: float = 1.0
    metadata: dict[str, object] = Field(default_factory=dict)


class ActionNode(PageNode):
    role: str | None = None
    action_type: str = ""


class EntityNode(PageNode):
    entity_type: str = ""


class RegionNode(PageNode):
    region_kind: str = ""


class PageEdge(BaseModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    edge_type: str
    metadata: dict[str, object] = Field(default_factory=dict)


class PageGraph(BaseModel):
    graph_id: str
    title: str
    url: str
    nodes: list[PageNode] = Field(default_factory=list)
    edges: list[PageEdge] = Field(default_factory=list)
    summary: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)

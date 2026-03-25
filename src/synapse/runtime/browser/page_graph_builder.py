from __future__ import annotations

import uuid

from synapse.models.page_graph import ActionNode, EntityNode, PageEdge, PageGraph, PageNode, RegionNode


class PageGraphBuilder:
    def build_page_graph(self, full_spm: dict[str, object]) -> PageGraph:
        title = str(full_spm.get("title", ""))
        url = str(full_spm.get("url", ""))
        nodes: list[PageNode] = []
        edges: list[PageEdge] = []

        page_node_id = self._node_id("page", title or "root")
        nodes.append(
            EntityNode(
                node_id=page_node_id,
                node_type="page",
                entity_type="page",
                label=title or url,
                text=title,
                metadata={"url": url},
            )
        )

        region_node_ids: list[str] = []
        for index, section in enumerate(self._items(full_spm, "sections")):
            region_id = self._node_id("region", f"{index}:{section.get('selector_hint') or section.get('heading') or 'section'}")
            region_node_ids.append(region_id)
            label = str(section.get("heading") or f"section-{index + 1}")
            nodes.append(
                RegionNode(
                    node_id=region_id,
                    node_type="region",
                    region_kind="content",
                    label=label,
                    text=str(section.get("text", ""))[:240],
                    selector_reference=self._optional_str(section.get("selector_hint")),
                    region=label,
                    confidence=0.92,
                )
            )
            edges.append(self._edge(page_node_id, region_id, "contains"))

        for index, button in enumerate(self._items(full_spm, "buttons")):
            region = self._infer_region(region_node_ids, index)
            node_id = self._node_id("action", f"button:{button.get('selector_hint') or button.get('text') or index}")
            nodes.append(
                ActionNode(
                    node_id=node_id,
                    node_type="action",
                    action_type="click",
                    label=str(button.get("text") or f"button-{index + 1}"),
                    text=str(button.get("text", "")),
                    selector_reference=self._optional_str(button.get("selector_hint")),
                    role=self._optional_str(button.get("role")) or "button",
                    region=region,
                    confidence=0.97,
                    metadata={"disabled": bool(button.get("disabled", False))},
                )
            )
            edges.append(self._edge(page_node_id, node_id, "can_act_on"))
            if region:
                edges.append(self._edge(region, node_id, "contains"))

        for index, item in enumerate(self._items(full_spm, "inputs")):
            region = self._infer_region(region_node_ids, index)
            node_id = self._node_id("action", f"input:{item.get('selector_hint') or item.get('name') or index}")
            label = str(item.get("name") or item.get("placeholder") or item.get("input_type") or f"input-{index + 1}")
            nodes.append(
                ActionNode(
                    node_id=node_id,
                    node_type="action",
                    action_type="type",
                    label=label,
                    text=str(item.get("value", "") or ""),
                    selector_reference=self._optional_str(item.get("selector_hint")),
                    role=str(item.get("input_type") or "input"),
                    region=region,
                    confidence=0.95,
                    metadata={"placeholder": item.get("placeholder")},
                )
            )
            edges.append(self._edge(page_node_id, node_id, "can_act_on"))
            if region:
                edges.append(self._edge(region, node_id, "contains"))

        for index, form in enumerate(self._items(full_spm, "forms")):
            region = self._infer_region(region_node_ids, index)
            form_id = self._node_id("region", f"form:{form.get('selector_hint') or form.get('name') or index}")
            nodes.append(
                RegionNode(
                    node_id=form_id,
                    node_type="region",
                    region_kind="form",
                    label=str(form.get("name") or f"form-{index + 1}"),
                    text=f"{len(form.get('fields', []) or [])} fields",
                    selector_reference=self._optional_str(form.get("selector_hint")),
                    region=region,
                    confidence=0.94,
                    metadata={"method": form.get("method"), "action": form.get("action")},
                )
            )
            edges.append(self._edge(page_node_id, form_id, "contains"))
            if region:
                edges.append(self._edge(region, form_id, "contains"))
            for field in form.get("fields", []) or []:
                field_id = self._node_id("action", f"field:{field.get('selector_hint') or field.get('name')}")
                nodes.append(
                    ActionNode(
                        node_id=field_id,
                        node_type="action",
                        action_type="type",
                        label=str(field.get("name") or field.get("field_type") or "field"),
                        selector_reference=self._optional_str(field.get("selector_hint")),
                        role=str(field.get("field_type") or "field"),
                        region=form_id,
                        confidence=0.93,
                    )
                )
                edges.append(self._edge(form_id, field_id, "contains"))

        for index, link in enumerate(self._items(full_spm, "links")):
            region = self._infer_region(region_node_ids, index)
            node_id = self._node_id("action", f"link:{link.get('selector_hint') or link.get('href') or index}")
            label = str(link.get("text") or link.get("href") or f"link-{index + 1}")
            nodes.append(
                ActionNode(
                    node_id=node_id,
                    node_type="action",
                    action_type="open",
                    label=label,
                    text=str(link.get("text", "")),
                    selector_reference=self._optional_str(link.get("selector_hint")),
                    role="link",
                    region=region,
                    confidence=0.96,
                    metadata={"href": link.get("href")},
                )
            )
            edges.append(self._edge(page_node_id, node_id, "navigates_to"))
            if region:
                edges.append(self._edge(region, node_id, "contains"))

        for index, table in enumerate(self._items(full_spm, "tables")):
            table_id = self._node_id("entity", f"table:{table.get('selector_hint') or index}")
            headers = table.get("headers", []) or []
            row_count = len(table.get("rows", []) or [])
            nodes.append(
                EntityNode(
                    node_id=table_id,
                    node_type="entity",
                    entity_type="table",
                    label=", ".join(headers[:4]) or f"table-{index + 1}",
                    text=f"{row_count} rows",
                    selector_reference=self._optional_str(table.get("selector_hint")),
                    confidence=0.9,
                    metadata={"headers": headers[:8], "row_count": row_count},
                )
            )
            edges.append(self._edge(page_node_id, table_id, "contains"))

        return PageGraph(
            graph_id=str(uuid.uuid4()),
            title=title,
            url=url,
            nodes=nodes,
            edges=edges,
            summary=self.summarize_graph_for_planner(
                PageGraph(graph_id="preview", title=title, url=url, nodes=nodes, edges=edges)
            ),
            metadata={
                "node_count": len(nodes),
                "edge_count": len(edges),
                "actionable_count": len([node for node in nodes if node.node_type == "action"]),
            },
        )

    def build_compact_page_graph(self, full_spm: dict[str, object]) -> PageGraph:
        graph = self.build_page_graph(full_spm)
        actionable_nodes = [node for node in graph.nodes if node.node_type == "action"][:16]
        region_nodes = [node for node in graph.nodes if node.node_type == "region"][:10]
        entity_nodes = [node for node in graph.nodes if node.node_type == "entity"][:6]
        allowed_ids = {node.node_id for node in actionable_nodes + region_nodes + entity_nodes}
        compact_edges = [
            edge for edge in graph.edges
            if edge.source_node_id in allowed_ids and edge.target_node_id in allowed_ids
        ][:32]
        compact_graph = PageGraph(
            graph_id=f"{graph.graph_id}:compact",
            title=graph.title,
            url=graph.url,
            nodes=region_nodes + actionable_nodes + entity_nodes,
            edges=compact_edges,
            metadata={**graph.metadata, "compact": True},
        )
        compact_graph.summary = self.summarize_graph_for_planner(compact_graph)
        return compact_graph

    def find_actionable_paths(self, graph: PageGraph, goal_hint: str) -> list[dict[str, object]]:
        goal = goal_hint.lower()
        matches: list[dict[str, object]] = []
        region_lookup = {node.node_id: node.label for node in graph.nodes if node.node_type == "region"}
        for node in graph.nodes:
            if node.node_type != "action":
                continue
            haystack = " ".join(
                [
                    node.label.lower(),
                    node.text.lower(),
                    str(node.metadata.get("href", "")).lower(),
                    str(node.metadata.get("placeholder", "")).lower(),
                ]
            )
            if goal and goal not in haystack and goal not in (node.role or "").lower():
                continue
            matches.append(
                {
                    "node_id": node.node_id,
                    "label": node.label,
                    "action_type": str(node.metadata.get("action_type", getattr(node, "action_type", ""))),
                    "selector_reference": node.selector_reference,
                    "region": region_lookup.get(node.region or "", node.region),
                    "confidence": node.confidence,
                }
            )
        if matches:
            return sorted(matches, key=lambda item: float(item["confidence"]), reverse=True)
        return [
            {
                "node_id": node.node_id,
                "label": node.label,
                "action_type": str(getattr(node, "action_type", "")),
                "selector_reference": node.selector_reference,
                "region": node.region,
                "confidence": node.confidence,
            }
            for node in graph.nodes
            if node.node_type == "action"
        ][:8]

    def summarize_graph_for_planner(self, graph: PageGraph, goal_hint: str | None = None) -> str:
        region_count = len([node for node in graph.nodes if node.node_type == "region"])
        action_nodes = [node for node in graph.nodes if node.node_type == "action"]
        action_labels = ", ".join(node.label for node in action_nodes[:8]) or "none"
        summary = (
            f"Semantic graph for '{graph.title}' has {region_count} regions, "
            f"{len(action_nodes)} actionable nodes, and {len(graph.edges)} relationships. "
            f"Primary actions: {action_labels}."
        )
        if goal_hint:
            paths = self.find_actionable_paths(graph, goal_hint)
            if paths:
                summary += " Goal-relevant actions: " + ", ".join(path["label"] for path in paths[:5]) + "."
        return summary

    @staticmethod
    def _items(full_spm: dict[str, object], key: str) -> list[dict[str, object]]:
        value = full_spm.get(key, [])
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _optional_str(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _node_id(prefix: str, seed: str) -> str:
        return f"{prefix}:{seed}"

    @staticmethod
    def _edge(source: str, target: str, edge_type: str) -> PageEdge:
        return PageEdge(
            edge_id=str(uuid.uuid4()),
            source_node_id=source,
            target_node_id=target,
            edge_type=edge_type,
        )

    @staticmethod
    def _infer_region(region_node_ids: list[str], index: int) -> str | None:
        if not region_node_ids:
            return None
        return region_node_ids[min(index, len(region_node_ids) - 1)]

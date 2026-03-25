from __future__ import annotations

from typing import Any

from synapse.models.browser import (
    CompactActionableElement,
    CompactFormSummary,
    CompactStructuredPageModel,
    CompactTableSummary,
    PageButton,
    PageElementMatch,
    PageForm,
    PageFormField,
    PageInput,
    PageInspection,
    PageLink,
    RepetitiveElementGroup,
    SemanticRegionSummary,
    PageSection,
    PageTable,
    StructuredPageModel,
)
from synapse.runtime.browser.page_graph_builder import PageGraphBuilder


class SPMExtractor:
    def __init__(self) -> None:
        self.page_graph_builder = PageGraphBuilder()

    async def snapshot_page(self, page: Any) -> StructuredPageModel:
        snapshot = await page.evaluate(
            """
            () => {
              const LIMITS = {
                sections: 24, buttons: 32, inputs: 32, forms: 12, tables: 12, links: 40, tableHeaders: 20, tableRows: 12, formFields: 20,
              };
              const selectorHint = (element) => {
                const tag = element.tagName.toLowerCase();
                if (element.id) return `#${element.id}`;
                if (element.getAttribute("data-testid")) return `[data-testid="${element.getAttribute("data-testid")}"]`;
                if (element.getAttribute("name")) return `${tag}[name="${element.getAttribute("name")}"]`;
                if (element.classList?.length) return `${tag}.${Array.from(element.classList).slice(0, 2).join(".")}`;
                return tag;
              };
              const compactText = (value) => (value || "").replace(/\\s+/g, " ").trim();
              const seen = { sections: new Set(), buttons: new Set(), inputs: new Set(), forms: new Set(), tables: new Set(), links: new Set() };
              const sections = []; const buttons = []; const inputs = []; const forms = []; const tables = []; const links = [];
              const addUnique = (bucket, key, collection, entry, limit) => {
                if (!key || seen[bucket].has(key) || collection.length >= limit) return;
                seen[bucket].add(key); collection.push(entry);
              };
              const roots = [];
              const collectRoots = (root, prefix = "") => {
                roots.push({ root, prefix });
                for (const element of Array.from(root.querySelectorAll("*"))) {
                  if (element.shadowRoot) {
                    const shadowPrefix = prefix ? `${prefix} >> shadow(${selectorHint(element)})` : `shadow(${selectorHint(element)})`;
                    collectRoots(element.shadowRoot, shadowPrefix);
                  }
                  if (element.tagName?.toLowerCase() === "iframe") {
                    try {
                      const frameDoc = element.contentDocument;
                      if (frameDoc?.documentElement) {
                        const framePrefix = prefix ? `${prefix} >> iframe(${selectorHint(element)})` : `iframe(${selectorHint(element)})`;
                        collectRoots(frameDoc, framePrefix);
                      }
                    } catch (_error) {}
                  }
                }
              };
              const scopedSelector = (prefix, element) => {
                const base = selectorHint(element);
                return prefix ? `${prefix} >> ${base}` : base;
              };
              collectRoots(document);
              for (const { root, prefix } of roots) {
                for (const element of Array.from(root.querySelectorAll("main section, section, article")).slice(0, LIMITS.sections)) {
                  const key = scopedSelector(prefix, element);
                  addUnique("sections", key, sections, { heading: compactText(element.querySelector("h1, h2, h3, h4, h5, h6")?.textContent || "") || null, text: compactText(element.textContent || "").slice(0, 400), selector_hint: key }, LIMITS.sections);
                }
                for (const element of Array.from(root.querySelectorAll("button, [role='button'], input[type='submit'], input[type='button']")).slice(0, LIMITS.buttons)) {
                  const key = scopedSelector(prefix, element);
                  addUnique("buttons", key, buttons, { text: compactText(element.innerText || element.value || element.textContent || ""), selector_hint: key, role: element.getAttribute("role"), disabled: Boolean(element.disabled) }, LIMITS.buttons);
                }
                for (const element of Array.from(root.querySelectorAll("input, textarea, select")).slice(0, LIMITS.inputs)) {
                  const key = scopedSelector(prefix, element);
                  addUnique("inputs", key, inputs, { name: element.getAttribute("name"), input_type: element.getAttribute("type") || element.tagName.toLowerCase(), placeholder: element.getAttribute("placeholder"), selector_hint: key, value: element.value || null }, LIMITS.inputs);
                }
                for (const form of Array.from(root.querySelectorAll("form")).slice(0, LIMITS.forms)) {
                  const key = scopedSelector(prefix, form);
                  addUnique("forms", key, forms, { name: form.getAttribute("name") || form.getAttribute("id"), selector_hint: key, method: form.getAttribute("method"), action: form.getAttribute("action"), fields: Array.from(form.querySelectorAll("input, textarea, select")).slice(0, LIMITS.formFields).map((field) => ({ name: field.getAttribute("name"), field_type: field.getAttribute("type") || field.tagName.toLowerCase(), selector_hint: scopedSelector(prefix, field) })) }, LIMITS.forms);
                }
                for (const table of Array.from(root.querySelectorAll("table")).slice(0, LIMITS.tables)) {
                  const key = scopedSelector(prefix, table);
                  addUnique("tables", key, tables, { selector_hint: key, headers: Array.from(table.querySelectorAll("thead th, tr th")).slice(0, LIMITS.tableHeaders).map((cell) => compactText(cell.textContent || "")), rows: Array.from(table.querySelectorAll("tbody tr, tr")).slice(0, LIMITS.tableRows).map((row) => Array.from(row.querySelectorAll("td")).slice(0, LIMITS.tableHeaders).map((cell) => compactText(cell.textContent || ""))).filter((row) => row.length > 0) }, LIMITS.tables);
                }
                for (const link of Array.from(root.querySelectorAll("a[href]")).slice(0, LIMITS.links)) {
                  const key = `${scopedSelector(prefix, link)}:${link.href}`;
                  addUnique("links", key, links, { text: compactText(link.textContent || ""), href: link.href, selector_hint: scopedSelector(prefix, link) }, LIMITS.links);
                }
              }
              return { url: window.location.href, title: document.title || "", sections, buttons, inputs, forms, tables, links };
            }
            """
        )
        spm = StructuredPageModel(
            url=snapshot["url"],
            title=snapshot["title"],
            sections=[PageSection(**section) for section in snapshot["sections"]],
            buttons=[PageButton(**button) for button in snapshot["buttons"]],
            inputs=[PageInput(**item) for item in snapshot["inputs"]],
            forms=[
                PageForm(
                    name=form["name"],
                    selector_hint=form["selector_hint"],
                    method=form["method"],
                    action=form["action"],
                    fields=[PageFormField(**field) for field in form["fields"]],
                )
                for form in snapshot["forms"]
            ],
            tables=[PageTable(**table) for table in snapshot["tables"]],
            links=[PageLink(**link) for link in snapshot["links"]],
        )
        return self.attach_compressed_views(spm)

    def attach_compressed_views(self, spm: StructuredPageModel) -> StructuredPageModel:
        full_spm = self.full_spm(spm)
        page_graph = self.page_graph_builder.build_page_graph(full_spm)
        compact_page_graph = self.page_graph_builder.build_compact_page_graph(full_spm)
        return spm.model_copy(
            update={
                "full_spm": full_spm,
                "compact_spm": self.build_compact_spm(spm),
                "page_graph": page_graph,
                "compact_page_graph": compact_page_graph,
            }
        )

    def full_spm(self, spm: StructuredPageModel) -> dict[str, object]:
        return {
            "title": spm.title,
            "url": spm.url,
            "sections": [section.model_dump(mode="json") for section in spm.sections],
            "buttons": [button.model_dump(mode="json") for button in spm.buttons],
            "inputs": [item.model_dump(mode="json") for item in spm.inputs],
            "forms": [form.model_dump(mode="json") for form in spm.forms],
            "tables": [table.model_dump(mode="json") for table in spm.tables],
            "links": [link.model_dump(mode="json") for link in spm.links],
        }

    def build_compact_spm(self, spm: StructuredPageModel) -> CompactStructuredPageModel:
        page_summary = self._page_summary(spm)
        semantic_regions = self._semantic_regions(spm)
        grouped_elements = self._group_repetitive_elements(spm)
        actionable_elements = self._actionable_elements(spm)
        table_summaries = self._table_summaries(spm)
        form_summaries = self._form_summaries(spm)
        return CompactStructuredPageModel(
            title=spm.title,
            url=spm.url,
            page_summary=page_summary,
            semantic_regions=semantic_regions,
            grouped_elements=grouped_elements,
            actionable_elements=actionable_elements,
            table_summaries=table_summaries,
            form_summaries=form_summaries,
        )

    def _page_summary(self, spm: StructuredPageModel) -> str:
        section_bits = [section.heading or section.text[:80] for section in spm.sections[:3] if (section.heading or section.text)]
        button_bits = [button.text for button in spm.buttons[:4] if button.text]
        return (
            f"Page '{spm.title}' with {len(spm.sections)} sections, {len(spm.buttons)} buttons, "
            f"{len(spm.inputs)} inputs, {len(spm.forms)} forms, {len(spm.tables)} tables, and {len(spm.links)} links. "
            f"Key content: {', '.join(section_bits) or 'none'}. "
            f"Primary actions: {', '.join(button_bits) or 'none'}."
        )

    def _semantic_regions(self, spm: StructuredPageModel) -> list[SemanticRegionSummary]:
        regions: list[SemanticRegionSummary] = []
        if spm.links:
            nav_labels = [link.text or link.href or "" for link in spm.links[:6]]
            regions.append(
                SemanticRegionSummary(
                    region_type="navigation",
                    label="primary navigation",
                    summary=f"Navigation links: {', '.join(filter(None, nav_labels)) or 'unnamed links'}.",
                    actionable_count=min(len(spm.links), 6),
                )
            )
        for section in spm.sections[:5]:
            regions.append(
                SemanticRegionSummary(
                    region_type="content",
                    label=section.heading,
                    selector_hint=section.selector_hint,
                    summary=(section.text[:180] + "...") if len(section.text) > 180 else section.text,
                    actionable_count=0,
                )
            )
        for form in spm.forms[:4]:
            field_names = [field.name or field.field_type or "field" for field in form.fields[:5]]
            regions.append(
                SemanticRegionSummary(
                    region_type="form",
                    label=form.name,
                    selector_hint=form.selector_hint,
                    summary=f"{len(form.fields)} fields: {', '.join(field_names) or 'unnamed fields'}.",
                    actionable_count=len(form.fields),
                )
            )
        for table in spm.tables[:3]:
            regions.append(
                SemanticRegionSummary(
                    region_type="table",
                    label=", ".join(table.headers[:4]) or "data table",
                    selector_hint=table.selector_hint,
                    summary=f"Table with {len(table.rows)} rows and headers {', '.join(table.headers[:5]) or 'none'}.",
                    actionable_count=0,
                )
            )
        return regions[:12]

    def _group_repetitive_elements(self, spm: StructuredPageModel) -> list[RepetitiveElementGroup]:
        groups: list[RepetitiveElementGroup] = []
        groups.extend(self._group_by_label("button", [button.text or "button" for button in spm.buttons], [button.selector_hint for button in spm.buttons]))
        groups.extend(
            self._group_by_label(
                "input",
                [item.name or item.placeholder or item.input_type or "input" for item in spm.inputs],
                [item.selector_hint for item in spm.inputs],
            )
        )
        groups.extend(
            self._group_by_label(
                "link",
                [link.text or (link.href or "link") for link in spm.links],
                [link.selector_hint for link in spm.links],
            )
        )
        return sorted(groups, key=lambda group: group.count, reverse=True)[:10]

    def _group_by_label(
        self,
        element_type: str,
        labels: list[str],
        selectors: list[str | None],
    ) -> list[RepetitiveElementGroup]:
        grouped: dict[str, dict[str, object]] = {}
        for label, selector in zip(labels, selectors, strict=False):
            normalized = (label or element_type).strip().lower() or element_type
            bucket = grouped.setdefault(
                normalized,
                {
                    "sample_texts": [],
                    "sample_selectors": [],
                    "count": 0,
                },
            )
            bucket["count"] = int(bucket["count"]) + 1
            if label and len(bucket["sample_texts"]) < 3:
                bucket["sample_texts"].append(label)
            if selector and len(bucket["sample_selectors"]) < 3:
                bucket["sample_selectors"].append(selector)
        return [
            RepetitiveElementGroup(
                element_type=element_type,
                group_label=label,
                count=int(values["count"]),
                sample_texts=list(values["sample_texts"]),
                sample_selectors=list(values["sample_selectors"]),
                summary=f"{values['count']} {element_type} elements with similar labels.",
            )
            for label, values in grouped.items()
            if int(values["count"]) > 1
        ]

    def _actionable_elements(self, spm: StructuredPageModel) -> list[CompactActionableElement]:
        elements: list[CompactActionableElement] = []
        for button in spm.buttons[:8]:
            elements.append(
                CompactActionableElement(
                    element_type="button",
                    action="click",
                    label=button.text or "button",
                    selector_hint=button.selector_hint,
                    metadata={"role": button.role, "disabled": button.disabled},
                )
            )
        for item in spm.inputs[:6]:
            elements.append(
                CompactActionableElement(
                    element_type="input",
                    action="type",
                    label=item.name or item.placeholder or item.input_type or "input",
                    selector_hint=item.selector_hint,
                    metadata={"input_type": item.input_type, "placeholder": item.placeholder},
                )
            )
        for form in spm.forms[:4]:
            elements.append(
                CompactActionableElement(
                    element_type="form",
                    action="submit",
                    label=form.name or "form",
                    selector_hint=form.selector_hint,
                    metadata={"field_count": len(form.fields), "method": form.method},
                )
            )
        for link in spm.links[:8]:
            elements.append(
                CompactActionableElement(
                    element_type="navigation_link",
                    action="open",
                    label=link.text or link.href or "link",
                    selector_hint=link.selector_hint,
                    metadata={"href": link.href},
                )
            )
        return elements[:20]

    def _table_summaries(self, spm: StructuredPageModel) -> list[CompactTableSummary]:
        return [
            CompactTableSummary(
                selector_hint=table.selector_hint,
                headers=table.headers[:8],
                row_count=len(table.rows),
                sample_rows=table.rows[:3],
            )
            for table in spm.tables[:4]
        ]

    def _form_summaries(self, spm: StructuredPageModel) -> list[CompactFormSummary]:
        return [
            CompactFormSummary(
                name=form.name,
                selector_hint=form.selector_hint,
                method=form.method,
                action=form.action,
                field_count=len(form.fields),
                field_names=[field.name or field.field_type or "field" for field in form.fields[:8]],
            )
            for form in spm.forms[:5]
        ]

    def find_element(self, spm: StructuredPageModel, element_type: str, text: str) -> list[PageElementMatch]:
        normalized_type = element_type.lower()
        normalized_text = text.lower()
        matches: list[PageElementMatch] = []
        if normalized_type == "sections":
            for section in spm.sections:
                haystack = " ".join(filter(None, [section.heading, section.text])).lower()
                if normalized_text in haystack:
                    matches.append(PageElementMatch(element_type="section", text=section.heading or section.text, selector_hint=section.selector_hint))
        elif normalized_type == "buttons":
            for button in spm.buttons:
                if normalized_text in button.text.lower():
                    matches.append(PageElementMatch(element_type="button", text=button.text, selector_hint=button.selector_hint, metadata={"role": button.role, "disabled": button.disabled}))
        elif normalized_type == "inputs":
            for item in spm.inputs:
                haystack = " ".join(filter(None, [item.name, item.placeholder, item.value])).lower()
                if normalized_text in haystack:
                    matches.append(PageElementMatch(element_type="input", text=item.name or item.placeholder or item.value or "", selector_hint=item.selector_hint, metadata={"input_type": item.input_type}))
        elif normalized_type == "forms":
            for form in spm.forms:
                haystack = " ".join(filter(None, [form.name, form.selector_hint])).lower()
                if normalized_text in haystack:
                    matches.append(PageElementMatch(element_type="form", text=form.name or "", selector_hint=form.selector_hint, metadata={"method": form.method, "action": form.action}))
        elif normalized_type == "tables":
            for table in spm.tables:
                haystack = " ".join(table.headers + [cell for row in table.rows for cell in row]).lower()
                if normalized_text in haystack:
                    matches.append(PageElementMatch(element_type="table", text=" | ".join(table.headers), selector_hint=table.selector_hint, metadata={"row_count": len(table.rows)}))
        elif normalized_type == "links":
            for link in spm.links:
                haystack = " ".join(filter(None, [link.text, link.href])).lower()
                if normalized_text in haystack:
                    matches.append(PageElementMatch(element_type="link", text=link.text or link.href or "", selector_hint=link.selector_hint, metadata={"href": link.href}))
        else:
            raise ValueError(f"Unsupported structured element type: {element_type}")
        return matches

    async def inspect(self, page: Any, selector: str) -> PageInspection:
        locator = page.locator(selector).first
        box = await locator.bounding_box()
        attributes = await locator.evaluate(
            """
            (element) => Object.fromEntries(
              Array.from(element.attributes).map((attribute) => [attribute.name, attribute.value])
            )
            """
        )
        return PageInspection(
            selector=selector,
            text=await locator.text_content(),
            html_tag=await locator.evaluate("(element) => element.tagName.toLowerCase()"),
            attributes=attributes,
            is_visible=await locator.is_visible(),
            bounding_box=box,
        )

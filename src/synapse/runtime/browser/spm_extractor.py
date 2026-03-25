from __future__ import annotations

from typing import Any

from synapse.models.browser import (
    PageButton,
    PageElementMatch,
    PageForm,
    PageFormField,
    PageInput,
    PageInspection,
    PageLink,
    PageSection,
    PageTable,
    StructuredPageModel,
)


class SPMExtractor:
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
        return StructuredPageModel(
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

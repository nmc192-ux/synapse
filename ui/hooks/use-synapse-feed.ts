"use client";

import { useEffect, useState } from "react";
import { initialState } from "@/lib/mock-data";
import {
  ActionItem,
  ActivityItem,
  InterventionItem,
  DashboardState,
  MemoryItem,
  MessageItem,
  SynapseEvent,
  TaskItem,
  ThoughtItem,
  AgentBudgetItem,
} from "@/lib/types";

const defaultSocketUrl =
  process.env.NEXT_PUBLIC_SYNAPSE_WS_URL ?? "ws://127.0.0.1:8000/api/ws";

type DashboardFeed = DashboardState & {
  refreshInterventions: () => Promise<void>;
  approveIntervention: (interventionId: string) => Promise<void>;
  rejectIntervention: (interventionId: string, reason?: string) => Promise<void>;
  provideInterventionInput: (interventionId: string, payload: Record<string, unknown>) => Promise<void>;
};

export function useSynapseFeed(): DashboardFeed {
  const [state, setState] = useState<DashboardState>(initialState);

  useEffect(() => {
    const socket = new WebSocket(defaultSocketUrl);

    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as SynapseEvent;
      setState((current) => applyEvent(current, event));
    };

    socket.onerror = () => {
      socket.close();
    };

    return () => {
      socket.close();
    };
  }, []);

  useEffect(() => {
    void refreshInterventions();
  }, []);

  async function refreshInterventions() {
    try {
      const response = await fetch("/api/interventions");
      if (!response.ok) {
        return;
      }
      const interventions = (await response.json()) as Record<string, unknown>[];
      setState((current) => ({
        ...current,
        interventions: interventions.map(toInterventionItem).filter(Boolean) as InterventionItem[],
      }));
    } catch {
      return;
    }
  }

  async function approveIntervention(interventionId: string) {
    await fetch(`/api/interventions/${interventionId}/approve`, { method: "POST" });
    await refreshInterventions();
  }

  async function rejectIntervention(interventionId: string, reason?: string) {
    await fetch(`/api/interventions/${interventionId}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reason ? { reason } : {}),
    });
    await refreshInterventions();
  }

  async function provideInterventionInput(interventionId: string, payload: Record<string, unknown>) {
    await fetch(`/api/interventions/${interventionId}/provide_input`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await refreshInterventions();
  }

  return {
    ...state,
    refreshInterventions,
    approveIntervention,
    rejectIntervention,
    provideInterventionInput,
  };
}

function applyEvent(current: DashboardState, event: SynapseEvent): DashboardState {
  const payload = event.payload ?? {};
  const activity = buildActivity(event, payload);
  const thought = buildThought(event, payload);
  const action = buildAction(event, payload);
  const message = buildMessage(event, payload);
  const memory = buildMemory(event, payload);
  const task = buildTask(event, payload);
  const budget = buildBudget(event, payload);
  const intervention = buildIntervention(event, payload);

  return {
    ...current,
    events: [event, ...current.events].slice(0, 40),
    activity: activity ? [activity, ...current.activity].slice(0, 8) : current.activity,
    thoughts: thought ? [thought, ...current.thoughts].slice(0, 6) : current.thoughts,
    actions: action ? [action, ...current.actions].slice(0, 10) : current.actions,
    memory: memory ? [memory, ...current.memory].slice(0, 6) : current.memory,
    messages: message ? [message, ...current.messages].slice(0, 8) : current.messages,
    tasks: task ? mergeTask(current.tasks, task).slice(0, 8) : current.tasks,
    budgets: budget ? mergeBudget(current.budgets, budget).slice(0, 6) : current.budgets,
    interventions: intervention ? mergeIntervention(current.interventions, intervention).slice(0, 8) : current.interventions,
    page: derivePage(current.page, event, payload),
  };
}

function buildActivity(event: SynapseEvent, payload: Record<string, unknown>): ActivityItem {
  return {
    id: `${event.event_type}-${event.timestamp ?? crypto.randomUUID()}`,
    label: event.agent_id ?? "synapse-runtime",
    detail: summarizeEvent(event.event_type, payload),
    tone: toneForEvent(event.event_type),
    timestamp: relativeTimestamp(event.timestamp),
  };
}

function buildThought(
  event: SynapseEvent,
  payload: Record<string, unknown>,
): ThoughtItem | null {
  if (!event.event_type.startsWith("loop.")) {
    return null;
  }

  return {
    id: `${event.event_type}-thought-${event.timestamp ?? crypto.randomUUID()}`,
    agent: event.agent_id ?? "agent",
    phase: event.event_type.replace("loop.", ""),
    content: summarizeEvent(event.event_type, payload),
  };
}

function buildAction(
  event: SynapseEvent,
  payload: Record<string, unknown>,
): ActionItem | null {
  const actionName = typeof payload.action === "string" ? payload.action : event.event_type;
  const target =
    typeof payload.selector === "string"
      ? payload.selector
      : typeof payload.url === "string"
        ? payload.url
        : typeof payload.tool_name === "string"
          ? payload.tool_name
          : typeof payload.session_id === "string"
            ? payload.session_id
            : "runtime";

  if (
    !event.event_type.includes("page") &&
    !event.event_type.includes("tool") &&
    !event.event_type.includes("task") &&
    !event.event_type.includes("screenshot") &&
    !event.event_type.includes("upload") &&
    !event.event_type.includes("download") &&
    !event.event_type.includes("popup") &&
    !event.event_type.includes("challenge") &&
    !event.event_type.includes("captcha") &&
    !event.event_type.includes("human_intervention") &&
    !event.event_type.includes("console") &&
    !event.event_type.includes("network")
  ) {
    return null;
  }

  return {
    id: `${event.event_type}-action-${event.timestamp ?? crypto.randomUUID()}`,
    action: actionName,
    target,
    status: event.event_type.includes("task") ? "running" : "completed",
  };
}

function buildMessage(
  event: SynapseEvent,
  payload: Record<string, unknown>,
): MessageItem | null {
  if (event.event_type !== "agent.message" && event.event_type !== "a2a.message") {
    return null;
  }

  const from = stringify(payload.sender_agent_id) ?? event.agent_id ?? "unknown";
  const to = stringify(payload.recipient_agent_id) ?? "broadcast";
  const content =
    stringify(payload.content) ??
    stringify(payload.message) ??
    stringify(payload.type) ??
    "Agent message received";

  return {
    id: `${event.event_type}-message-${event.timestamp ?? crypto.randomUUID()}`,
    from,
    to,
    content,
    kind: event.event_type === "a2a.message" ? "a2a" : "agent",
  };
}

function buildMemory(
  event: SynapseEvent,
  payload: Record<string, unknown>,
): MemoryItem | null {
  if (event.event_type !== "loop.reflected" && event.event_type !== "task.updated") {
    return null;
  }

  return {
    id: `${event.event_type}-memory-${event.timestamp ?? crypto.randomUUID()}`,
    key: event.event_type,
    value: summarizeEvent(event.event_type, payload),
  };
}

function buildTask(
  event: SynapseEvent,
  payload: Record<string, unknown>,
): TaskItem | null {
  if (event.event_type !== "task.updated") {
    return null;
  }

  return {
    id: stringify(payload.task_id) ?? stringify(payload.id) ?? crypto.randomUUID(),
    goal: stringify(payload.goal) ?? "Runtime task",
    status: stringify(payload.status) ?? "running",
    assignedAgent: stringify(payload.assigned_agent) ?? event.agent_id ?? "unassigned",
  };
}

function buildBudget(
  event: SynapseEvent,
  payload: Record<string, unknown>,
): AgentBudgetItem | null {
  if (event.event_type !== "budget.updated") {
    return null;
  }

  const usage = payload.usage;
  if (!usage || typeof usage !== "object") {
    return null;
  }

  const usageRecord = usage as Record<string, unknown>;
  const limits = (usageRecord.limits && typeof usageRecord.limits === "object"
    ? usageRecord.limits
    : {}) as Record<string, unknown>;

  return {
    agent: event.agent_id ?? "agent",
    runtimeSeconds: Number(usageRecord.runtime_seconds ?? 0),
    warnings: [
      ...toStringArray(usageRecord.warnings),
      ...(typeof payload.warning === "string" ? [payload.warning] : []),
    ].slice(0, 4),
    llmCostEstimate: Number(usageRecord.llm_cost_estimate ?? 0),
    toolCostEstimate: Number(usageRecord.tool_cost_estimate ?? 0),
    metrics: [
      budgetMetric("Steps", usageRecord.steps_used, limits.max_steps),
      budgetMetric("Pages", usageRecord.pages_opened, limits.max_pages),
      budgetMetric("Tool Calls", usageRecord.tool_calls, limits.max_tool_calls),
    ],
  };
}

function buildIntervention(
  event: SynapseEvent,
  payload: Record<string, unknown>,
): InterventionItem | null {
  if (
    event.event_type !== "intervention.queued" &&
    event.event_type !== "intervention.updated" &&
    event.event_type !== "intervention.resolved"
  ) {
    return null;
  }
  const record = (payload.intervention && typeof payload.intervention === "object"
    ? payload.intervention
    : payload) as Record<string, unknown>;
  return toInterventionItem(record);
}

function derivePage(
  current: DashboardState["page"],
  event: SynapseEvent,
  payload: Record<string, unknown>,
): DashboardState["page"] {
  const page = payload.page;
  if (!page || typeof page !== "object") {
    return current;
  }

  const pageRecord = page as Record<string, unknown>;
  const sections = toRecordArray(pageRecord.sections);
  const buttons = toRecordArray(pageRecord.buttons);
  const inputs = toRecordArray(pageRecord.inputs);
  const forms = toRecordArray(pageRecord.forms);
  const tables = toRecordArray(pageRecord.tables);

  const elements = [
    ...sections.map((section) => ({
      tag: "section",
      text: stringify(section.heading) ?? stringify(section.text) ?? "(section)",
      selectorHint: stringify(section.selector_hint) ?? "section",
    })),
    ...buttons.map((button) => ({
      tag: "button",
      text: stringify(button.text) ?? "(button)",
      selectorHint: stringify(button.selector_hint) ?? "button",
    })),
    ...inputs.map((input) => ({
      tag: "input",
      text: stringify(input.name) ?? stringify(input.placeholder) ?? "(input)",
      selectorHint: stringify(input.selector_hint) ?? "input",
    })),
    ...forms.map((form) => ({
      tag: "form",
      text: stringify(form.name) ?? "(form)",
      selectorHint: stringify(form.selector_hint) ?? "form",
    })),
    ...tables.map((table) => ({
      tag: "table",
      text: Array.isArray(table.headers) ? table.headers.map((item) => String(item)).join(" | ") : "(table)",
      selectorHint: stringify(table.selector_hint) ?? "table",
    })),
  ].slice(0, 6);

  const excerpt =
    sections
      .map((section) => stringify(section.text))
      .filter((value): value is string => Boolean(value))
      .join(" ")
      .slice(0, 280) ||
    stringify(pageRecord.text_excerpt) ||
    stringify(pageRecord.excerpt) ||
    current.excerpt;

  return {
    url: stringify(pageRecord.url) ?? current.url,
    title: stringify(pageRecord.title) ?? current.title,
    excerpt,
    links: Array.isArray(pageRecord.links)
      ? pageRecord.links
          .map((item) => {
            if (typeof item === "string") return item;
            if (item && typeof item === "object") {
              const link = item as Record<string, unknown>;
              return stringify(link.href) ?? stringify(link.text) ?? "";
            }
            return "";
          })
          .filter((value) => value.length > 0)
          .slice(0, 6)
      : current.links,
    elements: elements.length > 0 ? elements : current.elements,
    sections:
      sections.length > 0
        ? sections.slice(0, 4).map((section) => ({
            heading: stringify(section.heading) ?? "Untitled section",
            text: stringify(section.text) ?? "",
          }))
        : current.sections,
  };
}

function summarizeEvent(eventType: string, payload: Record<string, unknown>): string {
  switch (eventType) {
    case "page.navigated":
      return `Navigated to ${stringify(payload.url) ?? "a new page"} and refreshed structured page state.`;
    case "data.extracted":
      return `Extraction completed for ${stringify(payload.selector) ?? "page content"}.`;
    case "tool.called":
      return `Tool call finished: ${stringify(payload.tool_name) ?? "unknown tool"}.`;
    case "task.updated":
      return `Task ${stringify(payload.status) ?? "running"} for ${stringify(payload.goal) ?? "runtime work"}.`;
    case "budget.updated":
      return stringify(payload.warning) ?? "Agent budget usage updated.";
    case "loop.observed":
      return `Observed ${stringify(payload.event_count) ?? "0"} fresh events before planning.`;
    case "loop.planned":
      return `Planned ${(payload.actions as unknown[] | undefined)?.length ?? 0} browser actions.`;
    case "loop.acted":
      return `Executed ${stringify(payload.type) ?? "an action"} against the active browser session.`;
    case "loop.reflected":
      return stringify(payload.notes) ?? "Agent reflected on the last action sequence.";
    case "agent.message":
      return stringify(payload.content) ?? "New agent-to-agent message received.";
    case "a2a.message":
      return `A2A exchange: ${stringify(payload.type) ?? "message"}.`;
    case "screenshot.captured":
      return `Captured a new page screenshot artifact.`;
    case "popup.dismissed":
      return "Dismissed blocking modal or consent dialog before interaction.";
    case "download.completed":
      return `Download captured: ${stringify(payload?.artifact && (payload.artifact as Record<string, unknown>).suggested_filename) ?? "artifact"}.`;
    case "upload.completed":
      return `Uploaded ${(payload.uploaded_files as unknown[] | undefined)?.length ?? 0} file(s).`;
    case "navigation.route_changed":
      return `Detected route change to ${stringify(payload.to_url) ?? "new route"}.`;
    case "session.expired":
      return "Session expiration was detected while browsing.";
    case "browser.challenge.detected":
      return `Anti-bot challenge detected on ${stringify(payload.page_url) ?? "the current page"}.`;
    case "browser.captcha.detected":
      return "CAPTCHA challenge detected; autonomous execution should stop or hand off.";
    case "browser.human_intervention.required":
      return "Operator handoff required to continue past a browser challenge.";
    case "browser.console.logged":
      return stringify(payload.message) ?? "Browser console log captured.";
    case "browser.network.failed":
      return `Network request failed for ${stringify(payload.url) ?? "unknown resource"}.`;
    case "browser.navigation.traced":
      return `Navigation trace recorded for ${stringify(payload.url) ?? "current page"}.`;
    case "browser.popup.opened":
      return `Popup detected for ${stringify(payload.popup_url) ?? "new window"}.`;
    case "intervention.queued":
      return stringify(payload.reason) ?? "Run is waiting for operator review.";
    case "intervention.updated":
      return "Operator input was attached to a waiting run.";
    case "intervention.resolved":
      return "Operator resolved a waiting run.";
    case "browser.error":
      return stringify(payload.error) ?? "Browser interaction error captured.";
    default:
      return `Received ${eventType}.`;
  }
}

function toneForEvent(eventType: string): ActivityItem["tone"] {
  if (eventType.includes("challenge") || eventType.includes("captcha") || eventType.includes("human_intervention")) {
    return "alert";
  }
  if (eventType.includes("error")) return "alert";
  if (eventType.includes("tool") || eventType.includes("planned")) return "warm";
  return "normal";
}

function relativeTimestamp(timestamp?: string): string {
  if (!timestamp) return "live";
  return new Date(timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function stringify(value: unknown): string | null {
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

function toRecordArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object");
}

function mergeTask(current: TaskItem[], next: TaskItem): TaskItem[] {
  const remaining = current.filter((task) => task.id !== next.id);
  return [next, ...remaining];
}

function mergeBudget(current: AgentBudgetItem[], next: AgentBudgetItem): AgentBudgetItem[] {
  const remaining = current.filter((item) => item.agent !== next.agent);
  return [next, ...remaining];
}

function mergeIntervention(current: InterventionItem[], next: InterventionItem): InterventionItem[] {
  const remaining = current.filter((item) => item.id !== next.id);
  return [next, ...remaining];
}

function budgetMetric(label: string, used: unknown, limit: unknown) {
  const resolvedUsed = Number(used ?? 0);
  const resolvedLimit = Number(limit ?? 0);
  const percent = resolvedLimit > 0 ? Math.min(100, Math.round((resolvedUsed / resolvedLimit) * 100)) : 0;
  return {
    label,
    used: resolvedUsed,
    limit: resolvedLimit,
    percent,
  };
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

function toInterventionItem(value: Record<string, unknown> | null | undefined): InterventionItem | null {
  if (!value) {
    return null;
  }
  const payload = value.payload && typeof value.payload === "object" ? (value.payload as Record<string, unknown>) : {};
  const ui = payload.ui && typeof payload.ui === "object" ? (payload.ui as Record<string, unknown>) : {};
  const runContext = ui.run_context && typeof ui.run_context === "object" ? (ui.run_context as Record<string, unknown>) : {};
  const interventionId = stringify(value.intervention_id);
  const runId = stringify(value.run_id);
  if (!interventionId || !runId) {
    return null;
  }
  return {
    id: interventionId,
    runId,
    projectId: stringify(value.project_id),
    reason: stringify(value.reason) ?? stringify(ui.reason) ?? "Operator review required",
    state: stringify(value.state) ?? "pending",
    category: stringify(payload.category) ?? undefined,
    contextPreview:
      stringify(runContext.goal) ??
      stringify(payload.reason) ??
      `Run ${runId} requires operator review.`,
    createdAt: stringify(value.created_at) ?? undefined,
    resolvedAt: stringify(value.resolved_at),
  };
}

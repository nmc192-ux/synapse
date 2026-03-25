"use client";

import { useEffect, useState } from "react";
import { initialState } from "@/lib/mock-data";
import {
  ActionItem,
  ActivityItem,
  DashboardState,
  MemoryItem,
  MessageItem,
  SynapseEvent,
  ThoughtItem,
} from "@/lib/types";

const defaultSocketUrl =
  process.env.NEXT_PUBLIC_SYNAPSE_WS_URL ?? "ws://127.0.0.1:8000/api/ws";

export function useSynapseFeed(): DashboardState {
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

  return state;
}

function applyEvent(current: DashboardState, event: SynapseEvent): DashboardState {
  const payload = event.payload ?? {};
  const activity = buildActivity(event, payload);
  const thought = buildThought(event, payload);
  const action = buildAction(event, payload);
  const message = buildMessage(event, payload);
  const memory = buildMemory(event, payload);

  return {
    ...current,
    events: [event, ...current.events].slice(0, 40),
    activity: activity ? [activity, ...current.activity].slice(0, 8) : current.activity,
    thoughts: thought ? [thought, ...current.thoughts].slice(0, 6) : current.thoughts,
    actions: action ? [action, ...current.actions].slice(0, 10) : current.actions,
    memory: memory ? [memory, ...current.memory].slice(0, 6) : current.memory,
    messages: message ? [message, ...current.messages].slice(0, 8) : current.messages,
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
    !event.event_type.includes("screenshot")
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
      return `Task status updated with latest runtime artifacts.`;
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
    default:
      return `Received ${eventType}.`;
  }
}

function toneForEvent(eventType: string): ActivityItem["tone"] {
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

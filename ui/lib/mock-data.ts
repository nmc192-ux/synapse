import { DashboardState } from "@/lib/types";

export const initialState: DashboardState = {
  events: [],
  activity: [
    {
      id: "activity-1",
      label: "Codex agent",
      detail: "Opened the target page and captured a structured snapshot.",
      tone: "normal",
      timestamp: "just now",
    },
    {
      id: "activity-2",
      label: "OpenClaw agent",
      detail: "Delegated GitHub ecosystem search to the plugin runtime.",
      tone: "warm",
      timestamp: "8s ago",
    },
    {
      id: "activity-3",
      label: "Claude Code agent",
      detail: "Prepared a reflection summary from recent extraction events.",
      tone: "normal",
      timestamp: "16s ago",
    },
  ],
  thoughts: [
    {
      id: "thought-1",
      agent: "codex-example",
      phase: "observe",
      content: "The page exposes a stable headline and three reachable links. DOM looks safe for extraction.",
    },
    {
      id: "thought-2",
      agent: "openclaw-example",
      phase: "plan",
      content: "Use web.search first, then compare page language against GitHub repository descriptions.",
    },
    {
      id: "thought-3",
      agent: "claude-code-example",
      phase: "reflect",
      content: "Current memory suggests the operator wants explainability as much as execution speed.",
    },
  ],
  actions: [
    {
      id: "action-1",
      action: "browser.open",
      target: "https://example.com",
      status: "completed",
    },
    {
      id: "action-2",
      action: "browser.extract",
      target: "h1",
      status: "completed",
    },
    {
      id: "action-3",
      action: "browser.call_tool",
      target: "github.search",
      status: "running",
    },
    {
      id: "action-4",
      action: "browser.send_agent_message",
      target: "codex-example -> claude-code-example",
      status: "queued",
    },
  ],
  memory: [
    {
      id: "memory-1",
      key: "mission",
      value: "Build an operator-grade browser runtime for autonomous agents.",
    },
    {
      id: "memory-2",
      key: "focus",
      value: "Prioritize observability: activity, thoughts, memory, and inter-agent traffic.",
    },
    {
      id: "memory-3",
      key: "runtime",
      value: "FastAPI + Playwright + WebSocket event stream + plugin tools.",
    },
  ],
  messages: [
    {
      id: "message-1",
      from: "openclaw-example",
      to: "codex-example",
      content: "I found candidate repos. Compare them against the current site copy.",
    },
    {
      id: "message-2",
      from: "codex-example",
      to: "claude-code-example",
      content: "Page snapshot is stable. Draft reflection after extraction completes.",
    },
  ],
  page: {
    url: "https://example.com",
    title: "Example Domain",
    excerpt:
      "This domain is for use in illustrative examples in documents. It may be used in literature without prior coordination or asking for permission.",
    links: [
      "https://www.iana.org/domains/example",
      "https://example.com/docs",
      "https://example.com/archive",
    ],
    elements: [
      { tag: "h1", text: "Example Domain", selectorHint: "h1" },
      { tag: "p", text: "Illustrative example content", selectorHint: "p" },
      { tag: "a", text: "More information", selectorHint: "a[href]" },
      { tag: "button", text: "Inspect runtime", selectorHint: "button" },
    ],
  },
};

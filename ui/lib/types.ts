export type SynapseEvent = {
  event_type: string;
  agent_id?: string | null;
  session_id?: string | null;
  timestamp?: string;
  payload?: Record<string, unknown>;
};

export type ActivityItem = {
  id: string;
  label: string;
  detail: string;
  tone?: "normal" | "warm" | "alert";
  timestamp: string;
};

export type ThoughtItem = {
  id: string;
  agent: string;
  phase: string;
  content: string;
};

export type ActionItem = {
  id: string;
  action: string;
  target: string;
  status: string;
};

export type MemoryItem = {
  id: string;
  key: string;
  value: string;
};

export type MessageItem = {
  id: string;
  from: string;
  to: string;
  content: string;
  kind: "agent" | "a2a";
};

export type PageElementView = {
  tag: string;
  text: string;
  selectorHint: string;
};

export type PageSectionView = {
  heading: string;
  text: string;
};

export type PageState = {
  url: string;
  title: string;
  excerpt: string;
  links: string[];
  elements: PageElementView[];
  sections: PageSectionView[];
};

export type TaskItem = {
  id: string;
  goal: string;
  status: string;
  assignedAgent: string;
};

export type DashboardState = {
  events: SynapseEvent[];
  activity: ActivityItem[];
  thoughts: ThoughtItem[];
  actions: ActionItem[];
  memory: MemoryItem[];
  messages: MessageItem[];
  tasks: TaskItem[];
  page: PageState;
};

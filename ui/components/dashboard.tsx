"use client";

import { useState, type ReactNode } from "react";
import { useSynapseFeed } from "@/hooks/use-synapse-feed";

function Panel({
  title,
  badge,
  children,
  className,
}: {
  title: string;
  badge?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className ?? ""}`.trim()}>
      <div className="panel-inner">
        <div className="panel-title">
          <h2>{title}</h2>
          {badge ? <span className="panel-badge">{badge}</span> : null}
        </div>
        {children}
      </div>
    </section>
  );
}

export function Dashboard() {
  const state = useSynapseFeed();
  const [interventionInputs, setInterventionInputs] = useState<Record<string, string>>({});
  const liveEvents = state.events.length;
  const activeAgents = new Set([
    ...state.activity.map((item) => item.label),
    ...state.thoughts.map((item) => item.agent),
  ]).size;
  const lastSignal = state.activity[0]?.timestamp ?? "live";
  const activeTasks = state.tasks.filter((task) => task.status !== "completed").length;
  const a2aMessages = state.messages.filter((message) => message.kind === "a2a").length;

  return (
    <main className="shell">
      <div className="frame">
        <header className="hero">
          <div>
            <span className="eyebrow">Synapse Control Surface</span>
            <h1 className="title">Observe autonomous browser systems as they think.</h1>
            <p className="subtitle">
              A Next.js operator UI for the Synapse runtime, designed around live agent activity,
              page structure, reasoning phases, actions, memory, and agent-to-agent traffic.
            </p>
          </div>

          <div className="status-strip">
            <div className="stat">
              <span className="stat-label">Active Agents</span>
              <span className="stat-value">{activeAgents}</span>
            </div>
            <div className="stat">
              <span className="stat-label">Live Events</span>
              <span className="stat-value">{liveEvents}</span>
            </div>
            <div className="stat">
              <span className="stat-label">Last Signal</span>
              <span className="stat-value">{lastSignal}</span>
            </div>
            <div className="stat">
              <span className="stat-label">Active Tasks</span>
              <span className="stat-value">{activeTasks}</span>
            </div>
          </div>
        </header>

        {state.authError ? (
          <section className="panel" style={{ marginBottom: 18 }}>
            <div className="panel-inner">
              <div className="panel-title">
                <h2>Operator Auth</h2>
                <span className="panel-badge">{state.authStatus}</span>
              </div>
              <p>{state.authError}</p>
              <p className="footer-note">
                Configure <span className="mono">NEXT_PUBLIC_SYNAPSE_BEARER_TOKEN</span> or{" "}
                <span className="mono">NEXT_PUBLIC_SYNAPSE_API_KEY</span> and{" "}
                <span className="mono">NEXT_PUBLIC_SYNAPSE_PROJECT_ID</span> for project-scoped dashboard access.
              </p>
            </div>
          </section>
        ) : null}

        <div className="dashboard-grid">
          <Panel title="Web Page View" badge={state.page.title} className="page-view">
            <div className="browser-frame">
              <div className="browser-bar">
                <span className="browser-dot" />
                <span className="browser-dot" />
                <span className="browser-dot" />
                <div className="url-bar mono">{state.page.url}</div>
              </div>

              <div className="page-content">
                <div className="page-stage">
                  <div className="excerpt-card">
                    <span className="muted">Current Page View</span>
                    <h3>{state.page.title}</h3>
                    <p>{state.page.excerpt}</p>
                  </div>

                  <div className="section-stack">
                    {state.page.sections.map((section, index) => (
                      <article className="section-card" key={`${section.heading}-${index}`}>
                        <span>Section</span>
                        <strong>{section.heading}</strong>
                        <p>{section.text}</p>
                      </article>
                    ))}
                  </div>
                </div>

                <div className="mini-grid">
                  <div className="link-grid">
                    {state.page.links.map((link) => (
                      <div className="chip-card" key={link}>
                        <span>Link</span>
                        <strong className="mono">{link}</strong>
                      </div>
                    ))}
                  </div>

                  <div className="element-grid">
                    {state.page.elements.map((element) => (
                      <div className="element-card" key={`${element.selectorHint}-${element.tag}`}>
                        <span>{element.tag}</span>
                        <strong>{element.text}</strong>
                        <strong className="mono">{element.selectorHint}</strong>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </Panel>

          <div className="stack">
            <Panel title="Agent Activity" badge={`${state.activity.length} events`}>
              <div className="activity-feed">
                {state.activity.map((item) => (
                  <article
                    key={item.id}
                    className={`activity-card ${item.tone === "warm" ? "warm" : item.tone === "alert" ? "alert" : ""}`.trim()}
                  >
                    <span>{item.timestamp}</span>
                    <strong>{item.label}</strong>
                    <p>{item.detail}</p>
                  </article>
                ))}
              </div>
            </Panel>

            <Panel title="Agent Thoughts" badge="observe / plan / act / reflect">
              <div className="thought-stream">
                {state.thoughts.map((item) => (
                  <article className="thought-card" key={item.id}>
                    <span>
                      {item.agent} · {item.phase}
                    </span>
                    <p>{item.content}</p>
                  </article>
                ))}
              </div>
            </Panel>
          </div>
        </div>

        <div className="dashboard-grid" style={{ marginTop: 18 }}>
          <div className="stack">
            <Panel title="Actions Log" badge={`${state.actions.length} tracked`}>
              <div className="timeline">
                {state.actions.map((action) => (
                  <div className="timeline-item" key={action.id}>
                    <span>{action.status}</span>
                    <strong>{action.action}</strong>
                    <strong className="mono">{action.target}</strong>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel title="Memory" badge="working context">
              <div className="memory-list">
                {state.memory.map((item) => (
                  <div className="memory-card" key={item.id}>
                    <span>{item.key}</span>
                    <strong>{item.value}</strong>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel title="Task Status" badge={`${state.tasks.length} tasks`}>
              <div className="task-list">
                {state.tasks.map((task) => (
                  <article className={`task-card task-${task.status}`.trim()} key={task.id}>
                    <span>{task.status}</span>
                    <strong>{task.goal}</strong>
                    <p className="mono">
                      {task.id} · {task.assignedAgent}
                    </p>
                  </article>
                ))}
              </div>
            </Panel>

            <Panel title="Operator Queue" badge={`${state.interventions.length} pending`}>
              <div className="task-list">
                {state.interventions.map((intervention) => (
                  <article className={`task-card task-${intervention.state}`.trim()} key={intervention.id}>
                    <span>{intervention.state}</span>
                    <strong>{intervention.reason}</strong>
                    <p>{intervention.contextPreview}</p>
                    <p className="mono">
                      {intervention.runId}
                      {intervention.category ? ` · ${intervention.category}` : ""}
                    </p>
                    <div style={{ display: "grid", gap: 8 }}>
                      <input
                        value={interventionInputs[intervention.id] ?? ""}
                        onChange={(event) =>
                          setInterventionInputs((current) => ({
                            ...current,
                            [intervention.id]: event.target.value,
                          }))
                        }
                        placeholder="Operator input / hint"
                        style={{ padding: "10px 12px", borderRadius: 12, border: "1px solid rgba(255,255,255,0.12)" }}
                      />
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button
                          type="button"
                          onClick={() => state.approveIntervention(intervention.id)}
                        >
                          Approve
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            state.rejectIntervention(
                              intervention.id,
                              interventionInputs[intervention.id] || "Operator rejected run",
                            )
                          }
                        >
                          Reject
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            state.provideInterventionInput(intervention.id, {
                              note: interventionInputs[intervention.id] || "Operator input provided",
                            })
                          }
                        >
                          Provide Input
                        </button>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </Panel>

            <Panel title="Agent Budget" badge={`${state.budgets.length} agents`}>
              <div className="budget-list">
                {state.budgets.map((budget) => (
                  <article className="budget-card" key={budget.agent}>
                    <div className="budget-header">
                      <strong>{budget.agent}</strong>
                      <span>{budget.runtimeSeconds}s</span>
                    </div>
                    <div className="budget-metrics">
                      {budget.metrics.map((metric) => (
                        <div className="budget-metric" key={`${budget.agent}-${metric.label}`}>
                          <div className="budget-labels">
                            <span>{metric.label}</span>
                            <strong>
                              {metric.used}/{metric.limit}
                            </strong>
                          </div>
                          <div className="budget-bar">
                            <div className="budget-fill" style={{ width: `${metric.percent}%` }} />
                          </div>
                        </div>
                      ))}
                    </div>
                    <p className="budget-cost mono">
                      llm ${budget.llmCostEstimate.toFixed(4)} · tools ${budget.toolCostEstimate.toFixed(4)}
                    </p>
                    {budget.warnings.map((warning) => (
                      <p className="budget-warning" key={warning}>
                        {warning}
                      </p>
                    ))}
                  </article>
                ))}
              </div>
            </Panel>
          </div>

          <Panel title="A2A Messages" badge={`${a2aMessages} routed`}>
            <div className="messages">
              {state.messages.map((message) => (
                <article
                  className={`message-card ${message.kind === "a2a" ? "a2a" : "agent"}`.trim()}
                  key={message.id}
                >
                  <div className="message-route">
                    <span>{message.from}</span>
                    <strong>{message.to}</strong>
                  </div>
                  <p>{message.content}</p>
                </article>
              ))}
            </div>
            <p className="footer-note">
              Live updates subscribe to <span className="mono">/api/ws</span>. Set{" "}
              <span className="mono">NEXT_PUBLIC_SYNAPSE_WS_URL</span> to point this UI at a
              running Synapse backend, and provide auth with{" "}
              <span className="mono">NEXT_PUBLIC_SYNAPSE_BEARER_TOKEN</span> or{" "}
              <span className="mono">NEXT_PUBLIC_SYNAPSE_API_KEY</span>.
            </p>
          </Panel>
        </div>
      </div>
    </main>
  );
}

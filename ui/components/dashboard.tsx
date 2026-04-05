"use client";

import { useMemo, useState } from "react";
import { useSynapseFeed } from "@/hooks/use-synapse-feed";
import type { InterventionItem, RequestHealthItem, RunHealthItem, SynapseEvent, WorkerHealthItem } from "@/lib/types";

type OperatorTab = "queue" | "runs" | "backlog" | "events" | "health";

type QueueItem = {
  runId: string;
  goal: string;
  status: string;
  phase: string;
  attentionScore: number;
  attentionPriority: string;
  attentionAction: string;
  projectId?: string | null;
  topReason: string;
  operatorState: string;
  ageLabel: string;
};

type BacklogSubtype = {
  key: string;
  count: number;
  tone: "critical" | "warning" | "info";
};

export function Dashboard() {
  const state = useSynapseFeed();
  const [tab, setTab] = useState<OperatorTab>("queue");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [interventionInputs, setInterventionInputs] = useState<Record<string, string>>({});
  const queue = useMemo(() => buildQueue(state.runHealth, state.requestHealth, state.interventions), [state.runHealth, state.requestHealth, state.interventions]);
  const selectedRun = useMemo(
    () => queue.find((item) => item.runId === selectedRunId) ?? queue[0] ?? buildFallbackRun(state.runHealth[0]),
    [queue, selectedRunId, state.runHealth],
  );
  const selectedRequests = useMemo(
    () =>
      state.requestHealth
        .filter((item) => item.runId === selectedRun?.runId)
        .sort((left, right) => severityRank(right.healthState) - severityRank(left.healthState) || right.totalAgeSeconds - left.totalAgeSeconds),
    [state.requestHealth, selectedRun],
  );
  const selectedIntervention = useMemo(
    () => state.interventions.find((item) => item.runId === selectedRun?.runId && item.state === "pending") ?? null,
    [state.interventions, selectedRun],
  );
  const selectedEvents = useMemo(
    () => state.events.filter((item) => item.run_id === selectedRun?.runId).slice(0, 8),
    [state.events, selectedRun],
  );
  const backlogSubtypes = useMemo(() => buildBacklogSubtypes(state.requestHealth), [state.requestHealth]);
  const workerSummary = summarizeWorkers(state.workers);

  async function submitInput(interventionId: string) {
    const value = interventionInputs[interventionId]?.trim();
    if (!value) {
      return;
    }
    await state.provideInterventionInput(interventionId, { operator_input: value });
    setInterventionInputs((current) => ({ ...current, [interventionId]: "" }));
  }

  return (
    <main className="operator-shell">
      <header className="operator-topbar">
        <div className="operator-brand">
          <span className="operator-brand-mark">terminal</span>
          <div>
            <p className="operator-brand-kicker">Synapse Operator</p>
            <h1>SYNAPSE_OPERATOR_v1</h1>
          </div>
        </div>
        <div className="operator-topbar-meta">
          <span className={`status-pill ${state.authError ? "status-alert" : "status-ok"}`}>
            {state.authError ? "AUTH DEGRADED" : "LIVE FEED READY"}
          </span>
          <span className="status-pill status-muted">{workerSummary.summary}</span>
        </div>
      </header>

      <div className="operator-frame">
        <aside className="operator-sidebar">
          <div className="sidebar-section">
            <p className="sidebar-label">Core Runtime</p>
            {[
              ["queue", "Queue"],
              ["runs", "Runs"],
              ["backlog", "Backlog"],
              ["events", "Events"],
              ["health", "Health"],
            ].map(([value, label]) => (
              <button
                key={value}
                className={`sidebar-link ${tab === value ? "active" : ""}`}
                onClick={() => setTab(value as OperatorTab)}
                type="button"
              >
                <span>{label}</span>
              </button>
            ))}
          </div>
          <div className="sidebar-section sidebar-foot">
            <p className="sidebar-label">Cluster Status</p>
            <div className="sidebar-cluster">
              <strong>{workerSummary.healthyWorkers}/{state.workers.length || 0}</strong>
              <span>healthy workers</span>
            </div>
          </div>
        </aside>

        <section className="operator-content">
          {state.authError ? (
            <div className="operator-banner operator-banner-alert">
              <strong>Operator auth problem</strong>
              <span>{state.authError}</span>
            </div>
          ) : null}

          <div className="operator-pagehead">
            <div>
              <p className="page-kicker">{pageKicker(tab)}</p>
              <h2>{pageTitle(tab)}</h2>
              <p>{pageSubtitle(tab)}</p>
            </div>
            <div className="operator-stats">
              <div className="stat-card">
                <span>Waiting For Operator</span>
                <strong>{queue.length}</strong>
              </div>
              <div className="stat-card">
                <span>Tracked Requests</span>
                <strong>{state.requestHealth.length}</strong>
              </div>
              <div className="stat-card">
                <span>Interventions</span>
                <strong>{state.interventions.length}</strong>
              </div>
              <div className="stat-card">
                <span>Workers</span>
                <strong>{state.workers.length}</strong>
              </div>
            </div>
          </div>

          {tab === "queue" ? (
            <div className="operator-grid">
              <section className="panel queue-panel">
                <div className="panel-head">
                  <h3>Operator Review Queue</h3>
                  <span>{queue.length} pending</span>
                </div>
                <div className="queue-grid">
                  {queue.length ? (
                    queue.map((item) => (
                      <button
                        key={item.runId}
                        className={`queue-card ${selectedRun?.runId === item.runId ? "selected" : ""} ${toneClass(item.attentionPriority)}`}
                        onClick={() => setSelectedRunId(item.runId)}
                        type="button"
                      >
                        <div className="queue-card-head">
                          <span className={`priority-badge ${toneClass(item.attentionPriority)}`}>{item.attentionPriority}</span>
                          <span className="queue-age">{item.ageLabel}</span>
                        </div>
                        <strong>{item.runId}</strong>
                        <p>{item.goal}</p>
                        <dl>
                          <div>
                            <dt>Project</dt>
                            <dd>{item.projectId ?? "unknown"}</dd>
                          </div>
                          <div>
                            <dt>Attention</dt>
                            <dd>{item.attentionScore}</dd>
                          </div>
                        </dl>
                        <div className="queue-reason">
                          <span>Top reason</span>
                          <strong>{item.topReason}</strong>
                        </div>
                        <div className="queue-foot">
                          <span>{item.operatorState}</span>
                          <span>{item.attentionAction}</span>
                        </div>
                      </button>
                    ))
                  ) : (
                    <EmptyState title="No runs currently waiting for operator review." />
                  )}
                </div>
              </section>

              <RunDetailPanel
                run={selectedRun}
                requests={selectedRequests}
                intervention={selectedIntervention}
                events={selectedEvents}
                interventionInputs={interventionInputs}
                setInterventionInputs={setInterventionInputs}
                onApprove={state.approveIntervention}
                onReject={state.rejectIntervention}
                onProvideInput={submitInput}
              />
            </div>
          ) : null}

          {tab === "runs" ? (
            <section className="panel">
              <div className="panel-head">
                <h3>Live Runs</h3>
                <span>{state.runHealth.length} tracked</span>
              </div>
              <div className="table-shell">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Run</th>
                      <th>Status</th>
                      <th>Phase</th>
                      <th>Goal</th>
                      <th>Attention</th>
                      <th>Summary</th>
                    </tr>
                  </thead>
                  <tbody>
                    {state.runHealth.map((item) => (
                      <tr key={item.runId} onClick={() => { setSelectedRunId(item.runId); setTab("queue"); }}>
                        <td>{item.runId}</td>
                        <td><span className={`inline-state ${toneClass(item.attentionPriority)}`}>{item.status}</span></td>
                        <td>{item.phase}</td>
                        <td>{item.goal}</td>
                        <td>{item.attentionScore} · {item.attentionAction}</td>
                        <td>{item.summary}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}

          {tab === "backlog" ? (
            <div className="stack-grid">
              <section className="panel">
                <div className="panel-head">
                  <h3>Backlog Subtypes</h3>
                  <span>{backlogSubtypes.length} families</span>
                </div>
                <div className="subtype-grid">
                  {backlogSubtypes.map((item) => (
                    <article className={`subtype-card ${toneClass(item.tone)}`} key={item.key}>
                      <span>{item.key}</span>
                      <strong>{item.count}</strong>
                    </article>
                  ))}
                </div>
              </section>
              <section className="panel">
                <div className="panel-head">
                  <h3>Representative Requests</h3>
                  <span>top degraded shapes</span>
                </div>
                <div className="request-list">
                  {state.requestHealth
                    .filter((item) => item.healthState !== "completed")
                    .sort((left, right) => severityRank(right.healthState) - severityRank(left.healthState) || right.totalAgeSeconds - left.totalAgeSeconds)
                    .slice(0, 10)
                    .map((item) => (
                      <article className={`request-card ${toneClass(item.healthState)}`} key={item.id}>
                        <div className="request-card-head">
                          <strong>{item.action}</strong>
                          <span>{item.healthState}</span>
                        </div>
                        <p>{item.recoverySummary ?? item.statusReason ?? "No recovery summary available."}</p>
                        <small>
                          {item.runId} · {item.workerId} · {formatSeconds(item.totalAgeSeconds)}
                        </small>
                      </article>
                    ))}
                </div>
              </section>
            </div>
          ) : null}

          {tab === "events" ? (
            <section className="panel">
              <div className="panel-head">
                <h3>Recent Runtime Events</h3>
                <span>{state.events.length} loaded</span>
              </div>
              <div className="event-feed">
                {state.events.length ? (
                  state.events.map((event) => (
                    <article className={`event-row ${toneClass(event.event_type)}`} key={`${event.event_id ?? event.timestamp ?? event.event_type}`}>
                      <div>
                        <strong>{event.event_type}</strong>
                        <p>{event.run_id ?? event.agent_id ?? "runtime"}</p>
                      </div>
                      <span>{formatTimestamp(event.timestamp)}</span>
                    </article>
                  ))
                ) : (
                  <EmptyState title="No runtime events loaded yet." />
                )}
              </div>
            </section>
          ) : null}

          {tab === "health" ? (
            <div className="stack-grid">
              <section className="panel">
                <div className="panel-head">
                  <h3>Worker Health</h3>
                  <span>{state.workers.length} workers</span>
                </div>
                <div className="worker-grid">
                  {state.workers.map((worker) => (
                    <article className={`worker-card ${toneClass(worker.healthStatus)}`} key={worker.workerId}>
                      <div className="worker-card-head">
                        <strong>{worker.workerId}</strong>
                        <span>{worker.healthStatus}</span>
                      </div>
                      <p>{worker.queueName}</p>
                      <small>
                        {worker.status} · sessions {worker.activeSessions}
                      </small>
                    </article>
                  ))}
                </div>
              </section>
              <section className="panel">
                <div className="panel-head">
                  <h3>System Strip</h3>
                  <span>service-level</span>
                </div>
                <div className="health-strip">
                  <article className="health-item ok"><strong>Backend</strong><span>{state.authError ? "degraded" : "ready"}</span></article>
                  <article className={`health-item ${workerSummary.degradedWorkers ? "warn" : "ok"}`}><strong>Browser Runners</strong><span>{workerSummary.summary}</span></article>
                  <article className={`health-item ${state.interventions.length ? "warn" : "ok"}`}><strong>Operator Queue</strong><span>{queue.length} waiting</span></article>
                  <article className={`health-item ${backlogSubtypes.length ? "warn" : "ok"}`}><strong>Backlog</strong><span>{backlogSubtypes[0]?.key ?? "clear"}</span></article>
                </div>
              </section>
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}

function RunDetailPanel({
  run,
  requests,
  intervention,
  events,
  interventionInputs,
  setInterventionInputs,
  onApprove,
  onReject,
  onProvideInput,
}: {
  run: QueueItem | null;
  requests: RequestHealthItem[];
  intervention: InterventionItem | null;
  events: SynapseEvent[];
  interventionInputs: Record<string, string>;
  setInterventionInputs: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  onApprove: (interventionId: string) => Promise<void>;
  onReject: (interventionId: string, reason?: string) => Promise<void>;
  onProvideInput: (interventionId: string) => Promise<void>;
}) {
  if (!run) {
    return (
      <section className="panel detail-panel">
        <div className="panel-head">
          <h3>Run Detail</h3>
        </div>
        <EmptyState title="Select a run to inspect request health and operator actions." />
      </section>
    );
  }

  const counts = summarizeRequestCounts(requests);

  return (
    <section className="panel detail-panel">
      <div className="panel-head">
        <div>
          <h3>{run.runId}</h3>
          <p className="detail-subtitle">{run.status} · {run.phase}</p>
        </div>
        <span className={`priority-badge ${toneClass(run.attentionPriority)}`}>{run.attentionAction}</span>
      </div>

      <div className="summary-grid">
        {[
          ["slow", counts.slow],
          ["stuck", counts.stuck],
          ["abandoned", counts.abandoned],
          ["operator_required", counts.operatorRequired],
          ["unresolved", counts.unresolved],
        ].map(([label, value]) => (
          <article className="summary-tile" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </article>
        ))}
      </div>

      <div className="detail-layout">
        <div className="detail-main">
          <div className="detail-block">
            <h4>Request Health</h4>
            <div className="table-shell">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Action</th>
                    <th>Health</th>
                    <th>Recovery</th>
                    <th>Status Reason</th>
                    <th>Timing</th>
                  </tr>
                </thead>
                <tbody>
                  {requests.length ? (
                    requests.map((item) => (
                      <tr key={item.id}>
                        <td>{item.action}</td>
                        <td><span className={`inline-state ${toneClass(item.healthState)}`}>{item.healthState}</span></td>
                        <td>{item.recoveryClass}{item.recoverySummary ? ` · ${item.recoverySummary}` : ""}</td>
                        <td>{item.statusReason ?? "—"}</td>
                        <td>{formatRequestTiming(item)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={5}>No request health records loaded for this run.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="detail-block">
            <h4>Recent Runtime Events</h4>
            <div className="event-feed compact">
              {events.length ? (
                events.map((event) => (
                  <article className={`event-row ${toneClass(event.event_type)}`} key={`${event.event_id ?? event.timestamp ?? event.event_type}`}>
                    <div>
                      <strong>{event.event_type}</strong>
                      <p>{event.agent_id ?? event.source ?? "runtime"}</p>
                    </div>
                    <span>{formatTimestamp(event.timestamp)}</span>
                  </article>
                ))
              ) : (
                <EmptyState title="No recent runtime events loaded for this run." />
              )}
            </div>
          </div>
        </div>

        <aside className="detail-side">
          <div className="detail-block action-block">
            <h4>Operator Actions</h4>
            {intervention ? (
              <>
                <p className="action-reason">{intervention.reason}</p>
                <p className="action-context">{intervention.contextPreview}</p>
                <textarea
                  className="operator-input"
                  placeholder="Provide operator input for this run"
                  value={interventionInputs[intervention.id] ?? ""}
                  onChange={(event) =>
                    setInterventionInputs((current) => ({ ...current, [intervention.id]: event.target.value }))
                  }
                />
                <div className="action-row">
                  <button className="action-btn primary" onClick={() => onApprove(intervention.id)} type="button">Approve</button>
                  <button className="action-btn danger" onClick={() => onReject(intervention.id)} type="button">Reject</button>
                </div>
                <div className="action-row">
                  <button className="action-btn secondary" onClick={() => onProvideInput(intervention.id)} type="button">Provide Input</button>
                </div>
              </>
            ) : (
              <EmptyState title="No pending intervention is attached to this run." />
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}

function EmptyState({ title }: { title: string }) {
  return <div className="empty-state">{title}</div>;
}

function summarizeRequestCounts(requests: RequestHealthItem[]) {
  return {
    slow: requests.filter((item) => item.healthState === "slow").length,
    stuck: requests.filter((item) => item.healthState === "stuck").length,
    abandoned: requests.filter((item) => item.healthState === "abandoned").length,
    operatorRequired: requests.filter((item) => item.healthState === "operator_required").length,
    unresolved: requests.filter((item) => item.active || item.healthState === "stuck" || item.healthState === "operator_required").length,
  };
}

function summarizeWorkers(workers: WorkerHealthItem[]) {
  const healthyWorkers = workers.filter((item) => item.healthStatus === "healthy").length;
  const degradedWorkers = workers.filter((item) => item.healthStatus !== "healthy").length;
  return {
    healthyWorkers,
    degradedWorkers,
    summary: workers.length ? `${healthyWorkers} healthy / ${degradedWorkers} degraded` : "no worker data",
  };
}

function buildQueue(runHealth: RunHealthItem[], requestHealth: RequestHealthItem[], interventions: InterventionItem[]): QueueItem[] {
  return runHealth
    .filter((item) => item.healthState === "needs_operator" || item.status === "waiting_for_operator")
    .map((item) => {
      const intervention = interventions.find((entry) => entry.runId === item.runId && entry.state === "pending");
      const requests = requestHealth.filter((entry) => entry.runId === item.runId);
      const topRequest = requests.sort((left, right) => severityRank(right.healthState) - severityRank(left.healthState) || right.totalAgeSeconds - left.totalAgeSeconds)[0];
      const ageSource = intervention?.createdAt ?? topRequest?.updatedAt ?? topRequest?.startedAt ?? null;
      return {
        runId: item.runId,
        goal: item.goal,
        status: item.status,
        phase: item.phase,
        attentionScore: item.attentionScore,
        attentionPriority: item.attentionPriority,
        attentionAction: item.attentionAction,
        projectId: intervention?.projectId ?? null,
        topReason: intervention?.reason ?? topRequest?.recoverySummary ?? topRequest?.statusReason ?? item.summary,
        operatorState: intervention?.state ?? item.status,
        ageLabel: relativeAge(ageSource),
      };
    })
    .sort((left, right) => severityRank(right.attentionPriority) - severityRank(left.attentionPriority) || right.attentionScore - left.attentionScore);
}

function buildFallbackRun(item?: RunHealthItem): QueueItem | null {
  if (!item) {
    return null;
  }
  return {
    runId: item.runId,
    goal: item.goal,
    status: item.status,
    phase: item.phase,
    attentionScore: item.attentionScore,
    attentionPriority: item.attentionPriority,
    attentionAction: item.attentionAction,
    projectId: null,
    topReason: item.summary,
    operatorState: item.status,
    ageLabel: "live",
  };
}

function buildBacklogSubtypes(requests: RequestHealthItem[]): BacklogSubtype[] {
  const counts = new Map<string, number>();
  for (const request of requests) {
    if (request.healthState === "completed") {
      continue;
    }
    const key = backlogSubtypeLabel(request);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([key, count]) => ({
      key,
      count,
      tone: (key.includes("bootstrap") || key.includes("operator")
        ? "critical"
        : key.includes("progress") || key.includes("timeout")
          ? "warning"
          : "info") as BacklogSubtype["tone"],
    }))
    .sort((left, right) => right.count - left.count);
}

function backlogSubtypeLabel(request: RequestHealthItem) {
  const reason = `${request.recoverySummary ?? ""} ${request.statusReason ?? ""}`.toLowerCase();
  const action = request.action.toLowerCase();
  if (reason.includes("ownership conflict")) return "ownership_conflict";
  if (reason.includes("lease moved") || reason.includes("lease is no longer present")) return "lease_moved";
  if (reason.includes("did not start on a worker") || reason.includes("has not started on a worker")) return "bootstrap_not_started";
  if (reason.includes("bootstrap stalled") || reason.includes("bootstrap exceeded")) return "bootstrap_stalled";
  if (reason.includes("reported no durable progress")) return "started_no_durable_progress";
  if (reason.includes("stopped reporting durable progress") || reason.includes("repeated progress heartbeats")) return "progress_heartbeat_stall";
  if (reason.includes("without a durable result")) return request.startedAt ? "generic_timeout_after_start" : "generic_timeout_before_start";
  if (request.healthState === "operator_required" && action === "create_session") return "bootstrap_operator_review";
  if (action === "create_session") return "bootstrap_other";
  return `${request.healthState}_other`;
}

function pageTitle(tab: OperatorTab) {
  switch (tab) {
    case "queue":
      return "Operator Review Queue";
    case "runs":
      return "Run Inspection";
    case "backlog":
      return "Backlog Patterns";
    case "events":
      return "Runtime Events";
    case "health":
      return "System Health";
  }
}

function pageSubtitle(tab: OperatorTab) {
  switch (tab) {
    case "queue":
      return "Prioritize waiting_for_operator runs, inspect request-health detail, and take bounded intervention actions.";
    case "runs":
      return "Track live runs with attention scoring, request degradation, and delegation state.";
    case "backlog":
      return "Surface the dominant unresolved/operator-review families before we pick the next runtime fix.";
    case "events":
      return "Scan the freshest runtime signals flowing through the control plane.";
    case "health":
      return "Quick operational read on worker health and queue pressure.";
  }
}

function pageKicker(tab: OperatorTab) {
  return {
    queue: "queue / triage / act",
    runs: "runs / inspect / compare",
    backlog: "backlog / subtype / diagnose",
    events: "events / stream / context",
    health: "health / workers / runtime",
  }[tab];
}

function toneClass(value: string) {
  const normalized = value.toLowerCase();
  if (normalized.includes("urgent") || normalized.includes("high") || normalized.includes("error") || normalized.includes("operator") || normalized.includes("stuck")) {
    return "tone-critical";
  }
  if (normalized.includes("medium") || normalized.includes("warning") || normalized.includes("slow") || normalized.includes("timeout")) {
    return "tone-warning";
  }
  if (normalized.includes("healthy") || normalized.includes("ok") || normalized.includes("low")) {
    return "tone-ok";
  }
  return "tone-info";
}

function severityRank(value: string) {
  const normalized = value.toLowerCase();
  if (normalized.includes("urgent") || normalized.includes("operator") || normalized.includes("stuck")) return 4;
  if (normalized.includes("high") || normalized.includes("warning") || normalized.includes("slow")) return 3;
  if (normalized.includes("medium") || normalized.includes("watch")) return 2;
  if (normalized.includes("healthy") || normalized.includes("low")) return 1;
  return 0;
}

function relativeAge(value?: string | null) {
  if (!value) return "live";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "live";
  const diff = Math.max(0, Date.now() - date.getTime());
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(minutes / 60);
  if (hours > 0) return `${hours}h ${minutes % 60}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${Math.floor(diff / 1000)}s`;
}

function formatSeconds(value?: number | null) {
  if (value === null || value === undefined) return "—";
  if (value >= 60) return `${Math.round(value / 60)}m`;
  return `${Math.round(value)}s`;
}

function formatTimestamp(value?: string) {
  if (!value) return "live";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatRequestTiming(item: RequestHealthItem) {
  return [
    `total ${formatSeconds(item.totalAgeSeconds)}`,
    item.startedAt ? `started ${relativeAge(item.startedAt)} ago` : "not started",
    item.lastProgressAt ? `progress ${relativeAge(item.lastProgressAt)} ago` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

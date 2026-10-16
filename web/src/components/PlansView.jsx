import { useCallback, useEffect, useRef, useState } from "react";
import { api, timeAgo, ws } from "../api.js";
import { Icon } from "./Icons.jsx";

const STATUS_LABEL = {
  approved: "queued",
  running: "running",
  completed: "completed",
  finished_with_issues: "finished with issues",
};

export default function PlansView({ workspace, onError, notify, me, openConnect }) {
  const locked = me && !me.plans_allowed;
  const [goal, setGoal] = useState("");
  const [feedback, setFeedback] = useState("");
  const [draft, setDraft] = useState(null);
  const [drafting, setDrafting] = useState(false);
  const [projects, setProjects] = useState([]);
  const [selectedId, setSelectedId] = useState(null);

  const loadProjects = useCallback(async () => {
    try {
      const list = await api.get(`${ws(workspace)}/plans`);
      setProjects(list);
      return list;
    } catch (e) {
      onError(e);
      return [];
    }
  }, [workspace, onError]);

  useEffect(() => {
    loadProjects().then((list) => setSelectedId((id) => id || list[0]?.id || null));
  }, [loadProjects]);

  // Keep the list's progress bars moving while anything runs.
  useEffect(() => {
    if (!projects.some((p) => p.running)) return;
    const t = setInterval(loadProjects, 3000);
    return () => clearInterval(t);
  }, [projects, loadProjects]);

  async function makeDraft(withFeedback) {
    if (!goal.trim()) return;
    setDrafting(true);
    try {
      setDraft(await api.post(`${ws(workspace)}/plans/draft`, { goal, feedback: withFeedback ? feedback : "" }));
      setFeedback("");
    } catch (e) {
      onError(e);
    } finally {
      setDrafting(false);
    }
  }

  async function approve() {
    try {
      const { project_id } = await api.post(`${ws(workspace)}/plans`, { goal, plan: draft });
      setDraft(null);
      setGoal("");
      await loadProjects();
      setSelectedId(project_id);
      notify("Plan approved — executing in the background", "success");
    } catch (e) {
      onError(e);
    }
  }

  function editTask(i, description) {
    setDraft((d) => ({ ...d, tasks: d.tasks.map((t, j) => (j === i ? { ...t, description } : t)) }));
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Plans</h1>
          <p className="muted">
            For bigger goals: the planner breaks a goal into a dependency graph of tasks; you review and edit it; then
            each task runs with its own least-privilege agent, is checked by an LLM judge, and retries on failure.
            Progress survives restarts.
          </p>
        </div>
      </header>

      <section className="card">
        <h2>New plan</h2>
        {locked && (
          <div className="banner warn">
            <Icon name="lock" />{" "}
            <span>
              Plans run many model calls, so they need your own API key.{" "}
              <button className="link" onClick={() => openConnect()}>
                Connect your AI
              </button>{" "}
              — Gemini keys are free.
            </span>
          </div>
        )}
        <textarea
          className="goal"
          rows={3}
          placeholder="e.g. Add a REST API with FastAPI for the task service, with tests"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
        />
        <div className="row gap-s">
          <button className="btn primary" onClick={() => makeDraft(false)} disabled={locked || drafting || !goal.trim()}>
            <Icon name="sparkle" size={14} /> {drafting ? "Planning…" : draft ? "Re-plan from scratch" : "Draft plan"}
          </button>
          {drafting && <span className="muted small">This takes 10–60 seconds.</span>}
        </div>
      </section>

      {draft && (
        <section className="card draft">
          <div className="row between">
            <div>
              <h2>{draft.project_name}</h2>
              <p className="muted small">
                {draft.goal_summary} · {draft.tech_stack.join(", ")} · est. {draft.total_estimated_hours}h
              </p>
            </div>
            <button className="btn primary" onClick={approve}>
              <Icon name="play" size={14} /> Approve & run
            </button>
          </div>

          <div className="task-list">
            {draft.tasks.map((t, i) => (
              <details key={t.id} className="task-row">
                <summary>
                  <span className="task-id">{t.id}</span>
                  <span className={`type-badge ${t.task_type}`}>{t.task_type}</span>
                  <span className="task-title">{t.title}</span>
                  {t.depends_on.length > 0 && <span className="muted small">after {t.depends_on.join(", ")}</span>}
                </summary>
                <div className="task-detail">
                  <label className="field">
                    <span>Description (editable)</span>
                    <textarea rows={4} value={t.description} onChange={(e) => editTask(i, e.target.value)} />
                  </label>
                  <div className="two-col">
                    <div>
                      <div className="tool-label">Output files</div>
                      <ul>
                        {t.output_files.map((f) => (
                          <li key={f}>
                            <code>{f}</code>
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <div className="tool-label">Acceptance criteria</div>
                      <ul>
                        {t.acceptance_criteria.map((c) => (
                          <li key={c}>{c}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </div>
              </details>
            ))}
          </div>

          {draft.risks.length > 0 && (
            <div className="risks">
              <div className="tool-label">Risks</div>
              <ul>
                {draft.risks.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="replan">
            <input
              placeholder="Not quite right? Describe what should change…"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
            />
            <button className="btn" onClick={() => makeDraft(true)} disabled={drafting || !feedback.trim()}>
              Reject & re-plan
            </button>
          </div>
        </section>
      )}

      {projects.length > 0 && (
        <div className="plans-layout">
          <section className="card project-list">
            <h2>Runs</h2>
            {projects.map((p) => {
              const total = Object.values(p.progress).reduce((a, b) => a + b, 0) || 1;
              const done = p.progress.completed || 0;
              return (
                <button
                  key={p.id}
                  className={`project ${p.id === selectedId ? "active" : ""}`}
                  onClick={() => setSelectedId(p.id)}
                >
                  <div className="row between">
                    <b>{p.name}</b>
                    <span className={`status-badge ${p.running ? "running" : p.status}`}>
                      {p.running ? "running" : STATUS_LABEL[p.status] || p.status}
                    </span>
                  </div>
                  <div className="progress">
                    <span style={{ width: `${(done / total) * 100}%` }} />
                  </div>
                  <div className="muted small">
                    {done}/{total} tasks · {timeAgo(p.created_at)}
                  </div>
                </button>
              );
            })}
          </section>
          {selectedId && (
            <ProjectDetail
              key={selectedId}
              workspace={workspace}
              projectId={selectedId}
              onError={onError}
              onChanged={loadProjects}
              onDeleted={() => {
                setSelectedId(null);
                loadProjects();
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

function ProjectDetail({ workspace, projectId, onError, onChanged, onDeleted }) {
  const [data, setData] = useState(null);
  const logRef = useRef(null);

  const load = useCallback(async () => {
    try {
      setData(await api.get(`${ws(workspace)}/plans/${projectId}`));
    } catch (e) {
      onError(e);
    }
  }, [workspace, projectId, onError]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!data?.running) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [data?.running, load]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [data?.logs?.length]);

  async function resume(retryFailed) {
    try {
      await api.post(`${ws(workspace)}/plans/${projectId}/resume`, { retry_failed: retryFailed });
      await load();
      onChanged();
    } catch (e) {
      onError(e);
    }
  }

  async function remove() {
    if (!confirm("Delete this plan run? Files it created stay in the workspace.")) return;
    try {
      await api.del(`${ws(workspace)}/plans/${projectId}`);
      onDeleted();
    } catch (e) {
      onError(e);
    }
  }

  if (!data) return <section className="card">Loading…</section>;
  const hasProblems = (data.progress.failed || 0) + (data.progress.blocked || 0) > 0;
  const unfinished = (data.progress.pending || 0) + (data.progress.in_progress || 0) > 0;

  return (
    <section className="card project-detail">
      <div className="row between">
        <div>
          <h2>{data.name}</h2>
          <p className="muted small">{data.goal}</p>
        </div>
        <div className="row gap-s">
          {!data.running && unfinished && (
            <button className="btn primary small" onClick={() => resume(false)}>
              <Icon name="play" size={14} /> Resume
            </button>
          )}
          {!data.running && hasProblems && (
            <button className="btn small" onClick={() => resume(true)}>
              <Icon name="refresh" size={14} /> Retry failed
            </button>
          )}
          {!data.running && (
            <button className="icon-btn danger" onClick={remove} title="Delete run" aria-label="Delete run">
              <Icon name="trash" />
            </button>
          )}
        </div>
      </div>

      <div className="task-list">
        {data.tasks.map((t) => (
          <details key={t.id} className="task-row">
            <summary>
              <span className={`status-dot ${t.status}`} title={t.status} />
              <span className="task-id">{t.id}</span>
              <span className={`type-badge ${t.task_type}`}>{t.task_type}</span>
              <span className="task-title">{t.title}</span>
              <span className="muted small">
                {t.status.replace("_", " ")}
                {t.retry_count > 0 ? ` · retry ${t.retry_count}/${t.max_retries}` : ""}
              </span>
            </summary>
            <div className="task-detail">
              <p>{t.description}</p>
              {t.error && <div className="banner error small">{t.error}</div>}
              {t.result && (
                <>
                  <div className="tool-label">Agent summary</div>
                  <pre>{t.result}</pre>
                </>
              )}
            </div>
          </details>
        ))}
      </div>

      {data.logs.length > 0 && (
        <>
          <div className="tool-label">Live log</div>
          <div className="log" ref={logRef}>
            {data.logs.map((l, i) => (
              <div key={i}>
                <span className="muted">{new Date(l.t * 1000).toLocaleTimeString()}</span> {l.msg}
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

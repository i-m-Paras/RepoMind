import { Icon } from "./Icons.jsx";
import { timeAgo } from "../api.js";

const VIEWS = [
  { id: "chat", label: "Chat", icon: "chat" },
  { id: "files", label: "Files", icon: "files" },
  { id: "index", label: "Index", icon: "index" },
  { id: "plans", label: "Plans", icon: "plans" },
  { id: "skills", label: "Skills", icon: "skills" },
];

export default function Sidebar(props) {
  const { open, onClose, info, workspaces, current, onSelect, onNewWorkspace, onDeleteWorkspace } = props;
  const { view, onView, status, sessions, sessionId, onSession, onNewSession, onDeleteSession, me, onConnect } = props;
  const index = status?.index;

  return (
    <>
      {open && <div className="scrim" onClick={onClose} />}
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand">
          <div className="brand-mark">R</div>
          <div>
            <div className="brand-name">{info?.name || "RepoMind"}</div>
            <div className="brand-sub">AI coding agent</div>
          </div>
        </div>

        <div className="section">
          <label className="section-label" htmlFor="workspace-select">
            Workspace
          </label>
          <div className="workspace-row">
            <select
              id="workspace-select"
              value={current || ""}
              onChange={(e) => onSelect(e.target.value)}
              disabled={!workspaces.length}
            >
              {!workspaces.length && <option value="">No workspaces</option>}
              {workspaces.map((w) => (
                <option key={w.name} value={w.name}>
                  {w.name}
                </option>
              ))}
            </select>
            <button className="icon-btn" title="New workspace" onClick={onNewWorkspace} aria-label="New workspace">
              <Icon name="plus" />
            </button>
            {current && (
              <button
                className="icon-btn danger"
                title="Delete workspace"
                onClick={() => onDeleteWorkspace(current)}
                aria-label="Delete workspace"
              >
                <Icon name="trash" />
              </button>
            )}
          </div>
          {current && index && (
            <div className={`index-pill ${index.state}`}>
              <span className="dot" />
              {index.state === "indexing"
                ? "Indexing…"
                : index.state === "error"
                ? "Index error"
                : `${status.chunks} chunks indexed`}
            </div>
          )}
        </div>

        <nav className="nav">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              className={`nav-item ${view === v.id ? "active" : ""}`}
              onClick={() => onView(v.id)}
              disabled={!current}
            >
              <Icon name={v.icon} />
              {v.label}
            </button>
          ))}
        </nav>

        {current && view === "chat" && (
          <div className="section sessions">
            <div className="section-head">
              <span className="section-label">Chats</span>
              <button className="btn ghost small" onClick={onNewSession}>
                <Icon name="plus" size={14} /> New
              </button>
            </div>
            <div className="session-list">
              {sessions.length === 0 && <div className="muted small pad">No chats yet</div>}
              {sessions.map((s) => (
                <div key={s.id} className={`session ${s.id === sessionId ? "active" : ""}`}>
                  <button className="session-main" onClick={() => onSession(s.id)} title={s.title}>
                    <span className="session-title">{s.title}</span>
                    <span className="session-time">{timeAgo(s.updated_at)}</span>
                  </button>
                  <button
                    className="icon-btn danger subtle"
                    onClick={() => onDeleteSession(s.id)}
                    aria-label="Delete chat"
                    title="Delete chat"
                  >
                    <Icon name="trash" size={14} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="sidebar-footer">
          {me && (me.limited || me.own_key || me.github || info?.demo_mode) && (
            <div className={`plan-badge ${me.own_key ? "own" : ""}`}>
              <div className="row between">
                <b>{me.own_key ? "Your key" : "Free"}</b>
                <button className="btn ghost small" onClick={onConnect}>
                  {me.own_key ? "Change" : "Upgrade"}
                </button>
              </div>
              <div className="muted small">
                {me.limited
                  ? `${me.chats_left} of ${me.chat_limit} free messages left today`
                  : `${me.provider} / ${me.model}`}
                {me.github ? " · GitHub connected" : ""}
              </div>
            </div>
          )}
          {info && (
            <>
              <div title="Chat model">
                <Icon name="sparkle" size={13} /> {me?.own_key ? `${me.provider} / ${me.model}` : info.llm}
              </div>
              <div title="Vector store">
                <Icon name="index" size={13} /> {info.vector_store} · {info.rag_mode} RAG
              </div>
            </>
          )}
        </div>
      </aside>
    </>
  );
}

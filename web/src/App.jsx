import { useCallback, useEffect, useState } from "react";
import { api, ApiError, setConnection, setToken, ws } from "./api.js";
import Sidebar from "./components/Sidebar.jsx";
import ChatView from "./components/ChatView.jsx";
import FilesView from "./components/FilesView.jsx";
import IndexView from "./components/IndexView.jsx";
import PlansView from "./components/PlansView.jsx";
import SkillsView from "./components/SkillsView.jsx";
import NewWorkspaceModal from "./components/NewWorkspaceModal.jsx";
import LoginGate from "./components/LoginGate.jsx";
import ConnectModal from "./components/ConnectModal.jsx";
import { Icon } from "./components/Icons.jsx";

const WS_KEY = "repomind.workspace";
const VIEWS = ["chat", "files", "index", "plans", "skills"];

// The active view lives in the URL hash (#/files), so views can be linked and Back works.
function viewFromHash() {
  const view = window.location.hash.replace(/^#\/?/, "");
  return VIEWS.includes(view) ? view : "chat";
}

function remembered() {
  try {
    return localStorage.getItem(WS_KEY);
  } catch {
    return null;
  }
}

export default function App() {
  const [info, setInfo] = useState(null);
  const [needsLogin, setNeedsLogin] = useState(false);
  const [workspaces, setWorkspaces] = useState(null);
  const [current, setCurrent] = useState(remembered());
  const [view, setViewState] = useState(viewFromHash);
  const [status, setStatus] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [toast, setToast] = useState(null);
  const [showNew, setShowNew] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [me, setMe] = useState(null);
  const [connect, setConnect] = useState(null); // null | {reason}

  const setView = useCallback((next) => {
    window.location.hash = `/${next}`;
  }, []);

  useEffect(() => {
    const onHash = () => setViewState(viewFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const notify = useCallback((message, kind = "error") => {
    setToast({ message, kind, id: Date.now() });
  }, []);

  // Central error handler: 401 → token prompt, 402 (free allowance used up) → connect your key, else a toast.
  const onError = useCallback(
    (e) => {
      if (e instanceof ApiError && e.status === 401) setNeedsLogin(true);
      else if (e instanceof ApiError && e.status === 402) setConnect({ reason: e.message });
      else notify(e.message || String(e));
    },
    [notify]
  );

  const refreshMe = useCallback(async () => {
    try {
      setMe(await api.get("/api/me"));
    } catch (e) {
      onError(e);
    }
  }, [onError]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    api.get("/api/info").then(setInfo).catch(onError);
  }, [onError]);

  const loadWorkspaces = useCallback(async () => {
    try {
      const list = await api.get("/api/workspaces");
      setWorkspaces(list);
      setNeedsLogin(false);
      return list;
    } catch (e) {
      onError(e);
      return [];
    }
  }, [onError]);

  useEffect(() => {
    if (info) {
      loadWorkspaces();
      refreshMe();
    }
  }, [info, loadWorkspaces, refreshMe]);

  // Keep the selected workspace valid.
  useEffect(() => {
    if (!workspaces) return;
    if (!current || !workspaces.some((w) => w.name === current)) {
      setCurrent(workspaces[0]?.name || null);
    }
  }, [workspaces, current]);

  useEffect(() => {
    try {
      if (current) localStorage.setItem(WS_KEY, current);
    } catch {
      /* ignore */
    }
    setSessionId(null);
    setSessions([]);
    setStatus(null);
  }, [current]);

  const refreshStatus = useCallback(async () => {
    if (!current) return;
    try {
      setStatus(await api.get(`${ws(current)}/status`));
    } catch (e) {
      onError(e);
    }
  }, [current, onError]);

  const refreshSessions = useCallback(async () => {
    if (!current) return [];
    try {
      const list = await api.get(`${ws(current)}/sessions`);
      setSessions(list);
      return list;
    } catch (e) {
      onError(e);
      return [];
    }
  }, [current, onError]);

  useEffect(() => {
    if (!current) return;
    refreshStatus();
    refreshSessions().then((list) => setSessionId((id) => id || list[0]?.id || null));
  }, [current, refreshStatus, refreshSessions]);

  // Poll the index status while indexing runs in the background.
  useEffect(() => {
    if (status?.index?.state !== "indexing") return;
    const t = setInterval(refreshStatus, 2000);
    return () => clearInterval(t);
  }, [status, refreshStatus]);

  async function newSession() {
    try {
      const session = await api.post(`${ws(current)}/sessions`);
      await refreshSessions();
      setSessionId(session.id);
      setView("chat");
    } catch (e) {
      onError(e);
    }
  }

  async function deleteSession(id) {
    try {
      await api.del(`${ws(current)}/sessions/${id}`);
      const list = await refreshSessions();
      if (id === sessionId) setSessionId(list[0]?.id || null);
    } catch (e) {
      onError(e);
    }
  }

  async function createWorkspace(body) {
    await api.post("/api/workspaces", body);
    await loadWorkspaces();
    setCurrent(body.name);
    setView(body.git_url ? "chat" : "files");
    setShowNew(false);
  }

  async function deleteWorkspace(name) {
    if (!confirm(`Delete workspace "${name}" and all its files, index and chats?`)) return;
    try {
      await api.del(ws(name));
      await loadWorkspaces();
    } catch (e) {
      onError(e);
    }
  }

  if (needsLogin) {
    return (
      <LoginGate
        onSubmit={async (token) => {
          setToken(token);
          setNeedsLogin(false);
          await loadWorkspaces();
        }}
      />
    );
  }

  const openConnect = (reason = null) => setConnect({ reason });
  const shared = { workspace: current, info, me, status, onError, notify, refreshStatus, openConnect };

  return (
    <div className="app">
      <Sidebar
        open={navOpen}
        onClose={() => setNavOpen(false)}
        info={info}
        workspaces={workspaces || []}
        current={current}
        onSelect={(name) => setCurrent(name)}
        onNewWorkspace={() => setShowNew(true)}
        onDeleteWorkspace={deleteWorkspace}
        view={view}
        onView={(v) => {
          setView(v);
          setNavOpen(false);
        }}
        status={status}
        sessions={sessions}
        sessionId={sessionId}
        onSession={(id) => {
          setSessionId(id);
          setView("chat");
          setNavOpen(false);
        }}
        onNewSession={newSession}
        onDeleteSession={deleteSession}
        me={me}
        onConnect={() => openConnect()}
      />

      <main className="main">
        <div className="mobile-bar">
          <button className="icon-btn" onClick={() => setNavOpen(true)} aria-label="Open menu">
            <Icon name="menu" />
          </button>
          <span className="mobile-title">{current || "RepoMind"}</span>
        </div>

        {info && !info.api_key_set && (
          <div className="banner warn">
            <Icon name="alert" /> <span>
              <b>GEMINI_API_KEY</b> is not set on the server. Add it to <code>.env</code> and restart — chat,
              indexing and plans need it.
            </span>
          </div>
        )}

        {workspaces && workspaces.length === 0 ? (
          <Welcome onCreate={() => setShowNew(true)} />
        ) : !current ? null : view === "chat" ? (
          <ChatView
            key={current}
            {...shared}
            sessionId={sessionId}
            onSessionCreated={(id) => {
              setSessionId(id);
              refreshSessions();
            }}
            onTurnFinished={() => {
              refreshSessions();
              refreshMe();
            }}
            goTo={setView}
          />
        ) : view === "files" ? (
          <FilesView key={current} {...shared} />
        ) : view === "index" ? (
          <IndexView key={current} {...shared} />
        ) : view === "plans" ? (
          <PlansView key={current} {...shared} />
        ) : (
          <SkillsView key={current} {...shared} />
        )}
      </main>

      {showNew && <NewWorkspaceModal onClose={() => setShowNew(false)} onCreate={createWorkspace} />}

      {connect && (
        <ConnectModal
          info={info}
          me={me}
          reason={connect.reason}
          onClose={() => setConnect(null)}
          onSave={(conn) => {
            setConnection(conn);
            setConnect(null);
            refreshMe();
            refreshStatus();
            notify(conn ? "Connected — RepoMind now uses your key" : "Disconnected — back to the free allowance", "success");
          }}
        />
      )}

      {toast && (
        <div key={toast.id} className={`toast ${toast.kind}`} role="status">
          <Icon name={toast.kind === "error" ? "alert" : "check"} />
          <span>{toast.message}</span>
          <button className="icon-btn" onClick={() => setToast(null)} aria-label="Dismiss">
            <Icon name="x" />
          </button>
        </div>
      )}
    </div>
  );
}

function Welcome({ onCreate }) {
  return (
    <div className="empty-state welcome">
      <div className="brand-mark large">R</div>
      <h1>Welcome to RepoMind</h1>
      <p>
        An AI coding agent for your codebase. Create a workspace, add your code (upload files, a folder or a zip,
        or clone a public Git repo), and RepoMind indexes it so you can ask questions, make changes and run
        multi-step plans.
      </p>
      <button className="btn primary" onClick={onCreate}>
        <Icon name="plus" /> Create your first workspace
      </button>
    </div>
  );
}

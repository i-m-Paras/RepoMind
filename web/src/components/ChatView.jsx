import { useEffect, useRef, useState } from "react";
import { api, streamEvents, ws } from "../api.js";
import { Icon } from "./Icons.jsx";
import Markdown from "./Markdown.jsx";

const SUGGESTIONS = [
  "Give me a tour of this codebase: main modules and how they fit together.",
  "How does authentication work? Cite files and line numbers.",
  "Review the most important module for bugs and security issues.",
  "Write unit tests for the core business logic.",
];

// Apply one streamed event to the assistant turn it belongs to (immutably).
function applyEvent(turn, event) {
  const parts = [...turn.parts];
  const last = parts[parts.length - 1];
  switch (event.type) {
    case "token":
      if (last?.kind === "text") parts[parts.length - 1] = { ...last, text: last.text + event.text };
      else parts.push({ kind: "text", text: event.text });
      return { ...turn, parts };
    case "tool_call":
      // After an approval the same call is re-emitted; keep one card per call id.
      if (parts.some((p) => p.kind === "tool" && p.id === event.id)) return turn;
      parts.push({ kind: "tool", id: event.id, name: event.name, args: event.args });
      return { ...turn, parts };
    case "tool_result":
      return {
        ...turn,
        parts: parts.map((p) =>
          p.kind === "tool" && p.id === event.id ? { ...p, result: event.content, status: event.status } : p
        ),
      };
    case "done": {
      const text = (event.text || "").trim();
      const lastText = [...parts].reverse().find((p) => p.kind === "text");
      if (text && (!lastText || lastText.text.trim() !== text)) {
        if (last?.kind === "text") parts[parts.length - 1] = { kind: "text", text: event.text };
        else parts.push({ kind: "text", text: event.text });
      }
      return { ...turn, parts, streaming: false, cached: !!event.cached };
    }
    case "error":
      return { ...turn, streaming: false, error: event.message };
    default:
      return turn;
  }
}

export default function ChatView({ workspace, sessionId, status, info, me, openConnect, onError, onSessionCreated, onTurnFinished, goTo }) {
  const [turns, setTurns] = useState([]);
  const [pending, setPending] = useState(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const abortRef = useRef(null);
  const ownSession = useRef(null);
  const scrollRef = useRef(null);
  const stickToBottom = useRef(true);

  // Load history when the selected session changes (unless we just created it ourselves).
  useEffect(() => {
    // A session created by send() is already streaming here — don't abort or reload it.
    if (sessionId && ownSession.current === sessionId) {
      ownSession.current = null;
      return;
    }
    abortRef.current?.abort();
    setPending(null);
    if (!sessionId) {
      setTurns([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .get(`${ws(workspace)}/sessions/${sessionId}`)
      .then((data) => {
        if (cancelled) return;
        setTurns(data.messages);
        setPending(data.pending_approval);
      })
      .catch(onError)
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [workspace, sessionId, onError]);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [turns, pending]);

  function onScroll() {
    const el = scrollRef.current;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  async function runStream(url, body) {
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    stickToBottom.current = true;
    try {
      await streamEvents(
        url,
        body,
        (event) => {
          if (event.type === "approval") {
            setPending(event.requests);
            setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? { ...turn, streaming: false } : turn)));
            return;
          }
          setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? applyEvent(turn, event) : turn)));
        },
        controller.signal
      );
    } catch (e) {
      if (e.name === "AbortError") {
        setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? { ...turn, streaming: false, stopped: true } : turn)));
      } else {
        setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? { ...turn, streaming: false, error: e.message } : turn)));
        onError(e);
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
      onTurnFinished?.();
    }
  }

  async function send(text) {
    const message = (text ?? input).trim();
    if (!message || busy) return;
    setInput("");
    setPending(null);
    let sid = sessionId;
    if (!sid) {
      try {
        const session = await api.post(`${ws(workspace)}/sessions`);
        sid = session.id;
        ownSession.current = sid;
        onSessionCreated(sid);
      } catch (e) {
        onError(e);
        return;
      }
    }
    setTurns((t) => [...t, { role: "user", text: message }, { role: "assistant", parts: [], streaming: true }]);
    await runStream(`${ws(workspace)}/chat`, { session_id: sid, message });
  }

  async function decide(decisions) {
    setPending(null);
    setTurns((t) => {
      const lastTurn = t[t.length - 1];
      if (lastTurn?.role === "assistant") return t.map((turn, i) => (i === t.length - 1 ? { ...turn, streaming: true } : turn));
      return [...t, { role: "assistant", parts: [], streaming: true }];
    });
    await runStream(`${ws(workspace)}/chat/resume`, { session_id: sessionId, decisions });
  }

  const emptyIndex = status && status.chunks === 0 && status.index?.state !== "indexing";

  return (
    <div className="chat">
      <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
        <div className="chat-inner">
          {loading && <div className="muted center pad">Loading conversation…</div>}

          {!loading && turns.length === 0 && (
            <div className="empty-state">
              <div className="brand-mark large">R</div>
              <h1>Ask anything about {workspace}</h1>
              <p className="muted">
                RepoMind searches the indexed code, reads files, and can edit code{info?.allow_shell ? " or run commands" : ""} — anything
                that changes your workspace waits for your approval
                {info?.approvals?.length ? ` (${info.approvals.join(", ")})` : ""}.
              </p>
              {emptyIndex && (
                <div className="banner info">
                  <Icon name="files" /> This workspace has no indexed code yet.{" "}
                  <button className="link" onClick={() => goTo("files")}>
                    Add files
                  </button>
                </div>
              )}
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="suggestion" onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((turn, i) =>
            turn.role === "assistant" ? (
              <AssistantTurn key={i} turn={turn} />
            ) : turn.role === "summary" ? (
              <details key={i} className="summary-note">
                <summary>Earlier messages were summarized to save context</summary>
                <Markdown>{turn.text}</Markdown>
              </details>
            ) : (
              <div key={i} className="msg user">
                <div className="bubble">{turn.text}</div>
              </div>
            )
          )}

          {pending && <ApprovalCard requests={pending} onDecide={decide} />}
        </div>
      </div>

      {me?.limited && (
        <div className={`quota-note ${me.chats_left === 0 ? "out" : ""}`}>
          {me.chats_left === 0
            ? "You've used today's free messages."
            : `${me.chats_left} of ${me.chat_limit} free messages left today.`}{" "}
          <button className="link" onClick={() => openConnect()}>
            Connect your own API key
          </button>{" "}
          for unlimited chat and Plans.
        </div>
      )}

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <textarea
          rows={1}
          value={input}
          placeholder={pending ? "Approve or reject the pending action above, or send a new message…" : "Ask about the code, or ask for a change…"}
          onChange={(e) => {
            setInput(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = Math.min(e.target.scrollHeight, 220) + "px";
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
        />
        {busy ? (
          <button type="button" className="btn stop" onClick={() => abortRef.current?.abort()} title="Stop">
            <Icon name="stop" />
          </button>
        ) : (
          <button className="btn primary send" disabled={!input.trim()} title="Send (Enter)">
            <Icon name="send" />
          </button>
        )}
      </form>
    </div>
  );
}

function AssistantTurn({ turn }) {
  const empty = turn.parts.length === 0;
  return (
    <div className="msg assistant">
      <div className="avatar">R</div>
      <div className="assistant-body">
        {empty && turn.streaming && (
          <div className="thinking">
            <span />
            <span />
            <span />
          </div>
        )}
        {turn.parts.map((part, i) =>
          part.kind === "text" ? <Markdown key={i}>{part.text}</Markdown> : <ToolCard key={part.id || i} part={part} />
        )}
        {turn.cached && <div className="tag">⚡ answered from semantic cache</div>}
        {turn.stopped && <div className="tag">stopped</div>}
        {turn.error && (
          <div className="banner error">
            <Icon name="alert" /> {turn.error}
          </div>
        )}
      </div>
    </div>
  );
}

function toolSummary(part) {
  const a = part.args || {};
  return a.query || a.path || a.command || a.pattern || a.name || a.skill_name || "";
}

function ToolCard({ part }) {
  const done = part.result !== undefined;
  const failed = part.status === "error" || (done && /^Error:/.test(part.result));
  return (
    <details className={`tool ${failed ? "failed" : done ? "done" : "running"}`}>
      <summary>
        <span className="tool-status">{done ? <Icon name={failed ? "x" : "check"} size={13} /> : <span className="spinner" />}</span>
        <span className="tool-name">{part.name}</span>
        <span className="tool-arg">{String(toolSummary(part)).slice(0, 90)}</span>
      </summary>
      <div className="tool-body">
        <div className="tool-label">Arguments</div>
        <pre>{JSON.stringify(part.args, null, 2)}</pre>
        {done && (
          <>
            <div className="tool-label">Result</div>
            <pre>{part.result}</pre>
          </>
        )}
      </div>
    </details>
  );
}

function ApprovalCard({ requests, onDecide }) {
  const [decisions, setDecisions] = useState(() => requests.map(() => null));
  const [mode, setMode] = useState(() => requests.map(() => null)); // null | "reject" | "edit"
  const [drafts, setDrafts] = useState(() => requests.map((r) => JSON.stringify(r.args, null, 2)));
  const [reasons, setReasons] = useState(() => requests.map(() => ""));
  const [jsonError, setJsonError] = useState(null);

  function set(i, decision) {
    const next = decisions.map((d, j) => (j === i ? decision : d));
    setDecisions(next);
    setMode((m) => m.map((x, j) => (j === i ? null : x)));
    if (next.every(Boolean)) onDecide(next);
  }

  function setAt(setter, i, value) {
    setter((arr) => arr.map((x, j) => (j === i ? value : x)));
  }

  return (
    <div className="approval">
      <div className="approval-head">
        <Icon name="alert" /> RepoMind wants to run {requests.length > 1 ? `${requests.length} actions` : "an action"} that
        changes your workspace
      </div>
      {requests.map((req, i) => (
        <div key={i} className={`approval-item ${decisions[i] ? "decided" : ""}`}>
          <div className="approval-tool">
            <Icon name="tool" /> <b>{req.name}</b>
            {decisions[i] && <span className="tag">{decisions[i].type}</span>}
          </div>
          <ArgsPreview args={req.args} />

          {!decisions[i] && mode[i] === null && (
            <div className="approval-actions">
              <button className="btn primary small" onClick={() => set(i, { type: "approve" })}>
                <Icon name="check" size={14} /> Approve
              </button>
              {req.allowed_decisions.includes("edit") && (
                <button className="btn small" onClick={() => setAt(setMode, i, "edit")}>
                  <Icon name="edit" size={14} /> Edit
                </button>
              )}
              <button className="btn danger small" onClick={() => setAt(setMode, i, "reject")}>
                <Icon name="x" size={14} /> Reject
              </button>
            </div>
          )}

          {mode[i] === "reject" && (
            <div className="approval-form">
              <input
                autoFocus
                placeholder="Why? (optional — sent to the agent)"
                value={reasons[i]}
                onChange={(e) => setAt(setReasons, i, e.target.value)}
              />
              <button
                className="btn danger small"
                onClick={() => set(i, reasons[i].trim() ? { type: "reject", message: reasons[i].trim() } : { type: "reject" })}
              >
                Confirm reject
              </button>
              <button className="btn ghost small" onClick={() => setAt(setMode, i, null)}>
                Cancel
              </button>
            </div>
          )}

          {mode[i] === "edit" && (
            <div className="approval-form column">
              <textarea rows={8} className="mono" value={drafts[i]} onChange={(e) => setAt(setDrafts, i, e.target.value)} />
              {jsonError === i && <div className="form-error">Arguments must be valid JSON</div>}
              <div className="row gap">
                <button
                  className="btn primary small"
                  onClick={() => {
                    try {
                      const args = JSON.parse(drafts[i]);
                      setJsonError(null);
                      set(i, { type: "edit", edited_action: { name: req.name, args } });
                    } catch {
                      setJsonError(i);
                    }
                  }}
                >
                  Run with these arguments
                </button>
                <button className="btn ghost small" onClick={() => setAt(setMode, i, null)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function ArgsPreview({ args }) {
  const { content, new_text, old_text, command, path, ...rest } = args || {};
  return (
    <div className="args">
      {path && (
        <div className="arg-line">
          <span>path</span> <code>{path}</code>
        </div>
      )}
      {command && (
        <div className="arg-line">
          <span>command</span> <code>{command}</code>
        </div>
      )}
      {Object.entries(rest).map(([k, v]) => (
        <div key={k} className="arg-line">
          <span>{k}</span> <code>{typeof v === "string" ? v : JSON.stringify(v)}</code>
        </div>
      ))}
      {old_text !== undefined && (
        <pre className="diff del">{old_text}</pre>
      )}
      {new_text !== undefined && <pre className="diff add">{new_text}</pre>}
      {content !== undefined && <pre className="code-preview">{content}</pre>}
    </div>
  );
}

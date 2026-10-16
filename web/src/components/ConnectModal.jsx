import { useState } from "react";
import { api, getConnection } from "../api.js";
import { Icon } from "./Icons.jsx";

const PROVIDERS = [
  { id: "gemini", label: "Google Gemini", keyUrl: "https://aistudio.google.com/apikey", hint: "Free keys at Google AI Studio" },
  { id: "openai", label: "OpenAI", keyUrl: "https://platform.openai.com/api-keys", hint: "Pay-as-you-go" },
  { id: "anthropic", label: "Anthropic", keyUrl: "https://console.anthropic.com/settings/keys", hint: "Pay-as-you-go" },
];

export default function ConnectModal({ info, me, reason, onClose, onSave }) {
  const saved = getConnection();
  const defaults = info?.providers || {};
  const [provider, setProvider] = useState(saved?.provider || "gemini");
  const [model, setModel] = useState(saved?.model || defaults[saved?.provider || "gemini"] || "");
  const [apiKey, setApiKey] = useState(saved?.apiKey || "");
  const [githubToken, setGithubToken] = useState(saved?.githubToken || "");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  const current = PROVIDERS.find((p) => p.id === provider);

  function pickProvider(id) {
    setProvider(id);
    setModel(defaults[id] || "");
    setResult(null);
  }

  async function testAndSave(e) {
    e.preventDefault();
    const conn = {
      provider,
      model: model.trim(),
      apiKey: apiKey.trim(),
      githubToken: githubToken.trim(),
    };
    if (!conn.apiKey && !conn.githubToken) {
      setError("Add an API key, a GitHub token, or both.");
      return;
    }
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const res = await api.testConnection(conn);
      setResult(res);
      const failed = [res.llm, res.github].some((r) => r && !r.ok);
      if (!failed) onSave(conn);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal connect" onClick={(e) => e.stopPropagation()} onSubmit={testAndSave}>
        <div className="modal-head">
          <h2>
            <Icon name="sparkle" /> Connect your AI
          </h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </div>

        {reason && (
          <div className="banner warn small">
            <Icon name="alert" /> <span>{reason}</span>
          </div>
        )}

        <div className="plan-compare">
          <div className={`plan-card ${me?.limited ? "on" : ""}`}>
            <b>Free</b>
            <span className="muted small">
              {info?.llm} on this site's key · {me?.chat_limit ?? 2} messages a day · small repos · no Plans
            </span>
          </div>
          <div className={`plan-card ${me?.own_key ? "on" : ""}`}>
            <b>Your own key</b>
            <span className="muted small">Unlimited chat, Plans, bigger repos, any supported model — billed to you</span>
          </div>
        </div>

        <div className="segmented three">
          {PROVIDERS.map((p) => (
            <button type="button" key={p.id} className={provider === p.id ? "on" : ""} onClick={() => pickProvider(p.id)}>
              {p.label}
            </button>
          ))}
        </div>

        <label className="field">
          <span>
            {current.label} API key ·{" "}
            <a href={current.keyUrl} target="_blank" rel="noreferrer">
              get one
            </a>{" "}
            <span className="muted">({current.hint})</span>
          </span>
          <input
            type="password"
            autoComplete="off"
            placeholder={provider === "gemini" ? "AIza… or AQ.…" : provider === "openai" ? "sk-…" : "sk-ant-…"}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </label>

        <label className="field">
          <span>Model</span>
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder={defaults[provider]} />
        </label>

        {provider !== "gemini" && (
          <p className="muted small">
            Code search still uses Gemini embeddings on this site's key, so very large repos need a Gemini key.
          </p>
        )}

        <label className="field">
          <span>
            GitHub token <span className="muted">(optional — lets the agent read issues, PRs and repos as you)</span> ·{" "}
            <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noreferrer">
              create one
            </a>
          </span>
          <input
            type="password"
            autoComplete="off"
            placeholder="github_pat_… or ghp_…"
            value={githubToken}
            onChange={(e) => setGithubToken(e.target.value)}
          />
        </label>

        <p className="muted small">
          <Icon name="lock" size={12} /> Keys stay in this browser. They're sent with each request over HTTPS, used for that
          request only, and never saved on the server. Anything that changes files still asks for your approval.
        </p>

        {result && (
          <div className="test-results">
            {[result.llm, result.github].filter(Boolean).map((r, i) => (
              <div key={i} className={`banner small ${r.ok ? "info" : "error"}`}>
                <Icon name={r.ok ? "check" : "alert"} /> <span>{r.message}</span>
              </div>
            ))}
          </div>
        )}
        {error && <div className="form-error">{error}</div>}

        <div className="modal-actions">
          {saved && (
            <button type="button" className="btn ghost danger-text" onClick={() => onSave(null)}>
              Disconnect
            </button>
          )}
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy}>
            {busy ? "Testing…" : "Test & save"}
          </button>
        </div>
      </form>
    </div>
  );
}

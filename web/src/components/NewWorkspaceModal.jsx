import { useState } from "react";
import { Icon } from "./Icons.jsx";

export default function NewWorkspaceModal({ onClose, onCreate }) {
  const [name, setName] = useState("");
  const [gitUrl, setGitUrl] = useState("");
  const [mode, setMode] = useState("empty");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function suggestName(url) {
    const match = url.match(/\/([\w.-]+?)(\.git)?\/?$/);
    if (match && !name) setName(match[1]);
  }

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onCreate({ name: name.trim(), git_url: mode === "git" ? gitUrl.trim() : null });
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="modal-head">
          <h2>New workspace</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </div>

        <div className="segmented">
          <button type="button" className={mode === "empty" ? "on" : ""} onClick={() => setMode("empty")}>
            <Icon name="upload" /> Upload my code
          </button>
          <button type="button" className={mode === "git" ? "on" : ""} onClick={() => setMode("git")}>
            <Icon name="git" /> Clone a Git repo
          </button>
        </div>

        {mode === "git" && (
          <label className="field">
            <span>Public repository URL</span>
            <input
              autoFocus
              placeholder="https://github.com/user/repo"
              value={gitUrl}
              onChange={(e) => setGitUrl(e.target.value)}
              onBlur={(e) => suggestName(e.target.value)}
              required
            />
          </label>
        )}

        <label className="field">
          <span>Name</span>
          <input
            autoFocus={mode === "empty"}
            placeholder="my-project"
            value={name}
            onChange={(e) => setName(e.target.value)}
            pattern="[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}"
            title="Letters, digits, '-', '_' and '.'"
            required
          />
        </label>

        <p className="muted small">
          {mode === "git"
            ? "The repo is cloned (shallow) and indexed. Large repos take a while to embed."
            : "You'll land in Files, where you can upload files, a whole folder, or a .zip."}
        </p>

        {error && <div className="form-error">{error}</div>}

        <div className="modal-actions">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy || !name.trim()}>
            {busy ? (mode === "git" ? "Cloning…" : "Creating…") : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}

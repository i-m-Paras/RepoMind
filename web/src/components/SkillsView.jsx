import { useCallback, useEffect, useState } from "react";
import { api, ws } from "../api.js";
import { Icon } from "./Icons.jsx";

const TEMPLATE = `---
name: my_skill
description: One line on what this skill does
when_to_use: keywords, that, should, trigger, it
---
Step-by-step instructions the agent follows when this skill is loaded.

1. Use search_codebase to find ...
2. Read the relevant files with read_file.
3. ...
`;

export default function SkillsView({ workspace, onError, notify, refreshStatus }) {
  const [skills, setSkills] = useState([]);
  const [editing, setEditing] = useState(null); // {name, content, isNew}

  const load = useCallback(async () => {
    try {
      setSkills(await api.get(`${ws(workspace)}/skills`));
    } catch (e) {
      onError(e);
    }
  }, [workspace, onError]);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    const name = editing.name.trim();
    try {
      await api.put(`${ws(workspace)}/skills/${encodeURIComponent(name)}`, { content: editing.content });
      notify(`Saved skill "${name}" — the agent can use it now`, "success");
      setEditing(null);
      load();
      refreshStatus();
    } catch (e) {
      onError(e);
    }
  }

  async function remove(name) {
    if (!confirm(`Delete skill "${name}"?`)) return;
    try {
      await api.del(`${ws(workspace)}/skills/${encodeURIComponent(name)}`);
      load();
      refreshStatus();
    } catch (e) {
      onError(e);
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Skills</h1>
          <p className="muted">
            Reusable instruction packs. Only each skill's name and description sit in the agent's prompt; it loads the
            full instructions when your request matches. They live in <code>.repomind/skills</code> inside the workspace — the same folder the terminal app reads.
          </p>
        </div>
        <button className="btn primary" onClick={() => setEditing({ name: "", content: TEMPLATE, isNew: true })}>
          <Icon name="plus" size={14} /> New skill
        </button>
      </header>

      {editing && (
        <section className="card">
          <h2>{editing.isNew ? "New skill" : `Edit ${editing.name}`}</h2>
          <label className="field">
            <span>Folder name</span>
            <input
              value={editing.name}
              disabled={!editing.isNew}
              placeholder="my_skill"
              pattern="[a-z0-9][a-z0-9_\-]{0,63}"
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
            />
          </label>
          <label className="field">
            <span>SKILL.md</span>
            <textarea
              className="mono"
              rows={16}
              value={editing.content}
              onChange={(e) => setEditing({ ...editing, content: e.target.value })}
            />
          </label>
          <div className="row gap-s">
            <button className="btn primary" onClick={save} disabled={!editing.name.trim()}>
              <Icon name="save" size={14} /> Save skill
            </button>
            <button className="btn ghost" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
        </section>
      )}

      <div className="skill-grid">
        {skills.map((s) => (
          <div key={s.name} className="card skill">
            <div className="row between">
              <h3>{s.name}</h3>
              <span className={`status-badge ${s.source === "builtin" ? "approved" : "completed"}`}>{s.source}</span>
            </div>
            <p>{s.description}</p>
            {s.when_to_use && <p className="muted small">Triggers: {s.when_to_use}</p>}
            {s.files.length > 0 && (
              <p className="muted small">
                Support files: {s.files.map((f) => <code key={f}>{f}</code>)}
              </p>
            )}
            <div className="row gap-s skill-actions">
              <button
                className="btn small"
                onClick={() => setEditing({ name: s.name, content: s.content, isNew: s.source === "builtin" })}
              >
                <Icon name="edit" size={14} /> {s.source === "builtin" ? "Customize" : "Edit"}
              </button>
              {s.source === "workspace" && (
                <button className="btn small danger" onClick={() => remove(s.name)}>
                  <Icon name="trash" size={14} /> Delete
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

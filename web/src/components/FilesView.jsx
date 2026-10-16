import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, formatBytes, ws } from "../api.js";
import { Icon } from "./Icons.jsx";

// Folders never worth uploading — the server skips them too, this just saves bandwidth.
const SKIP = /(^|\/)(node_modules|\.git|\.venv|venv|env|__pycache__|dist|build|\.repomind|\.next|\.DS_Store)(\/|$)/;

function buildTree(files) {
  const root = { name: "", path: "", dirs: {}, files: [] };
  for (const f of files) {
    const parts = f.path.split("/");
    let node = root;
    parts.slice(0, -1).forEach((part, i) => {
      node.dirs[part] ||= { name: part, path: parts.slice(0, i + 1).join("/"), dirs: {}, files: [] };
      node = node.dirs[part];
    });
    node.files.push({ name: parts[parts.length - 1], path: f.path, size: f.size });
  }
  return root;
}

// Recursively read a dropped folder (DataTransferItem.webkitGetAsEntry).
// includeSelf=false puts a folder's contents at the root instead of under its own name.
async function readEntry(entry, prefix = "", includeSelf = true) {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    return [{ file, path: prefix + file.name }];
  }
  const reader = entry.createReader();
  const entries = [];
  for (;;) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) break;
    entries.push(...batch);
  }
  const childPrefix = includeSelf ? `${prefix}${entry.name}/` : prefix;
  const nested = await Promise.all(entries.map((e) => readEntry(e, childPrefix)));
  return nested.flat();
}

export default function FilesView({ workspace, onError, notify, refreshStatus }) {
  const [files, setFiles] = useState(null);
  const [expanded, setExpanded] = useState(() => new Set());
  const [selected, setSelected] = useState(null); // {path, content, original, reason, isNew}
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef(null);
  const folderInput = useRef(null);

  const load = useCallback(async () => {
    try {
      setFiles((await api.get(`${ws(workspace)}/files`)).files);
    } catch (e) {
      onError(e);
    }
  }, [workspace, onError]);

  useEffect(() => {
    load();
  }, [load]);

  const tree = useMemo(() => buildTree(files || []), [files]);
  const dirty = selected && selected.content !== selected.original;

  async function open(path) {
    if (dirty && !confirm("Discard unsaved changes?")) return;
    try {
      const data = await api.get(`${ws(workspace)}/file?path=${encodeURIComponent(path)}`);
      setSelected({ path, content: data.content ?? "", original: data.content ?? "", reason: data.reason });
    } catch (e) {
      onError(e);
    }
  }

  async function save() {
    if (!selected || selected.reason) return;
    try {
      await api.put(`${ws(workspace)}/file`, { path: selected.path, content: selected.content });
      setSelected((s) => ({ ...s, original: s.content, isNew: false }));
      notify(`Saved ${selected.path} — re-indexed`, "success");
      load();
      refreshStatus();
    } catch (e) {
      onError(e);
    }
  }

  async function remove(path) {
    if (!confirm(`Delete ${path}?`)) return;
    try {
      await api.del(`${ws(workspace)}/file?path=${encodeURIComponent(path)}`);
      if (selected?.path === path) setSelected(null);
      load();
      refreshStatus();
    } catch (e) {
      onError(e);
    }
  }

  async function download(path) {
    try {
      const blob = await api.fetchBlob(`${ws(workspace)}/download?path=${encodeURIComponent(path)}`);
      const url = URL.createObjectURL(blob);
      const a = Object.assign(document.createElement("a"), { href: url, download: path.split("/").pop() });
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onError(e);
    }
  }

  function newFile() {
    const path = prompt("New file path (e.g. src/utils.py)");
    if (!path) return;
    setSelected({ path: path.trim(), content: "", original: null, isNew: true });
  }

  async function upload(items) {
    const wanted = items.filter((it) => !SKIP.test(it.path));
    if (!wanted.length) return;
    const form = new FormData();
    for (const it of wanted) {
      form.append("files", it.file, it.file.name);
      form.append("paths", it.path);
    }
    setUploading(true);
    try {
      const res = await api.upload(`${ws(workspace)}/upload`, form);
      notify(`Uploaded ${res.written} file${res.written === 1 ? "" : "s"} — indexing in the background`, "success");
      await load();
      refreshStatus();
    } catch (e) {
      onError(e);
    } finally {
      setUploading(false);
    }
  }

  async function onDrop(e) {
    e.preventDefault();
    setDragging(false);
    const entries = [...e.dataTransfer.items].map((it) => it.webkitGetAsEntry?.()).filter(Boolean);
    if (entries.length) {
      const single = entries.length === 1 && entries[0].isDirectory;
      const nested = await Promise.all(entries.map((entry) => readEntry(entry, "", !single)));
      upload(nested.flat());
    } else {
      upload([...e.dataTransfer.files].map((file) => ({ file, path: file.name })));
    }
  }

  function toggle(path) {
    setExpanded((s) => {
      const next = new Set(s);
      next.has(path) ? next.delete(path) : next.add(path);
      return next;
    });
  }

  return (
    <div className="files">
      <section
        className={`file-panel ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <div className="panel-head">
          <h2>Files</h2>
          <div className="row gap-s">
            <button className="icon-btn" title="New file" onClick={newFile} aria-label="New file">
              <Icon name="plus" />
            </button>
            <button className="icon-btn" title="Refresh" onClick={load} aria-label="Refresh">
              <Icon name="refresh" />
            </button>
          </div>
        </div>

        <div className="upload-row">
          <button className="btn small" onClick={() => fileInput.current.click()} disabled={uploading}>
            <Icon name="upload" size={14} /> Files / .zip
          </button>
          <button className="btn small" onClick={() => folderInput.current.click()} disabled={uploading}>
            <Icon name="folder" size={14} /> Folder
          </button>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              upload([...e.target.files].map((file) => ({ file, path: file.name })));
              e.target.value = "";
            }}
          />
          <input
            ref={folderInput}
            type="file"
            webkitdirectory=""
            multiple
            hidden
            onChange={(e) => {
              // Drop the selected folder's own name so its contents land at the workspace root.
              upload(
                [...e.target.files].map((file) => ({
                  file,
                  path: file.webkitRelativePath.split("/").slice(1).join("/") || file.name,
                }))
              );
              e.target.value = "";
            }}
          />
        </div>

        <div className="tree">
          {uploading && <div className="muted small pad">Uploading…</div>}
          {files && files.length === 0 && !uploading && (
            <div className="dropzone-hint">
              <Icon name="upload" size={28} />
              <p>Drop files, a folder, or a .zip here</p>
              <p className="muted small">Zips are extracted; node_modules, .git and virtualenvs are skipped.</p>
            </div>
          )}
          {files && (
            <TreeNode node={tree} depth={0} expanded={expanded} toggle={toggle} open={open} selected={selected?.path} />
          )}
        </div>
        {dragging && <div className="drop-overlay">Drop to upload</div>}
      </section>

      <section className="editor-panel">
        {!selected ? (
          <div className="empty-state small">
            <Icon name="file" size={28} />
            <p className="muted">Select a file to view or edit it. Saving re-indexes it immediately.</p>
          </div>
        ) : (
          <>
            <div className="panel-head">
              <div className="file-path">
                <Icon name="file" /> {selected.path}
                {dirty && <span className="dirty-dot" title="Unsaved changes" />}
              </div>
              <div className="row gap-s">
                {!selected.reason && (
                  <button className="btn primary small" onClick={save} disabled={!dirty && !selected.isNew}>
                    <Icon name="save" size={14} /> Save
                  </button>
                )}
                {!selected.isNew && (
                  <>
                    <button className="icon-btn" title="Download" onClick={() => download(selected.path)} aria-label="Download">
                      <Icon name="download" />
                    </button>
                    <button className="icon-btn danger" title="Delete" onClick={() => remove(selected.path)} aria-label="Delete">
                      <Icon name="trash" />
                    </button>
                  </>
                )}
              </div>
            </div>
            {selected.reason ? (
              <div className="empty-state small">
                <p className="muted">{selected.reason} — can't be shown here.</p>
              </div>
            ) : (
              <CodeEditor
                value={selected.content}
                onChange={(content) => setSelected((s) => ({ ...s, content }))}
                onSave={save}
              />
            )}
          </>
        )}
      </section>
    </div>
  );
}

function TreeNode({ node, depth, expanded, toggle, open, selected }) {
  const dirs = Object.values(node.dirs).sort((a, b) => a.name.localeCompare(b.name));
  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name));
  return (
    <>
      {dirs.map((dir) => (
        <div key={dir.path}>
          <button className="tree-row" style={{ paddingLeft: 8 + depth * 14 }} onClick={() => toggle(dir.path)}>
            <Icon name="chevron" size={12} className={`chev ${expanded.has(dir.path) ? "open" : ""}`} />
            <Icon name="folder" size={14} />
            <span>{dir.name}</span>
          </button>
          {expanded.has(dir.path) && (
            <TreeNode node={dir} depth={depth + 1} expanded={expanded} toggle={toggle} open={open} selected={selected} />
          )}
        </div>
      ))}
      {files.map((f) => (
        <button
          key={f.path}
          className={`tree-row file ${selected === f.path ? "active" : ""}`}
          style={{ paddingLeft: 22 + depth * 14 }}
          onClick={() => open(f.path)}
          title={`${f.path} · ${formatBytes(f.size)}`}
        >
          <Icon name="file" size={14} />
          <span>{f.name}</span>
        </button>
      ))}
    </>
  );
}

function CodeEditor({ value, onChange, onSave }) {
  const gutter = useRef(null);
  const lines = value.split("\n").length;
  return (
    <div className="code-editor">
      <div className="gutter" ref={gutter} aria-hidden="true">
        {Array.from({ length: lines }, (_, i) => (
          <div key={i}>{i + 1}</div>
        ))}
      </div>
      <textarea
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onScroll={(e) => (gutter.current.scrollTop = e.target.scrollTop)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "s") {
            e.preventDefault();
            onSave();
          } else if (e.key === "Tab") {
            e.preventDefault();
            const { selectionStart: start, selectionEnd: end } = e.target;
            onChange(value.slice(0, start) + "    " + value.slice(end));
            requestAnimationFrame(() => e.target.setSelectionRange(start + 4, start + 4));
          }
        }}
      />
    </div>
  );
}

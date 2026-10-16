import { useCallback, useEffect, useState } from "react";
import { api, timeAgo, ws } from "../api.js";
import { Icon } from "./Icons.jsx";

const PAGE = 30;

export default function IndexView({ workspace, info, status, onError, refreshStatus }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [chunks, setChunks] = useState({ total: 0, chunks: [] });
  const [offset, setOffset] = useState(0);

  const index = status?.index;
  const stats = index?.stats;

  const loadChunks = useCallback(async () => {
    try {
      setChunks(await api.get(`${ws(workspace)}/index/chunks?limit=${PAGE}&offset=${offset}`));
    } catch (e) {
      onError(e);
    }
  }, [workspace, offset, onError]);

  useEffect(() => {
    loadChunks();
  }, [loadChunks, index?.state]);

  async function reindex(force) {
    if (force && !confirm("Rebuild the whole index? Every chunk is re-embedded (uses embedding quota).")) return;
    try {
      await api.post(`${ws(workspace)}/index`, { force });
      refreshStatus();
    } catch (e) {
      onError(e);
    }
  }

  async function search(e) {
    e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    try {
      setResults((await api.post(`${ws(workspace)}/index/search`, { query, k: 8 })).results);
    } catch (e) {
      onError(e);
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Index</h1>
          <p className="muted">
            Code is split into AST chunks (one per function or class) and embedded with {info?.embeddings}. Unchanged
            files are skipped on re-index, and edits are picked up live{info?.watcher ? " by the file watcher" : ""}.
          </p>
        </div>
        <div className="row gap-s">
          <button className="btn" onClick={() => reindex(false)} disabled={index?.state === "indexing"}>
            <Icon name="refresh" size={14} /> Re-index
          </button>
          <button className="btn ghost" onClick={() => reindex(true)} disabled={index?.state === "indexing"}>
            Rebuild
          </button>
        </div>
      </header>

      <div className="stat-grid">
        <Stat label="Status" value={index?.state === "indexing" ? "Indexing…" : index?.state || "—"} tone={index?.state} />
        <Stat label="Chunks" value={status?.chunks ?? "—"} />
        <Stat label="Files" value={stats?.files ?? "—"} />
        <Stat label="Last sync" value={index?.updated_at ? timeAgo(index.updated_at) : "—"} />
      </div>
      {stats && (
        <p className="muted small">
          Last sync: {stats.added} added · {stats.changed} changed · {stats.deleted} deleted · {stats.skipped} unchanged
          (skipped)
        </p>
      )}
      {index?.state === "error" && (
        <div className="banner error">
          <Icon name="alert" /> {index.error}
        </div>
      )}

      <section className="card">
        <h2>Try retrieval</h2>
        <p className="muted small">The same search the agent's search_codebase tool runs. Higher score = closer match.</p>
        <form className="search-row" onSubmit={search}>
          <Icon name="search" />
          <input placeholder="e.g. where are invoices calculated?" value={query} onChange={(e) => setQuery(e.target.value)} />
          <button className="btn primary small" disabled={searching}>
            {searching ? "Searching…" : "Search"}
          </button>
        </form>
        {results && results.length === 0 && <p className="muted">No results — is the index empty?</p>}
        {results?.map((r, i) => (
          <ChunkCard key={i} chunk={r} score={r.score} />
        ))}
      </section>

      <section className="card">
        <div className="row between">
          <h2>Indexed chunks</h2>
          <div className="row gap-s pager">
            <button className="btn small ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Prev
            </button>
            <span className="muted small">
              {chunks.total ? `${offset + 1}–${Math.min(offset + PAGE, chunks.total)} of ${chunks.total}` : "0"}
            </span>
            <button className="btn small ghost" disabled={offset + PAGE >= chunks.total} onClick={() => setOffset(offset + PAGE)}>
              Next
            </button>
          </div>
        </div>
        {chunks.chunks.map((c, i) => (
          <ChunkCard key={`${c.source}:${c.start_line}:${i}`} chunk={c} />
        ))}
      </section>
    </div>
  );
}

function Stat({ label, value, tone }) {
  return (
    <div className={`stat ${tone || ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

function ChunkCard({ chunk, score }) {
  return (
    <details className="chunk">
      <summary>
        <span className={`type-badge ${chunk.type}`}>{chunk.type}</span>
        <span className="chunk-name">{chunk.name}</span>
        <span className="chunk-loc">
          {chunk.source}:{chunk.start_line}-{chunk.end_line}
        </span>
        {score !== undefined && (
          <span className="score">
            <span className="score-bar">
              <span style={{ width: `${Math.max(0, Math.min(1, score)) * 100}%` }} />
            </span>
            {score.toFixed(3)}
          </span>
        )}
      </summary>
      <pre>{chunk.content}</pre>
    </details>
  );
}

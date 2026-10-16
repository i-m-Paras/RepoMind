import os
from pathlib import Path

import chromadb

from repomind.config import config
from repomind.context.chroma_client import get_collection, mark_changed
from repomind.workspace import root
from repomind.context.indexers.code_parser import parse_file, get_source_files
from repomind.llm.factory import get_embedder
from repomind.observability.logger import get_logger

logger = get_logger(__name__)

# ------------------------------------------------------------------
# How incremental indexing works
# ------------------------------------------------------------------
# Every chunk stored in ChromaDB includes an "mtime" metadata field —
# the file's last-modified timestamp at the time it was indexed.
#
# On each index_codebase() call:
#   1. Read current mtimes from disk  (os.path.getmtime per file)
#   2. Read stored mtimes from ChromaDB (_get_indexed_mtimes)
#   3. Skip files whose mtime hasn't changed  → no embedding cost
#   4. Re-embed files whose mtime changed     → delete old chunks first
#   5. Delete chunks for files no longer on disk
#
# doc_id format: "{filepath}::{chunk_name}::{start_line}"
# This makes each chunk uniquely addressable for upsert/delete.
# ------------------------------------------------------------------


# Bump when the embedded text changes: chunks from an older version are re-embedded on the next sync.
# v2: each chunk is embedded with a "path :: type name" header (helps retrieval match file/function names).
# v3: module-level code and decorators are chunked too.
EMBED_VERSION = 3

# {repo_path: counts from the last index_codebase() run} — shown on the web UI's Index page
last_sync_stats: dict[str, dict] = {}


def _get_collection() -> chromadb.Collection:
    """Open (or create) the ChromaDB collection defined in config."""
    return get_collection()


def _get_indexed_mtimes(collection: chromadb.Collection) -> dict[str, float]:
    """
    Read all chunk metadata from ChromaDB and return {filepath: mtime}.

    A file has many chunks — we take the highest mtime seen across all of
    them (they should all be equal, but max() is safe if they ever drift).
    """
    results = collection.get(include=["metadatas"])
    mtimes: dict[str, float] = {}
    stale: set[str] = set()
    for meta in results["metadatas"]:
        source = meta.get("source", "")
        if not source:
            continue
        if meta.get("v") != EMBED_VERSION:
            stale.add(source)
        # Keep the highest mtime seen for each source file
        mtimes[source] = max(mtimes.get(source, 0.0), meta.get("mtime", 0.0))
    # Embedded by an older EMBED_VERSION → an impossible mtime, so index_codebase re-embeds it
    for source in stale:
        mtimes[source] = -1.0
    return mtimes


def _delete_file_chunks(collection: chromadb.Collection, filepath: str) -> None:
    """
    Remove every chunk belonging to filepath from the collection.

    Called before re-embedding a changed file so old chunks (e.g. from a
    renamed function) don't accumulate alongside the new ones.
    """
    results = collection.get(where={"source": filepath}, include=[])
    if results["ids"]:
        collection.delete(ids=results["ids"])
        mark_changed()
        logger.debug(f"Deleted {len(results['ids'])} chunks for {filepath}")


def _embed_file(collection: chromadb.Collection, filepath: str, mtime: float) -> int:
    """
    Parse, embed, and upsert all chunks of a single file.

    mtime is stored in each chunk's metadata so future calls can detect
    whether the file has changed without re-reading or re-embedding it.

    Returns the number of chunks successfully indexed (0 on parse error).
    Shared by index_codebase() and index_single_file() so embedding
    logic is not duplicated.
    """
    embedder = get_embedder()
    try:
        chunks = parse_file(filepath)
    except (SyntaxError, ValueError) as e:
        logger.error(f"Skipping {filepath}: {e}")
        return 0

    # doc_id is stable across re-runs for the same chunk — upsert is safe.
    # Keyed by id so duplicates within one file collapse (a batch upsert rejects repeated ids).
    chunks = list({f"{c.source}::{c.name}::{c.start_line}": c for c in chunks}.values())
    if not chunks:
        return 0

    # One embedding request per file (not per chunk) — keeps Gemini's free-tier quota in check.
    # The stored document stays the raw code; only the embedded text gets the header.
    embeddings = embedder.embed_documents([_embedding_text(chunk) for chunk in chunks])
    collection.upsert(
        ids=[f"{chunk.source}::{chunk.name}::{chunk.start_line}" for chunk in chunks],
        embeddings=embeddings,
        documents=[chunk.content for chunk in chunks],
        metadatas=[{
            "source":     chunk.source,
            "name":       chunk.name,
            "type":       chunk.type,
            "start_line": chunk.start_line,
            "end_line":   chunk.end_line,
            "mtime":      mtime,   # stored so next run can skip this file if unchanged
            "v":          EMBED_VERSION,
        } for chunk in chunks]
    )
    mark_changed()
    return len(chunks)


def _embedding_text(chunk) -> str:
    try:
        path = str(Path(chunk.source).relative_to(root()))
    except ValueError:
        path = chunk.source
    return f"{path} :: {chunk.type} {chunk.name}\n{chunk.content}"


# ------------------------------------------------------------------
# Public API used by watcher.py (per-file, real-time)
# ------------------------------------------------------------------

def index_single_file(filepath: str) -> None:
    """
    Reindex one file immediately — called by the watchdog handler when
    a file is created or modified.

    Always deletes existing chunks first so stale chunks (e.g. from a
    function that was renamed or deleted) don't linger alongside new ones.
    """
    if not os.path.exists(filepath):
        # File may have been deleted between the event firing and this call
        return
    collection = _get_collection()
    mtime      = os.path.getmtime(filepath)
    _delete_file_chunks(collection, filepath)
    n = _embed_file(collection, filepath, mtime)
    logger.info(f"Watchdog indexed {filepath} → {n} chunks")


def remove_file_from_index(filepath: str) -> None:
    """
    Remove all chunks for a deleted file — called by the watchdog handler
    on file delete so stale chunks don't pollute RAG results.
    """
    collection = _get_collection()
    _delete_file_chunks(collection, filepath)
    logger.info(f"Watchdog removed {filepath} from index")


# ------------------------------------------------------------------
# Public API used by main.py (full scan, startup + /reindex + post-/plan)
# ------------------------------------------------------------------

def index_codebase(repo_path: str) -> chromadb.Collection:
    """
    Incrementally index all source files in repo_path into ChromaDB.

      New files       → parsed and embedded
      Changed files   → old chunks deleted, re-parsed and re-embedded
      Deleted files   → all chunks removed
      Unchanged files → skipped entirely (mtime matches stored value)

    Safe to call repeatedly — startup, after /plan completes, or via
    /reindex. Fast after the first run because unchanged files are skipped.
    """
    collection     = _get_collection()
    # {filepath: current mtime on disk}
    current_files  = {f: os.path.getmtime(f) for f in get_source_files(repo_path)}
    # {filepath: mtime when last indexed into ChromaDB}
    indexed_mtimes = _get_indexed_mtimes(collection)

    # ── Step 1: remove chunks for files no longer on disk ────────────
    deleted = set(indexed_mtimes) - set(current_files)
    for filepath in deleted:
        logger.info(f"Removing deleted file from index: {filepath}")
        _delete_file_chunks(collection, filepath)

    # ── Step 2: index new or changed files, skip unchanged ───────────
    added = changed = skipped = 0
    for filepath, mtime in current_files.items():
        # Tolerance: Chroma round-trips float metadata with tiny precision loss
        if abs(indexed_mtimes.get(filepath, -1.0) - mtime) < 1e-3:
            # mtime unchanged → file content hasn't changed → skip entirely
            skipped += 1
            continue

        if filepath in indexed_mtimes:
            # File was indexed before but mtime changed → delete old chunks first
            _delete_file_chunks(collection, filepath)
            changed += 1
        else:
            # File has never been indexed → fresh add
            added += 1

        _embed_file(collection, filepath, mtime)

    last_sync_stats[repo_path] = {
        "files": len(current_files), "added": added, "changed": changed,
        "deleted": len(deleted), "skipped": skipped,
    }
    logger.info(
        f"Index updated — added: {added}, changed: {changed}, "
        f"deleted: {len(deleted)}, skipped: {skipped} | total: {collection.count()}"
    )
    return collection


def show_index(collection: chromadb.Collection) -> None:
    """Display all documents stored in the ChromaDB collection."""
    from rich.console import Console
    console = Console()
    results = collection.get(include=["documents", "metadatas", "embeddings"])
    console.print(f"\n[bold]Semantic Index — {collection.count()} chunks[/bold]\n")

    for i, (doc, meta, emb) in enumerate(zip(
        results["documents"],
        results["metadatas"],
        results["embeddings"]
    )):
        console.print(f"[bold cyan]── Chunk {i+1} ──────────────────────────[/bold cyan]")
        console.print(f"  File     : {meta['source']}")
        console.print(f"  Name     : {meta['name']} ({meta['type']})")
        console.print(f"  Lines    : {meta['start_line']} - {meta['end_line']}")
        console.print(f"  Embedding: [{', '.join(f'{v:.4f}' for v in emb[:5])}...] ({len(emb)} dims)")
        console.print(f"  Code     :\n[dim]{doc[:300]}[/dim]\n")

"""One ChromaDB client per database folder, shared by the indexer, retriever and web server.

chromadb.PersistentClient() isn't safe to create from two threads at once (its
internal system cache races), which happens in the web server when the index
status is polled while indexing runs. Creating clients behind a lock avoids it.
"""

import threading
from pathlib import Path

import chromadb

from repomind.config import config
from repomind.workspace import data_path

_lock = threading.Lock()
_clients: dict[str, chromadb.ClientAPI] = {}
_versions: dict[str, int] = {}  # bumped on every write, so the BM25 index knows when to rebuild


def _db_path() -> str:
    return str(data_path(config["chromadb"]["persist_dir"]).resolve())


def get_client() -> chromadb.ClientAPI:
    path = _db_path()
    with _lock:
        if path not in _clients:
            _clients[path] = chromadb.PersistentClient(path=path)
        return _clients[path]


def get_collection() -> chromadb.Collection:
    return get_client().get_or_create_collection(name=config["chromadb"]["collection_name"])


def mark_changed() -> None:
    """Call after adding or deleting chunks in this project's collection."""
    path = _db_path()
    with _lock:
        _versions[path] = _versions.get(path, 0) + 1


def version() -> tuple[str, int]:
    path = _db_path()
    return path, _versions.get(path, 0)


def forget(folder: Path) -> None:
    """Drop cached clients for databases inside folder (before the folder is deleted)."""
    folder = folder.resolve()
    with _lock:
        for path in [p for p in _clients if Path(p).is_relative_to(folder)]:
            del _clients[path]
        chromadb.api.client.SharedSystemClient.clear_system_cache()

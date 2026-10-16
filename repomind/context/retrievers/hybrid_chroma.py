"""Hybrid retrieval on the Chroma index: dense (Gemini embeddings) + sparse (BM25), fused with RRF.

Dense search finds code that *means* the same thing as the query ("where do we charge
users?" → BillingService). BM25 finds exact identifiers and rare words the embedding
blurs ("TASK_LIMITS", "verify_token"). Reciprocal Rank Fusion merges the two ranked
lists without having to compare their scores:

    rrf(chunk) = Σ  weight_i / (rrf_k + rank_i(chunk))

The BM25 index is built in memory from the chunks already stored in Chroma, so it
needs no extra service, and is rebuilt only after the index changes.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from rank_bm25 import BM25Okapi

from repomind.config import config
from repomind.context import chroma_client
from repomind.context.retrievers.semantic_chroma import retrieve as dense_retrieve
from repomind.observability.logger import get_logger
from repomind.workspace import root

logger = get_logger(__name__)

_lock = threading.Lock()
_bm25_cache: dict[str, tuple[int, BM25Okapi | None, list[dict]]] = {}  # db path → (version, index, chunks)


def tokenize(text: str) -> list[str]:
    """Code-aware tokens: 'getUserById' / 'get_user_by_id' → get, user, by, id (+ the whole word)."""
    tokens = []
    for word in re.findall(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*", text):
        parts = [p for chunk in word.split("_") for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", chunk)]
        tokens.extend(p.lower() for p in parts)
        if len(parts) > 1:
            tokens.append(word.lower())
    return tokens


def _relative(path: str) -> str:
    try:
        return str(Path(path).relative_to(root()))
    except ValueError:
        return path


def _bm25() -> tuple[BM25Okapi | None, list[dict]]:
    path, version = chroma_client.version()
    with _lock:
        cached = _bm25_cache.get(path)
        if cached and cached[0] == version:
            return cached[1], cached[2]
        data = chroma_client.get_collection().get(include=["documents", "metadatas"])
        chunks = [{**meta, "content": doc} for doc, meta in zip(data["documents"], data["metadatas"])]
        # Index the file path and chunk name too, so "billing" or "verify_token" match by name.
        corpus = [tokenize(f"{_relative(c['source'])} {c['name']} {c['content']}") for c in chunks]
        index = BM25Okapi(corpus) if corpus else None
        _bm25_cache[path] = (version, index, chunks)
        logger.info(f"Built BM25 index over {len(chunks)} chunks")
        return index, chunks


def _sparse_retrieve(query: str, k: int) -> list[dict]:
    index, chunks = _bm25()
    if index is None:
        return []
    scores = index.get_scores(tokenize(query))
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    return [{**chunks[i], "bm25": float(scores[i])} for i in ranked[:k] if scores[i] > 0]


def _key(chunk: dict) -> tuple:
    return chunk["source"], chunk["start_line"], chunk["name"]


def retrieve(query: str, k: int = 5) -> list[dict]:
    """Top-k chunks by Reciprocal Rank Fusion of dense and BM25 results."""
    settings = config["rag"].get("hybrid", {})
    rrf_k = settings.get("rrf_k", 60)
    weights = {"dense": settings.get("dense_weight", 0.5), "sparse": settings.get("sparse_weight", 0.5)}
    fetch_k = max(k * 4, 20)  # look deeper than k in each list so fusion has candidates to work with

    total = chroma_client.get_collection().count()
    if total == 0:
        return []
    fetch_k = min(fetch_k, total)
    ranked_lists = {
        "dense": dense_retrieve(query, k=fetch_k),
        "sparse": _sparse_retrieve(query, k=fetch_k),
    }

    fused: dict[tuple, dict] = {}
    for kind, results in ranked_lists.items():
        for rank, chunk in enumerate(results, start=1):
            entry = fused.setdefault(_key(chunk), {**chunk, "score": 0.0})
            entry["score"] += weights[kind] / (rrf_k + rank)
            entry[f"{kind}_rank"] = rank

    results = sorted(fused.values(), key=lambda c: c["score"], reverse=True)[:k]
    for r in results:
        r["score"] = round(r["score"], 5)
        r.pop("mtime", None)
        r.pop("v", None)
    logger.info(
        f"Hybrid retrieval: {len(ranked_lists['dense'])} dense + {len(ranked_lists['sparse'])} BM25 "
        f"candidates → top {len(results)}"
    )
    return results

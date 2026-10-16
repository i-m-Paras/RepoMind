"""WorkspaceEngine: what the CLI's REPL does for one project, exposed to the web server.

    chat / resume     stream agent events (tokens, tool calls, approvals, final answer)
    history           a session's messages, shaped for display
    indexing          get_indexer() in a background thread + the file watcher
    plans             draft → approve → TaskOrchestrator in the background, with live logs

Every method runs inside the workspace's context (see repomind.workspace.activate),
so the CLI modules it calls resolve paths inside this workspace.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
import uuid
from collections import OrderedDict, defaultdict
from contextlib import AsyncExitStack
from contextvars import ContextVar
from pathlib import Path
from typing import AsyncIterator

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from repomind.agent.factory import build_agent
from repomind.cache.semantic_cache import build_semantic_cache, get_repo_domain
from repomind.config import config
from repomind.context import chroma_client
from repomind.context.indexers import semantic_chroma
from repomind.context.indexers.code_parser import get_source_files
from repomind.context.indexers.factory import get_indexer
from repomind.context.indexers.watcher import start_watcher, stop_watcher
from repomind.context.retrievers.factory import get_retriever
from repomind.llm.factory import current_model_name, user_llm
from repomind.mcp.repomind_mcp_client import get_repomind_mcp_tools
from repomind.memory.short_term import get_checkpointer_db_path
from repomind.observability.logger import get_logger
from repomind.skills.skill_tools import get_registry
from repomind.tasks.orchestrator import TaskOrchestrator
from repomind.tasks.planner import ExecutionPlan, create_plan
from repomind.tasks.recovery import RecoveryManager
from repomind.tasks.task_store import SQLiteTaskStore
from repomind.workspace import data_path, root

logger = get_logger(__name__)

_TOOL_RESULT_PREVIEW = 4000
_SKILL_FILE = "SKILL.md"
_MAX_CACHED_AGENTS = 16

# A web visitor's own GitHub token for this request (never stored); None = the server's, if allowed.
github_token: ContextVar[str | None] = ContextVar("repomind_github_token", default=None)
# Max indexable files when embeddings run on the server's free key (demo mode); None = no cap.
index_file_cap: ContextVar[int | None] = ContextVar("repomind_index_file_cap", default=None)


class SessionStore:
    """Chat sessions (= LangGraph thread_ids) with titles, in .repomind/sessions.json.

    The conversation itself lives in the SQLite checkpointer, same as the CLI's
    /new_session and /switch; this file only lists sessions so the UI can show them.
    """

    def __init__(self) -> None:
        self.path = data_path(config["memory"]["db_path"]).parent / "sessions.json"

    def _read(self) -> list[dict]:
        return json.loads(self.path.read_text()) if self.path.exists() else []

    def _write(self, sessions: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sessions, indent=2))

    def all(self) -> list[dict]:
        return sorted(self._read(), key=lambda s: s["updated_at"], reverse=True)

    def create(self) -> dict:
        now = time.time()
        session = {"id": str(uuid.uuid4()), "title": "New chat", "created_at": now, "updated_at": now}
        self._write([*self._read(), session])
        return session

    def touch(self, session_id: str, first_message: str) -> None:
        """Bump updated_at; name a 'New chat' after its first message."""
        sessions = self._read()
        session = next((s for s in sessions if s["id"] == session_id), None)
        if session is None:
            session = {"id": session_id, "title": "New chat", "created_at": time.time()}
            sessions.append(session)
        if session["title"] == "New chat":
            session["title"] = first_message.strip().splitlines()[0][:60]
        session["updated_at"] = time.time()
        self._write(sessions)

    def delete(self, session_id: str) -> None:
        self._write([s for s in self._read() if s["id"] != session_id])


class WorkspaceEngine:
    def __init__(self, path: Path, server_github: bool = True) -> None:
        self.path = path
        self.server_github = server_github  # False on a public demo: only visitors' own tokens
        self.sessions = SessionStore()
        self.tasks = SQLiteTaskStore(config.get("tasks", {}).get("db_path", ".repomind/tasks.db"))
        # One agent per (chat model + key, GitHub token, skills version): visitors bring their own keys.
        self._agents: OrderedDict[tuple, object] = OrderedDict()
        self._github_tools: dict[str, list] = {}
        self._server_github_tools: list = []
        self._skills_version = 0
        self.cache = None
        self.cache_domain = get_repo_domain(str(path))
        self.index_status = {"state": "idle", "stats": None, "error": None, "updated_at": None}
        self.plan_logs: dict[str, list[dict]] = defaultdict(list)
        self._plan_runs: dict[str, asyncio.Task] = {}
        self._index_task: asyncio.Task | None = None
        self._observer = None
        self._mcp_tools: list = []
        self._stack = AsyncExitStack()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.checkpointer = await self._stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(get_checkpointer_db_path())
        )
        await self.checkpointer.setup()
        self.cache = await build_semantic_cache()
        self._mcp_tools = await get_repomind_mcp_tools(skip={"github"})
        # The server's own GitHub token (from .env) is used only when allowed and no visitor token is given.
        self._server_github_tools = await get_repomind_mcp_tools(only={"github"}) if self.server_github else []
        self.start_indexing()
        self._observer = start_watcher(str(self.path), on_change=self._on_code_change)

    async def rebuild_agent(self) -> None:
        """Forget built agents — after skills change, so the next system prompt lists them."""
        get_registry().load()
        self._skills_version += 1
        self._agents.clear()

    async def agent_for(self):
        """The agent for the current request's connection (visitor's key + GitHub token, or the server's)."""
        settings, token = user_llm(), github_token.get()
        key = (settings, _digest(token), self._skills_version)
        if key not in self._agents:
            if token:
                if _digest(token) not in self._github_tools:
                    self._github_tools[_digest(token)] = await get_repomind_mcp_tools(
                        overrides={"GITHUB_TOKEN": token}, only={"github"}
                    )
                github_tools = self._github_tools[_digest(token)]
            else:
                github_tools = self._server_github_tools
            tools = [*self._mcp_tools, *github_tools]
            get_registry().load()
            self._agents[key] = await build_agent(self.checkpointer, mcp_tools=tools, require_approval=True)
            while len(self._agents) > _MAX_CACHED_AGENTS:
                self._agents.popitem(last=False)
        self._agents.move_to_end(key)
        return self._agents[key]

    async def close(self) -> None:
        if self._observer:
            stop_watcher(self._observer)
        for task in [*self._plan_runs.values(), self._index_task]:
            if task:
                task.cancel()
        await self._stack.aclose()

    # ------------------------------------------------------------------
    # Index + cache freshness
    # ------------------------------------------------------------------

    def start_indexing(self, force: bool = False) -> None:
        running = self._index_task is not None and not self._index_task.done()
        if running and not force:
            return
        # A forced rebuild requested mid-sync runs right after the current one.
        self._index_task = asyncio.create_task(self._index(force, after=self._index_task if running else None))

    async def reindex(self) -> None:
        self.start_indexing()
        await self._index_task

    async def _index(self, force: bool, after: asyncio.Task | None = None) -> None:
        if after is not None:
            await asyncio.gather(after, return_exceptions=True)
        self.index_status.update(state="indexing", error=None)
        try:
            cap = index_file_cap.get()
            if cap is not None:
                count = len(await asyncio.to_thread(get_source_files, str(self.path)))
                if count > cap:
                    raise ValueError(
                        f"{count} code files is more than the free demo indexes ({cap}). "
                        "Connect your own Gemini API key to index bigger projects, or delete some files."
                    )
            if force:
                await asyncio.to_thread(self._drop_index)
            await asyncio.to_thread(get_indexer(), str(self.path))
            stats = semantic_chroma.last_sync_stats.get(str(self.path))
            self.index_status.update(state="ready", stats=stats, updated_at=time.time())
            await self.invalidate_cache()
        except Exception as e:
            logger.exception(f"[{self.path.name}] indexing failed")
            self.index_status.update(state="error", error=str(e), updated_at=time.time())

    def _collection(self):
        return chroma_client.get_collection()

    def _drop_index(self) -> None:
        try:
            chroma_client.get_client().delete_collection(config["chromadb"]["collection_name"])
        except Exception:
            pass  # nothing indexed yet

    def chunk_count(self) -> int:
        if config["vector_store"]["provider"] != "chroma":
            return -1  # unknown: listing chunks is only implemented for Chroma
        try:
            return self._collection().count()
        except Exception:
            return 0

    def list_chunks(self, limit: int, offset: int) -> dict:
        if config["vector_store"]["provider"] != "chroma":
            return {"total": 0, "chunks": []}
        collection = self._collection()
        data = collection.get(limit=limit, offset=offset, include=["documents", "metadatas"])
        chunks = [
            {**meta, "source": _relative(meta["source"]), "content": doc}
            for doc, meta in zip(data["documents"], data["metadatas"])
        ]
        return {"total": collection.count(), "chunks": chunks}

    def search(self, query: str, k: int) -> list[dict]:
        results = get_retriever()(query, k=k)
        for r in results:
            r["source"] = _relative(r["source"])
            distance = r.pop("distance", None)
            if "score" not in r and distance is not None:  # dense-only mode: show similarity
                r["score"] = round(1 - float(distance), 4)
        return results

    def index_file(self, path: Path) -> None:
        if config["vector_store"]["provider"] == "chroma":
            semantic_chroma.index_single_file(str(path))
        else:
            get_indexer()(str(self.path))

    def unindex_file(self, path: Path) -> None:
        if config["vector_store"]["provider"] == "chroma":
            semantic_chroma.remove_file_from_index(str(path))

    def _on_code_change(self) -> None:
        # Called from the watcher thread: hop onto the event loop to invalidate the cache.
        if self.cache is not None and self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.invalidate_cache(), self._loop)

    async def invalidate_cache(self) -> None:
        if self.cache is not None:
            try:
                await self.cache.invalidate_domain(self.cache_domain)
            except Exception as e:
                logger.warning(f"Cache invalidation failed: {e}")

    # ------------------------------------------------------------------
    # Skills (the same .repomind/skills folder the CLI reads)
    # ------------------------------------------------------------------

    def skills_dir(self) -> Path:
        return data_path(config.get("skills", {}).get("skills_dir", ".repomind/skills"))

    def describe_skills(self) -> list[dict]:
        registry = get_registry()
        registry.load()
        return [
            {
                "name": name,
                "description": skill["meta"].get("description", ""),
                "source": "workspace",
                "content": (skill["skill_dir"] / _SKILL_FILE).read_text(),
            }
            for name, skill in registry._skills.items()
        ]

    async def save_skill(self, name: str, content: str) -> None:
        folder = self.skills_dir() / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / _SKILL_FILE).write_text(content)
        await self.rebuild_agent()

    async def delete_skill(self, name: str) -> None:
        folder = self.skills_dir() / name
        if not (folder / _SKILL_FILE).exists():
            raise FileNotFoundError(f"Skill not found: {name}")
        shutil.rmtree(folder)
        await self.rebuild_agent()

    # ------------------------------------------------------------------
    # Chat (/ask)
    # ------------------------------------------------------------------

    @staticmethod
    def _thread(session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}, "recursion_limit": 60}

    async def chat(self, session_id: str, message: str) -> AsyncIterator[dict]:
        """Stream one user turn. Events: token, tool_call, tool_result, approval, done, error."""
        try:
            agent = await self.agent_for()
        except Exception as e:  # e.g. no server key and the visitor hasn't connected one
            yield {"type": "error", "message": f"Agent unavailable: {e}"}
            return
        self.sessions.touch(session_id, first_message=message)
        cfg = self._thread(session_id)

        # A pending approval leaves tool calls unanswered; reject them so the new message can proceed.
        state = await agent.aget_state(cfg)
        if state.interrupts:
            count = len(state.interrupts[0].value.get("action_requests", []))
            reject = {"type": "reject", "message": "The user sent a new message instead of approving this."}
            async for _ in self._stream(agent, Command(resume={"decisions": [reject] * count}), cfg):
                pass

        # Same semantic cache as the CLI's handle_query: a hit skips the agent entirely.
        model = current_model_name()
        if self.cache is not None:
            try:
                cached = await self.cache.get(message, domain=self.cache_domain, model=model)
            except Exception as e:
                logger.warning(f"Semantic cache lookup failed: {e}")
                cached = None
            if cached is not None:
                # Record the turn so follow-up questions still see it in memory.
                await agent.aupdate_state(cfg, {"messages": [HumanMessage(message), AIMessage(cached)]},
                                               as_node="model")
                yield {"type": "done", "text": cached, "cached": True}
                return

        final = None
        async for event in self._stream(agent, {"messages": [{"role": "user", "content": message}]}, cfg):
            if event["type"] == "done":
                final = event["text"]
            yield event

        # A turn that stopped for an approval has no "done" event, so it is never cached.
        if self.cache is not None and final:
            try:
                ttl = config.get("semantic_cache", {}).get("ttl", 86400)
                await self.cache.put(message, final, domain=self.cache_domain, model=model, ttl=ttl)
            except Exception as e:
                logger.warning(f"Semantic cache store failed: {e}")

    async def resume(self, session_id: str, decisions: list[dict]) -> AsyncIterator[dict]:
        """Continue after an approval prompt. decisions: one {type: approve|edit|reject, ...} per request."""
        try:
            agent = await self.agent_for()
        except Exception as e:
            yield {"type": "error", "message": f"Agent unavailable: {e}"}
            return
        async for event in self._stream(agent, Command(resume={"decisions": decisions}), self._thread(session_id)):
            yield event

    async def _stream(self, agent, payload, cfg: dict) -> AsyncIterator[dict]:
        try:
            async for mode, chunk in agent.astream(payload, cfg, stream_mode=["updates", "messages"]):
                if mode == "messages":
                    message, meta = chunk
                    if meta.get("langgraph_node") == "model" and isinstance(message, AIMessageChunk) and message.text:
                        yield {"type": "token", "text": message.text}
                    continue
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        yield {"type": "approval", "requests": _approval_requests(update[0].value)}
                        continue
                    for message in _update_messages(update):
                        for event in _message_events(message):
                            yield event

            state = await agent.aget_state(cfg)
            if not state.interrupts:
                messages = state.values.get("messages", [])
                last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
                yield {"type": "done", "text": last.text if last else ""}
        except Exception as e:
            logger.exception("Agent error")
            yield {"type": "error", "message": str(e)}

    async def history(self, session_id: str) -> dict:
        """Messages grouped into turns: user text, then assistant parts (text + tool calls with results)."""
        try:
            agent = await self.agent_for()  # any agent reads the same checkpointed history
        except Exception:
            return {"messages": [], "pending_approval": None}
        state = await agent.aget_state(self._thread(session_id))
        turns: list[dict] = []
        tool_parts: dict[str, dict] = {}
        for message in (state.values or {}).get("messages", []):
            if isinstance(message, HumanMessage):
                turns.append({"role": "user", "text": message.text})
                continue
            if not turns or turns[-1]["role"] != "assistant":
                turns.append({"role": "assistant", "parts": []})
            parts = turns[-1]["parts"]
            if isinstance(message, AIMessage):
                if message.text:
                    parts.append({"kind": "text", "text": message.text})
                for call in message.tool_calls:
                    part = {"kind": "tool", "id": call["id"], "name": call["name"], "args": call["args"]}
                    tool_parts[call["id"]] = part
                    parts.append(part)
            elif isinstance(message, ToolMessage) and message.tool_call_id in tool_parts:
                tool_parts[message.tool_call_id].update(
                    result=message.text[:_TOOL_RESULT_PREVIEW], status=message.status
                )
        pending = _approval_requests(state.interrupts[0].value) if state.interrupts else None
        return {"messages": turns, "pending_approval": pending}

    async def delete_session(self, session_id: str) -> None:
        await self.checkpointer.adelete_thread(session_id)
        self.sessions.delete(session_id)

    # ------------------------------------------------------------------
    # Plans (/plan, /task_status)
    # ------------------------------------------------------------------

    async def draft_plan(self, goal: str, feedback: str = "") -> ExecutionPlan:
        return await asyncio.to_thread(create_plan, goal, feedback)

    def approve_plan(self, goal: str, plan: ExecutionPlan) -> str:
        project_id = self.tasks.create_project(goal, plan)
        self.start_plan(project_id)
        return project_id

    def start_plan(self, project_id: str, retry_failed: bool = False) -> None:
        if self.plan_running(project_id):
            return
        if retry_failed:
            self.tasks.reset_for_retry(project_id)
        self._plan_runs[project_id] = asyncio.create_task(self._run_plan(project_id))

    def plan_running(self, project_id: str) -> bool:
        task = self._plan_runs.get(project_id)
        return task is not None and not task.done()

    async def _run_plan(self, project_id: str) -> None:
        def notify(message: str) -> None:
            if message:
                message = message.replace(f"@{project_id[:8]}", "")  # task-id suffix, see display_task
                self.plan_logs[project_id].append({"t": time.time(), "msg": message})

        try:
            recovered = RecoveryManager(self.tasks).recover(project_id)  # tasks a crash left in_progress
            if recovered:
                notify(f"Recovered {recovered} interrupted task(s)")
            await TaskOrchestrator(self.tasks, max_concurrent=1, notify=notify).run(project_id)
            progress = self.tasks.get_progress(project_id)
            if progress.get("failed") or progress.get("blocked") or progress.get("pending"):
                notify("Finished with issues — retry failed tasks to try again.")
            else:
                notify(f"All {progress.get('completed', 0)} tasks completed.")
            # Re-index so chat questions can find the files the plan just generated.
            notify("Re-indexing generated files...")
            await self.reindex()
            notify("Index updated — ask about the new code in Chat.")
        except asyncio.CancelledError:
            notify("Stopped (server shutting down). Resume it to continue.")
            raise
        except Exception as e:
            notify(f"Plan run crashed: {e}")
            logger.exception("Plan run crashed")

    def plan_status(self, project: dict) -> str:
        if self.plan_running(project["id"]):
            return "running"
        progress = self.tasks.get_progress(project["id"])
        if not progress or progress.get("pending") == sum(progress.values()):
            return "approved"
        if set(progress) <= {"completed", "skipped"}:
            return "completed"
        return "finished_with_issues"


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _digest(secret: str | None) -> str | None:
    return hashlib.sha256(secret.encode()).hexdigest()[:16] if secret else None


def display_task(task: dict) -> dict:
    """Drop the per-project suffix the task store adds to ids ("task_001@1a2b3c4d" → "task_001")."""
    return {**task, "id": task["id"].split("@")[0]}


def _relative(path: str) -> str:
    try:
        return str(Path(path).relative_to(root()))
    except ValueError:
        return path


def _update_messages(update) -> list[BaseMessage]:
    if not isinstance(update, dict):
        return []
    messages = update.get("messages") or []
    if not isinstance(messages, list):
        messages = [messages]
    return [m for m in messages if isinstance(m, BaseMessage)]


def _message_events(message: BaseMessage) -> list[dict]:
    if isinstance(message, AIMessage):
        return [{"type": "tool_call", "id": c["id"], "name": c["name"], "args": c["args"]} for c in message.tool_calls]
    if isinstance(message, ToolMessage):
        return [{
            "type": "tool_result",
            "id": message.tool_call_id,
            "name": message.name,
            "content": message.text[:_TOOL_RESULT_PREVIEW],
            "status": message.status,
        }]
    return []


def _approval_requests(value: dict) -> list[dict]:
    """HITL interrupt payload → [{name, args, description, allowed_decisions}]."""
    actions = value.get("action_requests", [])
    reviews = value.get("review_configs", [])
    return [
        {
            "name": action.get("name"),
            "args": action.get("args", {}),
            "description": action.get("description", ""),
            "allowed_decisions": (reviews[i] if i < len(reviews) else {}).get("allowed_decisions", ["approve", "reject"]),
        }
        for i, action in enumerate(actions)
    ]

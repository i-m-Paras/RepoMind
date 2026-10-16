"""Web API + the React UI (web/dist) for RepoMind.

    repomind-web                                  # http://localhost:8000
    uvicorn repomind.server.app:app --reload      # during development (with `npm run dev` in web/)

Each workspace is a folder under WORKSPACES_DIR. A request for /api/workspaces/{name}/...
activates that folder (repomind.workspace.activate), so the same modules the CLI uses
work on it — sandboxed, so the agent's tools can't touch files outside it.

Bring your own key: the browser can send its own provider + API key + model (and a
GitHub token) as X-LLM-* / X-GitHub-Token headers. They're used for that request and
the work it starts, held only in memory, and never stored or logged.

DEMO_MODE=true (the Docker image) is for a public link:
  - every visitor (X-Visitor-Id, a random id kept in their browser) gets private workspaces
  - visitors without their own key use the server's Gemini key with a small free allowance
    (FREE_CHAT_LIMIT messages per day; plans and big repos need their own key)
  - the server's GITHUB_TOKEN is never used — only a visitor's own
Keep ALLOW_SHELL off for a public link. APP_ACCESS_TOKEN puts the whole site behind a password.
"""

from __future__ import annotations

import asyncio
import hmac
import io
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import zipfile
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from repomind.config import config
from repomind.context import chroma_client
from repomind.llm.factory import DEFAULT_MODELS, PROVIDERS, UserLLM, build_chat_model, use_user_llm
from repomind.mcp.repomind_mcp_config import load_repomind_mcp_configs
from repomind.observability.logger import get_logger
from repomind.server.engine import WorkspaceEngine, display_task, github_token, index_file_cap
from repomind.tasks.planner import ExecutionPlan
from repomind.workspace import SKIP_DIRS, activate, resolve

logger = get_logger(__name__)


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_DIST = PROJECT_ROOT / "web" / "dist"
EXAMPLES_DIR = PROJECT_ROOT / "examples"
WORKSPACES_DIR = Path(os.getenv("WORKSPACES_DIR", PROJECT_ROOT / "workspaces")).resolve()
ACCESS_TOKEN = os.getenv("APP_ACCESS_TOKEN", "")
ALLOW_SHELL = _flag("ALLOW_SHELL", "false")
DEMO_MODE = _flag("DEMO_MODE", "false")
FREE_CHAT_LIMIT = int(os.getenv("FREE_CHAT_LIMIT", "2"))        # messages / visitor / 24h on the server key
FREE_PLANS = _flag("FREE_PLANS", "false")                       # plans make many LLM calls
FREE_MAX_FILES = int(os.getenv("FREE_MAX_FILES", "150"))        # indexable files per workspace on the server key
MAX_WORKSPACES = int(os.getenv("MAX_WORKSPACES", "3"))          # per visitor, demo mode
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20" if DEMO_MODE else "50")) * 1024 * 1024
MAX_FILE_LIST = 5000
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
VISITOR = re.compile(r"^[A-Za-z0-9-]{8,64}$")
GIT_URL = re.compile(r"^https://[\w.-]+(:\d+)?/[\w.~/-]+?(\.git)?/?$")


# ----------------------------------------------------------------------
# Who is calling: visitor id, their own keys (if any), free allowance
# ----------------------------------------------------------------------

@dataclass
class Caller:
    visitor: str            # namespace for workspaces in demo mode
    ip: str                 # what the free allowance is counted against
    llm: UserLLM | None     # their own chat model + key, or None = server key
    github: bool            # sent their own GitHub token

    @property
    def free(self) -> bool:
        """Limited by the free allowance: demo mode and no key of their own."""
        return DEMO_MODE and self.llm is None

    @property
    def base(self) -> Path:
        return WORKSPACES_DIR / self.visitor if DEMO_MODE else WORKSPACES_DIR


class FreeAllowance:
    """Messages per visitor (by IP) in a rolling 24h window, in memory (resets on restart)."""

    def __init__(self) -> None:
        self._used: dict[str, deque] = defaultdict(deque)

    def used(self, ip: str) -> int:
        window = self._used[ip]
        while window and window[0] < time.time() - 86400:
            window.popleft()
        return len(window)

    def consume(self, ip: str) -> None:
        if self.used(ip) >= FREE_CHAT_LIMIT:
            raise HTTPException(402, f"You've used the {FREE_CHAT_LIMIT} free messages for today. "
                                     "Connect your own API key (free from Google AI Studio) to keep going.")
        self._used[ip].append(time.time())

    def refund(self, ip: str) -> None:
        if self._used[ip]:
            self._used[ip].pop()


allowance = FreeAllowance()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _user_llm(request: Request) -> UserLLM | None:
    key = request.headers.get("x-llm-key", "").strip()
    if not key:
        return None
    provider = request.headers.get("x-llm-provider", "gemini").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}' (use {', '.join(PROVIDERS)})")
    model = request.headers.get("x-llm-model", "").strip() or DEFAULT_MODELS[provider]
    if len(key) > 300 or len(model) > 100 or not re.match(r"^[\w.:/-]+$", model):
        raise ValueError("Invalid API key or model name")
    return UserLLM(provider=provider, api_key=key, model=model)


# Async on purpose: FastAPI runs async dependencies in the request's own context, so the
# contextvars set here are visible to the endpoint and to the background tasks it starts.
async def caller(request: Request) -> Caller:
    visitor = request.headers.get("x-visitor-id", "").strip()
    if DEMO_MODE and not VISITOR.match(visitor):
        raise ValueError("Missing visitor id — reload the page")
    who = Caller(
        visitor=visitor if DEMO_MODE else "",
        ip=_client_ip(request),
        llm=_user_llm(request),
        github=bool(request.headers.get("x-github-token", "").strip()),
    )
    use_user_llm(who.llm)
    github_token.set(request.headers.get("x-github-token", "").strip()[:300] or None)
    # Embeddings run on the server's Gemini key unless the visitor brought a Gemini key.
    own_embeddings = who.llm is not None and who.llm.provider == "gemini"
    index_file_cap.set(FREE_MAX_FILES if DEMO_MODE and not own_embeddings else None)
    return who


# ----------------------------------------------------------------------
# Workspaces: folders under WORKSPACES_DIR (per visitor in demo mode), one engine each
# ----------------------------------------------------------------------

def workspace_path(who: Caller, name: str) -> Path:
    if not NAME.match(name or ""):
        raise ValueError("Workspace names use letters, digits, '-', '_' and '.' (max 64)")
    return who.base / name


def existing_workspace(who: Caller, name: str) -> Path:
    path = workspace_path(who, name)
    if not path.is_dir():
        raise FileNotFoundError(f"Workspace not found: {name}")
    return path


def list_names(base: Path) -> list[str]:
    if not base.is_dir():
        return []
    return [p.name for p in sorted(base.iterdir()) if p.is_dir() and NAME.match(p.name)]


def seed_examples(base: Path) -> None:
    """First visit: copy examples/ in so there's something to try right away."""
    marker = base / ".seeded"
    if marker.exists() or not EXAMPLES_DIR.is_dir():
        return
    base.mkdir(parents=True, exist_ok=True)
    for example in EXAMPLES_DIR.iterdir():
        if example.is_dir() and not (base / example.name).exists():
            shutil.copytree(example, base / example.name)
            logger.info(f"Seeded example workspace: {example.name}")
    marker.touch()


class EngineManager:
    def __init__(self) -> None:
        self._engines: dict[Path, WorkspaceEngine] = {}
        self._lock = asyncio.Lock()

    async def get(self, path: Path) -> WorkspaceEngine:
        async with self._lock:
            if path not in self._engines:
                engine = WorkspaceEngine(path, server_github=not DEMO_MODE)
                await engine.start()
                self._engines[path] = engine
            return self._engines[path]

    async def drop(self, path: Path) -> None:
        async with self._lock:
            engine = self._engines.pop(path, None)
        if engine:
            await engine.close()

    async def close_all(self) -> None:
        for path in list(self._engines):
            await self.drop(path)


engines = EngineManager()


async def ws_dir(name: str, who: Caller = Depends(caller)) -> Path:
    path = existing_workspace(who, name)
    activate(path, sandboxed=True, allow_shell=ALLOW_SHELL)
    return path


async def ws_engine(path: Path = Depends(ws_dir)) -> WorkspaceEngine:
    return await engines.get(path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not DEMO_MODE:
        seed_examples(WORKSPACES_DIR)
    yield
    await engines.close_all()


app = FastAPI(title="RepoMind", lifespan=lifespan)


@app.middleware("http")
async def require_token(request: Request, call_next):
    path = request.url.path
    if ACCESS_TOKEN and path.startswith("/api/") and path not in ("/api/health", "/api/info"):
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, ACCESS_TOKEN):
            return JSONResponse({"detail": "Invalid or missing access token"}, status_code=401)
    return await call_next(request)


@app.exception_handler(FileNotFoundError)
async def _not_found(_: Request, e: FileNotFoundError):
    return JSONResponse({"detail": str(e)}, status_code=404)


@app.exception_handler(FileExistsError)
async def _conflict(_: Request, e: FileExistsError):
    return JSONResponse({"detail": str(e)}, status_code=409)


@app.exception_handler(ValueError)
async def _bad_request(_: Request, e: ValueError):
    return JSONResponse({"detail": str(e)}, status_code=400)


def _sse(events: AsyncIterator[dict]) -> StreamingResponse:
    async def body():
        async for event in events:
            yield f"data: {json.dumps(event, default=str)}\n\n"
    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ----------------------------------------------------------------------
# App info, plan / allowance, connection test
# ----------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/info")
async def info():
    return {
        "name": "RepoMind",
        "auth_required": bool(ACCESS_TOKEN),
        "demo_mode": DEMO_MODE,
        "llm": f"gemini / {os.getenv('GEMINI_MODEL') or config['llm']['model']}",
        "embeddings": f"gemini / {os.getenv('GEMINI_EMBEDDING_MODEL') or config['embeddings']['model']}",
        "vector_store": config["vector_store"]["provider"],
        "rag_mode": config["rag"]["mode"],
        "approvals": ["file changes", "shell commands"] if ALLOW_SHELL else ["file changes"],
        "allow_shell": ALLOW_SHELL,
        "semantic_cache": bool(config["semantic_cache"]["enabled"]),
        "watcher": True,
        "mcp_servers": list(load_repomind_mcp_configs(skip={"github"} if DEMO_MODE else None)),
        "api_key_set": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        "providers": {p: DEFAULT_MODELS[p] for p in PROVIDERS},
    }


@app.get("/api/me")
async def me(who: Caller = Depends(caller)):
    """What this visitor can do right now: free allowance, or their own connected key."""
    return {
        "own_key": who.llm is not None,
        "provider": who.llm.provider if who.llm else "gemini",
        "model": who.llm.model if who.llm else (os.getenv("GEMINI_MODEL") or config["llm"]["model"]),
        "github": who.github,
        "limited": who.free,
        "chat_limit": FREE_CHAT_LIMIT if who.free else None,
        "chats_left": max(0, FREE_CHAT_LIMIT - allowance.used(who.ip)) if who.free else None,
        "plans_allowed": not who.free or FREE_PLANS,
        "max_files": index_file_cap.get(),
    }


@app.post("/api/connection/test")
async def test_connection(who: Caller = Depends(caller)):
    """Check the visitor's key (one tiny model call) and GitHub token before they save them."""
    result: dict = {"llm": None, "github": None}
    if who.llm:
        try:
            llm = build_chat_model(who.llm.provider, who.llm.model, who.llm.api_key, max_tokens=16)
            await llm.ainvoke("Reply with: ok")
            result["llm"] = {"ok": True, "message": f"{who.llm.provider} / {who.llm.model} works"}
        except Exception as e:
            result["llm"] = {"ok": False, "message": _short_error(e)}
    token = github_token.get()
    if token:
        result["github"] = await asyncio.to_thread(_check_github, token)
    return result


def _check_github(token: str) -> dict:
    request = urllib.request.Request("https://api.github.com/user", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "RepoMind",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return {"ok": True, "message": f"Connected as {json.load(response).get('login')}"}
    except Exception as e:
        return {"ok": False, "message": f"GitHub rejected the token ({_short_error(e)})"}


def _short_error(error: Exception) -> str:
    text = str(error)
    return (text[:240] + "…") if len(text) > 240 else text


def require_plans(who: Caller) -> None:
    if who.free and not FREE_PLANS:
        raise HTTPException(402, "Plans run many model calls, so they need your own API key. "
                                 "Connect one (Gemini keys are free at aistudio.google.com) to use Plans.")


# ----------------------------------------------------------------------
# Workspaces
# ----------------------------------------------------------------------

class WorkspaceIn(BaseModel):
    name: str
    git_url: str | None = None


@app.get("/api/workspaces")
async def workspaces(who: Caller = Depends(caller)):
    if DEMO_MODE:
        seed_examples(who.base)
    return [{"name": name} for name in list_names(who.base)]


@app.post("/api/workspaces")
async def new_workspace(body: WorkspaceIn, who: Caller = Depends(caller)):
    target = workspace_path(who, body.name)
    if target.exists():
        raise FileExistsError(f"Workspace already exists: {body.name}")
    if DEMO_MODE and len(list_names(who.base)) >= MAX_WORKSPACES:
        raise ValueError(f"The demo allows {MAX_WORKSPACES} workspaces — delete one first")
    if not body.git_url:
        target.mkdir(parents=True)
        return {"name": body.name}

    if not GIT_URL.match(body.git_url):
        raise ValueError("Only public https:// git URLs are supported, e.g. https://github.com/user/repo")
    result = await asyncio.to_thread(
        subprocess.run, ["git", "clone", "--depth", "1", "--", body.git_url, str(target)],
        capture_output=True, text=True, timeout=180, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError(f"git clone failed: {result.stderr.strip()[-500:]}")
    shutil.rmtree(target / ".git", ignore_errors=True)
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    if size > MAX_UPLOAD_BYTES * 5:
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError(f"Repository is too large ({size // 1024 // 1024} MB)")
    return {"name": body.name}


@app.delete("/api/workspaces/{name}")
async def remove_workspace(path: Path = Depends(ws_dir)):
    await engines.drop(path)
    chroma_client.forget(path)
    shutil.rmtree(path)
    return {"ok": True}


@app.get("/api/workspaces/{name}/status")
async def workspace_status(engine: WorkspaceEngine = Depends(ws_engine)):
    skills = [s["name"] for s in await asyncio.to_thread(engine.describe_skills)]
    return {"index": engine.index_status, "chunks": await asyncio.to_thread(engine.chunk_count), "skills": skills}


# ----------------------------------------------------------------------
# Files
# ----------------------------------------------------------------------

class FileIn(BaseModel):
    path: str
    content: str


def _iter_files(base: Path):
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            yield Path(current) / f


@app.get("/api/workspaces/{name}/files")
async def list_files(base: Path = Depends(ws_dir)):
    files = []
    for path in _iter_files(base):
        files.append({"path": str(path.relative_to(base)), "size": path.stat().st_size})
        if len(files) >= MAX_FILE_LIST:
            break
    return {"files": files, "truncated": len(files) >= MAX_FILE_LIST}


@app.get("/api/workspaces/{name}/file")
async def read_file(path: str, base: Path = Depends(ws_dir)):
    target = resolve(path)
    if not target.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    if target.stat().st_size > 2_000_000:
        return {"path": path, "content": None, "reason": "File is larger than 2 MB"}
    try:
        return {"path": path, "content": target.read_text(encoding="utf-8")}
    except UnicodeDecodeError:
        return {"path": path, "content": None, "reason": "Binary file"}


@app.get("/api/workspaces/{name}/download")
async def download_file(path: str, base: Path = Depends(ws_dir)):
    target = resolve(path)
    if not target.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    return FileResponse(target, filename=target.name)


@app.put("/api/workspaces/{name}/file")
async def write_file(body: FileIn, engine: WorkspaceEngine = Depends(ws_engine)):
    target = resolve(body.path)
    if any(part in SKIP_DIRS for part in target.relative_to(engine.path).parts):
        raise ValueError(f"Can't write inside {', '.join(sorted(SKIP_DIRS))}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    try:  # the file is saved; a failed re-index shouldn't fail the request
        await asyncio.to_thread(engine.index_file, target)
    except Exception as e:
        logger.warning(f"Re-index after save failed: {e}")
    await engine.invalidate_cache()
    return {"ok": True}


@app.delete("/api/workspaces/{name}/file")
async def delete_file(path: str, engine: WorkspaceEngine = Depends(ws_engine)):
    target = resolve(path)
    if target == engine.path:
        raise ValueError("Delete the workspace instead")
    if target.is_dir():
        shutil.rmtree(target)
        engine.start_indexing()
    elif target.is_file():
        target.unlink()
        await asyncio.to_thread(engine.unindex_file, target)
        await engine.invalidate_cache()
    else:
        raise FileNotFoundError(f"Not found: {path}")
    return {"ok": True}


@app.post("/api/workspaces/{name}/upload")
async def upload(
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(default=[]),
    target_dir: str = Form(default=""),
    extract_zip: bool = Form(default=True),
    engine: WorkspaceEngine = Depends(ws_engine),
):
    """Upload files (folder uploads send each file's relative path in `paths`). Zips are extracted."""
    written, total = 0, 0
    for i, upload_file in enumerate(files):
        data = await upload_file.read()
        total += len(data)
        if total > MAX_UPLOAD_BYTES:
            raise ValueError(f"Upload exceeds {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
        rel = paths[i] if i < len(paths) and paths[i] else upload_file.filename
        if extract_zip and rel.lower().endswith(".zip"):
            written += _extract_zip(data, target_dir)
            continue
        if _skipped(rel):
            continue
        target = resolve(f"{target_dir}/{rel}" if target_dir else rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written += 1
    engine.start_indexing()
    return {"written": written}


def _skipped(rel: str) -> bool:
    return any(part in SKIP_DIRS for part in Path(rel).parts)


def _extract_zip(data: bytes, target_dir: str) -> int:
    """Extract a zip safely: every member path goes through resolve() (no zip-slip)."""
    written, total = 0, 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        # Strip a single top-level folder ("repo-main/...") so files land at the workspace root.
        roots = {n.split("/", 1)[0] for n in names}
        strip = len(roots) == 1 and all("/" in n for n in names)
        for member in names:
            rel = member.split("/", 1)[1] if strip else member
            if not rel or _skipped(rel) or "__MACOSX" in member:
                continue
            total += archive.getinfo(member).file_size
            if total > MAX_UPLOAD_BYTES * 4:
                raise ValueError("Zip expands to more than the allowed size")
            try:
                target = resolve(f"{target_dir}/{rel}" if target_dir else rel)
            except ValueError:
                logger.warning(f"Skipping unsafe zip entry: {member}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
            written += 1
    return written


# ----------------------------------------------------------------------
# Index (/show_index, /reindex)
# ----------------------------------------------------------------------

class ReindexIn(BaseModel):
    force: bool = False


class SearchIn(BaseModel):
    query: str
    k: int = 5


@app.post("/api/workspaces/{name}/index")
async def reindex(body: ReindexIn, engine: WorkspaceEngine = Depends(ws_engine)):
    engine.start_indexing(force=body.force)
    return {"index": engine.index_status}


@app.get("/api/workspaces/{name}/index/chunks")
async def chunks(limit: int = 50, offset: int = 0, engine: WorkspaceEngine = Depends(ws_engine)):
    return await asyncio.to_thread(engine.list_chunks, max(1, min(limit, 200)), max(0, offset))


@app.post("/api/workspaces/{name}/index/search")
async def search(body: SearchIn, engine: WorkspaceEngine = Depends(ws_engine)):
    return {"results": await asyncio.to_thread(engine.search, body.query, max(1, min(body.k, 20)))}


# ----------------------------------------------------------------------
# Chat (/ask, /new_session, /switch)
# ----------------------------------------------------------------------

class ChatIn(BaseModel):
    session_id: str
    message: str


class ResumeIn(BaseModel):
    session_id: str
    decisions: list[dict]


@app.get("/api/workspaces/{name}/sessions")
async def sessions(engine: WorkspaceEngine = Depends(ws_engine)):
    return engine.sessions.all()


@app.post("/api/workspaces/{name}/sessions")
async def new_session(engine: WorkspaceEngine = Depends(ws_engine)):
    return engine.sessions.create()


@app.delete("/api/workspaces/{name}/sessions/{session_id}")
async def delete_session(session_id: str, engine: WorkspaceEngine = Depends(ws_engine)):
    await engine.delete_session(session_id)
    return {"ok": True}


@app.get("/api/workspaces/{name}/sessions/{session_id}")
async def session_history(session_id: str, engine: WorkspaceEngine = Depends(ws_engine)):
    return await engine.history(session_id)


@app.post("/api/workspaces/{name}/chat")
async def chat(body: ChatIn, engine: WorkspaceEngine = Depends(ws_engine), who: Caller = Depends(caller)):
    if not body.message.strip():
        raise ValueError("Message is empty")
    if not who.free:
        return _sse(engine.chat(body.session_id, body.message))

    allowance.consume(who.ip)  # 402 once the free messages are used up

    async def counted():
        async for event in engine.chat(body.session_id, body.message):
            # Answers from the semantic cache (no model call) and failed turns don't use up a message
            if event["type"] == "error" or (event["type"] == "done" and event.get("cached")):
                allowance.refund(who.ip)
            yield event
    return _sse(counted())


@app.post("/api/workspaces/{name}/chat/resume")
async def chat_resume(body: ResumeIn, engine: WorkspaceEngine = Depends(ws_engine)):
    return _sse(engine.resume(body.session_id, body.decisions))


# ----------------------------------------------------------------------
# Skills
# ----------------------------------------------------------------------

class SkillIn(BaseModel):
    content: str


@app.get("/api/workspaces/{name}/skills")
async def skills(engine: WorkspaceEngine = Depends(ws_engine)):
    return await asyncio.to_thread(engine.describe_skills)


@app.put("/api/workspaces/{name}/skills/{skill}")
async def save_skill(skill: str, body: SkillIn, engine: WorkspaceEngine = Depends(ws_engine)):
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", skill):
        raise ValueError("Skill names use lowercase letters, digits, '-' and '_'")
    await engine.save_skill(skill, body.content)
    return {"ok": True}


@app.delete("/api/workspaces/{name}/skills/{skill}")
async def delete_skill(skill: str, engine: WorkspaceEngine = Depends(ws_engine)):
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", skill):
        raise ValueError("Invalid skill name")
    await engine.delete_skill(skill)
    return {"ok": True}


# ----------------------------------------------------------------------
# Plans (/plan, /task_status)
# ----------------------------------------------------------------------

class DraftIn(BaseModel):
    goal: str
    feedback: str = ""


class ApproveIn(BaseModel):
    goal: str
    plan: ExecutionPlan


class ResumePlanIn(BaseModel):
    retry_failed: bool = False


@app.post("/api/workspaces/{name}/plans/draft")
async def draft_plan(body: DraftIn, engine: WorkspaceEngine = Depends(ws_engine), who: Caller = Depends(caller)):
    require_plans(who)
    if not body.goal.strip():
        raise ValueError("Goal is empty")
    return (await engine.draft_plan(body.goal, body.feedback)).model_dump(mode="json")


@app.post("/api/workspaces/{name}/plans")
async def approve_plan(body: ApproveIn, engine: WorkspaceEngine = Depends(ws_engine), who: Caller = Depends(caller)):
    require_plans(who)
    return {"project_id": engine.approve_plan(body.goal, body.plan)}


@app.get("/api/workspaces/{name}/plans")
async def plans(engine: WorkspaceEngine = Depends(ws_engine)):
    return [
        {**p, "status": engine.plan_status(p), "running": engine.plan_running(p["id"])}
        for p in engine.tasks.list_projects()
    ]


@app.get("/api/workspaces/{name}/plans/{project_id}")
async def plan_detail(project_id: str, engine: WorkspaceEngine = Depends(ws_engine)):
    project = engine.tasks.get_project(project_id)
    if not project:
        raise FileNotFoundError(f"Plan not found: {project_id}")
    return {
        **project,
        "status": engine.plan_status(project),
        "tasks": [display_task(t) for t in engine.tasks.get_all_tasks(project_id)],
        "progress": engine.tasks.get_progress(project_id),
        "running": engine.plan_running(project_id),
        "logs": engine.plan_logs.get(project_id, [])[-300:],
    }


@app.post("/api/workspaces/{name}/plans/{project_id}/resume")
async def resume_plan(project_id: str, body: ResumePlanIn, engine: WorkspaceEngine = Depends(ws_engine),
                      who: Caller = Depends(caller)):
    require_plans(who)
    if not engine.tasks.get_project(project_id):
        raise FileNotFoundError(f"Plan not found: {project_id}")
    engine.start_plan(project_id, retry_failed=body.retry_failed)
    return {"ok": True}


@app.delete("/api/workspaces/{name}/plans/{project_id}")
async def delete_plan(project_id: str, engine: WorkspaceEngine = Depends(ws_engine)):
    if engine.plan_running(project_id):
        raise ValueError("Plan is still running")
    engine.tasks.delete_project(project_id)
    return {"ok": True}


# ----------------------------------------------------------------------
# React UI (built into web/dist by `npm run build`)
# ----------------------------------------------------------------------

if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = (WEB_DIST / full_path).resolve()
        if full_path and WEB_DIST in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")


def serve() -> None:
    import uvicorn

    uvicorn.run(
        "repomind.server.app:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        log_level="warning",
    )

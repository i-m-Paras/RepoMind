"""Which project RepoMind is working on.

The CLI works on the directory it was launched from, so root() is just Path.cwd().
The web server serves many workspaces from one process, so it wraps each request in
use_workspace(path): root() then points at that workspace, and file/shell tools are
sandboxed to it. contextvars follow asyncio tasks and asyncio.to_thread, so every
module below sees the right workspace without passing it around.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_root: ContextVar[Path | None] = ContextVar("repomind_root", default=None)
_sandboxed: ContextVar[bool] = ContextVar("repomind_sandboxed", default=False)
_allow_shell: ContextVar[bool] = ContextVar("repomind_allow_shell", default=True)

# Never indexed or watched: RepoMind's own data folder, VCS, envs and build output.
SKIP_DIRS = {".repomind", ".git", ".venv", "venv", "env", "__pycache__", "node_modules", "dist", "build"}

# Environment variables hidden from shell commands run in a sandboxed workspace.
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def root() -> Path:
    """The project being worked on: the active workspace, or the current directory."""
    return _root.get() or Path.cwd()


def data_path(relative: str) -> Path:
    """A config path like '.repomind/tasks.db', resolved inside the project."""
    path = Path(relative)
    return path if path.is_absolute() else root() / path


def resolve(path: str) -> Path:
    """Resolve a path the agent gave a tool. In a sandboxed workspace it must stay inside it."""
    base = root().resolve()
    target = Path(path).expanduser()
    target = (target if target.is_absolute() else base / target).resolve()
    if _sandboxed.get() and target != base and base not in target.parents:
        raise ValueError(f"Path is outside the workspace: {path}")
    return target


def shell_allowed() -> bool:
    return _allow_shell.get()


def shell_env() -> dict[str, str] | None:
    """Environment for shell commands: unchanged in the CLI, without secrets in a sandbox."""
    if not _sandboxed.get():
        return None
    return {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in _SECRET_MARKERS)}


def activate(path: Path, sandboxed: bool = True, allow_shell: bool = True) -> tuple:
    """Point the current context (one web request, and the tasks it starts) at a workspace."""
    return _root.set(path.resolve()), _sandboxed.set(sandboxed), _allow_shell.set(allow_shell)


@contextmanager
def use_workspace(path: Path, sandboxed: bool = True, allow_shell: bool = True):
    tokens = activate(path, sandboxed, allow_shell)
    try:
        yield
    finally:
        for var, token in zip((_root, _sandboxed, _allow_shell), tokens):
            var.reset(token)

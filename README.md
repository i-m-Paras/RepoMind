# RepoMind

RAG-powered code assistant (like Claude Code, in your browser or terminal) running on **Gemini**. It indexes a
codebase (tree-sitter AST chunks → Gemini embeddings + BM25 → hybrid search with RRF) and then answers
questions about it and edits it. It can also:
- use MCP servers (GitHub, filesystem) and load skills,
- remember chat sessions (SQLite),
- cache answers semantically (Redis),
- run multi-step plans with an LLM judge checking each step.

**Live demo:** _add your Render link here_ — try it free (2 messages a day), or connect your own Gemini /
OpenAI / Anthropic key for unlimited use.

### How retrieval works

- **Chunking:** tree-sitter splits code into one chunk per function/class (15 languages), plus "module" chunks
  for top-level code (constants, config) so nothing is lost. Text files use sliding windows.
- **Indexing:** each chunk is embedded with a `path :: type name` header. Files are re-embedded only when they
  change (mtime), and a watcher re-indexes edits live.
- **Hybrid search:** Gemini embeddings find code that *means* the same thing as the question. BM25 (with
  code-aware tokens: `getUserById` → get, user, by, id) finds exact identifiers. Reciprocal Rank Fusion merges
  the two rankings. Weights are in `config.yaml → rag.hybrid`.

## Run locally

Needs Python 3.12 and Node.js (for the MCP servers).

```bash
git clone https://github.com/i-m-Paras/RepoMind.git && cd RepoMind
python3.12 -m venv env && env/bin/pip install -e .
cp .env.example .env    # then put your GEMINI_API_KEY in it (free at https://aistudio.google.com/apikey)
```

**Terminal app** (works on the current directory):

```bash
cd /path/to/any/project
/path/to/RepoMind/env/bin/repomind
```

Commands: `/ask <q>`, `/show_index`, `/reindex`, `/new_session`, `/switch <id>`, `/session`,
`/plan <goal>`, `/task_status`, `/exit`. Data is stored in `<project>/.repomind/`.

**Web UI** (workspaces live in `workspaces/`, seeded from `examples/`):

```bash
cd web && npm install && npm run build && cd ..   # build the React UI once (and after UI changes)
env/bin/repomind-web                              # http://127.0.0.1:8000
```

For UI development, run `env/bin/repomind-web` and `cd web && npm run dev` (http://localhost:5173, proxies `/api`).

| Page | Same as in the CLI |
|---|---|
| Chat | `/ask`, `/new_session`, `/switch`. File changes (and shell commands, if enabled) pause for approve / edit / reject |
| Files | Upload files / a folder / a `.zip`, or clone a public Git repo, then edit them |
| Index | `/show_index`, `/reindex`, plus a retrieval playground |
| Plans | `/plan` (draft → edit → approve → run) and `/task_status`, with a live log |
| Skills | `.repomind/skills/<name>/SKILL.md` in the workspace |

## Deploy (free): Render

The app needs a long-running Python server with a writable disk, background tasks and Node (for MCP).
Static/serverless hosts like Netlify or Vercel can't run it. Render's **free web service** can: it runs the
`Dockerfile` here (UI + API in one image) with no credit card, and fits easily in its 512 MB (RepoMind uses
about 200 MB).

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/i-m-Paras/RepoMind)

The button uses `render.yaml`: sign in, paste your `GEMINI_API_KEY`, click **Deploy Blueprint**. Or set it up
by hand:

1. Push this repo to GitHub (or fork https://github.com/i-m-Paras/RepoMind).
2. On https://dashboard.render.com → **New → Web Service** → connect GitHub → pick the repo.
3. Settings:
   - **Root Directory:** leave empty
   - **Language / Runtime:** Docker (detected from the Dockerfile)
   - **Instance type:** Free
   - **Environment variables:** `GEMINI_API_KEY` = your key (the free-tier key visitors share).
     Optional: `GEMINI_RPM=10`, `FREE_CHAT_LIMIT=2`.
4. **Deploy Web Service.** The first build takes ~5–10 min. The app is then live at
   `https://<service-name>.onrender.com`. Put that link at the top of this README.

Every push to `main` redeploys automatically.

**How the public demo works** (`DEMO_MODE=true`, set in the Dockerfile)
- **Private workspaces:** each visitor gets their own (keyed by a random id kept in their browser), with the
  sample project copied in.
- **Free tier, on your key:** `FREE_CHAT_LIMIT` messages per visitor per day (default 2), repos up to
  `FREE_MAX_FILES` code files (150), and no Plans (they make many calls).
- **Your-own-key upgrade:** "Connect your AI" in the sidebar takes a Gemini, OpenAI or Anthropic key (plus an
  optional GitHub token for the GitHub MCP tools). This lifts the limits. Keys stay in the visitor's browser:
  they're sent per request and never stored on the server.
- **No shell, sandboxed files:** shell commands are off (`ALLOW_SHELL=false`), and file tools can't leave the
  workspace. The server's own `GITHUB_TOKEN` is never used in demo mode.
- **Free-tier limits of Render itself:** the service sleeps after 15 minutes idle (the next visit takes about
  a minute) and storage resets on each deploy or restart.
- **Semantic cache:** off unless you set `REDIS_URL` (e.g. a free Redis Cloud database).

The same image runs on Hugging Face Spaces (Docker Spaces need a PRO account), Railway or any VM.

## Services

| Service | Needed? | Setup |
|---|---|---|
| Gemini API | **Required** | `GEMINI_API_KEY` in `.env` (https://aistudio.google.com/apikey) |
| Node.js / npx | For MCP servers | Installed locally; included in the Docker image |
| Redis Stack | Optional (semantic cache). Turns itself off if Redis isn't running | Local: `brew tap redis-stack/redis-stack && brew install --cask redis-stack`, then `redis-stack-server`. Cloud: `REDIS_URL` |
| GitHub token | Optional (GitHub MCP tools), local use only | `GITHUB_TOKEN=` in `.env` |
| Qdrant | Optional, only if `vector_store.provider: qdrant` | `QDRANT_URL` (+ `QDRANT_API_KEY`) in `.env` |

## Configuration

`repomind/config.yaml` holds the defaults. These environment variables (`.env` locally, Space settings
when deployed) override them:

| Variable | Default | |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | `gemini-3.5-flash` is smarter but allows ~20 requests/day on the free tier |
| `GEMINI_RPM` | off | Client-side requests-per-minute cap |
| `ALLOW_SHELL` | `false` (web) | Give the web agent `run_command` (still needs approval). The CLI always has it |
| `DEMO_MODE` | `false` (`true` in Docker) | Private per-visitor workspaces + free allowance |
| `FREE_CHAT_LIMIT` / `FREE_MAX_FILES` / `FREE_PLANS` | `2` / `150` / `false` | Free allowance on your key (demo mode) |
| `APP_ACCESS_TOKEN` | none | Password for the web UI / API |
| `WORKSPACES_DIR` | `./workspaces` | Where web workspaces are stored |
| `REDIS_URL`, `SEMANTIC_CACHE` | config.yaml | Semantic cache location / on-off |

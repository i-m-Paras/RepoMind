# One image: React UI + FastAPI + RepoMind. Runs anywhere that runs Docker — Render's free
# web service, Hugging Face Spaces, Railway, a VM — and listens on $PORT (set by the host).

# ---------- 1) Build the React UI ----------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------- 2) Python app ----------
FROM python:3.12-slim

# git: "Clone a Git repo" workspaces.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Node 22 (from the build stage) for the MCP servers, which run via npx.
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Run as a non-root user (uid 1000 also suits Hugging Face Spaces).
RUN useradd --create-home --uid 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=7860 \
    WORKSPACES_DIR=/home/user/workspaces \
    ALLOW_SHELL=false \
    DEMO_MODE=true

WORKDIR /home/user/app
COPY --chown=user pyproject.toml README.md ./
COPY --chown=user repomind ./repomind
COPY --chown=user examples ./examples
COPY --chown=user --from=web /web/dist ./web/dist
# Editable install: the app finds web/dist and examples/ next to the package.
RUN pip install --user -e .
# MCP servers installed up front: `npx` (run from this folder) then finds them locally
# instead of downloading on every tool call — matters on a small free CPU.
RUN npm install --no-audit --no-fund @modelcontextprotocol/server-filesystem @modelcontextprotocol/server-github

EXPOSE 7860
CMD ["repomind-web"]

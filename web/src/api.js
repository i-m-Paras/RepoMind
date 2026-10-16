// Thin client for the RepoMind API. Kept in this browser's localStorage only:
//   - the access token, if the server requires one
//   - a random visitor id (the server keeps each visitor's workspaces separate)
//   - the visitor's own AI connection {provider, model, apiKey, githubToken} — sent as headers with
//     each request, used by the server for that request only, never saved there

const TOKEN_KEY = "repomind.token";
const VISITOR_KEY = "repomind.visitor";
const CONNECTION_KEY = "repomind.connection";

function load(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function save(key, value) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* storage unavailable — lasts for this page only */
  }
}

let visitorId = load(VISITOR_KEY);
if (!visitorId) {
  visitorId = crypto.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  save(VISITOR_KEY, visitorId);
}

let connection = null;
try {
  connection = JSON.parse(load(CONNECTION_KEY) || "null");
} catch {
  connection = null;
}

export function getConnection() {
  return connection;
}

// null disconnects (back to the free allowance)
export function setConnection(next) {
  connection = next;
  save(CONNECTION_KEY, next ? JSON.stringify(next) : null);
}

function connectionHeaders(conn) {
  const h = { "X-Visitor-Id": visitorId };
  if (conn?.apiKey) {
    h["X-LLM-Provider"] = conn.provider;
    h["X-LLM-Key"] = conn.apiKey;
    if (conn.model) h["X-LLM-Model"] = conn.model;
  }
  if (conn?.githubToken) h["X-GitHub-Token"] = conn.githubToken;
  return h;
}

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable — token lasts for this page only */
  }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function headers(extra = {}, conn = connection) {
  const token = getToken();
  const all = { ...connectionHeaders(conn), ...extra };
  return token ? { ...all, Authorization: `Bearer ${token}` } : all;
}

async function request(method, url, body, isForm = false) {
  const res = await fetch(url, {
    method,
    headers: headers(body && !isForm ? { "Content-Type": "application/json" } : {}),
    body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export const api = {
  get: (url) => request("GET", url),
  post: (url, body) => request("POST", url, body || {}),
  put: (url, body) => request("PUT", url, body),
  del: (url) => request("DELETE", url),
  upload: (url, form) => request("POST", url, form, true),
  // Check a connection before saving it (sends the candidate keys, not the saved ones)
  async testConnection(conn) {
    const res = await fetch("/api/connection/test", { method: "POST", headers: headers({}, conn) });
    if (!res.ok) throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail || res.statusText);
    return res.json();
  },
  async fetchBlob(url) {
    const res = await fetch(url, { headers: headers() });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return res.blob();
  },
};

// POST that answers with Server-Sent Events; calls onEvent for every `data:` line.
export async function streamEvents(url, body, onEvent, signal) {
  const res = await fetch(url, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      for (const line of frame.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }
}

export const ws = (name) => `/api/workspaces/${encodeURIComponent(name)}`;

export function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function timeAgo(seconds) {
  if (!seconds) return "";
  const diff = Date.now() / 1000 - seconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

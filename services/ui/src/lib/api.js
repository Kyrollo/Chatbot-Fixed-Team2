/**
 * api.js — Typed API client for all RAG System backend services
 *
 * All calls proxy through Vite dev server → respective FastAPI service.
 * In production, use Traefik/Kong which routes the same prefixes.
 *
 * Auth: every request attaches `Authorization: Bearer <token>` from the
 *       Keycloak access token (or mock token in dev).
 */

const BASE = "";  // Vite proxy handles routing

async function request(method, path, token, body, signal) {
  const headers = {
    Authorization: `Bearer ${token}`,
  };

  let requestBody;
  if (body instanceof FormData) {
    requestBody = body; // let browser set content-type with boundary
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    requestBody = JSON.stringify(body);
  }

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: requestBody,
    signal,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const err = await res.json();
      detail = err.detail || err.message || detail;
    } catch {/* ignore parse error */}
    throw new ApiError(res.status, detail);
  }

  return res;
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

// ─── Domain Service (port 8001, prefix /domains) ─────────────────────────────

export const domainsApi = {
  list: async (token) => {
    const res = await request("GET", "/domains", token);
    return res.json();
  },

  get: async (token, domainId) => {
    const res = await request("GET", `/domains/${domainId}`, token);
    return res.json();
  },

  create: async (token, payload) => {
    const res = await request("POST", "/domains", token, payload);
    return res.json();
  },

  update: async (token, domainId, payload) => {
    const res = await request("PATCH", `/domains/${domainId}`, token, payload);
    return res.json();
  },

  archive: async (token, domainId) => {
    const res = await request("DELETE", `/domains/${domainId}`, token);
    return res.json();
  },

  // Members
  listMembers: async (token, domainId) => {
    const res = await request("GET", `/domains/${domainId}/members`, token);
    return res.json();
  },

  assignMember: async (token, domainId, payload) => {
    const res = await request("POST", `/domains/${domainId}/members`, token, payload);
    return res.json();
  },

  updateMember: async (token, domainId, userId, payload) => {
    const res = await request("PATCH", `/domains/${domainId}/members/${userId}`, token, payload);
    return res.json();
  },

  removeMember: async (token, domainId, userId) => {
    await request("DELETE", `/domains/${domainId}/members/${userId}`, token);
  },

  // Config
  getConfig: async (token, domainId) => {
    const res = await request("GET", `/domains/${domainId}/config`, token);
    return res.json();
  },

  updateConfig: async (token, domainId, payload) => {
    const res = await request("PATCH", `/domains/${domainId}/config`, token, payload);
    return res.json();
  },
};

// ─── Ingestion Service (port 8002, prefix /ingest) ───────────────────────────

export const ingestionApi = {
  upload: async (token, file, domainId) => {
    const form = new FormData();
    form.append("file", file);
    form.append("domain_id", domainId);
    const res = await request("POST", "/ingest", token, form);
    return res.json();
  },

  getStatus: async (token, documentId) => {
    const res = await request("GET", `/ingest/${documentId}`, token);
    return res.json();
  },
};

// ─── Generation Service (port 8004, prefix /generate) ────────────────────────

export const generationApi = {
  /**
   * Non-streaming query. Returns QueryResponse.
   */
  query: async (token, payload) => {
    const res = await request("POST", "/generate/query", token, {
      ...payload,
      stream: false,
    });
    return res.json();
  },

  /**
   * Streaming query. Returns a ReadableStreamDefaultReader<Uint8Array>.
   * Caller is responsible for reading chunks and decoding.
   */
  queryStream: async (token, payload, signal) => {
    const res = await request("POST", "/generate/query", token, {
      ...payload,
      stream: true,
    }, signal);
    return res.body.getReader();
  },
};

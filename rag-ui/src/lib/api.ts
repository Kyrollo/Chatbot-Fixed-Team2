// src/lib/api.ts
import { useAuthStore } from '../store/authStore'

const BASE_URL = '' // proxied via vite/Traefik on same origin

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = useAuthStore.getState().token
  const headers: Record<string, string> = {
    ...(options.body && !(options.body instanceof FormData)
      ? { 'Content-Type': 'application/json' }
      : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers as Record<string, string> | undefined),
  }

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = data.detail ?? JSON.stringify(data)
    } catch {
      /* ignore */
    }
    throw new Error(`API ${res.status}: ${detail}`)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: 'GET' }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  upload: <T>(path: string, form: FormData) => request<T>(path, { method: 'POST', body: form }),
}

// --- Domain Service ---
export const domainApi = {
  login: (userId: string) => api.post<any>('/domains/auth/login', { user_id: userId }),
  list: () => api.get<any[]>('/domains'),
  get: (id: string) => api.get<any>(`/domains/${id}`),
  create: (data: { name: string; description: string }) => api.post('/domains', data),
  update: (id: string, data: any) => api.patch(`/domains/${id}`, data),
  archive: (id: string) => api.delete(`/domains/${id}`),
  getConfig: (id: string) => api.get<any>(`/domains/${id}/config`),
  updateConfig: (id: string, data: any) => api.patch(`/domains/${id}/config`, data),
  members: (id: string) => api.get<any[]>(`/domains/${id}/members`),
  addMember: (id: string, data: { user_id: string; role: string }) =>
    api.post(`/domains/${id}/members`, data),
  updateMember: (id: string, userId: string, data: { role: string }) =>
    api.patch(`/domains/${id}/members/${userId}`, data),
  removeMember: (id: string, userId: string) => api.delete(`/domains/${id}/members/${userId}`),
  documents: (domainId: string) => api.get<any[]>(`/domains/${domainId}/documents`),
  deleteDocument: (domainId: string, docId: string) => 
    api.delete(`/domains/${domainId}/documents/${docId}`),
  documentChunks: (domainId: string, docId: string) => 
    api.get<any[]>(`/domains/${domainId}/documents/${docId}/chunks`),
}

// --- Ingestion Service ---
export const ingestApi = {
  upload: (file: File, domainId: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('domain_id', domainId)
    return api.upload<{ document_id: string; status: string }>('/ingest', form)
  },
  status: (documentId: string) => api.get<any>(`/ingest/${documentId}`),
  cancel: (documentId: string) => api.post(`/ingest/${documentId}/cancel`),
}

// --- Generation Service ---
export interface QueryPayload {
  query: string
  domain_id: string
  stream: boolean
  top_k_retrieve: number
  top_k_rerank: number
  temperature: number
  max_tokens: number
}

export const generateApi = {
  query: (payload: QueryPayload) => api.post<any>('/generate/query', payload),
  // streaming variant - returns a fetch Response for manual reading
  queryStream: async (payload: QueryPayload, signal?: AbortSignal) => {
    const token = useAuthStore.getState().token
    const res = await fetch('/generate/query', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(payload),
      signal,
    })
    if (!res.ok || !res.body) throw new Error(`Stream error ${res.status}`)
    return res.body
  },
}

// --- Evaluation Service ---
export const evaluateApi = {
  evaluate: (payload: { query: string; answer: string; context_chunks: string[] }) =>
    api.post<any>('/evaluate', payload),
}

// --- Health checks (Traefik / individual services) ---
export const healthApi = {
  check: (path: string) => api.get<any>(`${path}/health`).then(() => true).catch(() => false),
}

// --- Admin Service (Users registry) ---
export const adminApi = {
  listUsers: () => api.get<any[]>('/domains/admin/users'),
  createUser: (data: { id: string; name: string; role: string }) => 
    api.post<any>('/domains/admin/users', data),
  deleteUser: (userId: string) => api.delete<void>(`/domains/admin/users/${userId}`),
}

// --- Monitoring Service ---
export const monitoringApi = {
  metrics: () => api.get<any>('/monitoring/metrics'),
}

# RAG System — Web Chat UI

React 18 + Vite 5 SPA for the RAG System. Connects to all backend services via the Vite dev proxy (dev) or Traefik (production).

## Stack

| | |
|---|---|
| Framework | React 18 + Vite 5 |
| Styling | Tailwind CSS 3 with custom design tokens |
| Auth | Keycloak-js PKCE flow (mock mode for dev) |
| Markdown | react-markdown + remark-gfm |
| Icons | lucide-react |
| Notifications | react-hot-toast |

## Directory layout

```
src/
├── lib/
│   ├── auth.jsx      Keycloak context + VITE_MOCK_AUTH bypass
│   └── api.js        Typed fetch wrappers for all 5 backend services
├── hooks/
│   ├── useChat.js    Streaming conversation state + abort
│   ├── useDomains.js Domain CRUD
│   └── useUpload.js  PDF upload + status polling
├── components/
│   ├── ChatMessage.jsx   Markdown renderer + citation pills
│   ├── ChatInput.jsx     Auto-grow textarea, stream toggle
│   ├── CitationPanel.jsx Slide-in source text panel
│   ├── DomainSidebar.jsx Domain list + create form
│   ├── UploadPanel.jsx   Drag-and-drop PDF upload
│   └── ui.jsx            Spinner, Badge, Avatar, Tooltip
└── pages/
    └── ChatPage.jsx      Main layout
```

## Running locally

### Without Keycloak (mock auth)
```bash
cp .env.example .env
echo "VITE_MOCK_AUTH=true" >> .env
npm install
npm run dev          # http://localhost:5173
```

### With the full backend stack
```bash
# From repo root — starts all services + Vite
python run_services.py --ui
```

### Build for production
```bash
npm run build        # outputs to dist/
```

## Auth modes

**`VITE_MOCK_AUTH=true`** — skips Keycloak entirely, injects a hardcoded system-admin dev user. Never use in production.

**`VITE_MOCK_AUTH=false`** (default) — full Keycloak PKCE flow. You need `rag-ui` registered as a **public client** in the `rag-system` realm with:
- Valid redirect URI: `http://localhost:5173/*`
- Valid post-logout redirect URI: `http://localhost:5173`
- Web origins: `http://localhost:5173`

## API proxying

In dev, `vite.config.js` proxies these paths to the backend:

| Path | Target |
|---|---|
| `/domains` | domain-service :8001 |
| `/ingest` | ingestion-service :8002 |
| `/api/v1/retrieve` | retrieval-service :8003 |
| `/generate` | generation-service :8004 |
| `/evaluate` | evaluation-service :8005 |

In production (behind Traefik), these paths route identically — no config change needed.

## Streaming

The chat toggle controls whether responses stream (`text/plain` chunked body) or return in one JSON response. Streaming uses `ReadableStream` + `TextDecoder` directly — no SSE library needed.

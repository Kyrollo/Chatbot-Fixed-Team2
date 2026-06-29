# RAG System — TLS & Secrets Management

**A guide to the security layer: explanation, installation, and how to run it**

This document covers:
- Caddy Reverse Proxy (TLS / HTTPS)
- `.env` and `.env.example` files
- `secrets_check.py` — secrets validation before startup
- `gen_dev_keys.py` and `dev_auth.py` — local JWT keys
- `INTERNAL_API_KEY` — protecting internal routes

---

## Table of Contents

1. [Overview](#1-overview)
2. [TLS Layer — Caddy Reverse Proxy](#2-tls-layer--caddy-reverse-proxy)
3. [Secrets Management](#3-secrets-management)
4. [Local JWT Keys (Keycloak Replacement)](#4-local-jwt-keys-keycloak-replacement)
5. [Installation](#5-installation)
6. [Running It](#6-running-it)
7. [Troubleshooting](#7-troubleshooting)
8. [Production Security Notes](#8-production-security-notes)

---

## 1. Overview

The system runs several microservices locally without Docker, orchestrated by `run_services.py`. Each service runs on plain HTTP on an internal port (8001, etc.). However, anyone interacting with the frontend or the API from outside must go through **Caddy** — a reverse proxy that performs **TLS termination** (it speaks HTTPS with the client and forwards the request internally as plain HTTP to the real service).

Alongside TLS, there's a complete secrets-management layer: an `.env` file holding every sensitive value (passwords, API keys, internal keys), a local JWT key pair that's auto-generated when Keycloak isn't running, and a basic validation step (`secrets_check.py`) that confirms these values haven't been left at their weak defaults before any service starts.

> **One-line summary:**
> - **Caddy** = a single HTTPS gateway for everything (UI + API + Keycloak) without touching a single SSL certificate by hand.
> - **`.env` + `secrets_check.py` + dev keys** = a protection layer for secrets, ensuring even the dev environment never runs on weak default values.

### 1.1 Quick Component Map

| Component | File / Folder | Role |
|---|---|---|
| Caddy | `Caddyfile` | TLS termination + reverse proxy for every front door |
| Secrets | `.env` / `.env.example` | All sensitive and configurable values |
| Secrets Validation | `scripts/secrets_check.py` | Refuses to start the system if a secret is missing or weak |
| Dev JWT Keys | `scripts/gen_dev_keys.py` | Generates a local RSA-2048 key pair (Keycloak replacement) |
| Dev Auth Provider | `scripts/dev_auth.py` | Issues local JWT tokens using that key pair |
| Orchestrator | `run_services.py` | Ties everything together and starts it in the right order |

---

## 2. TLS Layer — Caddy Reverse Proxy

The file responsible for this entire layer is the `Caddyfile` at the project root. Caddy is the server that receives all client (browser) traffic over HTTPS and distributes it to the real services running on plain HTTP.

### 2.1 Why Caddy specifically?

- It performs **Automatic HTTPS** out of the box — no need to fetch a certificate from an external CA or run `mkcert` yourself. Caddy ships with its own local CA and generates certificates for `localhost` automatically on first run.
- The configuration syntax (the Caddyfile) is far simpler than nginx or Traefik for local development.
- It natively supports reverse proxying, CORS headers, and protecting specific routes (like `/internal/*`) with no extra code.

### 2.2 The Three Front Doors It Covers

| Address (HTTPS) | Routes to | Purpose |
|---|---|---|
| `https://localhost:3000` | `rag-ui/dist` (static files) | User interface (React) |
| `https://localhost:8000` | `http://localhost:8001` (monolith-service) | Main API Gateway |
| `https://localhost:8443` | `http://localhost:8180` (Keycloak) | Identity provider / login |

### 2.3 Full Caddyfile Contents

Here is the complete configuration as it exists in the project:

```caddyfile
{
	admin localhost:2019
}

# ─── React UI ──────────────────────────────────────────────────────────────
https://localhost:3000 {
	root * rag-ui/dist
	file_server
	try_files {path} /index.html

	log {
		output file logs/caddy-ui.log
	}
}

# ─── API Gateway ───────────────────────────────────────────────────────────
https://localhost:8000 {

	header {
		Access-Control-Allow-Origin  "https://localhost:3000"
		Access-Control-Allow-Headers "Authorization, Content-Type, X-Internal-Key"
		Access-Control-Allow-Methods "GET, POST, PUT, PATCH, DELETE, OPTIONS"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
	}

	route {
		@options method OPTIONS
		respond @options 204

		handle /internal/* {
			@not_loopback not remote_ip 127.0.0.1 ::1
			abort @not_loopback
			reverse_proxy localhost:8001
		}

		handle {
			reverse_proxy localhost:8001
		}
	}

	log {
		output file logs/caddy-api.log
	}
}

# ─── Keycloak ──────────────────────────────────────────────────────────────
https://localhost:8443 {
	reverse_proxy localhost:8180 {
		header_up Host              {host}
		header_up X-Real-IP         {remote_host}
		header_up X-Forwarded-Proto https
	}

	log {
		output file logs/caddy-keycloak.log
	}
}
```

### 2.4 Block-by-Block Explanation

- **`admin localhost:2019`** — Caddy's own admin port (an internal API for controlling Caddy at runtime; not exposed externally).
- **React UI block** — Serves the built UI files (`rag-ui/dist`) as static assets. `try_files` makes any unknown route (e.g. `/domains/123`) fall back to `index.html` so React Router can handle client-side routing (SPA fallback).
- **API Gateway block** — This is the core piece: it adds explicit CORS headers (only `https://localhost:3000` is allowed to talk to the API), and short-circuits any `OPTIONS` request with a `204` instead of forwarding it to the real backend.
- **`/internal/*` path** — Protected by a `remote_ip` condition: if the request doesn't come from `127.0.0.1` or `::1` (i.e., from outside the machine itself), it gets `abort`ed before reaching the service. This means any internal, service-to-service endpoint is unreachable from the outside even if someone knows the URL.
- **Keycloak block** — A simple reverse proxy from `https://localhost:8443` to Keycloak on `8180`, adding `X-Forwarded-Proto: https` so Keycloak knows the original request was HTTPS.

> ⚠️ **Important note about certificates**
> There is no `.crt` or `.pem` file anywhere related to Caddy — and that's intentional. Caddy uses **Automatic HTTPS**: on first run it creates its own local Certificate Authority (CA) and signs `localhost` certificates with it, storing everything in its own data directory (typically `%AppData%\Caddy` on Windows or `~/.local/share/caddy` on Linux).
> The first time you run Caddy, your browser will warn about an untrusted (self-signed) certificate until you trust its local CA — see Section 6.4 below for the fix.

---

## 3. Secrets Management

The system clearly separates two files: `.env.example` (a generic template, safe to commit to git) and `.env` (the real file with actual values, which must never be committed).

### 3.1 The `.env.example` File

This is the template included in the project, organized into clearly marked sections: `[REQUIRED]` must be changed, `[OPTIONAL]` has sensible defaults, and `[AUTO-CONFIGURED]` is set automatically by `run_services.py`.

| Section | Key Variables | Status |
|---|---|---|
| PostgreSQL | `POSTGRES_PASSWORD`, `DATABASE_URL` | REQUIRED |
| Groq API (LLM) | `GROQ_API_KEY` | REQUIRED |
| Internal API Key | `INTERNAL_API_KEY` | OPTIONAL (but must actually be changed) |
| Keycloak | `KEYCLOAK_ISSUER`, `KEYCLOAK_PUBLIC_KEY` | AUTO-CONFIGURED |
| Redis / Qdrant | `REDIS_URL`, `QDRANT_PATH` | AUTO-CONFIGURED |
| Internal service URLs | `DOMAIN_SERVICE_URL`, etc. | AUTO-CONFIGURED (`https://localhost:8000`) |

### 3.2 The Internal Key — `INTERNAL_API_KEY`

This is a shared secret **between services**, not between the client and the server. It's sent in the `X-Internal-Key` header (note it's referenced in Caddy's CORS configuration in the previous section) so one service can confirm a request came from another trusted internal service, not just anyone.

- The default value in the code is `rag-internal-dev-key-change-in-prod`, which is classified as a "weak value" and rejected by the validation check (explained next).
- The correct way to generate a strong value:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Then put the output into `.env` next to `INTERNAL_API_KEY=`.

### 3.3 `secrets_check.py` — the Gatekeeper

A small but essential script at `scripts/secrets_check.py`, automatically called by `run_services.py` before any service starts. Its job is simple: confirm that every required secret (`POSTGRES_PASSWORD`, `GROQ_API_KEY`, `INTERNAL_API_KEY`):

1. Is present in `.env` (not empty).
2. Doesn't match any value from a list of known weak values (e.g. `changeme`, `password`, `secret`, or the default `INTERNAL_API_KEY`).
3. Meets the minimum required length (e.g. `INTERNAL_API_KEY` must be at least 32 characters).

If any of these checks fail, the script prints a clear error message and **stops the entire system from starting** (`sys.exit(1)`) — meaning it's impossible to run the system on weak default values. This is a deliberate security measure.

If a value is present but triggers a soft warning (not a hard error) — e.g. `GROQ_API_KEY` not starting with `gsk_` — it prints a warning and continues normally.

### 3.4 `.gitignore` — Preventing Secret Leaks

The `.gitignore` file at the project root explicitly excludes every sensitive file from git:

```gitignore
# Environment & secrets
.env
*.ini

# Dev JWT key pair
*.pem
data/dev/
```

---

## 4. Local JWT Keys (Keycloak Replacement)

In dev mode, if Keycloak isn't running (or Java isn't installed), the system has a complete fallback mechanism based on a locally generated RSA-2048 key pair, so you can log in and try the system without ever installing Keycloak.

### 4.1 `gen_dev_keys.py`

Generates a key pair (private/public) and saves them to `data/dev/jwt_private.pem` and `data/dev/jwt_public.pem`. It tries `openssl` first, and falls back to Python's `cryptography` library if `openssl` isn't available.

```bash
# Manual run (optional — it's usually auto-generated):
python scripts/gen_dev_keys.py

# To generate a strong INTERNAL_API_KEY to copy into .env:
python scripts/gen_dev_keys.py --secrets

# If the old key pair was leaked or you need a fresh one:
python scripts/gen_dev_keys.py --force
```

- On Linux/macOS, the script restricts the private key file permissions to `0600` — only the current user can read/write it.
- These keys are covered by `.gitignore` (`*.pem` and `data/dev/`), so they can never be accidentally committed.

### 4.2 `dev_auth.py`

This is what uses the private key to **sign** local JWT tokens, the same way Keycloak would, so every service in the system can handle this transparently without knowing the difference.

- Default `DEV_ISSUER`: `http://localhost/dev-realm` — this value is set into `KEYCLOAK_ISSUER` when `run_services.py` runs without Keycloak.
- Services validate tokens using `KEYCLOAK_PUBLIC_KEY`, which comes from the same public key (`get_public_key_body` in `run_services.py`).
- Default token validity: 24 hours (`expires_minutes = 1440`).

### 4.3 Ready-Made Dev Users (Dev Auth Mode)

| User ID | Role |
|---|---|
| `admin` | `system_admin` |
| `manager` | `domain_admin` |
| `contributor` | `contributor` |
| `viewer` | `reader` |

---

## 5. Installation

### 5.1 Installing Caddy

Caddy is a single executable (a Go binary), not a Python package or an npm dependency, so it needs to be installed separately from `requirements.txt` or `package.json`.

**On Windows**
- Download the latest release from the official site: https://caddyserver.com/download
- Or, if you have Chocolatey: `choco install caddy`
- Confirm `caddy.exe` is on your `PATH` (run `caddy version` in any terminal).

**On Linux / WSL2**

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
```

**On macOS**

```bash
brew install caddy
```

### 5.2 Installing the Rest of the Requirements

The TLS and secrets layer doesn't need any extra Python packages beyond the regular `requirements.txt`, but if you ever generate JWT keys manually without `openssl`, make sure the `cryptography` library is installed (it's already part of `requirements.txt`):

```bash
pip install -r requirements.txt
```

---

## 6. Running It

### 6.1 Setting Up Secrets for the First Time

1. Copy the template:

```bash
copy .env.example .env        :: Windows
cp .env.example .env           # Linux / macOS
```

2. Open `.env` and update the required values:
   - `POSTGRES_PASSWORD` — your database password.
   - `GROQ_API_KEY` — a key from https://console.groq.com (starts with `gsk_`).
   - `INTERNAL_API_KEY` — use the generated value from the command in Section 3.2.

3. Run the secrets check manually to confirm everything is in order (optional, since `run_services.py` does this automatically):

```bash
python scripts/secrets_check.py
python scripts/secrets_check.py --strict   # turns warnings into errors
```

### 6.2 Running the Services Together with Caddy

The correct order is: start the internal services first (via `run_services.py`), then start Caddy in a second terminal so it has something to route to.

1. Start the backend (Python) from the project root:

```bash
python run_services.py
```

2. In a second terminal, from the same project root (where the `Caddyfile` lives), start Caddy:

```bash
caddy run
```

The first time you run `caddy run`, it will print a message that it generated and installed its local CA. On Windows/macOS, browsers usually trust it automatically after a system permission prompt; on Linux you may need `sudo` or to run `caddy trust` manually (see Section 6.4).

3. Start the frontend (React) in a third terminal:

```bash
cd rag-ui
npm install
npm run build    :: important: Caddy serves from rag-ui/dist, not the dev server
```

> **Note on the frontend**
> The `https://localhost:3000` block in the Caddyfile reads static files directly from `rag-ui/dist` — so you need to run `npm run build` first (not `npm run dev`).
> If you're actively developing the UI and want hot reload, use the regular `npm run dev` on port 5173 (see `vite.config.ts`) and leave Caddy for just the API/Keycloak.

### 6.3 Verifying Everything Is Running

```bash
# Confirm Caddy is up and receiving requests
curl -k https://localhost:8000/api/v1/domains/monitoring/health

# Confirm Keycloak (or dev auth) is reachable through Caddy
curl -k https://localhost:8443/realms/rag-system

# Confirm the UI is running
curl -k https://localhost:3000
```

The `-k` flag here means "ignore certificate verification" — useful only for testing from a terminal; the browser will actually need to trust Caddy's certificate (next section).

### 6.4 Handling the Browser Certificate Warning

The first time you open `https://localhost:8000` or `https://localhost:3000`, the browser may show an "Unsafe connection" warning because Caddy's certificate is signed by a local CA the browser doesn't yet recognize.

1. The safest fix: run the following command once (it installs Caddy's local CA into the OS trust store):

```bash
caddy trust
```

2. If `caddy trust` isn't available or you have limited permissions, you can bypass the warning from the browser itself (Advanced → Proceed anyway) — acceptable for local development only, not appropriate for production.

---

## 7. Troubleshooting

### 7.1 Caddy

| Problem | Fix |
|---|---|
| `caddy: command not found` | Make sure Caddy is installed and on your `PATH` (see Section 5.1). |
| `bind: address already in use` on 8000/3000/8443 | Another process is already using that port — find it with `netstat -ano \| findstr ":8000"` and stop it. |
| Browser certificate warning | Run `caddy trust` once, or click "Proceed anyway" in a dev environment. |
| The UI loads but the page is blank | Make sure you ran `npm run build` before `caddy run`, not `npm run dev`. |
| `/internal/*` returns 403 / abort | Expected if the request comes from outside the local machine — this is intentional; connect from the same host. |

### 7.2 Secrets

| Problem | Fix |
|---|---|
| "Secrets validation FAILED" at startup | Open `.env` and confirm `POSTGRES_PASSWORD`, `GROQ_API_KEY`, and `INTERNAL_API_KEY` aren't still set to their default values. |
| `INTERNAL_API_KEY is too short` | Generate a new value: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GROQ_API_KEY doesn't look like a Groq key` | Just a warning — confirm the key starts with `gsk_` from console.groq.com. |
| Accidentally lost the local JWT key | Run `python scripts/gen_dev_keys.py --force` to generate a fresh pair. |

---

## 8. Production Security Notes

The current setup is designed for local development. If you move the system to a real production environment, keep the following in mind:

- Use real TLS certificates signed by a trusted CA (e.g. Let's Encrypt via Caddy itself — it supports this automatically once you have a real domain instead of `localhost`).
- Change `INTERNAL_API_KEY` and `POSTGRES_PASSWORD` to strong, unique random values per environment (never reuse the same value between dev and prod).
- Enable real Keycloak instead of `dev_auth.py` (this fallback path is explicitly designed for development only — the code comments themselves say so).
- Run `secrets_check.py` with `--strict` in any CI/CD pipeline so warnings become errors that block deployment.
- Never copy the `data/dev` folder (it contains private keys) into any shared environment or bake it into a Docker image.

---

*This document was prepared by directly inspecting the project files: `Caddyfile`, `.env.example`, `scripts/secrets_check.py`, `scripts/gen_dev_keys.py`, `scripts/dev_auth.py`, `run_services.py`.*

# Chatbot-Fixed-Team2

Multi-user, multi-domain RAG system for the Fixed Solutions AI Internship 2026.

This repository follows the target architecture in [Architecture_Decisions_e5.md](Architecture_Decisions_e5.md). The full platform is still larger than the code currently in the repo, but the existing services are now wired into one working development stack with consistent ports and shared networking.

## What Works Now

The current connected development stack includes:

- `gateway` via Traefik
- `auth` via Keycloak
- `domain-service` via FastAPI
- `postgres` for domain-service persistence

These services are connected through Docker Compose and a shared Docker network.

## Current Dev Ports

| Port | Service |
|---|---|
| `80` | Traefik gateway |
| `8080` | Traefik dashboard |
| `8180` | Keycloak |
| `8001` | domain-service |
| `5432` | PostgreSQL |

## Current Request Flow

Today, the live request path is:

1. Client calls Traefik on port `80`
2. Traefik checks auth against Keycloak
3. Authenticated requests are forwarded to `domain-service`
4. `domain-service` validates the JWT again and enforces RBAC
5. Domain data is stored in PostgreSQL

That means the currently implemented services do work together rather than existing only as separate folders.

## Run The Stack

From the repository root:

```bash
docker compose up --build
```

Stop it with:

```bash
docker compose down
```

## Service URLs

- Traefik dashboard: `http://localhost:8080/dashboard/`
- Keycloak realm base: `http://localhost:8180/realms/rag-system`
- domain-service direct docs: `http://localhost:8001/docs`
- domain-service through gateway: `http://localhost/domains`

## Current Repository Structure

```text
services/
  auth/
  domain-service/
  gateway/
docker-compose.yml
Architecture_Decisions_e5.md
README.md
```

## Implemented Services

### `services/domain-service`

This is the most complete application service in the repository.

Implemented features:

- FastAPI app
- async SQLAlchemy setup
- PostgreSQL models for domains, memberships, and config
- Keycloak JWT validation
- system admin checks
- per-domain role enforcement
- domain CRUD
- member CRUD
- per-domain RAG config
- internal `/internal/check-access` endpoint for future service-to-service authorization

### `services/auth`

This provides Keycloak bootstrap configuration:

- realm export
- local compose setup
- seeded realm and users

### `services/gateway`

This provides the gateway layer:

- Traefik config for development
- Kong config for production
- smoke test script
- integrated compose setup for the currently implemented services

## Target Architecture

Per [Architecture_Decisions_e5.md](Architecture_Decisions_e5.md), the intended system is larger and includes:

- `ingestion-service`
- `worker-service`
- `retrieval-service`
- `generation-service`
- `evaluation-service`
- `ui`
- Redis
- Qdrant
- Ollama
- Groq API
- Kubernetes and Helm for production

Those parts are still planned, not yet implemented in this repository.

## What Changed To Make The Current Services Work Together

The main integration fixes were:

- added a root `docker-compose.yml` for the real connected stack
- connected Traefik to the real `domain-service`
- removed mock backends from the active gateway configuration
- fixed the `domain-service` route to use port `8001`
- aligned the development port documentation
- connected `domain-service` to PostgreSQL
- adjusted Keycloak settings so token validation works in both host and Docker contexts

## Active Gateway Route

The active gateway route right now is:

| Path | Backend |
|---|---|
| `/domains` | `domain-service` |

This is the only route backed by a real service at the moment.

## Keycloak Notes

The imported realm is `rag-system`.

Included users in the realm export include:

- `admin` with `system_admin`
- `reader1` with `reader`

Keycloak admin console runs at `http://localhost:8180`.

## Smoke Test

To verify the connected stack:

```bash
cd services/gateway
pip install requests pyyaml
python smoke_test.py
```

The smoke test checks:

- Traefik startup
- Keycloak reachability
- `/domains` auth protection
- 404 behavior for unknown routes
- Traefik dashboard availability

## Architecture Status

### Connected and working now

- gateway <-> Keycloak
- gateway <-> domain-service
- domain-service <-> PostgreSQL

### Not connected yet because those services do not exist yet

- gateway <-> ingestion-service
- gateway <-> retrieval-service
- gateway <-> generation-service
- gateway <-> evaluation-service
- domain-service <-> worker-service
- retrieval-service <-> Qdrant
- generation-service <-> Ollama or Groq

## Next Build Steps

The next logical implementation steps are:

1. add `ingestion-service`
2. add Redis and `worker-service`
3. add `retrieval-service`
4. add `generation-service`
5. wire those services into the gateway one by one

## Summary

The repo is no longer just a disconnected architecture sketch. The currently implemented services are now aligned on ports, network wiring, auth flow, and local startup, while the README still keeps a clear distinction between what exists now and what remains part of the larger architecture plan.

# Domain Service

Domain Service for the **Multi-User Multi-Domain RAG System** (Sprint 1).

It manages knowledge domains, domain memberships (RBAC), and per-domain RAG
configuration. It also exposes an internal endpoint that other services
(ingestion, retrieval, generation) call to verify access before performing
operations.

## Features

- Domain CRUD with soft-delete (archive)
- Per-domain role-based membership: `domain_admin`, `contributor`, `reader`
- Per-domain RAG configuration (chunking, LLM route, confidence threshold)
- Keycloak JWT authentication
- Internal RBAC check endpoint for service-to-service authorization

## Requirements

- Python 3.12+
- PostgreSQL 13+
- A running Keycloak realm

## Environment Variables

See `.env.example`. Copy it and fill in values:

```bash
cp .env.example .env
```

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Async PostgreSQL DSN (`postgresql+asyncpg://...`) |
| `KEYCLOAK_ISSUER` | Realm issuer URL |
| `KEYCLOAK_PUBLIC_KEY` | Realm RS256 public key (PEM body or full PEM) |
| `KEYCLOAK_CLIENT_ID` | Client ID for this service |
| `SYSTEM_ADMIN_ROLE` | Realm role mapped to system admin |
| `INTERNAL_API_KEY` | Shared secret for `/internal/*` endpoints |
| `SERVICE_PORT` | Port to listen on (default 8001) |

## Running Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Ensure Postgres is running and DATABASE_URL is set
uvicorn main:app --reload --port 8001
```

Tables are auto-created on startup for Sprint 1. For production use Alembic
migrations instead.

OpenAPI docs: <http://localhost:8001/docs>

## Docker

```bash
docker build -t domain-service .

docker run --rm -p 8001:8001 --env-file .env domain-service
```

## API Overview

### Domains
| Method | Path | Permission |
|--------|------|------------|
| POST | `/domains` | system_admin |
| GET | `/domains` | authenticated |
| GET | `/domains/{domain_id}` | assigned members |
| PATCH | `/domains/{domain_id}` | system_admin, domain_admin |
| DELETE | `/domains/{domain_id}` | system_admin, domain_admin (archive) |

### Members
| Method | Path | Permission |
|--------|------|------------|
| POST | `/domains/{domain_id}/members` | system_admin, domain_admin |
| GET | `/domains/{domain_id}/members` | + contributor |
| PATCH | `/domains/{domain_id}/members/{user_id}` | system_admin, domain_admin |
| DELETE | `/domains/{domain_id}/members/{user_id}` | system_admin, domain_admin |

### Config
| Method | Path | Permission |
|--------|------|------------|
| GET | `/domains/{domain_id}/config` | + contributor |
| PATCH | `/domains/{domain_id}/config` | system_admin, domain_admin |

### Internal
| Method | Path | Permission |
|--------|------|------------|
| POST | `/internal/check-access` | internal services (X-Internal-Key) |

## Testing Examples

Get a token from Keycloak, then:

```bash
TOKEN="<jwt>"

# Create a domain (system admin only)
curl -X POST http://localhost:8001/domains \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "legal", "description": "Legal documents"}'

# List my domains
curl http://localhost:8001/domains \
  -H "Authorization: Bearer $TOKEN"

# Assign a member
curl -X POST http://localhost:8001/domains/<domain_id>/members \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user-uuid", "role": "contributor"}'

# Update config
curl -X PATCH http://localhost:8001/domains/<domain_id>/config \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"chunk_size": 1024, "confidence_threshold": 0.7}'
```

Internal access check (service-to-service):

```bash
curl -X POST http://localhost:8001/internal/check-access \
  -H "X-Internal-Key: change-me-internal-key" \
  -H "Content-Type: application/json" \
  -d '{
        "user_id": "user-uuid",
        "domain_id": "domain-uuid",
        "required_role": "contributor"
      }'
# -> {"allowed": true, "role": "contributor", "reason": null}
```

## Integration with Keycloak

1. Create a realm (e.g. `rag`).
2. Create a client `domain-service` (confidential or public depending on setup).
3. Define a realm role `system_admin` and assign it to platform administrators.
4. Copy the realm's RS256 public key (Realm Settings → Keys → RS256 → Public key)
   into `KEYCLOAK_PUBLIC_KEY`.
5. Clients obtain tokens from Keycloak and send them as
   `Authorization: Bearer <token>`.

The service reads `sub`, `preferred_username`, `email`, and roles from
`realm_access.roles` / `resource_access.<client>.roles`.

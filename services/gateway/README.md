# Gateway Service

The gateway is now wired to the real `domain-service` for development.

- **Dev gateway:** Traefik
- **Prod gateway config:** Kong
- **Auth provider:** Keycloak
- **Current live backend behind the gateway:** `domain-service`

## Development Ports

- `80` Traefik HTTP entrypoint
- `8080` Traefik dashboard
- `8180` Keycloak
- `8001` domain-service
- `5432` PostgreSQL

## Run The Integrated Dev Stack

From the repository root:

```bash
docker compose up --build
```

Or from this folder:

```bash
docker compose up --build
```

Both compose files start the same connected stack:

- PostgreSQL
- Keycloak
- domain-service
- Traefik

## Active Route

| Path | Service |
|---|---|
| `/domains` | `domain-service` |

## Auth Behavior

Requests to `/domains` flow through:

```text
Client -> Traefik -> Keycloak auth check -> domain-service
```

The `domain-service` also validates the bearer token itself, so both the
gateway and the backend participate in auth enforcement.

## Smoke Test

After the stack is up:

```bash
pip install requests pyyaml
python smoke_test.py
```

The smoke test verifies:

- Traefik is reachable
- Keycloak is reachable
- `/domains` is protected
- unknown routes return `404`
- the dashboard is up

## Notes

- Older mock `whoami` backends were removed from the active dev stack.
- Future services such as ingestion, retrieval, and generation remain part of
  the target architecture, but they are not wired into the running gateway yet.

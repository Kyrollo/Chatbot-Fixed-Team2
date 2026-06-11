# Authentication Service

Authentication and authorization are implemented using Keycloak.

## Realm

`rag-system`

## Roles

- system_admin
- domain_admin
- contributor
- reader

## Clients

- rag-ui
- rag-api
- domain-service

## Local startup (no Docker)

Keycloak is started automatically by `python run_services.py` on port **8080**.

Admin console: http://localhost:8080

Bootstrap admin: `admin` / `admin`

Seeded realm users (from `realm-export.json`):

| User | Password |
|---|---|
| admin | admin |
| reader1 | reader1 |

Realm config: `services/auth/realm-export.json`

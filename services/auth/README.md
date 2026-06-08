# Authentication Service

Authentication and authorization are implemented using Keycloak.

## Realm

rag-system

## Roles

- system_admin
- domain_admin
- contributor
- reader

## Clients

- rag-ui
- rag-api

## Startup

docker compose up -d

Access:

http://localhost:8180

## Import Realm

Realm configuration is available in:

realm-export.json

Import using Keycloak import functionality.

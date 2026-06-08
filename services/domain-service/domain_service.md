# Domain Service Structure (Sprint 1)

This document defines the **minimal Domain Service implementation** for the Multi-User Multi-Domain RAG System. The goal is to implement only the functionality required for Sprint 1 without over-engineering the service.

---

# Directory Structure

```text
services/
└── domain-service/
    ├── main.py
    ├── config.py
    ├── database.py
    ├── dependencies.py
    ├── models.py
    ├── schemas.py
    ├── service.py
    ├── router.py
    ├── requirements.txt
    ├── Dockerfile
    ├── .env.example
    └── README.md
```

---

# File Responsibilities

## 1. main.py

### Purpose

Application entry point.

### Responsibilities

* Create FastAPI application.
* Register routers.
* Configure startup/shutdown events if needed.
* Expose OpenAPI documentation.

### Example Responsibilities

```python
app = FastAPI()

app.include_router(router)
```

---

## 2. config.py

### Purpose

Manage environment variables and application settings.

### Responsibilities

* Database URL
* Keycloak issuer URL
* Keycloak public key
* Keycloak client ID
* Service settings

### Contains

```python
DATABASE_URL
KEYCLOAK_ISSUER
KEYCLOAK_PUBLIC_KEY
KEYCLOAK_CLIENT_ID
```

---

## 3. database.py

### Purpose

Manage database connection.

### Responsibilities

* Create SQLAlchemy Async Engine.
* Create Session Factory.
* Define Base model.

### Contains

```python
engine
AsyncSessionLocal
Base
```

Used by all database operations.

---

## 4. dependencies.py

### Purpose

Reusable FastAPI dependencies.

### Responsibilities

### Database

```python
get_db()
```

Provides database session.

### Authentication

```python
get_current_user()
```

* Extract JWT.
* Validate JWT using Keycloak public key.
* Extract user information.

Returns:

```python
{
    "user_id": "...",
    "username": "...",
    "email": "...",
    "is_system_admin": True/False
}
```

### Authorization Helpers

```python
require_system_admin()
require_domain_admin()
```

Used by endpoints.

---

## 5. models.py

### Purpose

SQLAlchemy ORM models.

### Responsibilities

Define database tables.

### Models

---

### Domain

Represents knowledge domains.

Fields:

```python
id
name
description
status
created_by
created_at
updated_at
```

---

### DomainRole

Stores domain memberships.

Fields:

```python
id
domain_id
user_id
role
assigned_by
assigned_at
```

Roles:

```text
domain_admin
contributor
reader
```

---

### DomainConfig

Stores domain-specific RAG settings.

Fields:

```python
id
domain_id
llm_route
chunk_size
chunk_overlap
confidence_threshold
extra_settings
updated_at
```

---

## 6. schemas.py

### Purpose

Pydantic request and response models.

### Responsibilities

Validate API requests and responses.

### Domain Schemas

```python
DomainCreate
DomainUpdate
DomainResponse
```

---

### Member Schemas

```python
MemberAssign
MemberUpdate
MemberResponse
```

---

### Config Schemas

```python
ConfigUpdate
ConfigResponse
```

---

### Internal RBAC Schemas

```python
AccessCheckRequest
AccessCheckResponse
```

Example:

Request:

```json
{
    "user_id": "...",
    "domain_id": "...",
    "required_role": "contributor"
}
```

Response:

```json
{
    "allowed": true
}
```

---

## 7. service.py

### Purpose

Contains business logic.

### Responsibilities

No database logic inside routers.

---

# Domain Service Functions

## Domain CRUD

### create_domain()

Responsibilities:

* Verify domain name uniqueness.
* Create domain.
* Create default DomainConfig.
* Save creator information.

---

### list_domains()

Responsibilities:

System Admin:

```text
Return all domains.
```

Other users:

```text
Return only assigned domains.
```

---

### get_domain()

Responsibilities:

* Retrieve domain details.
* Verify user access.

---

### update_domain()

Responsibilities:

* Update name.
* Update description.
* Verify permissions.

---

### archive_domain()

Responsibilities:

Soft delete:

```text
status = archived
```

No hard delete.

---

# Member Management Functions

## assign_member()

Responsibilities:

* Add user to domain.
* Assign role.
* Prevent duplicate assignments.

---

## list_members()

Responsibilities:

* Return all domain members.

---

## update_member_role()

Responsibilities:

* Change contributor/reader/domain_admin roles.

---

## remove_member()

Responsibilities:

* Remove user access from domain.

---

# Domain Configuration Functions

## get_config()

Responsibilities:

* Retrieve domain configuration.

---

## update_config()

Responsibilities:

Update:

```text
llm_route
chunk_size
chunk_overlap
confidence_threshold
extra_settings
```

---

# Internal RBAC Functions

## check_access()

Responsibilities:

Determine if user has sufficient permissions.

Rules:

```text
system_admin -> Always allowed

domain_admin -> Full domain access

contributor -> Contributor + Reader permissions

reader -> Read-only permissions
```

Used by:

* ingestion-service
* retrieval-service
* generation-service

---

## 8. router.py

### Purpose

Expose REST API endpoints.

### Responsibilities

Connect HTTP requests with service layer.

---

# Domain Endpoints

## Create Domain

```http
POST /domains
```

Permission:

```text
system_admin
```

---

## List Domains

```http
GET /domains
```

Permission:

```text
Authenticated users
```

---

## Get Domain

```http
GET /domains/{domain_id}
```

Permission:

```text
Assigned members
```

---

## Update Domain

```http
PATCH /domains/{domain_id}
```

Permission:

```text
system_admin
domain_admin
```

---

## Archive Domain

```http
DELETE /domains/{domain_id}
```

Permission:

```text
system_admin
domain_admin
```

Performs soft delete.

---

# Member Endpoints

## Assign Member

```http
POST /domains/{domain_id}/members
```

Permission:

```text
system_admin
domain_admin
```

---

## List Members

```http
GET /domains/{domain_id}/members
```

Permission:

```text
system_admin
domain_admin
contributor
```

---

## Update Member Role

```http
PATCH /domains/{domain_id}/members/{user_id}
```

Permission:

```text
system_admin
domain_admin
```

---

## Remove Member

```http
DELETE /domains/{domain_id}/members/{user_id}
```

Permission:

```text
system_admin
domain_admin
```

---

# Domain Configuration Endpoints

## Get Config

```http
GET /domains/{domain_id}/config
```

Permission:

```text
system_admin
domain_admin
contributor
```

---

## Update Config

```http
PATCH /domains/{domain_id}/config
```

Permission:

```text
system_admin
domain_admin
```

---

# Internal RBAC Endpoint

## Check Access

```http
POST /internal/check-access
```

Permission:

```text
Internal services only
```

Purpose:

Verify whether a user can perform an operation on a domain.

Used before:

* Document upload
* Retrieval operations
* Generation requests

---

## 9. requirements.txt

### Purpose

Python dependencies.

Expected packages:

```text
fastapi
uvicorn
sqlalchemy
asyncpg
alembic
pydantic
pydantic-settings
python-jose
python-multipart
psycopg2-binary
```

---

## 10. Dockerfile

### Purpose

Containerize the service.

Responsibilities:

* Build image.
* Install dependencies.
* Start FastAPI application.

---

## 11. .env.example

### Purpose

Document required environment variables.

Contains:

```env
DATABASE_URL=

KEYCLOAK_ISSUER=

KEYCLOAK_PUBLIC_KEY=

KEYCLOAK_CLIENT_ID=

SERVICE_PORT=8001
```

---

## 12. README.md

### Purpose

Developer guide.

Contains:

* Service purpose.
* How to run locally.
* Required environment variables.
* API overview.
* Docker instructions.
* Testing examples.
* Integration with Keycloak.

```

---

This structure is intentionally minimal for Sprint 1. It implements all required Domain Service functionality while avoiding unnecessary complexity and excessive file fragmentation.
```

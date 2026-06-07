# Gateway Service

API Gateway for the RAG system.
- **Dev:** Traefik
- **Prod:** Kong
- **Auth:** Keycloak

---

## Run & Test Locally

```bash
# 1. Start Keycloak
cd ../auth
docker compose up -d

# 2. Wait 60 seconds, then start gateway
cd ../gateway
docker compose up -d

# 3. Install test dependency
pip install requests pyyaml

# 4. Run smoke test
python smoke_test.py

# 5. Tear down
docker compose down
cd ../auth
docker compose down
```

Traefik dashboard → http://localhost:8080/dashboard/
Keycloak → http://localhost:8180

---

## Routes

| Path        | Service            |
|-------------|--------------------|
| /domains    | domain-service     |
| /ingest     | ingestion-service  |
| /retrieve   | retrieval-service  |
| /generate   | generation-service |
| /evaluate   | evaluation-service |

---

## Auth Flow

```text
User → Traefik → Keycloak validation → Service

Headers injected:
- X-User-Id
- X-User-Roles
- X-Domain-Id
```

---

## Notes

- All requests go through Traefik gateway
- Keycloak handles authentication and issues JWT tokens
- Gateway enforces auth before reaching services
- Kong config mirrors Traefik for production
- Run smoke test after any change
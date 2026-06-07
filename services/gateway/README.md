# Gateway Service

API Gateway for the RAG system.
- **Dev:** Traefik
- **Prod:** Kong

---

## Run & Test Locally

```bash
# 1. Start Traefik + mock services
docker compose up -d

# 2. Install test dependency
pip install requests pyyaml

# 3. Run smoke test
python smoke_test.py

# 4. Tear down
docker compose down
```

Traefik dashboard → http://localhost:8080/dashboard/

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

## Notes

- `middlewares.yml` has an auth placeholder — will be replaced with Keycloak JWT validation when auth service is ready.
- Kong config mirrors the same routes with JWT + rate-limiting plugins for production.
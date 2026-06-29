# Go-Live Checklist

**Project:** Multi-Domain RAG System  
**Target Date:** ____________________  
**Prepared by:** Kerollos Mansour 
**Release Candidate:** ____________________  
**Environment:** ____________________

## Instructions

Complete this checklist before production release. Every item needs an owner and evidence. Mark an item as N/A only when it truly does not apply, and record the reason in the notes column.

Status values:

- **Done:** Verified and evidence captured.
- **Blocked:** Cannot complete because of a known issue.
- **N/A:** Not applicable with justification.
- **Open:** Not yet verified.

## 1. Infrastructure

| Status | Item | Verification / Evidence | Owner | Notes |
|---|---|---|---|---|
| Open | PostgreSQL is running on the expected host and port | Confirm connection and version. | Dev1 | |
| Open | Database `domain_db` exists | Confirm required schema exists. | Dev1 | |
| Open | Required tables exist | Verify `users`, `domains`, `domain_roles`, `domain_configs`, `documents`, `document_chunks`, `rag_query_logs`, `evaluation_logs`. | Dev1 | |
| Open | Redis is running | Confirm queue/cache connection. | Dev1 | |
| Open | Qdrant storage is available | Confirm configured storage path exists and is writable. | Dev1 | |
| Open | Upload directory exists | Confirm `data/uploads` or configured upload path is writable. | Dev1 | |
| Open | Model files are present | Confirm embedding, reranker, OCR, and NER model paths from `.env`. | Dev1 | |
| Open | Gateway starts cleanly | Start service in target mode and capture startup logs. | Dev1 | |
| Open | Worker starts cleanly | Confirm worker binds to queue and can receive jobs. | Dev1 | |
| Open | Evaluation worker and scheduler run | Confirm evaluation worker and beat process are alive. | Dev3 | |
| Open | Frontend serves successfully | Confirm UI loads at production or staging URL. | Dev1 | |
| Open | Disk space is sufficient | Confirm at least the agreed free-space threshold for uploads, DB growth, and models. | Dev1 | |
| Open | Backups are configured | Confirm database and uploaded-file backup plan. | Dev1 | |
| Open | Restore process is documented | Confirm restore steps and owner are recorded. | Dev1 | |

## 2. Configuration

| Status | Item | Verification / Evidence | Owner | Notes |
|---|---|---|---|---|
| Open | `.env` exists on server | Confirm production/staging `.env` is present outside git. | Dev1 | |
| Open | `.env.example` is current | Confirm all required variables are documented. | Dev1 | |
| Open | Secrets are not committed | Confirm `.env` is ignored and absent from repository history. | Dev2 | |
| Open | `INTERNAL_API_KEY` changed from default | Confirm value is unique for environment. | Dev2 | |
| Open | LLM provider key configured | Confirm Groq or local provider route is valid. | Dev3 | |
| Open | Local LLM route configured when required | Confirm sensitive domains use Ollama/local route if policy requires it. | Dev3 | |
| Open | JWT or Keycloak config is production-ready | Confirm issuer, audience, keys, realm, and client settings. | Dev2 | |
| Open | CORS restricted | Confirm only trusted frontend origins are allowed. | Dev2 | |
| Open | File size limit configured | Confirm upload limit matches expected 50 MB or approved value. | Dev1 | |
| Open | Logging level appropriate | Confirm production logging avoids excessive sensitive output. | Dev1 | |

## 3. Security and Access Control

| Status | Item | Verification / Evidence | Owner | Notes |
|---|---|---|---|---|
| Open | Authentication required on protected endpoints | Confirm unauthenticated requests are rejected. | Dev2 | |
| Open | RBAC tests pass | Run `tests/test_rbac.py` manually and record result. | Dev2 | |
| Open | Reader cannot upload | Confirm UI/API blocks action. | Dev2 | |
| Open | Contributor cannot manage members | Confirm UI/API blocks action. | Dev2 | |
| Open | Non-member cannot access another domain | Confirm cross-domain query/list access is blocked. | Dev2 | |
| Open | Admin actions are traceable | Confirm logs or DB records allow review. | Dev2 | |
| Open | Database password is strong | Confirm no weak defaults such as `1234` in production. | Dev2 | |
| Open | API keys rotated for production | Confirm development keys are not reused. | Dev2 | |
| Open | User deprovisioning path documented | Confirm owner and process for removing users. | Dev2 | |

## 4. Functional Testing

| Status | Item | Verification / Evidence | Owner | Notes |
|---|---|---|---|---|
| Open | UAT completed | `docs/UAT_plan.md` has actual results and sign-off. | Dev2 | |
| Open | End-to-end PDF flow works | Upload PDF, process to done, query, verify citations. | Dev2 | |
| Open | DOCX upload works | Upload DOCX and confirm searchable chunks. | Dev2 | |
| Open | CSV upload works | Upload CSV and confirm searchable chunks. | Dev2 | |
| Open | Image/OCR upload works | Upload image or scanned PDF and confirm OCR result. | Dev2 | |
| Open | Arabic flow works | Upload Arabic document and ask Arabic question. | Dev2 | |
| Open | Empty-domain query behaves clearly | Confirm no-context message. | Dev2 | |
| Open | Invalid files are rejected | Confirm unsupported type returns clear error. | Dev2 | |
| Open | Oversized files are rejected | Confirm configured size limit is enforced. | Dev2 | |
| Open | Evaluation logs are created | Ask question and confirm evaluation record. | Dev3 | |

## 5. Performance

| Status | Item | Verification / Evidence | Owner | Notes |
|---|---|---|---|---|
| Open | Load test completed | Run Locust manually and attach summary. | Dev2 | |
| Open | p95 latency under threshold | Confirm p95 under 3000 ms for agreed scenario. | Dev2 | |
| Open | Error rate under threshold | Confirm error rate below 5%. | Dev2 | |
| Open | Worker handles expected upload volume | Confirm queue drains under expected use. | Dev1 | |
| Open | Cache behavior acceptable | Confirm repeated query behavior and no stale critical issue. | Dev3 | |

## 6. Documentation

| Status | Item | Verification / Evidence | Owner | Notes |
|---|---|---|---|---|
| Open | README current | Confirm architecture, setup, ports, and run guide are correct. | Dev1 | |
| Open | User guide complete | Review `docs/user_guide.md`. | Dev2 | |
| Open | Governance policy approved | Review `docs/AI_governance_policy.md`. | Dev2 | |
| Open | UAT plan complete | Review `docs/UAT_plan.md`. | Dev2 | |
| Open | API docs accessible | Confirm OpenAPI docs load in target environment. | Dev1 | |
| Open | Runbook exists for common failures | Confirm worker, Redis, DB, LLM, and OCR troubleshooting docs. | Dev1 | |

## 7. Release Decision

Known blockers:

| ID | Description | Owner | Required Fix | Status |
|---|---|---|---|---|
| | | | | |

Known non-blocking risks:

| ID | Description | Owner | Mitigation | Accepted By |
|---|---|---|---|---|
| | | | | |

## 8. Team Sign-Off

| Role | Name | Area | Ready? | Signature | Date |
|---|---|---|---|---|---|
| Dev1 | | Infrastructure and deployment | Yes / No | | |
| Dev2 | | Testing, security, and compliance | Yes / No | | |
| Dev3 | | Evaluation and answer quality | Yes / No | | |
| Project Lead | | Final release approval | Yes / No | | |

## 9. Final Go/No-Go

| Decision | Made By | Date | Notes |
|---|---|---|---|
| GO / NO-GO | | | |

Do not deploy to production until every blocker is closed or explicitly accepted by the project lead.

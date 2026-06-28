# User Acceptance Testing Plan

**Project:** Multi-Domain RAG System  
**Sprint:** 4 - Testing, Compliance, and Documentation  
**Environment:** API `http://localhost:8000`, UI `http://localhost:3000`  
**Prepared by:** Kerollos Mansour  
**Date:** June 2026

## 1. Purpose

This document defines the manual user acceptance tests that must be completed before release. It covers user-facing workflows, role-based access control, domain isolation, document ingestion, retrieval, answer generation, evaluation visibility, and expected error behavior.

UAT is successful only when the tester confirms that a real user can complete the main workflows through the UI or API without developer intervention, and that restricted actions are blocked with understandable errors.

## 2. Scope

In scope:

- Login through dev auth and Keycloak where configured.
- Domain creation, listing, member assignment, and archive/delete behavior.
- Document upload for supported formats.
- Document processing state transitions.
- Question answering with citations.
- Evaluation dashboard and quality records.
- RBAC restrictions for reader, contributor, domain_admin, and system_admin users.
- Cross-domain isolation.
- Common error handling and degraded service behavior.
- Session persistence.

Out of scope:

- Penetration testing.
- Long-running production soak tests.
- Model quality benchmarking beyond the visible evaluation scores.
- Browser compatibility outside the project-supported browsers.

## 3. Required Test Data

Before starting UAT, prepare the following:

| Data Item | Required Value |
|---|---|
| Admin user | `admin` or another user with `system_admin` privileges |
| Reader user | A user assigned as `reader` to the UAT domain |
| Contributor user | A user assigned as `contributor` to the UAT domain |
| Domain admin user | A user assigned as `domain_admin` to the UAT domain |
| UAT domain | A clean domain named `UAT Test Domain` |
| Valid PDF | A small searchable PDF under 50 MB |
| Valid DOCX | A small Word document under 50 MB |
| Invalid file | A `.exe`, `.zip`, or other unsupported type |
| Oversized file | A test file larger than 50 MB |
| Corrupted PDF | A damaged or password-protected PDF |
| Arabic document | Arabic PDF or DOCX for multilingual validation |

## 4. Preconditions

- Backend gateway is running at `http://localhost:8000`.
- Frontend is running at `http://localhost:3000`.
- PostgreSQL, Redis, Qdrant storage, worker service, and evaluation service are available.
- The UAT users exist in the `users` table or are provisioned through the configured identity provider.
- The tester knows which role each test user has.
- Browser cache is cleared or the tester uses separate browser profiles for each role.

## 5. Result Rules

Use the **Actual Result** column to record what happened, including any HTTP status, UI message, or unexpected behavior.

Use **Pass/Fail** as follows:

- **Pass:** Actual result matches the expected result.
- **Fail:** Actual result differs from expected result or the tester cannot complete the scenario.
- **Blocked:** Test cannot be executed because a prerequisite is missing.
- **N/A:** Scenario does not apply to the selected deployment mode. Add a note explaining why.

## 6. Test Scenarios

| Test ID | Feature | Role | Test Steps | Expected Result | Actual Result | Pass/Fail |
|---|---|---|---|---|---|---|
| UAT-01 | Dev Auth Login | Admin | 1. Open `http://localhost:3000`.<br>2. Enter user ID `admin`.<br>3. Click Login. | User is authenticated, redirected to the main app, and receives a valid JWT-backed session. | | |
| UAT-02 | Invalid Dev Auth Login | Any | 1. Open login page.<br>2. Enter `fakeuser999`.<br>3. Click Login. | Login is rejected. UI shows a clear user-not-found or unauthorized message. API returns 401. | | |
| UAT-03 | Keycloak Login | Any provisioned user | 1. Open login page.<br>2. Select Keycloak login if available.<br>3. Enter valid Keycloak credentials. | User is authenticated through Keycloak and returned to the app with the correct mapped role. | | |
| UAT-04 | Domain Creation | Admin | 1. Login as admin.<br>2. Open Domains.<br>3. Create `UAT Test Domain` with a useful description. | Domain appears in the domain list with status `active`. | | |
| UAT-05 | Duplicate Domain Name | Admin | 1. Login as admin.<br>2. Create a domain using a name that already exists. | Creation is blocked with a duplicate/conflict message. API returns 409 or equivalent validation error. | | |
| UAT-06 | PDF Upload | Contributor | 1. Login as contributor.<br>2. Select UAT domain.<br>3. Upload a valid PDF under 50 MB. | Upload is accepted, API returns 202, and document appears with status `pending`. | | |
| UAT-07 | DOCX Upload | Contributor | 1. Login as contributor.<br>2. Select UAT domain.<br>3. Upload a valid DOCX. | Upload is accepted, API returns 202, and document appears in the document list. | | |
| UAT-08 | Image Upload | Contributor | 1. Login as contributor.<br>2. Upload a PNG or JPG containing readable text. | Upload is accepted and routed through OCR during processing. | | |
| UAT-09 | CSV Upload | Contributor | 1. Login as contributor.<br>2. Upload a small CSV file. | Upload is accepted and processed into searchable chunks. | | |
| UAT-10 | Invalid File Type | Contributor | 1. Login as contributor.<br>2. Try uploading `.exe`, `.zip`, or another unsupported file. | Upload is rejected with an unsupported-file-type message. API returns 400. | | |
| UAT-11 | Oversized File | Contributor | 1. Login as contributor.<br>2. Try uploading a file larger than 50 MB. | Upload is rejected with a file-size message. API returns 400. | | |
| UAT-12 | Processing Status | Contributor | 1. Upload a valid PDF.<br>2. Watch the document status.<br>3. Refresh until worker completes. | Status changes from `pending` to `processing` to `done`; chunk count becomes greater than zero. | | |
| UAT-13 | Processing Failure | Contributor | 1. Upload corrupted or password-protected PDF.<br>2. Wait for worker attempt. | Status changes to `failed` and an error reason is visible to an admin or in logs. | | |
| UAT-14 | Query With Documents | Reader | 1. Login as reader.<br>2. Select UAT domain with processed documents.<br>3. Ask "What is this document about?" | Answer is generated and includes citations with source document metadata. | | |
| UAT-15 | Query Empty Domain | Reader | 1. Login as reader.<br>2. Select a domain with no processed documents.<br>3. Ask a question. | System responds clearly that no relevant context/documents were found. | | |
| UAT-16 | Citation Display | Reader | 1. Ask a question in a populated domain.<br>2. Inspect citations. | Citations show filename, page when available, score, and snippet. Citation content supports the answer. | | |
| UAT-17 | Evaluation Dashboard | Admin | 1. Ask a question.<br>2. Open Quality/Evaluation dashboard.<br>3. Locate recent query. | Evaluation record shows query, scores, model, timestamp, and any failure state if evaluation failed. | | |
| UAT-18 | Reader Cannot Upload | Reader | 1. Login as reader.<br>2. Try to upload a document. | Upload action is hidden or blocked. API returns 403 if attempted directly. | | |
| UAT-19 | Reader Cannot Delete Domain | Reader | 1. Login as reader.<br>2. Attempt domain delete/archive if UI exposes action. | Action is unavailable or blocked with 403. Domain remains active. | | |
| UAT-20 | Contributor Can Upload | Contributor | 1. Login as contributor.<br>2. Upload valid document to assigned domain. | Upload succeeds with 202 and document appears in list. | | |
| UAT-21 | Contributor Cannot Manage Members | Contributor | 1. Login as contributor.<br>2. Try to add/change domain members. | Action is unavailable or blocked with 403. Membership is unchanged. | | |
| UAT-22 | Admin Can Manage Members | Admin | 1. Login as admin.<br>2. Open domain members.<br>3. Assign reader role to a test user. | Member assignment succeeds and appears in the member list. | | |
| UAT-23 | Cross-Domain Isolation | Reader | 1. Login as reader assigned to Domain A.<br>2. Try to query Domain B or access its document list. | Access is blocked with 403, and Domain B does not appear in the visible domain list. | | |
| UAT-24 | Worker Down Behavior | Contributor | 1. Stop worker service in a controlled environment.<br>2. Upload a document.<br>3. Observe UI. | Upload is accepted but document remains pending. UI does not crash and communicates pending state. | | |
| UAT-25 | LLM Unavailable Behavior | Reader/Admin | 1. Configure invalid LLM credentials in a test environment.<br>2. Restart services.<br>3. Ask a question. | System returns a clear upstream/LLM unavailable error, not a blank screen. | | |
| UAT-26 | Document Deletion | Contributor/Admin | 1. Select a processed document.<br>2. Delete it.<br>3. Refresh document list. | Document disappears from list and should no longer appear in citations. | | |
| UAT-27 | Session Persistence | Any | 1. Login.<br>2. Close browser tab.<br>3. Reopen app before token expiry. | User remains logged in if token is still valid; otherwise redirected to login. | | |
| UAT-28 | Arabic Query | Reader | 1. Upload and process Arabic document.<br>2. Ask an Arabic question. | Answer and citations are relevant to the Arabic source content. | | |
| UAT-29 | Cache Behavior | Reader | 1. Ask the same question twice in same domain.<br>2. Compare second response time. | Second response may be faster due to cache and must return equivalent answer/citations. | | |
| UAT-30 | Logout | Any | 1. Login.<br>2. Click logout.<br>3. Try returning to protected page. | Session token is removed and protected pages require login again. | | |

## 7. Defect Tracking

| Defect ID | Test ID | Description | Severity | Owner | Status | Resolution Notes |
|---|---|---|---|---|---|---|
| | | | | | | |

Severity guidance:

- **Critical:** Blocks release, data leak, authentication bypass, or complete workflow failure.
- **High:** Major workflow broken for a primary role.
- **Medium:** Important feature degraded but workaround exists.
- **Low:** Cosmetic issue, confusing copy, or minor usability problem.

## 8. Evidence to Capture

For each completed UAT run, keep:

- Date and environment used.
- Browser and OS.
- User IDs tested.
- Screenshot of failed scenarios.
- API status code and response body for failed API scenarios.
- Defect IDs linked to failed tests.
- Final signed version of this document.

## 9. Sign-Off

| Role | Name | Signature | Date | Decision |
|---|---|---|---|---|
| QA Lead | | | | Pass / Fail / Conditional |
| Dev Lead | | | | Pass / Fail / Conditional |
| Product Owner | | | | Pass / Fail / Conditional |
| Project Manager | | | | Pass / Fail / Conditional |

**Final UAT Status:** Pass / Conditional Pass / Fail  
**Conditional pass notes:**  


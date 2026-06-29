# AI Governance Policy

**System:** Multi-Domain RAG System  
**Version:** 1.0  
**Effective Date:** June 2026  
**Owner:** Kerollos Mansour  
**Review Cycle:** Every 6 months, or after any major architecture, model, auth, or data-handling change

## 1. Purpose

This policy defines responsible use, data-handling expectations, and operational controls for the Multi-Domain RAG System. The system lets authorized users upload documents into isolated knowledge domains and ask questions answered from those documents with citations.

The system is not a general-purpose public chatbot. It is a document-grounded assistant for controlled organizational knowledge bases. Its outputs must be treated as decision support, not final authority.

## 2. System Overview

The system uses retrieval-augmented generation:

1. A user selects a domain and asks a question.
2. The retrieval service searches processed documents in that domain.
3. Relevant chunks are sent to an LLM with instructions to answer from the retrieved context.
4. The answer is returned with citations.
5. Query metadata, answer content, citations, and evaluation scores may be logged for audit and quality review.

Main components:

- `domain-service`: domains, users, memberships, roles, and domain configuration.
- `ingestion-service`: upload validation and document records.
- `worker-service`: extraction, OCR, chunking, embeddings, vector storage, and status updates.
- `retrieval-service`: dense, sparse, rerank, and optional graph retrieval.
- `generation-service`: RAG prompt construction, LLM calls, answer return, and query logging.
- `evaluation-service`: automated answer quality scoring.
- PostgreSQL, Redis, and Qdrant storage.

## 3. Roles and Access

| Role | Allowed Use |
|---|---|
| Reader | Ask questions in assigned domains and view permitted answers/citations. |
| Contributor | Reader permissions plus document upload in assigned domains. |
| Domain Admin | Contributor permissions plus member management and domain administration for assigned domains. |
| System Admin | Full administrative access across domains, users, and configuration. |

Access must follow least privilege. Users should receive the lowest role that allows them to do their work.

## 4. Acceptable Use

Users may:

- Ask questions about documents in domains they are assigned to.
- Upload authorized organizational documents to domains where they have contributor or admin access.
- Use answers as a starting point for document review.
- Use citations to locate and verify source material.
- Report low-quality answers, missing documents, or suspicious citations.
- Use Arabic and English content where supported by the configured OCR, embedding, and generation models.

## 5. Prohibited Use

Users must not:

- Upload documents they are not authorized to share.
- Upload secrets, private keys, passwords, payroll data, medical records, national IDs, payment card data, or other sensitive personal data unless specifically approved.
- Use the system as the only basis for legal, medical, financial, safety-critical, or employment decisions.
- Ask the system to bypass access controls or reveal data from another domain.
- Share JWT tokens, browser sessions, API keys, or credentials.
- Bulk scrape outputs or run load tests without administrator approval.
- Treat answers without citations as verified facts.
- Intentionally upload malicious files or documents designed to manipulate model output.

## 6. Data Handling

### Domain Isolation

Each domain has separate membership, documents, retrieval indexes, and configuration. Users may only access domains where they are explicitly assigned or where their system-level role permits access.

### Uploaded Files

Uploaded files are stored on the server filesystem. Metadata and processing state are stored in PostgreSQL. Extracted chunks are stored in PostgreSQL and indexed for retrieval. Embeddings are stored in Qdrant.

### LLM Processing

When cloud LLM routing is enabled, the generation request may send the user question and retrieved document chunks to the configured provider. If documents are sensitive, administrators should route the domain to a local LLM such as Ollama where feasible.

### Logs

The system may log:

- User ID.
- Domain ID.
- Query text.
- Answer text.
- Citation metadata.
- Evaluation scores.
- Timestamps and service errors.

Logs are used for audit, troubleshooting, and quality monitoring. Administrators should restrict access to logs because they may contain document excerpts and user questions.

### Retention

Unless a stricter organizational policy exists:

- Uploaded documents remain until deleted by an authorized administrator or cleanup process.
- Query logs and evaluation logs remain until purged by database retention jobs.
- Test data should be removed after testing.
- Backups should follow the same confidentiality controls as the live database.

## 7. Quality and Human Oversight

All users are responsible for checking citations before acting on an answer. Administrators must review low-scoring evaluation records and investigate repeated failures.

Known quality risks:

- Missing source documents can produce incomplete answers.
- OCR may misread scans, tables, handwriting, or low-quality images.
- Citations may identify relevant passages without proving every sentence in the answer.
- LLM output can be fluent but unsupported.
- Mixed-language content may reduce retrieval or generation quality.
- Cached answers may reflect previously indexed content until cache expiration.

## 8. Security Controls

Required controls:

- Authentication must be enabled for all protected endpoints.
- RBAC checks must be enforced at service boundaries.
- `.env` must not be committed to version control.
- API keys and JWT keys must be treated as secrets.
- CORS must be restricted to trusted frontend origins in production.
- Database credentials must use strong passwords.
- Administrative actions should be traceable through logs.
- Load testing must be performed only in approved environments.

## 9. Administrator Responsibilities

Administrators must:

- Provision users with the correct role.
- Remove access for users who no longer need it.
- Review domain membership regularly.
- Configure local or cloud LLM routing based on data sensitivity.
- Monitor evaluation scores and error logs.
- Maintain backups and retention procedures.
- Keep dependencies, models, and services updated.
- Document production configuration changes.

## 10. User Responsibilities

Users must:

- Use only assigned domains.
- Upload only approved documents.
- Verify answers against citations.
- Report inaccurate answers or missing citations.
- Avoid entering unnecessary sensitive personal information into questions.
- Protect their account and session.

## 11. Incident Handling

Report the following immediately to the system administrator:

- Suspected cross-domain data exposure.
- Unauthorized access or leaked tokens.
- Answers citing documents from the wrong domain.
- Repeated hallucinations or unsupported answers.
- Accidental upload of sensitive or prohibited data.
- Service behavior that suggests prompt injection or data exfiltration attempts.

Incident response should include access review, log preservation, affected-domain analysis, data removal where needed, and documented remediation.

## 12. Review and Approval

This policy must be reviewed:

- Before production go-live.
- After major model/provider changes.
- After authentication or RBAC changes.
- After any security incident.
- At least every 6 months.


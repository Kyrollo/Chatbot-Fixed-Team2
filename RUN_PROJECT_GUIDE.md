# RAG System Run Guide
**Fixed Solutions AI Internship 2026 | Local Deployment & Operations Guide**

This guide describes how to set up, configure, and run the complete RAG backend stack and React frontend on your local machine.

---

## 1. Required Installations

Ensure the following tools are installed and configured on your PATH:

| Component | Required Version | Purpose | Verify Command |
|:---|:---|:---|:---|
| **Python** | 3.11 – 3.13 | Backend runtime & scripting | `python --version` |
| **Java (JDK)** | 17+ | Required to run Keycloak locally | `java -version` |
| **PostgreSQL** | 16 | Primary relational database | `psql -U postgres -V` |
| **Node.js & npm** | 20+ (npm 10+) | React frontend builder & package manager | `node -v` && `npm -v` |
| **Tesseract OCR** | Local binary | Scanned PDF text extraction | `tesseract --version` |

---

## 2. Environment Variables Configuration

Copy `.env.example` to a new file named `.env` at the root of the workspace:

```bash
copy .env.example .env
```

Ensure the database settings match your local credentials. The database named `domain_db` must exist:

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=YOUR_POSTGRES_PASSWORD_HERE
POSTGRES_DB=domain_db

# Primary LLM provider configuration (Groq cloud)
GROQ_API_KEY=gsk_YOUR_GROQ_API_KEY
GROQ_MODEL=llama-3.3-70b-versatile

# Fallback LLM configuration (Local Ollama)
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.2:3b
```

---

## 3. Step-by-Step Startup Order

To prevent paging file exhaustion and DLL collision limits on Windows, always start components in the following order:

### Step 3.1: Start PostgreSQL
1. Ensure the PostgreSQL service is active:
   * **Windows (cmd as Admin):** `net start postgresql-x64-16`
   * **Linux/macOS:** `sudo systemctl start postgresql`
2. Create the target database if it does not exist:
   ```bash
   psql -U postgres -c "CREATE DATABASE domain_db;"
   ```

### Step 3.2: Start Local Infrastructure & APIs (Launcher Script)
The launcher script automatically downloads portable Redis and Keycloak (if not present) and starts them, followed by FastAPI backend services:

1. Activate your virtual environment:
   * **Windows:** `.venv\Scripts\activate`
   * **Linux/macOS:** `source .venv/bin/activate`
2. Run the launcher script:
   ```bash
   # Option A: Start backend APIs + local Redis + local Keycloak
   python run_services.py

   # Option B (RECOMMENDED for ingestion): Also launch Celery worker
   python run_services.py --worker
   ```
   *Note: The script includes a 5-second startup stagger between uvicorn processes to prevent DLL loading errors.*

### Step 3.3: Start the React Frontend
1. Open a new console window.
2. Navigate to the `rag-ui` directory:
   ```bash
   cd rag-ui
   ```
3. Install dependencies:
   ```bash
   npm install
   ```
4. Start the Vite local development server:
   ```bash
   npm run dev
   ```
   The frontend will be accessible at: **`http://localhost:5173/`**

---

## 4. Verification & Health Check URLs

Verify that each service is running properly by calling its health check endpoints:

| Service | Host Port | Check Command / URL | Expected Output |
|:---|:---|:---|:---|
| **Domain Service** | `8001` | `curl http://localhost:8001/health` | `{"status":"ok","service":"domain-service"}` |
| **Ingestion Service** | `8002` | `curl http://localhost:8002/health` | `{"status":"ok","service":"ingestion-service"}` |
| **Retrieval Service** | `8003` | `curl http://localhost:8003/health` | `{"status":"ok","service":"retrieval-service"}` |
| **Generation Service** | `8004` | `curl http://localhost:8004/generate/health` | `{"status":"ok","service":"generation-service"}` |
| **Evaluation Service** | `8005` | `curl http://localhost:8005/evaluate/health` | `{"status":"ok","service":"evaluation-service"}` |

---

## 5. Troubleshooting & Common Errors

### 5.1: Windows [WinError 1455] / Paging File Too Small
* **Cause:** Multiple services loading deep machine learning frameworks (like PyTorch and safetensors) concurrently, exceeding the Windows system commit charge.
* **Fixes:**
  1. Let `run_services.py` manage service staggering (it sleeps 5 seconds between launches).
  2. Increase your system Virtual Memory / Paging file size in Windows Advanced System Settings to at least 16 GB.
  3. Close unused heavy background processes (Docker Desktop, multiple IDEs).

### 5.2: Redis connection error / HELLO command fails
* **Cause:** The Python `redis` library uses RESP3 protocol by default, which is unsupported by the bundled Redis 5.x portable server.
* **Fix:** The codebase explicitly resolves this by establishing connections with `protocol=2` mapping. Do not modify the redis connection arguments.

### 5.3: Alembic Migrations or DB Synced errors
* **Cause:** SQLAlchemy ORM schemas mismatched with the local PostgreSQL database structure.
* **Fix:** The `domain-service` includes an automatic table generation layer inside the startup lifespan. On launch, it calls `init_db()` which runs `Base.metadata.create_all` automatically. If errors persist, reset the database with:
  ```bash
  psql -U postgres -d domain_db -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
  ```
  Restart `run_services.py` to recreate clean synced tables.

# RAG System — Complete Execution & Deployment Guide

This guide contains the exact step-by-step commands and procedures for initializing the database, starting all backend services, launching the monitoring stack, executing the Locust load tests, applying bottleneck tuning, and serving the frontend UI in three distinct ways: **Vite Dev Server**, **Caddy Server**, and **Windows Internet Information Services (IIS)**.

---

## 🔌 Service Port Topology & Environment Configuration

Here are the ports configured in your `.env` file and Docker topologies:

| Component / Service | Internal Port | Gateway / Proxy Port | Technology | Environment Variable | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **API Gateway (Caddy)** | — | `8000` | HTTPS Proxy | `GATEWAY_PORT=8000` | Entry point for all API requests |
| **Monolith API (FastAPI)** | `8001` | Proxy via `8000` | FastAPI | `DOMAIN_SERVICE_PORT=8001` | Main backend gateway service |
| **Evaluation API** | `8005` | Proxy via `8000` | FastAPI | `EVALUATION_SERVICE_PORT=8005` | Evaluation judges & telemetry |
| **Vite Dev Server** | `5173` | — | Vite / HTTP | `VITE_PORT=5173` | Local frontend dev server |
| **React UI (Caddy)** | `3001` | `3001` | HTTPS server | `UI_PORT=3001` | Serves compiled production build |
| **Keycloak Gateway (Caddy)**| — | `8443` | HTTPS Proxy | `KEYCLOAK_GATEWAY_PORT=8443` | Secure OIDC Auth entry point |
| **Keycloak (Auth)** | `8180` | Proxy via `8443` | OpenID Connect | `KEYCLOAK_PORT=8180` | Keycloak server port |
| **Database (PostgreSQL)** | `5434` | — | PostgreSQL 17 | `POSTGRES_PORT=5434` | Relational & Graph (AGE) storage |
| **Cache & Queue (Redis)** | `6379` | — | Redis cache | `REDIS_PORT=6379` | Celery broker & cache |
| **Grafana (Monitoring)** | `3000` | — | Dashboard UI | — | Visualizes query latency, cache rate |
| **Prometheus (Monitoring)** | `9092` | — | Metrics DB | — | Scraping engine for service metrics |
| **Alertmanager** | `9093` | — | Alert Dispatch | — | Manages threshold system alerts |

---

## 🛠️ Step 1: Database Setup & Migrations

Before running any services, initialize PostgreSQL 17 (on port `5434` as configured in the `.env` file).

### 1.1 Create the Database
Open **Command Prompt (CMD)** and run:
```cmd
set PGPASSWORD=1234
psql -h localhost -p 5434 -U postgres -c "CREATE DATABASE domain_db;"
```

### 1.2 Run Schema Migrations
Activate your Python virtual environment and run the migration script:
```powershell
# PowerShell
.venv\Scripts\Activate.ps1
python scripts/run_migration.py
```
```cmd
:: CMD
.venv\Scripts\activate.bat
python scripts/run_migration.py
```

### 1.3 Reset Database (Optional)
To purge existing logs, documents, and chunks back to a clean slate:
```powershell
python scripts/clear_database.py
python scripts/run_migration.py
```

---

## 🐳 Step 2: Start Infrastructure Services (Docker)

The project dockerizes the gateway and authentication layers to guarantee consistent routing.

### 2.1 Start Keycloak and Traefik
From the project root directory, run:
```powershell
docker compose up -d
```

### 2.2 Verify Keycloak and Traefik
- **Keycloak Administration:** [http://localhost:8180](http://localhost:8180) (Default Credentials: `admin` / `admin`)
- **Traefik Dashboard:** [http://localhost:8088](http://localhost:8088)

---

## 🏃 Step 3: Run Backend Services (APIs + Celery Worker)

Launch the core Python processes using the custom orchestrator.

### 3.1 Start Services on Host
To run the full stack, including APIs, document ingestion workers (PaddleOCR/Surya), and evaluation backend:

1. **(Optional) Start Ubuntu WSL2** in the background (only required if using the Apache AGE Graph database layer):
```powershell
start "" wsl -d Ubuntu-22.04 -- bash -c "tail -f /dev/null"
```

2. **Start the backend services orchestrator** (ensure virtual environment is active):
```powershell
# Activate virtual environment
.venv\Scripts\Activate.ps1

# Run the complete stack (skipping docker-hosted infra to prevent port clashes)
python run_services.py --worker --evaluation --skip-infra
```

### 3.2 Verify Service Health
Run the health checks from another terminal to ensure all APIs are active:
```powershell
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8003/health
Invoke-RestMethod http://localhost:8004/generate/health
Invoke-RestMethod http://localhost:8005/health
```

---

## 📊 Step 4: Monitoring Stack & Baseline Metric Collection

### 4.1 Launch the Monitoring Stack (Prometheus + Grafana)
Deploy the monitoring agents in Docker:
```powershell
docker compose -f monitoring/docker-compose.monitoring.yml up -d
```
- **Grafana Console:** [http://localhost:3000](http://localhost:3000) (Default: `admin` / `admin`)
- **Prometheus UI:** [http://localhost:9092](http://localhost:9092)
- **Alertmanager:** [http://localhost:9093](http://localhost:9093)

### 4.2 Run Baseline Metric Collection
Execute the pre-test collector to document the idle system state. Since it is a bash script, execute it inside **Git Bash** or **WSL2 Ubuntu**:
```bash
bash monitoring/scripts/baseline.sh
```
*This command writes and prints results automatically to:* `monitoring/baseline_results.txt`.

---

## 🚀 Step 5: Start Locust Load Test & Apply Tuning

Simulate concurrent RBAC users querying the endpoints.

### 5.1 Start the Locust Load Test
Ensure the virtual environment is active:
```powershell
# Interactive Web UI mode
locust -f tests/load_test.py --host=http://localhost:8000
```
Open **[http://localhost:8089](http://localhost:8089)** to set user count and spawn rate.

Alternatively, run **headless** for 3 minutes and write statistics to CSV:
```powershell
locust -f tests/load_test.py --host=http://localhost:8000 --users 50 --spawn-rate 5 --run-time 3m --headless --csv=tests/load_results
```

### 5.2 Tune Bottlenecks If They Appear
If Grafana reveals high latency or database queuing during the test, execute the tuning script inside **Git Bash** or **WSL2 Ubuntu** to apply fixes or diagnostics:

```bash
# Run specific tuning sections:
bash monitoring/scripts/tuning.sh A    # Fix PostgreSQL Connection Pool Exhaustion
bash monitoring/scripts/tuning.sh B    # Fix Redis Memory Limit / Evictions
bash monitoring/scripts/tuning.sh C    # Increase Uvicorn/Celery Worker Processes
bash monitoring/scripts/tuning.sh D    # Run EXPLAIN ANALYZE on Slow SQL Queries
bash monitoring/scripts/tuning.sh E    # Add Missing Databases Indexes
bash monitoring/scripts/tuning.sh ALL  # Run all diagnostics and index additions
```

---

## 🎨 Step 6: Serve the React UI (3 Deployment Options)

### 💻 Option A: Vite Development Server (npm run dev)
Best for code editing and local development.
```powershell
cd rag-ui
npm install
npm run dev
```
Open browser: **[http://localhost:5173](http://localhost:5173)**

---

### 🐊 Option B: Production Gateway serving with Caddy
Serves the built production bundle using Caddy's secure TLS configuration.

1. **Build the production bundle:**
   ```powershell
   cd rag-ui
   npm run build
   cd ..
   ```
2. **Define environment variables for Caddy:**
   Set the ports mapping in your current CLI terminal:
   ```powershell
   # PowerShell
   $env:UI_PORT="3001"
   $env:GATEWAY_PORT="8000"
   $env:KEYCLOAK_GATEWAY_PORT="8443"
   $env:KEYCLOAK_PORT="8180"
   $env:DOMAIN_SERVICE_PORT="8001"
   ```
   ```cmd
   :: CMD
   set UI_PORT=3001
   set GATEWAY_PORT=8000
   set KEYCLOAK_GATEWAY_PORT=8443
   set KEYCLOAK_PORT=8180
   set DOMAIN_SERVICE_PORT=8001
   ```
3. **Start the Caddy server:**
   ```powershell
   caddy.exe run --config Caddyfile
   ```
Open browser: **[https://localhost:3001](https://localhost:3001)**

---

### 🖥️ Option C: Production Serving via Windows IIS
Serves the React app and proxies backend routes using IIS's native modules.

#### Prerequisites
1. **Enable IIS** on your Windows system:
   - Search for **"Turn Windows features on or off"** in the Windows Start menu.
   - Check **Internet Information Services**.
   - Expand and check **Common HTTP Features** (Static Content) and **Application Development Features** (.NET Extensibility, ASP.NET).
2. **Install URL Rewrite Module:**
   - Download and install the URL Rewrite module: [IIS URL Rewrite Download](https://www.iis.net/downloads/microsoft/url-rewrite).
3. **Install Application Request Routing (ARR 3.0):**
   - Download and install: [IIS ARR Download](https://www.iis.net/downloads/microsoft/application-request-routing).
   - Open IIS Manager, click on your server node, select **Application Request Routing Cache**, click **Server Proxy Settings** in the right pane, check **Enable proxy**, and click **Apply**.

#### Deployment Setup
1. **Build the frontend production folder:**
   ```powershell
   cd rag-ui
   npm run build
   ```
   *Note: This generates output files in `rag-ui/dist/` alongside a pre-configured `web.config` file.*
2. **Add a new IIS Website:**
   - Open **IIS Manager** (`inetmgr`).
   - Right-click **Sites** -> **Add Website**.
   - **Site name:** `rag-ui`
   - **Physical path:** `d:\Personal\Fixed Solutions\Project Files\Last Version\rag-ui\dist`
   - **Binding Type:** `http` or `https`
   - **Port:** `3001` (or desired custom port).
3. **Define Server Port Variable (if necessary):**
   - Ensure the server environment variable matches the rewrite proxy rules in `web.config`.
   - Alternatively, you can modify `rag-ui/dist/web.config` directly to point to the exact FastAPI monolith port:
     Replace:
     `url="http://localhost:{ENV:DOMAIN_SERVICE_PORT}/{R:0}"`
     with:
     `url="http://localhost:8001/{R:0}"`
4. **Permissions Configuration:**
   - Ensure the IIS Application Pool identity (typically `IIS_IUSRS` or `IUSR`) has read permissions to the `rag-ui/dist` directory.
5. **Access the Application:**
   Open browser: **[http://localhost:3001](http://localhost:3001)**

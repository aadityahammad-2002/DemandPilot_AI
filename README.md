
# YottaFlex AI — Demand Forecasting & Inventory Replenishment

A full-stack retail inventory planning system built using **Python, FastAPI, PostgreSQL, React, TypeScript, and Vite**.

The application supports synthetic retail data generation, demand forecasting, inventory replenishment recommendations, decision traceability, validation, and interactive dashboards.

## 1. Prerequisites

Install the following software before setting up the project:

| Software | Version |
|---|---|
| Python | 3.13 |
| Node.js | 24.x or compatible |
| npm | Installed with Node.js |
| PostgreSQL | 18 or compatible |
| Git | Latest stable |
| VS Code | Recommended |

Verify your installations in PowerShell:

```powershell
python --version
node --version
npm --version
git --version
```

PostgreSQL must be installed and its Windows service must be running.

## 2. Clone the Repository

```powershell
git clone https://github.com/aadityahammad-2002/yottaflex-demand-forecasting.git

cd yottaflex-demand-forecasting
```

Open the project in VS Code:

```powershell
code .
```

Alternatively, download the repository as a ZIP, extract it, and open the extracted project folder in VS Code.

All commands below assume you are working inside the project root unless otherwise specified.

## 3. Backend Setup (Python + FastAPI)

Open a new PowerShell terminal in VS Code.

Navigate to the backend:

```powershell
cd backend
```

Create a Python virtual environment:

```powershell
py -3.13 -m venv .venv
```

Activate the environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

Install backend dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements-phase5.txt
```

Verify dependency installation:

```powershell
python -m pip check
```

The backend environment is now installed.

## 4. PostgreSQL Database Setup

This project uses PostgreSQL for persistent retail planning data.

### 4.1 Create a database (first-time setup only)

If you already have an approved `yottaflex_dual_v7` database with migration revision `0005`, **skip database creation and migrations**.

For a fresh installation, open PostgreSQL's `psql` terminal as an administrator:

```powershell
psql -U postgres -d postgres
```

If `psql` is not on your PATH, use its full installation path, for example:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d postgres
```

Run the following commands inside `psql`:

```sql
CREATE ROLE yottaflex LOGIN;
```

Set a private password for the role:

```text
\password yottaflex
```

Create the database:

```sql
CREATE DATABASE yottaflex OWNER yottaflex;
```

Exit `psql`:

```text
\q
```

Use a unique local database password. Never commit database credentials to GitHub.

### 4.2 Configure the backend environment

From the `backend` directory:

```powershell
Copy-Item .env.example .env
```

Open `backend/.env` and configure:

```dotenv
DATABASE_URL=postgresql+psycopg://yottaflex:YOUR_URL_ENCODED_PASSWORD@localhost:5432/yottaflex_dual_v7
APP_ENV=development
SQL_ECHO=false
```

Replace `YOUR_URL_ENCODED_PASSWORD` with your local PostgreSQL password, URL-encoded if it contains special characters.

The `.env` file is private and must not be uploaded to GitHub.

### 4.3 Apply database migrations (fresh empty database only)

If you created a brand-new empty database, run from `backend`:

```powershell
python -m alembic upgrade head
```

Verify the migration:

```powershell
python -m alembic current
```

Expected revision:

```text
0005 (head)
```

**Important:** Do not run migrations against an existing populated or production database without reviewing the target and obtaining approval.

### 4.4 Verify the PostgreSQL schema

From `backend`:

```powershell
python verify_backend_schema_readonly.py
```

Expected result includes:

```text
READ-ONLY POSTGRESQL SCHEMA CHECK: PASS
```

This verification does not modify database records.

## 5. Frontend Setup (React + TypeScript)

Open a second PowerShell terminal in VS Code.

From the project root, navigate to the frontend:

```powershell
cd frontend
```

Install frontend dependencies:

```powershell
npm ci
```

Optional frontend environment configuration:

```powershell
Copy-Item .env.example .env.local
```

The frontend API URL should be:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

The frontend is now ready.

## 6. Run the Project Locally

The backend and frontend run as separate services.

**Important:** Keep both terminal windows running while using the dashboard.

### 6.1 Start the FastAPI Backend — Terminal 1

From the project root, run:

```powershell
cd backend
```

Start the backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.integration.api:create_app --factory --host 127.0.0.1 --port 8000
```

Expected terminal output:

```text
Application startup complete.
Uvicorn running on http://127.0.0.1:8000
```

Backend endpoints:

| Service | URL |
|---|---|
| API Base | http://127.0.0.1:8000 |
| Health Check | http://127.0.0.1:8000/health |
| Swagger API Docs | http://127.0.0.1:8000/docs |
| Dataset Validation | http://127.0.0.1:8000/api/v1/dataset-validation |

Verify the backend by opening:

http://127.0.0.1:8000/health

### 6.2 Start the React Frontend — Terminal 2

From the project root, run:

```powershell
cd frontend
npm run dev
```

Expected output:

```text
VITE ready
Local: http://127.0.0.1:5173/
```

Open the application:

**http://127.0.0.1:5173**

The welcome page provides two dashboard perspectives:

- **Operations Manager:** Demand forecasts, inventory, replenishment recommendations, exceptions, and decision traces.
- **Developer:** Technical architecture, data generation, forecasting methods, validation, and execution evidence.

The dashboard depends on the FastAPI service being available. Database-backed views also require appropriate approved sample records. A fresh empty database will not automatically contain populated inventory or forecast runs.

### 6.3 Stop the Application

Press `Ctrl+C` in each running terminal.

## 7. Optional: Start Backend and Frontend Using One Script

The repository includes:

```text
START_PHASE5_WINDOWS.ps1
```

After installing backend/frontend dependencies and configuring the approved PostgreSQL database at migration `0005`, run from the project root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\START_PHASE5_WINDOWS.ps1
```

This script starts the local API and frontend processes and creates logs under `evidence/`.

It only accepts the configured local isolated database `yottaflex_dual_v7` and requires ports `8000` and `5173` to be available.

It does not run database migrations or full-scale data generation.

If a server does not start correctly, inspect the generated logs or use the separate-terminal method in Section 6.

## 8. Generate a Small Synthetic Dataset

The repository includes a synthetic demo dataset:

```text
examples/smoke_run/
```

It also includes a labelled negative-scenario sample:

```text
examples/smoke_overlay_run/
```

To generate a fresh small dataset without modifying PostgreSQL, open a PowerShell terminal in `backend`:

```powershell
cd backend
```

Run:

```powershell
.\.venv\Scripts\python.exe -m app.generation.cli generate --profile smoke --out ..\new_smoke_output
```

Validate the output:

```powershell
.\.venv\Scripts\python.exe -m app.generation.cli validate --out ..\new_smoke_output
```

The output directory must be new or empty as required by the generator.

Generated data includes SKU/store master records, sales, inventory snapshots, lead-time observations, validation metadata, and restricted ground-truth information.

The five mandatory negative scenarios are represented through controlled labelled overlays, separate from valid baseline records.

**Do not directly import invalid negative overlays into the production database.**

For database import and pipeline execution, review the dedicated runbook before enabling write operations.

## 9. Run Automated Tests

### 9.1 Backend Tests

From the `backend` directory:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --tb=short
```

The repository also provides a Windows verifier that runs the backend suite in four separate batches.

### 9.2 Frontend Unit Tests

From `frontend`:

```powershell
npm run test
```

### 9.3 Production Build

```powershell
npm run build
```

Production build output is generated in:

```text
frontend/dist/
```

### 9.4 Playwright Browser Tests

Install the Chromium browser dependency if necessary:

```powershell
npx playwright install chromium
```

With the approved populated test database and both local services running:

```powershell
npx playwright test --project=chromium --reporter=list
```

Some browser tests interact with the local application. Use only an approved test environment.

### 9.5 Unified Windows Verification

From the project root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\VERIFY_FINAL_WINDOWS.ps1
```

For an additional read-only PostgreSQL verification:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\VERIFY_FINAL_WINDOWS.ps1 -CheckPostgres
```

The verifier saves a local report under `evidence/final_local_*`.

It does not run full-scale generation, database resets, migrations, or GitHub pushes.

## 10. Project Structure

```text
YottaFlex_AI_FINAL_Submission/
|
|-- backend/
|   |-- app/
|   |   |-- forecasting/
|   |   |-- generation/
|   |   |-- integration/
|   |   |-- replenishment/
|   |-- alembic/
|   |-- tests/
|   |-- requirements.txt
|   |-- requirements-phase5.txt
|
|-- frontend/
|   |-- src/
|   |-- e2e/
|   |-- package.json
|   |-- package-lock.json
|
|-- examples/
|   |-- smoke_run/
|   |-- smoke_overlay_run/
|
|-- evidence/
|-- docs/
|-- scripts/
|
|-- START_PHASE5_WINDOWS.ps1
|-- FINAL_WINDOWS_RUNBOOK.md
|-- FINAL_IMPLEMENTATION_REPORT.md
|-- FINAL_ACCEPTANCE_MATRIX.md
|-- GITHUB_SUBMISSION_AUDIT.md
|-- README.md
```

## 11. Core Features

- Deterministic synthetic retail data generation.
- Ten mandatory retail planning entities.
- Store/SKU demand forecasting.
- Forecast validation and explainability.
- Stockout-aware demand handling.
- Labelled negative-scenario overlays.
- Inventory replenishment recommendations.
- MOQ, pack-size and capacity constraints.
- Date-effective SKU lifecycle eligibility.
- Optional verified open-order input.
- Per-recommendation decision traces.
- PostgreSQL persistence and execution history.
- FastAPI endpoints.
- React Operations Manager dashboard.
- React Developer dashboard.
- Small-scale business outcome backtesting.
- Automated backend, frontend and integration tests.

## 12. Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React, TypeScript, Vite |
| Backend | Python, FastAPI |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| Migrations | Alembic |
| Forecasting | Statistical and machine-learning models |
| Testing | Pytest, Vitest, Playwright |
| Data Processing | CSV, optional Parquet |
| Version Control | Git and GitHub |

## 13. Verification Status and Limitations

The enhanced source has recorded the following verification:

- Backend regression: **200 passed, 1 skipped, 0 failed**, executed across four disjoint batches.
- Enhanced TypeScript typecheck: passed.
- Small dataset generation and validation: passed.
- Five labelled negative-scenario overlays: verified on small sample data.
- Small forecasting/replenishment pipeline: verified.
- Small business backtest: executed.

Additional local acceptance is still required for the modified frontend Vitest/Vite/Playwright release, live PostgreSQL concurrency, and full-scale processing.

The target full-scale configuration is:

**2,000 SKUs × 100 stores × 365 days**, representing 73,000,000 potential combinations before lifecycle filtering.

Full-scale acceptance has **not** been independently completed. Resource preflight and separate execution are required.

This project is a local-first coding challenge implementation, not a publicly hardened commercial service. Authentication/RBAC, full-scale load testing and complete purchase-order lifecycle execution are not implemented or fully verified.

See:

- [Final Acceptance Matrix](FINAL_ACCEPTANCE_MATRIX.md)
- [Final Implementation Report](FINAL_IMPLEMENTATION_REPORT.md)
- [Windows Runbook](FINAL_WINDOWS_RUNBOOK.md)

## 14. AI Assistance and Documentation

AI tools were used extensively during design, implementation, debugging, validation and documentation.

Relevant documentation:

- [AI Prompt History](docs/AI_PROMPT_HISTORY.md)
- [AI Assistance Disclosure](docs/AI_ASSISTANCE_DISCLOSURE.md)
- [Requirements Matrix](docs/REQUIREMENTS_MATRIX.md)
- [Architecture Decisions](docs/architecture_decisions.md)
- [Data Dictionary](docs/data_dictionary.json)

The prompt-history file contains available/recovered prompts and does not claim to be a complete verbatim export of every AI interaction.

## 15. Security Notes

- Never commit `backend/.env` or `frontend/.env.local`.
- Never expose PostgreSQL credentials, API keys or private tokens.
- Do not commit `backend/.venv`, `frontend/node_modules` or large generated datasets.
- Keep the backend bound to localhost for the local challenge demo.
- Do not run destructive migrations or imports on an unapproved database.
- Review Git changes before public publication.

## 16. Author

**Aaditya Hammad**

GitHub: https://github.com/aadityahammad-2002

**Project:** YottaFlex AI — Demand Forecasting & Inventory Replenishment

**Challenge:** AI Forward Deployed Engineer — 72-Hour Coding Challenge

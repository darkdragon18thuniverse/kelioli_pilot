# Kelioli Pilot API


A minimal, production-grade, and highly optimized FastAPI application for administrative management, role-based access control, and automated telephonic call record processing via Sarvam AI speech-to-text.

## Project Structure

```text
kelioli_pilot/
├── .venv/
├── .env                  # Environment configurations
├── pyproject.toml
├── pytest.ini            # Pytest suite configurations
├── README.md
├── curltester.md
├── test.csv
├── scripts/              # Operational & DB bootstrap scripts
├── tests/                # Automated isolated test suite
│   └── auth/             # Authentication & RBAC boundary tests
└── src/
    └── app/
        ├── api/          # FastAPI routes
        ├── controllers/  # Business logic layer
        ├── models/       # Active Record database models
        └── services/     # Sarvam AI STT integrations

```

## Installation & Setup

### 1. Audio Processing & System Dependencies
> **Note**: **No system-level `ffmpeg` binary is required.** Audio chunking ($\le 29\text{s}$ segments for Sarvam STT REST API) is handled purely in-memory using PyAV (the `av` package binding).

### 2. Install Python Dependencies

Using `uv` (recommended) or `pip`:

```bash
# Sync dependencies via uv
uv sync

# Or install via requirements.txt
uv pip install -r requirements.txt
```


### 2. Environment Configuration

Create a `.env` file in the root directory:

```env
JWT_SECRET_KEY=system-dev-fallback-token-key-2026
SARVAM_API_KEY=your_sarvam_api_key_here
HTTP_TIMEOUT=15.0

```

---

## Production Deployment & Systemd Setup (GCP VM)

### 1. One-Command Server Setup (`makeserver.sh`)
Run this once on your VM to install system packages, Python virtual environment, dependencies, systemd service, and Nginx proxy:

```bash
chmod +x makeserver.sh runserver.sh
./makeserver.sh
```

### 2. HTTPS SSL Setup (Let's Encrypt Certbot)
To issue a free HTTPS SSL certificate on your VM so Nginx natively listens on Port 443 with HTTPS:

```bash
sudo certbot --nginx -d api.kelioli.curigon.com
```
Follow the prompt to agree to terms. Certbot will automatically configure Nginx for HTTPS on Port 443 and auto-renew the certificate!

### 3. Running & Restarting Server (`runserver.sh`)
Whenever you pull code updates or restart the server, run `runserver.sh`:

```bash
./runserver.sh
```


---

## Running Test Suites

All tests run in an isolated temporary SQLite database environment (`test_production.db`) without touching production data.

```bash
# Run all test suites across the repository
uv run pytest -v

# Run tests for a specific module (e.g., Auth & RBAC)
uv run pytest tests/auth/ -v

# Run a specific test file
uv run pytest tests/auth/test_auth_logic.py -v

```

---

## Organization Data Export & Import

Dump an organization's full data tree (departments, users, parameters, csv uploads, calls, evaluations, metrics, etc.) to JSON for dev workflow, or reload it into a target database:

```bash
# Export an organization's data tree
python scripts/export_org_data.py --org-id 5

# Import an organization's data dump into a target database
python scripts/import_org_data.py --input scripts/exports/org_5_<timestamp>.json
```

---

## Core API Endpoints

1. **Health Check**
* `GET /health` – System operational status check.


2. **Authentication**
* `POST /api/v1/auth/login` – OAuth2 form-data authentication. Returns HS256 JWT access token.
* `GET /api/v1/auth/me` – Decoded user session profile context.


3. **Administration**
* `POST /api/v1/admin/organizations` – Global tenant provisioning (Superadmin only).
* `POST /api/v1/admin/departments` – Sandbox department configuration.
* `POST /api/v1/admin/users` – Hierarchical RBAC user provisioning.


4. **Call Processing**
* `POST /api/v1/calls/process-csv` – Multipart CSV upload for concurrent Sarvam AI transcriptions.

```
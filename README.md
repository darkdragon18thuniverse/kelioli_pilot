# Kelioli Pilot API

A minimal, production-grade, and highly optimized FastAPI application for processing telephonic CSV call records and transcribing them using Sarvam AI.

## Project Structure

```text
kelioli_pilot/
├── .venv/
├── .env                  # Environment configurations (API keys)
├── README.md
├── curltester.md
├── pyproject.toml
├── requirements.txt
├── test.csv
├── uv.lock
└── src/
    └── app/
        ├── controllers/
        │   └── csv_controller.py
        └── main.py

Installation & Setup
1. Prerequisites
Ensure you have uv installed on your machine. If not, install it via:
curl -LsSf [https://astral.sh/uv/install.sh](https://astral.sh/uv/install.sh) | sh

2. Install Dependencies
Install all packages explicitly pinned in requirements.txt:
uv pip install -r requirements.txt

3. Environment Configuration
Create a .env file in the root directory:
SARVAM_API_KEY=your_sarvam_api_key_here
HTTP_TIMEOUT=15.0

Running the Application
Start the development server with hot-reloading active:
uv run uvicorn src.app.main:app --reload

• API Base URL: http://127.0.0.1:8000
• Interactive Documentation (Swagger UI): http://127.0.0.1:8000/api/docs
Core Endpoints
1. Health Check
• Method: GET
• Path: /health
• Description: Zero-dependency lightweight diagnostic check.
2. Process CSV
• Method: POST
• Path: /process-csv
• Payload: multipart/form-data containing a file field.
• Description: Streams call recordings down from URLs supplied in a call_url CSV column, runs speech-to-text transcriptions concurrently using the Sarvam AI pipeline, and handles background cleanup of temporary disk memory automatically.
Testing
You can use the provided curltester.md file for quick execution references. To manually hit the processing endpoint with your sample dataset, execute:
curl -F "file=@test.csv" [http://127.0.0.1:8000/process-csv](http://127.0.0.1:8000/process-csv)
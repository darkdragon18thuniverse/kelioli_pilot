import os
from dotenv import load_dotenv

# Execute environmental variable ingestion before loading downstream system modules
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.app.core.logging_config import setup_logging, get_logger
from src.app.core.middleware import LoggingAndCorrelationMiddleware
from src.app.core.database import init_database
from src.app.api.v1.auth import router as auth_router
from src.app.api.v1.admin import router as admin_router
from src.app.api.v1.compliance import router as compliance_router
from src.app.api.v1.calls import router as calls_router, csv_router
from src.app.api.v1.billing import router as billing_router

# Initialize system logger
setup_logging()
logger = get_logger("src.app.main")

# Initialize the core application framework instance
app = FastAPI(
    title="Kelioli AI Pilot Core",
    description="Multi-Tenant Medical Audit System Routing Engine Architecture.",
    version="1.0.0"
)

# --- Logging & Request Correlation Middleware ---
app.add_middleware(LoggingAndCorrelationMiddleware)

# --- CORS Middleware Security Configuration ---
cors_env = os.getenv("CORS_ALLOWED_ORIGINS")
if cors_env:
    origins = [origin.strip() for origin in cors_env.split(",") if origin.strip()]
else:
    origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  
    allow_headers=["*"],  
)

# --- Database Bootstrapping ---
init_database()

@app.on_event("startup")
def startup_event():
    if not os.getenv("PYTEST_CURRENT_TEST"):
        import threading
        from src.app.services.call_queue_worker import run_worker
        logger.info("Starting background call queue worker thread.")
        threading.Thread(target=run_worker, daemon=True).start()

# --- Application Routing Nodes Mount ---
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(admin_router, prefix="/api/v1/admin")
app.include_router(compliance_router, prefix="/api/v1")
app.include_router(calls_router, prefix="/api/v1/calls")
app.include_router(csv_router, prefix="/api/v1/csv-uploads")
app.include_router(billing_router, prefix="/api/v1/billing")


@app.get("/health", tags=["System Diagnostics"])
def health_check():
    """Returns absolute runtime verification attributes for upstream load balancers."""
    return {"status": "healthy", "engine": "FastAPI", "relational_integrity": "active"}

# --- Uvicorn Server Loop Invocation ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.app.main:app", host="127.0.0.1", port=8000, reload=True)
import os
from dotenv import load_dotenv

# Execute environmental variable ingestion before loading downstream system modules
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.app.core.database import init_database
from src.app.api.v1.auth import router as auth_router
from src.app.api.v1.admin import router as admin_router
from src.app.api.v1.compliance import router as compliance_router
from src.app.api.v1.calls import router as calls_router

# Initialize the core application framework instance
app = FastAPI(
    title="Kelioli AI Pilot Core",
    description="Multi-Tenant Medical Audit System Routing Engine Architecture.",
    version="1.0.0"
)

# --- CORS Middleware Security Configuration ---
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

# --- Application Routing Nodes Mount ---
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(admin_router, prefix="/api/v1/admin")
app.include_router(compliance_router, prefix="/api/v1")
app.include_router(calls_router, prefix="/api/v1/calls")

@app.get("/health", tags=["System Diagnostics"])
def health_check():
    """Returns absolute runtime verification attributes for upstream load balancers."""
    return {"status": "healthy", "engine": "FastAPI", "relational_integrity": "active"}

# --- Uvicorn Server Loop Invocation ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.app.main:app", host="127.0.0.1", port=8000, reload=True)
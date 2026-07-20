from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from src.app.controllers.agents_controller import router as agents_router
from src.app.controllers.calls_controller import router as calls_router
from src.app.core.database import init_db

# Verify local table architecture exists prior to traffic engagement
init_db()

app = FastAPI(
    title="Elite FastAPI Service",
    version="1.0.0",
    description="Production-grade, modular uv-managed FastAPI application."
)

@app.get("/health", tags=["Diagnostics"])
async def health_check() -> dict[str, str]:
    return {"status": "UP", "uptime": "ok"}

# Include clean modular layout routes
app.include_router(agents_router)
app.include_router(calls_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.app.main:app", host="127.0.0.1", port=8000, reload=True)
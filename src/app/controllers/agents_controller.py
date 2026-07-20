from fastapi import APIRouter, HTTPException, Query
from src.app.core.database import add_agent, get_all_agents

router = APIRouter(prefix="/agents", tags=["Agents Directory"])

@router.post("")
async def create_agent(name: str = Query(..., description="The structural profile name configuration of the new agent")) -> dict:
    """Registers a new internal team agent tracking profile instance."""
    if not name.strip():
        raise HTTPException(status_code=400, detail="Agent name cannot be empty")
    return {"agent": add_agent(name)}

@router.get("")
async def fetch_agents() -> dict[str, list[dict]]:
    """Fetches the complete directory profile matrix of current team agents."""
    return {"agents": get_all_agents()}
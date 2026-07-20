import csv
import os
import httpx
from fastapi import APIRouter, UploadFile, HTTPException, Query
from src.app.core.database import save_result, get_all_calls
from src.app.services.stt import download_and_transcribe_call

router = APIRouter(tags=["Calls Engine"])

@router.get("/calls")
async def fetch_calls() -> dict[str, list[dict]]:
    """Retrieves historical transcription runs along with clear matched agent name correlations."""
    return {"calls": get_all_calls()}

@router.post("/process-csv")
async def process_csv(
    file: UploadFile,
    agent_id: int = Query(..., description="The relational numeric database tracking identity ID of the agent")
) -> dict[str, list[dict[str, str]]]:
    if not file.filename or not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Uploaded file must be a CSV")

    content = (await file.read()).decode("utf-8").splitlines()
    reader = csv.DictReader(content)
    
    if not reader.fieldnames or "call_url" not in reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV must contain a 'call_url' column")

    results = []
    http_timeout = float(os.getenv("HTTP_TIMEOUT", "15.0"))

    with httpx.Client(timeout=http_timeout, follow_redirects=True) as client:
        for row in reader:
            url_str = row.get("call_url")
            if not url_str or not (url := url_str.strip()).startswith(("http://", "https://")):
                continue

            try:
                txt = download_and_transcribe_call(url, client)
                results.append({"url": url, "transcript": txt})
                save_result(agent_id=agent_id, url=url, transcript=txt)
            except Exception as e:
                err_str = str(e)
                results.append({"url": url, "error": err_str})
                save_result(agent_id=agent_id, url=url, error=err_str)

    return {"results": results}
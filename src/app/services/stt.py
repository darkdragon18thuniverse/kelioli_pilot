import os
import time
import json
import httpx
import mimetypes
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, ValidationError
from src.app.core.logging_config import get_logger

logger = get_logger(__name__)

MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
BACKOFF_FACTOR = float(os.getenv("RETRY_BACKOFF_FACTOR", 2.0))


def retry_with_backoff(func):
    def wrapper(*args, **kwargs):
        retries = 0
        while retries < MAX_RETRIES:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                retries += 1
                sleep_time = BACKOFF_FACTOR ** retries
                logger.warning(f"External API call failed ({e}). Attempt {retries}/{MAX_RETRIES}. Retrying in {sleep_time}s...")
                if retries >= MAX_RETRIES:
                    logger.error(f"External API call failed permanently after {MAX_RETRIES} attempts.")
                    raise
                time.sleep(sleep_time)
    return wrapper


class STTService:
    @staticmethod
    @retry_with_backoff
    def transcribe(file_path: str) -> Dict[str, Any]:
        api_key = os.getenv("SARVAM_API_KEY")
        if not api_key or api_key == "mock_key":
            logger.warning("SARVAM_API_KEY is not configured with a valid live production key.")
            raise ValueError("SARVAM_API_KEY is not configured with a valid live production key.")

        if not os.path.exists(file_path):
            logger.error(f"Audio file for STT not found: {file_path}")
            raise FileNotFoundError(f"Audio file not found at path: {file_path}")

        url = "https://api.sarvam.ai/speech-to-text"
        headers = {"api-subscription-key": api_key}
        logger.info(f"Initiating Sarvam STT transcription for file: {file_path}")

        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "audio/mpeg" if file_path.lower().endswith(".mp3") else "audio/wav"

        start_time = time.perf_counter()
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, mime_type)}
            data = {"model": "saaras:v3", "mode": "transcribe"}
            with httpx.Client(timeout=120.0) as client:
                res = client.post(url, headers=headers, files=files, data=data)
                if res.status_code >= 400:
                    logger.error(f"Sarvam STT HTTP {res.status_code} Error: {res.text}")
                res.raise_for_status()
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                logger.info(f"Sarvam STT transcription completed successfully in {elapsed_ms:.2f}ms")
                return res.json()


class EvalItem(BaseModel):
    parameter_id: int
    did_follow_rule: int
    failure_reason: Optional[str] = None


class EvalResponse(BaseModel):
    procedure_enquired: str
    evaluations: List[EvalItem]


EVAL_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "procedure_enquired": {"type": "string"},
        "evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "parameter_id": {"type": "integer"},
                    "did_follow_rule": {"type": "integer", "enum": [0, 1]},
                    "failure_reason": {"type": ["string", "null"]}
                },
                "required": ["parameter_id", "did_follow_rule", "failure_reason"],
                "additionalProperties": False
            }
        }
    },
    "required": ["procedure_enquired", "evaluations"],
    "additionalProperties": False
}


class LLMService:
    @staticmethod
    def _call_openrouter(api_key: str, selected_model: str, messages: list) -> str:
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": selected_model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "eval_schema",
                    "strict": True,
                    "schema": EVAL_JSON_SCHEMA
                }
            }
        }
        with httpx.Client(timeout=60.0) as client:
            res = client.post(url, headers=headers, json=payload)
            if res.status_code >= 400:
                logger.error(f"OpenRouter LLM HTTP {res.status_code} Error: {res.text}")
            res.raise_for_status()
            response_data = res.json()
            return response_data["choices"][0]["message"]["content"]

    @staticmethod
    @retry_with_backoff
    def evaluate(model: str, company_context: str, department_context: str,
                 parameters: list, transcript: str) -> Dict[str, Any]:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key or api_key == "mock_key":
            logger.warning("LLM is not configured with a valid live production key.")
            raise ValueError("LLM is not configured with a valid live production key.")

        selected_model = model or "openrouter/free"

        system_prompt = (
            f"You are an expert compliance auditor.\n"
            f"Company Context: {company_context or 'N/A'}\n"
            f"Department Context: {department_context or 'N/A'}\n"
            f"Evaluate the transcript strictly against the parameters and output valid JSON matching the schema."
        )
        user_content = f"Parameters: {json.dumps(parameters)}\n\nTranscript: {transcript}"

        logger.info(f"Initiating OpenRouter LLM compliance evaluation using model '{selected_model}' across {len(parameters)} rules.")
        start_time = time.perf_counter()
        content_str = LLMService._call_openrouter(
            api_key, selected_model,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ]
        )

        try:
            parsed = json.loads(content_str)
            validated = EvalResponse.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"LLM response failed structural validation, attempting one repair call: {e}")
            repair_content = LLMService._call_openrouter(
                api_key, selected_model,
                [
                    {"role": "system", "content": "You output only valid JSON matching the required schema. Fix the structure of the JSON below. Do not change the values, only the shape."},
                    {"role": "user", "content": content_str}
                ]
            )
            parsed = json.loads(repair_content)
            validated = EvalResponse.model_validate(parsed)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(f"OpenRouter LLM evaluation completed successfully in {elapsed_ms:.2f}ms")
        return validated.model_dump()

import os
import time
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
        
        start_time = time.perf_counter()
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "audio/wav")}
            data = {"model": "saaras:v3", "mode": "transcribe"}
            with httpx.Client(timeout=60.0) as client:
                res = client.post(url, headers=headers, files=files, data=data)
                res.raise_for_status()
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                logger.info(f"Sarvam STT transcription completed successfully in {elapsed_ms:.2f}ms")
                return res.json()


class LLMService:
    @staticmethod
    @retry_with_backoff
    def evaluate(model: str, company_context: str, department_context: str, 
                 parameters: list, transcript: str) -> Dict[str, Any]:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key or api_key == "mock_key":
            logger.warning("OPENROUTER_API_KEY is not configured with a valid live production key.")
            raise ValueError("OPENROUTER_API_KEY is not configured with a valid live production key.")

        selected_model = model or "openrouter/free"
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}", 
            "Content-Type": "application/json"
        }
        
        system_prompt = (
            f"You are an expert compliance auditor.\n"
            f"Company Context: {company_context or 'N/A'}\n"
            f"Department Context: {department_context or 'N/A'}\n"
            f"Evaluate the transcript strictly against the parameters and output valid JSON matching the schema."
        )
        user_content = f"Parameters: {json.dumps(parameters)}\n\nTranscript: {transcript}"

        payload = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "eval_schema",
                    "strict": True,
                    "schema": {
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
                                        "failure_offset_seconds": {"type": ["integer", "null"]},
                                        "failure_reason": {"type": ["string", "null"]}
                                    },
                                    "required": ["parameter_id", "did_follow_rule", "failure_offset_seconds", "failure_reason"],
                                    "additionalProperties": False
                                }
                            }
                        },
                        "required": ["procedure_enquired", "evaluations"],
                        "additionalProperties": False
                    }
                }
            }
        }

        logger.info(f"Initiating OpenRouter LLM compliance evaluation using model '{selected_model}' across {len(parameters)} rules.")
        start_time = time.perf_counter()
        with httpx.Client(timeout=60.0) as client:
            res = client.post(url, headers=headers, json=payload)
            res.raise_for_status()
            response_data = res.json()
            content_str = response_data["choices"][0]["message"]["content"]
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.info(f"OpenRouter LLM evaluation completed successfully in {elapsed_ms:.2f}ms")
            return json.loads(content_str)

import os
import time
import json
import io
import httpx
import mimetypes
import threading
import av
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, ValidationError
from src.app.core.logging_config import get_logger

logger = get_logger(__name__)

MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
BACKOFF_FACTOR = float(os.getenv("RETRY_BACKOFF_FACTOR", 2.0))
STT_MIN_INTERVAL = float(os.getenv("STT_MIN_INTERVAL", 1.05))

_last_stt_request_time: float = 0.0
_stt_rate_limit_lock = threading.Lock()



def _enforce_rate_limit(min_interval: float = 1.0):
    global _last_stt_request_time
    with _stt_rate_limit_lock:
        now = time.perf_counter()
        elapsed = now - _last_stt_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_stt_request_time = time.perf_counter()


def _chunk_audio_bytes(file_bytes: bytes, chunk_duration_sec: float = 29.0):
    """
    Slices audio bytes into <=29s chunks using PyAV at packet-level without decoding to raw PCM.
    Yields (filename, chunk_bytes) tuple.
    """
    input_file = io.BytesIO(file_bytes)

    with av.open(input_file) as in_container:
        if not in_container.streams.audio:
            raise ValueError("No audio stream found in input file.")

        in_stream = in_container.streams.audio[0]
        time_base = float(in_stream.time_base)
        chunk_duration_ts = int(chunk_duration_sec / time_base)

        format_name = in_container.format.name
        ext = "mp3" if "mp3" in format_name.lower() else ("wav" if "wav" in format_name.lower() else "m4a")

        current_chunk_index = 1
        chunk_start_ts = 0
        packet_count = 0

        def create_output_container():
            out_file = io.BytesIO()
            out_c = av.open(out_file, mode='w', format=format_name)
            out_st = out_c.add_stream_from_template(template=in_stream)
            return out_file, out_c, out_st

        out_file, out_container, out_stream = create_output_container()

        for packet in in_container.demux(in_stream):
            if packet.pts is not None and (packet.pts - chunk_start_ts) >= chunk_duration_ts:
                if packet_count > 0:
                    out_container.close()
                    out_file.seek(0)
                    yield f"chunk_{current_chunk_index:03d}.{ext}", out_file.read()

                    current_chunk_index += 1
                    chunk_start_ts = packet.pts
                    packet_count = 0

                    out_file, out_container, out_stream = create_output_container()

            if packet.pts is not None:
                packet.pts -= chunk_start_ts
            if packet.dts is not None:
                packet.dts -= chunk_start_ts

            packet.stream = out_stream
            out_container.mux(packet)
            packet_count += 1

        if packet_count > 0:
            out_container.close()
            out_file.seek(0)
            yield f"chunk_{current_chunk_index:03d}.{ext}", out_file.read()



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
            file_bytes = f.read()

        chunks = list(_chunk_audio_bytes(file_bytes, chunk_duration_sec=29.0))
        logger.info(f"Split {file_path} into {len(chunks)} chunk(s) using PyAV.")

        chunk_transcripts = []
        first_res_json = None

        with httpx.Client(timeout=120.0) as client:
            for idx, (filename, chunk_bytes) in enumerate(chunks, 1):
                _enforce_rate_limit(min_interval=STT_MIN_INTERVAL)

                mime_type, _ = mimetypes.guess_type(filename)
                if not mime_type:
                    mime_type = "audio/mpeg" if filename.lower().endswith(".mp3") else "audio/wav"

                files = {"file": (filename, chunk_bytes, mime_type)}
                data = {"model": "saaras:v3", "mode": "transcribe"}
                res = client.post(url, headers=headers, files=files, data=data)

                if res.status_code == 429:
                    retry_after = float(res.headers.get("Retry-After", 5.0))
                    logger.warning(f"Sarvam STT 429 Rate Limit hit on chunk {idx}/{len(chunks)}. Sleeping {retry_after}s before retrying...")
                    time.sleep(retry_after)
                    res = client.post(url, headers=headers, files=files, data=data)

                if res.status_code >= 400:
                    logger.error(f"Sarvam STT HTTP {res.status_code} Error on chunk {idx}/{len(chunks)}: {res.text}")
                res.raise_for_status()


                res_json = res.json()
                if first_res_json is None:
                    first_res_json = res_json

                chunk_text = res_json.get("transcript", "").strip()
                if chunk_text:
                    chunk_transcripts.append(chunk_text)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        combined_transcript = " ".join(chunk_transcripts)
        logger.info(f"Sarvam STT transcription for {len(chunks)} chunk(s) completed successfully in {elapsed_ms:.2f}ms")

        final_result = first_res_json.copy() if first_res_json else {}
        final_result["transcript"] = combined_transcript
        final_result["model_used"] = "saaras:v3"
        return final_result


class EvalItem(BaseModel):
    parameter_id: int
    did_follow_rule: int
    failure_reason: Optional[str] = None
    failed_line_text: Optional[str] = None


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
                    "failure_reason": {"type": ["string", "null"]},
                    "failed_line_text": {"type": ["string", "null"]}
                },
                "required": ["parameter_id", "did_follow_rule", "failure_reason", "failed_line_text"],
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
            f"Evaluate the transcript strictly against the parameters and output valid JSON matching the schema.\n"
            f"For each parameter, set did_follow_rule to 1 if the rule was followed, or 0 if violated.\n"
            f"If did_follow_rule is 0, failure_reason must explain why the rule failed, and failed_line_text must contain the exact verbatim quote or offending line from the transcript where the rule was violated.\n"
            f"If did_follow_rule is 1, set failure_reason and failed_line_text to null."
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

import os
import time
import json
import io
import re
import httpx
import mimetypes
import threading
import av
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, ValidationError
from src.app.core.logging_config import get_logger

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

logger = get_logger(__name__)


def _dict_to_genai_schema(schema_dict: Dict[str, Any]) -> Any:
    if types is None:
        raise RuntimeError("google-genai SDK is not installed.")

    raw_type = schema_dict.get("type")
    nullable = False
    type_str = ""

    if isinstance(raw_type, list):
        if "null" in raw_type:
            nullable = True
        non_null_types = [t for t in raw_type if t != "null"]
        type_str = non_null_types[0] if non_null_types else "string"
    elif isinstance(raw_type, str):
        type_str = raw_type

    if schema_dict.get("nullable"):
        nullable = True

    type_map = {
        "object": types.Type.OBJECT,
        "array": types.Type.ARRAY,
        "string": types.Type.STRING,
        "integer": types.Type.INTEGER,
        "number": types.Type.NUMBER,
        "boolean": types.Type.BOOLEAN,
    }
    genai_type = type_map.get(type_str, types.Type.STRING)

    kwargs: Dict[str, Any] = {
        "type": genai_type,
        "nullable": nullable,
    }

    if "properties" in schema_dict:
        kwargs["properties"] = {
            k: _dict_to_genai_schema(v) for k, v in schema_dict["properties"].items()
        }
    if "items" in schema_dict:
        kwargs["items"] = _dict_to_genai_schema(schema_dict["items"])
    if "required" in schema_dict:
        kwargs["required"] = schema_dict["required"]
    if "enum" in schema_dict:
        kwargs["enum"] = [str(x) for x in schema_dict["enum"]]

    return types.Schema(**kwargs)


MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
BACKOFF_FACTOR = float(os.getenv("RETRY_BACKOFF_FACTOR", 2.0))
STT_MIN_INTERVAL = float(os.getenv("STT_MIN_INTERVAL", 1.05))
GEMINI_MIN_INTERVAL = float(os.getenv("GEMINI_MIN_INTERVAL", 2.0))

_last_stt_request_time: float = 0.0
_stt_rate_limit_lock = threading.Lock()

_last_gemini_request_time: float = 0.0
_gemini_rate_limit_lock = threading.Lock()


def _enforce_rate_limit(min_interval: float = 1.0):
    global _last_stt_request_time
    with _stt_rate_limit_lock:
        now = time.perf_counter()
        elapsed = now - _last_stt_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_stt_request_time = time.perf_counter()


def _enforce_gemini_rate_limit(min_interval: float = 2.0):
    global _last_gemini_request_time
    with _gemini_rate_limit_lock:
        now = time.perf_counter()
        elapsed = now - _last_gemini_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_gemini_request_time = time.perf_counter()


def _extract_retry_delay(e: Exception, default_backoff: float) -> float:
    """
    Dynamically extracts required retry delay from API rate limit errors (e.g. 429 RESOURCE_EXHAUSTED).
    Falls back to exponential default_backoff if no explicit retry delay is present.
    """
    err_str = str(e)

    # 1. Match 'retryDelay': '54s' or 'retryDelay': '54.635...' or retryDelay="54s"
    match = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s?", err_str, re.IGNORECASE)
    if match:
        try:
            return max(float(match.group(1)) + 1.0, default_backoff)
        except (ValueError, TypeError):
            pass

    # 2. Match "retry in 54.63s" or "retry in 54s"
    match = re.search(r"retry in (\d+(?:\.\d+)?)s", err_str, re.IGNORECASE)
    if match:
        try:
            return max(float(match.group(1)) + 1.0, default_backoff)
        except (ValueError, TypeError):
            pass

    # 3. Match httpx response headers if present
    if hasattr(e, "response") and getattr(e, "response", None) is not None:
        response = getattr(e, "response")
        if hasattr(response, "headers") and response.headers:
            retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
            if retry_after:
                try:
                    return max(float(retry_after) + 1.0, default_backoff)
                except (ValueError, TypeError):
                    pass

    # 4. Generic 429 / RESOURCE_EXHAUSTED fallback
    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "rate limit" in err_str.lower():
        return max(30.0, default_backoff)

    return default_backoff


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
        max_attempts = MAX_RETRIES

        while retries < max_attempts:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                retries += 1
                default_sleep = BACKOFF_FACTOR ** retries
                err_str = str(e)

                is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "rate limit" in err_str.lower()
                if is_rate_limit:
                    max_attempts = max(MAX_RETRIES, 5)
                    sleep_time = _extract_retry_delay(e, default_sleep)
                    logger.warning(
                        f"External API Rate Limit (429) encountered ({e}). "
                        f"Attempt {retries}/{max_attempts}. Dynamic sleep for {sleep_time:.2f}s before retrying..."
                    )
                else:
                    sleep_time = default_sleep
                    logger.warning(
                        f"External API call failed ({e}). Attempt {retries}/{max_attempts}. Retrying in {sleep_time:.2f}s..."
                    )

                if retries >= max_attempts:
                    logger.error(f"External API call failed permanently after {max_attempts} attempts.")
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


class FormatRuleLLMResponse(BaseModel):
    expected_action: str
    failure_examples: List[str]


FORMAT_RULE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "expected_action": {"type": "string"},
        "failure_examples": {
            "type": "array",
            "items": {"type": "string"}
        }
    },
    "required": ["expected_action", "failure_examples"],
    "additionalProperties": False
}


class FormatContextLLMResponse(BaseModel):
    context: str


FORMAT_CONTEXT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "context": {"type": "string"}
    },
    "required": ["context"],
    "additionalProperties": False
}


class LLMService:
    @staticmethod
    def _call_llm(provider: str, api_key: Optional[str], selected_model: str, messages: list,
                  json_schema: Optional[dict] = None, schema_name: str = "eval_schema",
                  effort: Optional[str] = "high") -> str:
        schema_to_use = json_schema if json_schema is not None else EVAL_JSON_SCHEMA
        effort_val = effort or "high"

        if provider == "gemini":
            _enforce_gemini_rate_limit(min_interval=GEMINI_MIN_INTERVAL)
            key = api_key or os.getenv("GEMINI_API_KEY")
            if not key or key == "mock_key":
                logger.warning("Gemini LLM is not configured with a valid live production key.")
                raise ValueError("LLM is not configured with a valid live production key.")

            if genai is None or types is None:
                raise RuntimeError("google-genai SDK is not installed.")

            system_text = "\n".join(m["content"] for m in messages if m.get("role") == "system")
            user_text = "\n".join(m["content"] for m in messages if m.get("role") == "user")

            client = genai.Client(api_key=key)
            genai_schema = _dict_to_genai_schema(schema_to_use)

            config_kwargs: Dict[str, Any] = {
                "thinking_config": types.ThinkingConfig(thinking_level=effort_val.upper()),
                "response_mime_type": "application/json",
                "response_schema": genai_schema
            }
            if system_text:
                config_kwargs["system_instruction"] = [types.Part.from_text(text=system_text)]

            config = types.GenerateContentConfig(**config_kwargs)
            contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_text)])]

            response_stream = client.models.generate_content_stream(
                model=selected_model,
                contents=contents,
                config=config
            )

            chunks = []
            for chunk in response_stream:
                if hasattr(chunk, "text") and chunk.text:
                    chunks.append(chunk.text)
            return "".join(chunks)

        else:  # openrouter branch (default)
            key = api_key or os.getenv("OPENROUTER_API_KEY")
            if not key or key == "mock_key":
                logger.warning("OpenRouter LLM is not configured with a valid live production key.")
                raise ValueError("LLM is not configured with a valid live production key.")

            openrouter_effort_map = {
                "minimal": "low",
                "low": "low",
                "medium": "medium",
                "high": "high"
            }
            mapped_effort = openrouter_effort_map.get(effort_val.lower(), "low")

            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": selected_model,
                "messages": messages,
                "reasoning": {
                    "effort": mapped_effort,
                    "exclude": False
                },
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": schema_to_use
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
    def format_rule(raw_input: str, expected_action: Optional[str] = None,
                    failure_examples: Optional[List[str]] = None, model: Optional[str] = None,
                    provider: str = "openrouter", effort: Optional[str] = "low") -> Dict[str, Any]:
        selected_model = model or "openrouter/free"

        system_prompt = (
            "You are an expert compliance rule architect.\n"
            "Your task is to reformat raw manager input and any pre-filled fields into clear, professional compliance parameter definitions.\n"
            "Produce JSON containing exactly two keys:\n"
            "1. 'expected_action': A clear, direct statement of what the agent must do.\n"
            "2. 'failure_examples': A list of short, distinct sentences enumerating realistic ways this rule gets violated (minimum tokens, no repetition, maximum 5 items).\n"
            "The output must be generic enough to apply across any domain or department, without referencing specific call timestamps or call-specific details."
        )
        user_content = (
            f"Raw Input: {raw_input}\n"
            f"Existing Expected Action: {expected_action or 'N/A'}\n"
            f"Existing Failure Examples: {json.dumps(failure_examples) if failure_examples else 'N/A'}"
        )

        logger.info(f"Initiating LLM rule reformatting using provider '{provider}', model '{selected_model}', effort '{effort}'.")
        start_time = time.perf_counter()
        content_str = LLMService._call_llm(
            provider=provider,
            api_key=None,
            selected_model=selected_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            json_schema=FORMAT_RULE_JSON_SCHEMA,
            schema_name="format_rule_schema",
            effort=effort or "low"
        )

        try:
            parsed = json.loads(content_str)
            validated = FormatRuleLLMResponse.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"LLM rule reformatting response failed structural validation, attempting repair: {e}")
            repair_content = LLMService._call_llm(
                provider=provider,
                api_key=None,
                selected_model=selected_model,
                messages=[
                    {"role": "system", "content": "You output only valid JSON matching the required schema. Fix the structure of the JSON below. Do not change the values, only the shape."},
                    {"role": "user", "content": content_str}
                ],
                json_schema=FORMAT_RULE_JSON_SCHEMA,
                schema_name="format_rule_schema",
                effort=effort or "low"
            )
            parsed = json.loads(repair_content)
            validated = FormatRuleLLMResponse.model_validate(parsed)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(f"LLM rule reformatting completed successfully in {elapsed_ms:.2f}ms")
        return validated.model_dump()

    @staticmethod
    @retry_with_backoff
    def format_context(raw_input: str, context_type: str, model: Optional[str] = None,
                       provider: str = "openrouter", effort: Optional[str] = "low") -> Dict[str, str]:
        selected_model = model or "openrouter/free"

        if context_type == "company":
            system_prompt = (
                "You are an expert executive assistant and context architect.\n"
                "Condense and structure raw company text into the following exact sections with headers:\n"
                "[Company Overview]\n"
                "[Brand Guidelines]\n"
                "[Policies]\n"
                "Use concise, professional language with minimum tokens and no unnecessary fluff."
            )
        elif context_type == "department":
            system_prompt = (
                "You are an expert operations architect.\n"
                "Condense and structure raw department text into the following exact sections with headers:\n"
                "[Team Function]\n"
                "[Workflows]\n"
                "[Guidelines]\n"
                "Use concise, professional language with minimum tokens and no unnecessary fluff."
            )
        else:
            raise ValueError(f"Invalid context_type '{context_type}'. Must be 'company' or 'department'.")

        user_content = f"Raw Context Input: {raw_input}"

        logger.info(f"Initiating LLM context reformatting ({context_type}) using provider '{provider}', model '{selected_model}', effort '{effort}'.")
        start_time = time.perf_counter()
        content_str = LLMService._call_llm(
            provider=provider,
            api_key=None,
            selected_model=selected_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            json_schema=FORMAT_CONTEXT_JSON_SCHEMA,
            schema_name="format_context_schema",
            effort=effort or "low"
        )

        try:
            parsed = json.loads(content_str)
            validated = FormatContextLLMResponse.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"LLM context reformatting response failed structural validation, attempting repair: {e}")
            repair_content = LLMService._call_llm(
                provider=provider,
                api_key=None,
                selected_model=selected_model,
                messages=[
                    {"role": "system", "content": "You output only valid JSON matching the required schema. Fix the structure of the JSON below. Do not change the values, only the shape."},
                    {"role": "user", "content": content_str}
                ],
                json_schema=FORMAT_CONTEXT_JSON_SCHEMA,
                schema_name="format_context_schema",
                effort=effort or "low"
            )
            parsed = json.loads(repair_content)
            validated = FormatContextLLMResponse.model_validate(parsed)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(f"LLM context reformatting completed successfully in {elapsed_ms:.2f}ms")
        return validated.model_dump()

    @staticmethod
    @retry_with_backoff
    def evaluate(model: str, company_context: str, department_context: str,
                 parameters: list, transcript: str, provider: str = "openrouter",
                 effort: Optional[str] = "medium") -> Dict[str, Any]:
        selected_model = model or "openrouter/free"
        effort_val = effort or "medium"

        system_prompt = (
            f"You are an expert compliance auditor.\n"
            f"Company Context: {company_context or 'N/A'}\n"
            f"Department Context: {department_context or 'N/A'}\n"
            f"Evaluate the transcript strictly against the parameters and output valid JSON matching the schema.\n"
            f"procedure_enquired must be a short label of no more than 6 words on a single line (e.g. 'Dental Implant Consultation'), not a sentence or summary.\n"
            f"For each parameter, set did_follow_rule to 1 if the rule was followed, or 0 if violated.\n"
            f"If did_follow_rule is 0, failure_reason must explain why the rule failed, and failed_line_text must contain the exact verbatim quote or offending line from the transcript where the rule was violated.\n"
            f"If did_follow_rule is 1, set failure_reason and failed_line_text to null."
        )
        user_content = f"Parameters: {json.dumps(parameters)}\n\nTranscript: {transcript}"

        logger.info(f"Initiating LLM compliance evaluation using provider '{provider}', model '{selected_model}', effort '{effort_val}' across {len(parameters)} rules.")
        start_time = time.perf_counter()
        content_str = LLMService._call_llm(
            provider=provider,
            api_key=None,
            selected_model=selected_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            json_schema=EVAL_JSON_SCHEMA,
            schema_name="eval_schema",
            effort=effort_val
        )

        try:
            parsed = json.loads(content_str)
            validated = EvalResponse.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"LLM response failed structural validation, attempting one repair call: {e}")
            repair_content = LLMService._call_llm(
                provider=provider,
                api_key=None,
                selected_model=selected_model,
                messages=[
                    {"role": "system", "content": "You output only valid JSON matching the required schema. Fix the structure of the JSON below. Do not change the values, only the shape."},
                    {"role": "user", "content": content_str}
                ],
                json_schema=EVAL_JSON_SCHEMA,
                schema_name="eval_schema",
                effort=effort_val
            )
            parsed = json.loads(repair_content)
            validated = EvalResponse.model_validate(parsed)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(f"LLM evaluation completed successfully in {elapsed_ms:.2f}ms")
        return validated.model_dump()


import os
import time
import json
import io
import re
import httpx
import mimetypes
import threading
import av
from typing import Dict, Any, List, Optional, Tuple
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
# Default assumes gemini-3.5-flash-lite's free-tier cap of 15 requests/minute
# (60s / 15 = 4s), with a small margin. Override via env var if the org's
# model/tier changes. This value is now enforced across ALL worker processes
# (see _acquire_shared_rate_limit_slot), so it reflects the true request rate.
GEMINI_MIN_INTERVAL = float(os.getenv("GEMINI_MIN_INTERVAL", 4.5))
# Upper bound on how long a request will wait for a rate limit slot before
# being allowed through anyway (see _acquire_shared_rate_limit_slot).
# Kept below nginx's default proxy_read_timeout of 60s so the fail-open path
# still happens inside the window the client is listening on; waiting longer
# would just produce a 504 while the worker kept sleeping.
RATE_LIMIT_MAX_WAIT = float(os.getenv("RATE_LIMIT_MAX_WAIT", 45.0))

# --- OpenRouter data-privacy routing controls ---
# "deny" = route only to providers that do not store/train on user data.
# Currently defaults to "allow" so that free models stay routable: most free
# endpoints are subsidised in exchange for prompt retention/training, and "deny"
# makes ~10 of the 17 free models unroutable. Flip to "deny" via env (no deploy
# needed) when moving to paid models such as deepseek/deepseek-v4-flash, where
# the cheapest provider is already compliant and "deny" costs nothing.
OPENROUTER_DATA_COLLECTION = os.getenv("OPENROUTER_DATA_COLLECTION", "allow").lower()
if OPENROUTER_DATA_COLLECTION not in ("deny", "allow"):
    OPENROUTER_DATA_COLLECTION = "deny"
# Stricter still: only Zero Data Retention endpoints. Off by default because it
# materially shrinks the provider pool; enable once routing is confirmed stable.
OPENROUTER_REQUIRE_ZDR = os.getenv("OPENROUTER_REQUIRE_ZDR", "false").lower() in ("1", "true", "yes")

# NOTE: rate limiting state must be shared across ALL gunicorn worker processes,
# not just threads within one process, otherwise the effective request rate is
# multiplied by the worker count. State is therefore kept in SQLite
# (see `rate_limit_state` table) rather than in a module-level variable.
# A per-process lock is still used to avoid every thread in a process hammering
# the DB simultaneously for the same key.
_stt_rate_limit_lock = threading.Lock()
_gemini_rate_limit_lock = threading.Lock()


def _acquire_shared_rate_limit_slot(rate_key: str, min_interval: float) -> None:
    """
    Cross-process rate limiter backed by SQLite.

    Waits until at least `min_interval` seconds have elapsed since the last slot
    granted for `rate_key` by ANY worker process, then atomically claims a new
    slot. The wait duration is computed and slept in one go rather than polled,
    so a gated call costs ~2 DB round-trips instead of one per poll tick — this
    matters because every worker shares the single SQLite file that also serves
    all application queries.

    Best-effort by design: if a slot cannot be claimed within
    RATE_LIMIT_MAX_WAIT the call is allowed through with a warning. Briefly
    exceeding the client-side interval is preferable to pinning a gunicorn
    worker indefinitely, and the provider's own 429 plus `retry_with_backoff`
    remain as the backstop.
    """
    if min_interval <= 0:
        return

    from src.app.models.base import DatabaseManager

    deadline = time.monotonic() + RATE_LIMIT_MAX_WAIT

    while True:
        now = time.time()
        with DatabaseManager.get_connection() as conn:
            row = conn.execute(
                "SELECT last_request_at FROM rate_limit_state WHERE rate_key = ?;",
                (rate_key,)
            ).fetchone()

            wait_for = 0.0
            if row is not None:
                # Clamped to min_interval so a clock jump backwards (NTP) or a
                # bad stored value cannot make us sleep for an unbounded time.
                wait_for = min(max(min_interval - (now - row["last_request_at"]), 0.0), min_interval)

            if wait_for <= 0.0:
                # The WHERE clause is the authoritative atomic guard: if another
                # worker claimed the slot between our SELECT and this INSERT,
                # rowcount is 0 and we loop to recompute the wait.
                cursor = conn.execute(
                    """
                    INSERT INTO rate_limit_state (rate_key, last_request_at)
                    VALUES (?, ?)
                    ON CONFLICT(rate_key) DO UPDATE SET last_request_at = excluded.last_request_at
                    WHERE excluded.last_request_at - rate_limit_state.last_request_at >= ?
                    """,
                    (rate_key, now, min_interval)
                )
                if cursor.rowcount > 0:
                    return
                # Lost the race; brief pause before recomputing against the
                # winner's freshly written timestamp.
                wait_for = 0.05

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                f"Rate limit slot for '{rate_key}' not acquired within {RATE_LIMIT_MAX_WAIT:.0f}s; "
                f"proceeding without it to avoid blocking the worker."
            )
            return

        time.sleep(min(wait_for, remaining))


def _enforce_rate_limit(min_interval: float = 1.0):
    with _stt_rate_limit_lock:
        _acquire_shared_rate_limit_slot("stt_sarvam", min_interval)


def _enforce_gemini_rate_limit(min_interval: float = 2.0):
    with _gemini_rate_limit_lock:
        _acquire_shared_rate_limit_slot("llm_gemini", min_interval)


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
    Yields (filename, chunk_bytes, start_time_sec, end_time_sec) tuple.
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
        current_chunk_start_sec = 0.0
        packet_count = 0
        last_packet_pts = 0

        def create_output_container():
            out_file = io.BytesIO()
            out_c = av.open(out_file, mode='w', format=format_name)
            out_st = out_c.add_stream_from_template(template=in_stream)
            return out_file, out_c, out_st

        out_file, out_container, out_stream = create_output_container()

        for packet in in_container.demux(in_stream):
            if packet.pts is not None:
                last_packet_pts = packet.pts
            if packet.pts is not None and (packet.pts - chunk_start_ts) >= chunk_duration_ts:
                if packet_count > 0:
                    out_container.close()
                    out_file.seek(0)
                    chunk_end_sec = round(packet.pts * time_base, 2)
                    yield f"chunk_{current_chunk_index:03d}.{ext}", out_file.read(), current_chunk_start_sec, chunk_end_sec

                    current_chunk_index += 1
                    chunk_start_ts = packet.pts
                    current_chunk_start_sec = chunk_end_sec
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
            chunk_end_sec = round((chunk_start_ts + (last_packet_pts - chunk_start_ts if last_packet_pts > chunk_start_ts else 0)) * time_base, 2)
            if in_container.duration is not None:
                container_dur = round(float(in_container.duration) / 1_000_000.0, 2)
                if container_dur > 0 and chunk_end_sec > container_dur:
                    chunk_end_sec = container_dur
            if chunk_end_sec <= current_chunk_start_sec:
                chunk_end_sec = round(current_chunk_start_sec + chunk_duration_sec, 2)
            yield f"chunk_{current_chunk_index:03d}.{ext}", out_file.read(), current_chunk_start_sec, chunk_end_sec


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
    def transcribe(file_path: str, duration_seconds: Optional[float] = None) -> Dict[str, Any]:
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
        transcript_chunks = []
        first_res_json = None

        with httpx.Client(timeout=120.0) as client:
            for idx, chunk_item in enumerate(chunks, 1):
                if len(chunk_item) >= 4:
                    filename, chunk_bytes, start_sec, end_sec = chunk_item[0], chunk_item[1], chunk_item[2], chunk_item[3]
                else:
                    filename, chunk_bytes = chunk_item[0], chunk_item[1]
                    start_sec = round((idx - 1) * 29.0, 2)
                    end_sec = round(idx * 29.0, 2)

                if duration_seconds and duration_seconds > 0 and end_sec > duration_seconds:
                    end_sec = round(duration_seconds, 2)

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
                    transcript_chunks.append({
                        "index": idx,
                        "start_time": start_sec,
                        "end_time": end_sec,
                        "text": chunk_text
                    })
                else:
                    transcript_chunks.append({
                        "index": idx,
                        "start_time": start_sec,
                        "end_time": end_sec,
                        "text": ""
                    })

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        combined_transcript = " ".join(chunk_transcripts)
        logger.info(f"Sarvam STT transcription for {len(chunks)} chunk(s) completed successfully in {elapsed_ms:.2f}ms")

        final_result = first_res_json.copy() if first_res_json else {}
        final_result["transcript"] = combined_transcript
        final_result["transcript_chunks"] = transcript_chunks
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
                  effort: Optional[str] = "high") -> Tuple[str, Dict[str, Any]]:
        """
        Returns (content_str, usage_info) where usage_info has keys
        'prompt_tokens', 'completion_tokens', and 'model_used'.
        """
        schema_to_use = json_schema if json_schema is not None else EVAL_JSON_SCHEMA
        effort_val = effort or "high"

        if provider == "gemini":
            # DATA PRIVACY (Gemini): there is deliberately no request-level
            # parameter to opt out of training here — Google does not offer one.
            # Whether prompts/responses are used to improve Google's products is
            # determined solely by the project's BILLING TIER:
            #   * Unpaid/free quota -> Google uses submitted content and
            #     responses for product improvement, and human reviewers may
            #     read them. Google's terms state: "Do not submit sensitive,
            #     confidential, or personal information to the Unpaid Services."
            #   * Paid tier (Cloud project with an active billing account) ->
            #     "Google doesn't use your prompts ... or responses to improve
            #     our products."
            # Call transcripts processed here contain patient-identifying
            # information, so this project MUST be on the paid tier.
            # Ref: https://ai.google.dev/gemini-api/terms
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
            usage_info: Dict[str, Any] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "model_used": selected_model
            }
            for chunk in response_stream:
                if hasattr(chunk, "text") and chunk.text:
                    chunks.append(chunk.text)
                # usage_metadata typically arrives with cumulative totals on
                # each chunk (finalized on the last one); keep overwriting so
                # we end up with the final totals for the whole response.
                chunk_usage = getattr(chunk, "usage_metadata", None)
                if chunk_usage is not None:
                    prompt_count = getattr(chunk_usage, "prompt_token_count", None)
                    if prompt_count is not None:
                        usage_info["prompt_tokens"] = prompt_count
                    # Gemini reports reasoning tokens separately in
                    # thoughts_token_count, NOT inside candidates_token_count.
                    # Per Google's docs: "When thinking is turned on, response
                    # pricing is the sum of output tokens and thinking tokens."
                    # This call always runs with thinking enabled (see
                    # thinking_config above), so both are summed to get true
                    # billable output usage. Contrast with the OpenRouter branch
                    # below, where reasoning is ALREADY inside completion_tokens
                    # and must not be added again.
                    completion_count = getattr(chunk_usage, "candidates_token_count", None) or 0
                    thoughts_count = getattr(chunk_usage, "thoughts_token_count", None) or 0
                    if completion_count or thoughts_count:
                        usage_info["completion_tokens"] = completion_count + thoughts_count
            return "".join(chunks), usage_info

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
            # DATA PRIVACY (OpenRouter): unlike Gemini, OpenRouter DOES expose
            # per-request routing controls. `data_collection: "deny"` restricts
            # routing to providers that do not store user data non-transiently
            # or train on it; `zdr: true` further restricts to Zero Data
            # Retention endpoints. Both narrow the pool of eligible providers,
            # so they are env-configurable and can be relaxed without a deploy
            # if a model becomes unroutable.
            # Ref: https://openrouter.ai/docs/features/provider-routing
            provider_prefs: Dict[str, Any] = {
                "data_collection": OPENROUTER_DATA_COLLECTION
            }
            if OPENROUTER_REQUIRE_ZDR:
                provider_prefs["zdr"] = True

            payload = {
                "model": selected_model,
                "messages": messages,
                "provider": provider_prefs,
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
                usage = response_data.get("usage") or {}
                usage_info = {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "model_used": response_data.get("model", selected_model)
                }
                return response_data["choices"][0]["message"]["content"], usage_info

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
        content_str, _usage_info = LLMService._call_llm(
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
            repair_content, _usage_info = LLMService._call_llm(
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
        content_str, _usage_info = LLMService._call_llm(
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
            repair_content, _usage_info = LLMService._call_llm(
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
        content_str, usage_info = LLMService._call_llm(
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
        total_prompt_tokens = usage_info.get("prompt_tokens", 0)
        total_completion_tokens = usage_info.get("completion_tokens", 0)
        model_used = usage_info.get("model_used", selected_model)

        try:
            parsed = json.loads(content_str)
            validated = EvalResponse.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"LLM response failed structural validation, attempting one repair call: {e}")
            repair_content, repair_usage_info = LLMService._call_llm(
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
            # Repair call is a genuine second billable request; accumulate its
            # tokens on top of the initial call's tokens for accurate tracking.
            total_prompt_tokens += repair_usage_info.get("prompt_tokens", 0)
            total_completion_tokens += repair_usage_info.get("completion_tokens", 0)
            model_used = repair_usage_info.get("model_used", model_used)
            parsed = json.loads(repair_content)
            validated = EvalResponse.model_validate(parsed)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            f"LLM evaluation completed successfully in {elapsed_ms:.2f}ms "
            f"(prompt_tokens={total_prompt_tokens}, completion_tokens={total_completion_tokens})"
        )
        result = validated.model_dump()
        result["prompt_tokens"] = total_prompt_tokens
        result["completion_tokens"] = total_completion_tokens
        result["model_used"] = model_used
        return result


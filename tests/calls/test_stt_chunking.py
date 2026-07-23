import os
import time
import io
import pytest
import av
from unittest.mock import MagicMock, patch
from src.app.services.stt import STTService, _chunk_audio_bytes, _enforce_rate_limit

_REAL_TRANSCRIBE = STTService.__dict__["transcribe"].__get__(None, STTService)


def _generate_synthetic_mp3_bytes(duration_sec: float = 10.0) -> bytes:
    """Generates synthetic MP3 audio bytes using PyAV for testing."""
    out_file = io.BytesIO()
    container = av.open(out_file, mode="w", format="mp3")
    stream = container.add_stream("mp3", rate=44100)

    num_frames = int((44100 * duration_sec) / 1152)
    for _ in range(num_frames):
        frame = av.AudioFrame(format="s16p", layout="mono", samples=1152)
        frame.rate = 44100
        for plane in frame.planes:
            plane.update(b"\x00" * 2304)
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    out_file.seek(0)
    return out_file.read()


def test_chunk_audio_bytes_short():
    """Audio < 29s should produce 1 chunk."""
    mp3_bytes = _generate_synthetic_mp3_bytes(10.0)
    chunks = list(_chunk_audio_bytes(mp3_bytes, chunk_duration_sec=29.0))
    assert len(chunks) == 1
    assert chunks[0][0] == "chunk_001.mp3"
    assert len(chunks[0][1]) > 0


def test_chunk_audio_bytes_long():
    """Audio > 30s (e.g. 45s) should produce 2 chunks."""
    mp3_bytes = _generate_synthetic_mp3_bytes(45.0)
    chunks = list(_chunk_audio_bytes(mp3_bytes, chunk_duration_sec=29.0))
    assert len(chunks) == 2
    assert chunks[0][0] == "chunk_001.mp3"
    assert chunks[1][0] == "chunk_002.mp3"


def test_stt_transcribe_multi_chunk_concatenation(tmp_path, monkeypatch):
    """Transcribe multi-chunk audio (>30s) and verify Sarvam API is called per chunk and concatenated in order."""
    monkeypatch.setattr(STTService, "transcribe", _REAL_TRANSCRIBE)
    monkeypatch.setenv("SARVAM_API_KEY", "test_valid_key_123")

    audio_file = tmp_path / "long_audio.mp3"
    audio_file.write_bytes(_generate_synthetic_mp3_bytes(45.0))

    mock_responses = [
        MagicMock(status_code=200, json=lambda: {"transcript": "Hello doctor.", "language_code": "en-IN"}),
        MagicMock(status_code=200, json=lambda: {"transcript": "My arm is hurting.", "language_code": "en-IN"})
    ]

    with patch("httpx.Client.post", side_effect=mock_responses) as mock_post:
        result = STTService.transcribe(str(audio_file))

        assert mock_post.call_count == 2
        assert result["transcript"] == "Hello doctor. My arm is hurting."
        assert result["language_code"] == "en-IN"
        assert result["model_used"] == "saaras:v3"


def test_stt_transcribe_single_chunk(tmp_path, monkeypatch):
    """Transcribe single-chunk audio (<29s) and verify Sarvam API is called once."""
    monkeypatch.setattr(STTService, "transcribe", _REAL_TRANSCRIBE)
    monkeypatch.setenv("SARVAM_API_KEY", "test_valid_key_123")

    audio_file = tmp_path / "short_audio.mp3"
    audio_file.write_bytes(_generate_synthetic_mp3_bytes(10.0))

    mock_response = MagicMock(status_code=200, json=lambda: {"transcript": "Single chunk test.", "language_code": "en-IN"})

    with patch("httpx.Client.post", return_value=mock_response) as mock_post:
        result = STTService.transcribe(str(audio_file))

        assert mock_post.call_count == 1
        assert result["transcript"] == "Single chunk test."
        assert result["model_used"] == "saaras:v3"


def test_stt_transcribe_429_retry_handling(tmp_path, monkeypatch):
    """Verify HTTP 429 response triggers Retry-After pause and retry."""
    monkeypatch.setattr(STTService, "transcribe", _REAL_TRANSCRIBE)
    monkeypatch.setenv("SARVAM_API_KEY", "test_valid_key_123")
    monkeypatch.setenv("STT_MIN_INTERVAL", "0.01")

    audio_file = tmp_path / "short_audio.mp3"
    audio_file.write_bytes(_generate_synthetic_mp3_bytes(10.0))

    res_429 = MagicMock(status_code=429, headers={"Retry-After": "0.1"})
    res_200 = MagicMock(status_code=200, json=lambda: {"transcript": "Recovered after 429.", "language_code": "en-IN"})

    with patch("httpx.Client.post", side_effect=[res_429, res_200]) as mock_post:
        result = STTService.transcribe(str(audio_file))

        assert mock_post.call_count == 2
        assert result["transcript"] == "Recovered after 429."


def test_rate_limiter_spacing():
    """Verify rate limiter enforces min_interval spacing between consecutive requests."""
    t0 = time.perf_counter()
    _enforce_rate_limit(min_interval=0.2)
    t1 = time.perf_counter()
    _enforce_rate_limit(min_interval=0.2)
    t2 = time.perf_counter()

    assert (t2 - t1) >= 0.18

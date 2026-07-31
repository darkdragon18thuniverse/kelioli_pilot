import pytest
from unittest.mock import MagicMock, patch
from src.app.services.stt import _extract_retry_delay, retry_with_backoff


def test_extract_retry_delay_from_gemini_json_error():
    err_msg = (
        "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
        "generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 5, model: gemini-3.6-flash. "
        "Please retry in 54.635742198s."
    )
    exc = Exception(err_msg)
    delay = _extract_retry_delay(exc, default_backoff=2.0)
    assert delay >= 55.0  # 54.635 + 1.0 buffer


def test_extract_retry_delay_from_retry_delay_field():
    err_msg = "{'error': {'code': 429, 'details': [{'retryDelay': '45s'}]}}"
    exc = Exception(err_msg)
    delay = _extract_retry_delay(exc, default_backoff=2.0)
    assert delay == 46.0  # 45 + 1.0 buffer


def test_extract_retry_delay_fallback_generic_429():
    exc = Exception("429 Too Many Requests")
    delay = _extract_retry_delay(exc, default_backoff=2.0)
    assert delay == 30.0


def test_extract_retry_delay_non_429_returns_default():
    exc = Exception("500 Internal Server Error")
    delay = _extract_retry_delay(exc, default_backoff=4.0)
    assert delay == 4.0


def test_retry_with_backoff_handles_429_with_dynamic_sleep():
    mock_func = MagicMock()
    # First 2 calls fail with 429, 3rd call succeeds
    mock_func.side_effect = [
        Exception("429 RESOURCE_EXHAUSTED. Please retry in 1s."),
        Exception("429 RESOURCE_EXHAUSTED. Please retry in 1s."),
        "SUCCESS"
    ]

    with patch("time.sleep") as mock_sleep:
        decorated = retry_with_backoff(mock_func)
        result = decorated()

        assert result == "SUCCESS"
        assert mock_func.call_count == 3
        assert mock_sleep.call_count == 2
        # Check sleep time extracted dynamic delay (1s + 1s buffer = 2s)
        for call_args in mock_sleep.call_args_list:
            assert call_args[0][0] >= 2.0

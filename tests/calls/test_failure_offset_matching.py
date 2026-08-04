import pytest
from src.app.controllers.calls_controller import CallsController


def test_match_failed_line_offset_exact():
    chunks = [
        {"index": 1, "start_time": 0.0, "end_time": 29.0, "text": "Hello, thank you for calling dental care."},
        {"index": 2, "start_time": 29.0, "end_time": 58.0, "text": "We charge 500 dollars for root canal treatment."},
        {"index": 3, "start_time": 58.0, "end_time": 87.0, "text": "We do not accept insurance for this procedure."}
    ]

    offset = CallsController._match_failed_line_offset(
        "We charge 500 dollars for root canal treatment.",
        chunks
    )
    assert offset == 29

    offset_ins = CallsController._match_failed_line_offset(
        "We do not accept insurance for this procedure.",
        chunks
    )
    assert offset_ins == 58


def test_match_failed_line_offset_normalized():
    chunks = [
        {"index": 1, "start_time": 0.0, "end_time": 29.0, "text": "Hello, thank you for calling dental care."},
        {"index": 2, "start_time": 29.0, "end_time": 58.0, "text": "We charge $500 for root-canal treatment!!"}
    ]

    offset = CallsController._match_failed_line_offset(
        "We charge 500 for root canal treatment",
        chunks
    )
    assert offset == 29


def test_match_failed_line_offset_boundary_split_token_overlap():
    chunks = [
        {"index": 1, "start_time": 0.0, "end_time": 29.0, "text": "Hello, thank you for calling. We charge 500 dollars for initial inspection."},
        {"index": 2, "start_time": 29.0, "end_time": 58.0, "text": "And root canal procedure costs extra."}
    ]

    # Quote spanning boundary: "dollars for initial inspection and root canal procedure"
    offset = CallsController._match_failed_line_offset(
        "dollars for initial inspection and root canal procedure costs extra",
        chunks
    )
    assert offset in (0, 29)


def test_match_failed_line_offset_no_match():
    chunks = [
        {"index": 1, "start_time": 0.0, "end_time": 29.0, "text": "Hello, thank you for calling dental care."}
    ]

    offset = CallsController._match_failed_line_offset(
        "Unrelated text that does not appear in audio",
        chunks
    )
    assert offset is None


def test_match_failed_line_offset_empty_inputs():
    assert CallsController._match_failed_line_offset(None, []) is None
    assert CallsController._match_failed_line_offset("", [{"index": 1, "start_time": 0.0, "end_time": 29.0, "text": "Hello"}]) is None

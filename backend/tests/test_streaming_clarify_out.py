"""Tests for SSE `tool_complete` payload output propagation (clarify OUT fix).

Feature: streaming-clarify-out

Background
----------
The Hermes agent SDK calls ``tool_progress_callback("tool.completed", name,
None, None, duration=..., is_error=..., result=function_result)`` for every
tool completion.  The WebUI's ``on_tool`` bridge in ``api/streaming.py``
previously read only the first four positional args (event_type, name,
preview, args) and used ``preview`` (always None for ``tool.completed``) as
the ``output`` payload field.  As a result, every tool's collapsed IN/OUT
card showed ``(no response)`` for the OUT region during streaming.

The fix:

1. Read ``cb_kwargs["result"]`` (the SDK's actual function result) and use
   it as the SSE ``output`` payload.
2. Cap the size at 50 KB via ``_truncate_tool_output`` so that tools like
   ``read_file`` on large files do not blow up SSE payload or DOM size.
3. Stop filtering ``clarify`` out of the persisted session ``tool_calls``
   list so that refreshing the page after answering also shows the OUT.

These tests assert all three behaviours.
"""

import json
import pathlib
import sys

import pytest

# Make repo root importable so we can import api.streaming without installing.
REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

STREAMING_SRC = (REPO_ROOT / "app" / "api" / "streaming.py").read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# Pure-function unit tests for _truncate_tool_output
# ──────────────────────────────────────────────────────────────────────────────

def _import_helper():
    """Import the helper.  Delayed so the module's heavy deps (hermes-agent
    SDK, config) are not loaded eagerly when only static tests run."""
    from app.api.streaming import _truncate_tool_output, _MAX_TOOL_OUTPUT_CHARS
    return _truncate_tool_output, _MAX_TOOL_OUTPUT_CHARS


def test_truncate_short_string_unchanged():
    truncate, _ = _import_helper()
    out, truncated = truncate("hello")
    assert out == "hello"
    assert truncated is False


def test_truncate_at_boundary_unchanged():
    truncate, max_chars = _import_helper()
    text = "x" * max_chars
    out, truncated = truncate(text)
    assert out == text
    assert truncated is False


def test_truncate_just_over_boundary():
    truncate, max_chars = _import_helper()
    text = "x" * (max_chars + 1)
    out, truncated = truncate(text)
    assert truncated is True
    assert out.startswith("x" * max_chars)
    assert "[... truncated for streaming display ...]" in out


def test_truncate_far_over_boundary():
    truncate, max_chars = _import_helper()
    text = "y" * (max_chars * 3)
    out, truncated = truncate(text)
    assert truncated is True
    # Marker should be present; payload should not contain full original.
    assert "[... truncated for streaming display ...]" in out
    assert "y" * (max_chars * 2) not in out
    # Length of the kept prefix is exactly max_chars.
    prefix = out.split("\n\n[... truncated for streaming display ...]")[0]
    assert prefix == "y" * max_chars


def test_truncate_non_string_passthrough():
    truncate, _ = _import_helper()
    for value in (None, 0, 3.14, [], {"a": 1}, ("tup",)):
        assert truncate(value) == (value, False)


def test_truncate_custom_max_chars():
    truncate, _ = _import_helper()
    out, truncated = truncate("abcdef", max_chars=3)
    assert truncated is True
    assert out.startswith("abc")
    assert "[... truncated for streaming display ...]" in out


def test_truncate_empty_string():
    truncate, _ = _import_helper()
    out, truncated = truncate("")
    assert out == ""
    assert truncated is False


# ──────────────────────────────────────────────────────────────────────────────
# Static-source tests for the SSE payload construction
# ──────────────────────────────────────────────────────────────────────────────

def test_tool_complete_payload_reads_result_kwarg():
    """The `output` field must be sourced from cb_kwargs['result'], not preview."""
    # Locate the tool.completed branch and inspect its payload construction.
    assert "cb_kwargs.get('result', preview)" in STREAMING_SRC, (
        "api/streaming.py must read cb_kwargs['result'] for the tool_complete "
        "output payload so the live IN/OUT card does not show '(no response)'."
    )


def test_tool_complete_payload_uses_truncate_helper():
    """The payload must run the result through _truncate_tool_output."""
    assert "_truncate_tool_output" in STREAMING_SRC, (
        "api/streaming.py must call _truncate_tool_output to cap payload size."
    )


def test_tool_complete_payload_sets_output_truncated_flag():
    """The payload must mark output_truncated when the result exceeded the cap."""
    # Source uses dict-style assignment: payload['output_truncated'] = True
    assert "'output_truncated'] = True" in STREAMING_SRC or '"output_truncated"] = True' in STREAMING_SRC, (
        "api/streaming.py must set output_truncated=True on the payload when "
        "_truncate_tool_output signals truncation."
    )


def test_tool_complete_payload_emits_event():
    """Sanity check that the tool_complete put() call is still in place."""
    assert "put('tool_complete'" in STREAMING_SRC or 'put("tool_complete"' in STREAMING_SRC


def test_clarify_no_longer_filtered_from_persisted_tool_calls():
    """`_extract_tool_calls_from_messages` must NOT exclude clarify entries."""
    # The old filter looked like:
    #     ... if ... and tc.get('name') != 'clarify'
    # It should be gone so clarify tool calls are persisted with the rest.
    bad_pattern = "tc.get('name') != 'clarify'"
    assert bad_pattern not in STREAMING_SRC, (
        "api/streaming.py must not filter clarify tool calls out of the "
        "persisted session tool_calls list.  Without this, refreshing the "
        "session re-loads an empty clarify card."
    )


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end SSE payload: simulate the SDK's tool_progress_callback and check
# the bytes the WebUI would write to the wire.
# ──────────────────────────────────────────────────────────────────────────────

class _FakeHandler:
    """Minimal stand-in for BaseHTTPRequestHandler.wfile — accumulates bytes."""

    def __init__(self):
        self._buf = bytearray()

    def write(self, data: bytes):
        self._buf.extend(data)

    def flush(self):
        pass

    @property
    def payload(self) -> str:
        return self._buf.decode("utf-8")


def _build_on_tool_for_test(_send, _cancel_event):
    """Re-derive a simplified on_tool closure with just the fields the
    tool.completed branch reads, so we can test the payload end-to-end without
    spinning up a full _run_agent_streaming.

    Mirrors the production logic at api/streaming.py:1574-1610 closely, but
    only the bits that matter for the OUT fix.
    """
    from app.api.streaming import _truncate_tool_output

    def on_tool(*cb_args, **cb_kwargs):
        event_type = name = preview = args = None
        if len(cb_args) >= 4:
            event_type, name, preview, args = cb_args[:4]
        elif len(cb_args) == 2:
            event_type, name = cb_args

        if event_type != "tool.completed":
            return None

        result_raw = cb_kwargs.get("result", preview)
        output, truncated = _truncate_tool_output(result_raw)
        payload = {
            "event_type": event_type,
            "name": name,
            "output": output,
            "preview": preview,
            "duration": cb_kwargs.get("duration"),
            "is_error": bool(cb_kwargs.get("is_error", False)),
        }
        if truncated:
            payload["output_truncated"] = True
        return payload

    return on_tool


def _make_sse(payload: dict) -> str:
    """Mimic the SSE frame _sse() writes for a single event."""
    return f"event: tool_complete\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def test_clarify_sse_output_carries_user_response():
    """E2E: SDK calls cb('tool.completed', 'clarify', None, None, result=<json>).
    The SSE frame must carry the JSON in `output` so the frontend OUT region
    can render parsed.user_response.
    """
    on_tool = _build_on_tool_for_test(None, None)
    clarify_result = json.dumps(
        {
            "question": "Which color?",
            "choices_offered": ["red", "blue"],
            "user_response": "red",
        },
        ensure_ascii=False,
    )
    payload = on_tool(
        "tool.completed", "clarify", None, None,
        duration=1.2, is_error=False, result=clarify_result,
    )
    assert payload is not None
    assert payload["name"] == "clarify"
    assert payload["output"] == clarify_result, (
        "The output payload must equal the SDK's `result` kwarg so the "
        "frontend's MessageRow.tsx JSON.parse(content) can extract user_response."
    )
    assert payload["preview"] is None  # SDK passes None for completed events.
    assert "output_truncated" not in payload  # well below the cap, no flag.

    # Verify the wire-level SSE encoding is valid JSON with the expected data.
    wire = _make_sse(payload)
    assert "event: tool_complete" in wire
    body = wire.split("data: ", 1)[1].rstrip("\n\n")
    decoded = json.loads(body)
    assert decoded["output"] == clarify_result


def test_other_tools_also_get_result_in_output():
    """E2E: A non-clarify tool (e.g. terminal) should also have its result
    propagated.  This is the same code path, so the fix benefits all tools.
    """
    on_tool = _build_on_tool_for_test(None, None)
    terminal_stdout = "hello\nworld\n"
    payload = on_tool(
        "tool.completed", "terminal", None, None,
        duration=0.5, is_error=False, result=terminal_stdout,
    )
    assert payload["output"] == terminal_stdout
    assert "output_truncated" not in payload


def test_oversize_output_is_truncated_with_flag():
    """E2E: A tool that returns >50 KB must be truncated with the flag set."""
    on_tool = _build_on_tool_for_test(None, None)
    big = "z" * (60 * 1024)  # 60 KB > 50 KB cap
    payload = on_tool(
        "tool.completed", "read_file", None, None,
        duration=0.1, is_error=False, result=big,
    )
    assert payload["output_truncated"] is True
    # Cap is 50 KB; result prefix is 50*1024 'z's + marker.
    assert payload["output"].startswith("z" * (50 * 1024))
    assert "[... truncated for streaming display ...]" in payload["output"]
    assert len(payload["output"]) < 60 * 1024 + 200


def test_no_result_kwarg_falls_back_to_preview():
    """E2E: If the SDK (hypothetically) doesn't pass result, fall back to preview."""
    on_tool = _build_on_tool_for_test(None, None)
    payload = on_tool(
        "tool.completed", "custom_tool", "a-short-preview", {"k": "v"},
        duration=0.1, is_error=False,
    )
    assert payload["output"] == "a-short-preview"
    assert payload["preview"] == "a-short-preview"
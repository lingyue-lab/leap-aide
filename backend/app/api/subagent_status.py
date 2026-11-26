"""Failure-status detection for delegated subagent completion events.

The Hermes SDK decides a child's completion ``status`` from whether the
summary is non-empty, so a child that died on an exhausted API-retry loop
returns ``status="completed"`` with the error text *as* the summary (e.g.
``API call failed after 3 retries: ...``). The WebUI cannot change that
upstream (the SDK is an external dependency), so it re-derives the status
from the summary text at the event boundary instead of trusting the label.

Pure functions with no I/O — unit-testable without a server.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Statuses that already mean failure. The SDK emits these from its own
# exception/timeout paths; they are passed through unchanged.
FAILURE_STATUSES = frozenset({"error", "failed", "failure", "timeout"})

# Statuses that look like success and therefore need a summary sniff.
_SUCCESS_STATUSES = frozenset({"completed", "complete", "success", "ok"})

# Statuses on a delegate_task payload that mean the dispatch itself failed
# (async pool at capacity, crash, ...).
_DELEGATE_FATAL_STATUSES = frozenset(
    {"error", "failed", "failure", "timeout", "rejected"}
)

# Terminal-failure prefixes the SDK builds as the child's final_response /
# entry summary. Anchored: these are leading labels, not free text, so a
# child that merely *quotes* the string mid-report is not misread.
_FAILURE_SUMMARY_RE = re.compile(
    r"^(?:"
    r"API call failed after \d+ retries"
    r"|All API retries exhausted"
    r"|Billing or credits exhausted"
    r"|Provider reported usage/credit exhaustion"
    r")",
    re.IGNORECASE,
)

# Unanchored twin used only as a fallback when the delegate result is not
# parseable JSON — the retry-exhausted wording is distinctive enough.
_FAILURE_SUMMARY_LOOSE_RE = re.compile(
    r"API call failed after \d+ retries", re.IGNORECASE
)


def is_failure_summary(summary: Any) -> bool:
    """True when *summary* is (or starts with) a known terminal-failure label."""
    if not summary:
        return False
    return bool(_FAILURE_SUMMARY_RE.match(str(summary).strip()))


def effective_subagent_status(status: Any, summary: Any) -> str:
    """Status the WebUI should publish for a subagent completion.

    Keeps genuine failure statuses, but downgrades a success-looking status
    whose summary is actually an error message — the SDK labels exhausted
    API retries as ``completed`` because the error text counts as a
    non-empty summary.
    """
    st = str(status or "").strip().lower() or "completed"
    if st in FAILURE_STATUSES:
        return st
    if st in _SUCCESS_STATUSES and is_failure_summary(summary):
        return "error"
    return st


def _entry_is_error(entry: Any) -> bool:
    """True when one result entry of a delegate_task payload failed."""
    if not isinstance(entry, dict):
        return False
    st = str(entry.get("status") or "").strip().lower()
    if st in _DELEGATE_FATAL_STATUSES:
        return True
    if entry.get("error"):
        return True
    return is_failure_summary(entry.get("summary"))


def _payload_is_error(data: Any) -> bool:
    """True when a decoded delegate_task payload reports a failed child."""
    if isinstance(data, list):
        return any(_entry_is_error(item) for item in data)
    if not isinstance(data, dict):
        return False
    st = str(data.get("status") or "").strip().lower()
    if st in _DELEGATE_FATAL_STATUSES:
        return True
    if data.get("error"):
        return True
    results = data.get("results")
    if isinstance(results, list):
        return any(_entry_is_error(item) for item in results)
    # A bare single-entry payload ({task_index, status, summary, ...}).
    if "summary" in data or "task_index" in data:
        return _entry_is_error(data)
    return False


def delegate_result_is_error(raw: Any) -> bool:
    """True when a ``delegate_task`` tool result reports a failed child.

    ``raw`` is the tool result as passed to ``tool.completed`` — normally a
    JSON string, but already-decoded containers are accepted too. Dispatch
    acks (``status="dispatched"``) are not failures; only post-run results
    and hard dispatch rejections are.
    """
    data = raw
    if isinstance(raw, (str, bytes)):
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            # Unstructured fallback: the retry-exhausted wording is unique
            # enough to flag without risking ordinary prose matches.
            return bool(_FAILURE_SUMMARY_LOOSE_RE.search(text))
    return _payload_is_error(data)

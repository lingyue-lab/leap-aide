"""Unit tests for app.api.subagent_status — pure functions, no server needed.

Covers the two WebUI-side hardenings for the SDK's optimistic subagent
status labeling:
  1. effective_subagent_status downgrades "completed" when the summary is
     an API-retry exhaustion message.
  2. delegate_result_is_error sniffs delegate_task result payloads.
"""
import json

from app.api.subagent_status import (
    delegate_result_is_error,
    effective_subagent_status,
    fix_completion_status,
    is_failure_summary,
)

RETRY_SUMMARY = (
    "API call failed after 3 retries: Non-streaming API call timed out "
    "after 90s with no response (threshold: 90s)"
)


class TestIsFailureSummary:
    def test_retry_exhaustion(self):
        assert is_failure_summary(RETRY_SUMMARY)

    def test_retry_exhaustion_is_case_insensitive(self):
        assert is_failure_summary("api call failed after 1 retries: boom")

    def test_billing_exhausted(self):
        assert is_failure_summary("Billing or credits exhausted: HTTP 402")

    def test_unverified_credit_exhaustion(self):
        assert is_failure_summary(
            "Provider reported usage/credit exhaustion (unverified): 400"
        )

    def test_all_retries_exhausted(self):
        assert is_failure_summary("All API retries exhausted with no response")

    def test_normal_summary_is_not_failure(self):
        assert not is_failure_summary(
            "- Spec compliance: 4/4\n- Ready to merge: Yes"
        )

    def test_failure_text_quoted_mid_report_is_not_failure(self):
        # Anchored matching: a report that quotes the error later must pass.
        assert not is_failure_summary(
            "Review findings. Log line said 'API call failed after 3 retries' earlier."
        )

    def test_empty(self):
        assert not is_failure_summary("")
        assert not is_failure_summary(None)


class TestEffectiveSubagentStatus:
    def test_completed_with_error_summary_downgrades(self):
        assert effective_subagent_status("completed", RETRY_SUMMARY) == "error"

    def test_completed_with_normal_summary_stays(self):
        assert effective_subagent_status("completed", "All done, tests green") == "completed"

    def test_failure_status_passthrough(self):
        for st in ("error", "failed", "failure", "timeout"):
            assert effective_subagent_status(st, RETRY_SUMMARY) == st
            assert effective_subagent_status(st, None) == st

    def test_interrupted_passthrough(self):
        assert effective_subagent_status("interrupted", None) == "interrupted"

    def test_missing_status_defaults_to_completed(self):
        assert effective_subagent_status(None, "ok summary") == "completed"
        assert effective_subagent_status("", "ok summary") == "completed"

    def test_status_is_case_insensitive(self):
        assert effective_subagent_status("Completed", RETRY_SUMMARY) == "error"
        assert effective_subagent_status("FAILED", None) == "failed"


class TestDelegateResultIsError:
    def test_async_dispatch_ack_is_not_error(self):
        payload = '{"status": "dispatched", "delegation_id": "deleg_x", "count": 1}'
        assert not delegate_result_is_error(payload)

    def test_successful_batch_is_not_error(self):
        payload = (
            '{"results": [{"task_index": 0, "status": "completed",'
            ' "summary": "- Ready to merge: Yes"}]}'
        )
        assert not delegate_result_is_error(payload)

    def test_batch_with_retry_exhausted_child_is_error(self):
        payload = json.dumps({
            "results": [{"task_index": 0, "status": "completed", "summary": RETRY_SUMMARY}],
        })
        assert delegate_result_is_error(payload)

    def test_batch_with_failed_child_is_error(self):
        payload = '{"results": [{"task_index": 0, "status": "failed", "summary": null}]}'
        assert delegate_result_is_error(payload)

    def test_batch_with_timeout_child_is_error(self):
        payload = '{"results": [{"task_index": 0, "status": "timeout"}]}'
        assert delegate_result_is_error(payload)

    def test_batch_with_error_field_is_error(self):
        payload = '{"results": [{"task_index": 0, "status": "completed"}], "error": "crash"}'
        assert delegate_result_is_error(payload)

    def test_top_level_fatal_status_is_error(self):
        assert delegate_result_is_error('{"status": "rejected", "error": "pool full"}')

    def test_bare_entry_payload(self):
        payload = '{"task_index": 0, "status": "completed", "summary": "%s"}' % RETRY_SUMMARY
        assert delegate_result_is_error(payload)

    def test_list_payload(self):
        payload = '[{"status": "timeout"}]'
        assert delegate_result_is_error(payload)

    def test_unparseable_with_retry_text_is_error(self):
        assert delegate_result_is_error(f"not json: {RETRY_SUMMARY}")

    def test_unparseable_without_signature_is_not_error(self):
        assert not delegate_result_is_error("random prose, no failure here")

    def test_decoded_dict_accepted(self):
        assert delegate_result_is_error({"results": [{"status": "failed"}]})
        assert not delegate_result_is_error({"status": "dispatched"})

    def test_none_and_empty(self):
        assert not delegate_result_is_error(None)
        assert not delegate_result_is_error("")


# ── fix_completion_status — anchored rewrite of SDK completion messages ──
# Fixtures mirror the exact formatter shapes in hermes-agent
# tools/process_registry.py (verified against a real deleg_411b6caa message).

BATCH_MSG = """[ASYNC DELEGATION BATCH COMPLETE — deleg_x]
A background fan-out of 2 subagent(s) you dispatched earlier has finished.

Dispatched: 2026-11-25 05:52:58 (6m31s ago)
Role: leaf   Model: ?   Total duration: 400.0s

--- ✓ TASK 1/2: write the spec  (status=completed, api_calls=3, 40.0s) ---
- Spec compliance: 4/4

--- ⚠ TASK 2/2: write the review  (status=completed, api_calls=5, 391.03s, TRUNCATED: hit max_iterations — work may be incomplete) ---
[TRUNCATED — subagent hit its iteration cap; the summary below may be incomplete. Verify before relying on it, or re-dispatch the unfinished part.]
API call failed after 3 retries: Non-streaming API call timed out after 90s with no response (threshold: 90s)"""

BATCH_EVENT = {
    "type": "async_delegation",
    "status": "completed",  # batch-level label is also optimistic
    "results": [
        {"task_index": 0, "status": "completed", "summary": "- Spec compliance: 4/4"},
        {"task_index": 1, "status": "completed", "summary": RETRY_SUMMARY},
    ],
}

SINGLE_MSG = """[ASYNC DELEGATION COMPLETE — deleg_y]
A background subagent you dispatched earlier has finished.
Dispatched: 2026-11-25 05:52:58 (6m31s ago)
Original goal: review the diff
Role: leaf   Model: ?
Status: completed   API calls: 5   Duration: 391.03s [TRUNCATED: hit max_iterations — work may be incomplete]
--- RESULT ---
[TRUNCATED — subagent hit its iteration cap; the summary below may be incomplete. Verify before relying on it, or re-dispatch the unfinished part.]
API call failed after 3 retries: Non-streaming API call timed out after 90s with no response (threshold: 90s)"""

SINGLE_EVENT = {
    "type": "async_delegation",
    "status": "completed",
    "summary": RETRY_SUMMARY,
    "api_calls": 5,
}


class TestFixCompletionStatus:
    def test_batch_rewrites_only_failed_task_footer(self):
        out = fix_completion_status(BATCH_MSG, BATCH_EVENT)
        # The failed task's status token is corrected...
        assert "(status=error, api_calls=5, 391.03s" in out
        # ...while the genuinely completed task keeps its label.
        assert "(status=completed, api_calls=3, 40.0s)" in out
        # Body text is untouched.
        assert RETRY_SUMMARY in out
        assert "- Spec compliance: 4/4" in out

    def test_batch_no_failure_is_identity(self):
        event = {
            "results": [
                {"task_index": 0, "status": "completed", "summary": "all green"},
                {"task_index": 1, "status": "completed", "summary": "also green"},
            ],
        }
        out = fix_completion_status(BATCH_MSG, event)
        assert out == BATCH_MSG
        assert "(status=completed, api_calls=5, 391.03s" in out

    def test_batch_entry_already_labeled_failure_is_not_rewritten(self):
        # SDK already rendered "(status=timeout" for this entry — no-op.
        msg = BATCH_MSG.replace(
            "(status=completed, api_calls=5, 391.03s",
            "(status=timeout, api_calls=5, 391.03s",
        )
        event = {
            "results": [
                {"task_index": 0, "status": "completed", "summary": "ok"},
                {"task_index": 1, "status": "timeout", "summary": RETRY_SUMMARY},
            ],
        }
        out = fix_completion_status(msg, event)
        assert out == msg

    def test_single_rewrites_status_line(self):
        out = fix_completion_status(SINGLE_MSG, SINGLE_EVENT)
        assert "Status: error   API calls: 5" in out
        assert "Status: completed" not in out
        # Only the label line — body untouched.
        assert RETRY_SUMMARY in out

    def test_single_success_is_identity(self):
        event = {"status": "completed", "summary": "- Ready to merge: Yes"}
        out = fix_completion_status(SINGLE_MSG, event)
        assert out == SINGLE_MSG

    def test_single_error_field_forces_rewrite(self):
        event = {"status": "completed", "summary": None, "error": "boom"}
        out = fix_completion_status(SINGLE_MSG, event)
        assert "Status: error   API calls: 5" in out

    def test_failure_status_event_is_identity(self):
        # Already-correct labels are never touched.
        msg = SINGLE_MSG.replace("Status: completed", "Status: timeout")
        event = {"status": "timeout", "summary": RETRY_SUMMARY}
        assert fix_completion_status(msg, event) == msg

    def test_sdk_format_change_degrades_to_noop(self):
        # Anchored shapes missing → return unchanged, never raise.
        weird = "Status value is completed somewhere else\nno anchors"
        event = {"status": "completed", "summary": RETRY_SUMMARY}
        assert fix_completion_status(weird, event) == weird

    def test_non_string_or_non_dict_event_is_safe(self):
        assert fix_completion_status(None, BATCH_EVENT) is None
        assert fix_completion_status(BATCH_MSG, None) == BATCH_MSG
        assert fix_completion_status("", SINGLE_EVENT) == ""

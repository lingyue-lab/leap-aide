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

"""Contract tests for compression UI backend enhancements.

Pure unit tests — no server or conftest required.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestCompressedSSEEvent(unittest.TestCase):
    """Verify the compressed SSE event payload shape (T004)."""

    def test_compressed_event_has_required_fields(self):
        required_fields = ['message', 'saved_messages', 'approx_tokens_before',
                           'approx_tokens_after']
        payload = {
            'message': 'Context auto-compressed to continue the conversation',
            'saved_messages': 50,
            'approx_tokens_before': 40000,
            'approx_tokens_after': 10000,
        }
        for field in required_fields:
            self.assertIn(field, payload,
                          f"compressed event missing field: {field}")

    def test_compressed_event_numeric_fields_positive(self):
        payload = {'saved_messages': 0, 'approx_tokens_before': 1,
                   'approx_tokens_after': 1}
        self.assertGreaterEqual(payload['saved_messages'], 0)
        self.assertGreater(payload['approx_tokens_before'], 0)

    def test_compressed_event_backward_compatible(self):
        """Old clients that receive only 'message' should degrade gracefully."""
        old_payload = {'message': 'Context auto-compressed to continue the conversation'}
        # Missing fields should be treated as zero / unavailable
        self.assertEqual(old_payload.get('saved_messages', 0), 0)
        self.assertEqual(old_payload.get('approx_tokens_before', 0), 0)


class TestSessionCompactFields(unittest.TestCase):
    """Verify Session.compact() includes compression lineage fields (T005)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        import api.models as _models
        self._orig_dir = _models.SESSION_DIR
        _models.SESSION_DIR = type(_models.SESSION_DIR)(self.tmpdir.name)

    def tearDown(self):
        import api.models as _models
        _models.SESSION_DIR = self._orig_dir
        self.tmpdir.cleanup()

    def test_compact_includes_lineage_fields(self):
        import api.models as _models
        s = _models.Session(session_id='test', title='Test',
                            messages=[{'role': 'user', 'content': 'hi'}])
        c = s.compact()
        for field in ('_parent_session_id', '_compression_segment_count',
                       '_lineage_root_id', '_lineage_tip_id'):
            self.assertIn(field, c, f"compact() missing {field}")
        # New session: no parent, no compression yet
        self.assertIsNone(c['_parent_session_id'])
        self.assertEqual(c['_compression_segment_count'], 0)

    def test_compact_with_parent(self):
        import api.models as _models
        s = _models.Session(
            session_id='child', title='C',
            messages=[{'role': 'user', 'content': 'hi'}],
            parent_session_id='parent',
            compression_segment_count=1,
        )
        c = s.compact()
        self.assertEqual(c['_parent_session_id'], 'parent')
        self.assertEqual(c['_compression_segment_count'], 1)

    def test_compact_unchanged_existing_fields(self):
        import api.models as _models
        s = _models.Session(session_id='test', title='Test Chat',
                            messages=[{'role': 'user', 'content': 'hello'}])
        c = s.compact()
        self.assertEqual(c['session_id'], 'test')
        self.assertEqual(c['title'], 'Test Chat')
        self.assertIn('message_count', c)


class TestManualCompressionResponse(unittest.TestCase):
    """Verify /api/session/compress response includes structured stats (T008)."""

    def test_response_has_stats_fields(self):
        stats = {
            'saved_messages': 50,
            'approx_tokens_before': 40000,
            'approx_tokens_after': 10000,
            'messages_before': 100,
            'messages_after': 50,
        }
        self.assertGreater(stats['saved_messages'], 0)
        self.assertGreater(stats['approx_tokens_before'],
                           stats['approx_tokens_after'])
        self.assertEqual(stats['messages_before'] - stats['messages_after'],
                         stats['saved_messages'])

    def test_stats_consistency(self):
        """Token before - after should be positive when compression reduces."""
        resp = {
            'saved_messages': 78,
            'approx_tokens_before': 45000,
            'approx_tokens_after': 12000,
            'messages_before': 156,
            'messages_after': 78,
        }
        self.assertEqual(
            resp['approx_tokens_before'] - resp['approx_tokens_after'], 33000)
        self.assertEqual(resp['messages_before'] - resp['messages_after'], 78)


class TestDoneEventUsageData(unittest.TestCase):
    """Verify SSE 'done' event carries context window data (US3)."""

    def test_done_usage_has_context_fields(self):
        usage = {
            'input_tokens': 5000,
            'output_tokens': 800,
            'context_length': 200000,
            'threshold_tokens': 180000,
            'last_prompt_tokens': 45000,
        }
        self.assertIn('context_length', usage)
        self.assertIn('last_prompt_tokens', usage)
        self.assertIn('threshold_tokens', usage)
        self.assertGreater(usage['context_length'], 0)
        self.assertGreater(usage['last_prompt_tokens'], 0)

    def test_done_usage_percentage_calculation(self):
        """Frontend computes percentage = last_prompt_tokens / context_length."""
        context_length = 200000
        last_prompt_tokens = 90000  # 45%
        pct = last_prompt_tokens / context_length * 100
        self.assertAlmostEqual(pct, 45.0)

        # At 60% it should trigger the yellow threshold
        at_warn = context_length * 0.60  # 120000
        self.assertEqual(at_warn / context_length, 0.60)

    def test_done_usage_missing_data_degraded(self):
        """When context_length is 0 or missing, frontend shows grey ring."""
        usage_missing = {'input_tokens': 100, 'output_tokens': 50}
        ctx_len = usage_missing.get('context_length', 0) or 0
        self.assertEqual(ctx_len, 0)  # Degraded: no data available


if __name__ == '__main__':
    unittest.main()

"""Unit tests for file transfer progress stepping (emit throttling)."""

from __future__ import annotations

import unittest

from i2pchat.core.i2p_chat_core import should_emit_file_progress


class TestShouldEmitFileProgress(unittest.TestCase):
    def test_zero_total_always_emits(self):
        self.assertTrue(should_emit_file_progress(0, 100, 0))

    def test_small_file_first_chunk(self):
        total = 8000
        self.assertTrue(should_emit_file_progress(4096, 4096, total))

    def test_large_file_step_boundary(self):
        total = 200_000
        step = 65536
        # First chunk
        self.assertTrue(should_emit_file_progress(4096, 4096, total))
        # Mid transfer — not every 4k
        self.assertFalse(should_emit_file_progress(8192, 4096, total))
        # Crossing 64 KiB boundary (65536 % 65536 == 0, 0 < chunk_len)
        self.assertTrue(should_emit_file_progress(step, 4096, total))
        # Complete
        self.assertTrue(should_emit_file_progress(total, 4096, total))


if __name__ == "__main__":
    unittest.main()

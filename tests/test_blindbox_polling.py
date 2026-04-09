import os
import sys
import types
import unittest
from unittest.mock import patch

# test environment may not have Pillow installed
if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = object  # type: ignore[attr-defined]
    pil_module.Image = pil_image_module  # type: ignore[attr-defined]
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

from i2pchat.core.i2p_chat_core import I2PChatCore


class BlindBoxPollingTests(unittest.TestCase):
    def test_polling_budget_and_timeout_read_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_RECV_SCAN_BUDGET": "5",
                "I2PCHAT_BLINDBOX_GET_FIRST_TIMEOUT_SEC": "3.25",
                "I2PCHAT_BLINDBOX_GET_FIRST_MISS_GRACE_SEC": "1.4",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            self.assertEqual(core._blindbox_recv_scan_budget, 5)  # noqa: SLF001
            self.assertAlmostEqual(core._blindbox_get_first_timeout_sec, 3.25)  # noqa: SLF001
            self.assertAlmostEqual(core._blindbox_get_first_miss_grace_sec, 1.4)  # noqa: SLF001

    def test_recv_candidates_prioritize_forward_indexes(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_RECV_LOOKAHEAD": "8",
                "I2PCHAT_BLINDBOX_RECV_BACKTRACK": "3",
                "I2PCHAT_BLINDBOX_RECV_MAX_PER_POLL": "20",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core._blindbox_state.recv_base = 10  # noqa: SLF001
            core._blindbox_state.recv_window = 4  # noqa: SLF001
            core._blindbox_state.consumed_recv = {11, 14, 9}  # noqa: SLF001
            # forward: 10,12,13,15,16,17; backtrack tail: 7,8
            got = core._blindbox_recv_candidates()  # noqa: SLF001
            self.assertEqual(got, [10, 12, 13, 15, 16, 17, 7, 8])

    def test_recv_candidates_respect_max_per_poll_cap(self) -> None:
        with patch.dict(
            os.environ,
            {
                "I2PCHAT_BLINDBOX_RECV_LOOKAHEAD": "32",
                "I2PCHAT_BLINDBOX_RECV_BACKTRACK": "0",
                "I2PCHAT_BLINDBOX_RECV_MAX_PER_POLL": "5",
            },
            clear=False,
        ):
            core = I2PChatCore(profile="alice")
            core._blindbox_state.recv_base = 100  # noqa: SLF001
            core._blindbox_state.recv_window = 16  # noqa: SLF001
            core._blindbox_state.consumed_recv = {100, 102, 103}  # noqa: SLF001
            got = core._blindbox_recv_candidates()  # noqa: SLF001
            self.assertEqual(got, [101, 104, 105, 106, 107])

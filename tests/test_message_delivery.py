import unittest

from message_delivery import (
    DELIVERY_STATE_DELIVERED,
    DELIVERY_STATE_FAILED,
    DELIVERY_STATE_QUEUED,
    DELIVERY_STATE_SENDING,
    delivery_lifecycle_from_send_result,
    delivery_state_label,
    normalize_loaded_delivery_state,
)


class MessageDeliveryTests(unittest.TestCase):
    def test_live_send_maps_to_sending(self) -> None:
        lifecycle = delivery_lifecycle_from_send_result(
            route="online-live",
            accepted=True,
            reason="live-session",
            hint="sent",
        )
        self.assertEqual(lifecycle.state, DELIVERY_STATE_SENDING)
        self.assertFalse(lifecycle.retryable)

    def test_offline_send_maps_to_queued(self) -> None:
        lifecycle = delivery_lifecycle_from_send_result(
            route="offline-queued",
            accepted=True,
            reason="blindbox-ready",
            hint="queued",
        )
        self.assertEqual(lifecycle.state, DELIVERY_STATE_QUEUED)

    def test_failed_send_is_retryable_only_for_send_failed(self) -> None:
        failed = delivery_lifecycle_from_send_result(
            route="blocked",
            accepted=False,
            reason="send-failed",
            hint="boom",
        )
        blocked = delivery_lifecycle_from_send_result(
            route="blocked",
            accepted=False,
            reason="blindbox-await-root",
            hint="connect once",
        )
        self.assertEqual(failed.state, DELIVERY_STATE_FAILED)
        self.assertTrue(failed.retryable)
        self.assertFalse(blocked.retryable)

    def test_loaded_sending_state_degrades_to_failed(self) -> None:
        self.assertEqual(
            normalize_loaded_delivery_state(DELIVERY_STATE_SENDING),
            DELIVERY_STATE_FAILED,
        )
        self.assertEqual(
            normalize_loaded_delivery_state(DELIVERY_STATE_DELIVERED),
            DELIVERY_STATE_DELIVERED,
        )

    def test_delivery_state_label(self) -> None:
        self.assertEqual(delivery_state_label(DELIVERY_STATE_QUEUED), "Queued")
        self.assertEqual(delivery_state_label(None), "")


if __name__ == "__main__":
    unittest.main()

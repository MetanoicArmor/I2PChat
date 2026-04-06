import unittest

from i2pchat.sam.protocol import _redact_sam_reply


class SamReplyRedactionTests(unittest.TestCase):
    def test_redacts_sensitive_fields(self) -> None:
        raw = (
            "SESSION STATUS RESULT=OK DESTINATION=abc123 PRIV=secret-token "
            "MESSAGE=ready"
        )
        redacted = _redact_sam_reply(raw)
        self.assertIn("DESTINATION=<redacted>", redacted)
        self.assertIn("PRIV=<redacted>", redacted)
        self.assertNotIn("DESTINATION=abc123", redacted)
        self.assertNotIn("PRIV=secret-token", redacted)
        self.assertIn("MESSAGE=ready", redacted)

    def test_keeps_non_sensitive_tokens(self) -> None:
        raw = "HELLO REPLY RESULT=OK VERSION=3.1"
        self.assertEqual(_redact_sam_reply(raw), raw)


if __name__ == "__main__":
    unittest.main()

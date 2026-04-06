import unittest

from i2pchat.sam.errors import CantReachPeer, DuplicatedId, InvalidId, LegacySAMException, ProtocolError
from i2pchat.sam.protocol import expect_ok, parse_reply_line


class SamProtocolTests(unittest.TestCase):
    def test_parse_reply_line_extracts_command_topic_and_fields(self) -> None:
        reply = parse_reply_line(b"HELLO REPLY RESULT=OK VERSION=3.1\n")
        self.assertEqual(reply.command, "HELLO")
        self.assertEqual(reply.topic, "REPLY")
        self.assertEqual(reply.fields["RESULT"], "OK")
        self.assertEqual(reply.fields["VERSION"], "3.1")

    def test_parse_reply_line_rejects_empty_line(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_reply_line(b"\n")

    def test_parse_reply_line_rejects_single_token(self) -> None:
        with self.assertRaises(ProtocolError) as ctx:
            parse_reply_line(b"HELLO\n")
        self.assertIn("Malformed", ctx.exception.message)

    def test_parse_reply_line_rejects_whitespace_only(self) -> None:
        with self.assertRaises(ProtocolError) as ctx:
            parse_reply_line(b"   \n")
        self.assertIn("Empty", ctx.exception.message)

    def test_parse_reply_line_accepts_two_token_minimal_reply(self) -> None:
        reply = parse_reply_line(b"STREAM STATUS\n")
        self.assertEqual(reply.command, "STREAM")
        self.assertEqual(reply.topic, "STATUS")
        self.assertEqual(reply.fields, {})

    def test_expect_ok_returns_reply_on_success(self) -> None:
        reply = parse_reply_line(b"STREAM STATUS RESULT=OK\n")
        self.assertIs(expect_ok(reply), reply)

    def test_expect_ok_accepts_dest_reply_pub_priv_without_result(self) -> None:
        """i2pd may omit RESULT=OK on DEST GENERATE success (DEST REPLY PUB=… PRIV=…)."""
        line = (
            b"DEST REPLY PUB=aaa PRIV=bbb\n"
        )
        reply = parse_reply_line(line)
        self.assertIs(expect_ok(reply), reply)

    def test_expect_ok_maps_known_sam_errors(self) -> None:
        cant_reach = parse_reply_line(
            b"STREAM STATUS RESULT=CANT_REACH_PEER MESSAGE=offline\n"
        )
        with self.assertRaises(CantReachPeer):
            expect_ok(cant_reach)

        invalid_id = parse_reply_line(
            b"STREAM STATUS RESULT=INVALID_ID MESSAGE=unknown-session\n"
        )
        with self.assertRaises(InvalidId):
            expect_ok(invalid_id)

    def test_expect_ok_rejects_missing_result(self) -> None:
        reply = parse_reply_line(b"STREAM STATUS MESSAGE=no-result\n")
        with self.assertRaises(ProtocolError):
            expect_ok(reply)

    def test_expect_ok_maps_duplicated_id(self) -> None:
        reply = parse_reply_line(
            b"SESSION STATUS RESULT=DUPLICATED_ID MESSAGE=dup\n"
        )
        with self.assertRaises(DuplicatedId):
            expect_ok(reply)

    def test_expect_ok_maps_unknown_result_to_legacy(self) -> None:
        reply = parse_reply_line(b"STREAM STATUS RESULT=NO_SUCH_THING MESSAGE=x\n")
        with self.assertRaises(LegacySAMException) as ctx:
            expect_ok(reply)
        self.assertEqual(ctx.exception.result, "NO_SUCH_THING")


if __name__ == "__main__":
    unittest.main()

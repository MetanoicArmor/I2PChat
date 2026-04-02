import unittest

from i2pchat.protocol.chat_text_chunking import (
    MAX_CHAT_MESSAGE_CHARS,
    split_long_chat_text,
)


class ChatTextChunkingTests(unittest.TestCase):
    def test_short_text_single_part(self) -> None:
        self.assertEqual(split_long_chat_text("hello"), ["hello"])

    def test_empty_returns_empty_list(self) -> None:
        self.assertEqual(split_long_chat_text(""), [])

    def test_exact_limit_single_part(self) -> None:
        s = "a" * MAX_CHAT_MESSAGE_CHARS
        self.assertEqual(split_long_chat_text(s), [s])

    def test_splits_at_newline_when_reasonable(self) -> None:
        n = MAX_CHAT_MESSAGE_CHARS // 2
        line_a = "a" * n
        line_b = "b" * (MAX_CHAT_MESSAGE_CHARS + 50)
        text = line_a + "\n" + line_b
        parts = split_long_chat_text(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(parts[0].endswith("\n") or parts[0] == line_a + "\n")

    def test_hard_split_long_token(self) -> None:
        token = "x" * (MAX_CHAT_MESSAGE_CHARS + 100)
        parts = split_long_chat_text(token)
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[0]), MAX_CHAT_MESSAGE_CHARS)
        self.assertEqual(len(parts[1]), 100)

    def test_unicode_codepoints_not_bytes(self) -> None:
        # Длина по символам (эмодзи = 1), не по UTF-8 байтам.
        one = "😀" * (MAX_CHAT_MESSAGE_CHARS // 2)
        two = "😀" * (MAX_CHAT_MESSAGE_CHARS // 2 + 10)
        text = one + "\n" + two
        parts = split_long_chat_text(text)
        joined = "".join(parts)
        self.assertEqual(joined, text)
        self.assertTrue(all(len(p) <= MAX_CHAT_MESSAGE_CHARS for p in parts))


if __name__ == "__main__":
    unittest.main()

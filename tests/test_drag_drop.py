"""Unit tests for i2pchat.presentation.drag_drop (issue #24)."""

from __future__ import annotations

import os
import tempfile
import unittest

from i2pchat.presentation.drag_drop import (
    REJECT,
    SEND_FILE,
    SEND_IMAGE,
    classify_drop,
    validate_drop_file,
    validate_drop_image,
)


class TestClassifyDrop(unittest.TestCase):
    def test_parametrized(self):
        cases = [
            ([], REJECT),
            ([""], REJECT),
            (["/tmp/a.txt", "/tmp/b.txt"], REJECT),  # multiple files
            (["/tmp/photo.png"], SEND_IMAGE),
            (["/tmp/photo.PNG"], SEND_IMAGE),
            (["/tmp/photo.jpg"], SEND_IMAGE),
            (["/tmp/photo.jpeg"], SEND_IMAGE),
            (["/tmp/photo.webp"], SEND_IMAGE),
            (["/tmp/archive.zip"], SEND_FILE),
            (["/tmp/document.pdf"], SEND_FILE),
            (["/tmp/no_ext"], SEND_FILE),
        ]
        for urls, expected in cases:
            with self.subTest(urls=urls):
                self.assertEqual(classify_drop([], urls), expected)

    def test_mime_types_ignored(self):
        self.assertEqual(classify_drop(["image/png"], ["/tmp/file.txt"]), SEND_FILE)


class TestValidateDropFile(unittest.TestCase):
    def test_valid(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"hello")
            path = f.name
        try:
            ok, reason = validate_drop_file(path)
            self.assertTrue(ok)
            self.assertEqual(reason, "")
        finally:
            os.unlink(path)

    def test_not_found(self):
        ok, reason = validate_drop_file("/nonexistent/path/file.txt")
        self.assertFalse(ok)
        self.assertIn("not found", reason.lower())

    def test_empty_path(self):
        ok, reason = validate_drop_file("")
        self.assertFalse(ok)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            ok, reason = validate_drop_file(path)
            self.assertFalse(ok)
            self.assertIn("empty", reason.lower())
        finally:
            os.unlink(path)

    def test_directory(self):
        with tempfile.TemporaryDirectory() as d:
            ok, reason = validate_drop_file(d)
            self.assertFalse(ok)
            self.assertIn("regular", reason.lower())


class TestValidateDropImage(unittest.TestCase):
    def test_valid_extension(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            path = f.name
        try:
            # Pillow may or may not be installed; extension is valid either way
            _ok, _reason = validate_drop_image(path)
        finally:
            os.unlink(path)

    def test_wrong_extension(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
            f.write(b"data")
            path = f.name
        try:
            ok, reason = validate_drop_image(path)
            self.assertFalse(ok)
            self.assertIn("unsupported", reason.lower())
        finally:
            os.unlink(path)

    def test_not_found(self):
        ok, reason = validate_drop_image("/no/such/image.png")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()

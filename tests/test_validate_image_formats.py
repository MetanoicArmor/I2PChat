"""Проверка validate_image для PNG/JPEG/WebP."""

from __future__ import annotations

import unittest

try:
    import pytest
except ImportError as exc:  # pragma: no cover - environment-dependent test bootstrap
    raise unittest.SkipTest("pytest is not installed") from exc

from i2pchat.core.i2p_chat_core import detect_inline_image_format, validate_image


def test_detect_inline_image_format() -> None:
    assert detect_inline_image_format(b"\x89PNG\r\n\x1a\n") == "png"
    assert detect_inline_image_format(b"\xff\xd8\xff\xe0") == "jpeg"
    assert detect_inline_image_format(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP") == "webp"
    assert detect_inline_image_format(b"") is None
    assert detect_inline_image_format(b"GIF87a") is None


def test_validate_png_jpeg(tmp_path) -> None:
    from PIL import Image

    p = tmp_path / "a.png"
    Image.new("RGB", (4, 4), color="blue").save(p, "PNG")
    ok, err, ext = validate_image(str(p))
    assert ok and ext == "png" and err == ""

    j = tmp_path / "b.jpg"
    Image.new("RGB", (4, 4), color="red").save(j, "JPEG")
    ok, err, ext = validate_image(str(j))
    assert ok and ext == "jpeg" and err == ""


def test_validate_webp(tmp_path) -> None:
    from PIL import Image

    w = tmp_path / "c.webp"
    try:
        Image.new("RGB", (8, 8), color="green").save(w, "WEBP")
    except Exception:
        pytest.skip("Pillow WebP encode not available")

    ok, err, ext = validate_image(str(w))
    assert ok and ext == "webp" and err == ""

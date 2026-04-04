"""Рендер текста сообщений с подстановкой PNG Fluent UI Emoji (как в пикере)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PyQt6 import QtCore, QtGui

from i2pchat.gui.emoji_data import EMOJI_CHARS
from i2pchat.gui.emoji_paths import emoji_paths_cached, normalize_emoji_glyph

_emoji_re: Optional[re.Pattern[str]] = None

_DOC_RES_COUNTER_PROP = "raster_emoji_res_counter"
_RASTER_EMOJI_URL_PREFIX = "rasteremoji:"


def emoji_pick_regex() -> re.Pattern[str]:
    global _emoji_re
    if _emoji_re is None:
        parts = sorted((re.escape(c) for c in EMOJI_CHARS), key=len, reverse=True)
        _emoji_re = re.compile("|".join(parts) if parts else "$^")
    return _emoji_re


def emoji_inline_px(metrics: QtGui.QFontMetrics) -> int:
    # Баблы и поле ввода: чуть крупнее строки текста, в пределах читаемости бабла
    return max(19, min(30, int(metrics.height() * 1.02)))


def _app_device_pixel_ratio() -> float:
    """DPR активного/основного экрана для чётких растров в QTextDocument (Retina и т.п.)."""
    try:
        from PyQt6 import QtWidgets

        app = QtWidgets.QApplication.instance()
        if app is None:
            return 1.0
        scr = None
        w = app.activeWindow()
        if w is not None:
            scr = w.screen()
        if scr is None:
            scr = app.primaryScreen()
        if scr is None:
            return 1.0
        return max(1.0, min(3.0, float(scr.devicePixelRatio())))
    except Exception:
        return 1.0


def line_horizontal_advance_raster_emoji(
    line: str, metrics: QtGui.QFontMetrics, paths: dict[str, Path], emoji_px: int
) -> int:
    if not line:
        return int(metrics.horizontalAdvance(" "))
    if not paths:
        return int(metrics.horizontalAdvance(line))
    rx = emoji_pick_regex()
    total = 0
    pos = 0
    for m in rx.finditer(line):
        if m.start() > pos:
            total += metrics.horizontalAdvance(line[pos : m.start()])
        g = m.group(0)
        gn = normalize_emoji_glyph(g)
        total += emoji_px if gn in paths else metrics.horizontalAdvance(g)
        pos = m.end()
    if pos < len(line):
        total += metrics.horizontalAdvance(line[pos:])
    return max(total, int(metrics.horizontalAdvance(" ")))


def make_message_qtextdocument(
    text: str,
    font: QtGui.QFont,
    text_color: QtGui.QColor,
    inner_width: float,
    paths: dict[str, Path],
) -> QtGui.QTextDocument:
    """Собирает документ без HTML <img>: те же QPixmap + devicePixelRatio, что и в поле ввода."""
    doc = QtGui.QTextDocument()
    doc.setDefaultFont(font)
    doc.setProperty(_DOC_RES_COUNTER_PROP, -1)
    opt = QtGui.QTextOption()
    opt.setWrapMode(QtGui.QTextOption.WrapMode.WordWrap)
    doc.setDefaultTextOption(opt)

    cursor = QtGui.QTextCursor(doc)
    base = QtGui.QTextCharFormat()
    base.setForeground(text_color)
    cursor.setCharFormat(base)

    metrics = QtGui.QFontMetrics(font)
    px = emoji_inline_px(metrics)
    dpr = _app_device_pixel_ratio()

    raw = text or ""
    lines = raw.split("\n")
    if raw == "":
        lines = [" "]

    for li, line in enumerate(lines):
        if li > 0:
            cursor.insertBlock()
        if not paths:
            cursor.setCharFormat(base)
            cursor.insertText(line if line else " ")
            continue
        rx = emoji_pick_regex()
        pos = 0
        for m in rx.finditer(line):
            if m.start() > pos:
                cursor.setCharFormat(base)
                cursor.insertText(line[pos : m.start()])
            g = m.group(0)
            pth = paths.get(normalize_emoji_glyph(g))
            if pth is not None:
                insert_raster_emoji_at_cursor(cursor, doc, g, pth, px, dpr=dpr)
            else:
                cursor.setCharFormat(base)
                cursor.insertText(g)
            pos = m.end()
        if pos < len(line):
            cursor.setCharFormat(base)
            cursor.insertText(line[pos:])
        elif line == "":
            cursor.setCharFormat(base)
            cursor.insertText(" ")

    doc.setTextWidth(float(inner_width))
    return doc


# ----- Поле ввода (QTextEdit): те же PNG, в протокол уходит Unicode -----

RASTER_EMOJI_GLYPH_PROPERTY = int(QtGui.QTextFormat.Property.UserProperty) + 1


def compose_emoji_px(font: QtGui.QFont) -> int:
    return emoji_inline_px(QtGui.QFontMetrics(font))


def _next_raster_emoji_resource_id(doc: QtGui.QTextDocument) -> int:
    v = doc.property(_DOC_RES_COUNTER_PROP)
    n = 0 if v is None else int(v) + 1
    doc.setProperty(_DOC_RES_COUNTER_PROP, n)
    return n


def glyph_from_raster_emoji_image_format(fmt: QtGui.QTextImageFormat) -> str:
    v = fmt.property(RASTER_EMOJI_GLYPH_PROPERTY)
    return str(v) if v else ""


def insert_raster_emoji_at_cursor(
    cursor: QtGui.QTextCursor,
    doc: QtGui.QTextDocument,
    glyph: str,
    png_path: Path,
    px: int,
    *,
    dpr: Optional[float] = None,
) -> None:
    if dpr is None:
        dpr = _app_device_pixel_ratio()
    pm = QtGui.QPixmap(str(png_path))
    if pm.isNull():
        cursor.insertText(glyph)
        return
    # PNG 256×256 достаточно; чёткость на Retina — за счёт физических пикселей (px * dpr)
    side = max(1, int(round(px * dpr)))
    scaled = pm.scaled(
        side,
        side,
        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(dpr)
    rid = _next_raster_emoji_resource_id(doc)
    url = QtCore.QUrl(f"{_RASTER_EMOJI_URL_PREFIX}{rid}")
    doc.addResource(
        int(QtGui.QTextDocument.ResourceType.ImageResource),
        url,
        scaled,
    )
    img_fmt = QtGui.QTextImageFormat()
    img_fmt.setName(url.toString())
    img_fmt.setWidth(px)
    img_fmt.setHeight(px)
    img_fmt.setProperty(RASTER_EMOJI_GLYPH_PROPERTY, glyph)
    cursor.insertImage(img_fmt)


def append_plain_with_raster_emoji_at_cursor(
    cursor: QtGui.QTextCursor,
    doc: QtGui.QTextDocument,
    fragment: str,
    font: QtGui.QFont,
) -> None:
    if not fragment:
        return
    paths = emoji_paths_cached()
    if not paths:
        cursor.insertText(fragment)
        return
    px = compose_emoji_px(font)
    rx = emoji_pick_regex()
    pos = 0
    for m in rx.finditer(fragment):
        if m.start() > pos:
            cursor.insertText(fragment[pos : m.start()])
        g = m.group(0)
        pth = paths.get(normalize_emoji_glyph(g))
        if pth is not None:
            insert_raster_emoji_at_cursor(cursor, doc, g, pth, px)
        else:
            cursor.insertText(g)
        pos = m.end()
    if pos < len(fragment):
        cursor.insertText(fragment[pos:])


def document_needs_raster_emoji_materialize(doc: QtGui.QTextDocument) -> bool:
    """True if any *text* fragment still contains a glyph we replace with a bundled PNG.

    Do not use the joined Unicode plain string for this check: images are serialized
    back to emoji characters there, so the string would always look "needing" work
    after materialize and would trigger a full rebuild on every idle timer tick.
    """
    paths = emoji_paths_cached()
    if not paths:
        return False
    rx = emoji_pick_regex()
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if not frag.charFormat().isImageFormat():
                for m in rx.finditer(frag.text()):
                    if paths.get(normalize_emoji_glyph(m.group(0))) is not None:
                        return True
            it += 1
        block = block.next()
    return False


def fill_document_from_plain(doc: QtGui.QTextDocument, plain: str, font: QtGui.QFont) -> None:
    doc.blockSignals(True)
    try:
        doc.clear()
        doc.setDefaultFont(font)
        _opt = QtGui.QTextOption()
        _opt.setWrapMode(QtGui.QTextOption.WrapMode.WordWrap)
        doc.setDefaultTextOption(_opt)
        doc.setProperty(_DOC_RES_COUNTER_PROP, -1)
        cur = QtGui.QTextCursor(doc)
        append_plain_with_raster_emoji_at_cursor(cur, doc, plain, font)
    finally:
        doc.blockSignals(False)


def document_plain_with_raster_emoji_images(doc: QtGui.QTextDocument) -> str:
    parts: list[str] = []
    first_block = True
    block = doc.begin()
    while block.isValid():
        if not first_block:
            parts.append("\n")
        first_block = False
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            fmt = frag.charFormat()
            if fmt.isImageFormat():
                imf = fmt.toImageFormat()
                g = glyph_from_raster_emoji_image_format(imf)
                parts.append(g if g else "\ufffc")
            else:
                parts.append(frag.text())
            it += 1
        block = block.next()
    return "".join(parts)


def _qchar_width(ch: str) -> int:
    """QTextDocument positions count UTF-16 code units (QChar), not Unicode codepoints."""
    if not ch:
        return 0
    return 2 if ord(ch) > 0xFFFF else 1


def _plain_prefix_qchar_len(txt: str, n_codepoints: int) -> int:
    """QChar offset after the first ``n_codepoints`` codepoints of ``txt``."""
    inner = 0
    for i, ch in enumerate(txt):
        if i >= n_codepoints:
            break
        inner += _qchar_width(ch)
    return inner


def _qchar_inner_to_plain_length(txt: str, q_inner: int) -> int:
    """Number of codepoints in ``txt`` that lie entirely before QChar offset ``q_inner``."""
    if q_inner <= 0:
        return 0
    used = 0
    for i, ch in enumerate(txt):
        n = _qchar_width(ch)
        if used + n > q_inner:
            return i
        used += n
        if used >= q_inner:
            return i + 1
    return len(txt)


def map_qt_pos_to_plain_offset(doc: QtGui.QTextDocument, qt_pos: int) -> int:
    plain_off = 0
    first_block = True
    block = doc.begin()
    while block.isValid():
        if not first_block:
            if qt_pos < block.position():
                return plain_off
            plain_off += 1
        first_block = False
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            fs = block.position() + frag.position()
            fe = fs + frag.length()
            fmt = frag.charFormat()
            if fmt.isImageFormat():
                glyph = (
                    glyph_from_raster_emoji_image_format(fmt.toImageFormat()) or "\ufffc"
                )
                glen = len(glyph)
                if qt_pos <= fs:
                    return plain_off
                if qt_pos < fe:
                    return plain_off
                plain_off += glen
            else:
                txt = frag.text()
                if qt_pos <= fs:
                    return plain_off
                if qt_pos < fe:
                    inner = qt_pos - fs
                    return plain_off + _qchar_inner_to_plain_length(txt, inner)
                plain_off += len(txt)
            it += 1
        block = block.next()
    return plain_off


def map_plain_offset_to_qt_pos(doc: QtGui.QTextDocument, off: int) -> int:
    rem = max(0, off)
    first_block = True
    block = doc.begin()
    while block.isValid():
        if not first_block:
            if rem == 0:
                return block.position()
            rem -= 1
            if rem < 0:
                return block.position()
        first_block = False
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            fs = block.position() + frag.position()
            fe = fs + frag.length()
            fmt = frag.charFormat()
            if fmt.isImageFormat():
                glyph = (
                    glyph_from_raster_emoji_image_format(fmt.toImageFormat()) or "\ufffc"
                )
                glen = len(glyph)
                if rem == 0:
                    return fs
                if rem < glen:
                    return fs
                rem -= glen
                if rem == 0:
                    return fe
            else:
                txt = frag.text()
                tlen = len(txt)
                if rem < tlen:
                    return fs + _plain_prefix_qchar_len(txt, rem)
                rem -= tlen
                if rem == 0:
                    return fe
            it += 1
        block = block.next()
    return max(0, doc.characterCount() - 1)

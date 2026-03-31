"""Рендер текста сообщений с подстановкой PNG Fluent Emoji (как в пикере)."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Optional

from PyQt6 import QtCore, QtGui

from i2pchat.gui.compose_input import load_fluent_emoji_paths
from i2pchat.gui.emoji_data import EMOJI_CHARS

_emoji_re: Optional[re.Pattern[str]] = None
_paths_cache: Optional[dict[str, Path]] = None


def fluent_emoji_paths_cached() -> dict[str, Path]:
    global _paths_cache
    if _paths_cache is None:
        _paths_cache = load_fluent_emoji_paths()
    return _paths_cache


def emoji_pick_regex() -> re.Pattern[str]:
    global _emoji_re
    if _emoji_re is None:
        parts = sorted((re.escape(c) for c in EMOJI_CHARS), key=len, reverse=True)
        _emoji_re = re.compile("|".join(parts) if parts else "$^")
    return _emoji_re


def emoji_inline_px(metrics: QtGui.QFontMetrics) -> int:
    return max(16, min(26, int(metrics.height() * 0.92)))


def _line_html(line: str, paths: dict[str, Path], emoji_px: int) -> str:
    if not paths:
        return html.escape(line) if line else " "
    rx = emoji_pick_regex()
    out: list[str] = []
    pos = 0
    for m in rx.finditer(line):
        if m.start() > pos:
            out.append(html.escape(line[pos:m.start()]))
        g = m.group(0)
        pth = paths.get(g)
        if pth is not None:
            url = QtCore.QUrl.fromLocalFile(str(pth)).toString()
            out.append(
                f'<img src="{html.escape(url, quote=True)}" width="{emoji_px}" '
                f'height="{emoji_px}" style="vertical-align: middle;"/>'
            )
        else:
            out.append(html.escape(g))
        pos = m.end()
    if pos < len(line):
        out.append(html.escape(line[pos:]))
    return "".join(out) if out else " "


def message_body_html(text: str, paths: dict[str, Path], emoji_px: int) -> str:
    lines = (text or "").split("\n")
    return "<br/>".join(_line_html(line, paths, emoji_px) for line in lines) if lines else " "


def line_horizontal_advance_fluent(
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
        total += emoji_px if g in paths else metrics.horizontalAdvance(g)
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
    metrics = QtGui.QFontMetrics(font)
    px = emoji_inline_px(metrics)
    body = message_body_html(text or "", paths, px)
    if not body.strip():
        body = " "
    color = text_color.name(QtGui.QColor.NameFormat.HexRgb)
    full = (
        f'<style>body {{ margin: 0; color: {color}; }}</style>'
        f"<body>{body}</body>"
    )
    doc = QtGui.QTextDocument()
    doc.setDefaultFont(font)
    doc.setHtml(full)
    opt = QtGui.QTextOption()
    opt.setWrapMode(QtGui.QTextOption.WrapMode.WrapAnywhere)
    doc.setDefaultTextOption(opt)
    doc.setTextWidth(float(inner_width))
    return doc


# ----- Поле ввода (QTextEdit): те же PNG, в протокол уходит Unicode -----

FLUENT_EMOJI_GLYPH_PROPERTY = int(QtGui.QTextFormat.Property.UserProperty) + 1


def compose_emoji_px(font: QtGui.QFont) -> int:
    return emoji_inline_px(QtGui.QFontMetrics(font))


def _next_fluent_res_id(doc: QtGui.QTextDocument) -> int:
    v = doc.property("fluent_res_id")
    n = 0 if v is None else int(v) + 1
    doc.setProperty("fluent_res_id", n)
    return n


def glyph_from_fluent_image_format(fmt: QtGui.QTextImageFormat) -> str:
    v = fmt.property(FLUENT_EMOJI_GLYPH_PROPERTY)
    return str(v) if v else ""


def insert_fluent_emoji_at_cursor(
    cursor: QtGui.QTextCursor,
    doc: QtGui.QTextDocument,
    glyph: str,
    png_path: Path,
    px: int,
) -> None:
    pm = QtGui.QPixmap(str(png_path))
    if pm.isNull():
        cursor.insertText(glyph)
        return
    scaled = pm.scaled(
        px,
        px,
        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation,
    )
    rid = _next_fluent_res_id(doc)
    url = QtCore.QUrl(f"fluentemoji:{rid}")
    doc.addResource(
        int(QtGui.QTextDocument.ResourceType.ImageResource),
        url,
        scaled,
    )
    img_fmt = QtGui.QTextImageFormat()
    img_fmt.setName(url.toString())
    img_fmt.setWidth(px)
    img_fmt.setHeight(px)
    img_fmt.setProperty(FLUENT_EMOJI_GLYPH_PROPERTY, glyph)
    cursor.insertImage(img_fmt)


def append_plain_with_fluent_at_cursor(
    cursor: QtGui.QTextCursor,
    doc: QtGui.QTextDocument,
    fragment: str,
    font: QtGui.QFont,
) -> None:
    if not fragment:
        return
    paths = fluent_emoji_paths_cached()
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
        pth = paths.get(g)
        if pth is not None:
            insert_fluent_emoji_at_cursor(cursor, doc, g, pth, px)
        else:
            cursor.insertText(g)
        pos = m.end()
    if pos < len(fragment):
        cursor.insertText(fragment[pos:])


def fill_document_from_plain(doc: QtGui.QTextDocument, plain: str, font: QtGui.QFont) -> None:
    doc.blockSignals(True)
    try:
        doc.clear()
        doc.setDefaultFont(font)
        doc.setProperty("fluent_res_id", -1)
        cur = QtGui.QTextCursor(doc)
        append_plain_with_fluent_at_cursor(cur, doc, plain, font)
    finally:
        doc.blockSignals(False)


def document_plain_with_fluent_images(doc: QtGui.QTextDocument) -> str:
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
                g = glyph_from_fluent_image_format(imf)
                parts.append(g if g else "\ufffc")
            else:
                parts.append(frag.text())
            it += 1
        block = block.next()
    return "".join(parts)


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
                glyph = glyph_from_fluent_image_format(fmt.toImageFormat()) or "\ufffc"
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
                    return plain_off + len(txt[: qt_pos - fs])
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
                glyph = glyph_from_fluent_image_format(fmt.toImageFormat()) or "\ufffc"
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
                    return fs + rem
                rem -= tlen
                if rem == 0:
                    return fe
            it += 1
        block = block.next()
    return max(0, doc.characterCount() - 1)

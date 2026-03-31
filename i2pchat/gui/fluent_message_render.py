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

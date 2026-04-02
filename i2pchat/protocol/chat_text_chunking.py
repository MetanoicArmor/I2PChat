"""
Разбиение длинных пользовательских текстов на несколько сообщений (кадров U), по аналогии с Telegram.

Границы частей — по переводам строк, затем по пробелам, иначе жёстко по лимиту символов (кодпоинты Unicode).
"""

from __future__ import annotations

# Как у Telegram: одно «сообщение» в чате не длиннее этого числа символов (не байт).
MAX_CHAT_MESSAGE_CHARS = 4096

# Не резать сразу после начала части (избегаем обрыва на первом переводе строки).
_MIN_BREAK_LOOKBACK_FRAC = 4


def split_long_chat_text(
    text: str,
    max_chars: int = MAX_CHAT_MESSAGE_CHARS,
) -> list[str]:
    """
    Возвращает непустой список строк; каждая не длиннее ``max_chars`` (кроме случая
    ``len(text) <= max_chars``, тогда ``[text]``).
    """
    if max_chars < 32:
        raise ValueError("max_chars must be at least 32")
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    remaining = text
    min_lookback = max(1, max_chars // _MIN_BREAK_LOOKBACK_FRAC)

    while remaining:
        if len(remaining) <= max_chars:
            parts.append(remaining)
            break

        window = remaining[:max_chars]

        nl = window.rfind("\n")
        if nl >= min_lookback:
            parts.append(remaining[: nl + 1])
            remaining = remaining[nl + 1 :]
            continue

        sp = window.rfind(" ")
        if sp >= min_lookback:
            parts.append(remaining[:sp])
            remaining = remaining[sp + 1 :]
            continue

        parts.append(remaining[:max_chars])
        remaining = remaining[max_chars:]

    return [p for p in parts if p]

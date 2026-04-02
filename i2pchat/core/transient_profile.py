"""
Встроенный эфемерный профиль (TRANSIENT): без сохранения lock/BlindBox между запусками.

Каноническое имя каталога и аргумента — ``random_address`` (раньше в UI/CLI использовалось ``default``).
"""

from __future__ import annotations

from typing import FrozenSet, Optional

# Каталог данных: profiles/<TRANSIENT_PROFILE_NAME>/ (и прежний profiles/default/ при миграции).
TRANSIENT_PROFILE_NAME = "random_address"

# Старое имя; приводится к TRANSIENT_PROFILE_NAME при старте ядра/GUI.
LEGACY_TRANSIENT_PROFILE_NAMES: FrozenSet[str] = frozenset({"default"})


def is_transient_profile_name(name: Optional[str]) -> bool:
    """True для пустой строки, канонического и legacy-имени эфемерного профиля."""
    n = (name or "").strip()
    if not n:
        return True
    if n == TRANSIENT_PROFILE_NAME:
        return True
    return n in LEGACY_TRANSIENT_PROFILE_NAMES


def coalesce_profile_name(profile: Optional[str]) -> str:
    """Имя профиля для ядра и путей: эфемерный режим всегда канонический."""
    raw = (profile or "").strip()
    if is_transient_profile_name(raw if raw else None):
        return TRANSIENT_PROFILE_NAME
    return raw

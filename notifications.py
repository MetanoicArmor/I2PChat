import platform
import subprocess
import sys
from typing import Optional

try:
    # Лучший вариант — использовать установленную кроссплатформенную библиотеку.
    from plyer import notification as _plyer_notification  # type: ignore[import]
except Exception:  # pragma: no cover - optional dependency
    _plyer_notification = None  # type: ignore[assignment]

try:
    # macOS‑обёртка над NSUserNotificationCenter, умеет активировать нужный bundle.
    import pync  # type: ignore[import]
except Exception:  # pragma: no cover - optional dependency
    pync = None  # type: ignore[assignment]


MACOS_BUNDLE_ID = "net.i2pchat.I2PChat"


def _truncate(text: str, limit: int = 120) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def show_notification(title: str, message: str) -> None:
    """
    Показать системное уведомление, если это возможно.

    Функция должна быть безопасной: при любой ошибке просто молча возвращаемся,
    не ломая основное приложение.
    """

    # Нормализуем текст, чтобы не было лишних переносов в тостах.
    title = _truncate(str(title), 64)
    message = _truncate(str(message), 200)

    system = platform.system().lower()

    try:
        if system == "darwin":
            # macOS: показываем уведомления ТОЛЬКО через pync/Notification Center,
            # без какого‑либо osascript, чтобы не открывался Script Editor.
            if pync is not None:  # pragma: no cover - зависит от окружения
                try:
                    pync.Notifier.notify(
                        message,
                        title=title,
                        activate=MACOS_BUNDLE_ID,
                    )
                except Exception:
                    # В случае ошибки просто ничего не делаем.
                    pass
            return
        elif system == "linux":
            # 1) Если есть plyer, используем его — он сам разрулит платформу.
            if _plyer_notification is not None:  # pragma: no cover
                try:
                    _plyer_notification.notify(
                        title=title,
                        message=message,
                        app_name="I2PChat",
                    )
                    return
                except Exception:
                    pass

            # Linux: пробуем notify-send, если оно есть в PATH.
            subprocess.run(
                ["notify-send", title, message],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif system == "windows":
            # 1) Если есть plyer, используем его.
            if _plyer_notification is not None:  # pragma: no cover
                try:
                    _plyer_notification.notify(
                        title=title,
                        message=message,
                        app_name="I2PChat",
                    )
                    return
                except Exception:
                    pass

            # Фоллбэк для Windows без GUI‑API: просто логируем текстом.
            sys.stdout.write(f"[NOTIFY] {title}: {message}\n")
    except Exception:
        # Любые ошибки нотификаций игнорируем.
        return


def play_sound() -> None:
    """
    Простой кроссплатформенный «бип» при новом сообщении.

    Специальных звуковых файлов не используем, чтобы не тянуть ресурсы в репозиторий.
    """
    try:
        system = platform.system().lower()
        if system == "windows":
            # Стандартный консольный beep.
            sys.stdout.write("\a")
            sys.stdout.flush()
        else:
            # В большинстве терминалов *nix достаточно символа BEL.
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        return


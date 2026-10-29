"""Hasło do programu: pytanie raz na 7 dni (prosta blokada dostępu, nie szyfrowanie).

W kodzie jest tylko skrót hasła (PBKDF2), a nie samo hasło.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import time

from . import config

_SALT = b"zgrywka-mxb"
_ITERATIONS = 200_000
_PASSWORD_HASH = "c99491678cdc2e6cdb7ff25ae094c98b3d5075a73e8b1d4568549428c4fb8f3e"
VALID_SECONDS = 7 * 24 * 3600


def check_password(text: str) -> bool:
    digest = hashlib.pbkdf2_hmac("sha256", text.encode("utf-8"), _SALT, _ITERATIONS).hex()
    return hmac.compare_digest(digest, _PASSWORD_HASH)


def unlock_needed(settings: config.Settings, now: float | None = None) -> bool:
    """Hasło trzeba podać, gdy minęło 7 dni od ostatniego (albo zegar komputera cofnięto)."""
    now = time.time() if now is None else now
    return not (settings.unlocked_at - 24 * 3600 <= now < settings.unlocked_at + VALID_SECONDS)


def mark_unlocked(settings: config.Settings) -> None:
    settings.unlocked_at = time.time()
    config.save_settings(settings)


def ask_password_gui() -> bool:
    """Okno z hasłem. False, gdy użytkownik zrezygnował."""
    from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

    prompt = "Podaj hasło do programu (program prosi o nie raz na 7 dni):"
    while True:
        text, ok = QInputDialog.getText(None, f"{config.APP_NAME} – hasło", prompt, QLineEdit.EchoMode.Password)
        if not ok:
            return False
        if check_password(text):
            return True
        QMessageBox.warning(None, config.APP_NAME, "Nieprawidłowe hasło. Spróbuj jeszcze raz.")


def ask_password_cli(tries: int = 3) -> bool:
    for _ in range(tries):
        try:
            text = getpass.getpass("Hasło do programu Zgrywka MXB: ")
        except (EOFError, KeyboardInterrupt):
            return False
        if check_password(text):
            return True
        print("Nieprawidłowe hasło.")
    return False

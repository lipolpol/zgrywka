"""Uruchomienie programu: dziennik (log), obsługa braku konsoli w Windows i okno."""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import sys

from . import config


def _ensure_streams() -> None:
    # Uruchomiony bez konsoli (pythonw.exe, aplikacja .app) Python nie ma stdout/stderr,
    # a niektóre biblioteki próbują tam pisać.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def setup_logging() -> None:
    handler = logging.handlers.RotatingFileHandler(config.log_path(), maxBytes=2_000_000, backupCount=2,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    root.addHandler(console)
    for noisy in ("faster_whisper", "httpx", "huggingface_hub", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        crash_log = open(os.path.join(config.user_data_dir(), "awaria.log"), "a", encoding="utf-8")
        faulthandler.enable(crash_log)
    except OSError:
        pass


def _fatal(message: str) -> None:
    """Komunikat o błędzie, zanim da się pokazać okno Qt (np. brak bibliotek)."""
    print(message, file=sys.stderr)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, config.APP_NAME, 0x10)


def main() -> int:
    _ensure_streams()
    setup_logging()
    log = logging.getLogger("zgrywka")

    try:
        from PySide6.QtWidgets import QApplication

        from .ui import MainWindow, app_icon
    except ImportError as exc:
        log.exception("Nie udało się wczytać bibliotek")
        _fatal(f"Nie udało się wczytać bibliotek programu:\n{exc}\n\n"
               "Usuń folder .venv obok programu i uruchom plik startowy ponownie "
               "(biblioteki zainstalują się od nowa).")
        return 1

    def excepthook(exc_type, exc, tb):
        log.critical("Nieobsłużony błąd", exc_info=(exc_type, exc, tb))
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(None, config.APP_NAME, f"Wystąpił nieoczekiwany błąd:\n{exc}\n\n"
                                                    f"Szczegóły zapisano w pliku:\n{config.log_path()}")

    sys.excepthook = excepthook
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setOrganizationName(config.APP_ID)
    app.setWindowIcon(app_icon())
    settings = config.load_settings()
    window = MainWindow(settings)
    window.show()
    log.info("Start programu (Python %s, %s)", sys.version.split()[0], sys.platform)
    return app.exec()

"""Analiza nagrań w osobnym wątku, żeby okno programu nie "zamarzało"."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QThread, Signal

from .analyzer import Analyzer, FileAnalysis
from .asr import Engine, ModelError
from .matching import Person

log = logging.getLogger(__name__)


class AnalysisWorker(QThread):
    status = Signal(str)
    download_progress = Signal(int, int)        # pobrane bajty, wszystkie bajty
    engine_ready = Signal(str)                  # opis modelu i urządzenia
    file_started = Signal(int)                  # numer wiersza
    file_finished = Signal(int, object)         # numer wiersza, FileAnalysis
    progress = Signal(int, int, float)          # zrobione, wszystkie, szacowany czas do końca [s]
    failed = Signal(str)

    def __init__(self, engine: Engine, jobs: list[tuple[int, str]], people: list[Person], episode: str,
                 seconds: int, use_hints: bool, second_pass: bool, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.jobs = jobs
        self.people = people
        self.episode = episode
        self.seconds = seconds
        self.use_hints = use_hints
        self.second_pass = second_pass
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def run(self) -> None:
        try:
            self.engine.load(on_status=self.status.emit, on_download=self.download_progress.emit)
        except ModelError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Błąd ładowania modelu")
            self.failed.emit(f"Nie udało się uruchomić rozpoznawania mowy:\n{exc}")
            return
        self.engine_ready.emit(self.engine.description)

        analyzer = Analyzer(self.engine, self.people, self.episode, self.seconds,
                            self.use_hints, self.second_pass)
        started = time.monotonic()
        total = len(self.jobs)
        for done, (row, path) in enumerate(self.jobs):
            if self.cancelled:
                break
            self.file_started.emit(row)
            try:
                result = analyzer.analyze(path)
            except Exception as exc:  # noqa: BLE001 - jeden zły plik nie może przerwać całej zgrywki
                log.exception("Błąd analizy %s", path)
                result = FileAnalysis(path, error=f"nieoczekiwany błąd: {exc}")
            log.info("%s -> %s / odcinek=%s / %s", path,
                     result.person.person.name if result.person.person else "-",
                     result.episode.found, result.error or result.transcript[:200])
            self.file_finished.emit(row, result)
            elapsed = time.monotonic() - started
            remaining = elapsed / (done + 1) * (total - done - 1)
            self.progress.emit(done + 1, total, remaining)

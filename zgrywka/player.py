"""Wbudowany odtwarzacz: odsłuch nagrań w programie, z przewijaniem także godzinnych plików.

Dekodowanie: PyAV (każdy format, przewijanie bez czytania całego pliku).
Odtwarzanie: miniaudio (Windows/macOS/Linux, bez dodatkowych bibliotek w systemie).
"""

from __future__ import annotations

import logging
import threading
from collections import deque

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QStyle, QStyleOptionSlider, QToolButton, QWidget

log = logging.getLogger(__name__)

RATE = 48000
CHANNELS = 2
AHEAD_SECONDS = 1.5  # ile dźwięku dekodujemy z wyprzedzeniem


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _Buffer:
    """Bufor między wątkiem dekodującym a urządzeniem audio (ramki float32 stereo)."""

    def __init__(self):
        self._chunks: deque[np.ndarray] = deque()
        self.frames = 0
        self._cond = threading.Condition()

    def push(self, data: np.ndarray, stop: threading.Event) -> None:
        with self._cond:
            while self.frames > AHEAD_SECONDS * RATE and not stop.is_set():
                self._cond.wait(0.05)
            if not stop.is_set():
                self._chunks.append(data)
                self.frames += len(data)

    def pull(self, count: int) -> tuple[np.ndarray, int]:
        out = np.zeros((count, CHANNELS), dtype=np.float32)
        filled = 0
        with self._cond:
            while filled < count and self._chunks:
                chunk = self._chunks[0]
                take = min(count - filled, len(chunk))
                out[filled:filled + take] = chunk[:take]
                if take == len(chunk):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = chunk[take:]
                filled += take
                self.frames -= take
            self._cond.notify_all()
        return out, filled

    def clear(self) -> None:
        with self._cond:
            self._chunks.clear()
            self.frames = 0
            self._cond.notify_all()


class _Decoder(threading.Thread):
    """Dekoduje plik od wskazanej sekundy do bufora."""

    def __init__(self, path: str, start: float, buffer: _Buffer):
        super().__init__(daemon=True)
        self.path, self.start_at, self.buffer = path, start, buffer
        self.stop_event = threading.Event()
        self.done = False
        self.error = ""

    def run(self) -> None:
        import av

        try:
            with av.open(self.path) as container:
                stream = container.streams.audio[0]
                if self.start_at > 0:
                    container.seek(int(self.start_at * 1_000_000), backward=True, any_frame=False)
                resampler = av.AudioResampler(format="flt", layout="stereo", rate=RATE)
                skip = None  # ile próbek pominąć, bo przewinięcie trafia zwykle trochę przed wskazany moment
                for frame in container.decode(stream):
                    if self.stop_event.is_set():
                        return
                    if skip is None:
                        when = float(frame.pts * frame.time_base) if frame.pts is not None else self.start_at
                        skip = max(0, int((self.start_at - when) * RATE))
                    frame.pts = None
                    for out in resampler.resample(frame):
                        data = out.to_ndarray().reshape(-1, CHANNELS)
                        if skip:
                            cut = min(skip, len(data))
                            data, skip = data[cut:], skip - cut
                        if len(data):
                            self.buffer.push(np.ascontiguousarray(data, dtype=np.float32), self.stop_event)
                for out in resampler.resample(None):
                    self.buffer.push(out.to_ndarray().reshape(-1, CHANNELS).astype(np.float32), self.stop_event)
        except Exception as exc:  # noqa: BLE001 - błąd pokazujemy w pasku odtwarzacza
            self.error = str(exc)
            log.warning("Odtwarzanie %s: %s", self.path, exc)
        finally:
            self.done = True

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=2)  # plik musi być zamknięty, zanim go przeniesiemy (Windows)


class AudioPlayer(QObject):
    position_changed = Signal(float)
    state_changed = Signal(str)          # "playing" / "paused" / "stopped"
    media_changed = Signal(str, float)   # ścieżka, długość [s]
    error = Signal(str)

    def __init__(self, parent=None, backends=None):
        super().__init__(parent)
        self._backends = backends  # tylko do testów (np. urządzenie "NULL")
        self._device = None
        self._buffer = _Buffer()
        self._decoder: _Decoder | None = None
        self._base = 0.0     # sekunda, od której dekodujemy
        self._played = 0     # ramki odtworzone od _base
        self._ended = False
        self._volume = 0.9
        self.state = "stopped"
        self.path = ""
        self.duration = 0.0
        self._timer = QTimer(self, interval=100, timeout=self._tick)

    # --- sterowanie ---
    def load(self, path: str) -> bool:
        self.stop()
        try:
            import av

            with av.open(path) as container:
                if not container.streams.audio:
                    raise ValueError("brak ścieżki dźwiękowej")
                self.duration = (container.duration or 0) / 1_000_000
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Nie można odtworzyć pliku: {exc}")
            return False
        self.path = path
        self._base, self._played = 0.0, 0
        self.media_changed.emit(path, self.duration)
        self.position_changed.emit(0.0)
        return True

    def play(self) -> None:
        if not self.path:
            return
        if self._decoder is None or self._ended:
            start = 0.0 if self._ended else self.position()
            self._start_decoder(start)
        if not self._ensure_device():
            return
        self._set_state("playing")
        self._timer.start()

    def pause(self) -> None:
        if self.state == "playing":
            self._set_state("paused")

    def toggle(self) -> None:
        self.pause() if self.state == "playing" else self.play()

    def stop(self) -> None:
        """Zatrzymuje i zwalnia plik (można go potem przenieść)."""
        self._timer.stop()
        self._stop_decoder()
        self._close_device()
        self._base, self._played, self._ended = 0.0, 0, False
        if self.state != "stopped":
            self._set_state("stopped")
        self.position_changed.emit(0.0)

    def seek(self, seconds: float) -> None:
        if not self.path:
            return
        seconds = max(0.0, min(seconds, max(0.0, self.duration - 0.05) if self.duration else seconds))
        self._start_decoder(seconds)
        self.position_changed.emit(seconds)

    def skip(self, delta: float) -> None:
        self.seek(self.position() + delta)

    def position(self) -> float:
        return self._base + self._played / RATE

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))

    def close(self) -> None:
        self.stop()

    # --- wnętrze ---
    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)

    def _start_decoder(self, start: float) -> None:
        self._stop_decoder()
        self._base, self._played, self._ended = start, 0, False
        self._decoder = _Decoder(self.path, start, self._buffer)
        self._decoder.start()

    def _stop_decoder(self) -> None:
        if self._decoder is not None:
            self._decoder.stop()
            self._decoder = None
        self._buffer.clear()

    def _ensure_device(self) -> bool:
        if self._device is not None:
            return True
        try:
            import miniaudio

            kwargs = {"output_format": miniaudio.SampleFormat.FLOAT32, "nchannels": CHANNELS,
                      "sample_rate": RATE, "buffersize_msec": 120, "app_name": "Zgrywka MXB"}
            if self._backends:
                kwargs["backends"] = self._backends
            device = miniaudio.PlaybackDevice(**kwargs)
            generator = self._generate()
            next(generator)
            device.start(generator)
        except Exception as exc:  # noqa: BLE001
            log.warning("Urządzenie audio: %s", exc)
            self.error.emit(f"Nie można otworzyć wyjścia dźwięku: {exc}")
            return False
        self._device = device
        return True

    def _close_device(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:  # noqa: BLE001
                pass
            self._device = None

    def _generate(self):
        """Wywoływane przez urządzenie audio: oddaje kolejne porcje dźwięku."""
        required = yield b""
        while True:
            if self.state == "playing" and not self._ended:
                out, filled = self._buffer.pull(required)
                self._played += filled
                decoder = self._decoder
                if filled < required and decoder is not None and decoder.done and self._buffer.frames == 0:
                    self._ended = True
                if self._volume != 1.0:
                    out *= self._volume
            else:
                out = np.zeros((required, CHANNELS), dtype=np.float32)
            required = yield out.tobytes()

    def _tick(self) -> None:
        decoder = self._decoder
        if decoder is not None and decoder.error:
            message = decoder.error
            self.stop()
            self.error.emit(f"Błąd odtwarzania: {message}")
            return
        if self._ended:
            self._timer.stop()
            self._close_device()
            self._set_state("stopped")
            self.position_changed.emit(self.duration or self.position())
            return
        self.position_changed.emit(self.position())


class SeekSlider(QSlider):
    """Suwak, który po kliknięciu skacze od razu w kliknięte miejsce."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                                 QStyle.SubControl.SC_SliderGroove, self)
            handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                                 QStyle.SubControl.SC_SliderHandle, self)
            if not handle.contains(event.position().toPoint()):
                x = event.position().x() - groove.x() - handle.width() / 2
                span = max(1, groove.width() - handle.width())
                value = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(x), span)
                self.setValue(value)
                self.sliderMoved.emit(value)
                self.sliderReleased.emit()
                return
        super().mousePressEvent(event)


class PlayerBar(QWidget):
    """Pasek odtwarzacza na dole okna (jak w VLC)."""
    previous_requested = Signal()
    next_requested = Signal()

    SKIP = 10  # sekund

    def __init__(self, player: AudioPlayer, parent=None):
        super().__init__(parent)
        self.player = player
        style = self.style()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)

        def button(icon, tip, slot, text=""):
            b = QToolButton()
            b.setIcon(style.standardIcon(icon))
            b.setToolTip(tip)
            if text:
                b.setText(text)
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            layout.addWidget(b)
            return b

        P = QStyle.StandardPixmap
        self.prev_button = button(P.SP_MediaSkipBackward, "Poprzednie nagranie", self.previous_requested.emit)
        self.restart_button = button(P.SP_BrowserReload, "Od początku", lambda: self.player.seek(0))
        self.back_button = button(P.SP_MediaSeekBackward, f"Cofnij o {self.SKIP} s",
                                  lambda: self.player.skip(-self.SKIP), f"{self.SKIP} s")
        self.play_button = button(P.SP_MediaPlay, "Odtwórz / pauza (spacja)", self.player.toggle)
        self.stop_button = button(P.SP_MediaStop, "Zatrzymaj", self.player.stop)
        self.forward_button = button(P.SP_MediaSeekForward, f"Do przodu o {self.SKIP} s",
                                     lambda: self.player.skip(self.SKIP), f"{self.SKIP} s")
        self.next_button = button(P.SP_MediaSkipForward, "Następne nagranie", self.next_requested.emit)

        self.title = QLabel("Kliknij ▶ przy nagraniu, aby je odsłuchać")
        self.title.setMinimumWidth(160)
        self.title.setMaximumWidth(360)
        layout.addWidget(self.title)
        self.time_label = QLabel("0:00")
        layout.addWidget(self.time_label)
        self.slider = SeekSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setToolTip("Przesuń, aby przewinąć nagranie")
        layout.addWidget(self.slider, 1)
        self.duration_label = QLabel("0:00")
        layout.addWidget(self.duration_label)
        volume_icon = QLabel()
        volume_icon.setPixmap(style.standardIcon(P.SP_MediaVolume).pixmap(16, 16))
        layout.addWidget(volume_icon)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(90)
        self.volume.setFixedWidth(90)
        self.volume.setToolTip("Głośność")
        self.volume.valueChanged.connect(lambda v: self.player.set_volume(v / 100))
        layout.addWidget(self.volume)

        self._dragging = False
        self.slider.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.slider.sliderMoved.connect(lambda v: self.time_label.setText(format_time(v / 10)))
        self.slider.sliderReleased.connect(self._seek_from_slider)
        player.position_changed.connect(self._on_position)
        player.state_changed.connect(self._on_state)
        player.media_changed.connect(self._on_media)
        self._set_enabled(False)

    def set_title(self, text: str) -> None:
        self.title.setText(text)
        self.title.setToolTip(text)

    def _set_enabled(self, enabled: bool) -> None:
        for w in (self.restart_button, self.back_button, self.play_button, self.stop_button,
                  self.forward_button, self.slider):
            w.setEnabled(enabled)

    def _seek_from_slider(self) -> None:
        self._dragging = False
        self.player.seek(self.slider.value() / 10)

    def _on_media(self, _path: str, duration: float) -> None:
        self._set_enabled(True)
        self.slider.setRange(0, int(duration * 10))
        self.duration_label.setText(format_time(duration))

    def _on_position(self, seconds: float) -> None:
        if not self._dragging:
            self.slider.setValue(int(seconds * 10))
            self.time_label.setText(format_time(seconds))

    def _on_state(self, state: str) -> None:
        icon = QStyle.StandardPixmap.SP_MediaPause if state == "playing" else QStyle.StandardPixmap.SP_MediaPlay
        self.play_button.setIcon(self.style().standardIcon(icon))

"""Wczytywanie początku nagrania (pierwsze N sekund) jako 16 kHz mono float32."""

from __future__ import annotations

import os

import numpy as np

SAMPLE_RATE = 16000

AUDIO_EXTENSIONS = frozenset({
    ".wav", ".wave", ".bwf", ".rf64", ".w64", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga",
    ".opus", ".wma", ".aif", ".aiff", ".aifc", ".caf", ".mka", ".amr", ".ac3",
})


class AudioError(Exception):
    """Pliku nie da się odczytać jako audio."""


def is_audio_file(path: str) -> bool:
    name = os.path.basename(path)
    return not name.startswith(".") and os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS


def load_head(path: str, seconds: float) -> tuple[np.ndarray, float | None]:
    """Zwraca (próbki z pierwszych `seconds` sekund, długość całego nagrania w s albo None).

    Dekoduje tylko początek pliku, więc nawet wielogodzinne nagrania wczytują się od razu.
    """
    import av  # import tutaj: start aplikacji nie czeka na biblioteki audio

    if os.path.getsize(path) == 0:
        raise AudioError("plik jest pusty (0 bajtów)")
    try:
        container = av.open(path)
    except Exception as exc:  # av.error.FFmpegError i pochodne
        raise AudioError(f"nie można otworzyć pliku: {exc}") from exc

    max_samples = int(seconds * SAMPLE_RATE)
    chunks: list[np.ndarray] = []
    total = 0
    with container:
        if not container.streams.audio:
            raise AudioError("plik nie zawiera ścieżki dźwiękowej")
        stream = container.streams.audio[0]
        duration = None
        if container.duration:
            duration = container.duration / 1_000_000
        elif stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)

        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)

        def take(frames):
            nonlocal total
            for out in frames:
                data = out.to_ndarray().reshape(-1)
                chunks.append(data)
                total += data.size

        try:
            for packet in container.demux(stream):
                try:
                    frames = packet.decode()
                except av.error.InvalidDataError:
                    continue  # uszkodzony fragment - pomijamy
                for frame in frames:
                    frame.pts = None
                    take(resampler.resample(frame))
                if total >= max_samples:
                    break
            take(resampler.resample(None))
        except Exception as exc:
            # ucięty plik (np. rejestrator wyłączony w trakcie) - wystarczy to, co się udało odczytać
            if total == 0:
                raise AudioError(f"błąd dekodowania: {exc}") from exc

    if total == 0:
        raise AudioError("brak danych audio")
    audio = np.concatenate(chunks)[:max_samples].astype(np.float32, copy=False)
    return _normalize_level(audio), duration


def _normalize_level(audio: np.ndarray) -> np.ndarray:
    """Wyrównuje poziom: podbija bardzo ciche nagrania (max +30 dB) i ścisza przestery z plików float."""
    magnitude = np.abs(audio)
    level = float(np.percentile(magnitude, 99.9))
    if level < 1e-7:
        return audio  # cisza cyfrowa
    if level < 0.25:
        gain = np.float32(min(0.25 / level, 31.6))
        return np.clip(audio * gain, -1.0, 1.0)  # pojedyncze trzaski mogą się przyciąć - to nie szkodzi
    peak = float(magnitude.max())
    return audio / np.float32(peak) if peak > 1.0 else audio

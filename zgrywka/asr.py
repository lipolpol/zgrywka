"""Rozpoznawanie mowy (faster-whisper): pobranie modelu, wybór CPU/GPU i transkrypcja po polsku."""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import os
import sys
import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

# Bez telemetrii i pasków postępu w konsoli (w aplikacji okienkowej nie ma gdzie ich wypisać).
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@dataclass(frozen=True)
class ModelInfo:
    key: str
    repo: str
    label: str
    size_mb: int


MODELS: dict[str, ModelInfo] = {m.key: m for m in (
    ModelInfo("small", "Systran/faster-whisper-small", "Szybki (small, 0,5 GB)", 486),
    ModelInfo("medium", "Systran/faster-whisper-medium", "Średni (medium, 1,5 GB)", 1531),
    ModelInfo("large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
              "Dokładny (large-v3-turbo, 1,6 GB) – zalecany", 1622),
    ModelInfo("large-v3", "Systran/faster-whisper-large-v3", "Najdokładniejszy, wolny (large-v3, 3,1 GB)", 3091),
)}
DEFAULT_MODEL = "large-v3-turbo"
_MODEL_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
_MIN_SAMPLES = 1600  # < 0,1 s nagrania - nie ma czego rozpoznawać

# Typowe "halucynacje" Whispera na szumie (napisy z filmów, na których go uczono).
_HALLUCINATIONS = (
    "napisy wykonane", "napisy stworzone", "napisy przygotowa", "amara.org", "subskrybuj",
    "dzięki za obejrzenie", "dziękuję za obejrzenie", "transkrypcja",
)


class ModelError(Exception):
    """Nie udało się przygotować modelu rozpoznawania mowy."""


def local_model_path(key: str, models_dir: str) -> str | None:
    """Ścieżka do pobranego wcześniej modelu albo None (bez łączenia z internetem)."""
    from huggingface_hub import snapshot_download

    try:
        path = snapshot_download(MODELS[key].repo, cache_dir=models_dir,
                                 allow_patterns=_MODEL_FILES, local_files_only=True)
    except Exception:
        return None
    return path if os.path.isfile(os.path.join(path, "model.bin")) else None


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def download_model(key: str, models_dir: str,
                   on_progress: Callable[[int, int], None] | None = None) -> str:
    """Pobiera model (jednorazowo). on_progress(pobrane_bajty, wszystkie_bajty)."""
    from huggingface_hub import snapshot_download

    info = MODELS[key]
    os.makedirs(models_dir, exist_ok=True)
    expected = info.size_mb * 1_000_000
    free = _free_space(models_dir)
    if free is not None and free < expected * 1.1:
        raise ModelError(
            f"Za mało miejsca na dysku na model „{key}”: potrzeba ok. {expected / 1e9:.1f} GB, "
            f"wolne {free / 1e9:.1f} GB ({models_dir}).")

    start_size = _dir_size(models_dir)
    done = threading.Event()

    def poll():
        while not done.wait(0.5):
            if on_progress:
                on_progress(max(0, _dir_size(models_dir) - start_size), expected)

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    try:
        path = snapshot_download(info.repo, cache_dir=models_dir, allow_patterns=_MODEL_FILES)
    except Exception as exc:
        raise ModelError(
            "Nie udało się pobrać modelu rozpoznawania mowy. Sprawdź połączenie z internetem "
            f"(model pobiera się tylko za pierwszym razem).\n\nSzczegóły: {exc}") from exc
    finally:
        done.set()
        poller.join()
    if on_progress:
        on_progress(expected, expected)
    return path


def _free_space(path: str) -> int | None:
    import shutil

    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def default_threads() -> int:
    n = os.cpu_count() or 4
    return max(4, n // 2) if n >= 8 else n


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0


# Biblioteki CUDA (cuBLAS, cuDNN) można doinstalować jako pakiety pip
# (nvidia-cublas-cu12, nvidia-cudnn-cu12); trzeba je wtedy wskazać przed użyciem GPU.
GPU_PACKAGES = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"]
_NVIDIA_LIBS = ("cuda_runtime", "cuda_nvrtc", "cublas", "cudnn")
_nvidia_registered = False


def nvidia_libs_installed() -> bool:
    try:
        return all(importlib.util.find_spec(f"nvidia.{name}") for name in ("cublas", "cudnn"))
    except (ImportError, ValueError):
        return False


def register_nvidia_libs() -> None:
    global _nvidia_registered
    if _nvidia_registered or sys.platform == "darwin":
        return
    _nvidia_registered = True
    try:
        import nvidia  # pakiet-przestrzeń nazw z pakietów nvidia-*
    except ImportError:
        return
    for root in list(getattr(nvidia, "__path__", [])):
        for name in _NVIDIA_LIBS:
            libdir = os.path.join(root, name, "bin" if sys.platform == "win32" else "lib")
            if not os.path.isdir(libdir):
                continue
            if sys.platform == "win32":
                os.add_dll_directory(libdir)
                os.environ["PATH"] = libdir + os.pathsep + os.environ.get("PATH", "")
                continue
            for lib in sorted(os.listdir(libdir)):
                if ".so" in lib:
                    try:
                        ctypes.CDLL(os.path.join(libdir, lib), mode=ctypes.RTLD_GLOBAL)
                    except OSError as exc:
                        log.debug("Pominięto %s: %s", lib, exc)


def probe_main(model_path: str) -> int:
    """Test karty graficznej (uruchamiany w osobnym procesie, bo brak bibliotek CUDA/cuDNN
    potrafi zakończyć cały proces zamiast zgłosić błąd)."""
    try:
        register_nvidia_libs()
        from faster_whisper import WhisperModel

        model = WhisperModel(model_path, device="cuda", compute_type="float16")
        segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="pl",
                                       beam_size=1, vad_filter=False, without_timestamps=True)
        list(segments)
    except BaseException as exc:  # noqa: BLE001 - powód trafia do procesu głównego
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def cuda_usable(model_path: str, timeout: float = 300) -> tuple[bool, str]:
    import subprocess

    if getattr(sys, "frozen", False):
        from . import GPU_PROBE_FLAG

        cmd = [sys.executable, GPU_PROBE_FLAG, model_path]
    else:
        cmd = [sys.executable, "-c", "import sys; from zgrywka.asr import probe_main; "
               "sys.exit(probe_main(sys.argv[1]))", model_path]
    env = dict(os.environ)
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = package_parent + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False, "przekroczono czas testu GPU"
    except OSError as exc:
        return False, str(exc)
    if proc.returncode == 0:
        return True, ""
    lines = [line for line in (proc.stderr or "").splitlines() if line.strip()]
    return False, lines[-1] if lines else f"kod wyjścia {proc.returncode}"


class Engine:
    """Model rozpoznawania mowy - ładowany raz i używany dla wielu plików."""

    def __init__(self, model_key: str, device: str, models_dir: str):
        if model_key not in MODELS:
            model_key = DEFAULT_MODEL
        self.model_key = model_key
        self.device_pref = device  # "auto" | "cpu" | "cuda"
        self.models_dir = models_dir
        self.model = None
        self.device = ""
        self.compute_type = ""

    @property
    def description(self) -> str:
        where = "karta graficzna (CUDA)" if self.device == "cuda" else "procesor (CPU)"
        return f"{self.model_key} • {where} • {self.compute_type}"

    def load(self, on_status: Callable[[str], None] = lambda s: None,
             on_download: Callable[[int, int], None] | None = None) -> None:
        if self.model is not None:
            return
        path = local_model_path(self.model_key, self.models_dir)
        if path is None:
            on_status(f"Pobieranie modelu {self.model_key} (tylko za pierwszym razem)…")
            path = download_model(self.model_key, self.models_dir, on_download)

        from faster_whisper import WhisperModel

        devices = ["cpu"]
        if self.device_pref in ("auto", "cuda") and cuda_device_count() > 0:
            on_status("Sprawdzanie karty graficznej…")
            ok, why = cuda_usable(path)
            if ok:
                register_nvidia_libs()
                devices.insert(0, "cuda")
            else:
                log.warning("GPU niedostępne, używam CPU: %s", why)
                if self.device_pref == "cuda":
                    on_status("Karta graficzna niedostępna (brak bibliotek CUDA/cuDNN) – używam procesora.")

        on_status(f"Ładowanie modelu {self.model_key}…")
        errors = []
        for device in devices:
            for compute_type in (("float16", "int8_float16") if device == "cuda" else ("int8", "float32")):
                try:
                    self.model = WhisperModel(path, device=device, compute_type=compute_type,
                                              cpu_threads=default_threads() if device == "cpu" else 0)
                    self.device, self.compute_type = device, compute_type
                    log.info("Model gotowy: %s", self.description)
                    return
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{device}/{compute_type}: {exc}")
                    log.warning("Nie udało się załadować modelu (%s/%s): %s", device, compute_type, exc)
        raise ModelError("Nie udało się załadować modelu rozpoznawania mowy:\n" + "\n".join(errors))

    def transcribe(self, audio: np.ndarray, hotwords: str | None = None, vad_threshold: float = 0.5) -> str:
        if audio.size < _MIN_SAMPLES:
            return ""
        segments, _info = self.model.transcribe(
            audio,
            language="pl",
            beam_size=5,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={"threshold": vad_threshold, "min_silence_duration_ms": 1000},
            hotwords=hotwords or None,
        )
        texts = []
        for segment in segments:
            text = segment.text.strip()
            if text and not any(h in text.lower() for h in _HALLUCINATIONS):
                texts.append(text)
        return " ".join(texts)


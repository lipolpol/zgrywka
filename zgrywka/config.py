"""Ustawienia programu, lista osób i położenie plików (modele, dziennik zmian, logi)."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field

from .matching import Person, parse_alternatives

APP_NAME = "Zgrywka MXB"
APP_ID = "ZgrywkaMXB"
log = logging.getLogger(__name__)

DEFAULT_PEOPLE = [
    "Radek Chaberko",
    "Michał Chaberko",
    "Weronika Chrabąszcz",
    "Eliza Macudzińska",
    "Marcel Gawroński",
    "Santia Kozłowska",
]


def program_dir() -> str:
    if getattr(sys, "frozen", False):  # aplikacja spakowana (PyInstaller)
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_data_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = os.path.join(base, APP_ID)
    os.makedirs(path, exist_ok=True)
    return path


def _writable(directory: str) -> bool:
    try:
        os.makedirs(directory, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory):
            pass
        return True
    except OSError:
        return False


def models_dir() -> str:
    """Modele trzymamy obok programu (łatwo je znaleźć i usunąć), a gdy się nie da - w danych użytkownika."""
    beside = os.path.join(program_dir(), "modele")
    in_mac_bundle = getattr(sys, "frozen", False) and sys.platform == "darwin"
    return beside if not in_mac_bundle and _writable(beside) else os.path.join(user_data_dir(), "modele")


def journal_dir() -> str:
    return os.path.join(user_data_dir(), "historia")


def log_path() -> str:
    return os.path.join(user_data_dir(), "zgrywka.log")


@dataclass
class Settings:
    episode: str = ""
    # [{"name": "Weronika Chrabąszcz", "alternatywy": ["Wera", "Nika"]}, ...]
    people: list[dict] = field(default_factory=lambda: [{"name": n, "alternatywy": []} for n in DEFAULT_PEOPLE])
    model: str = "large-v3-turbo"
    device: str = "auto"            # auto / cpu / cuda
    seconds: int = 30               # ile sekund od początku nagrania analizować
    use_hints: bool = True          # podpowiadaj modelowi pisownię nazwisk i nazwę odcinka
    second_pass: bool = True        # druga próba, gdy nie wszystko rozpoznano
    recursive: bool = False         # szukaj plików także w podfolderach
    auto_apply: bool = False        # porządkuj od razu po analizie, bez podglądu
    person_only: bool = False       # sortowanie tylko po imieniu i nazwisku (odcinek z pola)
    save_report: bool = False       # zapisuj raport CSV w folderze po uporządkowaniu
    quote_style: str = "pl"         # „…” albo "…" (tylko macOS/Linux)
    last_folder: str = ""
    window_geometry: str = ""
    unlocked_at: float = 0.0        # kiedy ostatnio podano hasło (prośba raz na 7 dni)

    def person_objects(self) -> list[Person]:
        return [Person(p["name"], tuple(p["alternatywy"])) for p in clean_people(self.people)]


def clean_people(entries) -> list[dict]:
    """Lista osób bez pustych wpisów i powtórzeń. Przyjmuje też same nazwy (starszy zapis)."""
    result, seen = [], set()
    for entry in entries:
        if isinstance(entry, dict):
            name, alternatives = entry.get("name", ""), entry.get("alternatywy", [])
        else:
            name, alternatives = entry, []
        name = " ".join(str(name).split())
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        if not isinstance(alternatives, str):
            alternatives = ",".join(str(a) for a in alternatives)
        alternatives = [a for a in parse_alternatives(alternatives) if a.casefold() != name.casefold()]
        result.append({"name": name, "alternatywy": alternatives})
    return result


def settings_path() -> str:
    return os.path.join(user_data_dir(), "ustawienia.json")


def load_settings() -> Settings:
    settings = Settings()
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return settings
    except (OSError, ValueError) as exc:
        log.warning("Nie udało się wczytać ustawień: %s", exc)
        return settings
    for key, value in data.items():
        if hasattr(settings, key) and isinstance(value, type(getattr(settings, key))):
            setattr(settings, key, value)
    settings.people = clean_people(settings.people)
    return settings


def save_settings(settings: Settings) -> None:
    path = settings_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(settings), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("Nie udało się zapisać ustawień: %s", exc)

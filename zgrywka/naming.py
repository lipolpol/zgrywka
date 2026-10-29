"""Nazwy docelowe, numeracja oraz bezpieczne przenoszenie plików z możliwością cofnięcia."""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from dataclasses import dataclass, field

from .audio import is_audio_file

UNKNOWN_LABEL = "nie zidentyfikowany"
NO_PERSON_PREFIX = "NIE WIEM KTO"
NO_EPISODE_TEXT = "!!BRAK NAZWY FILMU!!"

# Windows (i dyski exFAT/FAT32) nie pozwalają na znak " w nazwach, dlatego domyślnie
# używamy polskich cudzysłowów „…”, które wyglądają podobnie i działają wszędzie.
QUOTE_STYLES = {
    "pl": ("„", "”"),
    "ascii": ('"', '"'),
}
ASCII_QUOTES_ALLOWED = sys.platform != "win32"

_FORBIDDEN = {"<": "(", ">": ")", ":": " -", "/": "-", "\\": "-", "|": "-", "?": "", "*": ""}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_QUOTE_CHARS = "\"'„”“«»‚‘’`"
MAX_LABEL_LENGTH = 150


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def clean_episode(text: str) -> str:
    """Nazwa odcinka bez otaczających cudzysłowów i zbędnych spacji ("tratwy" → tratwy)."""
    return " ".join((text or "").strip().strip(_QUOTE_CHARS).split())


def safe_filename(name: str, allow_ascii_quote: bool = False) -> str:
    """Zamienia znaki niedozwolone w nazwach plików na Windows/macOS/Linux."""
    out = []
    for ch in nfc(name):
        if ord(ch) < 32:
            continue
        if ch == '"' and not allow_ascii_quote:
            ch = "”"
        out.append(_FORBIDDEN.get(ch, ch))
    result = " ".join("".join(out).split()).rstrip(". ")
    if len(result) > MAX_LABEL_LENGTH:
        result = result[:MAX_LABEL_LENGTH].rstrip(". ")
    if result.split(".")[0].upper() in _RESERVED:
        result = "_" + result
    return result or "_"


@dataclass(frozen=True)
class Target:
    label: str   # np. 'Marcel Gawroński „tratwy”'
    move: bool   # True: plik trafia do podfolderu o tej nazwie; False: zmiana nazwy w miejscu


def make_target(person: str | None, episode_found: bool, episode: str, quote_style: str = "pl") -> Target:
    style = quote_style if (quote_style != "ascii" or ASCII_QUOTES_ALLOWED) else "pl"
    left, right = QUOTE_STYLES.get(style, QUOTE_STYLES["pl"])
    allow_ascii = style == "ascii"
    episode = clean_episode(episode)

    def q(text):
        return f"{left}{text}{right}"

    if person and episode_found and episode:
        label, move = f"{person} {q(episode)}", True
    elif person:
        label, move = f"{person} {q(NO_EPISODE_TEXT)}", False
    elif episode_found and episode:
        label, move = f"{NO_PERSON_PREFIX} {q(episode)}", False
    else:
        label, move = UNKNOWN_LABEL, False
    return Target(safe_filename(label, allow_ascii), move)


@dataclass(frozen=True)
class PriorLabel:
    """To, co wynika z nazwy nadanej wcześniej przez program (poprzednia zgrywka albo ręczna poprawka)."""
    person: str | None = None
    episode_found: bool = False


def _split_quoted(label: str) -> tuple[str, str] | None:
    for left, right in QUOTE_STYLES.values():
        pos = label.rfind(f" {left}")
        if pos > 0 and label.endswith(right):
            return label[:pos], label[pos + 2:-1]
    return None


def parse_prior_label(filename: str, people: list[str], episode: str) -> PriorLabel | None:
    match = re.fullmatch(r"(.+) \d+", nfc(os.path.splitext(os.path.basename(filename))[0]))
    if not match:
        return None
    label = match.group(1)
    if label.casefold() == UNKNOWN_LABEL.casefold():
        return PriorLabel()
    split = _split_quoted(label)
    if not split:
        return None
    prefix, inner = split
    episode = clean_episode(episode)
    episode_here = bool(episode) and inner.casefold() == safe_filename(episode).casefold()
    if prefix == NO_PERSON_PREFIX:
        return PriorLabel(None, episode_here)
    person = next((p for p in people if safe_filename(p).casefold() == prefix.casefold()), None)
    return PriorLabel(person, episode_here) if person else None


def merge_prior(person: str | None, episode_found: bool, prior: PriorLabel | None) -> tuple[str | None, bool, bool]:
    """Ponowna analiza może tylko dodać informacje, a nie zgubić tych z obecnej nazwy pliku.
    Zwraca (osoba, odcinek, czy_użyto_wcześniejszej_nazwy)."""
    used = False
    if prior:
        if not person and prior.person:
            person, used = prior.person, True
        if not episode_found and prior.episode_found:
            episode_found, used = True, True
    return person, episode_found, used


def natural_key(text: str):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", nfc(text))]


def numbered_name(label: str, number: int, ext: str) -> str:
    return f"{label} {number}{ext}"


def parse_number(filename: str, label: str) -> int | None:
    """Numer pliku nazwanego "<label> <n>.<rozszerzenie>" (bez względu na wielkość liter) albo None."""
    stem = os.path.splitext(nfc(filename))[0]
    prefix = nfc(label).casefold() + " "
    if not stem.casefold().startswith(prefix):
        return None
    rest = stem[len(prefix):]
    return int(rest) if rest.isdigit() else None


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(nfc(os.path.abspath(a))) == os.path.normcase(nfc(os.path.abspath(b)))


@dataclass
class PlannedMove:
    src: str
    dst: str
    target: Target
    number: int

    @property
    def unchanged(self) -> bool:
        return _same_path(self.src, self.dst) and os.path.basename(self.src) == os.path.basename(self.dst)


def _existing_numbers(directory: str, label: str) -> set[int]:
    numbers = set()
    try:
        names = os.listdir(directory)
    except OSError:
        return numbers
    for name in names:
        n = parse_number(name, label)
        if n is not None:
            numbers.add(n)
    return numbers


def plan_moves(items: list[tuple[str, Target]], root: str) -> list[PlannedMove]:
    """Ustala nazwy docelowe. Numeracja w grupie idzie wg naturalnej kolejności nazw plików
    i kontynuuje numery już istniejące w folderze docelowym (np. z poprzedniej zgrywki)."""
    groups: dict[tuple[str, str], list[tuple[str, Target, str]]] = {}
    for src, target in items:
        directory = os.path.join(root, target.label) if target.move else os.path.dirname(src)
        key = (os.path.normcase(nfc(os.path.abspath(directory))), nfc(target.label).casefold())
        groups.setdefault(key, []).append((src, target, directory))

    plan: list[PlannedMove] = []
    for members in groups.values():
        directory, label = members[0][2], members[0][1].label
        used = _existing_numbers(directory, label)
        pending = []
        for src, target, _ in members:
            number = parse_number(os.path.basename(src), label)
            if number is not None and _same_path(os.path.dirname(src), directory):
                plan.append(PlannedMove(src, src, target, number))  # już ma właściwą nazwę
            else:
                pending.append((src, target))
        next_number = max(used, default=0) + 1
        for src, target in sorted(pending, key=lambda it: natural_key(os.path.basename(it[0]))):
            ext = os.path.splitext(src)[1]
            dst = os.path.join(directory, numbered_name(label, next_number, ext))
            plan.append(PlannedMove(src, dst, target, next_number))
            next_number += 1
    return plan


# --------------------------------------------------------------------------------------
# Wykonanie i cofanie
# --------------------------------------------------------------------------------------

@dataclass
class ApplyReport:
    moved: list[PlannedMove] = field(default_factory=list)
    skipped: int = 0
    errors: list[tuple[PlannedMove, str]] = field(default_factory=list)
    journal: str = ""


def _move(src: str, dst: str) -> None:
    if os.path.exists(dst) and not _same_path(src, dst):
        raise FileExistsError(errno.EEXIST, "plik o tej nazwie już istnieje", dst)
    try:
        os.rename(src, dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.move(src, dst)  # inny dysk/partycja - kopiowanie i usunięcie


def apply_moves(plan: list[PlannedMove], journal_dir: str, root: str = "") -> ApplyReport:
    """Przenosi pliki. Każdy wykonany krok jest od razu zapisywany w dzienniku,
    więc operację da się cofnąć nawet po awarii w trakcie."""
    report = ApplyReport()
    os.makedirs(journal_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    report.journal = os.path.join(journal_dir, f"zgrywka_{stamp}.jsonl")
    with open(report.journal, "a", encoding="utf-8") as journal:
        journal.write(json.dumps({"root": root, "time": time.time()}, ensure_ascii=False) + "\n")
        for move in plan:
            if move.unchanged:
                report.skipped += 1
                continue
            try:
                directory = os.path.dirname(move.dst)
                if not os.path.isdir(directory):
                    os.makedirs(directory)
                    journal.write(json.dumps({"mkdir": directory}, ensure_ascii=False) + "\n")
                _move(move.src, move.dst)
            except OSError as exc:
                report.errors.append((move, exc.strerror or str(exc)))
                continue
            journal.write(json.dumps({"src": move.src, "dst": move.dst}, ensure_ascii=False) + "\n")
            journal.flush()
            report.moved.append(move)
    if not report.moved:
        os.remove(report.journal)
        report.journal = ""
    return report


@dataclass
class UndoReport:
    restored: int = 0
    errors: list[str] = field(default_factory=list)


def undo_journal(journal_path: str) -> UndoReport:
    report = UndoReport()
    with open(journal_path, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]
    for entry in reversed(entries):
        if "root" in entry:
            continue
        if "mkdir" in entry:
            try:
                os.rmdir(entry["mkdir"])  # tylko jeśli pusty
            except OSError:
                pass
            continue
        src, dst = entry["src"], entry["dst"]
        try:
            os.makedirs(os.path.dirname(src), exist_ok=True)
            _move(dst, src)
            report.restored += 1
        except OSError as exc:
            report.errors.append(f"{os.path.basename(dst)}: {exc.strerror or exc}")
    os.replace(journal_path, journal_path + ".cofniete")
    return report


def latest_journal(journal_dir: str) -> str | None:
    try:
        names = sorted(n for n in os.listdir(journal_dir) if n.endswith(".jsonl"))
    except OSError:
        return None
    return os.path.join(journal_dir, names[-1]) if names else None


def journal_summary(journal_path: str) -> tuple[str, int]:
    """(folder, liczba przeniesionych plików) zapisane w dzienniku."""
    root, count = "", 0
    with open(journal_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            root = entry.get("root", root)
            count += "src" in entry
    return root, count


# --------------------------------------------------------------------------------------
# Wyszukiwanie plików
# --------------------------------------------------------------------------------------

def is_output_dir(name: str) -> bool:
    """Folder utworzony przez program, np. 'Marcel Gawroński „tratwy”'."""
    return any(name.endswith(right) and f" {left}" in name for left, right in QUOTE_STYLES.values())


def find_audio_files(root: str, recursive: bool = False) -> list[str]:
    """Pliki audio w folderze (opcjonalnie w podfolderach, z pominięciem folderów osób)."""
    found = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not is_output_dir(nfc(d)))
        found.extend(os.path.join(directory, n) for n in filenames if is_audio_file(n))
        if not recursive:
            break
    return sorted(found, key=lambda p: natural_key(os.path.relpath(p, root)))

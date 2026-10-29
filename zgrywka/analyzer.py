"""Analiza jednego nagrania: transkrypcja początku i rozpoznanie osoby oraz nazwy odcinka."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .audio import AudioError, load_head
from .matching import (
    EpisodeMatch, MultiEpisodeMatcher, Person, PersonMatch, PersonMatcher, mask_tokens, near_positions, tokenize,
)


def build_hints(people: list[Person], episodes: list[str]) -> str:
    """Podpowiedź dla modelu: poprawna pisownia nazwisk i nazwy odcinka.

    Celowo kończy się neutralnym zdaniem. Gdy podpowiedź kończyła się nazwiskiem albo nazwą
    odcinka, a nagranie zaczynało się od tych samych słów, model potrafił je pominąć
    (traktował je jak tekst już zapisany).
    """
    parts = []
    episodes = [e.strip() for e in episodes if e.strip()]
    if episodes:
        parts.append(("Odcinki: " if len(episodes) > 1 else "Odcinek: ") + ", ".join(episodes) + ".")
    names = ", ".join(p.name for p in people)
    if names:
        parts.append(f"Osoby: {names}.")
    parts.append("Początek nagrania.")
    return " ".join(parts)


@dataclass
class FileAnalysis:
    path: str
    duration: float | None = None
    transcripts: list[str] = field(default_factory=list)
    hinted: bool = False  # czy pierwsza transkrypcja powstała z podpowiedzią
    person: PersonMatch = field(default_factory=PersonMatch)
    episode: EpisodeMatch = field(default_factory=EpisodeMatch)
    error: str = ""
    elapsed: float = 0.0

    @property
    def transcript(self) -> str:
        return " ‖ ".join(t for t in self.transcripts if t)


class Recognizer:
    """Decyzja "kto i jaki odcinek" na podstawie transkrypcji (bez ponownego słuchania nagrania)."""

    def __init__(self, people: list[Person], episodes: list[str]):
        self.persons = PersonMatcher(people)
        self.episodes = MultiEpisodeMatcher(episodes)

    def match(self, text: str) -> tuple[PersonMatch, EpisodeMatch]:
        tokens = tokenize(text)
        found = self.episodes.find_all(tokens)
        # Samo imię lub nazwisko z częścią liter przyjmujemy tylko tam, gdzie zwykle pada
        # przy przedstawianiu się: tuż przy nazwie odcinka (ale nie w niej samej).
        slots: set[int] = set()
        hidden: set[int] = set()
        for episode in found:
            slots |= near_positions(episode.position, len(tokens))
            hidden |= set(range(episode.position, episode.position + episode.length))
        slots -= hidden
        person = self.persons.match(mask_tokens(tokens, hidden) if hidden else tokens, slots)
        near = person.position if person.person else None
        if not found and near is not None:
            found = self.episodes.find_all(tokens, near=near)
        return person, self.episodes.choose(found)

    def first_pass(self, text: str, hinted: bool) -> tuple[PersonMatch, EpisodeMatch]:
        person, episode = self.match(text)
        if hinted and len(set(self.persons.full_mentions(text))) >= 2:
            # kilka osób z listy naraz - prawdopodobnie model "powtórzył" podpowiedź; nie ufamy temu
            return PersonMatch(), EpisodeMatch()
        return person, episode

    def decide(self, transcripts: list[str], hinted: bool) -> tuple[PersonMatch, EpisodeMatch]:
        if not transcripts:
            return PersonMatch(), EpisodeMatch()
        person, episode = self.first_pass(transcripts[0], hinted)
        for text in transcripts[1:]:
            person2, episode2 = self.match(text)
            person = _better_person(person, person2)
            if episode2.found and (not episode.found or episode2.score > episode.score):
                episode = episode2
        return person, episode


def assign_episode(recognized: str | None, episodes: list[str], person_only: bool) -> tuple[str | None, bool]:
    """Tryb "sortowanie tylko po imieniu i nazwisku": odcinek nie musi paść w nagraniu - bierzemy go
    z pola (rozpoznany, jeśli wpisano kilka i któryś padł, inaczej pierwszy). Zwraca (odcinek, z_pola)."""
    if recognized or not person_only or not episodes:
        return recognized, False
    return episodes[0], True


class Analyzer:
    def __init__(self, engine, people: list[Person], episodes: list[str], seconds: float = 30,
                 use_hints: bool = True, second_pass: bool = True, need_episode: bool = True):
        self.engine = engine
        self.recognizer = Recognizer(people, episodes)
        self.seconds = seconds
        self.hints = build_hints(people, episodes) if use_hints else None
        self.second_pass = second_pass
        self.need_episode = need_episode  # False w trybie "tylko po imieniu i nazwisku"

    def analyze(self, path: str) -> FileAnalysis:
        started = time.monotonic()
        result = FileAnalysis(path)
        try:
            audio, result.duration = load_head(path, self.seconds)
        except AudioError as exc:
            result.error = str(exc)
        except OSError as exc:
            result.error = f"błąd odczytu pliku: {exc.strerror or exc}"
        else:
            self._recognize(audio, result)
        result.elapsed = time.monotonic() - started
        return result

    def _recognize(self, audio, result: FileAnalysis) -> None:
        text = self.engine.transcribe(audio, hotwords=self.hints)
        result.transcripts.append(text)
        result.hinted = self.hints is not None
        person, episode = self.recognizer.first_pass(text, result.hinted)
        complete = person.person and (episode.found or not self.need_episode)
        if self.second_pass and not complete:
            # Druga próba: bez podpowiedzi i z czulszym wykrywaniem mowy.
            text2 = self.engine.transcribe(audio, hotwords=None, vad_threshold=0.35)
            if text2 and text2 != text:
                result.transcripts.append(text2)
        result.person, result.episode = self.recognizer.decide(result.transcripts, result.hinted)


def _better_person(a: PersonMatch, b: PersonMatch) -> PersonMatch:
    if not a.person:
        if not b.person and not b.ambiguous:
            return a if a.ambiguous else b
        return b
    if not b.person:
        return a
    best = a if a.rank >= b.rank else b
    if a.person != b.person:
        best.conflict = True
    return best

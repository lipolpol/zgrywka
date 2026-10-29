"""Tabela wyników: wiersz na nagranie, edycja osoby i odcinka (lista albo własny tekst)."""

from __future__ import annotations

import html
import os
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication, QComboBox, QCompleter, QStyle, QStyledItemDelegate

from .analyzer import FileAnalysis
from .matching import FIRST, FULL, LAST, PersonMatch
from .naming import Target, clean_episode
from .player import format_time

PENDING, RUNNING, DONE, ERROR, APPLIED = range(5)
COLUMNS = ["", "Plik", "Osoba", "Odcinek", "Nowa nazwa", "Rozpoznanie", "Co usłyszano na początku nagrania"]
COL_PLAY, COL_FILE, COL_PERSON, COL_EPISODE, COL_NEW, COL_HOW, COL_HEARD = range(len(COLUMNS))
NONE_PERSON = "— nie rozpoznano —"
NEW_PERSON = "＋ Dodaj nową osobę…"
NONE_EPISODE = "— brak —"

COLORS = {
    "move": QColor(46, 160, 67, 70),      # osoba + odcinek: do folderu odcinka i osoby
    "partial": QColor(230, 160, 0, 85),   # tylko osoba albo tylko odcinek: zmiana nazwy
    "none": QColor(220, 50, 50, 70),      # nic nie rozpoznano
    "error": QColor(128, 128, 128, 70),
    "running": QColor(30, 136, 229, 60),
}


@dataclass
class ResultRow:
    path: str                          # bieżące położenie pliku
    original: str                      # nazwa pliku przed porządkowaniem
    state: int = PENDING
    analysis: FileAnalysis | None = None
    person: str | None = None
    episode: str | None = None         # który odcinek (z pola "Nazwa odcinka" albo wpisany ręcznie)
    manual: bool = False               # poprawione ręcznie
    match: PersonMatch | None = None
    episode_heard: str = ""
    episode_loose: bool = False
    from_prior: bool = False           # część informacji z nazwy nadanej wcześniej przez program
    target: Target | None = None
    new_path: str = ""
    applied_from: str = ""

    @property
    def category(self) -> str | None:
        if self.state == ERROR:
            return "error"
        if self.state == RUNNING:
            return "running"
        if self.state not in (DONE, APPLIED):
            return None
        if self.person and self.episode:
            return "move"
        return "partial" if (self.person or self.episode) else "none"


def qt_tooltip(text: str) -> str:
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"


class ResultsModel(QAbstractTableModel):
    edited = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[ResultRow] = []
        self.people_names: list[str] = []
        self.episodes: list[str] = []
        self.playing_row: int | None = None
        self.playing = False
        style = QApplication.style()
        P = QStyle.StandardPixmap
        self._icons = {
            RUNNING: style.standardIcon(P.SP_BrowserReload),
            ERROR: style.standardIcon(P.SP_MessageBoxWarning),
            APPLIED: style.standardIcon(P.SP_DialogApplyButton),
        }
        self._play_icon = style.standardIcon(P.SP_MediaPlay)
        self._now_icon = style.standardIcon(P.SP_MediaVolume)
        self._folder_icon = style.standardIcon(P.SP_DirIcon)
        self._warn_icon = style.standardIcon(P.SP_MessageBoxInformation)

    # --- Qt API ---
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.ToolTipRole and section == COL_PLAY:
            return "Odsłuch"
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return str(section + 1)
        return None

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.rows[index.row()].state == DONE and index.column() in (COL_PERSON, COL_EPISODE):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = index.row()
        row, col = self.rows[r], index.column()
        finished = row.state in (DONE, APPLIED)
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_FILE:
                return row.original
            if col == COL_PERSON:
                return (row.person or "—") if finished else ""
            if col == COL_EPISODE:
                return (row.episode or "—") if finished else ""
            if col == COL_NEW:
                if row.state == APPLIED:
                    return os.path.basename(row.path)
                return os.path.basename(row.new_path) if row.new_path and finished else ""
            if col == COL_HOW:
                return self._how(row)
            if col == COL_HEARD:
                if row.state == ERROR and row.analysis:
                    return row.analysis.error
                return " ".join(row.analysis.transcript.split()) if row.analysis else ""
        elif role == Qt.ItemDataRole.BackgroundRole:
            return COLORS.get(row.category or "")
        elif role == Qt.ItemDataRole.DecorationRole:
            if col == COL_PLAY:
                return self._now_icon if (r == self.playing_row and self.playing) else self._play_icon
            if col == COL_FILE:
                return self._icons.get(row.state)
            if col == COL_NEW and row.target and row.target.move and finished:
                return self._folder_icon
            if col == COL_HOW and finished and not row.manual and (
                    (row.match and (row.match.conflict or row.match.loose)) or row.episode_loose):
                return self._warn_icon
        elif role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(row, col)
        elif role == Qt.ItemDataRole.FontRole and col in (COL_PERSON, COL_EPISODE) and row.manual:
            font = QFont()
            font.setItalic(True)
            return font
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        row = self.rows[index.row()]
        if row.state != DONE or role != Qt.ItemDataRole.EditRole:
            return False
        if index.column() == COL_PERSON:
            row.person = value or None
        elif index.column() == COL_EPISODE:
            row.episode = value or None
        else:
            return False
        row.manual = True
        self.row_changed(index.row())
        self.edited.emit()
        return True

    # --- pomocnicze ---
    def _how(self, row: ResultRow) -> str:
        if row.state == PENDING:
            return "czeka"
        if row.state == RUNNING:
            return "słucham…"
        if row.state == ERROR:
            return "błąd odczytu"
        if row.manual:
            return "poprawione ręcznie"
        m = row.match
        if m and m.person:
            kind = {FULL: "imię i nazwisko", FIRST: "samo imię", LAST: "samo nazwisko"}[m.kind]
            if m.loose:
                kind += " (część liter)"
            text = f"{kind}: „{m.heard}”" + (" – sprawdź" if m.conflict or m.loose else "")
        elif m and m.ambiguous:
            text = "pasuje do kilku: " + ", ".join(p.name for p in m.ambiguous)
        else:
            text = ""
        if row.episode_loose and "sprawdź" not in text:
            text = (text + " • " if text else "") + "odcinek z części liter – sprawdź"
        if row.from_prior:
            return f"{text} • reszta z wcześniejszej nazwy" if text else "z wcześniejszej nazwy pliku"
        return text or "nie rozpoznano osoby"

    def _tooltip(self, row: ResultRow, col: int) -> str | None:
        if col == COL_PLAY:
            return "Odsłuchaj to nagranie"
        if col == COL_FILE:
            lines = [row.path]
            if row.analysis and row.analysis.duration:
                lines.append(f"Długość: {format_time(row.analysis.duration)}")
            if row.applied_from:
                lines.append(f"Poprzednio: {row.applied_from}")
            return qt_tooltip("\n".join(lines))
        if col == COL_PERSON and row.state == DONE:
            return "Kliknij, aby wybrać osobę z listy albo wpisać własną"
        if col == COL_EPISODE:
            parts = []
            if row.episode_heard:
                parts.append(f"Usłyszano: „{row.episode_heard}”")
            if row.state == DONE:
                parts.append("Kliknij, aby wybrać odcinek albo wpisać własny")
            return qt_tooltip("\n".join(parts)) if parts else None
        if col == COL_NEW and (row.new_path or row.state == APPLIED):
            path = row.path if row.state == APPLIED else row.new_path
            where = "przeniesiono do" if row.state == APPLIED else "zostanie zapisany jako"
            return qt_tooltip(f"Plik {where}:\n{path}")
        if col == COL_HOW and row.match and row.match.conflict:
            return "W nagraniu padło kilka osób z listy – sprawdź, czy wybrano właściwą."
        if col == COL_HOW and ((row.match and row.match.loose) or row.episode_loose):
            return ("Zgadza się tylko część liter (np. zgubiona lub przekręcona litera) – "
                    "kliknij ▶, żeby to odsłuchać.")
        if col == COL_HOW and row.state == DONE:
            return "Kliknij, aby poprawić osobę"
        if col == COL_HEARD and row.analysis:
            parts = [t for t in row.analysis.transcripts if t]
            if len(parts) > 1:
                return qt_tooltip("1. próba (z podpowiedzią):\n" + parts[0] + "\n\n2. próba:\n" + parts[1])
            return qt_tooltip(parts[0]) if parts else (row.analysis.error or "Nic nie usłyszano")
        return None

    def row_changed(self, r: int) -> None:
        self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLUMNS) - 1))

    def set_files(self, paths: list[str]) -> None:
        self.beginResetModel()
        self.rows = [ResultRow(p, os.path.basename(p)) for p in paths]
        self.playing_row = None
        self.endResetModel()

    def set_state(self, r: int, state: int) -> None:
        self.rows[r].state = state
        self.row_changed(r)

    def set_playing(self, r: int | None, playing: bool) -> None:
        old = self.playing_row
        self.playing_row, self.playing = r, playing
        for i in {old, r} - {None}:
            if i < len(self.rows):
                self.dataChanged.emit(self.index(i, COL_PLAY), self.index(i, COL_PLAY))

    def apply_decision(self, r: int, match: PersonMatch, person: str | None, episode: str | None,
                       episode_heard: str, episode_loose: bool, from_prior: bool) -> None:
        row = self.rows[r]
        row.match, row.person, row.episode = match, person, episode
        row.episode_heard, row.episode_loose, row.from_prior = episode_heard, episode_loose, from_prior
        self.row_changed(r)

    def refresh(self) -> None:
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(COLUMNS) - 1))


class _ChoiceDelegate(QStyledItemDelegate):
    """Lista rozwijana, w której można też wpisać własny tekst."""

    def _choices(self) -> list[tuple[str, str]]:
        raise NotImplementedError

    def _current(self, index) -> str:
        raise NotImplementedError

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for text, data in self._choices():
            combo.addItem(text, data)
        completer = QCompleter([t for t, d in self._choices() if d and not d.startswith("\x00")], combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        combo.setCompleter(completer)

        def commit(*_):
            self.commitData.emit(combo)
            self.closeEditor.emit(combo)

        combo.activated.connect(commit)
        combo.lineEdit().returnPressed.connect(commit)
        return combo

    def setEditorData(self, editor, index):
        current = self._current(index)
        i = editor.findData(current) if current else 0
        if i >= 0:
            editor.setCurrentIndex(i)
        else:
            editor.setEditText(current)
        editor.lineEdit().selectAll()
        QTimer.singleShot(0, editor.showPopup)

    @staticmethod
    def _picked(editor) -> tuple[str, str | None]:
        """(wpisany tekst, dane wybranej pozycji albo None, gdy tekst wpisano ręcznie)."""
        text = " ".join(editor.currentText().split())
        i = editor.currentIndex()
        if i >= 0 and editor.itemText(i) == text:
            return text, editor.itemData(i)
        return text, None


class PersonDelegate(_ChoiceDelegate):
    """Kolumna "Osoba": wybór z listy osób, własny tekst albo dodanie nowej osoby."""
    NEW = "\x00nowa"

    def __init__(self, model: ResultsModel, ask_new_person, ensure_person, parent=None):
        super().__init__(parent)
        self.model = model
        self.ask_new_person = ask_new_person   # okno dodawania osoby -> nazwa albo None
        self.ensure_person = ensure_person     # wpisana nazwa -> osoba z listy (dopisana, jeśli jej nie było)

    def _choices(self):
        return [(NONE_PERSON, ""), *((n, n) for n in self.model.people_names), (NEW_PERSON, self.NEW)]

    def _current(self, index):
        return self.model.rows[index.row()].person or ""

    def setModelData(self, editor, model, index):
        text, data = self._picked(editor)
        if data == self.NEW:
            value = self.ask_new_person(editor.window())
            if value is None:
                return
        elif data is not None:
            value = data or None
        elif not text or text == NONE_PERSON:
            value = None
        else:
            value = self.ensure_person(text)
        model.setData(index, value, Qt.ItemDataRole.EditRole)


class EpisodeDelegate(_ChoiceDelegate):
    """Kolumna "Odcinek": wybór spośród wpisanych odcinków albo własna nazwa."""

    def __init__(self, model: ResultsModel, parent=None):
        super().__init__(parent)
        self.model = model

    def _choices(self):
        return [(NONE_EPISODE, ""), *((e, e) for e in self.model.episodes)]

    def _current(self, index):
        return self.model.rows[index.row()].episode or ""

    def setModelData(self, editor, model, index):
        text, data = self._picked(editor)
        if data is not None:
            value = data or None
        elif not text or text == NONE_EPISODE:
            value = None
        else:
            text = clean_episode(text)
            value = next((e for e in self.model.episodes if e.casefold() == text.casefold()), text)
        model.setData(index, value, Qt.ItemDataRole.EditRole)

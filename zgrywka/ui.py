"""Okno programu: przeciągnij folder, wpisz nazwę odcinka, sprawdź i uporządkuj."""

from __future__ import annotations

import csv
import html
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QProcess, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QProgressBar, QProgressDialog, QPushButton, QSpinBox, QSplitter, QStyle, QStyledItemDelegate,
    QTableView, QTabWidget, QVBoxLayout, QWidget,
)

from . import config
from .analyzer import FileAnalysis, Recognizer
from .asr import GPU_PACKAGES, MODELS, Engine, cuda_device_count, local_model_path, nvidia_libs_installed
from .matching import FIRST, FULL, LAST, PersonMatch
from .naming import (
    ASCII_QUOTES_ALLOWED, NO_EPISODE_TEXT, NO_PERSON_PREFIX, UNKNOWN_LABEL, Target, apply_moves, clean_episode,
    find_audio_files, journal_summary, latest_journal, make_target, merge_prior, parse_prior_label, plan_moves,
    undo_journal,
)
from .worker import AnalysisWorker

log = logging.getLogger(__name__)

PENDING, RUNNING, DONE, ERROR, APPLIED = range(5)
COLUMNS = ["Plik", "Osoba", "Odcinek", "Nowa nazwa", "Rozpoznanie", "Co usłyszano na początku nagrania"]
COL_FILE, COL_PERSON, COL_EPISODE, COL_NEW, COL_HOW, COL_HEARD = range(len(COLUMNS))
NONE_PERSON = "— nie rozpoznano —"
NEW_PERSON = "＋ Dodaj nową osobę…"

COLORS = {
    "move": QColor(46, 160, 67, 70),      # osoba + odcinek: do folderu osoby
    "partial": QColor(230, 160, 0, 85),   # tylko osoba albo tylko odcinek: zmiana nazwy
    "none": QColor(220, 50, 50, 70),      # nic nie rozpoznano
    "error": QColor(128, 128, 128, 70),
    "running": QColor(30, 136, 229, 60),
}

HELP_TEXT = f"""
<h3>Jak działa porządkowanie</h3>
<p>Program słucha początku każdego nagrania (domyślnie 30 sekund, można zmienić w Ustawieniach),
zamienia mowę na tekst i szuka w nim osób z listy oraz nazwy odcinka. Porównuje litery,
więc nie trzeba wpisywać żadnych „innych form”:</p>
<ul>
<li>przekręcone litery i polska wymowa: „Radek <b>H</b>aberko” = Radek Chaberko,
  „Weronika Hrabążcz” = Weronika Chrabąszcz;</li>
<li>zgubione albo dodane litery: „<b>adek</b>” = Radek, „Marcel <b>Woński</b>” = Marcel Gawroński;</li>
<li>odmiana i zdrobnienia: „mikroport Marcela Gawrońskiego”, „Radka”, „tratwa” = „tratwy”;</li>
<li>imię i nazwisko obok siebie wystarczą, gdy zgadza się mniej więcej co druga litera;</li>
<li>samo imię albo samo nazwisko też wystarczy, jeśli nikt inny z listy go nie ma.</li>
</ul>
<p>Żeby nie było przypadkowych trafień, luźniejsze dopasowanie (tylko część liter) program
przyjmuje wyłącznie tam, gdzie zwykle pada nazwisko: na początku nagrania i tuż obok nazwy
odcinka. Nie bierze też zwykłych słów („miał” to nie Michał, „radę” to nie Radek).
Takie wiersze są oznaczone dopiskiem <i>„część liter – sprawdź”</i>.</p>
<table cellpadding="4">
<tr><td><b>osoba + odcinek</b></td><td>plik trafia do folderu <i>Marcel Gawroński „tratwy”</i>
  i dostaje nazwę <i>Marcel Gawroński „tratwy” 1</i>, <i>… 2</i> itd.</td></tr>
<tr><td><b>sama osoba</b></td><td>tylko zmiana nazwy: <i>Marcel Gawroński „{NO_EPISODE_TEXT}” 1</i></td></tr>
<tr><td><b>sam odcinek</b></td><td>tylko zmiana nazwy: <i>{NO_PERSON_PREFIX} „tratwy” 1</i></td></tr>
<tr><td><b>nic</b></td><td>tylko zmiana nazwy: <i>{UNKNOWN_LABEL} 1</i></td></tr>
</table>
<p>Osoby dodajesz i poprawiasz w panelu „Osoby” po prawej (dwuklik zmienia nazwę) albo
w tabeli: kliknij komórkę w kolumnie „Osoba” i wybierz „Dodaj nową osobę…”.
Odcinek możesz zaznaczyć lub odznaczyć ręcznie. Dwuklik na wierszu odtwarza nagranie.
Każde porządkowanie można cofnąć przyciskiem „Cofnij ostatnie”.</p>
<p>Numeracja kontynuuje istniejące pliki, więc kolejne zgrywki do tego samego folderu
dostaną dalsze numery. Program nigdy nie nadpisuje istniejących plików.</p>
"""


# --------------------------------------------------------------------------------------
# Tabela wyników
# --------------------------------------------------------------------------------------

@dataclass
class ResultRow:
    path: str                          # bieżące położenie pliku
    original: str                      # nazwa pliku przed porządkowaniem
    state: int = PENDING
    analysis: FileAnalysis | None = None
    person: str | None = None
    episode_found: bool = False
    manual: bool = False               # poprawione ręcznie
    match: PersonMatch | None = None
    episode_heard: str = ""
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
        if self.person and self.episode_found:
            return "move"
        return "partial" if (self.person or self.episode_found) else "none"


def _qt_tooltip(text: str) -> str:
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"


class ResultsModel(QAbstractTableModel):
    edited = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[ResultRow] = []
        self.people_names: list[str] = []
        style = QApplication.style()
        self._icons = {
            RUNNING: style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
            ERROR: style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning),
            APPLIED: style.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton),
        }
        self._folder_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._warn_icon = style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)

    # --- Qt API ---
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return str(section + 1)
        return None

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        row = self.rows[index.row()]
        if row.state == DONE:
            if index.column() == COL_PERSON:
                flags |= Qt.ItemFlag.ItemIsEditable
            elif index.column() == COL_EPISODE:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = self.rows[index.row()], index.column()
        finished = row.state in (DONE, APPLIED)
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_FILE:
                return row.original
            if col == COL_PERSON:
                return (row.person or "—") if finished else ""
            if col == COL_EPISODE:
                return ("tak" if row.episode_found else "nie") if finished else ""
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
        elif role == Qt.ItemDataRole.CheckStateRole and col == COL_EPISODE and finished:
            return Qt.CheckState.Checked if row.episode_found else Qt.CheckState.Unchecked
        elif role == Qt.ItemDataRole.BackgroundRole:
            color = COLORS.get(row.category or "")
            return color
        elif role == Qt.ItemDataRole.DecorationRole:
            if col == COL_FILE:
                return self._icons.get(row.state)
            if col == COL_NEW and row.target and row.target.move and finished:
                return self._folder_icon
            if col == COL_HOW and row.match and (row.match.conflict or row.match.loose) and not row.manual \
                    and finished:
                return self._warn_icon
        elif role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(row, col)
        elif role == Qt.ItemDataRole.FontRole and col == COL_PERSON and row.manual:
            font = QFont()
            font.setItalic(True)
            return font
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        row = self.rows[index.row()]
        if row.state != DONE:
            return False
        if index.column() == COL_PERSON and role == Qt.ItemDataRole.EditRole:
            row.person = value or None
        elif index.column() == COL_EPISODE and role == Qt.ItemDataRole.CheckStateRole:
            row.episode_found = Qt.CheckState(value) == Qt.CheckState.Checked
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
        if row.from_prior:
            return f"{text} • reszta z wcześniejszej nazwy" if text else "z wcześniejszej nazwy pliku"
        return text or "nie rozpoznano osoby"

    def _tooltip(self, row: ResultRow, col: int) -> str | None:
        if col == COL_FILE:
            lines = [row.path]
            if row.analysis and row.analysis.duration:
                lines.append(f"Długość: {_format_duration(row.analysis.duration)}")
            if row.applied_from:
                lines.append(f"Poprzednio: {row.applied_from}")
            return _qt_tooltip("\n".join(lines))
        if col == COL_PERSON and row.state == DONE:
            return "Kliknij, aby wybrać inną osobę"
        if col == COL_EPISODE and row.episode_heard:
            return _qt_tooltip(f"Usłyszano: „{row.episode_heard}”")
        if col == COL_NEW and (row.new_path or row.state == APPLIED):
            path = row.path if row.state == APPLIED else row.new_path
            where = "przeniesiono do" if row.state == APPLIED else "zostanie zapisany jako"
            return _qt_tooltip(f"Plik {where}:\n{path}")
        if col == COL_HOW and row.match and row.match.conflict:
            return "W nagraniu padło kilka osób z listy – sprawdź, czy wybrano właściwą."
        if col == COL_HOW and row.match and row.match.loose:
            return ("Zgadza się tylko część liter (np. zgubiona lub przekręcona litera) – "
                    "dwuklik odtworzy nagranie, żeby to sprawdzić.")
        if col == COL_HEARD and row.analysis:
            parts = [t for t in row.analysis.transcripts if t]
            if len(parts) > 1:
                return _qt_tooltip("1. próba (z podpowiedzią):\n" + parts[0] + "\n\n2. próba:\n" + parts[1])
            return _qt_tooltip(parts[0]) if parts else (row.analysis.error or "Nic nie usłyszano")
        return None

    def row_changed(self, r: int) -> None:
        self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLUMNS) - 1))

    def set_files(self, paths: list[str]) -> None:
        self.beginResetModel()
        self.rows = [ResultRow(p, os.path.basename(p)) for p in paths]
        self.endResetModel()

    def set_state(self, r: int, state: int) -> None:
        self.rows[r].state = state
        self.row_changed(r)

    def apply_decision(self, r: int, match: PersonMatch, person: str | None, episode_found: bool,
                       episode_heard: str, from_prior: bool) -> None:
        row = self.rows[r]
        row.match, row.person, row.episode_found = match, person, episode_found
        row.episode_heard, row.from_prior = episode_heard, from_prior
        self.row_changed(r)

    def refresh(self) -> None:
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(COLUMNS) - 1))


class PersonDelegate(QStyledItemDelegate):
    """Lista rozwijana z osobami w kolumnie "Osoba" (z możliwością dodania nowej osoby)."""
    NEW = "\x00nowa"

    def __init__(self, model: ResultsModel, ask_new_person, parent=None):
        super().__init__(parent)
        self.model = model
        self.ask_new_person = ask_new_person

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItem(NONE_PERSON, "")
        for name in self.model.people_names:
            combo.addItem(name, name)
        combo.addItem(NEW_PERSON, self.NEW)
        combo.activated.connect(lambda _i, c=combo: (self.commitData.emit(c), self.closeEditor.emit(c)))
        return combo

    def setEditorData(self, editor, index):
        current = self.model.rows[index.row()].person or ""
        i = editor.findData(current)
        if i < 0 and current:
            editor.addItem(current, current)
            i = editor.count() - 1
        editor.setCurrentIndex(max(0, i))
        QTimer.singleShot(0, editor.showPopup)

    def setModelData(self, editor, model, index):
        value = editor.currentData()
        if value == self.NEW:
            value = self.ask_new_person(editor.window())
            if value is None:
                return
        model.setData(index, value, Qt.ItemDataRole.EditRole)


# --------------------------------------------------------------------------------------
# Elementy okna
# --------------------------------------------------------------------------------------

def _dropped_folder(mime) -> str | None:
    for url in mime.urls():
        if url.isLocalFile():
            path = url.toLocalFile()
            return path if os.path.isdir(path) else os.path.dirname(path)
    return None


class DropZone(QFrame):
    folder_dropped = Signal(str)
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(110)
        self._set_hover(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("Przeciągnij tutaj folder z nagraniami")
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.35)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("albo kliknij tutaj, aby go wybrać")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.path_label = QLabel("")
        self.path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for w in (title, hint, self.path_label):
            layout.addWidget(w)

    def _set_hover(self, hover: bool) -> None:
        color = "palette(highlight)" if hover else "palette(mid)"
        self.setStyleSheet(
            f"#dropZone {{ border: 2px dashed {color}; border-radius: 10px; "
            f"background: {'palette(alternate-base)' if hover else 'palette(base)'}; }}")

    def dragEnterEvent(self, event):
        if _dropped_folder(event.mimeData()):
            event.acceptProposedAction()
            self._set_hover(True)

    def dragLeaveEvent(self, event):
        self._set_hover(False)

    def dropEvent(self, event):
        self._set_hover(False)
        folder = _dropped_folder(event.mimeData())
        if folder:
            event.acceptProposedAction()
            self.folder_dropped.emit(folder)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class PeoplePanel(QWidget):
    """Lista osób w głównym oknie: dodawanie, poprawianie (dwuklik) i usuwanie."""
    changed = Signal()

    def __init__(self, settings: config.Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 0, 0)
        self.title = QLabel()
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.15)
        font.setBold(True)
        self.title.setFont(font)
        layout.addWidget(self.title)
        hint = QLabel("Tak jak tu wpiszesz, tak będą nazwane foldery i pliki. Przekręcone "
                      "i zgubione litery program wyłapie sam. Dwuklik poprawia nazwę.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                  | QAbstractItemView.EditTrigger.EditKeyPressed)
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        style = self.style()
        add = QPushButton(style.standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder), " Dodaj osobę")
        add.clicked.connect(lambda: self.ask_new_person())
        self.remove_button = QPushButton(style.standardIcon(QStyle.StandardPixmap.SP_TrashIcon), " Usuń")
        self.remove_button.clicked.connect(self.remove_selected)
        buttons.addWidget(add, 1)
        buttons.addWidget(self.remove_button)
        layout.addLayout(buttons)
        self._load(settings.people)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.itemSelectionChanged.connect(
            lambda: self.remove_button.setEnabled(bool(self.list.selectedItems())))
        self.remove_button.setEnabled(False)

    def _load(self, names: list[str]) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for name in names:
            self._append(name)
        self.list.blockSignals(False)
        self._update_title()

    def _append(self, name: str) -> QListWidgetItem:
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.list.addItem(item)
        return item

    def names(self) -> list[str]:
        return config.clean_people(self.list.item(i).text() for i in range(self.list.count()))

    def _update_title(self) -> None:
        self.title.setText(f"Osoby ({self.list.count()})")

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        name = " ".join(item.text().split())
        others = [self.list.item(i).text().casefold() for i in range(self.list.count())
                  if self.list.item(i) is not item]
        if not name or name.casefold() in others:
            # puste albo powtórzone - przywracamy poprzednią listę
            self.list.blockSignals(True)
            item.setText(self.settings.people[self.list.row(item)] if self.list.row(item) < len(self.settings.people)
                         else "")
            self.list.blockSignals(False)
            if not item.text():
                self.list.takeItem(self.list.row(item))
            if name:
                QMessageBox.information(self, config.APP_NAME, f"Osoba „{name}” już jest na liście.")
        elif name != item.text():
            self.list.blockSignals(True)
            item.setText(name)
            self.list.blockSignals(False)
        self._save()

    def _save(self) -> None:
        self.settings.people = self.names()
        self._update_title()
        self.changed.emit()

    def ask_new_person(self, parent: QWidget | None = None) -> str | None:
        """Pyta o imię i nazwisko nowej osoby i dodaje ją do listy. Zwraca nazwę albo None."""
        name, ok = QInputDialog.getText(parent or self, config.APP_NAME,
                                        "Imię i nazwisko nowej osoby (tak będą nazwane foldery i pliki):")
        name = " ".join(name.split())
        if not ok or not name:
            return None
        existing = {n.casefold(): n for n in self.names()}
        if name.casefold() in existing:
            return existing[name.casefold()]
        self.list.blockSignals(True)
        self.list.setCurrentItem(self._append(name))
        self.list.blockSignals(False)
        self.remove_button.setEnabled(True)
        self._save()
        return name

    def remove_selected(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        names = ", ".join(i.text() for i in items)
        answer = QMessageBox.question(self, config.APP_NAME, f"Usunąć z listy: {names}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        for item in items:
            self.list.takeItem(self.list.row(item))
        self._save()


class SettingsTab(QWidget):
    engine_settings_changed = Signal()
    matching_settings_changed = Signal()

    def __init__(self, settings: config.Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        outer = QVBoxLayout(self)

        recog = QGroupBox("Rozpoznawanie mowy")
        form = QFormLayout(recog)
        self.model_combo = QComboBox()
        for key, info in MODELS.items():
            self.model_combo.addItem(info.label, key)
        self.model_combo.setCurrentIndex(max(0, self.model_combo.findData(settings.model)))
        self.model_combo.currentIndexChanged.connect(self._on_engine_change)
        self.model_status = QLabel()
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.model_status)
        form.addRow("Model:", model_row)

        self.device_combo = QComboBox()
        for key, label in (("auto", "Automatycznie (karta graficzna NVIDIA, jeśli działa)"),
                           ("cpu", "Zawsze procesor (CPU)"),
                           ("cuda", "Karta graficzna NVIDIA (CUDA)")):
            self.device_combo.addItem(label, key)
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(settings.device)))
        self.device_combo.currentIndexChanged.connect(self._on_engine_change)
        if sys.platform == "darwin":
            self.device_combo.setEnabled(False)
            self.device_combo.setToolTip("Na komputerach Mac rozpoznawanie działa na procesorze.")
        form.addRow("Urządzenie:", self.device_combo)

        self.seconds_spin = QSpinBox()
        self.seconds_spin.setRange(5, 600)
        self.seconds_spin.setSuffix(" s")
        self.seconds_spin.setValue(settings.seconds)
        self.seconds_spin.setMaximumWidth(110)
        self.seconds_spin.valueChanged.connect(self._on_other_change)
        form.addRow("Analizuj początek nagrania:", self.seconds_spin)

        self.hints_check = QCheckBox("Podpowiadaj modelowi pisownię nazwisk i nazwę odcinka (zalecane)")
        self.second_check = QCheckBox("Druga próba, gdy czegoś nie rozpoznano (dokładniej, trochę wolniej)")
        form.addRow(self.hints_check)
        form.addRow(self.second_check)
        outer.addWidget(recog)

        files = QGroupBox("Pliki")
        form2 = QFormLayout(files)
        self.recursive_check = QCheckBox("Szukaj nagrań także w podfolderach")
        self.auto_check = QCheckBox("Porządkuj pliki od razu po analizie (bez podglądu)")
        self.report_check = QCheckBox("Po uporządkowaniu zapisz w folderze raport CSV (stare → nowe nazwy)")
        for check in (self.recursive_check, self.auto_check, self.report_check):
            form2.addRow(check)
        self.quote_combo = QComboBox()
        self.quote_combo.addItem("„tratwy”  (polskie – działa wszędzie, zalecane)", "pl")
        if ASCII_QUOTES_ALLOWED:
            self.quote_combo.addItem('"tratwy"  (proste – nie działa na Windows i dyskach exFAT/FAT32)', "ascii")
        self.quote_combo.setCurrentIndex(max(0, self.quote_combo.findData(settings.quote_style)))
        self.quote_combo.currentIndexChanged.connect(self._on_other_change)
        form2.addRow("Cudzysłów w nazwach:", self.quote_combo)
        outer.addWidget(files)

        for check, attr in ((self.hints_check, "use_hints"), (self.second_check, "second_pass"),
                            (self.recursive_check, "recursive"), (self.auto_check, "auto_apply"),
                            (self.report_check, "save_report")):
            check.setChecked(getattr(settings, attr))
            check.toggled.connect(self._on_other_change)

        self.gpu_box = QGroupBox("Karta graficzna")
        gpu_layout = QHBoxLayout(self.gpu_box)
        self.gpu_label = QLabel("Sprawdzanie…")
        self.gpu_label.setWordWrap(True)
        self.gpu_button = QPushButton("Zainstaluj przyspieszenie GPU")
        self.gpu_button.setVisible(False)
        self.gpu_button.clicked.connect(self.install_gpu)
        gpu_layout.addWidget(self.gpu_label, 1)
        gpu_layout.addWidget(self.gpu_button)
        outer.addWidget(self.gpu_box)
        self.gpu_box.setVisible(sys.platform != "darwin")

        paths = QGroupBox("Foldery programu")
        form3 = QFormLayout(paths)
        for label, path in (("Modele mowy:", config.models_dir()), ("Ustawienia i historia:", config.user_data_dir())):
            row = QHBoxLayout()
            field = QLineEdit(path)
            field.setReadOnly(True)
            button = QPushButton("Otwórz")
            button.clicked.connect(lambda _=False, p=path: open_path(p))
            row.addWidget(field, 1)
            row.addWidget(button)
            form3.addRow(label, row)
        outer.addWidget(paths)
        outer.addStretch(1)
        self.refresh_model_status()
        QTimer.singleShot(300, self.refresh_gpu_status)

    def _on_engine_change(self) -> None:
        self.settings.model = self.model_combo.currentData()
        self.settings.device = self.device_combo.currentData()
        self.refresh_model_status()
        self.engine_settings_changed.emit()

    def _on_other_change(self) -> None:
        s = self.settings
        s.seconds = self.seconds_spin.value()
        s.use_hints = self.hints_check.isChecked()
        s.second_pass = self.second_check.isChecked()
        s.recursive = self.recursive_check.isChecked()
        s.auto_apply = self.auto_check.isChecked()
        s.save_report = self.report_check.isChecked()
        s.quote_style = self.quote_combo.currentData()
        self.matching_settings_changed.emit()

    def refresh_model_status(self) -> None:
        key = self.model_combo.currentData()
        downloaded = local_model_path(key, config.models_dir()) is not None
        self.model_status.setText("pobrany ✓" if downloaded else
                                  f"pobierze się przy pierwszym użyciu (~{MODELS[key].size_mb / 1000:.1f} GB)")

    def refresh_gpu_status(self) -> None:
        if sys.platform == "darwin":
            return
        count = cuda_device_count()
        frozen = getattr(sys, "frozen", False)
        if count == 0:
            self.gpu_label.setText("Nie wykryto karty NVIDIA – rozpoznawanie działa na procesorze.")
        elif nvidia_libs_installed():
            self.gpu_label.setText("Wykryto kartę NVIDIA i biblioteki CUDA – przy ustawieniu "
                                   "„Automatycznie” program użyje karty graficznej.")
        else:
            self.gpu_label.setText(
                "Wykryto kartę NVIDIA, ale brakuje bibliotek CUDA, więc program używa procesora. "
                "Doinstalowanie ich (ok. 2,5 GB) przyspieszy rozpoznawanie wielokrotnie.")
            self.gpu_button.setVisible(not frozen)

    def install_gpu(self) -> None:
        import shutil

        free = shutil.disk_usage(sys.prefix).free
        if free < 3.5e9:
            QMessageBox.warning(self, config.APP_NAME,
                                "Za mało miejsca na dysku: biblioteki CUDA zajmują ok. 2,5 GB, a wolne jest "
                                f"{free / 1e9:.1f} GB. Zwolnij miejsce (najlepiej min. 3,5 GB) i spróbuj ponownie.")
            return
        answer = QMessageBox.question(
            self, config.APP_NAME,
            "Pobrać i zainstalować biblioteki NVIDIA CUDA (cuBLAS i cuDNN, ok. 2,5 GB)?\n\n"
            "Potrzebny jest internet. Po instalacji uruchom program ponownie.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        dialog = QProgressDialog("Instalowanie bibliotek CUDA…", "Przerwij", 0, 0, self)
        dialog.setWindowTitle(config.APP_NAME)
        dialog.setMinimumWidth(460)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        output: list[str] = []

        def on_output():
            text = bytes(process.readAllStandardOutput()).decode(errors="replace")
            output.append(text)
            last = [line for line in text.splitlines() if line.strip()]
            if last:
                dialog.setLabelText("Instalowanie bibliotek CUDA…\n" + last[-1][:90])

        def on_finished(code, _status):
            dialog.reset()
            if code == 0:
                QMessageBox.information(self, config.APP_NAME,
                                        "Zainstalowano. Uruchom program ponownie, aby używać karty graficznej.")
                self.gpu_button.setVisible(False)
                self.gpu_label.setText("Biblioteki CUDA zainstalowane – uruchom program ponownie.")
            else:
                log.error("Instalacja GPU nie powiodła się:\n%s", "".join(output))
                QMessageBox.warning(self, config.APP_NAME, "Instalacja nie powiodła się.\n\n" + "".join(output)[-1500:])

        process.readyReadStandardOutput.connect(on_output)
        process.finished.connect(on_finished)
        dialog.canceled.connect(process.kill)
        process.start(sys.executable, ["-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                                       *GPU_PACKAGES])
        dialog.show()


def open_path(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def reveal_in_folder(path: str) -> None:
    """Pokazuje plik w menedżerze plików (zaznaczony, jeśli system na to pozwala)."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
            return
        uri = QUrl.fromLocalFile(path).toString()
        result = subprocess.run(
            ["dbus-send", "--session", "--dest=org.freedesktop.FileManager1", "--type=method_call",
             "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
             f"array:string:{uri}", "string:"], capture_output=True, timeout=5)
        if result.returncode == 0:
            return
    except (OSError, subprocess.SubprocessError):
        pass
    open_path(os.path.dirname(path))


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{max(1, int(seconds))} s"
    return f"{int(seconds // 60)} min {int(seconds % 60):02d} s"


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64, 128, 256):
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(37, 99, 235))
        p.drawRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
        p.setBrush(QColor(255, 255, 255))
        bars = (0.35, 0.7, 0.5, 0.85, 0.45)
        w = size / 11
        start = (size - (len(bars) - 1) * w * 1.6 - w) / 2
        for i, h in enumerate(bars):
            x = start + i * w * 1.6
            p.drawRoundedRect(int(x), int(size * (0.5 - h / 2.6)), max(1, int(w)), int(size * h / 1.3), w / 2, w / 2)
        p.end()
        icon.addPixmap(pix)
    return icon


# --------------------------------------------------------------------------------------
# Okno główne
# --------------------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, settings: config.Settings):
        super().__init__()
        self.settings = settings
        self.engine: Engine | None = None
        self.worker: AnalysisWorker | None = None
        self.folder = ""
        self.cancel_requested = False
        self.closing = False
        self.engine_loading = False
        self.setWindowTitle(f"{config.APP_NAME} – porządkowanie nagrań z mikroportów")
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)

        self.results = ResultsModel(self)
        self.results.edited.connect(self.recompute_plan)

        self.people_panel = PeoplePanel(settings)
        self.people_panel.changed.connect(self.on_people_changed)
        tabs = QTabWidget()
        tabs.addTab(self._build_main_tab(), "Zgrywka")
        self.settings_tab = SettingsTab(settings)
        self.settings_tab.engine_settings_changed.connect(self.on_engine_settings_changed)
        self.settings_tab.matching_settings_changed.connect(self.on_matching_settings_changed)
        tabs.addTab(self.settings_tab, "Ustawienia")
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("Gotowy. Wpisz nazwę odcinka i przeciągnij folder z nagraniami.")

        self._save_timer = QTimer(self, singleShot=True, interval=500, timeout=self.save_settings)
        self._rematch_timer = QTimer(self, singleShot=True, interval=350, timeout=self.rematch)
        self.on_people_changed(save=False)
        self.update_buttons()
        if settings.window_geometry:
            self.restoreGeometry(bytes.fromhex(settings.window_geometry))
        else:
            self.resize(1280, 780)

    # --- budowa interfejsu ---
    def _build_main_tab(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 0)

        top = QHBoxLayout()
        label = QLabel("Nazwa odcinka:")
        big = label.font()
        big.setPointSizeF(big.pointSizeF() * 1.3)
        label.setFont(big)
        self.episode_edit = QLineEdit(self.settings.episode)
        self.episode_edit.setPlaceholderText("np. tratwy")
        self.episode_edit.setFont(big)
        self.episode_edit.setClearButtonEnabled(True)
        self.episode_edit.setToolTip("Nazwa odcinka, którą mówi się na początku nagrania. "
                                     "Trafi do nazw folderów i plików.")
        self.episode_edit.textChanged.connect(self.on_episode_changed)
        help_button = QPushButton("Jak to działa?")
        help_button.clicked.connect(lambda: QMessageBox.information(self, config.APP_NAME, HELP_TEXT))
        top.addWidget(label)
        top.addWidget(self.episode_edit, 1)
        top.addWidget(help_button)
        layout.addLayout(top)

        self.drop_zone = DropZone()
        self.drop_zone.folder_dropped.connect(lambda p: self.open_folder(p, autostart=True))
        self.drop_zone.clicked.connect(self.choose_folder)
        layout.addWidget(self.drop_zone)

        buttons = QHBoxLayout()
        style = self.style()
        self.start_button = QPushButton(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay), " Rozpoznaj nagrania")
        self.start_button.clicked.connect(lambda: self.start_analysis())
        self.stop_button = QPushButton(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop), " Zatrzymaj")
        self.stop_button.clicked.connect(self.stop_analysis)
        self.apply_button = QPushButton(style.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton),
                                        " Uporządkuj pliki")
        self.apply_button.setToolTip("Utwórz foldery, przenieś i ponumeruj pliki zgodnie z kolumną „Nowa nazwa”")
        self.apply_button.clicked.connect(self.apply)
        self.undo_button = QPushButton(style.standardIcon(QStyle.StandardPixmap.SP_ArrowBack), " Cofnij ostatnie")
        self.undo_button.clicked.connect(self.undo_last)
        for b in (self.start_button, self.stop_button, self.apply_button, self.undo_button):
            b.setIconSize(QSize(18, 18))
            b.setMinimumHeight(34)
        font = self.apply_button.font()
        font.setBold(True)
        self.apply_button.setFont(font)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.undo_button)
        layout.addLayout(buttons)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        self.progress_label = QLabel("")
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)

        self.table = QTableView()
        self.table.setModel(self.results)
        self.table.setItemDelegateForColumn(
            COL_PERSON, PersonDelegate(self.results, self.people_panel.ask_new_person, self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.SelectedClicked
                                   | QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.doubleClicked.connect(self.on_double_click)
        self.table.clicked.connect(self.on_click)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for col, width in ((COL_FILE, 190), (COL_PERSON, 180), (COL_EPISODE, 80), (COL_NEW, 330), (COL_HOW, 250)):
            self.table.setColumnWidth(col, width)
        self.table.verticalHeader().setDefaultSectionSize(26)
        layout.addWidget(self.table, 1)

        self.summary_label = QLabel("")
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.summary_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(content)
        splitter.addWidget(self.people_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1000, 260])
        splitter.setChildrenCollapsible(False)
        page = QWidget()
        QVBoxLayout(page).addWidget(splitter)
        return page

    # --- przeciąganie na całe okno ---
    def dragEnterEvent(self, event):
        if _dropped_folder(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        folder = _dropped_folder(event.mimeData())
        if folder:
            event.acceptProposedAction()
            self.open_folder(folder, autostart=True)

    # --- stan przycisków ---
    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def update_buttons(self) -> None:
        busy = self.busy
        has_rows = bool(self.results.rows)
        self.start_button.setEnabled(not busy and has_rows)
        self.stop_button.setEnabled(busy)
        self.apply_button.setEnabled(not busy and self._has_pending_changes())
        self.undo_button.setEnabled(not busy and latest_journal(config.journal_dir()) is not None)
        self.drop_zone.setEnabled(not busy)
        self.settings_tab.model_combo.setEnabled(not busy)
        self.settings_tab.device_combo.setEnabled(not busy and sys.platform != "darwin")

    def _has_pending_changes(self) -> bool:
        return any(r.state == DONE and r.new_path and not _same(r.path, r.new_path) for r in self.results.rows)

    # --- folder ---
    def choose_folder(self) -> None:
        if self.busy:
            return
        start = self.folder or self.settings.last_folder or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Wybierz folder z nagraniami", start)
        if folder:
            self.open_folder(folder, autostart=True)

    def open_folder(self, folder: str, autostart: bool) -> None:
        if self.busy:
            QMessageBox.information(self, config.APP_NAME, "Trwa analiza – zatrzymaj ją, zanim otworzysz inny folder.")
            return
        folder = os.path.abspath(folder)
        try:
            files = find_audio_files(folder, self.settings.recursive)
        except OSError as exc:
            QMessageBox.warning(self, config.APP_NAME, f"Nie można otworzyć folderu:\n{folder}\n\n{exc}")
            return
        self.folder = folder
        self.settings.last_folder = folder
        self._save_timer.start()
        self.results.set_files(files)
        count = len(files)
        noun = "plik audio" if count == 1 else ("pliki audio" if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14
                                                 else "plików audio")
        self.drop_zone.path_label.setText(f"<b>{html.escape(folder)}</b> — {count} {noun}")
        self.summary_label.setText("")
        self.update_buttons()
        if not files:
            self.statusBar().showMessage("W tym folderze nie ma plików audio.")
            QMessageBox.information(self, config.APP_NAME,
                                    "W wybranym folderze nie znaleziono plików audio (wav, mp3, m4a, flac…)."
                                    + ("" if self.settings.recursive else
                                       "\n\nJeśli nagrania są w podfolderach, włącz w Ustawieniach "
                                       "„Szukaj nagrań także w podfolderach”."))
            return
        if autostart:
            if not clean_episode(self.episode_edit.text()):
                self.statusBar().showMessage("Wpisz nazwę odcinka i kliknij „Rozpoznaj nagrania”.")
                self.episode_edit.setFocus()
                QMessageBox.information(self, config.APP_NAME,
                                        "Wpisz nazwę odcinka (np. tratwy), a potem kliknij „Rozpoznaj nagrania”.")
                return
            self.start_analysis()

    # --- analiza ---
    def _get_engine(self) -> Engine:
        wanted = (self.settings.model, self.settings.device)
        if self.engine is None or (self.engine.model_key, self.engine.device_pref) != wanted:
            self.engine = Engine(self.settings.model, self.settings.device, config.models_dir())
        return self.engine

    def start_analysis(self, rows: list[int] | None = None) -> None:
        if self.busy or not self.results.rows:
            return
        episode = clean_episode(self.episode_edit.text())
        if not episode:
            answer = QMessageBox.question(
                self, config.APP_NAME,
                "Nie wpisano nazwy odcinka.\n\nKontynuować? Pliki z rozpoznaną osobą dostaną wtedy "
                f"dopisek „{NO_EPISODE_TEXT}”.")
            if answer != QMessageBox.StandardButton.Yes:
                self.episode_edit.setFocus()
                return
        people = self.settings.person_objects()
        if not people:
            QMessageBox.warning(self, config.APP_NAME, "Lista osób jest pusta – dodaj osoby w panelu „Osoby” po prawej.")
            return
        targets = rows if rows is not None else range(len(self.results.rows))
        jobs = [(r, self.results.rows[r].path) for r in targets if self.results.rows[r].state != APPLIED]
        if not jobs:
            QMessageBox.information(self, config.APP_NAME, "Wszystkie pliki z listy są już uporządkowane.")
            return
        for r, _ in jobs:
            row = self.results.rows[r]
            row.state, row.analysis, row.match, row.manual, row.new_path, row.target = PENDING, None, None, False, "", None
            row.person, row.episode_found, row.episode_heard, row.from_prior = None, False, "", False
        self.results.refresh()
        self.cancel_requested = False
        self.worker = AnalysisWorker(self._get_engine(), jobs, people, episode, self.settings.seconds,
                                     self.settings.use_hints, self.settings.second_pass, self)
        self.worker.status.connect(self.on_status)
        self.worker.download_progress.connect(self.on_download_progress)
        self.worker.engine_ready.connect(self.on_engine_ready)
        self.worker.file_started.connect(self.on_file_started)
        self.worker.file_finished.connect(self.on_file_finished)
        self.worker.progress.connect(self.on_progress)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_worker_finished)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.progress_label.setText("Przygotowanie…")
        self._jobs_total = len(jobs)
        self.engine_loading = True
        self.worker.start()
        self.update_buttons()

    def stop_analysis(self) -> None:
        if self.busy:
            self.cancel_requested = True
            self.worker.cancel()
            self.progress_label.setText("Zatrzymywanie po bieżącym pliku…")

    def on_status(self, text: str) -> None:
        self.statusBar().showMessage(text)
        self.progress_label.setText(text)

    def on_download_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, 1000)
        self.progress.setValue(int(1000 * min(done, total) / max(total, 1)))
        self.progress.setFormat("%p%")
        self.progress_label.setText(f"Pobieranie modelu: {done / 1e6:,.0f} z {total / 1e6:,.0f} MB".replace(",", " "))

    def on_engine_ready(self, description: str) -> None:
        self.engine_loading = False
        self.statusBar().showMessage(f"Model: {description}")
        self.progress.setRange(0, self._jobs_total)
        self.progress.setValue(0)
        self.progress.setFormat("%v / %m")
        self.progress_label.setText("Analiza nagrań…")
        self.settings_tab.refresh_model_status()

    def on_file_started(self, r: int) -> None:
        self.results.set_state(r, RUNNING)
        self.table.scrollTo(self.results.index(r, 0))

    def on_file_finished(self, r: int, analysis: FileAnalysis) -> None:
        row = self.results.rows[r]
        row.analysis = analysis
        row.state = ERROR if analysis.error else DONE
        self._set_decision(r, analysis.person, analysis.episode)
        self.recompute_plan()

    def _set_decision(self, r: int, match: PersonMatch, episode) -> None:
        """Wynik rozpoznania uzupełniony o to, co już było w nazwie pliku nadanej wcześniej przez program."""
        prior = parse_prior_label(self.results.rows[r].original, self.results.people_names,
                                  self.episode_edit.text())
        person, found, used = merge_prior(match.person.name if match.person else None, episode.found, prior)
        self.results.apply_decision(r, match, person, found, episode.heard, used)

    def on_progress(self, done: int, total: int, remaining: float) -> None:
        self.progress.setValue(done)
        text = f"Przeanalizowano {done} z {total}"
        if done < total:
            text += f" • zostało ok. {_format_eta(remaining)}"
        self.progress_label.setText(text)

    def on_failed(self, message: str) -> None:
        QMessageBox.critical(self, config.APP_NAME, message)

    def on_worker_finished(self) -> None:
        cancelled = self.cancel_requested
        self.worker.deleteLater()
        self.worker = None
        self.progress.setVisible(False)
        for r, row in enumerate(self.results.rows):
            if row.state in (PENDING, RUNNING):
                row.state = PENDING
        self.results.refresh()
        self.recompute_plan()
        self.update_buttons()
        if self.closing:
            self.close()
            return
        analyzed = [r for r in self.results.rows if r.state in (DONE, ERROR)]
        self.progress_label.setText("Przerwano." if cancelled else (
            "Gotowe – sprawdź wyniki i kliknij „Uporządkuj pliki”." if analyzed else ""))
        if analyzed and not cancelled and self.settings.auto_apply and self._has_pending_changes():
            self.apply()

    # --- dopasowanie i plan ---
    def on_episode_changed(self, text: str) -> None:
        self.settings.episode = text
        self._save_timer.start()
        self._rematch_timer.start()

    def on_people_changed(self, save: bool = True) -> None:
        self.results.people_names = [p.name for p in self.settings.person_objects()]
        if save:
            self._save_timer.start()
            self._rematch_timer.start()

    def on_engine_settings_changed(self) -> None:
        self._save_timer.start()

    def on_matching_settings_changed(self) -> None:
        self._save_timer.start()
        self.recompute_plan()

    def rematch(self) -> None:
        """Po zmianie nazwy odcinka lub listy osób - ponowne dopasowanie z zapisanych transkrypcji
        (bez ponownego słuchania nagrań)."""
        recognizer = Recognizer(self.settings.person_objects(), clean_episode(self.episode_edit.text()))
        for r, row in enumerate(self.results.rows):
            if row.state == DONE and row.analysis and not row.manual:
                person, episode = recognizer.decide(row.analysis.transcripts, row.analysis.hinted)
                self._set_decision(r, person, episode)
        self.recompute_plan()

    def recompute_plan(self) -> None:
        episode = clean_episode(self.episode_edit.text())
        items = []
        for row in self.results.rows:
            if row.state == DONE:
                row.target = make_target(row.person, row.episode_found, episode, self.settings.quote_style)
                items.append((row.path, row.target))
            elif row.state != APPLIED:
                row.target, row.new_path = None, ""
        if self.folder:
            by_src = {m.src: m for m in plan_moves(items, self.folder)}
            for row in self.results.rows:
                if row.state == DONE and row.path in by_src:
                    row.new_path = by_src[row.path].dst
        self.results.refresh()
        self.update_summary()
        self.update_buttons()

    def update_summary(self) -> None:
        counts = {"move": 0, "partial": 0, "none": 0, "error": 0}
        applied = 0
        for row in self.results.rows:
            if row.state == APPLIED:
                applied += 1
            elif row.category in counts:
                counts[row.category] += 1
        if not any(counts.values()) and not applied:
            self.summary_label.setText("")
            return

        def chip(color: QColor, text: str) -> str:
            rgb = f"rgba({color.red()},{color.green()},{color.blue()},{color.alpha() / 255:.2f})"
            return f"<span style='background:{rgb}'>&nbsp;&nbsp;&nbsp;&nbsp;</span>&nbsp;{text}"

        labels = (("move", "do folderów osób"), ("partial", "tylko zmiana nazwy"), ("none", "nie rozpoznano"),
                  ("error", "błędy odczytu (pliki zostaną bez zmian)"))
        parts = [chip(COLORS[key], f"{text}: <b>{counts[key]}</b>") for key, text in labels
                 if counts[key] or (key != "error" and not applied)]
        if applied:
            parts.append(f"✓ uporządkowane: <b>{applied}</b>")
        self.summary_label.setText("&nbsp;&nbsp;&nbsp;".join(parts))

    # --- porządkowanie ---
    def apply(self) -> None:
        if self.busy:
            return
        self.recompute_plan()
        moves = [(r, row) for r, row in enumerate(self.results.rows)
                 if row.state == DONE and row.new_path and not _same(row.path, row.new_path)]
        if not moves:
            QMessageBox.information(self, config.APP_NAME, "Nie ma nic do zmiany.")
            return
        episode = clean_episode(self.episode_edit.text())
        items = [(row.path, row.target) for row in self.results.rows if row.state == DONE]
        plan = plan_moves(items, self.folder)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            report = apply_moves(plan, config.journal_dir(), self.folder)
        finally:
            QApplication.restoreOverrideCursor()
        by_src = {m.src: m for m in report.moved}
        for row in self.results.rows:
            move = by_src.get(row.path)
            if move:
                row.applied_from, row.path, row.state = move.src, move.dst, APPLIED
        self.results.refresh()
        self.recompute_plan()
        moved_to_folders = sum(1 for m in report.moved if m.target.move)
        renamed = len(report.moved) - moved_to_folders
        message = (f"Przeniesiono do folderów osób: {moved_to_folders}\n"
                   f"Zmieniono tylko nazwę: {renamed}")
        if report.errors:
            message += f"\n\nNie udało się ({len(report.errors)}):\n" + "\n".join(
                f"• {os.path.basename(m.src)}: {err}" for m, err in report.errors[:15])
        if self.settings.save_report and report.moved:
            try:
                path = self.write_report(episode)
                message += f"\n\nRaport: {os.path.basename(path)}"
            except OSError as exc:
                message += f"\n\nNie udało się zapisać raportu: {exc}"
        self.statusBar().showMessage(f"Uporządkowano {len(report.moved)} plików.")
        self.progress_label.setText(f"Uporządkowano {len(report.moved)} plików"
                                    + (f", błędy: {len(report.errors)}." if report.errors else "."))
        box = QMessageBox(QMessageBox.Icon.Warning if report.errors else QMessageBox.Icon.Information,
                          config.APP_NAME, message, parent=self)
        open_button = box.addButton("Otwórz folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_button:
            open_path(self.folder)

    def write_report(self, episode: str) -> str:
        path = os.path.join(self.folder, time.strftime("zgrywka_raport_%Y-%m-%d_%H-%M-%S.csv"))
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["Poprzednia nazwa", "Nowe położenie", "Osoba", "Odcinek", "Rozpoznanie", "Co usłyszano"])
            for r, row in enumerate(self.results.rows):
                if row.state not in (APPLIED, DONE, ERROR):
                    continue
                writer.writerow([
                    row.original, os.path.relpath(row.path, self.folder), row.person or "",
                    episode if row.episode_found else "", self.results.data(self.results.index(r, COL_HOW)),
                    row.analysis.transcript if row.analysis else "",
                ])
        return path

    def undo_last(self) -> None:
        journal = latest_journal(config.journal_dir())
        if not journal:
            return
        try:
            root, count = journal_summary(journal)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, config.APP_NAME, f"Nie można odczytać historii: {exc}")
            return
        answer = QMessageBox.question(
            self, config.APP_NAME,
            f"Cofnąć ostatnie porządkowanie?\n\nFolder: {root}\nPlików: {count}\n\n"
            "Pliki wrócą do poprzednich nazw i miejsc, a puste foldery osób zostaną usunięte.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        report = undo_journal(journal)
        for row in self.results.rows:
            if row.state == APPLIED and row.applied_from and os.path.exists(row.applied_from):
                row.path, row.applied_from, row.state = row.applied_from, "", DONE
        self.recompute_plan()
        message = f"Przywrócono {report.restored} plików."
        if report.errors:
            message += "\n\nNie udało się przywrócić:\n" + "\n".join(report.errors[:15])
        QMessageBox.information(self, config.APP_NAME, message)
        self.update_buttons()

    # --- tabela: menu i kliknięcia ---
    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def on_click(self, index: QModelIndex) -> None:
        if QApplication.keyboardModifiers() != Qt.KeyboardModifier.NoModifier:
            return  # zaznaczanie wielu wierszy
        if index.column() == COL_PERSON and self.results.flags(index) & Qt.ItemFlag.ItemIsEditable:
            self.table.edit(index)

    def on_double_click(self, index: QModelIndex) -> None:
        if index.column() not in (COL_PERSON, COL_EPISODE):
            open_path(self.results.rows[index.row()].path)

    def show_context_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        rows = self.selected_rows() or [index.row()]
        menu = QMenu(self)
        first = self.results.rows[rows[0]]
        menu.addAction("Odtwórz nagranie", lambda: open_path(first.path))
        menu.addAction("Pokaż w folderze", lambda: reveal_in_folder(first.path))
        editable = [r for r in rows if self.results.rows[r].state == DONE]
        if editable and not self.busy:
            menu.addSeparator()
            people_menu = menu.addMenu("Ustaw osobę")
            for name in [NONE_PERSON, *self.results.people_names]:
                people_menu.addAction(name, lambda n=name: self.set_rows(editable, person=None if n == NONE_PERSON else n))
            people_menu.addSeparator()
            people_menu.addAction(NEW_PERSON, lambda: self.assign_new_person(editable))
            menu.addAction("Odcinek: rozpoznany", lambda: self.set_rows(editable, episode=True))
            menu.addAction("Odcinek: nie rozpoznany", lambda: self.set_rows(editable, episode=False))
            menu.addAction("Przywróć rozpoznanie automatyczne", lambda: self.reset_manual(editable))
        again = [r for r in rows if self.results.rows[r].state in (DONE, ERROR)]
        if again and not self.busy:
            menu.addAction("Rozpoznaj ponownie", lambda: self.start_analysis(again))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    _UNSET = object()

    def set_rows(self, rows: list[int], person=_UNSET, episode=_UNSET) -> None:
        for r in rows:
            row = self.results.rows[r]
            if person is not self._UNSET:
                row.person = person
            if episode is not self._UNSET:
                row.episode_found = episode
            row.manual = True
        self.recompute_plan()

    def assign_new_person(self, rows: list[int]) -> None:
        name = self.people_panel.ask_new_person(self)
        if name:
            self.set_rows(rows, person=name)

    def reset_manual(self, rows: list[int]) -> None:
        for r in rows:
            self.results.rows[r].manual = False
        self.rematch()

    # --- zapis i zamykanie ---
    def save_settings(self) -> None:
        self.settings.window_geometry = bytes(self.saveGeometry()).hex()
        config.save_settings(self.settings)

    def closeEvent(self, event):
        if self.busy:
            answer = QMessageBox.question(self, config.APP_NAME, "Trwa analiza nagrań. Przerwać i zamknąć program?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self.engine_loading:
                # pobierania modelu nie da się przerwać w połowie - kończymy proces od razu
                # (żadne pliki nagrań nie są w tym czasie zmieniane, pobieranie wznowi się następnym razem)
                self.save_settings()
                logging.shutdown()
                os._exit(0)
            self.closing = True
            self.stop_analysis()
            self.progress_label.setText("Zamykanie po bieżącym pliku…")
            event.ignore()
            return
        self.save_settings()
        event.accept()


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

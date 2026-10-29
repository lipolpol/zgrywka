"""Okno programu: przeciągnij folder, wpisz nazwę odcinka, sprawdź, odsłuchaj i uporządkuj."""

from __future__ import annotations

import csv
import html
import logging
import os
import subprocess
import sys
import time

from PySide6.QtCore import QModelIndex, QProcess, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QProgressDialog, QPushButton, QSpinBox, QSplitter, QStyle, QTableView, QTabWidget, QVBoxLayout, QWidget,
)

from . import config
from .analyzer import FileAnalysis, Recognizer, assign_episode
from .asr import GPU_PACKAGES, MODELS, Engine, cuda_device_count, local_model_path, nvidia_libs_installed
from .matching import EpisodeMatch, PersonMatch
from .naming import (
    ASCII_QUOTES_ALLOWED, NO_EPISODE_TEXT, NO_PERSON_PREFIX, UNKNOWN_LABEL, apply_moves, clean_episode,
    find_audio_files, journal_summary, latest_journal, make_target, merge_prior, parse_episodes, parse_prior_label,
    plan_moves, undo_journal,
)
from .people import PeoplePanel
from .player import AudioPlayer, PlayerBar
from .results import (
    APPLIED, COL_EPISODE, COL_FILE, COL_HEARD, COL_HOW, COL_NEW, COL_PERSON, COL_PLAY, COLORS, DONE, ERROR,
    NEW_PERSON, NONE_EPISODE, NONE_PERSON, PENDING, RUNNING, EpisodeDelegate, PersonDelegate, ResultsModel,
)
from .worker import AnalysisWorker

log = logging.getLogger(__name__)

HELP_TEXT = f"""
<h3>Jak działa porządkowanie</h3>
<p>Program słucha początku każdego nagrania (domyślnie 30 sekund, można zmienić w Ustawieniach),
zamienia mowę na tekst i szuka w nim osób z listy oraz nazwy odcinka. Porównuje litery:</p>
<ul>
<li>przekręcone litery i polska wymowa: „Radek <b>H</b>aberko” = Radek Chaberko,
  „Weronika Hrabążcz” = Weronika Chrabąszcz;</li>
<li>zgubione albo dodane litery: „<b>adek</b>, tratwy” = Radek, „Marcel <b>Woński</b>” = Marcel Gawroński;</li>
<li>odmiana: „mikroport Marcela Gawrońskiego”, „Radka”, „tratwa” = „tratwy”;</li>
<li>imię i nazwisko obok siebie wystarczą, gdy zgadza się mniej więcej co druga litera;</li>
<li>samo imię albo samo nazwisko też wystarczy, jeśli nikt inny z listy go nie ma.</li>
</ul>
<p>Zdrobnienia i przezwiska (np. Wera, Nika, Werka) dopisz osobie jako <b>alternatywy</b>:
dwuklik na osobie w panelu „Osoby” po prawej, wpisz alternatywę i naciśnij Enter, potem kolejną…
a na koniec „Zapisz”.</p>
<p>W polu „Nazwa odcinka” możesz wpisać <b>kilka odcinków oddzielonych spacją</b>
(np. <i>dom tratwy las</i>, nazwę z kilku słów wpisz w cudzysłowie). Program rozpozna,
który odcinek pada w nagraniu. Nazwa z kilku słów pasuje też w innej kolejności i formie
(<i>dom strachu</i> = „domu strachu” = „straszny dom”). <b>Początek nagrania ma pierwszeństwo</b>:
gdy padną dwa tytuły albo dwie osoby, liczy się to, co padło wcześniej.</p>
<table cellpadding="4">
<tr><td><b>osoba + odcinek</b></td><td>plik trafia do folderu odcinka i osoby:
  <i>dom / Weronika Chrabąszcz „dom” / Weronika Chrabąszcz „dom” 1</i>, <i>… 2</i> itd.</td></tr>
<tr><td><b>sama osoba</b></td><td>tylko zmiana nazwy: <i>Marcel Gawroński „{NO_EPISODE_TEXT}” 1</i></td></tr>
<tr><td><b>sam odcinek</b></td><td>tylko zmiana nazwy: <i>{NO_PERSON_PREFIX} „dom” 1</i></td></tr>
<tr><td><b>nic</b></td><td>tylko zmiana nazwy: <i>{UNKNOWN_LABEL} 1</i></td></tr>
</table>
<p>Pole <b>„sortowanie tylko po imieniu i nazwisku”</b>: program rozpoznaje tylko osobę (samo imię,
samo nazwisko i przekręcone litery też), a odcinek bierze od razu z pola „Nazwa odcinka” – nie musi
on paść w nagraniu.</p>
<p>W tabeli kliknij komórkę <b>Osoba</b> albo <b>Odcinek</b>, aby wybrać z listy albo wpisać własną nazwę.
Ikona ▶ (albo dwuklik, albo spacja) odtwarza nagranie w odtwarzaczu na dole okna:
przewijanie suwakiem, ±10 s, poprzednie/następne nagranie.</p>
<p>Wiersze z dopiskiem „część liter – sprawdź” warto odsłuchać. Każde porządkowanie można cofnąć
przyciskiem „Cofnij ostatnie”. Numeracja kontynuuje istniejące pliki, a program nigdy nie nadpisuje
istniejących plików.</p>
"""


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
        self.player = AudioPlayer(self)
        self.player.state_changed.connect(self.on_player_state)
        self.player.error.connect(lambda message: self.statusBar().showMessage(message, 10000))
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
        self.results.episodes = parse_episodes(self.episode_edit.text())
        self._update_episode_chips()
        self.update_buttons()
        if settings.window_geometry:
            self.restoreGeometry(bytes.fromhex(settings.window_geometry))
        else:
            self.resize(1380, 820)

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
        self.episode_edit.setPlaceholderText("np. tratwy   (kilka odcinków oddziel spacją: dom tratwy las)")
        self.episode_edit.setFont(big)
        self.episode_edit.setClearButtonEnabled(True)
        self.episode_edit.setToolTip("Nazwa odcinka, którą mówi się na początku nagrania. Kilka odcinków oddziel "
                                     "spacją; nazwę z kilku słów wpisz w cudzysłowie, np. \"stara chata\".")
        self.episode_edit.textChanged.connect(self.on_episode_changed)
        help_button = QPushButton("Jak to działa?")
        help_button.clicked.connect(lambda: QMessageBox.information(self, config.APP_NAME, HELP_TEXT))
        top.addWidget(label)
        top.addWidget(self.episode_edit, 1)
        top.addWidget(help_button)
        layout.addLayout(top)
        chips_row = QHBoxLayout()
        self.episodes_label = QLabel()
        self.episodes_label.setTextFormat(Qt.TextFormat.RichText)
        chips_row.addWidget(self.episodes_label, 1)
        self.person_only_check = QCheckBox("sortowanie tylko po imieniu i nazwisku")
        self.person_only_check.setToolTip(
            "Program rozpoznaje tylko osobę, a odcinek bierze od razu z pola „Nazwa odcinka” "
            "(nie musi paść w nagraniu). Przy kilku wpisanych odcinkach: ten, który padł, a jeśli żaden – pierwszy.")
        self.person_only_check.setChecked(self.settings.person_only)
        self.person_only_check.toggled.connect(self.on_person_only_toggled)
        chips_row.addWidget(self.person_only_check)
        self.split_check = QCheckBox("przycinka mikroportów")
        self.split_check.setToolTip("Rozpoznawanie ciągu dalszego nagrań, które mikroport przeciął "
                                    "(np. co 30 minut) – funkcja w przygotowaniu.")
        self.split_check.toggled.connect(self.on_split_toggled)
        chips_row.addWidget(self.split_check)
        layout.addLayout(chips_row)

        self.drop_zone = DropZone()
        self.drop_zone.folder_dropped.connect(lambda p: self.open_folder(p, autostart=True))
        self.drop_zone.clicked.connect(self.choose_folder)
        layout.addWidget(self.drop_zone)

        buttons = QHBoxLayout()
        style = self.style()
        P = QStyle.StandardPixmap
        self.start_button = QPushButton(style.standardIcon(P.SP_MediaPlay), " Rozpoznaj nagrania")
        self.start_button.clicked.connect(lambda: self.start_analysis())
        self.stop_button = QPushButton(style.standardIcon(P.SP_MediaStop), " Zatrzymaj")
        self.stop_button.clicked.connect(self.stop_analysis)
        self.apply_button = QPushButton(style.standardIcon(P.SP_DialogApplyButton), " Uporządkuj pliki")
        self.apply_button.setToolTip("Utwórz foldery, przenieś i ponumeruj pliki zgodnie z kolumną „Nowa nazwa”")
        self.apply_button.clicked.connect(self.apply)
        self.undo_button = QPushButton(style.standardIcon(P.SP_ArrowBack), " Cofnij ostatnie")
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
        self.table.setItemDelegateForColumn(COL_PERSON, PersonDelegate(
            self.results, self.people_panel.ask_new_person, self.people_panel.ensure_person, self.table))
        self.table.setItemDelegateForColumn(COL_EPISODE, EpisodeDelegate(self.results, self.table))
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
        header.setSectionResizeMode(COL_PLAY, QHeaderView.ResizeMode.Fixed)
        for col, width in ((COL_PLAY, 30), (COL_FILE, 170), (COL_PERSON, 180), (COL_EPISODE, 110),
                           (COL_NEW, 330), (COL_HOW, 240)):
            self.table.setColumnWidth(col, width)
        self.table.verticalHeader().setDefaultSectionSize(26)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self.table, self.toggle_play,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        layout.addWidget(self.table, 1)

        self.summary_label = QLabel("")
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.summary_label)

        self.player_bar = PlayerBar(self.player)
        self.player_bar.previous_requested.connect(lambda: self.play_adjacent(-1))
        self.player_bar.next_requested.connect(lambda: self.play_adjacent(1))
        layout.addWidget(self.player_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(content)
        splitter.addWidget(self.people_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1100, 270])
        splitter.setChildrenCollapsible(False)
        page = QWidget()
        QVBoxLayout(page).addWidget(splitter)
        return page

    def _update_episode_chips(self) -> None:
        episodes = self.results.episodes
        if not episodes:
            self.episodes_label.setText("<i>Wpisz nazwę odcinka. Kilka odcinków oddziel spacją, "
                                        "np. dom tratwy las.</i>")
            return
        chips = " ".join(f"<span style='background-color:#2563eb; color:#ffffff;'>&nbsp;{html.escape(e)}&nbsp;</span>"
                         for e in episodes)
        if self.settings.person_only:
            text = (f"Odcinek przypisywany do nagrań: {chips}" if len(episodes) == 1 else
                    f"Odcinki (jeśli żaden nie padnie w nagraniu – pierwszy): {chips}")
        else:
            text = f"{'Odcinki' if len(episodes) > 1 else 'Odcinek'} do rozpoznania: {chips}"
        self.episodes_label.setText(text)

    def on_person_only_toggled(self, checked: bool) -> None:
        self.settings.person_only = checked
        self._update_episode_chips()
        self._save_timer.start()
        self.rematch()  # bez ponownego słuchania - zmienia się tylko przypisanie odcinka

    def on_split_toggled(self, checked: bool) -> None:
        if not checked:
            return
        QMessageBox.information(
            self, config.APP_NAME,
            "Funkcja „przycinka mikroportów” nie jest jeszcze dopracowana.\n\n"
            "Docelowo: gdy mikroport przetnie nagranie (np. po 30 minutach) i od razu zacznie nowy plik "
            "(różnica 1–5 klatek), program po godzinie nagrania rozpozna, że nowy plik to ciąg dalszy "
            "poprzedniego, i podpisze go tak samo, nawet jeśli na jego początku nic nie powiedziano.")
        self.split_check.blockSignals(True)
        self.split_check.setChecked(False)
        self.split_check.blockSignals(False)

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
        self.player.stop()
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
            if not self.results.episodes:
                self.statusBar().showMessage("Wpisz nazwę odcinka i kliknij „Rozpoznaj nagrania”.")
                self.episode_edit.setFocus()
                QMessageBox.information(self, config.APP_NAME,
                                        "Wpisz nazwę odcinka (np. tratwy, a kilka odcinków oddziel spacją), "
                                        "a potem kliknij „Rozpoznaj nagrania”.")
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
        episodes = self.results.episodes
        if not episodes:
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
            row.person, row.episode, row.episode_heard, row.episode_loose, row.from_prior = None, None, "", False, False
            row.episode_assumed = False
        self.results.refresh()
        self.cancel_requested = False
        self.worker = AnalysisWorker(self._get_engine(), jobs, people, list(episodes), self.settings.seconds,
                                     self.settings.use_hints, self.settings.second_pass,
                                     need_episode=not self.settings.person_only, parent=self)
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

    def _set_decision(self, r: int, match: PersonMatch, episode: EpisodeMatch) -> None:
        """Wynik rozpoznania uzupełniony o to, co już było w nazwie pliku nadanej wcześniej przez program."""
        prior = parse_prior_label(self.results.rows[r].original, self.results.people_names, self.results.episodes)
        recognized = episode.name if episode.found else None
        assigned, assumed = assign_episode(recognized, self.results.episodes, self.settings.person_only)
        person, episode_name, used = merge_prior(match.person.name if match.person else None, assigned, prior)
        self.results.apply_decision(r, match, person, episode_name, episode.heard if episode.found else "",
                                    episode.found and episode.loose, assumed, used)

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
        for row in self.results.rows:
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
        self.results.episodes = parse_episodes(text)
        self._update_episode_chips()
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
        """Po zmianie odcinków lub listy osób - ponowne dopasowanie z zapisanych transkrypcji
        (bez ponownego słuchania nagrań)."""
        recognizer = Recognizer(self.settings.person_objects(), self.results.episodes)
        for r, row in enumerate(self.results.rows):
            if row.state == DONE and row.analysis and not row.manual:
                person, episode = recognizer.decide(row.analysis.transcripts, row.analysis.hinted)
                self._set_decision(r, person, episode)
        self.recompute_plan()

    def recompute_plan(self) -> None:
        items = []
        for row in self.results.rows:
            if row.state == DONE:
                row.target = make_target(row.person, row.episode, self.settings.quote_style)
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

        labels = (("move", "do folderów odcinków i osób"), ("partial", "tylko zmiana nazwy"),
                  ("none", "nie rozpoznano"), ("error", "błędy odczytu (pliki zostaną bez zmian)"))
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
        if not self._has_pending_changes():
            QMessageBox.information(self, config.APP_NAME, "Nie ma nic do zmiany.")
            return
        self.player.stop()  # odtwarzany plik musi być zamknięty, zanim go przeniesiemy
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
        message = (f"Przeniesiono do folderów odcinków i osób: {moved_to_folders}\n"
                   f"Zmieniono tylko nazwę: {renamed}")
        if report.errors:
            message += f"\n\nNie udało się ({len(report.errors)}):\n" + "\n".join(
                f"• {os.path.basename(m.src)}: {err}" for m, err in report.errors[:15])
        if self.settings.save_report and report.moved:
            try:
                path = self.write_report()
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

    def write_report(self) -> str:
        path = os.path.join(self.folder, time.strftime("zgrywka_raport_%Y-%m-%d_%H-%M-%S.csv"))
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["Poprzednia nazwa", "Nowe położenie", "Osoba", "Odcinek", "Rozpoznanie", "Co usłyszano"])
            for r, row in enumerate(self.results.rows):
                if row.state not in (APPLIED, DONE, ERROR):
                    continue
                writer.writerow([
                    row.original, os.path.relpath(row.path, self.folder), row.person or "", row.episode or "",
                    self.results.data(self.results.index(r, COL_HOW)),
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
            "Pliki wrócą do poprzednich nazw i miejsc, a puste foldery odcinków i osób zostaną usunięte.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.player.stop()
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

    # --- odsłuch ---
    def play_row(self, r: int) -> None:
        if not 0 <= r < len(self.results.rows):
            return
        row = self.results.rows[r]
        if self.results.playing_row == r and self.player.path == row.path:
            self.player.toggle()
            return
        if self.player.load(row.path):
            self.results.set_playing(r, False)
            self.player_bar.set_title(os.path.basename(row.path))
            self.table.selectRow(r)
            self.player.play()

    def play_adjacent(self, step: int) -> None:
        current = self.results.playing_row
        if current is None:
            rows = self.selected_rows()
            current = rows[0] - step if rows else -step if step > 0 else len(self.results.rows)
        target = current + step
        if 0 <= target < len(self.results.rows):
            self.play_row(target)

    def toggle_play(self) -> None:
        if self.player.path and self.results.playing_row is not None:
            self.player.toggle()
        else:
            rows = self.selected_rows()
            if rows:
                self.play_row(rows[0])

    def on_player_state(self, state: str) -> None:
        self.results.set_playing(self.results.playing_row, state == "playing")

    # --- tabela: menu i kliknięcia ---
    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def on_click(self, index: QModelIndex) -> None:
        if QApplication.keyboardModifiers() != Qt.KeyboardModifier.NoModifier:
            return  # zaznaczanie wielu wierszy
        column = index.column()
        if column == COL_PLAY:
            self.play_row(index.row())
            return
        if column == COL_HOW:  # kliknięcie w "Rozpoznanie" - poprawa osoby
            index = self.results.index(index.row(), COL_PERSON)
            column = COL_PERSON
        if column in (COL_PERSON, COL_EPISODE) and self.results.flags(index) & Qt.ItemFlag.ItemIsEditable:
            self.table.edit(index)

    def on_double_click(self, index: QModelIndex) -> None:
        if index.column() in (COL_FILE, COL_NEW, COL_HEARD):
            self.play_row(index.row())

    def show_context_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        rows = self.selected_rows() or [index.row()]
        menu = QMenu(self)
        first = self.results.rows[rows[0]]
        menu.addAction("Odsłuchaj w programie", lambda: self.play_row(rows[0]))
        menu.addAction("Otwórz w zewnętrznym odtwarzaczu", lambda: open_path(first.path))
        menu.addAction("Pokaż w folderze", lambda: reveal_in_folder(first.path))
        editable = [r for r in rows if self.results.rows[r].state == DONE]
        if editable and not self.busy:
            menu.addSeparator()
            people_menu = menu.addMenu("Ustaw osobę")
            for name in [NONE_PERSON, *self.results.people_names]:
                people_menu.addAction(name, lambda n=name: self.set_rows(editable, person=None if n == NONE_PERSON else n))
            people_menu.addSeparator()
            people_menu.addAction(NEW_PERSON, lambda: self.assign_new_person(editable))
            episode_menu = menu.addMenu("Ustaw odcinek")
            episode_menu.addAction(NONE_EPISODE, lambda: self.set_rows(editable, episode=None))
            for episode in self.results.episodes:
                episode_menu.addAction(episode, lambda e=episode: self.set_rows(editable, episode=e))
            episode_menu.addSeparator()
            episode_menu.addAction("Wpisz inny odcinek…", lambda: self.ask_episode(editable))
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
                row.episode = episode
            row.manual = True
        self.recompute_plan()

    def assign_new_person(self, rows: list[int]) -> None:
        name = self.people_panel.ask_new_person(self)
        if name:
            self.set_rows(rows, person=name)

    def ask_episode(self, rows: list[int]) -> None:
        text, ok = QInputDialog.getText(self, config.APP_NAME, "Nazwa odcinka dla zaznaczonych nagrań:")
        episode = clean_episode(text)
        if ok and episode:
            episode = next((e for e in self.results.episodes if e.casefold() == episode.casefold()), episode)
            self.set_rows(rows, episode=episode)

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
        self.player.close()
        self.save_settings()
        event.accept()


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

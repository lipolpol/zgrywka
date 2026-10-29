"""Lista osób w głównym oknie i okno edycji osoby (imię i nazwisko + alternatywy)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QStyle, QVBoxLayout, QWidget,
)

from . import config
from .matching import parse_alternatives

_NAME_ROLE = Qt.ItemDataRole.UserRole
_ALTERNATIVES_ROLE = Qt.ItemDataRole.UserRole + 1


class PersonDialog(QDialog):
    """Okno osoby: imię i nazwisko (nazwy folderów i plików) oraz alternatywy, np. Wera, Nika, Werka."""

    def __init__(self, parent=None, name: str = "", alternatives=(), taken: set[str] = frozenset()):
        super().__init__(parent)
        self.setWindowTitle("Osoba" if name else "Nowa osoba")
        self.setMinimumWidth(480)
        self._taken = {t.casefold() for t in taken}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("np. Weronika Chrabąszcz")
        form.addRow("Imię i nazwisko:", self.name_edit)
        self.alternatives_edit = QLineEdit(", ".join(alternatives))
        self.alternatives_edit.setPlaceholderText("np. Wera, Nika, Werka")
        form.addRow("Alternatywy:", self.alternatives_edit)
        layout.addLayout(form)
        hint = QLabel(
            "Imię i nazwisko trafi do nazw folderów i plików.<br>"
            "<b>Alternatywy</b> to inne formy, po których program też rozpozna tę osobę: zdrobnienia, "
            "przezwiska albo pełne imię (oddziel je przecinkami, np. <i>Wera, Nika, Werka</i> albo "
            "<i>Radosław Chaberko</i>).<br>Przekręconych liter i odmiany (np. „Weroniki”, „Hrabąszcz”) "
            "nie trzeba wpisywać – program wyłapuje je sam.")
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Zapisz")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Anuluj")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        (self.alternatives_edit if name else self.name_edit).setFocus()

    def values(self) -> tuple[str, list[str]]:
        name = " ".join(self.name_edit.text().split())
        alternatives = [a for a in parse_alternatives(self.alternatives_edit.text()) if a.casefold() != name.casefold()]
        return name, alternatives

    def _accept(self) -> None:
        name, _ = self.values()
        if not name:
            QMessageBox.information(self, config.APP_NAME, "Wpisz imię i nazwisko.")
            return
        if name.casefold() in self._taken:
            QMessageBox.information(self, config.APP_NAME, f"Osoba „{name}” już jest na liście.")
            return
        self.accept()


class PeoplePanel(QWidget):
    """Lista osób: dodawanie, edycja (dwuklik) i usuwanie."""
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
        hint = QLabel("Tak jak tu wpiszesz, tak będą nazwane foldery i pliki. Dwuklik otwiera osobę – "
                      "tam dopiszesz alternatywy (np. Wera, Nika). Przekręcone litery program wyłapie sam.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(self.edit_item)
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        style = self.style()
        P = QStyle.StandardPixmap
        add = QPushButton(style.standardIcon(P.SP_FileDialogNewFolder), " Dodaj")
        add.setToolTip("Dodaj nową osobę")
        add.clicked.connect(lambda: self.ask_new_person())
        self.edit_button = QPushButton(style.standardIcon(P.SP_FileDialogDetailedView), " Edytuj")
        self.edit_button.setToolTip("Zmień imię i nazwisko albo alternatywy zaznaczonej osoby")
        self.edit_button.clicked.connect(lambda: self.edit_item(self.list.currentItem()))
        self.remove_button = QPushButton(style.standardIcon(P.SP_TrashIcon), " Usuń")
        self.remove_button.clicked.connect(self.remove_selected)
        for b in (add, self.edit_button, self.remove_button):
            buttons.addWidget(b)
        layout.addLayout(buttons)
        self._load(settings.people)
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self._update_buttons()

    # --- lista ---
    def _load(self, people) -> None:
        self.list.clear()
        for entry in config.clean_people(people):
            self._append(entry["name"], entry["alternatywy"])
        self._update_title()

    def _append(self, name: str, alternatives: list[str]) -> QListWidgetItem:
        item = QListWidgetItem()
        self._fill(item, name, alternatives)
        self.list.addItem(item)
        return item

    @staticmethod
    def _fill(item: QListWidgetItem, name: str, alternatives: list[str]) -> None:
        item.setData(_NAME_ROLE, name)
        item.setData(_ALTERNATIVES_ROLE, list(alternatives))
        item.setText(f"{name}\n    też: {', '.join(alternatives)}" if alternatives else name)
        item.setToolTip("Dwuklik – edycja osoby")

    def entries(self) -> list[dict]:
        return [{"name": self.list.item(i).data(_NAME_ROLE),
                 "alternatywy": list(self.list.item(i).data(_ALTERNATIVES_ROLE) or [])}
                for i in range(self.list.count())]

    def names(self) -> list[str]:
        return [e["name"] for e in self.entries()]

    def _update_title(self) -> None:
        self.title.setText(f"Osoby ({self.list.count()})")

    def _update_buttons(self) -> None:
        selected = bool(self.list.selectedItems())
        self.remove_button.setEnabled(selected)
        self.edit_button.setEnabled(len(self.list.selectedItems()) == 1)

    def _save(self) -> None:
        self.settings.people = config.clean_people(self.entries())
        self._update_title()
        self._update_buttons()
        self.changed.emit()

    # --- akcje ---
    def ask_new_person(self, parent: QWidget | None = None, name: str = "") -> str | None:
        """Okno dodawania osoby. Zwraca jej nazwę albo None (anulowano)."""
        dialog = PersonDialog(parent or self, "", (), set(self.names()))
        dialog.name_edit.setText(name)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        name, alternatives = dialog.values()
        self.list.setCurrentItem(self._append(name, alternatives))
        self._save()
        return name

    def ensure_person(self, name: str) -> str:
        """Nazwa wpisana ręcznie w tabeli: osoba z listy albo nowa (dopisana do listy)."""
        name = " ".join(name.split())
        for existing in self.names():
            if existing.casefold() == name.casefold():
                return existing
        self._append(name, [])
        self._save()
        return name

    def edit_item(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        name = item.data(_NAME_ROLE)
        taken = {n for n in self.names() if n != name}
        dialog = PersonDialog(self, name, item.data(_ALTERNATIVES_ROLE) or [], taken)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._fill(item, *dialog.values())
            self._save()

    def remove_selected(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        names = ", ".join(i.data(_NAME_ROLE) for i in items)
        answer = QMessageBox.question(self, config.APP_NAME, f"Usunąć z listy: {names}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        for item in items:
            self.list.takeItem(self.list.row(item))
        self._save()

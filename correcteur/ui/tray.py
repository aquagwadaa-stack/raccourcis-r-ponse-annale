"""Icône dans la zone de notification, avec le menu de l'application."""

from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

MODE_LABELS = {
    "lettres": "Lettres seules",
    "coches": "Coches sur la page",
    "les_deux": "Lettres et coches",
}


def make_icon(size: int = 64) -> QIcon:
    """Icône dessinée ici : un carré vert avec une coche blanche (aucun fichier image à livrer)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#16a34a"))
    painter.drawRoundedRect(2, 2, size - 4, size - 4, size * 0.22, size * 0.22)
    pen = QPen(QColor("white"), size * 0.13)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    path = QPainterPath(QPointF(size * 0.25, size * 0.52))
    path.lineTo(QPointF(size * 0.43, size * 0.70))
    path.lineTo(QPointF(size * 0.76, size * 0.32))
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


class Tray(QObject):
    """N'agit pas elle-même : elle émet des signaux que l'application relie."""

    capture_requested = Signal()
    toggle_requested = Signal()
    clear_requested = Signal()
    mode_changed = Signal(str)
    reindex_requested = Signal()
    open_folder_requested = Signal()
    quit_requested = Signal()

    def __init__(self, mode: str, hotkeys: dict[str, str] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        keys = hotkeys or {}

        def label(text: str, name: str) -> str:
            return f"{text}\t{keys[name].upper()}" if keys.get(name) else text

        self.icon = QSystemTrayIcon(make_icon(), self)
        self.icon.setToolTip("Correcteur d'annales")
        self.menu = QMenu()
        self._add(label("Corriger l'écran", "corriger"), self.capture_requested)
        self._add(label("Afficher / masquer les réponses", "masquer"), self.toggle_requested)
        self._add(label("Effacer les réponses", "effacer"), self.clear_requested)
        self.menu.addSeparator()
        mode_menu = self.menu.addMenu("Affichage")
        self._mode_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        for key, text in MODE_LABELS.items():
            action = mode_menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(key == mode)
            action.triggered.connect(lambda _checked=False, k=key: self.mode_changed.emit(k))
            group.addAction(action)
            self._mode_actions[key] = action
        self.menu.addSeparator()
        self._add("Mettre à jour l'index des cours", self.reindex_requested)
        self._add("Ouvrir le dossier du correcteur", self.open_folder_requested)
        self.menu.addSeparator()
        self._add("Quitter", self.quit_requested)
        self.icon.setContextMenu(self.menu)

    def _add(self, text: str, signal) -> QAction:
        action = self.menu.addAction(text)
        action.triggered.connect(lambda _checked=False: signal.emit())
        return action

    def show(self) -> bool:
        """Affiche l'icône ; renvoie ``False`` si le système n'a pas de zone de notification."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        self.icon.show()
        return True

    def set_mode(self, mode: str) -> None:
        if mode in self._mode_actions:
            self._mode_actions[mode].setChecked(True)

    def notify(self, title: str, text: str) -> None:
        if self.icon.isVisible():
            self.icon.showMessage(title, text, make_icon(), 6000)

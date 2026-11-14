"""Coches dessinées par-dessus la page, sur les bonnes réponses."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..capture import Capture
from ..models import ScreenResult

CHECK_SIZE = 26  # largeur de la coche, en pixels logiques
COLOR_RIGHT = QColor("#16a34a")
COLOR_WRONG = QColor("#dc2626")
COLOR_OUTLINE = QColor(255, 255, 255, 235)


@dataclass
class Mark:
    position: QPoint  # relatif au coin haut-gauche de l'écran
    kind: str  # "juste" (à cocher) ou "erreur" (cochée à tort par l'étudiant)


def build_marks(capture: Capture, result: ScreenResult, compare: bool) -> list[Mark]:
    """Coches à dessiner pour un résultat."""
    marks: list[Mark] = []
    for question in result.questions:
        if not question.repondue:
            continue
        for letter, (x, y) in question.positions.items():
            if letter in question.lettres:
                marks.append(Mark(capture.to_local(x, y), "juste"))
            elif compare and result.any_ticked and question.cochees and letter in question.cochees:
                marks.append(Mark(capture.to_local(x, y), "erreur"))
    return marks


class MarksOverlay(QWidget):
    """Fenêtre transparente plein écran, traversée par les clics."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._marks: list[Mark] = []
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput  # les clics atteignent la page en dessous
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowTitle("Correcteur d'annales - coches")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clear)

    def show_marks(self, capture: Capture, result: ScreenResult, compare: bool, duration_s: int) -> int:
        """Dessine les coches d'un résultat ; renvoie leur nombre."""
        self._marks = build_marks(capture, result, compare)
        if not self._marks:
            self.clear()
            return 0
        # Un pixel de moins que l'écran : une fenêtre exactement plein écran peut
        # être traitée par Windows comme une application en plein écran exclusif.
        geometry = QRect(capture.geometry)
        geometry.setHeight(geometry.height() - 1)
        self.setGeometry(geometry)
        self.show()
        self.update()
        if duration_s > 0:
            self._timer.start(duration_s * 1000)
        else:
            self._timer.stop()
        return len(self._marks)

    def clear(self) -> None:
        self._timer.stop()
        self._marks = []
        self.hide()

    def mark_count(self) -> int:
        return len(self._marks)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for mark in self._marks:
            if mark.kind == "juste":
                self._draw_check(painter, mark.position)
            else:
                self._draw_cross(painter, mark.position)

    @staticmethod
    def _stroke(painter: QPainter, path: QPainterPath, color: QColor, width: float) -> None:
        # Liseré clair sous le trait : la coche reste lisible sur fond blanc comme sur fond sombre.
        for pen_color, pen_width in ((COLOR_OUTLINE, width + 4), (color, width)):
            pen = QPen(pen_color, pen_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

    def _draw_check(self, painter: QPainter, center: QPoint) -> None:
        s = CHECK_SIZE
        cx, cy = center.x(), center.y()
        path = QPainterPath(QPointF(cx - 0.42 * s, cy + 0.02 * s))
        path.lineTo(QPointF(cx - 0.12 * s, cy + 0.32 * s))
        path.lineTo(QPointF(cx + 0.50 * s, cy - 0.42 * s))
        self._stroke(painter, path, COLOR_RIGHT, 4.5)

    def _draw_cross(self, painter: QPainter, center: QPoint) -> None:
        r = CHECK_SIZE * 0.32
        cx, cy = center.x(), center.y()
        path = QPainterPath(QPointF(cx - r, cy - r))
        path.lineTo(QPointF(cx + r, cy + r))
        path.moveTo(QPointF(cx + r, cy - r))
        path.lineTo(QPointF(cx - r, cy + r))
        self._stroke(painter, path, COLOR_WRONG, 3.5)

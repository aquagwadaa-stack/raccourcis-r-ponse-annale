"""Affichage des réponses : quelques caractères fins posés sur l'écran, sans fond.

La fenêtre ne prend jamais le clavier (le navigateur le garde). Le texte porte un
léger liseré de la couleur opposée pour rester lisible sur n'importe quel fond,
et sa couleur s'adapte à la page : foncé sur une page claire, clair sur une
page sombre.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter, QPen, QScreen
from PySide6.QtWidgets import QWidget

from ..config import DisplayConfig
from ..models import QuestionResult, ScreenResult, normalize_number

MARGIN = 12  # distance au bord de l'écran
PAD = 3  # place pour le liseré autour du texte
NO_QUESTION_TEXT = "aucune question sur cet écran"
FONT_FAMILIES = ["Segoe UI Light", "Segoe UI", "Inter", "Helvetica Neue", "Arial"]
HALO_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1))

# (texte, liseré) par rôle, pour du texte foncé (page claire) ou clair (page sombre)
_DARK = {
    "ans": QColor(17, 24, 39, 235),
    "num": QColor(17, 24, 39, 125),
    "faint": QColor(17, 24, 39, 140),
    "error": QColor(185, 28, 28, 235),
    "halo": QColor(255, 255, 255, 165),
}
_LIGHT = {
    "ans": QColor(248, 250, 252, 240),
    "num": QColor(248, 250, 252, 150),
    "faint": QColor(248, 250, 252, 150),
    "error": QColor(252, 165, 165, 240),
    "halo": QColor(0, 0, 0, 150),
}

Segment = tuple[str, str]  # (texte, rôle) ; rôle parmi ans, num, faint, error, gap


@dataclass
class _Row:
    result: QuestionResult
    order: int  # rang d'arrivée : les plus anciennes disparaissent en premier


def _number_key(numero: str) -> tuple[int, str]:
    return (int(numero), "") if numero.isdigit() else (10**9, numero)


def backdrop_luminance(thumbnail: QImage, screen_rect: QRect, area: QRect) -> float | None:
    """Luminosité moyenne (0 à 255) de la page sous ``area``, d'après la miniature d'une capture."""
    zone = area.intersected(screen_rect)
    if thumbnail.isNull() or zone.isEmpty() or screen_rect.isEmpty():
        return None
    tw, th = thumbnail.width(), thumbnail.height()
    x0 = max(0, int((zone.left() - screen_rect.left()) * tw / screen_rect.width()))
    x1 = min(tw, max(x0 + 1, round((zone.right() + 1 - screen_rect.left()) * tw / screen_rect.width())))
    y0 = max(0, int((zone.top() - screen_rect.top()) * th / screen_rect.height()))
    y1 = min(th, max(y0 + 1, round((zone.bottom() + 1 - screen_rect.top()) * th / screen_rect.height())))
    gray = thumbnail.convertToFormat(QImage.Format.Format_Grayscale8)
    total = count = 0
    for y in range(y0, y1):
        row = bytes(gray.constScanLine(y)[: gray.width()])
        total += sum(row[x0:x1])
        count += x1 - x0
    return total / count if count else None


class AnswerPanel(QWidget):
    """Numéro de question puis lettres à cocher, ex. « 4  ACD ». Une petite croix ferme l'affichage."""

    close_requested = Signal()

    def __init__(self, display: DisplayConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.display = display
        self._pending: dict[int, float] = {}
        self._rows: dict[str, _Row] = {}
        self._latest: set[str] = set()  # questions de la dernière capture : jamais cachées
        self._message: tuple[str, str] | None = None  # (texte, rôle)
        self._counter = 0
        self._wanted = True
        self._screen: QScreen | None = None
        self._moved_by_user = False
        self._drag_offset: QPoint | None = None
        self._backdrop: tuple[QImage, QRect] | None = None
        self._dark_text = True
        self._hover_close = False
        self._close_rect = QRect()
        self._lines: list[list[Segment]] = []
        self._font_cache: tuple[int, QFont, QFont] | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # pas d'entrée dans la barre des tâches
            | Qt.WindowType.WindowDoesNotAcceptFocus  # le navigateur garde le clavier
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(display.opacite)
        self.setWindowTitle("Correcteur d'annales")
        self.setMouseTracking(True)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)

    # -- interface publique ------------------------------------------------
    def start_capture(self, capture_id: int) -> None:
        """Une capture vient de partir : affiche « … » et les secondes écoulées."""
        self._pending[capture_id] = time.monotonic()
        self._message = None
        self._timer.start()
        self._refresh()

    def set_stage(self, capture_id: int, stage: str) -> None:
        """Étape en cours (lecture, recherche, correction) : non affichée, pour rester discret."""

    def end_capture(self, capture_id: int) -> None:
        self._pending.pop(capture_id, None)
        self._refresh()

    def set_result(self, capture_id: int, result: ScreenResult) -> None:
        self._pending.pop(capture_id, None)
        if not result.questions:
            self._message = (NO_QUESTION_TEXT, "faint")
        keys: set[str] = set()
        for question in result.questions:
            key = normalize_number(question.numero) or question.numero or "?"
            self._counter += 1
            self._rows[key] = _Row(question, self._counter)
            keys.add(key)
        if keys:
            self._latest = keys
            limit = max(self.display.reponses_max, len(keys))
            for key, _row in sorted(self._rows.items(), key=lambda kv: kv[1].order)[:-limit]:
                del self._rows[key]
        self._refresh()

    def set_error(self, capture_id: int, message: str) -> None:
        self._pending.pop(capture_id, None)
        self._message = (message, "error")
        self._refresh()

    def set_info(self, message: str) -> None:
        """Message d'information (démarrage, indexation des cours...) ; chaîne vide pour l'effacer."""
        self._message = (message, "faint") if message else None
        self._refresh()

    def clear(self) -> None:
        """Oublie les réponses affichées. Les captures en cours restent suivies."""
        self._rows.clear()
        self._latest = set()
        self._message = None
        self._refresh()

    def toggle(self) -> None:
        self._wanted = not self._wanted
        self._refresh()

    def set_wanted(self, wanted: bool) -> None:
        self._wanted = wanted
        self._refresh()

    def hide_for_capture(self) -> None:
        """Masque l'affichage le temps d'une capture, sans changer le choix de l'utilisateur."""
        self.hide()

    def place_on(self, screen: QScreen | None) -> None:
        """Choisit l'écran d'affichage (celui de la dernière capture)."""
        if screen is not None and screen is not self._screen:
            self._screen = screen
            self._moved_by_user = False
        self._refresh()

    def set_backdrop(self, thumbnail: QImage, screen_rect: QRect) -> None:
        """Miniature de la dernière capture, pour choisir la couleur du texte selon la page."""
        self._backdrop = (thumbnail, QRect(screen_rect))
        self._refresh()

    def has_content(self) -> bool:
        return bool(self._pending or self._rows or self._message)

    def lines(self) -> list[str]:
        """Texte brut des lignes affichées (pour les tests et le diagnostic)."""
        return ["".join(text for text, _role in line) for line in self._wrap(self._build())]

    def current_message(self) -> str | None:
        """Message d'information ou d'erreur affiché, s'il y en a un."""
        return self._message[0] if self._message else None

    @property
    def dark_text(self) -> bool:
        return self._dark_text

    # -- contenu -----------------------------------------------------------
    def _row_segments(self, key: str, row: _Row) -> list[Segment]:
        question = row.result
        answer = question.texte_court()
        role = "ans" if question.repondue else "faint"
        return [(key, "num"), ("  ", "gap"), (answer, role)]

    def _build(self) -> list[list[Segment]]:
        rows = sorted(self._rows.items(), key=lambda kv: _number_key(kv[0]))
        lines: list[list[Segment]] = []
        if self.display.disposition == "ligne":
            line: list[Segment] = []
            for key, row in rows:
                if line:
                    line.append(("     ", "gap"))
                line += self._row_segments(key, row)
            if line:
                lines.append(line)
        else:
            lines += [self._row_segments(key, row) for key, row in rows]
        if self._pending:
            seconds = int(time.monotonic() - min(self._pending.values()))
            lines.append([(f"…  {seconds} s", "faint")])
        if self._message is not None:
            lines.append([self._message])
        return lines

    # -- dessin ------------------------------------------------------------
    def _fonts(self) -> tuple[QFont, QFont]:
        size = self.display.taille_police
        if self._font_cache is None or self._font_cache[0] != size:
            main = QFont()
            main.setFamilies(FONT_FAMILIES)
            main.setPointSize(size)
            main.setWeight(QFont.Weight.Light)
            small = QFont(main)
            small.setPointSize(max(7, round(size * 0.85)))
            self._font_cache = (size, main, small)
        return self._font_cache[1], self._font_cache[2]

    def _font_for(self, role: str) -> QFont:
        main, small = self._fonts()
        return small if role in ("faint", "error") else main

    def _max_width(self) -> int:
        screen = self._screen or QGuiApplication.primaryScreen()
        return int(screen.availableGeometry().width() * 0.35) if screen is not None else 600

    def _wrap(self, lines: list[list[Segment]]) -> list[list[Segment]]:
        """Coupe en plusieurs lignes un message trop long (les réponses, elles, restent sur une ligne)."""
        limit = self._max_width()
        out: list[list[Segment]] = []
        for line in lines:
            if len(line) != 1 or line[0][1] not in ("faint", "error"):
                out.append(line)
                continue
            text, role = line[0]
            metrics = QFontMetrics(self._font_for(role))
            if metrics.horizontalAdvance(text) <= limit:
                out.append(line)
                continue
            current = ""
            for word in text.split():
                candidate = f"{current} {word}".strip()
                if current and metrics.horizontalAdvance(candidate) > limit:
                    out.append([(current, role)])
                    current = word
                else:
                    current = candidate
            out.append([(current, role)])
        return out

    def _measure(self) -> tuple[int, list[int], int]:
        """Largeur du texte, hauteur de chaque ligne, taille de la croix."""
        widths: list[int] = []
        heights: list[int] = []
        for line in self._lines:
            width = 0
            height = 0
            for text, role in line:
                metrics = QFontMetrics(self._font_for(role))
                width += metrics.horizontalAdvance(text)
                height = max(height, metrics.height())
            widths.append(width)
            heights.append(height)
        main_metrics = QFontMetrics(self._fonts()[0])
        cross = max(7, round(main_metrics.height() * 0.38))
        return max(widths, default=0), heights, cross

    def _refresh(self) -> None:
        if not self._pending:
            self._timer.stop()
        self._lines = self._wrap(self._build())
        if not (self._wanted and self._lines):
            self.hide()
            return
        text_width, heights, cross = self._measure()
        gap = max(6, cross)
        width = PAD + text_width + gap + cross + PAD
        height = PAD + sum(heights) + PAD
        self.setFixedSize(width, height)
        first = heights[0] if heights else cross
        self._close_rect = QRect(width - PAD - cross, PAD + (first - cross) // 2, cross, cross)
        if not self._moved_by_user:
            self._move_to_corner()
        self._choose_colors()
        if not self.isVisible():
            self.show()
        self.update()

    def _choose_colors(self) -> None:
        if self._backdrop is None:
            return
        luminance = backdrop_luminance(self._backdrop[0], self._backdrop[1], self.frameGeometry())
        if luminance is not None:
            self._dark_text = luminance >= 128

    def _move_to_corner(self) -> None:
        screen = self._screen or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area: QRect = screen.availableGeometry()
        position = self.display.position
        x = area.left() + MARGIN if position.endswith("gauche") else area.right() - self.width() - MARGIN
        y = area.top() + MARGIN if position.startswith("haut") else area.bottom() - self.height() - MARGIN
        self.move(x, y)

    def paintEvent(self, _event) -> None:
        palette = _DARK if self._dark_text else _LIGHT
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        # Fond invisible (alpha 1) : sans lui, Windows laisserait passer les clics entre les
        # lettres, et la croix comme le déplacement à la souris seraient presque impossibles.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        y = PAD
        for line in self._lines:
            line_height = max(QFontMetrics(self._font_for(role)).height() for _text, role in line)
            x = PAD
            for text, role in line:
                font = self._font_for(role)
                metrics = QFontMetrics(font)
                if role != "gap":
                    baseline = y + (line_height - metrics.height()) / 2 + metrics.ascent()
                    self._draw_text(painter, QPointF(x, baseline), text, font, palette[role], palette["halo"])
                x += metrics.horizontalAdvance(text)
            y += line_height
        self._draw_cross(painter, palette)

    @staticmethod
    def _draw_text(painter: QPainter, origin: QPointF, text: str, font: QFont, color: QColor, halo: QColor) -> None:
        painter.setFont(font)
        painter.setPen(halo)
        for dx, dy in HALO_OFFSETS:
            painter.drawText(origin + QPointF(dx, dy), text)
        painter.setPen(color)
        painter.drawText(origin, text)

    def _draw_cross(self, painter: QPainter, palette: dict[str, QColor]) -> None:
        rect = QRectF(self._close_rect).adjusted(1, 1, -1, -1)
        color = palette["ans"] if self._hover_close else palette["num"]
        for pen_color, width in ((palette["halo"], 3.0), (color, 1.2)):
            pen = QPen(pen_color, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(rect.topLeft(), rect.bottomRight())
            painter.drawLine(rect.topRight(), rect.bottomLeft())

    # -- souris : croix et déplacement --------------------------------------
    def _on_cross(self, pos: QPoint) -> bool:
        return self._close_rect.adjusted(-4, -4, 4, 4).contains(pos)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._on_cross(event.position().toPoint()):
            self.close_requested.emit()
            return
        self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        hover = self._on_cross(event.position().toPoint())
        if hover != self._hover_close:
            self._hover_close = hover
            self.update()
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._moved_by_user = True

    def mouseReleaseEvent(self, _event) -> None:
        self._drag_offset = None

    def leaveEvent(self, _event) -> None:
        if self._hover_close:
            self._hover_close = False
            self.update()

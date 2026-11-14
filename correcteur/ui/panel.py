"""Panneau des réponses : petite fenêtre toujours visible qui ne prend jamais le clavier."""

from __future__ import annotations

import html
import time
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QScreen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..config import DisplayConfig
from ..models import QuestionResult, ScreenResult, normalize_number

MAX_ROWS = 12  # au-delà, seules les questions corrigées le plus récemment restent affichées
MARGIN = 24  # distance au bord de l'écran
NO_QUESTION_TEXT = "Aucune question détectée sur cet écran"

COLOR_TEXT = "#f8fafc"
COLOR_DIM = "#94a3b8"
COLOR_OK = "#4ade80"
COLOR_DIFF = "#fbbf24"
COLOR_ERROR = "#f87171"

STAGE_LABELS = {
    "lecture": "lecture de l'écran",
    "recherche": "recherche dans les cours",
    "correction": "correction",
}


@dataclass
class _Pending:
    started: float
    stage: str = "lecture"


@dataclass
class _Row:
    result: QuestionResult
    compare: bool  # des cases cochées ont été vues : on peut comparer avec les réponses de l'étudiant
    order: int  # rang d'arrivée, pour ne garder que les plus récentes


def _number_key(numero: str) -> tuple[int, str]:
    return (int(numero), "") if numero.isdigit() else (10**9, numero)


class AnswerPanel(QWidget):
    """Affiche une ligne par question : numéro puis lettres justes."""

    def __init__(self, display: DisplayConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.display = display
        self._pending: dict[int, _Pending] = {}
        self._rows: dict[str, _Row] = {}
        self._message: tuple[str, str] | None = None  # (texte, couleur) : erreur ou information
        self._counter = 0
        self._wanted = True  # l'utilisateur veut-il voir le panneau ?
        self._moved_by_user = False
        self._screen: QScreen | None = None
        self._drag_offset: QPoint | None = None

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

        self._label = QLabel(self)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.addWidget(self._label)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)

    # -- interface publique ------------------------------------------------
    def start_capture(self, capture_id: int) -> None:
        """Une capture vient de partir : affiche une ligne d'attente."""
        self._pending[capture_id] = _Pending(started=time.monotonic())
        self._message = None
        self._timer.start()
        self._refresh()

    def set_stage(self, capture_id: int, stage: str) -> None:
        if capture_id in self._pending:
            self._pending[capture_id].stage = stage
            self._refresh()

    def set_result(self, capture_id: int, result: ScreenResult) -> None:
        self._pending.pop(capture_id, None)
        if not result.questions:
            self._message = (NO_QUESTION_TEXT, COLOR_DIM)
        for question in result.questions:
            key = normalize_number(question.numero) or question.numero
            previous = self._rows.get(key)
            # Une question coupée ne remplace pas une réponse déjà obtenue pour le même numéro.
            if previous is not None and previous.result.repondue and not question.repondue:
                continue
            self._counter += 1
            compare = self.display.comparer_mes_reponses and result.any_ticked and question.cochees is not None
            self._rows[key] = _Row(question, compare, self._counter)
        self._refresh()

    def set_error(self, capture_id: int, message: str) -> None:
        self._pending.pop(capture_id, None)
        self._message = (message, COLOR_ERROR)
        self._refresh()

    def set_info(self, message: str) -> None:
        """Message d'information (indexation des cours, raccourci indisponible...)."""
        self._message = (message, COLOR_DIM) if message else None
        self._refresh()

    def clear(self) -> None:
        """Oublie toutes les réponses (nouvelle annale). Les captures en cours restent suivies."""
        self._rows.clear()
        self._message = None
        self._refresh()

    def toggle(self) -> None:
        self._wanted = not self._wanted
        self._refresh()

    def set_wanted(self, wanted: bool) -> None:
        self._wanted = wanted
        self._refresh()

    def hide_for_capture(self) -> None:
        """Masque le panneau le temps d'une capture, sans changer le choix de l'utilisateur."""
        self.hide()

    def place_on(self, screen: QScreen | None) -> None:
        """Choisit l'écran d'affichage (celui de la dernière capture)."""
        if screen is not None and screen is not self._screen:
            self._screen = screen
            self._moved_by_user = False
        self._refresh()

    def has_content(self) -> bool:
        return bool(self._pending or self._rows or self._message)

    def lines(self) -> list[str]:
        """Texte brut des lignes affichées (utile aux tests et au diagnostic)."""
        return [text for text, _markup in self._build()]

    # -- rendu -------------------------------------------------------------
    def _row_text(self, row: _Row) -> tuple[str, str]:
        q = row.result
        number = f"{q.numero:>2}"
        if not q.repondue:
            body = q.texte_court()
            markup = f'<span style="color:{COLOR_DIM}; font-weight:normal; font-style:italic">{body}</span>'
            return f"{number}  {body}", self._line(number, markup)
        answer = q.texte_court()
        color = COLOR_TEXT
        suffix_plain = ""
        suffix_markup = ""
        if row.compare and (q.lettres or not q.reponse_libre):
            if (q.cochees or "") == q.lettres:
                color = COLOR_OK
            else:
                color = COLOR_DIFF
                mine = q.cochees or "rien"
                suffix_plain += f"  (toi : {mine})"
                suffix_markup += self._small(f"&nbsp;&nbsp;toi&nbsp;: {html.escape(mine)}", COLOR_DIFF)
        # « ~ » : réponse sans appui dans les cours ; « ? » : confiance basse.
        marks = ("~" if q.source == "connaissances" else "") + ("?" if q.confiance == "basse" else "")
        if marks:
            suffix_plain = f" {marks}" + suffix_plain
            suffix_markup = self._small(f"&nbsp;{marks}", COLOR_DIM) + suffix_markup
        markup = f'<span style="color:{color}">{html.escape(answer)}</span>{suffix_markup}'
        return f"{number}  {answer}{suffix_plain}", self._line(number, markup)

    def _small(self, text: str, color: str) -> str:
        size = max(9, int(self.display.taille_police * 0.6))
        return f'<span style="color:{color}; font-size:{size}pt; font-weight:normal">{text}</span>'

    @staticmethod
    def _line(number: str, markup: str) -> str:
        padded = html.escape(number).replace(" ", "&nbsp;")
        return f'<span style="color:{COLOR_DIM}; font-weight:normal">{padded}</span>&nbsp;&nbsp;{markup}'

    def _build(self) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = []
        rows = sorted(self._rows.items(), key=lambda item: item[1].order)[-MAX_ROWS:]
        hidden = len(self._rows) - len(rows)
        if hidden > 0:
            text = f"… {hidden} question(s) plus ancienne(s)"
            lines.append((text, self._small(text, COLOR_DIM)))
        for _key, row in sorted(rows, key=lambda item: _number_key(item[0])):
            lines.append(self._row_text(row))
        now = time.monotonic()
        for pending in self._pending.values():
            seconds = int(now - pending.started)
            text = f"… {STAGE_LABELS.get(pending.stage, pending.stage)} {seconds} s"
            lines.append((text, self._small(html.escape(text), COLOR_DIM)))
        if self._message is not None:
            text, color = self._message
            lines.append((text, self._small(html.escape(text), color)))
        return lines

    def _refresh(self) -> None:
        if not self._pending:
            self._timer.stop()
        lines = self._build()
        font = QFont()
        font.setFamilies(["Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Courier New"])
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(self.display.taille_police)
        font.setBold(True)
        self._label.setFont(font)
        self._label.setStyleSheet(f"color: {COLOR_TEXT};")
        self._label.setText("<br>".join(markup for _text, markup in lines))
        screen = self._screen or QGuiApplication.primaryScreen()
        if screen is not None:
            self._label.setMaximumWidth(int(screen.availableGeometry().width() * 0.45))
        self._label.setWordWrap(any(len(text) > 48 for text, _ in lines))
        if not (self._wanted and lines):
            self.hide()
            return
        self.adjustSize()
        if not self._moved_by_user:
            self._move_to_corner()
        if not self.isVisible():
            self.show()

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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(148, 163, 184, 90))
        painter.setBrush(QColor(15, 23, 42, 235))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)

    # -- déplacement à la souris -------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._moved_by_user = True

    def mouseReleaseEvent(self, _event) -> None:
        self._drag_offset = None

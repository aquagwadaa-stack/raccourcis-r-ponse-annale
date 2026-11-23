"""L'application : relie raccourcis, capture, correction et affichage."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from . import capture as cap
from .ai.base import Backend, BackendError
from .ai.select import make_backend
from .config import Config, save_setting
from .courses import CourseIndex
from .history import append_result
from .hotkeys import HotkeyManager
from .models import ScreenResult
from .pipeline import correct_screen
from .session import CorrectionSession
from .ui.marks import MarksOverlay
from .ui.panel import AnswerPanel
from .ui.tray import MODE_LABELS, Tray

log = logging.getLogger(__name__)

# Temps laissé à Windows pour retirer nos fenêtres de l'écran avant de le capturer.
HIDE_DELAY_MS = 150
UNEXPECTED_ERROR = "Erreur inattendue (détail dans historique/correcteur.log)."


class CorrecteurApp(QObject):
    """Une instance par exécution. Tout ce qui touche aux fenêtres se fait dans le fil principal ;
    les fils de travail ne communiquent que par les signaux ci-dessous."""

    stage_changed = Signal(int, str)
    capture_done = Signal(int, object)
    capture_failed = Signal(int, str)
    info = Signal(str)

    def __init__(
        self,
        cfg: Config,
        *,
        backend: Backend | None = None,
        index: CourseIndex | None = None,
        use_hotkeys: bool = True,
        use_tray: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._backend = backend
        self._backend_lock = threading.Lock()
        self.index = index if index is not None else CourseIndex(cfg.index_dir)
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="correcteur")
        self._captures: dict[int, cap.Capture] = {}
        self._sequence = 0
        self._latest = 0
        self._visible = True
        self._indexing = False

        self.session = CorrectionSession()
        self._hotkeys_paused = False
        self.panel = AnswerPanel(cfg.affichage)
        self.panel.close_requested.connect(self.close_display)
        self.marks = MarksOverlay()
        self.hotkeys = HotkeyManager(self)
        self.tray: Tray | None = None

        self.stage_changed.connect(self.panel.set_stage)
        self.capture_done.connect(self._on_done)
        self.capture_failed.connect(self._on_failed)
        self.info.connect(self.panel.set_info)
        self.hotkeys.triggered.connect(self._on_hotkey)

        if use_tray:
            keys = {"corriger": cfg.raccourcis.corriger, "masquer": cfg.raccourcis.masquer, "effacer": cfg.raccourcis.effacer}
            self.tray = Tray(cfg.affichage.mode, keys, self, font_size=cfg.affichage.taille_police)
            self.tray.capture_requested.connect(self.request_capture)
            self.tray.toggle_requested.connect(self.toggle)
            self.tray.clear_requested.connect(self.clear)
            self.tray.mode_changed.connect(self.set_mode)
            self.tray.size_changed.connect(self.set_font_size)
            self.tray.reindex_requested.connect(self.reindex)
            self.tray.pause_toggled.connect(self.set_hotkeys_paused)
            self.tray.open_folder_requested.connect(self.open_folder)
            self.tray.quit_requested.connect(self.quit)
            self.tray.show()
        self._use_hotkeys = use_hotkeys

    # -- démarrage et arrêt ------------------------------------------------
    def start(self) -> None:
        """Active les raccourcis et affiche l'état de départ."""
        problems: list[str] = list(self._start_hotkeys())
        try:
            self._get_backend()
        except BackendError as exc:
            problems.append(str(exc))
        if problems:
            for problem in problems:
                log.warning("Au démarrage : %s", problem)
            self.panel.set_info(" ".join(problems))
            return
        try:
            chunks = self.index.stats().chunks
        except Exception:
            log.exception("Index des cours illisible")
            chunks = 0
        courses = f"{chunks} passages de cours" if chunks else "aucun cours indexé"
        ready = f"Prêt : {self.cfg.raccourcis.corriger.upper()} pour corriger l'écran ({courses})."
        log.info(ready)
        self.panel.set_info(ready)
        QTimer.singleShot(7000, lambda: self._clear_info(ready))

    def _start_hotkeys(self) -> list[str]:
        if not self._use_hotkeys:
            return []
        errors = self.hotkeys.start(
            {
                "corriger": self.cfg.raccourcis.corriger,
                "masquer": self.cfg.raccourcis.masquer,
                "effacer": self.cfg.raccourcis.effacer,
            }
        )
        return list(errors.values())

    def set_hotkeys_paused(self, paused: bool) -> None:
        """Met les raccourcis en pause, par exemple pour taper un « ! » ailleurs."""
        self._hotkeys_paused = paused
        if paused:
            self.hotkeys.stop()
            message = f"Raccourcis en pause : la touche {self.cfg.raccourcis.corriger.upper()} retrouve son usage normal."
        else:
            problems = self._start_hotkeys()
            message = " ".join(problems) or "Raccourcis réactivés."
        if self.tray is not None:
            self.tray.set_paused(paused)
        self.panel.set_info(message)
        QTimer.singleShot(5000, lambda: self._clear_info(message))

    def _clear_info(self, text: str) -> None:
        if self.panel.current_message() == text:
            self.panel.set_info("")

    def quit(self) -> None:
        self.shutdown()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def shutdown(self) -> None:
        self.hotkeys.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.marks.clear()
        self.panel.hide()
        if self.tray is not None:
            self.tray.icon.hide()

    # -- actions -----------------------------------------------------------
    def _on_hotkey(self, name: str) -> None:
        {"corriger": self.request_capture, "masquer": self.toggle, "effacer": self.clear}.get(name, lambda: None)()

    def request_capture(self) -> None:
        """Capture l'écran et lance sa correction."""
        self._sequence += 1
        capture_id = self._sequence
        self._latest = capture_id
        self._visible = True
        self.panel.set_wanted(True)
        # Nos fenêtres ne doivent pas figurer sur la capture.
        self.marks.clear()
        self.panel.hide_for_capture()
        QTimer.singleShot(HIDE_DELAY_MS, lambda: self._capture_now(capture_id))

    def _capture_now(self, capture_id: int) -> None:
        try:
            capture = cap.grab()
        except Exception:
            log.exception("Capture d'écran impossible")
            self.panel.set_error(capture_id, "Capture d'écran impossible.")
            return
        self._captures[capture_id] = capture
        self.panel.place_on(capture.screen)
        self.panel.set_backdrop(capture.thumbnail, capture.geometry)
        self.panel.start_capture(capture_id)
        self._pool.submit(self._work, capture_id, capture)

    def toggle(self) -> None:
        self._visible = not self._visible
        self.panel.set_wanted(self._visible)
        if not self._visible:
            self.marks.clear()

    def clear(self) -> None:
        """Repart de zéro (nouvelle annale) : réponses affichées et mémoire de la séance."""
        self.panel.clear()
        self.marks.clear()
        self.session.clear()

    def close_display(self) -> None:
        """Croix de l'affichage : ferme sans arrêter le correcteur ; la prochaine capture le rouvre."""
        self._visible = False
        self.panel.clear()
        self.panel.set_wanted(False)
        self.marks.clear()

    def set_mode(self, mode: str) -> None:
        if mode not in MODE_LABELS:
            return
        self.cfg.affichage.mode = mode
        if mode == "lettres":
            self.marks.clear()
        if self.tray is not None:
            self.tray.set_mode(mode)
        try:
            save_setting(self.cfg.base_dir, "affichage", "mode", mode)
        except OSError:
            log.warning("Choix d'affichage non enregistré dans config.toml", exc_info=True)
        message = f"Affichage : {MODE_LABELS[mode].lower()}."
        self.panel.set_info(message)
        QTimer.singleShot(4000, lambda: self._clear_info(message))

    def set_font_size(self, size: int) -> None:
        """Taille du texte des réponses, appliquée tout de suite et enregistrée."""
        self.cfg.affichage.taille_police = size
        self.panel.set_font_size(size)
        try:
            save_setting(self.cfg.base_dir, "affichage", "taille_police", size)
        except OSError:
            log.warning("Taille du texte non enregistrée dans config.toml", exc_info=True)

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.cfg.base_dir)))

    def reindex(self) -> None:
        """Met à jour l'index des cours en arrière-plan."""
        if self._indexing:
            return
        self._indexing = True
        self.panel.set_info("Indexation des cours…")
        self._pool.submit(self._reindex_work)

    # -- fils de travail (aucun accès aux fenêtres ici) ---------------------
    def _get_backend(self) -> Backend:
        with self._backend_lock:
            if self._backend is None:
                self._backend = make_backend(self.cfg)
                log.info("Moteur utilisé : %s", self._backend.name)
            return self._backend

    def _work(self, capture_id: int, capture: cap.Capture) -> None:
        try:
            result = correct_screen(
                capture.image,
                self._get_backend(),
                self.index,
                per_question=self.cfg.ia.extraits_par_question,
                on_progress=lambda stage: self.stage_changed.emit(capture_id, stage),
                previous=lambda: self.session.previous_reading(capture_id),
                on_reading=lambda reading: self.session.record_reading(capture_id, reading),
            )
            result = self.session.merge(result)
            log.info("Capture %d : %s (%.0f s)", capture_id, result.resume() or "aucune question", result.durations.get("total", 0))
            append_result(self.cfg, result, capture.image.png)
            self.capture_done.emit(capture_id, result)
        except BackendError as exc:
            log.warning("Capture %d : %s | %s", capture_id, exc, exc.detail)
            self.capture_failed.emit(capture_id, str(exc))
        except Exception:
            log.exception("Capture %d : erreur inattendue", capture_id)
            self.capture_failed.emit(capture_id, UNEXPECTED_ERROR)

    def _reindex_work(self) -> None:
        try:
            if not self.cfg.courses_dir.is_dir():
                self.info.emit(f"Dossier des cours introuvable : {self.cfg.courses_dir}")
                return
            report = self.index.build(self.cfg.courses_dir, progress=self.info.emit)
            summary = f"Cours indexés : {report.indexed} fichier(s) lus, {report.unchanged} inchangé(s), {report.chunks} passages."
            if report.failed:
                summary += f" {len(report.failed)} fichier(s) illisible(s)."
            if report.empty_pages:
                summary += f" {report.empty_pages} page(s) scannée(s) sans texte."
            log.info(summary)
            self.info.emit(summary)
        except Exception:
            log.exception("Indexation impossible")
            self.info.emit("Indexation impossible (détail dans le journal).")
        finally:
            self._indexing = False

    # -- retour dans le fil principal --------------------------------------
    def _on_failed(self, capture_id: int, message: str) -> None:
        self._captures.pop(capture_id, None)
        self.panel.set_error(capture_id, message)

    def _on_done(self, capture_id: int, result: ScreenResult) -> None:
        capture = self._captures.pop(capture_id, None)
        mode = self.cfg.affichage.mode
        compare = self.cfg.affichage.comparer_mes_reponses
        drawn = False
        if mode != "lettres" and capture is not None and self._visible and capture_id == self._latest:
            drawn = self._draw_marks(capture, result, compare)
        if mode == "coches" and drawn:
            # Les coches suffisent, sauf pour ce qu'elles ne peuvent pas montrer.
            leftover = [q for q in result.questions if not _shown_by_marks(q)]
            if leftover:
                self.panel.set_result(capture_id, replace(result, questions=leftover))
            else:
                self.panel.end_capture(capture_id)
        else:
            self.panel.set_result(capture_id, result)

    def _draw_marks(self, capture: cap.Capture, result: ScreenResult, compare: bool) -> bool:
        try:
            # Si la page a défilé depuis la capture, les coches tomberaient à côté.
            ignore = self.panel.frameGeometry() if self.panel.isVisible() else None
            if not cap.same_screen(capture, ignore):
                log.info("La page a changé depuis la capture : pas de coches")
                return False
            return self.marks.show_marks(capture, result, compare, self.cfg.affichage.duree_coches_s) > 0
        except Exception:
            log.exception("Affichage des coches impossible")
            return False


def _shown_by_marks(question) -> bool:
    """Vrai si les coches disent tout de la question (chaque bonne lettre a une position connue)."""
    if not question.repondue or not question.lettres:
        return False
    return all(letter in question.positions for letter in question.lettres)

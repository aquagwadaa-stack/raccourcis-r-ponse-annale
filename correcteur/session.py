"""Mémoire d'une séance de correction.

Une question trop longue pour l'écran se corrige en plusieurs captures. La
séance garde donc la lecture de chaque capture (pour donner son contexte à la
suivante) et les réponses déjà données (pour compléter une question au fil des
captures). Elle s'oublie après un long moment sans capture, ou sur demande.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Callable

from .models import QuestionResult, Reading, ScreenResult, letters_to_tick, normalize_number

# Au-delà, la capture précédente appartient sans doute à une autre séance de travail.
MAX_AGE_S = 20 * 60
KEPT_READINGS = 5


class CorrectionSession:
    """Sûre à utiliser depuis plusieurs fils d'exécution."""

    def __init__(self, max_age_s: float = MAX_AGE_S, clock: Callable[[], float] = time.monotonic) -> None:
        self._max_age_s = max_age_s
        self._clock = clock
        self._lock = threading.Lock()
        self._readings: dict[int, tuple[float, Reading]] = {}
        self._answers: dict[str, tuple[float, QuestionResult]] = {}

    def clear(self) -> None:
        with self._lock:
            self._readings.clear()
            self._answers.clear()

    # -- lectures ----------------------------------------------------------
    def record_reading(self, capture_id: int, reading: Reading) -> None:
        with self._lock:
            self._readings[capture_id] = (self._clock(), reading)
            for old in sorted(self._readings)[:-KEPT_READINGS]:
                del self._readings[old]

    def previous_reading(self, capture_id: int) -> Reading | None:
        """Lecture de la dernière capture antérieure à ``capture_id``, si elle est récente."""
        with self._lock:
            earlier = [cid for cid in self._readings if cid < capture_id]
            if not earlier:
                return None
            stamp, reading = self._readings[max(earlier)]
            if self._clock() - stamp > self._max_age_s:
                return None
            return reading

    # -- réponses ----------------------------------------------------------
    def merge(self, result: ScreenResult) -> ScreenResult:
        """Complète les questions déjà corrigées en partie, et retient les réponses.

        Une question reprise d'un écran précédent garde les verdicts déjà donnés
        pour les propositions que la nouvelle capture n'a pas jugées ; une
        question dont le début est visible remplace l'ancienne réponse.
        """
        now = self._clock()
        merged: list[QuestionResult] = []
        with self._lock:
            for question in result.questions:
                key = normalize_number(question.numero) or question.numero
                known = self._answers.get(key)
                fresh = known is not None and now - known[0] <= self._max_age_s
                if question.suite and question.repondue and fresh and known[1].repondue:
                    verdicts = {**known[1].verdicts, **question.verdicts}
                    question = replace(
                        question,
                        verdicts=verdicts,
                        lettres=letters_to_tick(verdicts, question.cocher_fausses),
                        positions=dict(question.positions),
                    )
                if question.repondue:
                    self._answers[key] = (now, question)
                merged.append(question)
        return replace(result, questions=merged)

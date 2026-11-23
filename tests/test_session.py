"""Mémoire de séance : lectures précédentes et réponses complétées au fil des captures."""

from __future__ import annotations

from correcteur.models import QuestionResult, Reading, ScreenResult
from correcteur.session import CorrectionSession


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def screen(*questions):
    return ScreenResult(list(questions), 100, 100)


def test_lecture_precedente():
    clock = Clock()
    session = CorrectionSession(max_age_s=60, clock=clock)
    first, second = Reading(questions=[]), Reading(questions=[])
    session.record_reading(1, first)
    session.record_reading(3, second)
    assert session.previous_reading(1) is None
    assert session.previous_reading(2) is first
    assert session.previous_reading(4) is second
    clock.now += 61
    assert session.previous_reading(4) is None  # trop ancienne : autre séance
    session.record_reading(5, first)
    session.clear()
    assert session.previous_reading(6) is None


def test_question_completee_sur_la_capture_suivante():
    session = CorrectionSession()
    before = QuestionResult("5", "AC", verdicts={"A": True, "B": False, "C": True}, complete=False)
    session.merge(screen(before))
    after = QuestionResult("5", "E", verdicts={"D": False, "E": True}, suite=True, positions={"E": (1, 2)})
    merged = session.merge(screen(after)).questions[0]
    assert merged.lettres == "ACE" and merged.positions == {"E": (1, 2)}
    # La consigne « cocher les fausses » s'applique à toute la question.
    inverted = QuestionResult("5", "D", verdicts={"D": False, "E": True}, suite=True, cocher_fausses=True)
    assert session.merge(screen(inverted)).questions[0].lettres == "BD"


def test_question_revue_en_entier_remplace_l_ancienne_reponse():
    session = CorrectionSession()
    session.merge(screen(QuestionResult("5", "AF", verdicts={"A": True, "F": True})))
    fresh = QuestionResult("5", "B", verdicts={"A": False, "B": True})
    assert session.merge(screen(fresh)).questions[0].lettres == "B"


def test_reponse_trop_ancienne_ou_non_corrigee_ignoree():
    clock = Clock()
    session = CorrectionSession(max_age_s=60, clock=clock)
    session.merge(screen(QuestionResult("5", "A", verdicts={"A": True})))
    clock.now += 120
    late = QuestionResult("5", "E", verdicts={"E": True}, suite=True)
    assert session.merge(screen(late)).questions[0].lettres == "E"
    session.merge(screen(QuestionResult("6", "", repondue=False)))
    after = QuestionResult("6", "B", verdicts={"B": True}, suite=True)
    assert session.merge(screen(after)).questions[0].lettres == "B"

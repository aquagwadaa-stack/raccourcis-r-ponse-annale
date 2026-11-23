"""Tests de l'enchaînement lecture -> recherche -> correction, avec un moteur factice."""

from __future__ import annotations

import pytest

from correcteur.ai.base import BackendError
from correcteur.ai.prompts import format_reading
from correcteur.models import (
    PREVIOUS_SCREEN,
    AnsweredQuestion,
    Answers,
    Excerpt,
    Reading,
    ReadProposition,
    ReadQuestion,
    ScreenImage,
    Verdict,
    letters_to_tick,
    normalize_letter,
    normalize_number,
)
from correcteur.pipeline import continue_previous, correct_screen, merge, retrieve

IMAGE = ScreenImage(png=b"png", width=1000, height=800)


def prop(letter, text="x", ticked=False, x=40, y=100):
    return ReadProposition(lettre=letter, texte=text, cochee=ticked, x=x, y=y)


def question(number, props, complete=True, queries=("protéine Gs",), start=True, kind="QRM", stem=None):
    return ReadQuestion(
        numero=number,
        debut_visible=start,
        type=kind,
        enonce=stem if stem is not None else f"Énoncé {number}",
        propositions=props,
        complete=complete,
        requetes=list(queries),
    )


def answered(number, true_letters, letters="ABCDE", source="cours", cocher="vraies"):
    return AnsweredQuestion(
        numero=number,
        cocher=cocher,
        verdicts=[Verdict(lettre=l, vraie=l in true_letters) for l in letters],
        reponse_libre="",
        source=source,
        confiance="haute",
        note="",
    )


class FakeBackend:
    name = "factice"

    def __init__(self, reading=None, answers=None, read_error=None, answer_error=None):
        self.reading = reading
        self.answers = answers
        self.read_error = read_error
        self.answer_error = answer_error
        self.calls = []

    def read(self, image):
        self.calls.append("read")
        if self.read_error:
            raise self.read_error
        return self.reading

    def answer(self, image, reading, excerpts):
        self.calls.append(("answer", reading, list(excerpts)))
        if self.answer_error:
            raise self.answer_error
        return self.answers

    def check(self):
        return "ok"


class FakeSearcher:
    def __init__(self, excerpts, empty=False):
        self.excerpts = excerpts
        self.empty = empty
        self.queries = []

    def is_empty(self):
        return self.empty

    def search(self, query, limit=8):
        self.queries.append(query)
        return self.excerpts[:limit]


def test_normalisation():
    assert normalize_number("Question 12 :") == "12"
    assert normalize_number("07") == "7"
    assert normalize_number("") == ""
    assert normalize_letter(" b. ") == "B"
    assert letters_to_tick({"A": True, "B": False, "C": True}, False) == "AC"
    assert letters_to_tick({"A": True, "B": False, "C": True}, True) == "B"


def test_avec_cours_les_extraits_sont_transmis():
    reading = Reading(
        questions=[
            question("1", [prop("A", ticked=True), prop("B", y=140), prop("C", y=180)]),
            question("2", [prop("A", y=300), prop("B", y=340)]),
        ]
    )
    answers = Answers(questions=[answered("1", "AC", "ABC"), answered("2", "B", "AB")])
    backend = FakeBackend(reading, answers)
    searcher = FakeSearcher([Excerpt("cardio.pdf", 3, "texte du cours", 1.0, 7)])
    stages = []
    result = correct_screen(IMAGE, backend, searcher, on_progress=stages.append)

    assert result.resume() == "1: AC  2: B"
    assert stages == ["lecture", "recherche", "correction"]
    assert result.questions[0].cochees == "A" and result.any_ticked is True
    assert result.questions[0].positions["C"] == (40, 180)
    assert result.questions[0].verdicts == {"A": True, "B": False, "C": True}
    _, sent_reading, sent_excerpts = backend.calls[1]
    assert sent_reading is reading
    assert [e.chunk_id for e in sent_excerpts] == [7]  # dédoublonné entre les deux questions
    assert result.backend == "factice" and "total" in result.durations


def test_sans_cours_lecture_puis_correction():
    reading = Reading(questions=[question("3", [prop("A"), prop("B")])])
    backend = FakeBackend(reading, Answers(questions=[answered("3", "B", "AB", source="connaissances")]))
    stages = []
    result = correct_screen(IMAGE, backend, None, on_progress=stages.append)
    assert result.resume() == "3: B"
    assert stages == ["lecture", "correction"]
    assert backend.calls[0] == "read" and backend.calls[1][1] is reading
    assert result.questions[0].source == "connaissances"


def test_lecture_en_echec_correction_directe():
    backend = FakeBackend(None, Answers(questions=[answered("5", "A", "AB")]), read_error=BackendError("KO"))
    result = correct_screen(IMAGE, backend, FakeSearcher([Excerpt("a.pdf", 1, "x", 1.0, 1)]))
    assert result.resume() == "5: A"
    assert backend.calls[1] == ("answer", None, [])
    assert result.questions[0].positions == {} and result.questions[0].cochees is None


def test_erreur_de_correction_remonte():
    reading = Reading(questions=[question("1", [prop("A")])])
    backend = FakeBackend(reading, None, answer_error=BackendError("Clé API refusée"))
    with pytest.raises(BackendError, match="Clé API refusée"):
        correct_screen(IMAGE, backend, FakeSearcher([Excerpt("a.pdf", 1, "x", 1.0, 1)]))


def test_question_coupee_corrigee_pour_les_propositions_visibles():
    reading = Reading(
        questions=[
            question("4", [prop("A"), prop("B")]),
            question("5", [prop("A", y=500), prop("B", y=540), prop("C", y=580), prop("D", y=620)], complete=False),
        ]
    )
    backend = FakeBackend(reading, Answers(questions=[answered("4", "A", "AB"), answered("5", "AC", "ABCD")]))
    result = correct_screen(IMAGE, backend)
    # Pas de mention « coupée » : la réponse porte sur les propositions visibles.
    assert result.resume() == "4: A  5: AC"
    assert result.questions[1].complete is False and result.questions[1].repondue is True
    assert "coupée : la suite est sous le bord de l'écran" in format_reading(reading)


def test_enonce_seul_en_bas_de_l_ecran_ignore():
    reading = Reading(
        questions=[question("6", [prop("A"), prop("B")]), question("7", [], complete=False)]
    )
    backend = FakeBackend(reading, Answers(questions=[answered("6", "B", "AB")]))
    result = correct_screen(IMAGE, backend)
    assert result.resume() == "6: B"
    sent = backend.calls[1][1]
    assert [q.numero for q in sent.questions] == ["6"]  # l'énoncé seul n'est pas envoyé à la correction


def test_aucune_question_corrigeable_pas_d_appel_de_correction():
    for reading in (Reading(questions=[]), Reading(questions=[question("9", [], complete=False)])):
        backend = FakeBackend(reading, None)
        result = correct_screen(IMAGE, backend, FakeSearcher([Excerpt("a.pdf", 1, "x", 1.0, 1)]))
        assert result.questions == [] and backend.calls == ["read"]


def test_consigne_cocher_les_fausses():
    reading = Reading(questions=[question("2", [prop(l) for l in "ABCDE"])])
    answers = Answers(questions=[answered("2", "BCE", cocher="fausses")])
    result = merge(reading, answers, IMAGE)
    # Les lettres affichées sont celles à cocher : ici les propositions fausses.
    assert result.questions[0].lettres == "AD"
    assert result.questions[0].cocher_fausses is True
    assert result.questions[0].verdicts["B"] is True


def test_suite_d_une_question_sur_l_ecran_suivant():
    before = Reading(
        questions=[
            question("4", [prop("A"), prop("B")]),
            question("5", [prop("A", "alpha", y=500), prop("B", "beta", y=540), prop("C", "gamma", y=580)],
                     complete=False, stem="Concernant la protéine Gs :", queries=("protéine Gs",)),
        ]
    )
    now = Reading(
        questions=[
            question("", [prop("D", "delta", y=40), prop("E", "epsilon", y=80)], start=False, kind="autre",
                     stem="", queries=("adénylate cyclase",)),
            question("6", [prop("A", y=300)]),
        ]
    )
    joined = continue_previous(now, before)
    first = joined.questions[0]
    assert first.numero == "5" and first.enonce == "Concernant la protéine Gs :" and first.type == "QRM"
    assert [p.lettre for p in first.propositions] == ["A", "B", "C", "D", "E"]
    assert [p.from_previous_screen for p in first.propositions] == [True, True, True, False, False]
    assert first.requetes == ["protéine Gs", "adénylate cyclase"]
    text = format_reading(joined)
    assert "Question 5 (QRM, suite : début sur l'écran précédent)" in text
    assert "A. alpha (écran précédent)" in text and "D. delta\n" in text

    backend = FakeBackend(now, Answers(questions=[answered("5", "BD"), answered("6", "A", "A")]))
    seen = []
    result = correct_screen(IMAGE, backend, previous=lambda: before, on_reading=seen.append)
    assert seen[0].questions[0].numero == "5"
    assert result.resume() == "5: BD  6: A"
    q5 = result.questions[0]
    assert q5.suite is True and set(q5.positions) == {"D", "E"}  # coches seulement sur ce qui est à l'écran
    assert backend.calls[1][1].questions[0].numero == "5"


def test_suite_sans_ecran_precedent_corrigee_telle_quelle():
    now = Reading(questions=[question("", [prop("D"), prop("E")], start=False)])
    assert continue_previous(now, None) is now
    backend = FakeBackend(now, Answers(questions=[answered("", "E", "DE")]))
    result = correct_screen(IMAGE, backend)
    assert result.resume() == "?: E"


def test_suite_qui_reprend_une_proposition_deja_visible():
    before = Reading(questions=[question("8", [prop("A"), prop("B")], complete=False)])
    now = Reading(questions=[question("", [prop("B", "texte complet"), prop("C")], start=False)])
    first = continue_previous(now, before).questions[0]
    assert [(p.lettre, p.from_previous_screen) for p in first.propositions] == [("A", True), ("B", False), ("C", False)]
    assert first.propositions[0].x == PREVIOUS_SCREEN


def test_fusion_appariement_par_ordre_si_numeros_differents():
    reading = Reading(questions=[question("12", [prop("A"), prop("B")])])
    answers = Answers(questions=[answered("1", "B", "AB")])
    assert merge(reading, answers, IMAGE).resume() == "12: B"


def test_fusion_lettres_inconnues_ecartees_et_positions_hors_image():
    reading = Reading(questions=[question("1", [prop("A", x=-1, y=-1), prop("B", x=5000, y=10)])])
    answers = Answers(questions=[answered("1", "ABZ", "ABZ")])
    result = merge(reading, answers, IMAGE)
    assert result.questions[0].lettres == "AB" and result.questions[0].positions == {}


def test_question_oubliee_par_la_correction():
    reading = Reading(questions=[question("1", [prop("A")]), question("2", [prop("A")])])
    result = merge(reading, Answers(questions=[answered("1", "A", "A")]), IMAGE)
    assert result.resume() == "1: A  2: ?"


def test_aucune_proposition_juste():
    reading = Reading(questions=[question("4", [prop("A"), prop("B")])])
    assert merge(reading, Answers(questions=[answered("4", "", "AB")]), IMAGE).resume() == "4: aucune"


def test_recherche_un_passage_par_proposition_puis_plafond():
    class PerQuery:
        def is_empty(self):
            return False

        def search(self, query, limit=8):
            return [Excerpt("c.pdf", 1, f"passage {query}", 1.0, abs(hash(query)) % 10_000)]

    reading = Reading(questions=[question("1", [prop("A", "alpha"), prop("B", "beta"), prop("C", "gamma")])])
    excerpts = retrieve(reading, PerQuery(), per_question=3)
    texts = " | ".join(e.text for e in excerpts)
    assert len(excerpts) == 3 and all(word in texts for word in ("alpha", "beta", "gamma"))
    assert retrieve(reading, PerQuery(), per_question=8, max_chars=10)[:1]

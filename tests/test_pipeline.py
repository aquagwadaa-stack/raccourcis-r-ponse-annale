"""Tests de l'enchaînement lecture -> recherche -> correction, avec un moteur factice."""

from __future__ import annotations

import pytest

from correcteur.ai.base import BackendError
from correcteur.models import (
    AnsweredQuestion,
    Answers,
    Excerpt,
    Reading,
    ReadProposition,
    ReadQuestion,
    ScreenImage,
    Verdict,
    normalize_letter,
    normalize_number,
)
from correcteur.pipeline import correct_screen, merge, retrieve

IMAGE = ScreenImage(png=b"png", width=1000, height=800)


def prop(letter, text, ticked=False, x=40, y=100):
    return ReadProposition(lettre=letter, texte=text, cochee=ticked, x=x, y=y)


def question(number, props, complete=True, queries=("protéine Gs",)):
    return ReadQuestion(
        numero=number,
        type="QRM",
        enonce=f"Énoncé {number}",
        propositions=props,
        complete=complete,
        requetes=list(queries),
    )


def answered(number, right, letters="ABCDE", source="cours"):
    return AnsweredQuestion(
        numero=number,
        verdicts=[Verdict(lettre=l, correcte=l in right) for l in letters],
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
        self.answer_calls = []

    def read(self, image):
        if self.read_error:
            raise self.read_error
        return self.reading

    def answer(self, image, reading, excerpts):
        self.answer_calls.append((reading, list(excerpts)))
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


def test_avec_cours_les_extraits_sont_transmis():
    reading = Reading(
        questions=[
            question("1", [prop("A", "a", ticked=True), prop("B", "b", y=140), prop("C", "c", y=180)]),
            question("2", [prop("A", "a", y=300), prop("B", "b", y=340)]),
        ]
    )
    answers = Answers(questions=[answered("1", "AC", "ABC"), answered("2", "B", "AB")])
    backend = FakeBackend(reading, answers)
    searcher = FakeSearcher([Excerpt("cardio.pdf", 3, "texte du cours", 1.0, 7)])
    stages = []
    result = correct_screen(IMAGE, backend, searcher, on_progress=stages.append)

    assert result.resume() == "1: AC  2: B"
    assert stages == ["lecture", "recherche", "correction"]
    assert result.questions[0].cochees == "A"
    assert result.any_ticked is True
    assert result.questions[0].positions["C"] == (40, 180)
    sent_reading, sent_excerpts = backend.answer_calls[0]
    assert sent_reading is reading
    assert [e.chunk_id for e in sent_excerpts] == [7]  # dédoublonné entre les deux questions
    assert result.backend == "factice"
    assert "total" in result.durations


def test_sans_cours_lecture_et_correction_en_parallele():
    reading = Reading(questions=[question("3", [prop("A", "a"), prop("B", "b")])])
    backend = FakeBackend(reading, Answers(questions=[answered("3", "B", "AB", source="connaissances")]))
    result = correct_screen(IMAGE, backend, None)
    assert result.resume() == "3: B"
    assert backend.answer_calls[0] == (None, [])
    assert result.questions[0].source == "connaissances"


def test_sans_cours_lecture_en_echec_on_garde_les_reponses():
    backend = FakeBackend(
        None, Answers(questions=[answered("3", "B", "AB")]), read_error=BackendError("lecture KO")
    )
    result = correct_screen(IMAGE, backend, FakeSearcher([], empty=True))
    assert result.resume() == "3: B"
    assert result.questions[0].positions == {}
    assert result.questions[0].cochees is None


def test_avec_cours_lecture_en_echec_correction_directe():
    backend = FakeBackend(
        None, Answers(questions=[answered("5", "A", "AB")]), read_error=BackendError("lecture KO")
    )
    result = correct_screen(IMAGE, backend, FakeSearcher([Excerpt("a.pdf", 1, "x", 1.0, 1)]))
    assert result.resume() == "5: A"
    assert backend.answer_calls[0] == (None, [])


def test_erreur_de_correction_remonte():
    reading = Reading(questions=[question("1", [prop("A", "a")])])
    backend = FakeBackend(reading, None, answer_error=BackendError("Clé API refusée"))
    with pytest.raises(BackendError, match="Clé API refusée"):
        correct_screen(IMAGE, backend, FakeSearcher([Excerpt("a.pdf", 1, "x", 1.0, 1)]))


def test_question_coupee_non_corrigee():
    reading = Reading(
        questions=[
            question("1", [prop("A", "a"), prop("B", "b")]),
            question("2", [prop("A", "a")], complete=False),
        ]
    )
    backend = FakeBackend(reading, Answers(questions=[answered("1", "A", "AB")]))
    result = correct_screen(IMAGE, backend, FakeSearcher([Excerpt("a.pdf", 1, "x", 1.0, 1)]))
    assert result.resume() == "1: A  2: coupée"
    assert result.questions[1].repondue is False


def test_aucune_question_pas_d_appel_de_correction():
    backend = FakeBackend(Reading(questions=[]), None)
    result = correct_screen(IMAGE, backend, FakeSearcher([Excerpt("a.pdf", 1, "x", 1.0, 1)]))
    assert result.questions == []
    assert backend.answer_calls == []


def test_toutes_coupees_pas_d_appel_de_correction():
    reading = Reading(questions=[question("9", [prop("A", "a")], complete=False)])
    backend = FakeBackend(reading, None)
    result = correct_screen(IMAGE, backend, FakeSearcher([Excerpt("a.pdf", 1, "x", 1.0, 1)]))
    assert result.resume() == "9: coupée"
    assert backend.answer_calls == []


def test_fusion_appariement_par_ordre_si_numeros_differents():
    reading = Reading(questions=[question("12", [prop("A", "a"), prop("B", "b")])])
    answers = Answers(questions=[answered("1", "B", "AB")])
    result = merge(reading, answers, IMAGE)
    assert result.resume() == "12: B"


def test_fusion_lettres_inconnues_ecartees_et_positions_hors_image():
    reading = Reading(
        questions=[question("1", [prop("A", "a", x=-1, y=-1), prop("B", "b", x=5000, y=10)])]
    )
    answers = Answers(questions=[answered("1", "ABZ", "ABZ")])
    result = merge(reading, answers, IMAGE)
    assert result.questions[0].lettres == "AB"
    assert result.questions[0].positions == {}


def test_aucune_proposition_juste():
    reading = Reading(questions=[question("4", [prop("A", "a"), prop("B", "b")])])
    result = merge(reading, Answers(questions=[answered("4", "", "AB")]), IMAGE)
    assert result.resume() == "4: aucune"


def test_recherche_un_passage_par_proposition_puis_plafond():
    class PerQuery:
        def is_empty(self):
            return False

        def search(self, query, limit=8):
            # chaque requête renvoie un passage qui lui est propre
            return [Excerpt("c.pdf", 1, f"passage {query}", 1.0, abs(hash(query)) % 10_000)]

    reading = Reading(
        questions=[question("1", [prop("A", "alpha"), prop("B", "beta"), prop("C", "gamma")])]
    )
    excerpts = retrieve(reading, PerQuery(), per_question=3)
    texts = " | ".join(e.text for e in excerpts)
    assert len(excerpts) == 3
    assert "alpha" in texts and "beta" in texts and "gamma" in texts
    assert retrieve(reading, PerQuery(), per_question=8, max_chars=10)[:1]  # jamais vide si un passage existe

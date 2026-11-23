"""Application assemblée, avec un moteur factice (aucun appel à Claude)."""

from __future__ import annotations

import time

import pytest

from correcteur import app as app_module
from correcteur.ai.base import BackendError
from correcteur.app import CorrecteurApp
from correcteur.config import Config
from correcteur.models import AnsweredQuestion, Answers, Reading, ReadProposition, ReadQuestion, Verdict


class FakeBackend:
    name = "factice"

    def __init__(self, error: Exception | None = None, delay: float = 0.0):
        self.error = error
        self.delay = delay
        self.calls = 0

    def read(self, image):
        props = [
            ReadProposition(lettre=l, texte=f"proposition {l}", cochee=l in "AB", x=40, y=100 + 60 * i)
            for i, l in enumerate("ABC")
        ]
        return Reading(
            questions=[
                ReadQuestion(numero="7", debut_visible=True, type="QRM", enonce="Énoncé", propositions=props,
                             complete=True, requetes=["x"]),
                # Énoncé seul, coupé en bas de l'écran : rien à corriger sur cette capture.
                ReadQuestion(numero="8", debut_visible=True, type="QRM", enonce="Coupée", propositions=[],
                             complete=False, requetes=[]),
            ]
        )

    def answer(self, image, reading, excerpts):
        self.calls += 1
        time.sleep(self.delay)
        if self.error:
            raise self.error
        verdicts = [Verdict(lettre=l, vraie=l in "AC") for l in "ABC"]
        return Answers(
            questions=[
                AnsweredQuestion(numero="7", cocher="vraies", verdicts=verdicts, reponse_libre="",
                                 source="connaissances", confiance="haute", note="parce que")
            ]
        )

    def check(self):
        return "factice"


def wait_until(qapp, condition, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture()
def make_app(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "HIDE_DELAY_MS", 1)
    created = []

    def factory(backend=None, **display):
        cfg = Config(base_dir=tmp_path)
        for key, value in display.items():
            setattr(cfg.affichage, key, value)
        application = CorrecteurApp(cfg, backend=backend or FakeBackend(), use_hotkeys=False, use_tray=False)
        created.append(application)
        return application

    yield factory
    for application in created:
        application.shutdown()


def test_capture_puis_lettres_et_historique(qapp, make_app, tmp_path):
    application = make_app()
    application.request_capture()
    assert wait_until(qapp, lambda: application.panel.lines() == ["7  AC"])
    assert application.marks.mark_count() == 0  # mode « lettres »
    history = next((tmp_path / "historique").glob("*.txt")).read_text(encoding="utf-8")
    assert "7 : AC" in history and "connaissances, confiance haute" in history
    assert "tu avais coché : AB" in history and "parce que" in history and "moteur factice" in history


def test_mode_coches(qapp, make_app):
    application = make_app(mode="coches")
    application.request_capture()
    assert wait_until(qapp, lambda: application.marks.mark_count() > 0)
    # A et C justes, B cochée à tort ; tout se voit sur la page, le panneau reste vide.
    assert application.marks.mark_count() == 3 and application.marks.isVisible()
    assert application.panel.lines() == []
    application.toggle()
    assert not application.marks.isVisible() and not application.panel.isVisible()


def test_mode_les_deux_puis_effacer(qapp, make_app):
    application = make_app(mode="les_deux", comparer_mes_reponses=False)
    application.request_capture()
    assert wait_until(qapp, lambda: application.marks.mark_count() == 2)
    assert application.panel.lines() == ["7  AC"]
    application.clear()
    assert application.panel.lines() == [] and application.marks.mark_count() == 0


def test_pas_de_coches_si_la_page_a_change(qapp, make_app, monkeypatch):
    monkeypatch.setattr(app_module.cap, "same_screen", lambda capture, ignore=None: False)
    application = make_app(mode="coches")
    application.request_capture()
    assert wait_until(qapp, lambda: application.panel.lines() == ["7  AC"])  # repli sur les lettres
    assert application.marks.mark_count() == 0


def test_croix_ferme_sans_arreter(qapp, make_app):
    application = make_app(mode="les_deux")
    application.request_capture()
    assert wait_until(qapp, lambda: application.panel.lines() == ["7  AC"])
    application.panel.close_requested.emit()
    assert not application.panel.isVisible() and application.marks.mark_count() == 0
    application.request_capture()  # la capture suivante rouvre l'affichage
    assert wait_until(qapp, lambda: application.panel.lines() == ["7  AC"] and application.panel.isVisible())


def test_erreur_du_moteur_affichee(qapp, make_app):
    application = make_app(FakeBackend(error=BackendError("Clé API refusée", detail="401")))
    application.request_capture()
    assert wait_until(qapp, lambda: application.panel.lines() == ["Clé API refusée"])
    application._backend = FakeBackend(error=RuntimeError("boum"))
    application.request_capture()
    assert wait_until(qapp, lambda: application.panel.current_message() == app_module.UNEXPECTED_ERROR)


def test_plusieurs_captures_en_cours(qapp, make_app):
    backend = FakeBackend(delay=0.15)
    application = make_app(backend)
    application.request_capture()
    wait_until(qapp, lambda: len(application.panel.lines()) == 1)
    application.request_capture()
    assert wait_until(qapp, lambda: backend.calls == 2 and not any(l.startswith("…") for l in application.panel.lines()))
    assert application.panel.lines() == ["7  AC"]


class SplitQuestionBackend:
    """Deux captures : la question 5 commence sur la première et finit sur la seconde."""

    name = "factice"

    def __init__(self):
        self.reads = 0
        self.sent = []

    def read(self, image):
        self.reads += 1
        if self.reads == 1:
            props = [ReadProposition(lettre=l, texte=f"p{l}", cochee=False, x=40, y=100 + 40 * i)
                     for i, l in enumerate("ABC")]
            question = ReadQuestion(numero="5", debut_visible=True, type="QRM", enonce="Concernant FasL :",
                                    propositions=props, complete=False, requetes=["FasL"])
        else:
            props = [ReadProposition(lettre=l, texte=f"p{l}", cochee=False, x=40, y=60 + 40 * i)
                     for i, l in enumerate("DE")]
            question = ReadQuestion(numero="", debut_visible=False, type="autre", enonce="",
                                    propositions=props, complete=True, requetes=[])
        return Reading(questions=[question])

    def answer(self, image, reading, excerpts):
        self.sent.append(reading)
        letters = [p.lettre for p in reading.questions[0].propositions]
        # Sur la seconde capture, la correction ne rejuge que D et E : A à C viennent de la première.
        judged = letters if self.reads == 1 else ["D", "E"]
        return Answers(questions=[AnsweredQuestion(
            numero="5", cocher="vraies", verdicts=[Verdict(lettre=l, vraie=l in "BE") for l in judged],
            reponse_libre="", source="cours", confiance="haute", note="")])

    def check(self):
        return "factice"


def test_question_a_cheval_sur_deux_captures(qapp, make_app):
    backend = SplitQuestionBackend()
    application = make_app(backend)
    application.request_capture()
    assert wait_until(qapp, lambda: application.panel.lines() == ["5  B"])
    application.request_capture()
    assert wait_until(qapp, lambda: application.panel.lines() == ["5  BE"])
    joined = backend.sent[1].questions[0]
    assert joined.numero == "5" and joined.enonce == "Concernant FasL :"
    assert [p.lettre for p in joined.propositions] == ["A", "B", "C", "D", "E"]
    application.clear()  # effacer oublie aussi la séance
    assert application.session.previous_reading(99) is None


def test_demarrage_mode_enregistre_et_pause(qapp, make_app, tmp_path):
    application = make_app()
    application.start()
    assert application.panel.lines()[0].startswith("Prêt : ! pour corriger")
    application.set_mode("coches")
    assert application.cfg.affichage.mode == "coches"
    assert 'mode = "coches"' in (tmp_path / "config.toml").read_text(encoding="utf-8")
    application.set_mode("inconnu")
    assert application.cfg.affichage.mode == "coches"
    application.set_font_size(11)
    assert application.panel.display.taille_police == 11
    assert "taille_police = 11" in (tmp_path / "config.toml").read_text(encoding="utf-8")
    application.set_hotkeys_paused(True)
    assert "pause" in application.panel.lines()[0]
    application.set_hotkeys_paused(False)
    assert application.panel.lines()[0] == "Raccourcis réactivés."


def test_reindexation_en_arriere_plan(qapp, make_app, tmp_path):
    application = make_app()
    application.reindex()
    assert wait_until(qapp, lambda: any("introuvable" in line for line in application.panel.lines()))
    (tmp_path / "cours").mkdir()
    (tmp_path / "cours" / "rein.txt").write_text("Le néphron filtre le plasma au niveau du glomérule rénal.", encoding="utf-8")
    assert wait_until(qapp, lambda: not application._indexing)
    application.reindex()
    assert wait_until(qapp, lambda: any("Cours indexés : 1 fichier" in line for line in application.panel.lines()))
    assert application.index.search("néphron glomérule")[0].source == "rein.txt"

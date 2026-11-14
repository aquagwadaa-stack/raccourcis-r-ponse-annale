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
                ReadQuestion(numero="7", type="QRM", enonce="Énoncé", propositions=props, complete=True, requetes=["x"]),
                ReadQuestion(numero="8", type="QRM", enonce="Coupée", propositions=[], complete=False, requetes=[]),
            ]
        )

    def answer(self, image, reading, excerpts):
        self.calls += 1
        time.sleep(self.delay)
        if self.error:
            raise self.error
        verdicts = [Verdict(lettre=l, correcte=l in "AC") for l in "ABC"]
        return Answers(
            questions=[
                AnsweredQuestion(numero="7", verdicts=verdicts, reponse_libre="", source="connaissances", confiance="haute", note="parce que")
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
    assert wait_until(qapp, lambda: application.panel.lines() == [" 7  AC ~  (toi : AB)", " 8  coupée"])
    assert application.marks.mark_count() == 0  # mode « lettres »
    history = next((tmp_path / "historique").glob("*.txt")).read_text(encoding="utf-8")
    assert "7 : AC" in history and "connaissances, confiance haute" in history
    assert "tu avais coché : AB" in history and "parce que" in history and "moteur factice" in history


def test_mode_coches(qapp, make_app):
    application = make_app(mode="coches")
    application.request_capture()
    assert wait_until(qapp, lambda: application.marks.mark_count() > 0)
    # A et C justes, B cochée à tort ; la question coupée reste signalée dans le panneau.
    assert application.marks.mark_count() == 3 and application.marks.isVisible()
    assert application.panel.lines() == [" 8  coupée"]
    application.toggle()
    assert not application.marks.isVisible() and not application.panel.isVisible()


def test_mode_les_deux_puis_effacer(qapp, make_app):
    application = make_app(mode="les_deux", comparer_mes_reponses=False)
    application.request_capture()
    assert wait_until(qapp, lambda: application.marks.mark_count() == 2)
    assert application.panel.lines() == [" 7  AC ~", " 8  coupée"]
    application.clear()
    assert application.panel.lines() == [] and application.marks.mark_count() == 0


def test_pas_de_coches_si_la_page_a_change(qapp, make_app, monkeypatch):
    monkeypatch.setattr(app_module.cap, "same_screen", lambda capture, ignore=None: False)
    application = make_app(mode="coches")
    application.request_capture()
    assert wait_until(qapp, lambda: len(application.panel.lines()) == 2)
    assert application.marks.mark_count() == 0
    assert application.panel.lines()[0].startswith(" 7  AC")  # repli sur les lettres


def test_erreur_du_moteur_affichee(qapp, make_app):
    application = make_app(FakeBackend(error=BackendError("Clé API refusée", detail="401")))
    application.request_capture()
    assert wait_until(qapp, lambda: application.panel.lines() == ["Clé API refusée"])
    application._backend = FakeBackend(error=RuntimeError("boum"))
    application.request_capture()
    assert wait_until(qapp, lambda: application.panel.lines() == [app_module.UNEXPECTED_ERROR])


def test_plusieurs_captures_en_cours(qapp, make_app):
    backend = FakeBackend(delay=0.15)
    application = make_app(backend)
    application.request_capture()
    wait_until(qapp, lambda: len(application.panel.lines()) == 1)
    application.request_capture()
    assert wait_until(qapp, lambda: sum(line.startswith("…") for line in application.panel.lines()) == 2, timeout=2)
    assert wait_until(qapp, lambda: backend.calls == 2 and not any(l.startswith("…") for l in application.panel.lines()))
    assert application.panel.lines()[0].startswith(" 7  AC")


def test_demarrage_et_changement_de_mode(qapp, make_app):
    application = make_app()
    application.start()
    assert application.panel.lines()[0].startswith("Prêt : F8")
    application.set_mode("coches")
    assert application.cfg.affichage.mode == "coches" and "coches" in application.panel.lines()[0]
    application.set_mode("inconnu")
    assert application.cfg.affichage.mode == "coches"


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

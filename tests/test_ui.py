"""Panneau des réponses, coches et icône de notification."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QImage

from correcteur import capture as cap
from correcteur.config import DisplayConfig
from correcteur.models import QuestionResult, ScreenResult
from correcteur.ui.marks import MarksOverlay, build_marks
from correcteur.ui.panel import MAX_ROWS, NO_QUESTION_TEXT, AnswerPanel
from correcteur.ui.tray import MODE_LABELS, Tray, make_icon


def result(*questions, ticked=False):
    return ScreenResult(list(questions), 1000, 800, "factice", {"total": 12.0}, any_ticked=ticked)


def q(number, letters, **kwargs):
    kwargs.setdefault("source", "cours")
    return QuestionResult(number, letters, **kwargs)


def test_panneau_attente_puis_resultat(qapp):
    panel = AnswerPanel(DisplayConfig())
    assert not panel.has_content() and not panel.isVisible()
    panel.start_capture(1)
    assert panel.isVisible() and panel.lines()[0].startswith("… lecture de l'écran")
    panel.set_stage(1, "recherche")
    assert "recherche dans les cours" in panel.lines()[0]
    panel.set_result(1, result(q("2", "C"), q("1", "ACD")))
    assert panel.lines() == [" 1  ACD", " 2  C"]
    panel.set_result(2, result(q("10", "BE"), q("2", "AD")))  # la question 2 est recapturée
    assert panel.lines() == [" 1  ACD", " 2  AD", "10  BE"]


def test_panneau_reperes(qapp):
    panel = AnswerPanel(DisplayConfig())
    panel.set_result(
        1,
        result(
            q("1", "ACD", cochees="ACD"),
            q("2", "C", cochees="BC", source="connaissances", confiance="basse"),
            q("3", "", cochees=""),
            q("4", "", complete=False, repondue=False),
            q("5", "", reponse_libre="Nerf vague"),
            ticked=True,
        ),
    )
    assert panel.lines() == [
        " 1  ACD",
        " 2  C ~?  (toi : BC)",
        " 3  aucune",
        " 4  coupée",
        " 5  Nerf vague",
    ]
    # Sans case cochée vue à l'écran, aucune comparaison n'est affichée.
    panel.clear()
    panel.set_result(2, result(q("2", "C", cochees="")))
    assert panel.lines() == [" 2  C"]
    # Comparaison désactivée dans la configuration.
    off = AnswerPanel(DisplayConfig(comparer_mes_reponses=False))
    off.set_result(1, result(q("2", "C", cochees="B"), ticked=True))
    assert off.lines() == [" 2  C"]


def test_panneau_question_coupee_ne_remplace_pas_une_reponse(qapp):
    panel = AnswerPanel(DisplayConfig())
    panel.set_result(1, result(q("3", "AB")))
    panel.set_result(2, result(q("3", "", complete=False, repondue=False)))
    assert panel.lines() == [" 3  AB"]


def test_panneau_erreur_message_effacement_bascule(qapp):
    panel = AnswerPanel(DisplayConfig())
    panel.start_capture(1)
    panel.set_error(1, "Clé API refusée")
    assert panel.lines() == ["Clé API refusée"]
    panel.set_result(2, result())
    assert panel.lines() == [NO_QUESTION_TEXT]
    panel.start_capture(3)  # un nouvel essai efface le message précédent
    assert len(panel.lines()) == 1 and panel.lines()[0].startswith("…")
    panel.set_result(3, result(q("1", "A")))
    panel.toggle()
    assert not panel.isVisible() and panel.lines() == [" 1  A"]
    panel.toggle()
    assert panel.isVisible()
    panel.hide_for_capture()
    assert not panel.isVisible()
    panel.clear()
    assert panel.lines() == [] and not panel.isVisible()
    panel.set_info("Index des cours mis à jour")
    assert panel.lines() == ["Index des cours mis à jour"] and panel.isVisible()


def test_panneau_plafond_de_lignes(qapp):
    panel = AnswerPanel(DisplayConfig())
    for i in range(1, MAX_ROWS + 6):
        panel.set_result(i, result(q(str(i), "A")))
    lines = panel.lines()
    assert len(lines) == MAX_ROWS + 1 and "5 question(s) plus ancienne(s)" in lines[0]
    assert lines[1] == " 6  A" and lines[-1] == f"{MAX_ROWS + 5:>2}  A"


def test_panneau_position(qapp):
    for position in ("bas_droite", "haut_gauche"):
        panel = AnswerPanel(DisplayConfig(position=position))
        panel.set_result(1, result(q("1", "ACD")))
        area = qapp.primaryScreen().availableGeometry()
        center = panel.frameGeometry().center()
        assert (center.x() > area.center().x()) is position.endswith("droite")
        assert (center.y() > area.center().y()) is position.startswith("bas")
        assert area.contains(panel.frameGeometry())


def make_capture():
    image = QImage(2000, 1000, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    return cap.capture_from_image(image, QRect(100, 50, 1000, 500))


def test_coches(qapp):
    capture = make_capture()
    res = result(
        q("1", "AC", cochees="AB", positions={"A": (200, 100), "B": (200, 200), "C": (200, 300), "D": (200, 400)}),
        q("2", "A", complete=False, repondue=False, positions={"A": (200, 600)}),
        ticked=True,
    )
    marks = build_marks(capture, res, compare=True)
    assert [(m.kind, m.position) for m in marks] == [
        ("juste", QPoint(100, 50)),
        ("erreur", QPoint(100, 100)),
        ("juste", QPoint(100, 150)),
    ]
    assert [m.kind for m in build_marks(capture, res, compare=False)] == ["juste", "juste"]

    overlay = MarksOverlay()
    assert overlay.show_marks(capture, res, True, 0) == 3 and overlay.isVisible()
    assert overlay.geometry().topLeft() == QPoint(100, 50) and overlay.width() == 1000
    overlay.grab()  # le dessin ne doit pas lever d'erreur
    overlay.clear()
    assert not overlay.isVisible() and overlay.mark_count() == 0
    assert overlay.show_marks(capture, result(q("1", "A")), True, 5) == 0 and not overlay.isVisible()


def test_icone_et_menu(qapp):
    assert not make_icon().isNull()
    tray = Tray("lettres", {"corriger": "f8", "masquer": "f9", "effacer": "shift+f8"})
    texts = [a.text() for a in tray.menu.actions() if a.text()]
    assert texts[0] == "Corriger l'écran\tF8" and texts[-1] == "Quitter"
    seen = []
    tray.capture_requested.connect(lambda: seen.append("corriger"))
    tray.quit_requested.connect(lambda: seen.append("quitter"))
    tray.mode_changed.connect(seen.append)
    tray.menu.actions()[0].trigger()
    tray.menu.actions()[-1].trigger()
    tray._mode_actions["coches"].trigger()
    assert seen == ["corriger", "quitter", "coches"]
    tray.set_mode("les_deux")
    assert tray._mode_actions["les_deux"].isChecked() and set(MODE_LABELS) == set(tray._mode_actions)
    tray.notify("titre", "texte")  # sans icône visible : sans effet, sans erreur

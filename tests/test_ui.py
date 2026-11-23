"""Affichage discret des réponses, coches et icône de notification."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from correcteur import capture as cap
from correcteur.config import DisplayConfig
from correcteur.models import QuestionResult, ScreenResult
from correcteur.ui.marks import MarksOverlay, build_marks
from correcteur.ui.panel import NO_QUESTION_TEXT, AnswerPanel, backdrop_luminance
from correcteur.ui.tray import MODE_LABELS, Tray, make_icon


def result(*questions, ticked=False):
    return ScreenResult(list(questions), 1000, 800, "factice", {"total": 12.0}, any_ticked=ticked)


def q(number, letters, **kwargs):
    kwargs.setdefault("source", "cours")
    return QuestionResult(number, letters, **kwargs)


def test_attente_puis_resultat(qapp):
    panel = AnswerPanel(DisplayConfig())
    assert not panel.has_content() and not panel.isVisible()
    panel.start_capture(1)
    assert panel.isVisible() and panel.lines() == ["…"]
    panel.set_stage(1, "recherche")  # l'étape n'est pas affichée : rester discret
    assert panel.lines() == ["…"]
    panel.set_result(1, result(q("5", "C"), q("4", "ACD")))
    assert panel.lines() == ["4  ACD", "5  C"]


def test_deux_reponses_au_plus_les_plus_anciennes_disparaissent(qapp):
    panel = AnswerPanel(DisplayConfig())
    panel.set_result(1, result(q("4", "ACD"), q("5", "C")))
    panel.set_result(2, result(q("6", "BE")))
    assert panel.lines() == ["5  C", "6  BE"]
    panel.set_result(3, result(q("5", "CE")))  # la question 5 complétée remplace sa ligne
    assert panel.lines() == ["5  CE", "6  BE"]
    # Un écran de trois questions : on ne cache jamais une réponse toute fraîche.
    panel.set_result(4, result(q("7", "A"), q("8", "B"), q("9", "C")))
    assert panel.lines() == ["7  A", "8  B", "9  C"]
    panel.set_result(5, result(q("10", "D")))
    assert panel.lines() == ["9  C", "10  D"]


def test_nombre_de_reponses_et_disposition_en_ligne(qapp):
    panel = AnswerPanel(DisplayConfig(reponses_max=3, disposition="ligne"))
    for i, letters in enumerate("ABCD", start=1):
        panel.set_result(i, result(q(str(i), letters)))
    assert panel.lines() == ["2  B     3  C     4  D"]


def test_seulement_le_numero_et_les_lettres(qapp):
    panel = AnswerPanel(DisplayConfig())
    panel.set_result(
        1,
        result(q("2", "C", cochees="BC", source="connaissances", confiance="basse"), q("3", ""), ticked=True),
    )
    assert panel.lines() == ["2  C", "3  aucune"]
    panel.set_result(2, result(q("5", "", reponse_libre="Nerf vague"), q("6", "", repondue=False)))
    assert panel.lines() == ["5  Nerf vague", "6  ?"]


def test_messages_bascule_et_effacement(qapp):
    panel = AnswerPanel(DisplayConfig())
    panel.start_capture(1)
    panel.set_error(1, "Clé API refusée")
    assert panel.lines() == ["Clé API refusée"]
    panel.set_result(2, result())
    assert panel.lines() == [NO_QUESTION_TEXT]
    panel.start_capture(3)  # un nouvel essai efface le message précédent
    assert panel.lines() == ["…"]
    panel.set_result(3, result(q("1", "A")))
    panel.toggle()
    assert not panel.isVisible() and panel.lines() == ["1  A"]
    panel.toggle()
    assert panel.isVisible()
    panel.hide_for_capture()
    assert not panel.isVisible()
    panel.clear()
    assert panel.lines() == [] and not panel.isVisible()
    panel.set_info("Index des cours mis à jour")
    assert panel.lines() == ["Index des cours mis à jour"] and panel.isVisible()


def test_long_message_coupe_en_lignes(qapp):
    panel = AnswerPanel(DisplayConfig())
    panel.set_info("Claude Code n'est pas connecté à ton compte Claude : " * 6)
    lines = panel.lines()
    assert len(lines) > 1 and panel.width() <= qapp.primaryScreen().availableGeometry().width() * 0.4


def test_petite_croix_ferme_l_affichage(qapp):
    panel = AnswerPanel(DisplayConfig())
    closed = []
    panel.close_requested.connect(lambda: closed.append(True))
    panel.set_result(1, result(q("4", "ACD"), q("5", "C")))
    width, height = panel.width(), panel.height()
    cross = panel._close_rect
    assert cross.right() < width and cross.top() >= 0 and cross.width() < 14  # toute petite
    QTest.mouseClick(panel, Qt.MouseButton.LeftButton, pos=QPoint(10, height // 2))
    assert closed == []  # ailleurs que sur la croix : rien
    QTest.mouseClick(panel, Qt.MouseButton.LeftButton, pos=cross.center())
    assert closed == [True]


def test_dessin_discret(qapp):
    big = AnswerPanel(DisplayConfig(taille_police=20))
    small = AnswerPanel(DisplayConfig())
    for panel in (big, small):
        panel.set_result(1, result(q("4", "ACD"), q("5", "C")))
    assert small.height() < big.height() and small.width() < big.width()
    image = small.grab().toImage()
    opaque = sum(
        1 for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).alpha() > 100
    )
    assert 0 < opaque < image.width() * image.height() * 0.5  # du texte, pas de bulle


def test_couleur_du_texte_selon_la_page(qapp):
    panel = AnswerPanel(DisplayConfig())
    panel.set_result(1, result(q("1", "A")))
    screen = qapp.primaryScreen().geometry()
    page = QImage(160, 100, QImage.Format.Format_Grayscale8)
    page.fill(QColor("white"))
    panel.set_backdrop(page, screen)
    assert panel.dark_text is True
    page.fill(QColor("black"))
    panel.set_backdrop(page, screen)
    assert panel.dark_text is False
    white = QImage(160, 100, QImage.Format.Format_Grayscale8)
    white.fill(QColor("white"))
    assert backdrop_luminance(white, QRect(0, 0, 1000, 500), QRect(900, 400, 100, 100)) == 255
    assert backdrop_luminance(white, QRect(0, 0, 1000, 500), QRect(2000, 2000, 10, 10)) is None


def test_position(qapp):
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
    tray = Tray("lettres", {"corriger": "!", "masquer": "f9", "effacer": "shift+f8"})
    texts = [a.text() for a in tray.menu.actions() if a.text()]
    assert texts[0] == "Corriger l'écran\t!" and texts[-1] == "Quitter"
    assert "Mettre les raccourcis en pause" in texts
    seen = []
    tray.capture_requested.connect(lambda: seen.append("corriger"))
    tray.quit_requested.connect(lambda: seen.append("quitter"))
    tray.mode_changed.connect(seen.append)
    tray.pause_toggled.connect(lambda paused: seen.append(("pause", paused)))
    tray.menu.actions()[0].trigger()
    tray.menu.actions()[-1].trigger()
    tray._mode_actions["coches"].trigger()
    tray._pause.trigger()
    tray.set_paused(False)  # mise à jour par l'application : pas de nouveau signal
    assert seen == ["corriger", "quitter", "coches", ("pause", True)]
    assert not tray._pause.isChecked()
    sizes = []
    tray.size_changed.connect(sizes.append)
    tray._size_actions[8].trigger()
    assert sizes == [8] and tray._size_actions[8].isChecked() and set(tray._size_actions) == {4, 6, 8, 11, 14}
    tray.set_mode("les_deux")
    assert tray._mode_actions["les_deux"].isChecked() and set(MODE_LABELS) == set(tray._mode_actions)
    tray.notify("titre", "texte")  # sans icône visible : sans effet, sans erreur

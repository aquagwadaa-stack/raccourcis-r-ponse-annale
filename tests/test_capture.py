"""Capture : taille envoyée au modèle, correspondance image <-> écran, détection de défilement."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QImage, QPainter

from correcteur import capture as cap


@pytest.mark.parametrize(
    "size, expected_unchanged",
    [((1920, 1080), True), ((2560, 1440), True), ((2880, 1800), False), ((3840, 2160), False), ((5120, 1440), False)],
)
def test_taille_envoyee(size, expected_unchanged):
    width, height = cap.target_size(*size)
    assert ((width, height) == size) is expected_unchanged
    assert max(width, height) <= cap.MAX_LONG_EDGE and width * height <= cap.MAX_PIXELS
    assert abs(width / height - size[0] / size[1]) < 0.01


def page(width=2880, height=1800, shift=0):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    for i in range(30):
        painter.fillRect(200, 80 + i * 110 - shift, 1800 + (i % 5) * 120, 28, QColor("black"))
    painter.end()
    return image


def test_capture_et_correspondance_des_coordonnees(qapp):
    # Écran secondaire 2880x1800 affiché à 200 % : 1440x900 points, placé à droite du principal.
    capture = cap.capture_from_image(page(), QRect(1920, 0, 1440, 900))
    assert capture.image.png.startswith(b"\x89PNG")
    assert (capture.image.width, capture.image.height) == cap.target_size(2880, 1800)
    middle = capture.to_global(capture.image.width // 2, capture.image.height // 2)
    assert abs(middle.x() - (1920 + 720)) <= 1 and abs(middle.y() - 450) <= 1
    assert capture.to_global(0, 0) == QPoint(1920, 0)
    assert capture.to_local(capture.image.width, capture.image.height) == QPoint(1440, 900)
    left, top, right, bottom = capture.fraction_of(QRect(1920 + 1080, 675, 360, 225))
    assert (left, top, right, bottom) == pytest.approx((0.75, 0.75, 1.0, 1.0))


def test_petite_image_non_agrandie(qapp):
    capture = cap.capture_from_image(page(1366, 768), QRect(0, 0, 1366, 768))
    assert (capture.image.width, capture.image.height) == (1366, 768)
    assert capture.to_global(683, 384) == QPoint(683, 384)


def test_ressemblance_et_defilement(qapp):
    base = cap.make_thumbnail(page())
    assert cap.similarity(base, cap.make_thumbnail(page())) == 1.0
    scrolled = cap.similarity(base, cap.make_thumbnail(page(shift=260)))
    assert scrolled < cap.SAME_SCREEN_THRESHOLD

    # Notre panneau, en bas à droite, ne doit pas compter comme un changement de page.
    with_panel = page()
    painter = QPainter(with_panel)
    painter.fillRect(2400, 1500, 440, 260, QColor("#0f172a"))
    painter.end()
    thumb = cap.make_thumbnail(with_panel)
    zone = (2390 / 2880, 1490 / 1800, 1.0, 1.0)
    assert cap.similarity(base, thumb, zone) > 0.995
    assert cap.similarity(base, thumb, zone) > cap.similarity(base, thumb)
    assert cap.similarity(base, QImage()) == 0.0


def test_capture_d_ecran_reelle(qapp):
    capture = cap.grab()
    assert capture.image.width > 0 and capture.image.png.startswith(b"\x89PNG")
    assert cap.same_screen(capture) is True

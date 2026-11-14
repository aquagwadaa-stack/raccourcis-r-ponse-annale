"""Capture de l'écran et passage des coordonnées de l'image à celles de l'écran."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QRect, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QScreen

from .models import ScreenImage

# Limites d'image du modèle : au-delà, il la réduirait lui-même et les positions
# qu'il renvoie ne correspondraient plus à nos pixels.
MAX_LONG_EDGE = 2576
MAX_PIXELS = 3_750_000

THUMB_WIDTH = 160
THUMB_HEIGHT = 100
# En dessous de cette ressemblance, on considère que la page a bougé (défilement,
# changement de fenêtre) depuis la capture.
SAME_SCREEN_THRESHOLD = 0.97


def target_size(width: int, height: int) -> tuple[int, int]:
    """Taille de l'image à envoyer : inchangée si elle respecte les limites, réduite sinon."""
    if width <= 0 or height <= 0:
        return max(width, 1), max(height, 1)
    scale = min(1.0, MAX_LONG_EDGE / max(width, height), math.sqrt(MAX_PIXELS / (width * height)))
    if scale >= 1.0:
        return width, height
    return max(1, int(width * scale)), max(1, int(height * scale))


def encode_png(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def make_thumbnail(image: QImage) -> QImage:
    """Petite image en niveaux de gris, pour comparer deux captures."""
    small = image.scaled(
        THUMB_WIDTH,
        THUMB_HEIGHT,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return small.convertToFormat(QImage.Format.Format_Grayscale8)


def similarity(a: QImage, b: QImage, ignore: tuple[float, float, float, float] | None = None) -> float:
    """Ressemblance de deux miniatures, de 0 (rien à voir) à 1 (identiques).

    ``ignore`` est une zone à ne pas comparer, en fractions de l'écran
    ``(gauche, haut, droite, bas)`` : celle de notre propre panneau.
    """
    if a.size() != b.size() or a.isNull() or b.isNull():
        return 0.0
    width, height = a.width(), a.height()
    skip_cols: range = range(0)
    skip_rows: range = range(0)
    if ignore is not None:
        left, top, right, bottom = ignore
        skip_cols = range(max(0, int(left * width)), min(width, math.ceil(right * width)))
        skip_rows = range(max(0, int(top * height)), min(height, math.ceil(bottom * height)))
    total = 0
    count = 0
    for y in range(height):
        row_a = bytes(a.constScanLine(y)[:width])
        row_b = bytes(b.constScanLine(y)[:width])
        in_rows = y in skip_rows
        for x in range(width):
            if in_rows and x in skip_cols:
                continue
            total += abs(row_a[x] - row_b[x])
            count += 1
    if count == 0:
        return 1.0
    return 1.0 - total / (count * 255.0)


@dataclass
class Capture:
    """Une capture d'écran et de quoi replacer ses pixels sur l'écran."""

    image: ScreenImage
    geometry: QRect  # écran capturé, en coordonnées logiques globales de Qt
    thumbnail: QImage
    screen: QScreen | None = None

    def to_global(self, x: int, y: int) -> QPoint:
        """Pixel ``(x, y)`` de l'image envoyée au modèle -> point global de l'écran."""
        gx = self.geometry.x() + x * self.geometry.width() / max(1, self.image.width)
        gy = self.geometry.y() + y * self.geometry.height() / max(1, self.image.height)
        return QPoint(round(gx), round(gy))

    def to_local(self, x: int, y: int) -> QPoint:
        """Même point, relatif au coin haut-gauche de l'écran capturé."""
        return self.to_global(x, y) - self.geometry.topLeft()

    def fraction_of(self, rect: QRect) -> tuple[float, float, float, float]:
        """Rectangle global -> fractions ``(gauche, haut, droite, bas)`` de cet écran."""
        g = self.geometry
        return (
            (rect.left() - g.left()) / g.width(),
            (rect.top() - g.top()) / g.height(),
            (rect.right() + 1 - g.left()) / g.width(),
            (rect.bottom() + 1 - g.top()) / g.height(),
        )


def screen_under_cursor() -> QScreen:
    screen = QGuiApplication.screenAt(QCursor.pos())
    return screen or QGuiApplication.primaryScreen()


def grab_image(screen: QScreen) -> QImage:
    """Contenu de l'écran, en pixels réels (indépendamment de la mise à l'échelle de Windows)."""
    return screen.grabWindow(0).toImage()


def capture_from_image(image: QImage, geometry: QRect, screen: QScreen | None = None) -> Capture:
    width, height = target_size(image.width(), image.height())
    sent = image
    if (width, height) != (image.width(), image.height()):
        sent = image.scaled(
            width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
    return Capture(
        image=ScreenImage(png=encode_png(sent), width=sent.width(), height=sent.height()),
        geometry=QRect(geometry),
        thumbnail=make_thumbnail(image),
        screen=screen,
    )


def grab(screen: QScreen | None = None) -> Capture:
    """Capture l'écran indiqué, ou celui où se trouve la souris."""
    screen = screen or screen_under_cursor()
    image = grab_image(screen)
    if image.isNull():
        raise RuntimeError("La capture d'écran a échoué.")
    return capture_from_image(image, screen.geometry(), screen)


def same_screen(capture: Capture, ignore_rect: QRect | None = None) -> bool:
    """Vrai si l'écran ressemble encore à la capture (la page n'a pas défilé)."""
    if capture.screen is None:
        return True
    now = make_thumbnail(grab_image(capture.screen))
    ignore = capture.fraction_of(ignore_rect) if ignore_rect is not None else None
    return similarity(capture.thumbnail, now, ignore) >= SAME_SCREEN_THRESHOLD

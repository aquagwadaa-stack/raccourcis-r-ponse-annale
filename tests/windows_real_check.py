"""Vérification sur un vrai bureau Windows : transparence des fenêtres et raccourci global.

À lancer seul (``python tests/windows_real_check.py``) : il utilise l'affichage
réel de Windows, pas l'affichage factice des autres tests. Il imprime un
résultat JSON sur sa dernière ligne.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from correcteur import capture as cap  # noqa: E402
from correcteur.config import DisplayConfig  # noqa: E402
from correcteur.hotkeys import HotkeyManager  # noqa: E402
from correcteur.models import QuestionResult, ScreenResult  # noqa: E402
from correcteur.ui.marks import MarksOverlay  # noqa: E402
from correcteur.ui.panel import AnswerPanel  # noqa: E402


def pump(app: QApplication, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    app = QApplication(sys.argv[:1])
    screen = app.primaryScreen()
    ratio = screen.devicePixelRatio()
    out: dict = {"platform": app.platformName(), "ratio": ratio}

    # Une « page » blanche plein écran : on sait ce qu'il y a sous nos fenêtres.
    page = QWidget()
    page.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    page.setStyleSheet("background: white;")
    page.setGeometry(screen.geometry())
    page.show()
    pump(app, 1.0)

    capture = cap.grab()
    width, height = capture.image.width, capture.image.height
    out["capture"] = [width, height]
    mark_at = (width // 3, height // 3)
    result = ScreenResult([QuestionResult("1", "A", source="cours", positions={"A": mark_at})], width, height)

    marks = MarksOverlay()
    out["marks_drawn"] = marks.show_marks(capture, result, False, 0)
    panel = AnswerPanel(DisplayConfig(opacite=1.0))
    panel.place_on(screen)
    panel.set_result(1, result)
    pump(app, 1.2)
    out["page_still_active"] = not (marks.isActiveWindow() or panel.isActiveWindow())

    image = screen.grabWindow(0).toImage()

    def pixel(logical_x: float, logical_y: float) -> QColor:
        return image.pixelColor(int(logical_x * ratio), int(logical_y * ratio))

    # 1. La coche est-elle réellement dessinée par-dessus la page ?
    center = capture.to_local(*mark_at)
    green = 0
    for dx in range(-16, 17, 2):
        for dy in range(-16, 17, 2):
            color = pixel(center.x() + dx, center.y() + dy)
            if color.green() > color.red() + 40 and color.green() > color.blue() + 40:
                green += 1
    out["green_pixels_at_mark"] = green
    # 2. Ailleurs, la fenêtre des coches laisse-t-elle voir la page (blanche) ?
    far = pixel(screen.geometry().width() * 0.6, screen.geometry().height() * 0.15)
    out["far_pixel"] = [far.red(), far.green(), far.blue()]
    # 3. Le panneau est-il visible (fond sombre) ?
    inside = panel.frameGeometry().center() - screen.geometry().topLeft()
    dark = pixel(inside.x() - panel.width() * 0.4, inside.y())
    out["panel_pixel"] = [dark.red(), dark.green(), dark.blue()]
    out["same_screen_with_panel"] = cap.same_screen(capture, panel.frameGeometry())
    marks.clear()
    panel.hide()

    # 4. Le raccourci global : déclaration puis frappe simulée.
    received: list[str] = []
    manager = HotkeyManager()
    manager.triggered.connect(received.append)
    out["hotkey_errors"] = manager.start({"corriger": "ctrl+alt+shift+f11", "masquer": "f8"})
    if sys.platform == "win32":
        import ctypes

        user32 = ctypes.windll.user32
        for keys in ([0x11, 0x12, 0x10, 0x7A], [0x77]):  # ctrl+alt+maj+F11, puis F8 seul
            for vk in keys:
                user32.keybd_event(vk, 0, 0, 0)
            for vk in reversed(keys):
                user32.keybd_event(vk, 0, 2, 0)
            pump(app, 1.0)
    manager.stop()
    out["hotkeys_received"] = received
    page.close()
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

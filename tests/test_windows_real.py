"""Sous Windows uniquement : nos fenêtres sur un vrai bureau (transparence, raccourci)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="nécessite un bureau Windows")


def test_fenetres_et_raccourci_sur_un_vrai_bureau():
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    script = Path(__file__).with_name("windows_real_check.py")
    done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    print(out)
    assert out["platform"] == "windows"
    assert out["marks_drawn"] == 1
    assert out["green_pixels_at_mark"] >= 5, "la coche n'apparaît pas à l'écran"
    assert min(out["far_pixel"]) > 200, "la fenêtre des coches masque la page : transparence inactive"
    assert max(out["panel_pixel"]) < 120, "le panneau n'est pas visible"
    assert out["page_still_active"], "une de nos fenêtres a pris le clavier"
    assert out["same_screen_with_panel"] is True
    assert out["hotkey_errors"] == {}
    if not out["hotkeys_received"]:
        pytest.skip("frappe simulée non transmise (session sans bureau interactif)")
    assert out["hotkeys_received"] == ["corriger", "masquer"]

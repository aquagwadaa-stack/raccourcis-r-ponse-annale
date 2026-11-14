"""Raccourcis : analyse des textes, codes Windows, gestionnaire."""

from __future__ import annotations

import sys
import time

import pytest

from correcteur.hotkeys import (
    Hotkey,
    HotkeyError,
    HotkeyManager,
    modifier_flags,
    parse_hotkey,
    virtual_key,
)


@pytest.mark.parametrize(
    "text, modifiers, key",
    [
        ("f8", set(), "f8"),
        ("F8", set(), "f8"),
        ("shift+f8", {"shift"}, "f8"),
        ("Maj + F8", {"shift"}, "f8"),
        ("ctrl+alt+c", {"ctrl", "alt"}, "c"),
        ("Ctrl+Shift+Espace", {"ctrl", "shift"}, "space"),
        ("pause", set(), "pause"),
        ("win+f12", {"win"}, "f12"),
        ("alt+entrée", {"alt"}, "enter"),
    ],
)
def test_analyse(text, modifiers, key):
    hotkey = parse_hotkey(text)
    assert hotkey == Hotkey(frozenset(modifiers), key)


@pytest.mark.parametrize("text", ["", "ctrl+", "f25", "bidule+f8", "ctrl+??", "c", "7", "ctrl++c"])
def test_analyse_invalide(text):
    with pytest.raises(HotkeyError) as info:
        parse_hotkey(text)
    assert "Raccourci" in str(info.value)


def test_codes_windows():
    assert virtual_key(parse_hotkey("f8")) == 0x77
    assert virtual_key(parse_hotkey("f1")) == 0x70
    assert virtual_key(parse_hotkey("ctrl+alt+c")) == ord("C")
    assert virtual_key(parse_hotkey("ctrl+7")) == ord("7")
    assert virtual_key(parse_hotkey("ctrl+espace")) == 0x20
    assert modifier_flags(parse_hotkey("ctrl+alt+c")) == 0x0003
    assert modifier_flags(parse_hotkey("shift+f8")) == 0x0004
    assert str(parse_hotkey("alt+ctrl+f8")) == "ctrl+alt+f8"


def test_gestionnaire_signale_les_erreurs_sans_planter(qapp):
    manager = HotkeyManager()
    errors = manager.start({"corriger": "f8", "masquer": "pas+valide", "effacer": "F8"})
    assert "masquer" in errors and "effacer" in errors  # syntaxe invalide, doublon
    manager.stop()
    manager.stop()  # un second arrêt est sans effet
    assert manager.start({}) == {}


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey n'existe que sous Windows")
def test_windows_declaration_reelle(qapp):
    import ctypes

    combo = "ctrl+alt+shift+f11"
    received = []
    first = HotkeyManager()
    first.triggered.connect(received.append)
    assert first.start({"corriger": combo}) == {}
    try:
        # La même combinaison ne peut pas être déclarée deux fois : c'est ainsi
        # qu'on détecte un raccourci pris par un autre programme.
        second = HotkeyManager()
        errors = second.start({"corriger": combo})
        second.stop()
        assert "déjà pris" in errors.get("corriger", "")

        user32 = ctypes.windll.user32
        keys = [0x11, 0x12, 0x10, 0x7A]  # ctrl, alt, shift, f11
        for vk in keys:
            user32.keybd_event(vk, 0, 0, 0)
        for vk in reversed(keys):
            user32.keybd_event(vk, 0, 2, 0)
        deadline = time.time() + 3
        while not received and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        if not received:
            pytest.skip("frappe simulée non transmise (session sans bureau interactif)")
        assert received == ["corriger"]
    finally:
        first.stop()

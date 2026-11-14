"""Raccourcis clavier globaux (actifs quelle que soit la fenêtre au premier plan).

Sous Windows on passe par ``RegisterHotKey`` : le système ne nous signale que
les combinaisons déclarées, et il les intercepte, si bien que F8 n'arrive pas en
plus dans le navigateur. Ailleurs, on se rabat sur ``pynput`` s'il est installé.
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


class HotkeyError(ValueError):
    """Raccourci invalide ; le message est en français."""


@dataclass(frozen=True)
class Hotkey:
    modifiers: frozenset[str]  # parmi ctrl, alt, shift, win
    key: str  # nom normalisé : "f8", "c", "space"...

    def __str__(self) -> str:
        order = [m for m in ("ctrl", "alt", "shift", "win") if m in self.modifiers]
        return "+".join([*order, self.key])


_MODIFIER_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "controle": "ctrl",
    "alt": "alt",
    "shift": "shift", "maj": "shift", "majuscule": "shift",
    "win": "win", "windows": "win", "super": "win", "cmd": "win",
}
_KEY_ALIASES = {
    "espace": "space", "space": "space",
    "entree": "enter", "entrée": "enter", "enter": "enter", "return": "enter",
    "echap": "esc", "échap": "esc", "esc": "esc", "escape": "esc",
    "tab": "tab", "tabulation": "tab",
    "pause": "pause",
    "inser": "insert", "insert": "insert", "suppr": "delete", "delete": "delete",
    "debut": "home", "début": "home", "home": "home", "fin": "end", "end": "end",
    "pageprec": "pageup", "pageup": "pageup", "pagesuiv": "pagedown", "pagedown": "pagedown",
    "haut": "up", "up": "up", "bas": "down", "down": "down",
    "gauche": "left", "left": "left", "droite": "right", "right": "right",
    "impecr": "printscreen", "printscreen": "printscreen",
}
_VK_NAMED = {
    "space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "pause": 0x13,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "printscreen": 0x2C,
}
_MOD_FLAGS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


def parse_hotkey(text: str) -> Hotkey:
    """``"ctrl+alt+c"``, ``"F8"``, ``"maj+f8"``... -> ``Hotkey``."""
    parts = [p.strip().lower() for p in (text or "").split("+")]
    if not parts or any(not p for p in parts):
        raise HotkeyError(f"Raccourci invalide : « {text} ». Exemples : f8, ctrl+alt+c, maj+f8.")
    modifiers = set()
    for part in parts[:-1]:
        if part not in _MODIFIER_ALIASES:
            raise HotkeyError(f"Raccourci « {text} » : « {part} » n'est pas ctrl, alt, maj ou win.")
        modifiers.add(_MODIFIER_ALIASES[part])
    key = _KEY_ALIASES.get(parts[-1], parts[-1])
    is_function = key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24
    is_char = len(key) == 1 and key.isascii() and key.isalnum()
    if not (is_function or is_char or key in _VK_NAMED):
        raise HotkeyError(f"Raccourci « {text} » : touche « {parts[-1]} » inconnue. Exemples : f8, ctrl+alt+c.")
    if is_char and not modifiers:
        raise HotkeyError(f"Raccourci « {text} » : une lettre ou un chiffre seul gênerait la frappe, ajoute ctrl ou alt.")
    return Hotkey(frozenset(modifiers), key)


def virtual_key(hotkey: Hotkey) -> int:
    """Code de touche Windows."""
    key = hotkey.key
    if key in _VK_NAMED:
        return _VK_NAMED[key]
    if key.startswith("f") and key[1:].isdigit():
        return 0x70 + int(key[1:]) - 1
    return ord(key.upper())


def modifier_flags(hotkey: Hotkey) -> int:
    flags = 0
    for modifier in hotkey.modifiers:
        flags |= _MOD_FLAGS[modifier]
    return flags


def _pynput_spec(hotkey: Hotkey) -> str:
    names = {"ctrl": "<ctrl>", "alt": "<alt>", "shift": "<shift>", "win": "<cmd>"}
    special = {"pageup": "page_up", "pagedown": "page_down", "printscreen": "print_screen"}
    key = hotkey.key if len(hotkey.key) == 1 else f"<{special.get(hotkey.key, hotkey.key)}>"
    return "+".join([*(names[m] for m in ("ctrl", "alt", "shift", "win") if m in hotkey.modifiers), key])


class HotkeyManager(QObject):
    """Déclare des raccourcis nommés et émet ``triggered(nom)`` quand l'un d'eux est pressé."""

    triggered = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._listener = None

    def start(self, bindings: dict[str, str]) -> dict[str, str]:
        """Active les raccourcis ``{nom: "f8"}``.

        Renvoie ``{nom: message}`` pour ceux qui n'ont pas pu être activés
        (syntaxe invalide, combinaison déjà prise par un autre programme).
        """
        self.stop()
        errors: dict[str, str] = {}
        parsed: dict[str, Hotkey] = {}
        for name, text in bindings.items():
            try:
                hotkey = parse_hotkey(text)
            except HotkeyError as exc:
                errors[name] = str(exc)
                continue
            if hotkey in parsed.values():
                errors[name] = f"Raccourci « {text} » déjà utilisé pour une autre action."
                continue
            parsed[name] = hotkey
        if not parsed:
            return errors
        if sys.platform == "win32":
            errors.update(self._start_windows(parsed))
        else:
            errors.update(self._start_pynput(parsed))
        return errors

    def stop(self) -> None:
        if self._thread is not None and self._thread_id is not None:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=3)
        self._thread = None
        self._thread_id = None
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                log.exception("Arrêt de l'écoute clavier impossible")
            self._listener = None

    # -- Windows -----------------------------------------------------------
    def _start_windows(self, hotkeys: dict[str, Hotkey]) -> dict[str, str]:
        errors: dict[str, str] = {}
        ready = threading.Event()

        def loop() -> None:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            # RegisterHotKey lie le raccourci au fil qui l'appelle : déclaration
            # et boucle de messages doivent se faire dans le même fil.
            self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            names: dict[int, str] = {}
            for ident, (name, hotkey) in enumerate(hotkeys.items(), start=1):
                if user32.RegisterHotKey(None, ident, modifier_flags(hotkey) | MOD_NOREPEAT, virtual_key(hotkey)):
                    names[ident] = name
                else:
                    errors[name] = (
                        f"Le raccourci « {hotkey} » est déjà pris par un autre programme : "
                        "choisis-en un autre dans config.toml."
                    )
            ready.set()
            message = wintypes.MSG()
            try:
                while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                    if message.message == WM_HOTKEY and message.wParam in names:
                        self.triggered.emit(names[message.wParam])
            finally:
                for ident in names:
                    user32.UnregisterHotKey(None, ident)

        self._thread = threading.Thread(target=loop, name="correcteur-raccourcis", daemon=True)
        self._thread.start()
        ready.wait(timeout=5)
        return errors

    # -- autres systèmes ---------------------------------------------------
    def _start_pynput(self, hotkeys: dict[str, Hotkey]) -> dict[str, str]:
        try:
            from pynput import keyboard
        except Exception:  # paquet absent, ou pas d'affichage disponible
            log.warning("Raccourcis globaux indisponibles sur ce système (pynput absent).")
            return {name: "Raccourcis globaux indisponibles sur ce système." for name in hotkeys}
        try:
            mapping = {
                _pynput_spec(hotkey): (lambda n=name: self.triggered.emit(n)) for name, hotkey in hotkeys.items()
            }
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.start()
        except Exception as exc:
            log.exception("Écoute clavier impossible")
            return {name: f"Raccourcis globaux indisponibles ({exc})." for name in hotkeys}
        return {}

"""Execute plans on macOS: AX press first, synthetic mouse/keyboard as fallback."""
from __future__ import annotations

import subprocess
import time

import Quartz  # type: ignore
from ApplicationServices import AXUIElementPerformAction, AXUIElementSetAttributeValue  # type: ignore

KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
    "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
    "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31,
    "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, ",": 43, "n": 45, "m": 46,
    ".": 47, "enter": 36, "return": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
}
MODS = {
    "cmd": Quartz.kCGEventFlagMaskCommand, "shift": Quartz.kCGEventFlagMaskShift,
    "alt": Quartz.kCGEventFlagMaskAlternate, "opt": Quartz.kCGEventFlagMaskAlternate,
    "ctrl": Quartz.kCGEventFlagMaskControl,
}


def _post(ev):
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def press_key(combo: str):
    parts = combo.lower().split("+")
    key, flags = parts[-1], 0
    for m in parts[:-1]:
        flags |= MODS[m]
    code = KEYCODES[key]
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(ev, flags)
        _post(ev)


def type_text(text: str, chunk: int = 16):
    """Unicode typing (works for Chinese) by posting text chunks."""
    for i in range(0, len(text), chunk):
        s = text[i:i + chunk]
        n = len(s.encode("utf-16-le")) // 2
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(ev, n, s)
            _post(ev)
        time.sleep(0.005)


def click_at(x: float, y: float):
    for t in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        _post(Quartz.CGEventCreateMouseEvent(None, t, (x, y), Quartz.kCGMouseButtonLeft))


def click_element(ax_el, frame) -> str:
    if AXUIElementPerformAction(ax_el, "AXPress") == 0:
        return "ax-press"
    x, y, w, h = frame
    click_at(x + w / 2, y + h / 2)
    return "mouse"


def focus_element(ax_el, frame):
    if AXUIElementSetAttributeValue(ax_el, "AXFocused", True) != 0:
        x, y, w, h = frame
        click_at(x + w / 2, y + h / 2)


def scroll(direction: str, lines: int = 8):
    amt = -lines if direction == "down" else lines
    _post(Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, amt))


def open_app(name: str):
    subprocess.Popen(["open", "-a", name])


def set_volume(v: int):
    subprocess.Popen(["osascript", "-e", f"set volume output volume {int(v)}"])

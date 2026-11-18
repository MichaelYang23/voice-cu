"""macOS Accessibility (AX) snapshot of the target window -> compact candidate list.

Requires: Accessibility permission for the terminal running this script
(System Settings > Privacy & Security > Accessibility).
"""
from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass

from AppKit import NSWorkspace, NSRunningApplication  # type: ignore
import Quartz  # type: ignore
from ApplicationServices import (  # type: ignore
    AXIsProcessTrusted,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementSetAttributeValue,
    AXUIElementSetMessagingTimeout,
    AXValueGetValue,
    kAXValueCGPointType,
    kAXValueCGSizeType,
)

INTERACTIVE = {
    "AXButton": "button", "AXCheckBox": "checkbox", "AXRadioButton": "radio/tab",
    "AXPopUpButton": "popup", "AXMenuButton": "menu-button", "AXComboBox": "combobox",
    "AXTextField": "text-field", "AXTextArea": "text-area", "AXLink": "link",
    "AXSlider": "slider", "AXRow": "row", "AXCell": "cell", "AXDisclosureTriangle": "disclosure",
    "AXTab": "tab", "AXMenuItem": "menu-item", "AXIncrementor": "stepper",
}
TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox"}

DEFAULT_IGNORE = {"Terminal", "iTerm2", "Ghostty", "Warp", "WezTerm", "Alacritty", "kitty",
                  "Code", "Visual Studio Code", "Cursor", "Python"}


def check_permission() -> bool:
    return bool(AXIsProcessTrusted())


def _attr(el, name):
    err, val = AXUIElementCopyAttributeValue(el, name, None)
    return val if err == 0 else None


def _frame(el):
    pos, size = _attr(el, "AXPosition"), _attr(el, "AXSize")
    if pos is None or size is None:
        return None
    ok1, p = AXValueGetValue(pos, kAXValueCGPointType, None)
    ok2, s = AXValueGetValue(size, kAXValueCGSizeType, None)
    if not (ok1 and ok2):
        return None
    return (p.x, p.y, s.width, s.height)


def _label(el, role: str) -> str:
    for a in ("AXTitle", "AXDescription", "AXPlaceholderValue", "AXHelp"):
        v = _attr(el, a)
        if v and isinstance(v, str) and v.strip():
            return v.strip()
    v = _attr(el, "AXValue")
    if isinstance(v, str) and v.strip() and role not in TEXT_ROLES:
        return v.strip()
    if role in ("AXRow", "AXCell"):  # take text from a few descendants
        texts, q = [], deque([(el, 0)])
        while q and len(texts) < 3:
            node, d = q.popleft()
            for ch in (_attr(node, "AXChildren") or []):
                if _attr(ch, "AXRole") == "AXStaticText":
                    t = _attr(ch, "AXValue")
                    if isinstance(t, str) and t.strip():
                        texts.append(t.strip())
                if d < 3:
                    q.append((ch, d + 1))
        return " · ".join(texts)
    return ""


@dataclass
class Snapshot:
    app: str
    pid: int
    window: str
    elements: list[dict]      # {id, role, label, desc, frame}
    refs: dict                # id -> AXUIElement (not serialisable)
    state: str                # text handed to Kev
    ms: float


def target_app(ignore: set[str] | None = None):
    """Front-most on-screen app that isn't the terminal/editor running us."""
    ignore = ignore or DEFAULT_IGNORE
    me = os.getpid()
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID)
    for w in wins or []:
        if w.get("kCGWindowLayer", 1) != 0:
            continue
        pid = int(w.get("kCGWindowOwnerPID", 0))
        name = w.get("kCGWindowOwnerName", "")
        if pid == me or name in ignore:
            continue
        return name, pid
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    return front.localizedName(), front.processIdentifier()


def activate(pid: int):
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is not None:
        app.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps


def snapshot(max_elements: int = 120, max_depth: int = 30, budget_ms: float = 400,
             ignore: set[str] | None = None) -> Snapshot:
    t0 = time.perf_counter()
    name, pid = target_app(ignore)
    app_el = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(app_el, 0.3)
    AXUIElementSetAttributeValue(app_el, "AXManualAccessibility", True)  # Electron apps

    win = _attr(app_el, "AXFocusedWindow") or ((_attr(app_el, "AXWindows") or [None])[0])
    title = (_attr(win, "AXTitle") or "") if win is not None else ""

    elements, refs = [], {}
    if win is not None:
        q = deque([(win, 0)])
        while q and len(elements) < max_elements:
            if (time.perf_counter() - t0) * 1000 > budget_ms:
                break
            node, depth = q.popleft()
            role = _attr(node, "AXRole") or ""
            if role in INTERACTIVE and _attr(node, "AXEnabled") is not False:
                fr = _frame(node)
                if fr and fr[2] > 2 and fr[3] > 2:
                    label = _label(node, role)
                    if label or role in TEXT_ROLES:
                        eid = f"e{len(elements)}"
                        if role in TEXT_ROLES:
                            val = _attr(node, "AXValue")
                            label = label or "(empty)"
                            if isinstance(val, str) and val:
                                label += f" [contains: {val[:30]}]"
                        desc = f"{INTERACTIVE[role]}: {label[:80]}"
                        elements.append({"id": eid, "role": role, "label": label[:80],
                                         "desc": desc, "frame": fr})
                        refs[eid] = node
            if depth < max_depth and role not in ("AXRow", "AXCell") or depth < 2:
                for ch in (_attr(node, "AXChildren") or []):
                    q.append((ch, depth + 1))

    lines = [f"App: {name}", f"Window: {title}", "On-screen elements (id | kind: label):"]
    lines += [f"{e['id']} | {e['desc']}" for e in elements]
    return Snapshot(name, pid, title, elements, refs, "\n".join(lines),
                    (time.perf_counter() - t0) * 1000)


def installed_apps() -> list[str]:
    roots = ["/Applications", "/System/Applications", "/System/Applications/Utilities",
             "/Applications/Utilities", os.path.expanduser("~/Applications")]
    names = set()
    for r in roots:
        try:
            for f in os.listdir(r):
                if f.endswith(".app"):
                    names.add(f[:-4])
        except OSError:
            pass
    names.add("Finder")
    return sorted(names)

"""Kev / Jev decision client.

Talks the System One contract (POST {base}/v1/systemone) that both Kev
(self-hosted) and TypeSafe's Jev (hosted) expose, so the backend is swappable.

Design choice: the *screen* is the `state` and the *voice command* goes into the
question text. While the screen doesn't change, Kev reuses the cached state and
only pays for the questions (Kev README: Kev-4B on M5 ~721 ms new vs ~136 ms repeat).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

from .router import Plan

ACTIONS = {
    "click": "Click or press an on-screen element (button, link, list row, tab, checkbox, menu)",
    "type": "Type or dictate text into a text field",
    "open_app": "Open, launch or switch to an application",
    "key": "Press a key or keyboard shortcut",
    "scroll": "Scroll the view up or down",
    "none": "Not an actionable computer command, or impossible on this screen",
}

KEYS = {
    "enter": "Return / confirm / submit", "escape": "Escape / cancel / dismiss", "tab": "Next field",
    "cmd+n": "New document/note/window", "cmd+t": "New tab", "cmd+w": "Close tab or window",
    "cmd+s": "Save", "cmd+z": "Undo", "cmd+shift+z": "Redo", "cmd+c": "Copy", "cmd+v": "Paste",
    "cmd+a": "Select all", "cmd+f": "Find / search", "cmd+q": "Quit app", "cmd+r": "Reload / refresh",
    "cmd+[": "Go back", "cmd+]": "Go forward", "cmd+,": "Open preferences",
    "space": "Space / play-pause", "delete": "Delete / backspace",
    "up": "Arrow up", "down": "Arrow down", "left": "Arrow left", "right": "Arrow right",
}


@dataclass
class Decision:
    plan: Plan
    answers: dict
    latency_ms: float
    server_latency_ms: float | None
    request: dict


class KevClient:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, timeout: float = 10.0):
        self.base_url = (base_url or os.environ.get("KEV_BASE_URL", "http://127.0.0.1:8009")).rstrip("/")
        self.model = model or os.environ.get("KEV_MODEL", "kev-latest")
        self.api_key = api_key or os.environ.get("KEV_API_KEY")
        self.timeout = timeout
        self.session = requests.Session()

    def system_one(self, state: str, questions: dict) -> tuple[dict, float]:
        body = {"model": self.model, "state": state, "questions": questions}
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        t0 = time.perf_counter()
        r = self.session.post(f"{self.base_url}/v1/systemone", json=body,
                              headers=headers, timeout=self.timeout)
        dt = (time.perf_counter() - t0) * 1000
        r.raise_for_status()
        return r.json(), dt

    def warmup(self) -> float:
        _, dt = self.system_one("warmup", {"ok": {"type": "noul", "instructions": "Is this a test?"}})
        return dt


def build_questions(command: str, elements: list[dict], apps: list[str]) -> dict:
    cmd = command.replace('"', "'")
    qs: dict = {
        "action": {
            "type": "choice",
            "instructions": f'The user said: "{cmd}". What kind of computer action does this ask for?',
            "criteria": ACTIONS,
        },
        "key": {
            "type": "choice",
            "instructions": f'The user said: "{cmd}". If this is a key press or shortcut, which one?',
            "criteria": KEYS,
        },
        "scroll": {
            "type": "choice",
            "instructions": f'The user said: "{cmd}". If this asks to scroll, which direction?',
            "criteria": {"down": "Scroll down / next", "up": "Scroll up / previous"},
        },
    }
    if elements:
        crit = {e["id"]: e["desc"] for e in elements[:254]}
        crit["none"] = "None of the listed elements"
        qs["target"] = {
            "type": "choice",
            "instructions": (f'The user said: "{cmd}". Which on-screen element (from the state) '
                             "should be clicked or typed into?"),
            "criteria": crit,
        }
    if apps:
        crit = {a: None for a in apps[:254]}
        crit["none"] = "Not about opening an app"
        qs["app"] = {
            "type": "choice",
            "instructions": f'The user said: "{cmd}". Which application should be opened?',
            "criteria": crit,
        }
    return qs


def _top(ans: dict | None) -> tuple[str | None, float]:
    if not ans:
        return None, 0.0
    choice = ans.get("choice")
    conf = ans.get("confidence")
    if conf is None and ans.get("probabilities") and choice in ans["probabilities"]:
        conf = ans["probabilities"][choice]
    return choice, float(conf or 0.0)


def topk(ans: dict | None, k: int = 3) -> list[tuple[str, float]]:
    if not ans or not ans.get("probabilities"):
        return []
    return sorted(ans["probabilities"].items(), key=lambda kv: -kv[1])[:k]


def decide(client: KevClient, command: str, state: str, elements: list[dict],
           apps: list[str]) -> Decision:
    questions = build_questions(command, elements, apps)
    t0 = time.perf_counter()
    resp, _ = client.system_one(state, questions)
    latency = (time.perf_counter() - t0) * 1000
    answers = resp.get("answers", {})

    action, a_conf = _top(answers.get("action"))
    plan = Plan("none", source="model", confidence=a_conf)
    if action == "click":
        tid, t_conf = _top(answers.get("target"))
        if tid and tid != "none":
            plan = Plan("click", tid, "model", a_conf * t_conf)
    elif action == "type":
        tid, t_conf = _top(answers.get("target"))
        plan = Plan("type", None, "model", a_conf, extra={"target": None if tid == "none" else tid,
                                                          "target_conf": t_conf})
    elif action == "open_app":
        app, p_conf = _top(answers.get("app"))
        if app and app != "none":
            plan = Plan("open_app", app, "model", a_conf * p_conf)
    elif action == "key":
        key, k_conf = _top(answers.get("key"))
        plan = Plan("key", key, "model", a_conf * k_conf)
    elif action == "scroll":
        d, d_conf = _top(answers.get("scroll"))
        plan = Plan("scroll", d or "down", "model", a_conf * d_conf)

    return Decision(plan, answers, latency, resp.get("latency_ms"),
                    {"state": state, "questions": questions})

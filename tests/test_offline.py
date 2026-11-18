"""Offline tests (no Mac, no model): router rules + Kev client against a mock server.

    python -m pytest tests -q
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from voice_cu.decider import KevClient, build_questions, decide
from voice_cu.router import route

APPS = ["Notes", "Safari", "Google Chrome", "System Settings", "Calculator", "WeChat", "Finder"]


@pytest.mark.parametrize("cmd,action,arg", [
    ("打开备忘录", "open_app", "Notes"),
    ("open chrome.", "open_app", "Google Chrome"),
    ("Open Calculater", "open_app", "Calculator"),       # fuzzy
    ("切换到微信", "open_app", "WeChat"),
    ("音量调到30", "volume", 30),
    ("set volume to 150", "volume", 100),
    ("保存", "key", "cmd+s"),
    ("Undo.", "key", "cmd+z"),
    ("往下翻", "scroll", "down"),
    ("输入：明天下午三点开会", "type", "明天下午三点开会"),
    ('type "hello world"', "type", "hello world"),
])
def test_router_hits(cmd, action, arg):
    p = route(cmd, APPS)
    assert p is not None and p.action == action and p.arg == arg


@pytest.mark.parametrize("cmd", ["点一下发送按钮", "click the blue share button", "open the second email"])
def test_router_falls_through(cmd):
    assert route(cmd, APPS) is None


class Mock(BaseHTTPRequestHandler):
    last = None

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Mock.last = body
        tgt = body["questions"]["target"]["criteria"]
        out = {"model": "kev-latest", "latency_ms": 7, "answers": {
            "action": {"type": "choice", "choice": "click", "confidence": 0.9,
                       "probabilities": {"click": 0.9, "type": 0.05, "none": 0.05}},
            "target": {"type": "choice", "choice": "e1", "confidence": 0.8,
                       "probabilities": {k: (0.8 if k == "e1" else 0.2 / (len(tgt) - 1)) for k in tgt}},
            "key": {"type": "choice", "choice": "enter", "confidence": 0.3},
            "scroll": {"type": "choice", "choice": "down", "confidence": 0.5},
            "app": {"type": "choice", "choice": "none", "confidence": 0.9},
        }}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    s = HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


def test_decide_click(server):
    els = [{"id": "e0", "desc": "button: Cancel"}, {"id": "e1", "desc": "button: Send"}]
    state = "App: Mail\nWindow: New\ne0 | button: Cancel\ne1 | button: Send"
    d = decide(KevClient(server), "点发送", state, els, APPS)
    assert d.plan.action == "click" and d.plan.arg == "e1"
    assert abs(d.plan.confidence - 0.72) < 1e-6
    # command lives in the questions, not the state -> state cache reusable across commands
    assert "点发送" not in Mock.last["state"]
    assert "点发送" in Mock.last["questions"]["action"]["instructions"]
    assert Mock.last["model"] == "kev-latest"


def test_option_limits():
    els = [{"id": f"e{i}", "desc": f"button: b{i}"} for i in range(400)]
    q = build_questions("x", els, [f"App{i}" for i in range(400)])
    assert len(q["target"]["criteria"]) <= 255 and len(q["app"]["criteria"]) <= 255

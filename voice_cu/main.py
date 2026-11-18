"""Voice -> (router | Kev) -> macOS action, with per-stage timing and JSONL logging.

Run:  python -m voice_cu.main            # push-to-talk (hold Right Option)
      python -m voice_cu.main --text     # type commands in the terminal instead
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

from .decider import KevClient, decide, topk
from .router import Plan, route


def log_jsonl(path: str, rec: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def compose_text(command: str) -> str | None:
    """Optional: let an LLM write the text when the command isn't a literal dictation."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model=os.environ.get("COMPOSE_MODEL", "claude-haiku-5-5"), max_tokens=400,
        messages=[{"role": "user", "content":
                   "Output ONLY the text to type into the focused field, nothing else. "
                   f"User's voice command: {command}"}])
    return msg.content[0].text.strip()


def fmt_ms(d: dict) -> str:
    return "  ".join(f"{k}={v:.0f}ms" for k, v in d.items() if v is not None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true", help="type commands instead of speaking")
    ap.add_argument("--confirm", action="store_true",
                    help="ask before executing model decisions; answers become training labels")
    ap.add_argument("--threshold", type=float, default=0.35,
                    help="min model confidence to act without confirmation")
    ap.add_argument("--hotkey", default="alt_r", help="pynput key name for push-to-talk")
    ap.add_argument("--whisper", default="mlx-community/whisper-large-v3-turbo")
    ap.add_argument("--lang", default=None, help="force Whisper language, e.g. zh or en")
    ap.add_argument("--log", default=f"logs/decisions-{datetime.now():%Y%m%d}.jsonl")
    ap.add_argument("--max-elements", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true", help="decide but don't act")
    args = ap.parse_args()

    from . import actions, screen  # macOS-only imports

    if not screen.check_permission():
        print("⚠️  需要辅助功能权限：系统设置 > 隐私与安全性 > 辅助功能，勾选你的终端，然后重启终端。")
        sys.exit(1)
    os.makedirs(os.path.dirname(args.log) or ".", exist_ok=True)

    apps = screen.installed_apps()
    kev = KevClient()
    print(f"Kev @ {kev.base_url} warm-up: {kev.warmup():.0f} ms   ({len(apps)} apps indexed)")

    if args.text:
        def next_cmd():
            line = input("\n> ").strip()
            return line, None, None
    else:
        from .stt import PushToTalk
        ptt = PushToTalk(args.hotkey, args.whisper, args.lang)
        print(f"Whisper warm-up: {ptt.warmup():.0f} ms")
        print(f"按住 {args.hotkey} 说话，松开执行。Ctrl+C 退出。")
        next_cmd = ptt.next

    last_state = None
    while True:
        try:
            cmd, stt_ms, audio_s = next_cmd()
        except (KeyboardInterrupt, EOFError):
            print("\nbye")
            return
        if not cmd:
            continue
        t0 = time.perf_counter()
        timing = {"stt": stt_ms, "route": None, "ax": None, "kev": None, "exec": None}
        rec = {"ts": datetime.now().isoformat(), "command": cmd, "audio_s": audio_s}

        plan = route(cmd, apps)
        timing["route"] = (time.perf_counter() - t0) * 1000
        snap = None

        needs_screen = plan is None or plan.action == "type"
        if needs_screen:
            snap = screen.snapshot(args.max_elements)
            timing["ax"] = snap.ms
            rec.update(app=snap.app, window=snap.window, n_elements=len(snap.elements))

        if plan is None:
            dec = decide(kev, cmd, snap.state, snap.elements, apps)
            timing["kev"] = dec.latency_ms
            plan = dec.plan
            rec.update(state=snap.state, cache_hit_expected=(snap.state == last_state),
                       answers=dec.answers, server_latency_ms=dec.server_latency_ms,
                       candidates=[{"id": e["id"], "desc": e["desc"]} for e in snap.elements])
            last_state = snap.state

            print(f"  model → {plan.action} {plan.arg or ''} (conf {plan.confidence:.2f})")
            for name in ("action", "target"):
                tk = topk(dec.answers.get(name))
                if tk:
                    print(f"    {name}: " + ", ".join(f"{k}={p:.2f}" for k, p in tk))

            label = None
            if args.confirm or plan.confidence < args.threshold:
                ans = input("  执行? [回车=是 / n=否 / e12=改成点这个元素] ").strip().lower()
                if ans == "n":
                    label = {"correct": False}
                    plan = Plan("none")
                elif ans.startswith("e") and ans[1:].isdigit():
                    label = {"correct": False, "target": ans}
                    plan = Plan("click", ans, "user")
                else:
                    label = {"correct": True}
            rec["label"] = label
        rec["plan"] = {"action": plan.action, "arg": plan.arg, "source": plan.source,
                       "confidence": plan.confidence, "extra": plan.extra}

        # ---- execute ----
        t1 = time.perf_counter()
        how = None
        if not args.dry_run and plan.action != "none":
            if snap is not None:
                screen.activate(snap.pid)
            if plan.action == "open_app":
                actions.open_app(plan.arg)
            elif plan.action == "volume":
                actions.set_volume(plan.arg)
            elif plan.action == "key":
                actions.press_key(plan.arg)
            elif plan.action == "scroll":
                actions.scroll(plan.arg)
            elif plan.action == "click":
                el = next(e for e in snap.elements if e["id"] == plan.arg)
                how = actions.click_element(snap.refs[plan.arg], el["frame"])
            elif plan.action == "type":
                text = plan.arg if isinstance(plan.arg, str) else compose_text(cmd)
                tid = plan.extra.get("target")
                if tid and tid in snap.refs:
                    el = next(e for e in snap.elements if e["id"] == tid)
                    actions.focus_element(snap.refs[tid], el["frame"])
                if text:
                    actions.type_text(text)
                    rec["typed"] = text
                else:
                    print("  (没有可输入的文本：说「输入 xxx」，或设置 ANTHROPIC_API_KEY 让 LLM 生成)")
        timing["exec"] = (time.perf_counter() - t1) * 1000
        total = (time.perf_counter() - t0) * 1000 + (stt_ms or 0)
        rec.update(timing=timing, total_ms=total, exec_via=how)
        log_jsonl(args.log, rec)

        print(f"[{plan.source}] {plan.action} {plan.arg if plan.arg is not None else ''}")
        print(f"  ⏱  {fmt_ms(timing)}  total={total:.0f}ms")


if __name__ == "__main__":
    main()

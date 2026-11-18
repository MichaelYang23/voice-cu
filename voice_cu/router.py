"""Rule-based fast path: handles obvious commands with zero model calls.

Anything not matched here falls through to the Kev decision model.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# Chinese / colloquial names -> macOS app names
APP_ALIASES = {
    "备忘录": "Notes", "笔记": "Notes", "notes": "Notes",
    "浏览器": "Safari", "safari": "Safari", "谷歌浏览器": "Google Chrome", "chrome": "Google Chrome",
    "设置": "System Settings", "系统设置": "System Settings", "settings": "System Settings",
    "终端": "Terminal", "计算器": "Calculator", "日历": "Calendar", "访达": "Finder",
    "微信": "WeChat", "音乐": "Music", "邮件": "Mail", "照片": "Photos",
    "提醒事项": "Reminders", "地图": "Maps", "信息": "Messages", "预览": "Preview",
    "文本编辑": "TextEdit", "活动监视器": "Activity Monitor", "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
}

# spoken phrase -> key combo
KEY_PHRASES = {
    "enter": "enter", "press enter": "enter", "回车": "enter", "按回车": "enter", "确认": "enter",
    "escape": "escape", "取消": "escape", "esc": "escape",
    "undo": "cmd+z", "撤销": "cmd+z", "redo": "cmd+shift+z", "重做": "cmd+shift+z",
    "save": "cmd+s", "保存": "cmd+s",
    "copy": "cmd+c", "复制": "cmd+c", "paste": "cmd+v", "粘贴": "cmd+v",
    "select all": "cmd+a", "全选": "cmd+a", "cut": "cmd+x", "剪切": "cmd+x",
    "new tab": "cmd+t", "新标签页": "cmd+t", "新建标签页": "cmd+t",
    "close tab": "cmd+w", "关闭标签页": "cmd+w", "close window": "cmd+w", "关闭窗口": "cmd+w",
    "new": "cmd+n", "new note": "cmd+n", "新建": "cmd+n", "新建笔记": "cmd+n", "new window": "cmd+n",
    "find": "cmd+f", "search": "cmd+f", "查找": "cmd+f", "搜索": "cmd+f",
    "quit": "cmd+q", "退出": "cmd+q", "go back": "cmd+[", "返回": "cmd+[", "后退": "cmd+[",
    "go forward": "cmd+]", "前进": "cmd+]", "refresh": "cmd+r", "刷新": "cmd+r",
}

SCROLL_PHRASES = {
    "scroll down": "down", "向下滚动": "down", "往下翻": "down", "往下滚": "down", "下一页": "down",
    "scroll up": "up", "向上滚动": "up", "往上翻": "up", "往上滚": "up", "上一页": "up",
}

_PUNCT = re.compile(r"[，。！？,.!?、\s]+$")
_OPEN = re.compile(r"^(?:please\s+)?(?:open|launch|start|switch to|go to app|打开|启动|切换到|开一下)\s*(.+)$", re.I)
_TYPE = re.compile(r"^(?:type|write|输入|打字|写上|写)\s*[:：]?\s*[\"“']?(.+?)[\"”']?$", re.I)
_VOLUME = re.compile(r"(?:set\s+)?volume\s*(?:to\s*)?(\d{1,3})|音量(?:调到|设为|设置为|调成)?\s*(\d{1,3})", re.I)


@dataclass
class Plan:
    action: str                    # open_app | type | key | scroll | volume | click | none
    arg: str | int | None = None   # app name / text / key combo / direction / volume / element id
    source: str = "router"         # router | model
    confidence: float = 1.0
    extra: dict = field(default_factory=dict)


def normalize(cmd: str) -> str:
    return _PUNCT.sub("", cmd.strip()).strip()


def match_app(name: str, installed: list[str], cutoff: float = 0.75) -> str | None:
    key = name.strip().lower()
    key = re.sub(r"(app|应用|程序)$", "", key).strip()
    if key in APP_ALIASES:
        alias = APP_ALIASES[key]
        return alias if not installed or alias in installed else None
    lowered = {a.lower(): a for a in installed}
    if key in lowered:
        return lowered[key]
    hit = difflib.get_close_matches(key, list(lowered), n=1, cutoff=cutoff)
    return lowered[hit[0]] if hit else None


def route(cmd: str, installed_apps: list[str]) -> Plan | None:
    """Return a Plan if the command is unambiguous, else None (-> model)."""
    c = normalize(cmd)
    if not c:
        return Plan("none")
    low = c.lower()

    m = _VOLUME.search(low)
    if m:
        v = int(m.group(1) or m.group(2))
        return Plan("volume", max(0, min(100, v)))

    if low in KEY_PHRASES:
        return Plan("key", KEY_PHRASES[low])
    if low in SCROLL_PHRASES:
        return Plan("scroll", SCROLL_PHRASES[low])

    m = _TYPE.match(c)
    if m:
        return Plan("type", m.group(1))

    m = _OPEN.match(c)
    if m:
        app = match_app(m.group(1), installed_apps)
        if app:
            return Plan("open_app", app)
        # "open the settings button" etc. -> let the model decide
    return None

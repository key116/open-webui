"""
title: 游戏卡片路由
author: key116
version: 0.1.0
description: 关键词映射到 UI 组件。AI 每回合把游戏状态交给本工具，工具按“关键词表”自动挑选卡片组件、按“题材标签”挑选皮肤，并只突出本回合变化的内容。选项按钮点一下就把行动文字发回聊天。
required_open_webui_version: 0.6.0
"""

import json
import re
import time
from html import escape
from typing import Any, Optional

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 默认映射表（可以在 Open WebUI 的工具设置 Valves 里直接改，不用动代码）
# ---------------------------------------------------------------------------

DEFAULT_KEYWORD_MAP = {
    "标题": ["标题", "游戏名", "游戏", "title", "game"],
    "题材": ["题材", "标签", "风格", "theme", "tags", "genre"],
    "时间": ["时间", "回合", "天数", "日期", "倒计时", "time", "turn", "day", "clock"],
    "数值": ["状态", "数值", "属性", "stats", "status", "生命", "hp", "血量", "体力",
             "理智", "san", "幸运", "魔力", "mp", "精力", "饱食", "水分", "金币", "金钱",
             "gold", "钱", "体面", "奶酪", "声望", "压力", "污染"],
    "地点": ["地点", "场景", "位置", "所在", "location", "scene", "place", "room"],
    "事件": ["事件", "提示", "消息", "通知", "event", "notice", "message"],
    "判定": ["判定", "检定", "骰子", "掷骰", "鉴定", "roll", "check", "dice"],
    "道具": ["背包", "物品", "道具", "装备", "inventory", "items", "bag"],
    "线索": ["线索", "证据", "笔记", "clues", "evidence", "notes"],
    "人物": ["人物", "角色", "npc", "npcs", "characters", "同伴", "队友"],
    "关系": ["关系", "好感", "信任", "relations", "relationship", "trust"],
    "任务": ["任务", "目标", "任务清单", "quests", "quest", "objectives", "tasks", "todo"],
    "选项": ["选项", "行动", "可选行动", "choices", "options", "actions"],
}

DEFAULT_THEME_MAP = {
    "noir": ["恐怖", "克苏鲁", "coc", "规则怪谈", "侦探", "悬疑", "推理", "黑色", "惊悚", "noir", "horror", "mystery"],
    "parchment": ["中世纪", "奇幻", "搞笑", "童话", "冒险", "武侠", "仙侠", "古风", "fantasy", "medieval", "comedy"],
    "terminal": ["科幻", "赛博", "赛博朋克", "太空", "末日", "废土", "黑客", "未来", "scifi", "cyberpunk", "space"],
}

SKIN_LABEL = {"noir": "暗色档案", "parchment": "羊皮纸", "terminal": "终端"}

# 组件显示顺序
ORDER = ["事件", "判定", "地点", "道具", "线索", "人物", "关系", "任务", "其他"]


class Tools:
    class Valves(BaseModel):
        keyword_map: str = Field(
            default=json.dumps(DEFAULT_KEYWORD_MAP, ensure_ascii=False),
            description="关键词映射表（JSON）。左边是组件名，右边是会被认成这个组件的字段名。可以自由添加同义词。",
        )
        theme_map: str = Field(
            default=json.dumps(DEFAULT_THEME_MAP, ensure_ascii=False),
            description="题材标签映射表（JSON）。左边是皮肤名 noir / parchment / terminal，右边是会选中它的题材词。",
        )
        default_skin: str = Field(default="noir", description="认不出题材时用的皮肤：noir / parchment / terminal")
        only_highlight_changes: bool = Field(
            default=True,
            description="开：本回合没变化的分区收进可点开的小标签，只把变化的内容摊开显示。关：每次全部摊开。",
        )
        sound: bool = Field(default=True, description="点按钮时播放程序合成的短音效")

    def __init__(self):
        self.valves = self.Valves()
        # 每个聊天上一回合的状态，用来算“这回合变了什么”。服务器重启后会清空，不影响使用。
        self._memory: dict = {}

    # ------------------------------------------------------------------
    # 唯一的指令
    # ------------------------------------------------------------------
    def render_game_ui(
        self,
        state: dict,
        theme: str = "",
        new_game: bool = False,
        __chat_id__: Optional[str] = None,
        __metadata__: Optional[dict] = None,
    ):
        """
        把当前游戏状态显示成游戏 UI 卡片。每回合叙事写完后调用一次，放在回复最后。
        不用挑卡片：工具会按字段名自动决定显示哪些组件。

        state 用中文字段名填写，只填本游戏有的部分，例如：
        {
          "标题": "傻子跑腿",
          "回合": 3,
          "数值": {"金币": 2, "体面": "4/10", "奶酪": 1},
          "地点": {"名称": "老磨坊", "描述": "一句画面描写", "可探索": ["磨盘"], "危险": ["松动的楼梯"]},
          "事件": "鹅杰拉尔德冲你嘎了一声",
          "判定": {"项目": "说服", "骰值": 34, "目标": 50, "结果": "成功"},
          "背包": [{"名称": "生锈钥匙", "说明": "从井里捞上来的"}],
          "线索": ["井底有歌声"],
          "人物": [{"名称": "磨坊主", "备注": "很警惕", "信任": 2}],
          "任务": [{"名称": "取回魔法汤勺", "完成": false}],
          "选项": ["跟磨坊主道歉", "偷偷爬上楼梯", "把奶酪分给鹅"]
        }
        选项要写成玩家会说出口的完整行动句。数值可写数字、"当前/上限" 或 {"值": 7, "上限": 10}。

        :param state: 当前游戏状态，字段名见上例，可以只给一部分。
        :param theme: 题材词，例如 "中世纪 搞笑"、"克苏鲁"、"赛博朋克"。新游戏第一次调用时给，之后可省略。
        :param new_game: 开始新的一局时设为 true，清掉上一局的对比记录。
        """
        keyword_map = _load_json(self.valves.keyword_map, DEFAULT_KEYWORD_MAP)
        theme_map = _load_json(self.valves.theme_map, DEFAULT_THEME_MAP)

        if isinstance(state, str):
            try:
                state = json.loads(state)
            except Exception:
                state = {"事件": state}
        if not isinstance(state, dict):
            state = {}

        chat_key = __chat_id__ or (__metadata__ or {}).get("chat_id") or "default"
        if new_game:
            self._memory.pop(chat_key, None)
        previous = self._memory.get(chat_key, {})

        view = _normalize(state, keyword_map)

        theme_text = theme or view.pop("_theme", "") or ""
        skin = _pick_skin(theme_text, theme_map) if theme_text else previous.get("skin")
        if skin not in SKIN_LABEL:
            skin = self.valves.default_skin if self.valves.default_skin in SKIN_LABEL else "noir"

        prev_view = previous.get("view") or {}
        if not view.get("标题") and prev_view.get("标题"):
            view["标题"] = prev_view["标题"]
        changes = _diff(prev_view, view)

        self._memory[chat_key] = {"view": view, "skin": skin, "at": time.time()}

        html = _render(view, changes, skin, self.valves.only_highlight_changes, self.valves.sound)
        response = HTMLResponse(
            content=html,
            headers={"Content-Disposition": 'inline; filename="game-ui.html"'},
        )
        shown = [name for name in ORDER + ["数值", "选项"] if view.get(name)]
        context = (
            "游戏界面卡片已经显示给玩家（皮肤：" + SKIN_LABEL[skin] + "；组件：" + "、".join(shown) + "）。"
            + ("本回合变化：" + "；".join(changes["summary"]) + "。" if changes["summary"] else "")
            + "不要在文字里再罗列数值、背包或选项；选项按钮已在卡片上，等玩家点选或自己打字。"
        )
        return response, context


# ---------------------------------------------------------------------------
# 数据整理：字段名 → 组件
# ---------------------------------------------------------------------------

def _load_json(text: str, fallback: dict) -> dict:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else fallback
    except Exception:
        return fallback


def _match(key: str, keyword_map: dict) -> Optional[str]:
    k = str(key).strip().lower()
    for comp, words in keyword_map.items():
        if k in [str(w).lower() for w in words]:
            return comp
    best, best_len = None, 0
    for comp, words in keyword_map.items():
        for w in words:
            w = str(w).lower()
            if len(w) >= 2 and w in k and len(w) > best_len:
                best, best_len = comp, len(w)
    return best


def _pick_skin(theme_text: str, theme_map: dict) -> Optional[str]:
    t = str(theme_text).lower()
    for skin, words in theme_map.items():
        if any(str(w).lower() in t for w in words):
            return skin
    return None


def _first(d: dict, *names, default=None):
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return default


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return [value]


def _is_stat_dict(v) -> bool:
    return isinstance(v, dict) and any(k in v for k in ("值", "当前", "value", "current", "上限", "max"))


def _stat(label: str, raw) -> dict:
    value, maximum, text = None, None, None
    if isinstance(raw, bool):
        text = "是" if raw else "否"
    elif isinstance(raw, (int, float)):
        value = raw
    elif isinstance(raw, dict):
        value = _num(_first(raw, "值", "当前", "value", "current"))
        maximum = _num(_first(raw, "上限", "max", "最大"))
        if value is None:
            text = str(_first(raw, "值", "当前", "value", "current", default=""))
    else:
        s = str(raw).strip()
        m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s)
        if m:
            value, maximum = _num(m.group(1)), _num(m.group(2))
        elif _num(s) is not None:
            value = _num(s)
        else:
            text = s
    return {"label": str(label), "value": value, "max": maximum, "text": text}


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else v
    try:
        f = float(str(v).strip())
        return int(f) if f.is_integer() else f
    except Exception:
        return None


def _named(item, name_keys, desc_keys) -> dict:
    if isinstance(item, dict):
        name = _first(item, *name_keys)
        desc = _first(item, *desc_keys, default="")
        if name is None and len(item) == 1:
            name, desc = next(iter(item.items()))
        return {"name": str(name if name is not None else "未命名"), "desc": str(desc or ""), "raw": item}
    return {"name": str(item), "desc": "", "raw": {}}


CLOCK_LABEL = {"time": "时间", "clock": "时间", "turn": "回合", "day": "天数", "date": "日期"}

NAME = ("名称", "名字", "name", "title", "标题", "内容", "文字", "text", "label")
DESC = ("说明", "描述", "备注", "身份", "效果", "desc", "description", "note", "detail")


def _normalize(state: dict, keyword_map: dict) -> dict:
    view: dict = {"数值": [], "时间": [], "事件": [], "道具": [], "线索": [], "人物": [],
                  "关系": [], "任务": [], "选项": [], "其他": []}
    for key, value in state.items():
        comp = _match(key, keyword_map)
        if comp == "标题":
            view["标题"] = str(value)
        elif comp == "题材":
            view["_theme"] = " ".join(value) if isinstance(value, list) else str(value)
        elif comp == "时间":
            label = CLOCK_LABEL.get(str(key).strip().lower(), str(key))
            view["时间"].append({"label": label, "text": str(value)})
        elif comp == "数值":
            if isinstance(value, dict) and not _is_stat_dict(value):
                for k, v in value.items():
                    view["数值"].append(_stat(k, v))
            elif isinstance(value, list):
                for it in value:
                    if isinstance(it, dict):
                        lab = _first(it, *NAME, default="数值")
                        view["数值"].append(_stat(lab, it))
            else:
                view["数值"].append(_stat(key, value))
        elif comp == "地点":
            if isinstance(value, dict):
                points = []
                for p in _as_list(_first(value, "可探索", "探索点", "互动点", "points", "explore")):
                    points.append({"name": _named(p, NAME, DESC)["name"], "danger": False})
                for p in _as_list(_first(value, "危险", "警告", "danger", "hazards")):
                    points.append({"name": _named(p, NAME, DESC)["name"], "danger": True})
                view["地点"] = {
                    "name": str(_first(value, *NAME, "地点", "location", default="未知地点")),
                    "desc": str(_first(value, *DESC, default="")),
                    "points": points,
                }
            else:
                view["地点"] = {"name": str(value), "desc": "", "points": []}
        elif comp == "事件":
            view["事件"] += [str(_named(v, NAME, DESC)["name"]) for v in _as_list(value)]
        elif comp == "判定":
            if isinstance(value, dict):
                view["判定"] = {
                    "name": str(_first(value, "项目", "技能", "属性", "name", "skill", default="判定")),
                    "roll": _first(value, "骰值", "点数", "掷出", "roll", "value", default="?"),
                    "target": _first(value, "目标", "难度", "目标值", "target", "difficulty", default=""),
                    "result": str(_first(value, "结果", "outcome", "result", default="")),
                }
            else:
                view["判定"] = {"name": "判定", "roll": "?", "target": "", "result": str(value)}
        elif comp == "道具":
            items = []
            if isinstance(value, dict) and not any(k in value for k in NAME):
                for k, v in value.items():
                    items.append({"name": str(k), "desc": "", "qty": _num(v) if _num(v) is not None else None})
            else:
                for it in _as_list(value):
                    n = _named(it, NAME, DESC)
                    qty = _num(_first(n["raw"], "数量", "qty", "count")) if n["raw"] else None
                    items.append({"name": n["name"], "desc": n["desc"], "qty": qty})
            view["道具"] = items
        elif comp == "线索":
            view["线索"] = [{k: v for k, v in _named(it, NAME, ("内容",) + DESC).items() if k != "raw"} for it in _as_list(value)]
        elif comp == "人物":
            people = []
            for it in _as_list(value):
                n = _named(it, ("名称", "名字", "name"), DESC)
                rel, rel_label = None, ""
                for rk in ("信任", "好感", "关系", "trust", "affinity"):
                    if n["raw"] and n["raw"].get(rk) not in (None, ""):
                        rel, rel_label = n["raw"][rk], {"trust": "信任", "affinity": "好感"}.get(rk, rk)
                        break
                people.append({"name": n["name"], "desc": n["desc"], "rel": rel, "rel_label": rel_label})
            view["人物"] = people
        elif comp == "关系":
            rels = []
            if isinstance(value, dict):
                for k, v in value.items():
                    rels.append(_stat(k, v))
            else:
                for it in _as_list(value):
                    if isinstance(it, dict):
                        rels.append(_stat(_first(it, *NAME, "对象", default="?"), _first(it, "值", "信任", "好感", "value", default="")))
            view["关系"] = rels
        elif comp == "任务":
            quests = []
            if isinstance(value, dict) and not any(k in value for k in NAME):
                for k, v in value.items():
                    quests.append({"name": str(k), "done": _done(v)})
            else:
                for it in _as_list(value):
                    n = _named(it, NAME, DESC)
                    quests.append({"name": n["name"], "done": _done(_first(n["raw"], "完成", "已完成", "done", "状态", "status")) if n["raw"] else False})
            view["任务"] = quests
        elif comp == "选项":
            opts = []
            for it in _as_list(value):
                n = _named(it, NAME, DESC)
                if n["name"].strip():
                    opts.append({"text": n["name"].strip(), "desc": n["desc"]})
            view["选项"] = opts[:8]
        else:
            # 认不出的字段：数字 → 数值条；其他 → 通用信息
            if isinstance(value, (int, float)) and not isinstance(value, bool) or _is_stat_dict(value) or (
                isinstance(value, str) and re.fullmatch(r"-?\d+\s*/\s*\d+", value.strip())
            ):
                view["数值"].append(_stat(key, value))
            else:
                if isinstance(value, (list, dict)):
                    text = "、".join(_named(v, NAME, DESC)["name"] for v in _as_list(value)) if isinstance(value, list) else json.dumps(value, ensure_ascii=False)
                else:
                    text = str(value)
                view["其他"].append({"label": str(key), "text": text})
    return view


def _done(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("完成", "已完成", "done", "true", "yes", "是", "✓", "√")


# ---------------------------------------------------------------------------
# 对比上一回合
# ---------------------------------------------------------------------------

def _diff(prev: dict, cur: dict) -> dict:
    first = not prev
    ch = {"first": first, "stat_delta": {}, "new_items": set(), "new_clues": set(), "new_people": set(),
          "quest_changed": set(), "sections": set(), "summary": []}
    prev_stats = {s["label"]: s for s in prev.get("数值", [])}
    for s in cur.get("数值", []):
        p = prev_stats.get(s["label"])
        if p and isinstance(s["value"], (int, float)) and isinstance(p.get("value"), (int, float)) and s["value"] != p["value"]:
            d = s["value"] - p["value"]
            d = int(d) if float(d).is_integer() else round(d, 2)
            ch["stat_delta"][s["label"]] = d
            ch["summary"].append(f"{s['label']}{'+' if d > 0 else ''}{d}")
        elif p and s.get("text") != p.get("text") and s.get("text"):
            ch["stat_delta"][s["label"]] = "变"
    prev_rel = {s["label"]: s for s in prev.get("关系", [])}
    for s in cur.get("关系", []):
        p = prev_rel.get(s["label"])
        if p and isinstance(s["value"], (int, float)) and isinstance(p.get("value"), (int, float)) and s["value"] != p["value"]:
            ch["stat_delta"]["关系:" + s["label"]] = s["value"] - p["value"]
            ch["sections"].add("关系")

    def names(view, comp):
        return {x["name"] for x in view.get(comp, [])}

    for comp, key, word in (("道具", "new_items", "获得"), ("线索", "new_clues", "新线索"), ("人物", "new_people", "遇见")):
        new = names(cur, comp) - names(prev, comp)
        if not first:
            ch[key] = new
            if new:
                ch["sections"].add(comp)
                ch["summary"].append(word + "：" + "、".join(sorted(new)))
        if not first and names(prev, comp) - names(cur, comp):
            ch["sections"].add(comp)

    prev_q = {q["name"]: q["done"] for q in prev.get("任务", [])}
    for q in cur.get("任务", []):
        if q["name"] not in prev_q or prev_q[q["name"]] != q["done"]:
            if not first:
                ch["quest_changed"].add(q["name"])
                ch["sections"].add("任务")
                ch["summary"].append(("完成任务：" if q["done"] else "新任务：") + q["name"])

    if (cur.get("地点") or {}).get("name") != (prev.get("地点") or {}).get("name"):
        ch["sections"].add("地点")
        if not first and cur.get("地点"):
            ch["summary"].append("来到：" + cur["地点"]["name"])
    for comp in ("事件", "判定", "其他"):
        if cur.get(comp):
            ch["sections"].add(comp)
    if first:
        ch["sections"] |= {c for c in ORDER if cur.get(c)}
    return ch


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def _e(v) -> str:
    return escape("" if v is None else str(v))


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.1f}".rstrip("0").rstrip(".")
    return str(v)


def _render_stats(view, ch) -> str:
    stats = view.get("数值", [])
    clocks = view.get("时间", [])
    title = view.get("标题", "")
    if not (stats or clocks or title):
        return ""
    head = ""
    if title or clocks:
        clock_html = "".join(f'<span class="clock">{_e(c["label"])} {_e(c["text"])}</span>' for c in clocks)
        head = f'<div class="hud-head"><span class="game-title">{_e(title)}</span><span class="clocks">{clock_html}</span></div>'
    cells = []
    for s in stats:
        d = ch["stat_delta"].get(s["label"])
        delta = ""
        if isinstance(d, (int, float)):
            cls = "up" if d > 0 else "down"
            delta = f'<span class="delta {cls}">{"+" if d > 0 else ""}{_fmt(d)}</span>'
        elif d:
            delta = '<span class="delta up">变</span>'
        if s["value"] is not None:
            val = _fmt(s["value"]) + (f'<small>/{_fmt(s["max"])}</small>' if s["max"] else "")
        else:
            val = _e(s["text"])
        bar = ""
        if s["value"] is not None and s["max"]:
            pct = max(0, min(100, s["value"] / s["max"] * 100))
            low = " low" if pct <= 25 else ""
            bar = f'<div class="bar{low}"><i style="width:{pct:.0f}%"></i></div>'
        changed = " changed" if d else ""
        cells.append(f'<div class="stat{changed}"><span class="lab">{_e(s["label"])}</span><span class="val">{val}{delta}</span>{bar}</div>')
    grid = f'<div class="stats">{"".join(cells)}</div>' if cells else ""
    return f'<section class="hud">{head}{grid}</section>'


def _sec(title: str, body: str, cls: str = "") -> str:
    return f'<section class="sec {cls}"><h3>{_e(title)}</h3>{body}</section>'


def _render_component(comp, view, ch) -> str:
    v = view.get(comp)
    if not v:
        return ""
    if comp == "事件":
        return "".join(f'<div class="event"><b>!</b><span>{_e(t)}</span></div>' for t in v)
    if comp == "判定":
        res = v["result"]
        tone = "ok" if any(w in res for w in ("成功", "通过", "success")) else ("bad" if any(w in res for w in ("失败", "fail")) else "mid")
        if "大成功" in res or "极难成功" in res or "critical" in res.lower():
            tone = "crit"
        target = f'<span class="target">目标 {_e(v["target"])}</span>' if v["target"] not in ("", None) else ""
        return (f'<section class="roll {tone}"><div class="die">{_e(v["roll"])}</div>'
                f'<div class="roll-info"><span class="roll-name">{_e(v["name"])} 判定</span>{target}</div>'
                f'<div class="stamp">{_e(res or "?")}</div></section>')
    if comp == "地点":
        pts = "".join(
            f'<span class="pt {"danger" if p["danger"] else ""}"><i></i>{_e(p["name"])}</span>' for p in v["points"]
        )
        moved = " arrived" if "地点" in ch["sections"] and not ch["first"] else ""
        desc = f'<p class="loc-desc">{_e(v["desc"])}</p>' if v["desc"] else ""
        return (f'<section class="loc{moved}"><div class="loc-kicker">当前地点</div><div class="loc-name">{_e(v["name"])}</div>'
                f'{desc}{f"<div class=pts>{pts}</div>" if pts else ""}</section>')
    if comp == "道具":
        tiles = []
        for it in v:
            new = " new" if it["name"] in ch["new_items"] else ""
            qty = f'<span class="qty">×{_fmt(it["qty"])}</span>' if it.get("qty") not in (None, 1) else ""
            desc = f'<span class="idesc">{_e(it["desc"])}</span>' if it["desc"] else ""
            badge = '<span class="badge">新</span>' if new else ""
            tiles.append(f'<div class="item{new}">{badge}<span class="iname">{_e(it["name"])}{qty}</span>{desc}</div>')
        return _sec("背包", f'<div class="items">{"".join(tiles)}</div>')
    if comp == "线索":
        strips = []
        for i, c in enumerate(v):
            new = " new" if c["name"] in ch["new_clues"] else ""
            desc = f'<span class="cdesc">{_e(c["desc"])}</span>' if c["desc"] else ""
            strips.append(f'<div class="clue r{i % 3}{new}"><span class="cname">{_e(c["name"])}</span>{desc}</div>')
        return _sec("线索", f'<div class="clues">{"".join(strips)}</div>')
    if comp == "人物":
        rows = []
        for p in v:
            new = " new" if p["name"] in ch["new_people"] else ""
            rel = f'<span class="rel"><small>{_e(p.get("rel_label", ""))}</small>{_e(p["rel"])}</span>' if p["rel"] not in (None, "") else ""
            desc = f'<span class="pdesc">{_e(p["desc"])}</span>' if p["desc"] else ""
            rows.append(f'<div class="person{new}"><span class="avatar">{_e(p["name"][:1])}</span>'
                        f'<span class="pinfo"><span class="pname">{_e(p["name"])}</span>{desc}</span>{rel}</div>')
        return _sec("人物", "".join(rows))
    if comp == "关系":
        rows = []
        for s in v:
            d = ch["stat_delta"].get("关系:" + s["label"])
            delta = f'<span class="delta {"up" if d > 0 else "down"}">{"+" if d > 0 else ""}{_fmt(d)}</span>' if isinstance(d, (int, float)) else ""
            val = _fmt(s["value"]) if s["value"] is not None else _e(s["text"])
            rows.append(f'<div class="relrow"><span>{_e(s["label"])}</span><span class="relval">{val}{delta}</span></div>')
        return _sec("关系", "".join(rows))
    if comp == "任务":
        rows = []
        for q in v:
            cls = "done" if q["done"] else ""
            chg = " changed" if q["name"] in ch["quest_changed"] else ""
            mark = "☑" if q["done"] else "☐"
            rows.append(f'<div class="quest {cls}{chg}"><span class="box">{mark}</span><span>{_e(q["name"])}</span></div>')
        return _sec("任务", "".join(rows), "quests")
    if comp == "其他":
        rows = "".join(f'<div class="kv"><span>{_e(o["label"])}</span><span>{_e(o["text"])}</span></div>' for o in v)
        return _sec("其他信息", rows)
    return ""


def _render_choices(view) -> str:
    opts = view.get("选项", [])
    if not opts:
        return ""
    btns = []
    for i, o in enumerate(opts):
        letter = "ABCDEFGH"[i]
        desc = f'<span class="odesc">{_e(o["desc"])}</span>' if o["desc"] else ""
        btns.append(f'<div class="optw"><button type="button" class="opt" data-text="{_e(o["text"])}"><span class="letter">{letter}</span>'
                    f'<span class="otext">{_e(o["text"])}{desc}</span></button></div>')
    return (f'<section class="choices"><div class="choices-head">你打算</div>{"".join(btns)}'
            f'<div class="hint" id="hint">点选项即发送，也可以直接在下面打字做别的</div></section>')


def _render(view, ch, skin, only_changes, sound) -> str:
    parts = [_render_stats(view, ch)]
    folded = []
    for comp in ORDER:
        if not view.get(comp):
            continue
        if only_changes and not ch["first"] and comp not in ch["sections"] and comp in ("道具", "线索", "人物", "关系", "任务", "地点"):
            count = len(view[comp]) if isinstance(view[comp], list) else 1
            label = {"道具": "背包", "地点": view["地点"]["name"] if comp == "地点" else comp}.get(comp, comp)
            folded.append(f'<details class="fold"><summary>{_e(label)}{"" if comp == "地点" else f" {count}"}</summary>'
                          f'{_render_component(comp, view, ch)}</details>')
        else:
            parts.append(_render_component(comp, view, ch))
    if folded:
        parts.append(f'<div class="folds">{"".join(folded)}</div>')
    parts.append(_render_choices(view))
    body = "".join(p for p in parts if p)
    return (TEMPLATE.replace("__SKIN__", skin)
            .replace("__SOUND__", "true" if sound else "false")
            .replace("__BODY__", body))


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:transparent;-webkit-text-size-adjust:100%}
body{padding:6px 8px 12px;font:13px/1.55 -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
.skin-noir{--outer:#121214;--card1:#1f1d22;--card2:#141317;--ink:#e8e0d2;--muted:#968f84;--line:#3b3631;--accent:#c8473f;--accent2:#7fa3bd;--slice:#050506;--chip:#ffffff0d;--good:#7fc48a;--bad:#e0625a;--title:"Songti SC","STSong","Noto Serif SC",serif;--mono:"Menlo","SF Mono","Courier New",monospace}
.skin-parchment{--outer:#2b2117;--card1:#f1e5c8;--card2:#dfcb9f;--ink:#3a2817;--muted:#7d6546;--line:#b8995f;--accent:#a3401f;--accent2:#3d6b4c;--slice:#6e4f2a;--chip:#7a5a3014;--good:#2f7a45;--bad:#b0381f;--title:"Songti SC","STSong","Noto Serif SC",serif;--mono:"Menlo","SF Mono","Courier New",monospace}
.skin-terminal{--outer:#05090c;--card1:#0d1a1f;--card2:#071012;--ink:#bff7dc;--muted:#5d9682;--line:#1d4a3f;--accent:#3ff0a8;--accent2:#c579ff;--slice:#010303;--chip:#3ff0a80f;--good:#3ff0a8;--bad:#ff5f7a;--title:"Menlo","SF Mono","Courier New",monospace;--mono:"Menlo","SF Mono","Courier New",monospace}
.wrap{background:var(--outer);border-radius:14px;padding:12px 12px 14px;color:var(--ink);position:relative;overflow:hidden}
.skin-terminal .wrap::after{content:"";position:absolute;inset:0;pointer-events:none;background:repeating-linear-gradient(0deg,#ffffff06 0 1px,transparent 1px 3px);animation:scan 6s linear infinite}
@keyframes scan{to{background-position:0 60px}}
.slab{position:relative;margin-bottom:10px}
.slab::before{content:"";position:absolute;inset:0;transform:translate(4px,5px);background:var(--slice);clip-path:polygon(12px 0,100% 0,100% calc(100% - 12px),calc(100% - 12px) 100%,0 100%,0 12px)}
.card{position:relative;background:linear-gradient(160deg,var(--card1),var(--card2));clip-path:polygon(12px 0,100% 0,100% calc(100% - 12px),calc(100% - 12px) 100%,0 100%,0 12px);padding:11px 12px;box-shadow:inset 0 1px 0 #ffffff14}
.skin-parchment .card{box-shadow:inset 0 0 22px #8a5a2a40,inset 0 1px 0 #fff8}
.hud-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:8px}
.game-title{font-family:var(--title);font-size:15px;font-weight:700;letter-spacing:1px}
.clock{font-family:var(--mono);font-size:11px;color:var(--muted);border:1px solid var(--line);padding:1px 6px;margin-left:4px;white-space:nowrap}
.hud{margin-bottom:10px}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(92px,1fr));gap:6px}
.stat{background:var(--chip);border:1px solid var(--line);padding:5px 7px 6px;position:relative}
.stat.changed{border-color:var(--accent);animation:pulse 1.6s ease-in-out 2}
.lab{display:block;font-size:11px;color:var(--muted)}
.val{font-family:var(--mono);font-size:17px;font-weight:700;display:flex;align-items:baseline;gap:5px}
.val small{font-size:11px;color:var(--muted);font-weight:400}
.delta{font-size:11px;font-weight:700;padding:0 4px;animation:pop .5s ease-out}
.delta.up{color:var(--good)}.delta.down{color:var(--bad)}
.bar{height:4px;background:#0000002e;margin-top:4px;overflow:hidden}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--accent2),var(--accent));transition:width .6s}
.bar.low i{background:var(--bad);animation:blink 1.2s ease-in-out infinite}
@keyframes pulse{50%{box-shadow:0 0 12px var(--accent)}}
@keyframes pop{0%{transform:scale(1.8);opacity:0}100%{transform:scale(1);opacity:1}}
@keyframes blink{50%{opacity:.45}}
.event{display:flex;gap:8px;align-items:flex-start;background:var(--chip);border-left:3px solid var(--accent);padding:7px 9px;margin-bottom:8px;animation:slide .45s ease-out}
.event b{font-family:var(--mono);color:var(--accent)}
@keyframes slide{from{transform:translateX(-10px);opacity:0}}
.roll{display:flex;align-items:center;gap:10px;border:1px solid var(--line);background:var(--chip);padding:8px 10px;margin-bottom:8px;position:relative;overflow:hidden}
.die{width:46px;height:46px;flex:none;display:grid;place-items:center;font-family:var(--mono);font-size:19px;font-weight:700;border:2px solid var(--ink);transform:rotate(-4deg);box-shadow:3px 3px 0 var(--slice);animation:roll .7s cubic-bezier(.2,1.4,.4,1)}
@keyframes roll{0%{transform:rotate(-200deg) scale(.4);opacity:0}}
.roll-info{display:flex;flex-direction:column;flex:1;min-width:0}
.roll-name{font-weight:700}.target{font-family:var(--mono);font-size:11px;color:var(--muted)}
.stamp{font-family:var(--title);font-size:16px;font-weight:700;padding:3px 9px;border:2px solid currentColor;transform:rotate(-7deg);animation:stamp .5s .35s both,breathe 2.4s 1s ease-in-out infinite}
.roll.ok .stamp{color:var(--good)}.roll.bad .stamp{color:var(--bad)}.roll.mid .stamp{color:var(--accent2)}.roll.crit .stamp{color:var(--accent);text-shadow:0 0 8px var(--accent)}
@keyframes stamp{from{transform:rotate(-7deg) scale(2.2);opacity:0}}
@keyframes breathe{50%{filter:drop-shadow(0 0 5px currentColor)}}
.loc{border:1px solid var(--line);padding:9px 11px 10px;margin-bottom:8px;background:radial-gradient(circle at 85% 0%,#ffffff12,transparent 60%),var(--chip);position:relative}
.loc.arrived{animation:slide .5s ease-out}
.loc-kicker{font-size:10px;letter-spacing:2px;color:var(--muted)}
.loc-name{font-family:var(--title);font-size:17px;font-weight:700;margin:1px 0 3px}
.loc-desc{color:var(--ink);opacity:.85}
.pts{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px}
.pt{display:inline-flex;align-items:center;gap:5px;font-size:12px;padding:2px 8px 2px 6px;border:1px solid var(--line);background:#00000018}
.pt i{width:8px;height:8px;transform:rotate(45deg);background:var(--accent2);box-shadow:0 0 0 0 var(--accent2);animation:glow 2s ease-in-out infinite}
.pt.danger i{background:var(--bad);animation-duration:1s}
@keyframes glow{50%{box-shadow:0 0 7px 2px currentColor;transform:rotate(45deg) scale(1.25)}}
.sec{margin-bottom:8px}
.sec h3{font-size:11px;letter-spacing:2px;color:var(--muted);font-weight:600;margin-bottom:5px;display:flex;align-items:center;gap:6px}
.sec h3::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--line),transparent)}
.items{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.item{position:relative;border:1px solid var(--line);background:var(--chip);padding:6px 8px;box-shadow:2px 2px 0 var(--slice)}
.item.new{border-color:var(--accent);animation:pulse 1.6s ease-in-out 3}
.badge{position:absolute;top:-1px;right:-1px;background:var(--accent);color:var(--card2);font-size:10px;font-weight:700;padding:0 5px}
.iname{font-weight:700;display:block}.qty{font-family:var(--mono);font-size:11px;color:var(--muted);margin-left:4px}
.idesc,.cdesc,.pdesc{display:block;font-size:11px;color:var(--muted);line-height:1.4}
.clues{display:flex;flex-direction:column;gap:6px;padding:2px 4px}
.clue{background:var(--card1);color:var(--ink);border:1px solid var(--line);padding:6px 9px;box-shadow:2px 3px 0 var(--slice)}
.skin-noir .clue{background:#e9e1cf;color:#2a221a}.skin-noir .clue .cdesc{color:#6d5f4c}
.clue.r0{transform:rotate(-1.4deg);margin-right:14px}.clue.r1{transform:rotate(1deg);margin-left:12px}.clue.r2{transform:rotate(-.5deg);margin-right:6px}
.clue.new{outline:2px solid var(--accent);animation:pulse 1.6s ease-in-out 3}
.cname{font-weight:700}
.person{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px dashed var(--line)}
.person:last-child{border-bottom:0}
.person.new .avatar{box-shadow:0 0 0 2px var(--accent),0 0 10px var(--accent)}
.avatar{width:30px;height:30px;flex:none;display:grid;place-items:center;font-family:var(--title);font-weight:700;background:linear-gradient(145deg,var(--accent2),var(--slice));color:#fff;clip-path:polygon(6px 0,100% 0,100% calc(100% - 6px),calc(100% - 6px) 100%,0 100%,0 6px)}
.pinfo{flex:1;min-width:0}.pname{font-weight:700}
.rel small{font-size:10px;color:var(--muted);margin-right:3px}
.rel,.relval{font-family:var(--mono);font-size:12px;color:var(--accent2);display:flex;gap:4px;align-items:baseline}
.relrow{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px dashed var(--line)}
.quest{display:flex;gap:7px;padding:3px 0}
.quest .box{font-size:15px;line-height:1.2;color:var(--accent2)}
.quest.done span:last-child{text-decoration:line-through;color:var(--muted)}
.quest.changed{background:linear-gradient(90deg,var(--chip),transparent);box-shadow:inset 3px 0 0 var(--accent);padding-left:6px;animation:slide .45s ease-out}
.kv{display:flex;justify-content:space-between;gap:10px;padding:2px 0;border-bottom:1px dashed var(--line)}
.kv span:first-child{color:var(--muted)}
.folds{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.fold{border:1px solid var(--line);background:var(--chip)}
.fold[open]{flex-basis:100%;padding:0 8px 6px}
.fold summary{list-style:none;cursor:pointer;font-size:12px;padding:3px 9px;color:var(--muted)}
.fold summary::-webkit-details-marker{display:none}
.fold summary::before{content:"▸ ";color:var(--accent2)}
.fold[open] summary::before{content:"▾ "}
.choices{margin-top:4px}
.choices-head{font-size:11px;letter-spacing:2px;color:var(--muted);margin-bottom:5px}
.opt{display:flex;align-items:flex-start;gap:9px;width:100%;text-align:left;font:inherit;color:var(--ink);background:linear-gradient(160deg,var(--card1),var(--card2));border:1px solid var(--line);padding:9px 11px;cursor:pointer;clip-path:polygon(0 0,calc(100% - 10px) 0,100% 10px,100% 100%,8px 100%,0 calc(100% - 8px));transition:transform .08s,border-color .15s;-webkit-tap-highlight-color:transparent;touch-action:manipulation}
.optw{margin-bottom:8px;filter:drop-shadow(0 4px 0 var(--slice));transition:filter .08s}
.optw.pressed{filter:drop-shadow(0 1px 0 var(--slice))}
.opt:active,.optw.pressed .opt{transform:translateY(3px);border-color:var(--accent)}
.optw.chosen{filter:drop-shadow(0 1px 0 var(--slice)) drop-shadow(0 0 6px var(--accent))}
.optw.chosen .opt{transform:translateY(3px);border-color:var(--accent)}
.opt:disabled:not(.chosen){opacity:.4}
.letter{flex:none;width:20px;height:20px;display:grid;place-items:center;font-family:var(--mono);font-size:11px;font-weight:700;background:var(--accent);color:var(--card2);transform:rotate(-4deg)}
.otext{flex:1}.odesc{display:block;font-size:11px;color:var(--muted)}
.hint{font-size:11px;color:var(--muted);text-align:center;margin-top:2px}
</style></head>
<body class="skin-__SKIN__"><div class="wrap"><div class="slab"><div class="card">__BODY__</div></div></div>
<script>
(function(){
  var SOUND=__SOUND__,ctx=null;
  function blip(f,d){if(!SOUND)return;try{ctx=ctx||new(window.AudioContext||window.webkitAudioContext)();var o=ctx.createOscillator(),g=ctx.createGain();o.type="square";o.frequency.setValueAtTime(f,ctx.currentTime);o.frequency.exponentialRampToValueAtTime(f*1.6,ctx.currentTime+d);g.gain.setValueAtTime(.06,ctx.currentTime);g.gain.exponentialRampToValueAtTime(.0001,ctx.currentTime+d);o.connect(g);g.connect(ctx.destination);o.start();o.stop(ctx.currentTime+d)}catch(e){}}
  function resize(){try{parent.postMessage({type:"iframe:height",height:document.documentElement.scrollHeight},"*")}catch(e){}}
  var sent=false,btns=[].slice.call(document.querySelectorAll(".opt")),hint=document.getElementById("hint");
  btns.forEach(function(b){
    var w=b.parentNode;
    b.addEventListener("touchstart",function(){w.classList.add("pressed")},{passive:true});
    b.addEventListener("touchend",function(){w.classList.remove("pressed")});
    b.addEventListener("mousedown",function(){w.classList.add("pressed")});
    b.addEventListener("mouseup",function(){w.classList.remove("pressed")});
    b.addEventListener("click",function(){
      if(sent)return;sent=true;blip(520,.09);
      w.classList.add("chosen");btns.forEach(function(x){x.disabled=true});
      if(hint)hint.textContent="已发送，可能需要在弹窗里点确认";
      parent.postMessage({type:"input:prompt:submit",text:b.getAttribute("data-text")},"*");
    });
  });
  [].slice.call(document.querySelectorAll("details")).forEach(function(d){d.addEventListener("toggle",function(){blip(330,.05);setTimeout(resize,30)})});
  window.addEventListener("load",resize);
  if("ResizeObserver" in window){new ResizeObserver(resize).observe(document.body)}else{setTimeout(resize,60)}
  setTimeout(resize,400);
})();
</script></body></html>"""

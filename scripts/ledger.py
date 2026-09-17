#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger.py — 跨游戏战绩台账（聊天侧）

口径捞自老仓库的游戏厅：
  dice-duel/storage.py     局数 / 当前连胜 / 最长连胜
  dice-duel/achievements.py 递增阈值 + 幂等解锁
  dice-duel/dashboard.py    总账展示
那边是「掷骰一门」的账，这里铺成跨游戏的：一游戏一局记一条，统计全从账上推，
不另存派生数据（省得账和现实漂移）。

用法：
  python3 scripts/ledger.py add <游戏> <win|loss|draw> [--date D] [--opponent 阁下]
                                          [--moves N] [--level K] [--note 文本] [--source 路径]
  python3 scripts/ledger.py show [--game <游戏>]
  python3 scripts/ledger.py games
  python3 scripts/ledger.py json

账本：<数据根>/workspace/records/game-ledger/ledger.json（--ledger 可换路径，自测用）
"""

import argparse
import json
import os
import sys
import unicodedata
from datetime import date as _date

# ---------- 数据根 ----------
# 账本落 <根>/workspace/records/。根怎么定：$GAME_HOME ＞ 往上找带 temp/ 或
# workspace/ 的一层 ＞ 脚本上一级（与各引擎同一套口径）。
HERE = os.path.dirname(os.path.abspath(__file__))


def _data_root():
    env = os.environ.get("GAME_HOME")
    if env:
        return os.path.abspath(env)
    p = HERE
    while True:
        if os.path.isdir(os.path.join(p, "temp")) or os.path.isdir(os.path.join(p, "workspace")):
            return p
        up = os.path.dirname(p)
        if up == p:
            return os.path.dirname(HERE)
        p = up


RECORDS_DIR = os.path.join(_data_root(), "workspace", "records")
DEFAULT_LEDGER = os.path.join(RECORDS_DIR, "game-ledger", "ledger.json")
RESULT_CN = {"win": "胜", "loss": "负", "draw": "和"}

# ─── 成就：递增阈值，过了就不会退（照老仓库 achievements.py 的形）───
ACHIEVEMENTS = [
    ("first_win",  "初战告捷", lambda s: s["win"] >= 1),
    ("win_5",      "小有名气", lambda s: s["win"] >= 5),
    ("win_10",     "天界赌徒", lambda s: s["win"] >= 10),
    ("streak_3",   "势如破竹", lambda s: s["max_streak"] >= 3),
    ("streak_5",   "连胜之神", lambda s: s["max_streak"] >= 5),
    ("allrounder", "多面手",   lambda s: s["games"] >= 3),
    ("marathon",   "长局磨王", lambda s: s["max_moves"] >= 100),
]


def _w(s):
    """显示宽度：全角算 2 列（对齐用）"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in str(s))


def _pad(s, n, right=False):
    s = str(s)
    gap = max(0, n - _w(s))
    return (" " * gap + s) if right else (s + " " * gap)


def load(path):
    if not os.path.exists(path):
        return {"version": 1, "log": []}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    d.setdefault("version", 1)
    d.setdefault("log", [])
    return d


def save(d, path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")


def stats(log, game=None):
    """账上推统计。game=None 即全局。"""
    rows = [r for r in log if game is None or r["game"] == game]
    s = {"played": 0, "win": 0, "loss": 0, "draw": 0,
         "cur_streak": 0, "max_streak": 0, "max_moves": 0, "games": 0}
    streak = 0
    for r in rows:
        s["played"] += 1
        s[r["result"]] = s.get(r["result"], 0) + 1
        if r["result"] == "win":
            streak += 1
            s["max_streak"] = max(s["max_streak"], streak)
        else:
            streak = 0
        s["max_moves"] = max(s["max_moves"], int(r.get("moves") or 0))
    s["cur_streak"] = streak
    s["games"] = len({r["game"] for r in log})
    return s


def unlocked(s):
    return {aid for aid, _label, ok in ACHIEVEMENTS if ok(s)}


def fmt_line(name, s, total=None):
    rate = f"{s['win'] / s['played'] * 100:.0f}%" if s["played"] else "—"
    tail = ""
    if total:
        tail = f"   最长局 {s['max_moves']} 手"
    bits = f"{s['win']}胜 {s['loss']}负 {s['draw']}和"
    return (f"{_pad(name, 10)}{s['played']:>4} 局  "
            f"{_pad(bits, 18)}"
            f"胜率 {_pad(rate, 5)} 当前连胜 {s['cur_streak']}  最长 {s['max_streak']}{tail}")


def cmd_add(a):
    d = load(a.ledger)
    row = {
        "date": a.date or _date.today().isoformat(),
        "game": a.game,
        "result": a.result,
        "opponent": a.opponent,
    }
    for k in ("moves", "level", "note", "source"):
        v = getattr(a, k)
        if v not in (None, ""):
            row[k] = v
    before = unlocked(stats(d["log"]))
    d["log"].append(row)
    after = unlocked(stats(d["log"]))
    save(d, a.ledger)

    extra = "  ".join(f"{k}={v}" for k, v in row.items() if k not in ("date", "game", "result", "opponent"))
    print(f"记上了：{row['date']}  {row['game']}  {RESULT_CN[a.result]}  vs {row['opponent']}"
          + (f"  （{extra}）" if extra else ""))
    new = [lab for aid, lab, _ in ACHIEVEMENTS if aid in (after - before)]
    for lab in new:
        print(f"  [成就解锁] {lab}")
    print(fmt_line("全局", stats(d["log"])))


def cmd_show(a):
    d = load(a.ledger)
    log = d["log"]
    if not log:
        print("台账还是空的。")
        return
    games = sorted({r["game"] for r in log})
    scope = [a.game] if a.game else games
    print(f"天界游戏厅 · 战绩台账 —— {len(log)} 局")
    print()
    for g in scope:
        print(fmt_line(g, stats(log, g), total=True))
    if not a.game and len(games) > 1:
        print()
        print(fmt_line("合计", stats(log)))
    print()
    got = unlocked(stats(log))
    print(f"成就 · {len(got)}/{len(ACHIEVEMENTS)}")
    for aid, label, _ in ACHIEVEMENTS:
        print(f"  {'✅' if aid in got else '·'} {label}")
    print()
    print("最近：")
    for r in log[-5:]:
        bits = [r["date"], r["game"], RESULT_CN[r["result"]], "vs " + r.get("opponent", "-")]
        if r.get("moves"):
            bits.append(f"{r['moves']} 手")
        if r.get("level"):
            bits.append(f"档 {r['level']}")
        line = "  " + "  ".join(str(b) for b in bits)
        if r.get("note"):
            line += f"   {r['note']}"
        print(line)


def cmd_games(a):
    d = load(a.ledger)
    for g in sorted({r["game"] for r in d["log"]}):
        print(f"{g}（{stats(d['log'], g)['played']} 局）")


def main():
    p = argparse.ArgumentParser(prog="ledger.py", description="跨游戏战绩台账")
    p.add_argument("--ledger", default=DEFAULT_LEDGER, help="账本路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    ad = sub.add_parser("add", help="记一局")
    ad.add_argument("game")
    ad.add_argument("result", choices=list(RESULT_CN))
    ad.add_argument("--date", default=None)
    ad.add_argument("--opponent", default="阁下")
    ad.add_argument("--moves", type=int, default=None)
    ad.add_argument("--level", default=None)
    ad.add_argument("--note", default=None)
    ad.add_argument("--source", default=None)
    ad.set_defaults(fn=cmd_add)

    sh = sub.add_parser("show", help="看账")
    sh.add_argument("--game", default=None)
    sh.set_defaults(fn=cmd_show)

    gs = sub.add_parser("games", help="记过账的游戏")
    gs.set_defaults(fn=cmd_games)

    jn = sub.add_parser("json", help="原始账本")
    jn.set_defaults(fn=lambda a: print(json.dumps(load(a.ledger), ensure_ascii=False, indent=1)))

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

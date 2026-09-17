#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""game —— 群聊小游戏裁判合集 · 统一入口（chat-game-referee skill）

本入口只做分发，不改任何引擎的行为——子命令后面直接跟引擎的原参数：

  minesweeper（别名 mine）   扫雷 · 藏答案向   → scripts/minesweeper.py
      new / open / flag / show / hint / check / commit / reveal / close
  gomoku                     五子棋 · 对擂向   → scripts/gomoku.py
      new / play / show / undo / resign / draw / level / moves / demo / close
  liars-dice（别名 dice）    大话骰 ⛔维护中   → scripts/liars-dice.py
  liars-deck（别名 deck）    骗子牌 ⛔维护中   → scripts/liars-deck.py
  dice-duel（别名 duel）     掷骰决斗 · 对擂向 → scripts/dice-duel.py
      new / roll / show / check / reveal / close
  ledger                     跨游戏战绩台账    → scripts/ledger.py
      add / show / games / json
  spy                        谁是卧底 · 身份分配 → ../spy-game/scripts/spy.py
  turtle                     海龟汤 · 对局档案   → ../sea-turtle-soup/scripts/turtle.py
  list                       列子命令

例子：
  game minesweeper new C3 --preset quick
  game mine open D2
  game gomoku play H8
  game ledger show
  game duel roll
  game spy --list "1【甲】2【乙】3【丙】"
  game turtle new "手指所向"

数据根：状态落 <根>/temp/、归档落 <根>/workspace/records/，由引擎自己按
$GAME_HOME ＞ 往上找带 temp/ 或 workspace/ 的一层 ＞ 脚本上一级 解析。
解释器：优先 $GAME_PYTHON；没设就找数据根下的 .venv（出图要 PIL）；都没有则用当前 python。

旧命令照旧可用（`python3 scripts/<游戏>.py …`；兄弟 skill 的 `python3 ../spy-game/scripts/spy.py …`、`python3 ../sea-turtle-soup/scripts/turtle.py …`）——
本入口不做参数翻译、不吞输出，引擎打印什么就转发什么。
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 子命令 → 引擎脚本（同目录）
SUBS = {
    "minesweeper": "minesweeper.py",
    "mine": "minesweeper.py",
    "gomoku": "gomoku.py",
    "liars-dice": "liars-dice.py",
    "dice": "liars-dice.py",
    "liars-deck": "liars-deck.py",
    "deck": "liars-deck.py",
    "dice-duel": "dice-duel.py",
    "duel": "dice-duel.py",
    "ledger": "ledger.py",
}

# 子命令 → 兄弟 skill 里的脚本（相对同级 skills 目录 / 合仓时的仓库根）
SIBLINGS = {
    "spy": ("spy-game", "scripts/spy.py"),
    "turtle": ("sea-turtle-soup", "scripts/turtle.py"),
}

ALIAS_OF = {"mine": "minesweeper", "dice": "liars-dice", "deck": "liars-deck", "duel": "dice-duel"}

# 手写参数解析的引擎不认 -h（本地会当默认动作跑出去）——这两种的用法由本入口代打。
HANDROLLED = {"minesweeper.py", "gomoku.py"}


def data_root() -> str:
    """和引擎同一套口径：GAME_HOME ＞ 往上找带 temp/ 或 workspace/ 的一层 ＞ 脚本上一级。"""
    env = os.environ.get("GAME_HOME")
    if env:
        return os.path.abspath(env)
    p = HERE
    while True:
        if (p / "temp").is_dir() or (p / "workspace").is_dir():
            return str(p)
        if p.parent == p:
            return str(HERE.parent)
        p = p.parent


def pick_python() -> str:
    env = os.environ.get("GAME_PYTHON")
    if env:
        return env
    cand = Path(data_root()) / ".venv" / "bin" / "python3"
    return str(cand) if cand.exists() else sys.executable


def resolve(sub: str):
    """返回 (脚本路径, 说明)；找不到则 (None, 说明)。"""
    if sub in SUBS:
        return HERE / SUBS[sub], SUBS[sub]
    if sub in SIBLINGS:
        skill, rel = SIBLINGS[sub]
        # HERE.parents[1] = 同级目录：本地是 skills/，合仓时是仓库根
        return HERE.parents[1] / skill / rel, f"{skill}/{rel}"
    return None, ""


def usage() -> str:
    return (__doc__ or "").strip()


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0

    sub = argv[0]
    if sub == "list":
        print("子命令：")
        for k, v in SUBS.items():
            print(f"  {k:<12} → scripts/{v}")
        for k, (skill, rel) in SIBLINGS.items():
            print(f"  {k:<12} → {skill}/{rel}")
        return 0

    script, label = resolve(sub)
    if script is None:
        print(f"未知子命令: {sub}\n可用: {' / '.join(list(SUBS) + list(SIBLINGS))}（-h 看用法）",
              file=sys.stderr)
        return 2
    if not Path(script).exists():
        print(f"找不到子命令脚本: {script}", file=sys.stderr)
        if sub in SIBLINGS:
            print(f"（{sub} 在兄弟 skill 里：{label}——单跑本 skill 时没有它）", file=sys.stderr)
        return 1

    if any(a in ("-h", "--help") for a in argv[1:]) and script.name in HANDROLLED:
        import ast
        try:
            with open(script, encoding="utf-8") as f:
                doc = ast.get_docstring(ast.parse(f.read()))
        except Exception:
            doc = None
        print(doc.strip() if doc else f"{sub}: {script}")
        return 0

    return subprocess.call([pick_python(), str(script)] + argv[1:])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:          # 读者会 `| head` / `| less`——别甩 Traceback
        try:
            sys.stdout.close()
        except Exception:
            pass
        os._exit(0)

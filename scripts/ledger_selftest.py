#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger_selftest.py — ledger.py 自测（临时账本，不碰真账）

跑法：python3 scripts/ledger_selftest.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "ledger.py")

FAIL = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


def run(tmp, *args):
    r = subprocess.run([sys.executable, LEDGER, "--ledger", tmp, *args],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main():
    d = tempfile.mkdtemp(prefix="ledger-selftest-")
    tmp = os.path.join(d, "ledger.json")
    try:
        rc, out = run(tmp, "show")
        check("空账 show 不炸", rc == 0 and "空的" in out, out.strip())

        rc, out = run(tmp, "add", "gomoku", "draw", "--moves", "70", "--level", "2",
                      "--date", "2026-09-13", "--note", "9×9 死盘")
        check("add 落盘", rc == 0 and os.path.exists(tmp), out.strip())

        rc, out = run(tmp, "add", "gomoku", "win", "--moves", "40")
        check("首胜解锁成就", "[成就解锁] 初战告捷" in out, out)
        rc, out = run(tmp, "add", "gomoku", "win", "--moves", "42")
        check("重复 add 不重复报成就", "[成就解锁]" not in out, out)

        rc, out = run(tmp, "add", "gomoku", "win", "--moves", "33")
        check("连胜 3 解锁势力如破竹", "[成就解锁] 势如破竹" in out, out)

        rc, out = run(tmp, "show")
        check("show 全局统计对得上（4 局 3胜 0负 1和）", "4 局" in out and "3胜 0负 1和" in out, out)
        check("show 最长连胜 3", "最长 3" in out, out)

        rc, out = run(tmp, "add", "blackjack", "win")
        rc, out = run(tmp, "show")
        check("分游戏行都在", "gomoku" in out and "blackjack" in out, out)

        rc, out = run(tmp, "add", "gomoku", "loss")
        check("败局断连胜（当前连胜回 0）", "当前连胜 0" in out, out)

        rc, out = run(tmp, "games")
        check("games 列得出来", "gomoku" in out and "blackjack" in out, out)

        rc, out = run(tmp, "add", "rps", "win")
        check("第三个游戏解锁多面手", "[成就解锁] 多面手" in out, out)

        rc, out = run(tmp, "add", "gomoku", "win", "--moves", "120", "--note", "百手磨王局")
        check("百手局解锁长局磨王", "[成就解锁] 长局磨王" in out, out)

        rc, out = run(tmp, "json")
        check("json 输出可解析", rc == 0 and '"log"' in out, out[:200])

        rc2 = subprocess.run([sys.executable, "-c",
                              "import json,sys; json.load(open(sys.argv[1]))", tmp]).returncode
        check("账本文件是合法 JSON", rc2 == 0)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print()
    if FAIL:
        print(f"{len(FAIL)} 项 FAIL：{FAIL}")
        return 1
    print("全绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

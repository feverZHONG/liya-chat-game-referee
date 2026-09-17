#!/usr/bin/env python3
"""盘面类游戏 CLI 上桌前的自测（回归）。全绿才上桌；有 FAIL 就非零退出。

用法:
  python3 scripts/minesweeper_selftest.py                  # 自测同目录的 minesweeper.py
  python3 scripts/minesweeper_selftest.py /path/to/xxx.py  # 自测别的盘面 CLI
  python3 scripts/minesweeper_selftest.py --keep           # 保留临时目录看现场

覆盖（按真实对局顺序，临时盘、不碰正式 temp/）:
  new → check → commit → reveal（哈希独立复算）→ flag → CLEAR 全清
  → 新盘 BOOM 踩雷 → 踩雷后 show / 再报一手（不许崩）
  → 重复开同一格、开插旗格（不动作但 exit 0、无 Traceback）
  → 预设盘型 / 列数守卫 / hint 口子 / 踩雷自动摊牌

为什么要有这个：
  盘面 CLI 的必崩点都不在主流程上，而在「终局态」和「新建 vs 加载」的类型分歧上——
  手工点两下根本碰不到，上桌才炸。改完 CLI 先跑这个，30 秒的事。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

SELF = os.path.dirname(os.path.abspath(__file__))


def name(rc):
    return chr(ord("A") + rc[1]) + str(rc[0] + 1)


def load(state):
    with open(state, encoding="utf-8") as f:
        return json.load(f)


class Harness:
    def __init__(self, cli):
        self.work = tempfile.mkdtemp(prefix="board-selftest-")
        self.cli = os.path.join(self.work, "cli.py")
        shutil.copy(cli, self.cli)
        # 引擎会 shell out 给**同级**的兄弟脚本（ledger.py 等）——一起拷过去，
        # 否则副本在临时目录里找不到它们，收尾的台账那笔就记不上。
        src = os.path.dirname(os.path.abspath(cli))
        for f in os.listdir(src):
            if f.endswith(".py") and not f.endswith("_selftest.py"):
                shutil.copy(os.path.join(src, f), os.path.join(self.work, f))
        self.results = []

    def run(self, *args, state=None):
        cmd = [sys.executable, self.cli, *args]
        if state:
            cmd += ["--state", state]
        p = subprocess.run(cmd, capture_output=True, text=True)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def check(self, title, ok, detail=""):
        self.results.append((title, bool(ok)))
        print(("PASS " if ok else "FAIL ") + title
              + (f"  [{detail}]" if detail else ""))


def new_board(h, state, first="D2"):
    """建盘 + 确认落盘可解析。返回盘面 dict 或 None。"""
    rc, out = h.run("new", first, state=state)
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    h.check("new <首点> 跑通（新建路径）", rc == 0 and "Traceback" not in out, tail)
    if not os.path.exists(state):
        h.check("新盘已落盘", False, state)
        return None
    try:
        d = load(state)
    except Exception as e:  # noqa: BLE001
        h.check("新盘 JSON 可解析", False, repr(e))
        return None
    h.check("新盘 JSON 可解析", True)
    line = next((l for l in out.splitlines() if "未翻" in l), "")
    h.check("盘面自带统计行", "未翻" in out and "剩余空格" in out, line)
    return d


def play_to_clear(h, state):
    """把安全格全开一遍。每手重新读盘——一次 flood 会顺手开掉后面好几格，
    拿着开局那份 stale 的 opened 往下跑，最后几手会变成「本来就开着」的空转，
    收尾那手就不是真正开完盘的那手，CLEAR 也就测不到了（曾是恒定 FAIL）。"""
    d = load(state)
    W, H = d.get("W", 10), d.get("H", 10)
    mines = {tuple(m) for m in d["mines"]}
    out = ""
    for r in range(H):
        for c in range(W):
            if (r, c) in mines:
                continue
            d = load(state)
            if (r, c) in {tuple(x) for x in d["opened"]}:
                continue
            code, out = h.run("open", name((r, c)), state=state)
            if code != 0 or "Traceback" in out:
                h.check("安全格全开不报错", False, f"{name((r, c))} rc={code}")
                return
    h.check("CLEAR 全清", "CLEAR" in out or "全清" in out)
    d = load(state)
    left = W * H - len({tuple(x) for x in d["opened"]})
    h.check("全清后未开格 == 雷数", left == len(mines), f"未开 {left} / 雷 {len(mines)}")


def main():
    argv = [a for a in sys.argv[1:] if a != "--keep"]
    keep = "--keep" in sys.argv
    cli = argv[0] if argv else os.path.join(SELF, "minesweeper.py")
    if not os.path.exists(cli):
        sys.exit(f"找不到 CLI: {cli}")

    h = Harness(cli)
    st = os.path.join(h.work, "board.json")
    st2 = os.path.join(h.work, "board2.json")

    # ---- 第一盘：走完自证 + 全清 ----
    d = new_board(h, st, "D2")
    if d:
        rc, out = h.run("check", state=st)
        h.check("check 自洽（0 处对不上）", rc == 0 and "0 处" in out,
                out.strip().replace("\n", " | "))

        rc, out = h.run("commit", state=st)
        cpath = (st[:-5] if st.endswith(".json") else st) + ".commit.json"
        alt = st + ".commit.json"
        found = cpath if os.path.exists(cpath) else (alt if os.path.exists(alt) else None)
        h.check("commit 落盘名与文档一致", rc == 0 and found == cpath, f"实际={found}")

        if found:
            cm = load(found)
            h.check("commit 含 salt/mines/hash",
                    all(k in cm for k in ("salt", "mines", "hash")))
            rc, out = h.run("reveal", state=st)
            h.check("reveal 可达且带出同一 hash", rc == 0 and cm["hash"] in out)
            h.check("reveal 自带独立复算（一致）", rc == 0 and "一致" in out and "✗" not in out)
            recomputed = hashlib.sha256(
                (cm["salt"] + "|" + json.dumps(sorted(cm["mines"]))).encode()
            ).hexdigest()
            h.check("哈希独立复算一致", recomputed == cm["hash"], recomputed[:16])

        mine0 = tuple(sorted(tuple(m) for m in d["mines"])[0])
        rc, out = h.run("flag", name(mine0), state=st)
        h.check("flag 成功", rc == 0)
        rc, out = h.run("open", name(mine0), state=st)
        h.check("开插旗格：不动作但 exit 0", rc == 0 and "Traceback" not in out)

        play_to_clear(h, st)

        rc, out = h.run("open", name(tuple(d["opened"][0]) if d["opened"] else (0, 0)), state=st)
        h.check("重复开已开格：不动作但 exit 0", rc == 0 and "Traceback" not in out)

    # ---- 第二盘：踩雷 + 终局态可用 ----
    d2 = new_board(h, st2, "E5")
    if d2:
        m = tuple(sorted(tuple(x) for x in d2["mines"])[0])
        rc, out = h.run("open", name(m), state=st2)
        h.check("踩雷画 💥 + BOOM", rc == 0 and "BOOM" in out and "💥" in out)
        rc, out = h.run("show", state=st2)
        h.check("踩雷后 show 不崩", rc == 0 and "Traceback" not in out)
        rc, out = h.run("open", "A1", state=st2)
        h.check("踩雷后还能报坐标不崩", rc == 0 and "Traceback" not in out)

    # ---- 第三盘：预设 / 尺寸守卫 / 口子 / 踩雷自动摊牌 ----
    st3 = os.path.join(h.work, "board3.json")
    rc, out = h.run("new", "D2", "--preset", "quick", state=st3)
    d3 = load(st3) if os.path.exists(st3) else {}
    h.check("--preset quick 落成 6x6/5 雷",
            rc == 0 and d3.get("W") == 6 and d3.get("H") == 6
            and len(d3.get("mines", [])) == 5,
            f"{d3.get('W')}x{d3.get('H')}/{len(d3.get('mines', []))}")
    h.check("开局行带密度", "%" in out)
    rc, out = h.run("new", "D2", "--w", "30", "--h", "16", state=st3)
    h.check("列数 >26 被挡下（非零退出）", rc != 0 and "26" in out, out.strip()[:60])

    rc, out = h.run("hint", state=st3)
    h.check("hint 出口子且不崩", rc == 0 and "口子" in out and "Traceback" not in out,
            out.strip().replace("\n", " / ")[:80])
    png = os.path.join(h.work, "explicit.png")
    rc, out = h.run("show", "--png", png, state=st3)
    h.check("--png <路径> 落图（有效 PNG）",
            rc == 0 and os.path.exists(png)
            and open(png, "rb").read(8) == b"\x89PNG\r\n\x1a\n")
    rc, out = h.run("show", "--png", state=st3)  # 路径可省
    dflt = os.path.join(h.work, "board3.png")
    h.check("--png 省路径 → 默认 <state同名>.png", rc == 0 and os.path.exists(dflt))
    h.check("文字与图同时出（文字没被图顶掉）", "未翻" in out and "图已生成" in out)
    h.run("commit", state=st3)
    m3 = tuple(sorted(tuple(x) for x in load(st3)["mines"])[0])
    rc, out = h.run("open", name(m3), state=st3)
    h.check("踩雷自动摊牌（带独立复算）",
            rc == 0 and "BOOM" in out and "独立复算" in out)

    # ---- 第四盘：踩雷 + 收桌一条龙（存档 / INDEX / 台账 / 防呆）----
    arch = os.path.join(h.work, "arch")
    led = os.path.join(h.work, "led.json")
    if d:  # 第一盘已 CLEAR，且 commit 过
        rc, out = h.run("close", "--archive", arch, "--ledger", led, state=st)
        h.check("全清盘 close 跑通", rc == 0, out.strip()[-160:])
        mds = [f for f in os.listdir(arch) if f.endswith(".md") and f != "INDEX.md"]
        h.check("close 落 json + md + INDEX + 承诺",
                len(mds) == 1 and len([f for f in os.listdir(arch) if f.endswith(".json")]) == 2
                and os.path.exists(os.path.join(arch, "INDEX.md")),
                str(sorted(os.listdir(arch))))
        body = open(os.path.join(arch, mds[0]), encoding="utf-8").read()
        h.check("存档 md 含终局盘面 + 自洽校验 + 承诺复算",
                "全清" in body and "不自洽 0 处" in body and "一致 ✓" in body, body[:200])
        idx = open(os.path.join(arch, "INDEX.md"), encoding="utf-8").read()
        h.check("INDEX 自动重建、含这一档", mds[0] in idx and "全清" in idx, idx[:200])
        lj = json.load(open(led, encoding="utf-8"))
        h.check("台账记了一笔（扫雷 · 负 = 阁下全清）",
                len(lj["log"]) == 1 and lj["log"][-1]["game"] == "扫雷"
                and lj["log"][-1]["result"] == "loss", str(lj))
        rc, out = h.run("close", "--archive", arch, "--ledger", led, state=st)
        h.check("close 幂等（重收被挡、不多开一档）",
                rc != 0 and "已经收过" in out
                and len([f for f in os.listdir(arch) if f.endswith(".md")
                         and f != "INDEX.md"]) == 1, out)

    for i in (1, 2):  # 同一天、同盘型、同结果连开两盘 → 第二盘 slug 加尾号
        stx = os.path.join(h.work, f"boom{i}.json")
        h.run("new", "D2", "--preset", "quick", state=stx)
        h.run("commit", state=stx)
        mx = tuple(sorted(tuple(x) for x in load(stx)["mines"])[0])
        h.run("open", name(mx), state=stx)
        rc, out = h.run("close", "--archive", arch, "--ledger", led, state=stx)
        h.check(f"踩雷盘 close 跑通（第 {i} 盘）", rc == 0, out.strip()[-120:])
    mds2 = sorted(f for f in os.listdir(arch) if f.endswith(".md") and f != "INDEX.md")
    h.check("同日同结果两盘 slug 加尾号、不互相盖",
            len(mds2) == 3 and any(f.endswith("-2.md") for f in mds2), str(mds2))
    lj = json.load(open(led, encoding="utf-8"))
    h.check("台账共三笔（1 负 + 2 胜）",
            len(lj["log"]) == 3 and [r["result"] for r in lj["log"]] == ["loss", "win", "win"],
            str([r["result"] for r in lj["log"]]))

    stx = os.path.join(h.work, "open1.json")
    h.run("new", "D2", "--preset", "quick", state=stx)
    rc, out = h.run("close", "--archive", arch, "--ledger", led, state=stx)
    h.check("没收桌时 close 被挡", rc != 0 and "还没收桌" in out, out.strip()[:60])

    failed = [t for t, ok in h.results if not ok]
    print("\n" + ("全绿 ✓" if not failed else f"FAIL {len(failed)}/{len(h.results)}: " + ", ".join(failed)))
    print("临时盘:", h.work)
    if not keep:
        shutil.rmtree(h.work, ignore_errors=True)
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()

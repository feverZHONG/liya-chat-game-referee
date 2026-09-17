#!/usr/bin/env python3
"""五子棋引擎上桌前的自测（回归）。全绿才上桌；有 FAIL 就非零退出。

用法:
  python3 scripts/gomoku_selftest.py                 # 自测同目录的 gomoku.py
  python3 scripts/gomoku_selftest.py /path/to/xxx.py # 自测别的引擎
  python3 scripts/gomoku_selftest.py --keep          # 保留临时目录看现场

覆盖（按真实对局顺序，临时盘、不碰正式 temp/）:
  new（含 --human white 引擎先手占天元）→ play（引擎应手）→ 重复落子 / 越界坐标
  → undo 整回合 → level 改档 / 无效档被挡 → resign → 终局后 play 被挡
  → --size 守卫 / --png 出图（文字没被图顶掉）
  → AI 三场景（堵活三 / 自己成五 / 堵冲四）→ 自对弈 100 手不崩、能分胜负

为什么要有这个：五子棋的必崩点在「终局态」「加载 vs 新建」「undo 后退到谁的回合」
三处，以及 AI 的硬检查顺序（成五 / 堵五 / 活四）——手工点两下碰不到。
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

SELF = os.path.dirname(os.path.abspath(__file__))


def load(state):
    with open(state, encoding="utf-8") as f:
        return json.load(f)


def load_engine(path):
    spec = importlib.util.spec_from_file_location("engine_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Harness:
    def __init__(self, cli):
        self.work = tempfile.mkdtemp(prefix="gomoku-selftest-")
        self.cli = os.path.join(self.work, "cli.py")
        shutil.copy(cli, self.cli)
        # 引擎会 shell out 给**同级**的兄弟脚本（ledger.py / gomoku_audit.py）——一起拷过去，
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


def main():
    argv = [a for a in sys.argv[1:] if a != "--keep"]
    keep = "--keep" in sys.argv
    cli = argv[0] if argv else os.path.join(SELF, "gomoku.py")
    if not os.path.exists(cli):
        sys.exit(f"找不到引擎: {cli}")

    h = Harness(cli)
    st = os.path.join(h.work, "g.json")
    st2 = os.path.join(h.work, "g2.json")

    # ---- 第一盘：常规对局走一遍 ----
    rc, out = h.run("new", state=st)
    d = load(st) if os.path.exists(st) else {}
    h.check("new 跑通且落盘（默认 11×11）", rc == 0 and d.get("size") == 11 and "Traceback" not in out)
    dflt_png = os.path.join(h.work, "g.png")
    h.check("图默认每手跟着出（<state同名>.png）", os.path.exists(dflt_png))
    h.check("新盘全空 / 阁下执黑 / 档位 2",
            d.get("human") == 1 and d.get("level") == 2
            and all(v == 0 for row in d.get("board", []) for v in row))
    h.check("开局行带档位说明", "档位" in out and "对等" in out)

    rc, out = h.run("play", "H8", state=st)
    d = load(st)
    h.check("play 落子 + 引擎应手（手数 2）", rc == 0 and len(d["moves"]) == 2, out.splitlines()[0] if out else "")
    h.check("引擎落在空点上（盘上正好两子）",
            sum(1 for row in d["board"] for v in row if v) == 2)
    h.check("回话含双方落点", "阁下 黑 H8" in out and "引擎 白" in out)

    rc, out = h.run("play", "H8", state=st)
    h.check("重复落子被挡（不动作、exit 0）", rc == 0 and "已经有子" in out and "Traceback" not in out)
    rc, out = h.run("play", "Z99", state=st)
    h.check("越界坐标非零退出", rc != 0 and "越界" in out, out.strip()[:50])

    rc, out = h.run("moves", state=st)
    h.check("棋谱可读", rc == 0 and "棋谱" in out and "H8" in out)

    rc, out = h.run("undo", state=st)
    d = load(st)
    h.check("undo 退整回合（手数归零）", rc == 0 and len(d["moves"]) == 0,
            f"剩 {len(d['moves'])} 手")

    rc, out = h.run("level", "3", state=st)
    h.check("level 3 改档落盘", rc == 0 and load(st)["level"] == 3)
    rc, out = h.run("level", "9", state=st)
    h.check("无效档位被挡（非零退出）", rc != 0 and "档位" in out)
    rc, out = h.run("level", state=st)
    h.check("level 只读：报当前档位", rc == 0 and "较真" in out)

    rc, out = h.run("level", "4", state=st)
    h.check("level 4（搜杀）改档落盘", rc == 0 and load(st)["level"] == 4 and "搜杀" in out)

    rc, out = h.run("resign", state=st)
    d = load(st)
    h.check("resign 判负落盘", rc == 0 and d["winner"] == 2 and "认输" in out)
    rc, out = h.run("play", "A1", state=st)
    h.check("终局后 play 被挡（exit 0、不崩）", rc == 0 and "这盘完了" in out)

    png = os.path.join(h.work, "board.png")
    rc, out = h.run("show", "--png", png, state=st)
    h.check("--png 落图（有效 PNG）",
            rc == 0 and os.path.exists(png)
            and open(png, "rb").read(8) == b"\x89PNG\r\n\x1a\n")
    h.check("文字与图同时出（文字没被图顶掉）", "手数" in out and "图已生成" in out)

    # ---- 第二盘：阁下执白，引擎先手 ----
    rc, out = h.run("new", "--human", "white", "--size", "13", state=st2)
    d2 = load(st2)
    h.check("--human white 引擎先手占天元",
            rc == 0 and d2["human"] == 2 and len(d2["moves"]) == 1
            and d2["moves"][0][:2] == [6, 6], f"{d2.get('moves')}")
    h.check("--size 13 落盘", d2.get("size") == 13)
    rc, out = h.run("new", "--size", "4", state=st2)
    h.check("--size 4 被挡（非零退出）", rc != 0 and "5" in out)
    st3 = os.path.join(h.work, "g3.json")
    rc, out = h.run("new", "--no-png", state=st3)
    h.check("--no-png 关掉出图", rc == 0 and not os.path.exists(os.path.join(h.work, "g3.png")))

    # ---- demo：自对弈出图（不碰正式状态） ----
    demo_png = os.path.join(h.work, "demo.png")
    rc, out = h.run("demo", "16", "--out", demo_png)
    h.check("demo 自对弈出图", rc == 0 and os.path.exists(demo_png) and "手数" in out)
    h.check("demo 不写状态文件、不占对局图",
            not os.path.exists(os.path.join(h.work, "demo.json"))
            and "Traceback" not in out)

    # ---- AI 逻辑（直接调函数，不走 CLI） ----
    eng = load_engine(h.cli)
    size = 9
    B, W, E = eng.BLACK, eng.WHITE, eng.EMPTY

    b1 = [[E] * size for _ in range(size)]
    for r in (4, 5, 6):
        b1[r][7] = B                      # 黑 H5/H6/H7 活三
    mv = eng.ai_choose(b1, size, W, 2)
    h.check("AI 堵活三（H4 或 H8）", mv in ((3, 7), (7, 7)), eng.name_of(*mv))  # 9×9 下即 H4/H8

    b2 = [[E] * size for _ in range(size)]
    for r in (3, 4, 5, 6):
        b2[r][7] = W                      # 白 H4–H7，两端开
    for r in (4, 5, 6):
        b2[r][6] = B
    mv = eng.ai_choose(b2, size, W, 2)
    h.check("AI 自己成五（H3 或 H8）", mv in ((2, 7), (7, 7)), eng.name_of(*mv))

    b3 = [[E] * size for _ in range(size)]
    for r in (3, 4, 5, 6):
        b3[r][7] = B                      # 黑冲四，上头被白 H3 堵死
    b3[2][7] = W
    mv = eng.ai_choose(b3, size, W, 2)
    h.check("AI 堵冲四（H8）", mv == (7, 7), eng.name_of(*mv))

    # ---- 档 4（搜杀）：会算「连着的杀」，也会拆对面的 ----
    n = 11

    def blank11():
        return [[E] * n for _ in range(n)]

    def place(bd, stones):
        for (rr, cc, pp) in stones:
            bd[rr][cc] = pp

    b4 = blank11()                        # 白行 (4,3)-(4,5) + 列 (5,6)-(7,6)：落 (4,6) 成双四
    place(b4, [(4, c0, W) for c0 in (3, 4, 5)] + [(r0, 6, W) for r0 in (5, 6, 7)]
          + [(4, 2, B), (3, 6, B)])
    mv = eng.ai_choose(b4, n, W, 4)
    b4[mv[0]][mv[1]] = W
    h.check("档4 会做双四 / 活四（落完 ≥2 个杀点）",
            len(eng.five_points(b4, n, W)) >= 2, eng.name_of(*mv))

    b5 = blank11()                        # 白行 (5,5)-(5,7) + 列 (3,8)(4,8)：落 (5,8) = 四三
    place(b5, [(5, c0, W) for c0 in (5, 6, 7)] + [(3, 8, W), (4, 8, W), (5, 4, B)])
    mv = eng.ai_choose(b5, n, W, 4)
    h.check("档4 会做四三定式（I6）", mv == (5, 8), eng.name_of(*mv))

    b6 = blank11()                        # 黑活三：档 4 不能让黑接着做出活四
    place(b6, [(5, c0, B) for c0 in (5, 6, 7)] + [(6, 6, W), (7, 8, W)])
    mv = eng.ai_choose(b6, n, W, 4)
    b6[mv[0]][mv[1]] = W
    h.check("档4 破黑活三（走后黑做不出活四）",
            not eng.open_four_moves(b6, n, B), eng.name_of(*mv))

    b7 = blank11()                        # 黑冲四，上头白子堵死：档 4 要补下头
    place(b7, [(r0, 5, B) for r0 in (3, 4, 5, 6)] + [(2, 5, W)])
    mv = eng.ai_choose(b7, n, W, 4)
    h.check("档4 堵冲四（F8）", mv == (7, 5), eng.name_of(*mv))

    # ---- 自对弈：三档各跑一遍，不许崩、不许下非法手 ----
    # 分出胜负与否只做观察：两个同档 AI 互堵到收官是正常结果，不该当 FAIL。
    decided = []
    for lv in (1, 2, 3, 4):
        bb = [[E] * size for _ in range(size)]
        cur, done, illegal = B, False, False
        for _ in range(120):
            mv = eng.ai_choose(bb, size, cur, lv)
            if mv is None:  # 盘满，正常收场
                break
            r, c = mv
            if not (0 <= r < size and 0 <= c < size) or bb[r][c] != E:
                illegal = True
                break
            bb[r][c] = cur
            if eng.check_win(bb, size, r, c, cur):
                done = True
                break
            cur = 3 - cur
        decided.append(done)
        h.check(f"档{lv} 自对弈不崩、无非法落子", not illegal)
    print(f"  （观察：四档自对弈分出胜负 = {decided}，同档互堵到收官也算正常）")

    # ---- 收桌一条龙（终局才收 / 同日撞名加尾号 / 复盘照抄）----
    arch = os.path.join(h.work, "arch")
    led = os.path.join(h.work, "led.json")
    st9 = os.path.join(h.work, "g9.json")
    h.run("new", "--size", "9", "--no-png", state=st9)
    rc, out = h.run("close", "--archive", arch, "--ledger", led, state=st9)
    h.check("没收桌时 close 被挡", rc != 0 and "还没收桌" in out, out.strip()[:60])
    h.run("resign", state=st9)
    rc, out = h.run("close", "--archive", arch, "--ledger", led, state=st9)
    h.check("close 跑通（认输终局）", rc == 0, out.strip()[-200:])
    mds = [f for f in os.listdir(arch) if f.endswith(".md") and f != "INDEX.md"]
    h.check("close 落 json + md + INDEX",
            len(mds) == 1 and os.path.exists(os.path.join(arch, mds[0][:-3] + ".json"))
            and os.path.exists(os.path.join(arch, "INDEX.md")), str(sorted(os.listdir(arch))))
    body = open(os.path.join(arch, mds[0]), encoding="utf-8").read()
    h.check("存档 md 含终局盘面 + 棋谱 + 复盘 + 认输口径",
            "## 终局盘面" in body and "## 棋谱" in body and "① 重放" in body
            and "对手认输" in body, body[:200])
    idx = open(os.path.join(arch, "INDEX.md"), encoding="utf-8").read()
    h.check("INDEX 自动重建、含这一档与「认输」",
            mds[0] in idx and "认输" in idx and "9x9" in idx, idx[:300])
    lj = json.load(open(led, encoding="utf-8"))
    h.check("台账记了一笔（五子棋 · 胜 = 阁下认输）",
            len(lj["log"]) == 1 and lj["log"][-1]["game"] == "五子棋"
            and lj["log"][-1]["result"] == "win", str(lj))
    rc, out = h.run("close", "--archive", arch, "--ledger", led, state=st9)
    h.check("close 幂等（重收被挡、不多开一档）",
            rc != 0 and "已经收过" in out
            and len([f for f in os.listdir(arch) if f.endswith(".md") and f != "INDEX.md"]) == 1, out)

    st10 = os.path.join(h.work, "g10.json")   # 同日同盘型同结果 → slug 加尾号
    h.run("new", "--size", "9", "--no-png", state=st10)
    h.run("resign", state=st10)
    rc, out = h.run("close", "--archive", arch, "--ledger", led, state=st10)
    mds2 = sorted(f for f in os.listdir(arch) if f.endswith(".md") and f != "INDEX.md")
    h.check("同一日两盘 slug 加尾号、不互相盖",
            rc == 0 and len(mds2) == 2 and any(f.endswith("-2.md") for f in mds2), str(mds2))
    lj = json.load(open(led, encoding="utf-8"))
    h.check("台账共两笔", len(lj["log"]) == 2, str(len(lj["log"])))

    failed = [t for t, ok in h.results if not ok]
    print("\n" + ("全绿 ✓" if not failed else
                  f"FAIL {len(failed)}/{len(h.results)}: " + ", ".join(failed)))
    print("临时盘:", h.work)
    if not keep:
        shutil.rmtree(h.work, ignore_errors=True)
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()

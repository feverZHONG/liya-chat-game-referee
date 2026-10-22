#!/usr/bin/env python3
"""五子棋引擎体检台：VCF 正确性 / 档位天梯 / 每手速度。改过 AI 就顺手跑一遍。

用法:
  python3 scripts/gomoku_bench.py verify [样本数]        # 合法局面里 VCF 报的杀招，穷举应手复核
  python3 scripts/gomoku_bench.py ladder [盘型] [局数]    # 档位两两对擂（随机开局）
  python3 scripts/gomoku_bench.py speed [盘型]           # 各档走满一局的每手耗时

跟 gomoku_selftest.py 分工：那份管「会不会崩 / 记不记得规矩」，这份管「棋力有没有变」。
复盘一局 / 盘型和棋率采样不在这——那是 `gomoku_audit.py` 的活。
"""
import importlib.util
import os
import random
import statistics
import sys
import time
from collections import Counter

SELF = os.path.dirname(os.path.abspath(__file__))


def load_engine(path=None):
    spec = importlib.util.spec_from_file_location("engine_under_test", path or os.path.join(SELF, "gomoku.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def blank(n):
    return [[G.EMPTY] * n for _ in range(n)]


# ---------- VCF 正确性 ----------
def vcf_verify(board, size, me, depth=6, cap=30):
    """跟着 vcf 的杀招走：白每走一手，黑都走最优防守，最后必须真成五。"""
    b = [row[:] for row in board]
    opp, mv = 3 - me, None
    for _ in range(cap):
        if mv is None:
            mv = G.vcf(b, size, me, depth)
        if not mv:
            return False
        if G.check_win(b, size, mv[0], mv[1], me):
            return True
        b[mv[0]][mv[1]] = me
        w = G.five_points(b, size, me)
        if G.five_points(b, size, opp):
            return False                      # 对面手里有现成的五，这条杀不成立
        if len(w) >= 2:
            return True                       # 活四 / 双四
        if len(w) != 1:
            return False
        b[w[0][0]][w[0][1]] = opp             # 黑只能堵
        mv = None
    return False


def random_legal(n, plies, rng):
    b = blank(n); cur = G.BLACK
    for _ in range(plies):
        cs = [p for p in G.candidates(b, n) if b[p[0]][p[1]] == G.EMPTY]
        if not cs:
            return None
        r, c = rng.choice(cs)
        b[r][c] = cur
        if G.check_win(b, n, r, c, cur):
            return None
        cur = 3 - cur
    return b


def cmd_verify(args):
    n_samples = int(args[0]) if args else 400
    rng = random.Random(2026)
    hits = ok = trivial = 0
    fails = []
    for _ in range(n_samples):
        n = rng.choice([9, 11, 13])
        b = random_legal(n, rng.randint(12, 40), rng)
        if b is None:
            continue
        who = rng.choice([G.BLACK, G.WHITE])
        mv = G.vcf(b, n, who, 6)
        if not mv:
            continue
        hits += 1
        if G.check_win(b, n, mv[0], mv[1], who):
            trivial += 1
        if vcf_verify(b, n, who, 6):
            ok += 1
        else:
            fails.append((n, G.name_of(*mv)))
    print(f"VCF 正确性：报出杀招 {hits} 次（其中一步成五 {trivial} 次），穷举应手全挡不住 {ok} 次 "
          f"-> {'PASS' if hits == ok else 'FAIL'}")
    if fails:
        print("  反例:", fails[:6])
    return hits == ok


# ---------- 天梯 ----------
def play(size, lvb, lvw, first):
    b = blank(size); b[first[0]][first[1]] = G.BLACK
    cur, lvs, plies = G.WHITE, {G.BLACK: lvb, G.WHITE: lvw}, 1
    while plies < size * size:
        mv = G.ai_choose(b, size, cur, lvs[cur])
        if not mv:
            return "和"
        r, c = mv
        if b[r][c] != G.EMPTY:
            return "非法"
        b[r][c] = cur; plies += 1
        if G.check_win(b, size, r, c, cur):
            return "黑" if cur == G.BLACK else "白"
        cur = 3 - cur
    return "和"


def cmd_ladder(args):
    size = int(args[0]) if args else 11
    games = int(args[1]) if len(args) > 1 else 12
    rng = random.Random(4242)
    print(f"档位天梯（{size}×{size}，随机开局，每组 {games} 局）")
    for a, b_ in ((3, 2), (4, 2), (4, 3), (4, 4)):
        cnt = Counter(); t0 = time.time()
        for _ in range(games):
            m = size // 2
            first = (rng.randrange(m - 2, m + 3), rng.randrange(m - 2, m + 3))
            cnt[play(size, a, b_, first)] += 1
        print(f"  档{a}(黑) vs 档{b_}(白): 黑胜 {cnt['黑']:>2} / 白胜 {cnt['白']:>2} "
              f"/ 和 {cnt['和']:>2} / 非法 {cnt['非法']}  （{time.time()-t0:.0f}s）")


def cmd_speed(args):
    size = int(args[0]) if args else 11
    for lv in (2, 3, 4):
        b = blank(size); cur = G.BLACK; times = []
        for _ in range(size * size):
            t0 = time.perf_counter()
            mv = G.ai_choose(b, size, cur, lv)
            times.append(time.perf_counter() - t0)
            if not mv:
                break
            b[mv[0]][mv[1]] = cur
            if G.check_win(b, size, mv[0], mv[1], cur):
                break
            cur = 3 - cur
        print(f"  档{lv}: 中位 {statistics.median(times)*1000:>6.1f}ms / "
              f"最大 {max(times)*1000:>7.1f}ms（{len(times)} 手）")


G = load_engine()
if __name__ == "__main__":
    act = sys.argv[1] if len(sys.argv) > 1 else "verify"
    rest = sys.argv[2:]
    if act == "verify":
        sys.exit(0 if cmd_verify(rest) else 1)
    if act == "ladder":
        cmd_ladder(rest)
    elif act == "speed":
        cmd_speed(rest)
    else:
        sys.exit(f"不认识的动作: {act}（verify / ladder / speed）")

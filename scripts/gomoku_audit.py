#!/usr/bin/env python3
"""五子棋复盘 / 盘型采样探针（chat-game-referee）。

收盘后要说「这盘谁的锅」「9×9 是不是烂棋盘」——盘上的事从盘上算，别用嘴回。
两个模式：

  python3 gomoku_audit.py <state.json>
      复盘一局：棋谱重放（合法性、与落盘盘面是否一致）→ 双方错失的成五 →
      每手逼出的「四」/活三 → 有没有出现过双威胁必胜手 → 死盘断言
      （剩余空格 ≤16 时 2^n 全填法暴力，再多就只报数不跑）。

  python3 gomoku_audit.py --sample --games 12 --sizes 9,11,13,15 --level 2
      盘型采样：随机首手自对弈，量各盘型的和棋率 / 盘满率。
      ——引擎是确定性函数，不随机首手的话 N 局就是 1 局。

引擎逻辑从同目录 gomoku.py 导入，别在这儿重写一套。只读不写——绝不碰状态文件。
"""
import argparse
import importlib.util
import json
import os
import random
import sys
from itertools import product

HERE = os.path.dirname(os.path.abspath(__file__))
EMPTY, BLACK, WHITE = 0, 1, 2
MAX_FILL_BRUTE = 16  # 空格 2^n 的上限：2^16 秒级能跑完，再大别暴力（会卡住回话）


def load_engine():
    path = os.path.join(HERE, "gomoku.py")
    if not os.path.exists(path):
        sys.exit(f"找不到引擎: {path}（复盘脚本跟引擎要放同一个 scripts/ 目录）")
    spec = importlib.util.spec_from_file_location("gomoku_engine", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = load_engine()
nm = G.name_of


def board_after(moves, size, k):
    b = [[EMPTY] * size for _ in range(size)]
    for (r, c, p) in moves[:k]:
        b[r][c] = p
    return b


def makes_five(board, size, r, c, p):
    """p 摆到 (r,c) 能否连五（临时改盘后还原）。"""
    board[r][c] = p
    ok = G.check_win(board, size, r, c, p)
    board[r][c] = EMPTY
    return ok


def win_points(board, size, p, cands):
    """该颜色这一手能连五的点（= 对手必须堵的点）。cands 用引擎的候选集（半径 2）。"""
    return [(r, c) for (r, c) in cands
            if board[r][c] == EMPTY and makes_five(board, size, r, c, p)]


def best_threats(board, size, p, cands):
    """p 下一手最多能一次做出几个致胜点。≥2 = 双威胁 = 必胜手。"""
    best = 0
    for (r, c) in cands:
        if board[r][c] != EMPTY:
            continue
        b = [row[:] for row in board]
        b[r][c] = p
        best = max(best, len(win_points(b, size, p, G.candidates(b, size))))
    return best


def audit(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    size, moves, board = d["size"], d["moves"], d["board"]
    human = d.get("human", BLACK)
    print(f"棋谱 {path}")
    print(f"{size}×{size}｜{len(moves)} 手｜胜者 {d.get('winner')}｜档 {d.get('level')}"
          f"｜阁下执 {'黑' if human == BLACK else '白'}")

    # ① 重放：顺序 + 重复落子 + 与落盘盘面是否一致
    rep = [[EMPTY] * size for _ in range(size)]
    bad = []
    for i, (r, c, p) in enumerate(moves):
        if p != (BLACK if i % 2 == 0 else WHITE):
            bad.append(f"第{i + 1}手顺序不对")
        if rep[r][c] != EMPTY:
            bad.append(f"第{i + 1}手 {nm(r, c)} 重复落子")
        rep[r][c] = p
    print(f"\n① 重放：{'全部合法' if not bad else '；'.join(bad)}"
          f"｜{'与落盘盘面一致 ✓' if rep == board else '⚠ 与落盘盘面不一致'}")

    missed, forced_avail = [], []
    fours = {BLACK: [], WHITE: []}
    open3 = {BLACK: [], WHITE: []}
    for i, (r, c, p) in enumerate(moves):
        b0 = board_after(moves, size, i)
        c0 = G.candidates(b0, size)
        pre = win_points(b0, size, p, c0)
        if pre and (r, c) not in pre:
            missed.append((i + 1, nm(r, c), [nm(*x) for x in pre]))
        if best_threats(b0, size, p, c0) >= 2:
            forced_avail.append(i + 1)
        b1 = board_after(moves, size, i + 1)
        c1 = G.candidates(b1, size)
        w = win_points(b1, size, p, c1)
        if w:
            fours[p].append((i + 1, nm(r, c), [nm(*x) for x in w]))
        if best_threats(b1, size, p, c1) >= 2:
            open3[p].append((i + 1, nm(r, c)))

    print(f"\n② 错失成五：{missed if missed else '无（双方 0 次）'}")
    print(f"③ 出现过必胜手（一手两威胁）的手数：{forced_avail if forced_avail else '无'}")
    print("④ 逼对手必须堵的「四」：")
    for p, tag in ((BLACK, "黑"), (WHITE, "白")):
        print(f"   {tag}：{fours[p] if fours[p] else '无'}")
    print("⑤ 做出活三（下一手可做双威胁）：")
    for p, tag in ((BLACK, "黑"), (WHITE, "白")):
        print(f"   {tag}：{open3[p] if open3[p] else '无'}")

    # ⑥ 死盘断言：剩余空格全填法暴力
    empties = [(r, c) for r in range(size) for c in range(size) if board[r][c] == EMPTY]
    print(f"\n⑥ 终局空格 {len(empties)}：{[nm(*x) for x in empties]}")
    if not empties:
        print("   盘满——按规则即和棋")
    elif len(empties) <= MAX_FILL_BRUTE:
        bad_b = bad_w = 0
        for combo in product((BLACK, WHITE), repeat=len(empties)):
            bb = [row[:] for row in board]
            for (r, c), v in zip(empties, combo):
                bb[r][c] = v
            for col, bump in ((BLACK, "b"), (WHITE, "w")):
                if any(bb[r][c] == col and G.check_win(bb, size, r, c, col)
                       for r in range(size) for c in range(size)):
                    if bump == "b":
                        bad_b += 1
                    else:
                        bad_w += 1
        verdict = "　→ 死盘 ✓" if bad_b == 0 and bad_w == 0 else "　→ 还有活路，别提前收摊"
        print(f"   {2 ** len(empties)} 种填法全跑：黑能连五 {bad_b} 种，白能连五 {bad_w} 种{verdict}")
    else:
        print(f"   空格 >{MAX_FILL_BRUTE}，暴力跑不动——按规则要盘满才算和，别提前说「平了」")


def play_self(size, level, first):
    b = [[EMPTY] * size for _ in range(size)]
    b[first[0]][first[1]] = BLACK
    cur, plies, res = WHITE, 1, "draw"
    while plies < size * size:
        mv = G.ai_choose(b, size, cur, level)
        if not mv:
            break
        r, c = mv
        b[r][c] = cur
        plies += 1
        if G.check_win(b, size, r, c, cur):
            res = cur
            break
        cur = 3 - cur
    return res, plies


def sample(sizes, games, level, seed):
    rng = random.Random(seed)
    print(f"盘型采样｜档 {level}｜每盘型 {games} 局｜随机首手｜seed {seed}")
    print(f"{'盘型':>7} {'黑胜':>5} {'白胜':>5} {'和棋':>5} {'均手数':>7} {'盘满率':>7}")
    for size in sizes:
        cnt = {BLACK: 0, WHITE: 0, "draw": 0}
        tot = 0
        for _ in range(games):
            m, span = size // 2, min(5, size)
            lo = max(0, m - 2)
            res, plies = play_self(size, level,
                                   (rng.randrange(lo, lo + span), rng.randrange(lo, lo + span)))
            cnt[res] += 1
            tot += plies
        avg = tot / games
        print(f"{size:>4}×{size:<2} {cnt[BLACK]:>5} {cnt[WHITE]:>5} {cnt['draw']:>5}"
              f" {avg:>7.1f} {avg / (size * size) * 100:>6.0f}%")


def main():
    ap = argparse.ArgumentParser(description="五子棋复盘 / 盘型采样探针")
    ap.add_argument("state", nargs="?", help="状态文件（gomoku.json）——复盘用")
    ap.add_argument("--sample", action="store_true", help="盘型采样模式")
    ap.add_argument("--games", type=int, default=12, help="每盘型几局（默认 12）")
    ap.add_argument("--sizes", default="9,11,13,15", help="逗号分隔盘型（默认 9,11,13,15）")
    ap.add_argument("--level", type=int, default=2, help="自对弈档位（默认 2）")
    ap.add_argument("--seed", type=int, default=0, help="随机首手种子")
    a = ap.parse_args()
    if a.sample:
        sample([int(x) for x in a.sizes.split(",") if x.strip()], a.games, a.level, a.seed)
    elif a.state:
        audit(a.state)
    else:
        ap.error("给个状态文件复盘，或 --sample 采样")


if __name__ == "__main__":
    main()

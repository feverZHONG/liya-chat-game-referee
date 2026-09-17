#!/usr/bin/env python3
"""五子棋对擂引擎：落子 / 渲染 / 胜负 + 对手 AI（档位公开可查）。状态默认存 <数据根>/temp/gomoku.json。

用法:
  python3 gomoku.py new                        # 开新局（11×11，阁下执黑先手，档位 2；图默认跟着出）
  python3 gomoku.py new --size 9 --level 4     # 换盘型 / 换档（档 4 = 会搜杀的）
  python3 gomoku.py new --human white          # 阁下执白（引擎先手）
  python3 gomoku.py new --no-png               # 这盘不出图，只发字符盘面
  python3 gomoku.py play H8                    # 阁下落 H8，引擎立刻应一手
  python3 gomoku.py show                       # 只打印盘面（图默认跟着出）
  python3 gomoku.py undo                       # 退回上一回合（阁下 + 引擎各一手）
  python3 gomoku.py resign                     # 阁下认输，收摊
  python3 gomoku.py draw                       # 双方同意和棋，提前收摊（死盘协商用）
  python3 gomoku.py level                      # 看档位；level 1|2|3 改档（下一手生效）
  python3 gomoku.py moves                      # 棋谱
  python3 gomoku.py demo [手数]                # 自对弈一段并出图（看效果 / 验收渲染，不碰对局）
  python3 gomoku.py demo 24 --vs-level 3 --out /tmp/x.png   # 指定手数 / 两边档位 / 出图路径
  python3 gomoku.py close                      # 收桌一条龙：存档 + INDEX 重建 + 复盘 + 台账 + git 提示（终局才收）

坐标：列 A–K，行 1–11（如 E5）；列数上限 26（坐标只有 A–Z 单字母）。
档位写在状态文件里（1 陪练 / 2 对等 / 3 较真 / 4 搜杀），阁下随时可查——不藏着。默认 classic（`.`/`X`/`O` 全半宽、绝对对齐），
歪列就换 --style cjk（`＋`/`●`/`○` 全宽）。

**图默认每手都出**（写 `<state同名>.png`，每手覆盖同一张，回话里附在文字后面）——
文字仍是主线（可复制、图挂了也能玩），图是给阁下省眼力的：不用从字符堆里推盘面。

可用 --state /path/to/xxx.json 换状态文件（默认=<数据根>/temp/gomoku.json）。
数据根：$GAME_HOME ＞ 往上找带 temp/ 或 workspace/ 的一层 ＞ 脚本上一级。
引擎放哪都能跑。别手改 json。
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

SIZE = 11
EMPTY, BLACK, WHITE = 0, 1, 2
DIRS = ((0, 1), (1, 0), (1, 1), (1, -1))
GLYPH = {"classic": (".", "X", "O"), "cjk": ("＋", "●", "○")}
LEVELS = {
    1: "陪练 — 只看眼前，漏堵活三；成五 / 堵五还是会做的",
    2: "对等 — 会堵活三、会做四，评分权重拉满",
    3: "较真 — 在档 2 之上会避杀：不把活四 / 四三送到对面手里（自己还不做杀）",
    4: "搜杀 — 会算连四的杀（VCF）和四三定式，也会拆对面的杀",
}
LEVEL_CN = {k: v.split("—")[0].strip() for k, v in LEVELS.items()}
# ---------- 数据根 ----------
# 状态落 <根>/temp/、归档落 <根>/workspace/records/。根怎么定：
#   ① $GAME_HOME 指哪儿是哪儿；
#   ② 否则从脚本位置往上找「带 temp/ 或 workspace/ 的那一层」；
#   ③ 都没有（别人 clone 出去单跑）就用脚本上一级。
# 引擎放哪都能跑——不必再拷一份到 temp/ 下跑。
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


ROOT = _data_root()
STATE_DIR = os.path.join(ROOT, "temp")
RECORDS_DIR = os.path.join(ROOT, "workspace", "records")
DEFAULT_STATE = os.path.join(STATE_DIR, "gomoku.json")
DEFAULT_ARCHIVE = os.path.join(RECORDS_DIR, "gomoku")
DEFAULT_LEDGER = os.path.join(RECORDS_DIR, "game-ledger", "ledger.json")
LEDGER_SCRIPT = os.path.join(HERE, "ledger.py")
AUDIT_SCRIPT = os.path.join(HERE, "gomoku_audit.py")
GAME_NAME = "五子棋"
OPPONENT = "阁下"
DEFAULTS = {"state": DEFAULT_STATE, "style": "classic", "png": True,
            "size": SIZE, "level": 2, "human": "black",
            "archive": DEFAULT_ARCHIVE, "ledger": DEFAULT_LEDGER, "note": None,
            "no_ledger": False}


# ---------- 参数 ----------
def parse_argv(argv):
    opts, pos, i = dict(DEFAULTS), [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--state", "--style", "--size", "--level", "--human",
                 "--plies", "--vs-level", "--out", "--archive", "--ledger", "--note"):
            opts[a[2:].replace("-", "_")] = argv[i + 1]
            i += 2
            continue
        if a.startswith(("--state=", "--style=", "--size=", "--level=", "--human=",
                         "--plies=", "--vs-level=", "--out=",
                         "--archive=", "--ledger=", "--note=")):
            k, v = a[2:].split("=", 1)
            opts[k.replace("-", "_")] = v
            i += 1
            continue
        if a == "--no-ledger":
            opts["no_ledger"] = True
            i += 1
            continue
        if a == "--no-png":  # 图默认出，这是关掉的开关
            opts["png"] = None
            i += 1
            continue
        if a == "--png":  # 路径可省：省了就写 <state同名>.png，每手覆盖同一张
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt and not nxt.startswith("-") and not re.fullmatch(r"[A-Za-z]\d{1,2}", nxt):
                opts["png"] = nxt
                i += 2
            else:
                opts["png"] = True
                i += 1
            continue
        if a.startswith("--png="):
            opts["png"] = a.split("=", 1)[1]
            i += 1
            continue
        pos.append(a)
        i += 1
    return pos, opts


# ---------- 盘面 ----------
def new_board(size, first_black=True):
    d = {"size": size, "board": [[EMPTY] * size for _ in range(size)],
         "moves": [], "winner": None}
    return d


def load(state):
    if not os.path.exists(state):
        sys.exit(f"没有盘面: {state}（先 new 一盘，或 find {STATE_DIR} 找状态文件）")
    with open(state, encoding="utf-8") as f:
        return json.load(f)


def save(d, state):
    os.makedirs(os.path.dirname(os.path.abspath(state)), exist_ok=True)
    with open(state, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def inside(size, r, c):
    return 0 <= r < size and 0 <= c < size


def parse(s, size):
    s = s.strip().upper()
    col = ord(s[0]) - ord("A")
    row = int(s[1:]) - 1
    if not inside(size, row, col):
        raise ValueError(f"越界: {s}（列 A–{chr(ord('A') + size - 1)}，行 1–{size}）")
    return (row, col)


def name_of(r, c):
    return chr(ord("A") + c) + str(r + 1)


# ---------- 胜负 ----------
def check_win(board, size, r, c, p):
    """(r,c) 刚落 p，四方向数连子。休闲规则：长连（6+）也算赢，不做禁手。"""
    for dr, dc in DIRS:
        n = 1
        for sgn in (1, -1):
            i, j = r + sgn * dr, c + sgn * dc
            while inside(size, i, j) and board[i][j] == p:
                n += 1
                i += sgn * dr
                j += sgn * dc
        if n >= 5:
            return True
    return False


# ---------- 渲染 ----------
def render(d, style="classic"):
    size = d["size"]
    g = GLYPH.get(style, GLYPH["classic"])
    lines = ["    " + " ".join(chr(ord("A") + c) for c in range(size))]
    for r in range(size):
        row = [g[d["board"][r][c]] for c in range(size)]
        lines.append(f"{r + 1:>2}  " + " ".join(row))
    return "\n".join(lines)


def side_name(p):
    return "黑" if p == BLACK else "白"


def tally(d):
    """统计行——唯一口径，回话里别手算。"""
    b = d["board"]
    blk = sum(row.count(BLACK) for row in b)
    wht = sum(row.count(WHITE) for row in b)
    s = f"手数 {len(d['moves'])}｜● 黑 {blk} 子 ／ ○ 白 {wht} 子"
    if d["moves"]:
        r, c, p = d["moves"][-1]
        s += f"｜最后手 {name_of(r, c)}（{side_name(p)}）"
    return s


def over_text(d):
    w = d.get("winner")
    if w == BLACK:
        return "终局：黑胜（五连）" if not d.get("resign") else "终局：黑胜（对手认输）"
    if w == WHITE:
        return "终局：白胜（五连）" if not d.get("resign") else "终局：白胜（对手认输）"
    if w == "draw":
        full = all(d["board"][r][c] != EMPTY
                   for r in range(d["size"]) for c in range(d["size"]))
        return "终局：和棋（盘满）" if full else "终局：和棋（双方同意，提前收摊）"
    return ""


def emit(d, opts):
    """出盘面（唯一出口）：盘面 + 统计行 +（终局）结果；给了 --png 再落一张图。"""
    print(render(d, opts["style"]))
    print(tally(d))
    if d.get("winner"):
        print(over_text(d))
    if opts.get("png"):
        render_png(d, opts["png"])
        print("图已生成:", opts["png"])


# ---------- 图片渲染（客户端字体歪列时的兜底，图不受字体影响） ----------
CJK_FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
NUM_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font(path, size):
    from PIL import ImageFont
    return (ImageFont.truetype(path, size) if os.path.exists(path)
            else ImageFont.load_default())


def _png_text(dr, box, text, font, fill):
    x0, y0, x1, y1 = box
    bb = dr.textbbox((0, 0), text, font=font)
    dr.text((x0 + (x1 - x0 - (bb[2] - bb[0])) / 2 - bb[0],
             y0 + (y1 - y0 - (bb[3] - bb[1])) / 2 - bb[1]), text, font=font, fill=fill)


def render_png(d, out, cell=54, ss=2):
    """盘面画成 PNG（自带统计行；终局多一行结果；最后一手点红心）。"""
    from PIL import Image, ImageDraw
    size = d["size"]
    c = cell * ss
    pad_l, pad_t, pad_r, pad_b = 46 * ss, 40 * ss, 18 * ss, 14 * ss
    line_h = 26 * ss
    caps = [tally(d)]
    if d.get("winner"):
        caps.append(over_text(d))
    wood, grid, ink = (250, 243, 222), (166, 154, 128), (72, 62, 48)
    img = Image.new("RGB", (pad_l + size * c + pad_r,
                            pad_t + size * c + pad_b + line_h * len(caps)), wood)
    dr = ImageDraw.Draw(img)
    f_lab, f_cap = _font(NUM_FONT, int(c * .34)), _font(CJK_FONT, 19 * ss)

    for i in range(size + 1):
        dr.line([pad_l + i * c, pad_t, pad_l + i * c, pad_t + size * c], fill=grid, width=ss)
        dr.line([pad_l, pad_t + i * c, pad_l + size * c, pad_t + i * c], fill=grid, width=ss)

    last = None
    if d["moves"]:
        r0, c0, _ = d["moves"][-1]
        last = (r0, c0)
    for r in range(size):
        for cc in range(size):
            p = d["board"][r][cc]
            if not p:
                continue
            cx, cy, rad = pad_l + cc * c + c / 2, pad_t + r * c + c / 2, c * 0.40
            fill = (28, 28, 28) if p == BLACK else (252, 252, 252)
            dr.ellipse([cx - rad, cy - rad, cx + rad, cy + rad],
                       fill=fill, outline=(70, 60, 46), width=ss)
            if last == (r, cc):  # 最后一手点红心，一眼看出对方刚下哪
                dr.ellipse([cx - c * .11, cy - c * .11, cx + c * .11, cy + c * .11],
                           fill=(212, 48, 48))

    for cc in range(size):
        _png_text(dr, (pad_l + cc * c, pad_t - 32 * ss, pad_l + (cc + 1) * c, pad_t - 4 * ss),
                  chr(ord("A") + cc), f_lab, ink)
    for r in range(size):
        _png_text(dr, (0, pad_t + r * c, pad_l - 8 * ss, pad_t + (r + 1) * c),
                  str(r + 1), f_lab, ink)
    for i, t in enumerate(caps):
        y = pad_t + size * c + pad_b + i * line_h
        _png_text(dr, (0, y, img.width, y + line_h), t, f_cap, ink)

    if ss != 1:
        img = img.resize((img.width // ss, img.height // ss), Image.LANCZOS)
    img.save(out)
    return out


# ---------- AI ----------
WIN = 10_000_000


def line_info(board, size, r, c, dr, dc, p):
    """把 (r,c) 当作已放 p：数连续同色数 + 两端各有几个空位（0/1/2）。"""
    n = 1
    i, j = r + dr, c + dc
    while inside(size, i, j) and board[i][j] == p:
        n += 1
        i += dr
        j += dc
    open_end = 1 if (inside(size, i, j) and board[i][j] == EMPTY) else 0
    i, j = r - dr, c - dc
    while inside(size, i, j) and board[i][j] == p:
        n += 1
        i -= dr
        j -= dc
    open_end += 1 if (inside(size, i, j) and board[i][j] == EMPTY) else 0
    return n, open_end


def shape_value(n, ends):
    if n >= 5:
        return WIN
    if n == 4:
        return 1_000_000 if ends == 2 else (120_000 if ends == 1 else 0)
    if n == 3:
        return 60_000 if ends == 2 else (6_000 if ends == 1 else 0)
    if n == 2:
        return 2_500 if ends == 2 else (250 if ends == 1 else 0)
    return 120 if ends == 2 else 12


def point_score(board, size, r, c, p):
    """在空点 (r,c) 落 p 有多值钱（纯形状分，双威胁额外加权）。"""
    tot, big = 0, 0
    for dr, dc in DIRS:
        n, ends = line_info(board, size, r, c, dr, dc, p)
        tot += shape_value(n, ends)
        if n >= 4 or (n == 3 and ends == 2):
            big += 1
    if big >= 2:  # 双三 / 双四：一个点两个方向同时成形，别按单点求和低估它
        tot += 200_000 * (big - 1)
    return tot


def candidates(board, size, radius=2):
    """只在已有子周围 2 格内的空点里挑——全盘扫没意义还慢。

    空盘 → 天元；盘满 → 空表（调用方按「没得下」处理，别再硬塞一个已占的点）。
    """
    pts, stones = set(), 0
    for r in range(size):
        for c in range(size):
            if board[r][c] == EMPTY:
                continue
            stones += 1
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    rr, cc = r + dr, c + dc
                    if inside(size, rr, cc) and board[rr][cc] == EMPTY:
                        pts.add((rr, cc))
    if not stones:
        return [(size // 2, size // 2)]
    return sorted(pts)


# ---------- 档 4：威胁搜杀（VCF / 四三 / 破杀） ----------
# 档 1–3 是「形状评分」，看不出「连着的杀」；这一档起手算杀：先把四串成链，
# 再把「四 + 活三」这种定式点找出来；自己算不出杀时，还要回头查对面的杀。
def five_points(board, size, p, cells=None):
    """空点里落 p 直接成五的点——也就是「四」的杀点。只看子力附近，全盘扫没必要。"""
    cells = candidates(board, size) if cells is None else cells
    return [(r, c) for (r, c) in cells
            if board[r][c] == EMPTY and check_win(board, size, r, c, p)]


def four_moves(board, size, p):
    """落 p 能做出一手「四」（对手下一步必须堵）的点。"""
    out = []
    for (r, c) in candidates(board, size):
        board[r][c] = p
        if five_points(board, size, p):
            out.append((r, c))
        board[r][c] = EMPTY
    return out


def open_four_moves(board, size, p):
    """落 p 做出「活四 / 双四」（一手留 ≥2 个杀点 = 赢定）的点。"""
    out = []
    for (r, c) in candidates(board, size):
        board[r][c] = p
        if len(five_points(board, size, p)) >= 2:
            out.append((r, c))
        board[r][c] = EMPTY
    return out


def vcf(board, size, me, depth=6, budget=None):
    """连续冲四取胜（VCF）。返回第一手坐标，或 None。budget=[N] 是节点预算。

    每一步都必须是「四」——对手除了堵没有别的选择，所以整条线是强制的。
    做出活四 / 双四（≥2 个杀点）直接算赢；对手能抢先成五，这条线作废。
    """
    budget = [1500] if budget is None else budget
    wins = five_points(board, size, me)
    if wins:
        return wins[0]
    if depth <= 0 or budget[0] <= 0:
        return None
    opp = 3 - me
    for (r, c) in four_moves(board, size, me):
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        board[r][c] = me
        w = five_points(board, size, me)
        opp_wins = five_points(board, size, opp)
        # 活四 / 双四 = 下一手就赢；但对面手里捏着现成的五，人家先收，这条线作废
        ok = len(w) >= 2 and not opp_wins
        if not ok and len(w) == 1 and not opp_wins:
            blk = w[0]                         # 对手只能堵这个杀点
            board[blk[0]][blk[1]] = opp
            ok = bool(vcf(board, size, me, depth - 1, budget))
            board[blk[0]][blk[1]] = EMPTY
        board[r][c] = EMPTY
        if ok:
            return (r, c)
    return None


def four_three_moves(board, size, p):
    """落 p 做出「四三」——一个四（对手必堵）+ 一个活三，堵完活三就是活四，定式必胜。"""
    opp = 3 - p
    out = []
    for (r, c) in four_moves(board, size, p):
        board[r][c] = p
        w = five_points(board, size, p)
        if len(w) == 1 and not five_points(board, size, opp):
            blk = w[0]
            board[blk[0]][blk[1]] = opp        # 对手只能堵这个杀点
            if open_four_moves(board, size, p):
                out.append((r, c))
            board[blk[0]][blk[1]] = EMPTY
        board[r][c] = EMPTY
    return out


def ai_choose_search(board, size, ai, depth=6):
    """档 4：先算自己的杀（VCF / 四三），再挑「不送对面杀」的落点。"""
    hum = 3 - ai
    cands = candidates(board, size)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    mine = five_points(board, size, ai)
    if mine:                                    # 自己一步成五，收
        return mine[0]
    theirs = five_points(board, size, hum)
    if theirs:                                  # 对面有杀点，先堵
        if len(theirs) == 1:
            return theirs[0]
        return max(theirs, key=lambda m: point_score(board, size, m[0], m[1], ai))
    mv = vcf(board, size, ai, depth)            # 自己的连杀
    if mv:
        return mv
    f3 = four_three_moves(board, size, ai)      # 四三定式
    if f3:
        return f3[0]
    scored = [(r, c, point_score(board, size, r, c, ai),
               point_score(board, size, r, c, hum)) for (r, c) in cands]
    scored.sort(key=lambda x: x[2] + 0.9 * x[3], reverse=True)
    top = scored[:8]
    opp_vcf = vcf(board, size, hum, depth)      # 对面已经有连杀 → 先把它的起手点占掉
    if opp_vcf:
        top.sort(key=lambda x: 0 if (x[0], x[1]) == opp_vcf else 1)
    for (r, c, _a, _d) in top:
        board[r][c] = ai
        forcing = bool(five_points(board, size, ai))   # 我这手做成「四」→ 对面得先堵
        safe = forcing or (not five_points(board, size, hum)
                           and not vcf(board, size, hum, depth - 2, [200])
                           and not open_four_moves(board, size, hum)
                           and not four_three_moves(board, size, hum))
        board[r][c] = EMPTY
        if safe:
            return (r, c)
    return top[0][:2]


def avoid_threat_moves(board, size, ai, hum, scored):
    """档位 3：从高分往下挑，第一个「走出去不送对面活四 / 四三」的点。

    旧版是「我下之后，看对手最狠的一手拿多少分」——实测那套把棋下软了（自己给自己
    降档）。这里换成硬检查：只看对面有没有**立刻成杀**的手，有就换点。
    """
    for (r, c, _a, _d) in scored[:10]:
        board[r][c] = ai
        safe = (not open_four_moves(board, size, hum)
                and not four_three_moves(board, size, hum))
        board[r][c] = EMPTY
        if safe:
            return (r, c)
    return scored[0][:2]


def ai_choose(board, size, ai, level):
    """选点顺序：我成五 > 堵对手五 > 我做活四 > 堵对手活四 > 评分（档位定权重）。

    档 4 走另一条线：整体交给 `ai_choose_search`（算杀 + 破杀）。
    """
    if level >= 4:
        return ai_choose_search(board, size, ai)
    hum = 3 - ai
    cands = candidates(board, size)
    if not cands:
        return None  # 盘满，没得下
    if len(cands) == 1:
        return cands[0]
    scored = [(r, c, point_score(board, size, r, c, ai),
               point_score(board, size, r, c, hum)) for (r, c) in cands]
    for idx, tag in ((2, "我成五"), (3, "堵五")):
        hit = [x for x in scored if x[idx] >= WIN]
        if hit:
            return max(hit, key=lambda x: x[idx])[:2]
    if level >= 2:
        for idx in (2, 3):
            hit = [x for x in scored if x[idx] >= 1_000_000]
            if hit:
                return max(hit, key=lambda x: x[idx])[:2]
    w = {1: 0.35, 2: 0.85, 3: 1.0}.get(level, 0.85)
    scored.sort(key=lambda x: x[2] + w * x[3], reverse=True)
    if level >= 3:
        return avoid_threat_moves(board, size, ai, hum, scored)
    return scored[0][:2]


# ---------- 收桌（存档 + INDEX + 台账，一条命令） ----------
META_RE = re.compile(r"<!-- meta (\{.*?\}) -->")

INDEX_HEAD = """# 五子棋对局存档

> 一盘一档：`<日期>-<盘型>-<结果>-阁下vs莉娅.md`，同名 `.json` 是原始状态文件（棋谱，机器可读）。
> 复核任何一盘：`python3 skills/chat-game-referee/scripts/gomoku_audit.py <该盘的 .json>`
> 引擎与规矩：`skills/chat-game-referee/games/gomoku/rules.md`
> **收桌**：`python3 skills/chat-game-referee/scripts/gomoku.py close` —— 本 INDEX 由它自动重建，别手改。
"""

INDEX_TAIL = """
## 存档规矩

- 终局（五连 / 和棋 / 认输）才收；`close` 把 `temp/gomoku.json` 拷成一档并重建本页。
- **`temp/gomoku.json` 只活在当前这盘**——开新盘就被覆盖，要留必须先收桌。
- 终局盘面 / 棋谱 / 复盘一律由 `close` 从存档与 `gomoku_audit.py` 直接生成，**别手写**。
- 五子棋不藏答案，公平靠**档位公开**——`level` 写在状态文件里，阁下随时可查可改。
"""


def _rel_or_abs(p):
    r = os.path.relpath(p, ROOT)
    return p if r.startswith("..") else r


def _meta_of(path):
    """读一档存档的 `<!-- meta {...} -->`（INDEX 靠它重建，存档是唯一事实源）。"""
    try:
        with open(path, encoding="utf-8") as f:
            m = META_RE.search(f.read())
        return json.loads(m.group(1)) if m else None
    except (OSError, ValueError):
        return None


def _audit_lines(path):
    """照抄 gomoku_audit.py 的复盘输出（只读、不碰状态）。跑不动就明说，别编。"""
    script = AUDIT_SCRIPT if os.path.exists(AUDIT_SCRIPT) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "gomoku_audit.py")
    if not os.path.exists(script):
        return ["（找不到 gomoku_audit.py，复盘跳过。）"]
    try:
        r = subprocess.run([sys.executable, script, path], capture_output=True,
                           text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return ["（复盘超时——空格太多、穷举跑不完；拿这盘的 json 单独再跑。）"]
    out = (r.stdout or "").strip()
    return out.splitlines() or [f"（复盘没出结果：{(r.stderr or '').strip()[:200]}）"]


def _slug_result(d):
    """(结果中文, detail)。winner 是 int / 'draw'；human 是阁下那一方。"""
    w = d.get("winner")
    human = d.get("human", BLACK)
    if w == "draw":
        full = all(d["board"][r][c] != EMPTY
                   for r in range(d["size"]) for c in range(d["size"]))
        return "和棋", ("盘满" if full else "死盘")
    if w == human:
        return "阁下胜", ("认输" if d.get("resign") else "")
    return "莉娅胜", ("认输" if d.get("resign") else "")


def build_archive_md(d, slug, audit_lines):
    """一盘的账写成给人看的 md。盘面 / 棋谱 / 复盘全部照抄引擎与审计脚本，一手不手写。"""
    size = d["size"]
    human = d.get("human", BLACK)
    level = d.get("level", 2)
    res_cn, detail = _slug_result(d)
    blk = sum(row.count(BLACK) for row in d["board"])
    wht = sum(row.count(WHITE) for row in d["board"])
    meta = {"slug": slug, "date": slug[:10], "game": GAME_NAME, "mode": f"{size}x{size}",
            "result": res_cn, "detail": detail, "winner": str(d.get("winner")),
            "moves": len(d["moves"]), "level": level, "human": side_name(human),
            "opponent": OPPONENT}
    L = [f"# 五子棋对局存档 · {slug[:10]}", "",
         f"<!-- meta {json.dumps(meta, ensure_ascii=False)} -->", "",
         f"**盘型** {size}×{size}（连五即胜、不做禁手）｜**档位** {level} · {LEVEL_CN[level]}"
         f"｜阁下执{side_name(human)}" + ("先手，莉娅执白" if human == BLACK else "后手，莉娅执黑先占天元"),
         f"**手数** {len(d['moves'])}｜黑 {blk} 子 ／ 白 {wht} 子｜**结果：{res_cn}**"
         + (f"（{detail}）" if detail else ""), "",
         "## 终局盘面", "", "```", render(d), tally(d), over_text(d), "```", "",
         "## 棋谱", "", "```"]
    for k, (r, c, p) in enumerate(d["moves"], 1):
        who = OPPONENT if p == human else "莉娅"
        L.append(f"  {k:>3}. {who} {side_name(p)} {name_of(r, c)}")
    L += ["```", "", "## 复盘（照抄 `gomoku_audit.py`）", "", "```"]
    L += list(audit_lines)
    L += ["```", "", "## 口径", "",
          "- 五子棋是**对擂**：本天使下场当对手，胜负按盘算（记进台账的就是这个口径）。",
          "- 公平靠档位公开：`level` 写在状态文件里，阁下随时可查可改；中途手改 = 作弊。",
          "- 原始状态文件（棋谱）：同目录同名 `.json`——复核用 `gomoku_audit.py <该 json>`。"]
    return "\n".join(L) + "\n"


def rebuild_index(archive):
    """INDEX 从各档 md 的 meta 重建 —— 存档是唯一事实源，手改 INDEX 会被下一次收桌冲掉。"""
    rows = []
    for fn in sorted(os.listdir(archive)):
        if fn.endswith(".md") and fn != "INDEX.md":
            meta = _meta_of(os.path.join(archive, fn))
            if meta:
                rows.append((meta, fn))
    rows.sort(key=lambda r: (r[0].get("date", ""), r[1]), reverse=True)
    by_year = {}
    for meta, fn in rows:
        by_year.setdefault(str(meta.get("date", ""))[:4], []).append((meta, fn))
    out = [INDEX_HEAD.rstrip("\n")]
    for year in sorted(by_year, reverse=True):
        out += ["", f"## {year}", "",
                "| 日期 | 盘型 | 结果 | 对局 | 手数 | 档位 |",
                "|------|------|------|------|------|------|"]
        for meta, fn in by_year[year]:
            res = str(meta.get("result", ""))
            if meta.get("detail"):
                res += f"（{meta['detail']}）"
            hum = meta.get("human", "黑")
            lv = meta.get("level", "")
            out.append(f"| {meta.get('date','')} | {meta.get('mode','')} | {res} "
                       f"| {OPPONENT}（{hum}）vs 莉娅（{'白' if hum == '黑' else '黑'}） "
                       f"| {meta.get('moves','')} | {lv} · {LEVEL_CN.get(lv, '')} |")
        out.append("")
        for meta, fn in by_year[year]:
            base = fn[:-3]
            out.append(f"- [{meta.get('date','')} · {meta.get('mode','')} · "
                       f"{meta.get('result','')}]({fn})｜[原始状态 json]({base}.json)")
    out.append(INDEX_TAIL)
    with open(os.path.join(archive, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return len(rows)


def _ledger_script():
    for c in (LEDGER_SCRIPT, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.py")):
        if os.path.exists(c):
            return c
    return None


def cmd_close(opts):
    state = opts["state"]
    d = load(state)
    if not d.get("winner"):
        sys.exit("还没收桌——五连 / 和棋 / 认输才收（下完再收）。")
    if d.get("closed_as"):
        sys.exit(f"这盘已经收过了（{d['closed_as']}）——要再收先 new 开新盘。")
    size = d["size"]
    res_cn, detail = _slug_result(d)
    archive = opts["archive"]
    base_slug = f"{datetime.date.today().isoformat()}-{size}x{size}-{res_cn}-{OPPONENT}vs莉娅"
    slug, i = base_slug, 2
    while os.path.exists(os.path.join(archive, slug + ".md")):   # 同一天连开两盘不许互相盖
        slug, i = f"{base_slug}-{i}", i + 1
    os.makedirs(archive, exist_ok=True)
    dst_json = os.path.join(archive, slug + ".json")
    shutil.copyfile(state, dst_json)
    audit = _audit_lines(dst_json)          # 复盘跑在存档那份 json 上（只读）
    md_path = os.path.join(archive, slug + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(build_archive_md(d, slug, audit))
    n = rebuild_index(archive)
    d["closed_as"] = slug
    save(d, state)

    print(f"  收桌：{size}×{size} · {res_cn} · {len(d['moves'])} 手")
    print(f"  存档：{_rel_or_abs(dst_json)}")
    print(f"        {_rel_or_abs(md_path)}")
    print(f"  INDEX：{_rel_or_abs(os.path.join(archive, 'INDEX.md'))}（按存档重建，现有 {n} 档）")

    ledger = None if opts.get("no_ledger") else opts.get("ledger")
    if ledger:
        script = _ledger_script()
        if not script:
            print("  台账：找不到 ledger.py，跳过（存档已落）。")
            return
        human = d.get("human", BLACK)
        result = "draw" if d.get("winner") == "draw" else ("loss" if d["winner"] == human else "win")
        note = opts.get("note") or (f"{size}×{size} · 档 {d.get('level', 2)} · "
                                    f"{res_cn}" + (f"（{detail}）" if detail else ""))
        r = subprocess.run([sys.executable, script, "--ledger", ledger, "add", GAME_NAME, result,
                            "--level", str(d.get("level", 2)),
                            "--moves", str(len(d["moves"])), "--note", note,
                            "--source", _rel_or_abs(dst_json)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print("  台账：" + r.stdout.strip().replace("\n", "\n        "))
        else:
            print(f"  台账：记不上 → {r.stderr.strip()}")
        print(f"  git：cd {ROOT} && git add {_rel_or_abs(archive)} {_rel_or_abs(ledger)}"
              f" && git commit -q -m \"{GAME_NAME}:{slug[5:]}\"")
    else:
        print(f"  git：cd {ROOT} && git add {_rel_or_abs(archive)}"
              f" && git commit -q -m \"{GAME_NAME}:{slug[5:]}\"")


# ---------- 动作 ----------
def do_move(d, r, c, p):
    """落一手并判胜，返回 True=这手赢了。"""
    d["board"][r][c] = p
    d["moves"].append([r, c, p])
    if check_win(d["board"], d["size"], r, c, p):
        d["winner"] = p
        return True
    if len(d["moves"]) == d["size"] * d["size"]:
        d["winner"] = "draw"
    return False


def cmd_demo(pos, opts):
    """自对弈一段并出图——看效果 / 验收渲染用。

    不碰正式状态文件，也不占用对局图（默认写 `gomoku-demo.png`）。
    改过渲染代码、换机器或字体之后，跑这个出图瞄一眼，比开一盘快。
    """
    size = int(opts["size"])
    lv = int(opts["level"])
    lv2 = int(opts.get("vs_level") or lv)
    plies = int(pos[1]) if len(pos) > 1 else int(opts.get("plies") or 20)
    d = {"size": size, "board": [[EMPTY] * size for _ in range(size)],
         "moves": [], "winner": None}
    cur, lvs = BLACK, {BLACK: lv, WHITE: lv2}
    for _ in range(plies):
        mv = ai_choose(d["board"], size, cur, lvs[cur])
        if not mv:
            break
        r, c = mv
        d["board"][r][c] = cur
        d["moves"].append([r, c, cur])
        if check_win(d["board"], size, r, c, cur):
            d["winner"] = cur
            break
        cur = 3 - cur
    print(render(d, opts["style"]))
    print(tally(d))
    if d["winner"]:
        print(over_text(d))
    if opts.get("png"):
        out = opts.get("out") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "gomoku-demo.png")
        render_png(d, out)
        print("图已生成:", out)


def main():
    pos, opts = parse_argv(sys.argv[1:])
    state = opts["state"]
    act = pos[0] if pos else "show"

    if act == "demo":  # 演示不碰 state，也别占正式对局图——放在路径改写之前
        cmd_demo(pos, opts)
        return

    if opts["png"] is True:  # --png 省了路径：写 state 同名 .png，每手覆盖同一张
        opts["png"] = (state[:-5] if state.endswith(".json") else state) + ".png"

    if act == "close":   # 收桌：自个儿 load，不走下面的对局分支
        cmd_close(opts)
        return

    if act == "new":
        size = int(opts["size"])
        if not 5 <= size <= 26:
            sys.exit(f"盘型 {size} 不合理：列数 5–26（坐标只有 A–Z 单字母）")
        level = int(opts["level"])
        if level not in LEVELS:
            sys.exit(f"没有这个档位: {level}（可选 1/2/3/4）")
        human = {"black": BLACK, "white": WHITE, "b": BLACK, "w": WHITE}.get(
            str(opts["human"]).lower())
        if human is None:
            sys.exit(f"--human 只认 black / white")
        d = new_board(size)
        d.update({"level": level, "human": human})
        if human == WHITE:  # 阁下后手：引擎先占天元
            do_move(d, size // 2, size // 2, BLACK)
        save(d, state)
        emit(d, opts)
        print(f"\n新盘已存 {state}")
        print(f"盘面 {size}×{size}｜阁下执{side_name(human)}"
              f"｜引擎档位 {level} · {LEVELS[level]}")
        return

    d = load(state)
    size = d["size"]
    human = d.get("human", BLACK)
    ai = 3 - human

    if act == "level":
        if len(pos) > 1:
            lv = int(pos[1])
            if lv not in LEVELS:
                sys.exit(f"没有这个档位: {lv}（可选 1/2/3/4）")
            d["level"] = lv
            save(d, state)
            print(f"档位改成 {lv}（{LEVELS[lv]}）——下一手生效")
        else:
            lv = d.get("level", 2)
            print(f"当前档位 {lv}：{LEVELS[lv]}")
            print("（档位写在状态文件里，阁下随时可查；改档：level 1|2|3|4）")
        return

    if act == "moves":
        if not d["moves"]:
            print("棋谱：还一手没落")
            return
        print("棋谱：")
        for k, (r, c, p) in enumerate(d["moves"], 1):
            who = "阁下" if p == human else "引擎"
            print(f"  {k:>3}. {who}{side_name(p)} {name_of(r, c)}")
        return

    if act == "play":
        if d.get("winner"):
            emit(d, opts)
            print(f"\n{over_text(d)}——这盘完了，要接着下先 new 一盘。没动。")
            return
        try:
            r, c = parse(pos[1], size)
        except (IndexError, ValueError) as e:
            sys.exit(f"坐标不对：{e}")
        if d["board"][r][c] != EMPTY:
            emit(d, opts)
            print(f"\n{name_of(r, c)} 上已经有子了，没动。换一格。")
            return
        do_move(d, r, c, human)
        print(f"阁下 {side_name(human)} {name_of(r, c)}")
        if not d["winner"]:
            mv = ai_choose(d["board"], size, ai, d.get("level", 2))
            if mv:  # 盘满时 ai_choose 返回 None，没得下就按住
                do_move(d, mv[0], mv[1], ai)
                print(f"引擎 {side_name(ai)} {name_of(*mv)}")
        save(d, state)
        print()
        emit(d, opts)
        return

    if act == "undo":
        if not d["moves"]:
            print("还没落子，没得退")
            return
        target = None
        for i in range(len(d["moves"]) - 1, -1, -1):
            if d["moves"][i][2] == human:
                target = i
                break
        if target is None:
            print("棋谱里没有阁下落的手，没得退")
            return
        dropped = d["moves"][target:]
        for r, c, _p in dropped:
            d["board"][r][c] = EMPTY
        del d["moves"][target:]
        d["winner"] = None
        d.pop("resign", None)
        save(d, state)
        print(f"退掉 {len(dropped)} 手：{'、'.join(name_of(r, c) for r, c, _ in dropped)}"
              f"——轮到阁下重新落子")
        print()
        emit(d, opts)
        return

    if act == "draw":
        if d.get("winner"):
            print(f"{over_text(d)}——已经完了。")
            return
        d["winner"] = "draw"
        save(d, state)
        emit(d, opts)
        print("\n双方同意，和棋收摊。")
        return

    if act == "resign":
        if d.get("winner"):
            print(f"{over_text(d)}——已经完了。")
            return
        d["winner"] = ai
        d["resign"] = True
        save(d, state)
        emit(d, opts)
        print("\n阁下认输，收摊。")
        return

    emit(d, opts)


if __name__ == "__main__":
    main()

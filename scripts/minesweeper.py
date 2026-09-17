#!/usr/bin/env python3
"""扫雷盘面操作 CLI：揭示/标雷/重开 + 公平自证。状态默认存 <数据根>/temp/minesweeper.json。

用法:
  python3 minesweeper.py new C3 --preset quick          # 开局(预设盘型)并翻开 C3
  python3 minesweeper.py new C3 --w 6 --h 6 --mines 5   # 自定义盘面
  python3 minesweeper.py open C3      # 揭示 C3
  python3 minesweeper.py flag C3      # 标/取消标雷
  python3 minesweeper.py show         # 只打印盘面(带统计行；--reveal-all 摊牌)
  python3 minesweeper.py open C3 --png   # 文字盘面照旧 + 顺手落一张图(<state同名>.png，群里附在文字后)
  python3 minesweeper.py hint         # 口子清单：哪些数字没开邻居最少(只给形状)
  python3 minesweeper.py check        # 自洽校验（公平自证用，别手算）
  python3 minesweeper.py commit       # 封存盐+答案，打印可公布的 hash
  python3 minesweeper.py reveal       # 终局摊开盐+答案+独立复算（踩雷时自动摊）
  python3 minesweeper.py close        # 收桌一条龙：存档 + INDEX 重建 + 台账 + git 提示（终局才收）

终局（BOOM 踩雷 / CLEAR 全清）后一条 `close` 收桌：把 temp 那份盘 + 承诺拷成一档、
md 从盘面与 `check`/`reveal` 现生成、INDEX 按存档重建、台账记一笔，末了打出 git 命令。
没收桌 / 同一盘重收，CLI 自己挡。

--w / --h / --mines 只在 new 时生效（尺寸与雷数写进状态文件，后续按盘面自动识别）；
--preset 给盘型（quick/beginner/inter），显式 --w/--h/--mines 覆盖它。

可用 --state /path/to/xxx.json 换状态文件（默认=<数据根>/temp/minesweeper.json）。
数据根：$GAME_HOME ＞ 往上找带 temp/ 或 workspace/ 的一层 ＞ 脚本上一级。
引擎放哪都能跑。别手改 json。
"""
import datetime
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import sys

W = H = 10
NMINES = 10
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
DEFAULT_STATE = os.path.join(STATE_DIR, "minesweeper.json")
DEFAULT_ARCHIVE = os.path.join(RECORDS_DIR, "minesweeper")
DEFAULT_LEDGER = os.path.join(RECORDS_DIR, "game-ledger", "ledger.json")
LEDGER_SCRIPT = os.path.join(HERE, "ledger.py")
GAME_NAME = "扫雷"
OPPONENT = "阁下"


# ---------- 参数 ----------
DEFAULTS = {"state": DEFAULT_STATE, "style": "classic", "reveal_all": False,
            "w": W, "h": H, "mines": NMINES, "preset": None, "png": None,
            "archive": DEFAULT_ARCHIVE, "ledger": DEFAULT_LEDGER, "note": None,
            "no_ledger": False}

# 开局盘型预设：`new C3 --preset quick` 一句话开局，别每盘手敲三个数。
# 只放 QQ 上看得清、且列数 ≤26（单字母坐标够用）的尺寸。
PRESETS = {
    "quick":    (6, 6, 5),     # 6×6 快速局 ≈14%
    "beginner": (9, 9, 10),    # 9×9 初级 12.3%
    "inter":    (16, 16, 40),  # 16×16 中级 15.6%
}


def apply_preset(opts):
    """预设先落，命令行显式给的 --w/--h/--mines 优先（_explicit 记着谁被显式给过）。"""
    name = opts.get("preset")
    if not name:
        return
    p = PRESETS.get(name.lower())
    if not p:
        sys.exit(f"没有这个盘型: {name}（可选 {'/'.join(PRESETS)}）")
    for k, v in zip(("w", "h", "mines"), p):
        if k not in opts["_explicit"]:
            opts[k] = v


def set_dims(w=None, h=None, mines=None):
    """按盘面实际尺寸/雷数设定模块级 W/H/NMINES（load 后必须调，否则 6x6 盘按 10x10 解析）。"""
    global W, H, NMINES
    if w:
        W = int(w)
    if h:
        H = int(h)
    if mines:
        NMINES = int(mines)


def parse_argv(argv):
    opts, pos, i = dict(DEFAULTS), [], 0
    opts["_explicit"] = set()
    while i < len(argv):
        a = argv[i]
        if a in ("--state", "--style", "--preset", "--archive", "--ledger", "--note"):
            opts[a[2:]] = argv[i + 1]
            i += 2
            continue
        if a.startswith(("--state=", "--style=", "--preset=", "--archive=", "--ledger=", "--note=")):
            k, v = a[2:].split("=", 1)
            opts[k] = v
            i += 1
            continue
        if a == "--no-ledger":
            opts["no_ledger"] = True
            i += 1
            continue
        if a == "--png":  # 路径可省：省了就写 <state同名>.png，每手覆盖同一张
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt and not nxt.startswith("-") and not re.fullmatch(r"[A-Za-z]\d{1,2}", nxt):
                opts["png"] = nxt
                i += 2
            else:
                opts["png"] = True  # 占位，main 里换成默认路径
                i += 1
            continue
        if a.startswith("--png="):
            opts["png"] = a.split("=", 1)[1]
            i += 1
            continue
        if a in ("--w", "--h", "--mines"):
            k = a[2:]
            opts[k] = int(argv[i + 1])
            opts["_explicit"].add(k)
            i += 2
            continue
        if a.startswith(("--w=", "--h=", "--mines=")):
            k, v = a[2:].split("=", 1)
            opts[k] = int(v)
            opts["_explicit"].add(k)
            i += 1
            continue
        if a == "--reveal-all":
            opts["reveal_all"] = True
            i += 1
            continue
        pos.append(a)
        i += 1
    return pos, opts


# ---------- 盘面 ----------
def new_board(first=None):
    cells = [(r, c) for r in range(H) for c in range(W)]
    safe = set()
    if first:
        r, c = first
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if 0 <= rr < H and 0 <= cc < W:
                    safe.add((rr, cc))
    pool = [x for x in cells if x not in safe]
    if NMINES > len(pool):
        sys.exit(f"雷比可用格子多: {NMINES} > {len(pool)}（首点安全区已排除 9 格）")
    mines = set(random.sample(pool, NMINES))
    counts = {}
    for r in range(H):
        for c in range(W):
            if (r, c) in mines:
                continue
            counts[f"{r},{c}"] = sum(
                1
                for dr in (-1, 0, 1)
                for dc in (-1, 0, 1)
                if (dr or dc) and (r + dr, c + dc) in mines
            )
    return {"W": W, "H": H, "mines": sorted(mines),
            "counts": counts, "opened": [], "flagged": [], "steps": 0}


def load(state):
    if not os.path.exists(state):
        sys.exit(f"没有盘面: {state}（先 new 一盘，或 find {STATE_DIR} 找状态文件）")
    with open(state, encoding="utf-8") as f:
        d = json.load(f)
    d["mines"] = [tuple(m) for m in d["mines"]]
    d["opened"] = [tuple(x) for x in d["opened"]]
    d["flagged"] = [tuple(x) for x in d["flagged"]]
    return d


def save(d, state):
    os.makedirs(os.path.dirname(os.path.abspath(state)), exist_ok=True)
    with open(state, "w", encoding="utf-8") as f:
        json.dump({"W": d["W"], "H": d["H"],
                   "mines": [list(m) for m in d["mines"]],
                   "counts": d["counts"],
                   "opened": [list(x) for x in d["opened"]],
                   "flagged": [list(x) for x in d["flagged"]],
                   "steps": d.get("steps", 0),
                   "closed_as": d.get("closed_as")}, f,
                  ensure_ascii=False)


def parse(s):
    s = s.strip().upper()
    col = ord(s[0]) - ord("A")
    row = int(s[1:]) - 1
    if not (0 <= col < W and 0 <= row < H):
        raise ValueError(f"越界: {s}")
    return (row, col)


def name_of(rc):
    """(r,c) → D2 记法（回话里用，全大写）。"""
    return chr(ord("A") + rc[1]) + str(rc[0] + 1)


def flood(d, r, c):
    mines = set(d["mines"])
    opened = set(d["opened"])
    stack = [(r, c)]
    while stack:
        cur = stack.pop()
        if cur in opened or cur in mines:
            continue
        opened.add(cur)
        if d["counts"][f"{cur[0]},{cur[1]}"] == 0:
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    nb = (cur[0] + dr, cur[1] + dc)
                    if 0 <= nb[0] < H and 0 <= nb[1] < W and nb not in opened:
                        stack.append(nb)
    d["opened"] = sorted(opened)


def cell_text(d, r, c, hit=None, reveal_all=False, style="classic"):
    """一格显示成什么。classic 用符号，emoji 全 emoji（宽度均匀，不容易歪列）。

    两种雷分开画：💥 只给真踩中的那一颗，摊牌摊开的雷用 💣。
    全画 💥 会被玩家读成「你踩雷了」——2026-09-14 实盘吃过这个误会。
    """
    mines = set(d["mines"])
    mine_here = (r, c) in mines
    opened = (r, c) in set(d["opened"])
    flagged = (r, c) in set(d["flagged"])
    stepped = mine_here and (opened or hit == (r, c))   # 真踩中（open 只会停在第一颗）
    shown_mine = stepped or (mine_here and reveal_all)  # 摊牌时该画出来的雷
    mine_glyph = "\U0001F4A5" if stepped else "\U0001F4A3"
    if style == "emoji":
        if stepped:
            return "\U0001F4A5"
        if opened:
            n = d["counts"][f"{r},{c}"]
            return "".join((str(n), "\ufe0f", "\u20e3")) if n else "\u2b1c"
        if shown_mine:
            return mine_glyph
        if flagged:
            return "\U0001F6A9"
        return "\U0001F7E6"
    if opened:
        if mine_here:
            return "\U0001F4A5"
        n = d["counts"][f"{r},{c}"]
        return str(n) if n else "\u00b7"
    if shown_mine:
        return mine_glyph
    if flagged:
        return "\U0001F6A9"
    return "\u25a0"


def render(d, hit=None, reveal_all=False, style="classic"):
    if style == "emoji":
        lines = ["   " + "".join(chr(ord("A") + c) + "  " for c in range(W))]
        for r in range(H):
            row = [cell_text(d, r, c, hit, reveal_all, style) for c in range(W)]
            lines.append(f"{r+1:>2} " + " ".join(row))
        return "\n".join(lines)
    lines = ["    " + " ".join(chr(ord("A") + c) for c in range(W))]
    for r in range(H):
        row = [cell_text(d, r, c, hit, reveal_all) for c in range(W)]
        lines.append(f"{r+1:>2}  " + " ".join(row))
    return "\n".join(lines)


def tally(d):
    """统计行——唯一口径，回话里别手算（手算漏过旗格，报过错数）。

    「未翻」= 所有没翻开的格（插旗的也算）；「剩余空格」= 总格 − 雷 − 已开。
    """
    opened = set(d["opened"])
    flag_n = len(set(d["flagged"]))
    return (f"未翻 {W * H - len(opened)}（含旗 {flag_n}）"
            f"｜剩余空格 {W * H - len(d['mines']) - len(opened)}"
            f"｜雷 {len(d['mines'])}")


# ---------- 图片渲染（QQ 等移动端等宽字会歪列，图不受客户端字体影响） ----------
CJK_FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
NUM_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
COL = {"cover": (152, 152, 152), "hi": (188, 188, 188), "lo": (104, 104, 104),
       "open": (233, 233, 233), "line": (110, 110, 110), "bg": (250, 250, 250),
       "ink": (30, 30, 30), "flag": (198, 32, 32), "mine": (26, 26, 26),
       "hit": (206, 58, 58), "cap": (66, 66, 66)}
NUM_COL = {1: (28, 62, 200), 2: (20, 118, 40), 3: (194, 40, 40), 4: (72, 40, 132),
           5: (140, 62, 20), 6: (20, 128, 128), 7: (45, 45, 45), 8: (108, 108, 108)}


def _font(path, size):
    from PIL import ImageFont
    return (ImageFont.truetype(path, size) if os.path.exists(path)
            else ImageFont.load_default())


def _png_text(dr, box, text, font, fill):
    """在 box=(x0,y0,x1,y1) 里居中画一行字。"""
    x0, y0, x1, y1 = box
    bb = dr.textbbox((0, 0), text, font=font)
    dr.text((x0 + (x1 - x0 - (bb[2] - bb[0])) / 2 - bb[0],
             y0 + (y1 - y0 - (bb[3] - bb[1])) / 2 - bb[1]), text, font=font, fill=fill)


def _png_mine(dr, x0, y0, c, ss, stepped):
    cx, cy, rad = x0 + c / 2, y0 + c / 2, c * 0.25
    col = (255, 255, 255) if stepped else COL["mine"]
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                   (.7, .7), (-.7, .7), (.7, -.7), (-.7, -.7)):
        dr.line([cx - dx * c * .30, cy - dy * c * .30,
                 cx + dx * c * .40, cy + dy * c * .40],
                fill=col, width=max(2, int(2.2 * ss)))
    dr.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col)
    dr.ellipse([cx - rad * .45, cy - rad * .62, cx - rad * .05, cy - rad * .20],
               fill=(255, 200, 200) if stepped else (235, 235, 235))


def _png_flag(dr, x0, y0, c, ss):
    cx = x0 + c * .46
    top, bot = y0 + c * .20, y0 + c * .78
    dr.line([cx, top, cx, bot], fill=(40, 40, 40), width=max(2, int(2.4 * ss)))
    dr.line([cx - c * .17, bot, cx + c * .17, bot],
            fill=(40, 40, 40), width=max(2, int(2.4 * ss)))
    dr.polygon([(cx, top), (cx + c * .30, top + c * .17), (cx, top + c * .34)],
               fill=COL["flag"])


def render_png(d, out, hit=None, reveal_all=False, cell=64, ss=2):
    """盘面画成 PNG（图自带统计行，摊牌多一行图例）。

    格子：立体未开 / 浅底已开 / 数字用经典扫雷配色 / 红旗 / 黑雷；
    踩中的那颗是红底白雷。图不受客户端字体影响，手机上不歪列。
    """
    from PIL import Image, ImageDraw
    WW, HH = d["W"], d["H"]
    c = cell * ss
    pad_l, pad_t, pad_r, pad_b = 50 * ss, 44 * ss, 20 * ss, 16 * ss
    caps = [tally(d)]
    if reveal_all:
        caps.append("摊开的雷＝灰底黑雷｜踩中的那颗＝红底白雷")
    f_cap, f_lab = _font(CJK_FONT, 19 * ss), _font(NUM_FONT, 20 * ss)
    f_num = _font(NUM_FONT, int(c * .58))
    line_h = 27 * ss
    img = Image.new("RGB", (pad_l + WW * c + pad_r,
                            pad_t + HH * c + pad_b + line_h * len(caps)),
                    COL["bg"])
    dr = ImageDraw.Draw(img)
    mines, opened, flagged = set(d["mines"]), set(d["opened"]), set(d["flagged"])

    for r in range(HH):
        for cc in range(WW):
            x0, y0 = pad_l + cc * c, pad_t + r * c
            x1, y1 = x0 + c, y0 + c
            is_mine, is_open = (r, cc) in mines, (r, cc) in opened
            stepped = is_mine and (is_open or hit == (r, cc))
            shown = stepped or (is_mine and reveal_all)
            if is_open and not is_mine:
                dr.rectangle([x0, y0, x1 - 1, y1 - 1], fill=COL["open"],
                             outline=COL["line"], width=ss)
            elif stepped:
                dr.rectangle([x0, y0, x1 - 1, y1 - 1], fill=COL["hit"],
                             outline=COL["line"], width=ss)
            else:
                dr.rectangle([x0, y0, x1 - 1, y1 - 1], fill=COL["cover"],
                             outline=COL["line"], width=ss)
                dr.line([x0 + ss, y0 + ss, x1 - ss, y0 + ss], fill=COL["hi"], width=ss)
                dr.line([x0 + ss, y0 + ss, x0 + ss, y1 - ss], fill=COL["hi"], width=ss)
                dr.line([x1 - ss, y0 + ss, x1 - ss, y1 - ss], fill=COL["lo"], width=ss)
                dr.line([x0 + ss, y1 - ss, x1 - ss, y1 - ss], fill=COL["lo"], width=ss)
            if shown:
                _png_mine(dr, x0, y0, c, ss, stepped)
            elif is_open:
                n = d["counts"][f"{r},{cc}"]
                if n:
                    _png_text(dr, (x0, y0, x1, y1), str(n), f_num,
                              NUM_COL.get(n, COL["ink"]))
            elif (r, cc) in flagged:
                _png_flag(dr, x0, y0, c, ss)

    for cc in range(WW):
        _png_text(dr, (pad_l + cc * c, pad_t - 32 * ss, pad_l + (cc + 1) * c, pad_t),
                  chr(ord("A") + cc), f_lab, COL["ink"])
    for r in range(HH):
        _png_text(dr, (0, pad_t + r * c, pad_l - 8 * ss, pad_t + (r + 1) * c),
                  str(r + 1), f_lab, COL["ink"])
    for i, t in enumerate(caps):
        y = pad_t + HH * c + pad_b + i * line_h
        _png_text(dr, (0, y, img.width, y + line_h), t, f_cap, COL["cap"])

    if ss != 1:
        img = img.resize((img.width // ss, img.height // ss), Image.LANCZOS)
    img.save(out)
    return out


def emit(d, opts, hit=None):
    """出盘面（唯一出口）：盘面 + 统计行 +（摊牌时）图例；给了 --png 再落一张图。"""
    print(render(d, hit, opts["reveal_all"], opts["style"]))
    print(tally(d))
    if opts["reveal_all"]:
        print("💣 摊开的雷（没踩中）｜💥 踩中的那颗")
    if opts.get("png"):
        render_png(d, opts["png"], hit, opts["reveal_all"])
        print("图已生成:", opts["png"])


# ---------- 公平自证 ----------
def check_lines(d):
    """自洽校验：已翻开格的数字必须等于按隐藏雷位重算的值。返回文本行（close 也要照抄）。"""
    mines = set(d["mines"])
    bad = []
    for r, c in d["opened"]:
        if (r, c) in mines:
            bad.append((r, c, "是雷却被翻开"))
            continue
        n = sum(1 for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                if (dr or dc) and (r + dr, c + dc) in mines)
        if n != d["counts"][f"{r},{c}"]:
            bad.append((r, c, n, d["counts"][f"{r},{c}"]))
    left = W * H - len(d['opened'])
    return [f"已翻开 {len(d['opened'])} 格 | 不自洽 {len(bad)} 处 {bad[:5]}",
            f"雷数 {len(mines)} | 全在盘内 "
            f"{all(0 <= r < H and 0 <= c < W for r, c in mines)}",
            f"未翻开 {left} 格（其中插旗 {len(d['flagged'])} 面）"]


def cmd_check(d, state):
    print("\n".join(check_lines(d)))


def reveal_lines(state):
    """摊牌文本：盐 + 答案 + 自己独立复算一遍哈希（自证的最后一环，别手算）。"""
    path = commit_path(state)
    if not os.path.exists(path):
        sys.exit("没有承诺文件，先 commit")
    cm = json.load(open(path, encoding="utf-8"))
    again = hashlib.sha256(
        (cm["salt"] + "|" + json.dumps(cm["mines"])).encode()).hexdigest()
    return [f"盐 : {cm['salt']}",
            f"雷位: {cm['mines']}",
            f"公布哈希: {cm['hash']}",
            f"独立复算: {again} " + ("→ 一致 ✓" if again == cm["hash"] else "→ 对不上 ✗")]


def cmd_reveal(state):
    print("\n".join(reveal_lines(state)))


# ---------- 收桌（存档 + INDEX + 台账，一条命令） ----------
RESULT_CN = {"clear": "全清", "boom": "踩雷"}
META_RE = re.compile(r"<!-- meta (\{.*?\}) -->")

INDEX_HEAD = """# 扫雷对局存档

> 一盘一档：`<日期>-<盘型>-<结果>-阁下.md`，同名 `.json` 是原始状态文件（含终局雷位），
> `.commit.json` 是那盘的公平承诺（盐 + 雷位，终局才公开）。
> 引擎与规矩：`skills/chat-game-referee/games/minesweeper/rules.md`
> **收桌**：`python3 skills/chat-game-referee/scripts/minesweeper.py close` —— 本 INDEX 由它自动重建，别手改。

## 对局

| 日期 | 盘型 | 结果 | 手数 | 旗 |
|------|------|------|------|-----|
"""

INDEX_TAIL = """
## 存档规矩

- 终局（BOOM 踩雷 / CLEAR 全清）才收；`close` 把 `temp/minesweeper.json` 与
  `temp/minesweeper.commit.json` 拷成一档，并重建本页。
- **`temp/minesweeper.json` 只活在当前这盘**——开新盘就被覆盖，要留必须先收桌。
- 终局盘面 / 自洽校验 / 承诺复算一律由 `close` 从盘面与 `check`/`reveal` 直接生成，**别手写**。
- 口径：本天使执盘，胜负按盘算——**阁下踩雷 = 本天使胜，阁下全清 = 本天使负**（台账照这个记）。
- **答案（雷位）只在这份存档、`.commit.json` 与 `temp/` 的活盘里**——对局进行中别把盘写进
  任何长期文件（skill / md / memory）。
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


def end_state(d):
    """终局判定：踩雷 / 全清 / None（还没完）。扫雷只有这两个终局，没有「弃局」。"""
    mines = set(d["mines"])
    opened = set(d["opened"])
    if opened & mines:
        return "boom"
    if len(opened) == W * H - len(mines):
        return "clear"
    return None


def build_archive_md(d, state, slug, end):
    """一盘的账写成给人看的 md。盘面 / 校验 / 复算全部照抄引擎自己的输出，一手不手写。"""
    density = len(d["mines"]) * 100 / (d["W"] * d["H"])
    res_cn = RESULT_CN[end]
    tail = "（阁下把盘清干净了）" if end == "clear" else "（阁下踩在雷上）"
    meta = {"slug": slug, "date": slug[:10], "game": GAME_NAME, "mode": f"{d['W']}x{d['H']}",
            "result": res_cn, "end": end, "winner": "liya" if end == "boom" else "player",
            "moves": d.get("steps", 0), "opened": len(d["opened"]),
            "flags": len(d["flagged"]), "mines": len(d["mines"]), "opponent": OPPONENT}
    L = [f"# 扫雷对局存档 · {slug[:10]}", "",
         f"<!-- meta {json.dumps(meta, ensure_ascii=False)} -->", "",
         f"**盘型** {d['W']}×{d['H']}（{len(d['mines'])} 雷，密度 {density:.1f}%）"
         f"｜**手数** {d.get('steps', 0)}（开格 {len(d['opened'])} ｜ 旗 {len(d['flagged'])}）",
         f"**结果：{res_cn}**{tail}", "",
         "## 终局盘面（照抄 `show --reveal-all`）", "", "```",
         render(d, reveal_all=True), tally(d),
         "💣 摊开的雷（没踩中）｜💥 踩中的那颗", "```", "",
         "## 公平自证（照抄 `check` / `reveal`）", "", "```"]
    L += check_lines(d)
    L += ["```", ""]
    if os.path.exists(commit_path(state)):
        L += ["```"] + reveal_lines(state) + ["```", ""]
    else:
        L += ["- 这盘没做承诺（没跑过 `commit`）——公平自证只有上面的自洽校验。", ""]
    L += ["## 口径", "",
          "- 本天使执盘（裁判），胜负按盘算：**阁下踩雷 = 本天使胜，阁下全清 = 本天使负**。",
          "- 首点安全区：`new <首点>` 会把首点及其八邻排除在布雷池外——写清楚就算公平。",
          "- 原始状态文件（含终局雷位）：同目录同名 `.json`；公平承诺：同名 `.commit.json`。"]
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
    out = [INDEX_HEAD.rstrip("\n")]
    for meta, fn in rows:
        out.append(f"| {meta.get('date','')} | {meta.get('mode','')} | {meta.get('result','')} "
                   f"| {meta.get('moves','')} | {meta.get('flags','')} |")
    out.append("")
    for meta, fn in rows:
        base = fn[:-3]
        links = [f"[{meta.get('date','')} · {meta.get('mode','')} · {meta.get('result','')}]({fn})",
                 f"[原始状态 json]({base}.json)"]
        if os.path.exists(os.path.join(archive, base + ".commit.json")):
            links.append(f"[公平承诺]({base}.commit.json)")
        out.append("- " + "｜".join(links))
    out.append(INDEX_TAIL)
    with open(os.path.join(archive, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return len(rows)


def _ledger_script():
    for c in (LEDGER_SCRIPT, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.py")):
        if os.path.exists(c):
            return c
    return None


def cmd_close(state, opts):
    d = load(state)
    set_dims(d["W"], d["H"], len(d["mines"]))
    end = end_state(d)
    if not end:
        sys.exit("还没收桌——踩雷或全清才收（没终局的盘不存档）。")
    if d.get("closed_as"):
        sys.exit(f"这盘已经收过了（{d['closed_as']}）——要再收先 new 开新盘。")
    res_cn = RESULT_CN[end]
    archive = opts["archive"]
    base_slug = (f"{datetime.date.today().isoformat()}-{d['W']}x{d['H']}-{res_cn}-{OPPONENT}")
    slug, i = base_slug, 2
    while os.path.exists(os.path.join(archive, slug + ".md")):   # 同一天连开两盘不许互相盖
        slug, i = f"{base_slug}-{i}", i + 1
    os.makedirs(archive, exist_ok=True)
    dst_json = os.path.join(archive, slug + ".json")
    shutil.copyfile(state, dst_json)
    src_cp, dst_cp = commit_path(state), os.path.join(archive, slug + ".commit.json")
    if os.path.exists(src_cp):
        shutil.copyfile(src_cp, dst_cp)
    md_path = os.path.join(archive, slug + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(build_archive_md(d, state, slug, end))
    n = rebuild_index(archive)
    d["closed_as"] = slug          # 落了戳：同一盘再收会被挡，免得重复记账
    save(d, state)

    print(f"  收桌：{d['W']}×{d['H']} · {res_cn} · {d.get('steps', 0)} 手")
    print(f"  存档：{_rel_or_abs(dst_json)}")
    print(f"        {_rel_or_abs(md_path)}")
    if os.path.exists(dst_cp):
        print(f"        {_rel_or_abs(dst_cp)}")
    print(f"  INDEX：{_rel_or_abs(os.path.join(archive, 'INDEX.md'))}（按存档重建，现有 {n} 档）")

    ledger = None if opts.get("no_ledger") else opts.get("ledger")
    if ledger:
        script = _ledger_script()
        if not script:
            print("  台账：找不到 ledger.py，跳过（存档已落）。")
            return
        note = opts.get("note") or (f"裁判局：{d['W']}×{d['H']} · 阁下{res_cn}"
                                    f"（{d.get('steps', 0)} 手）")
        r = subprocess.run([sys.executable, script, "--ledger", ledger, "add", GAME_NAME,
                            "win" if end == "boom" else "loss",
                            "--moves", str(d.get("steps", 0)), "--note", note,
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


def cmd_hint(d):
    """口子清单：哪些已开数字周围「还没开的格子」最少。

    只给形状（哪个数字、还压着几格），不给结论（是雷/是安全一律不说）——
    跟裁判手写「口子在哪」同一个东西，只是自动化、不漏不数错。
    """
    mines = set(d["mines"])
    opened = set(d["opened"])
    flagged = set(d["flagged"])
    rows = []
    for (r, c) in d["opened"]:
        if (r, c) in mines:
            continue
        n = d["counts"][f"{r},{c}"]
        if not n:
            continue
        unknown = [(r + dr, c + dc)
                   for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                   if (dr or dc) and 0 <= r + dr < H and 0 <= c + dc < W
                   and (r + dr, c + dc) not in opened
                   and (r + dr, c + dc) not in flagged]
        if unknown:
            rows.append((len(unknown), n, (r, c)))
    if not rows:
        print("口子：没有边界数字可看（盘清完了，或者还没开出面）")
        return
    rows.sort()
    tight = [x for x in rows if x[0] <= 2] or rows[:3]
    print("口子（没开邻居最少的数字，只给形状）：")
    for k, n, rc in tight:
        print(f"  {name_of(rc)}（数字{n}）— 周围还有 {k} 格没开")
    rest = len(rows) - len(tight)
    if rest:
        print(f"  其余 {rest} 个边界数字都还压着 3 格以上，先不用看")


def commit_path(state):
    """minesweeper.json -> minesweeper.commit.json（跟 references 里写的一致）。"""
    return (state[:-5] if state.endswith(".json") else state) + ".commit.json"


def cmd_commit(d, state):
    path = commit_path(state)
    if os.path.exists(path):
        h = json.load(open(path, encoding="utf-8"))["hash"]
        print("已有承诺(未公开):", h)
        return
    salt = secrets.token_hex(16)
    payload = salt + "|" + json.dumps(sorted(list(m) for m in d["mines"]))
    h = hashlib.sha256(payload.encode()).hexdigest()
    json.dump({"algo": "sha256(盐|雷位)", "salt": salt,
               "mines": sorted(list(m) for m in d["mines"]), "hash": h},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("承诺已封存，可公布:", h)
    print("盐与雷位封在", path, "，终局用 reveal 摊开。")


# ---------- 动作 ----------
def main():
    pos, opts = parse_argv(sys.argv[1:])
    state = opts["state"]
    act = pos[0] if pos else "show"
    if opts["png"] is True:  # --png 省了路径：写 state 同名 .png，每手覆盖同一张
        opts["png"] = (state[:-5] if state.endswith(".json") else state) + ".png"

    if act == "new":
        apply_preset(opts)
        set_dims(opts["w"], opts["h"], opts["mines"])
        if W > 26:
            sys.exit(f"列数 {W} 超上限 26：坐标只有 A–Z 单字母，再宽这 CLI 标不出来"
                     f"（QQ 上 16 列以上也基本没法看）")
        old_cp = commit_path(state)
        if os.path.exists(old_cp):  # 旧盘已终局，残留的承诺文件不清理会让新盘 commit 直接被跳过
            os.remove(old_cp)
            print(f"旧的公平承诺文件已清掉（上一盘已终局）")
        first = parse(pos[1]) if len(pos) > 1 else None
        d = new_board(first)
        if first:
            flood(d, *first)
        save(d, state)
        emit(d, opts)
        print(f"\n新盘已存 {state}")
        print(f"盘面 {W}x{H}｜雷 {NMINES}｜{NMINES * 100 / (W * H):.1f}%")
        return

    if act == "reveal":
        cmd_reveal(state)
        return

    if act == "close":
        cmd_close(state, opts)
        return

    d = load(state)
    set_dims(d["W"], d["H"], len(d["mines"]))

    if act == "open":
        cell = parse(pos[1])
        if cell in set(d["opened"]):
            emit(d, opts)
            print(f"\n该格本来就开着，没动。剩余空格 "
                  f"{W * H - len(d['mines']) - len(set(d['opened']))}。")
            return
        if cell in set(d["flagged"]):
            emit(d, opts)
            print(f"\n{pos[1].upper()} 上插着旗，要开先「flag {pos[1].upper()}」撤旗。没动。")
            return
        if cell in set(d["mines"]):
            d["opened"] = sorted(set(d["opened"]) | {cell})
            d["steps"] = d.get("steps", 0) + 1
            save(d, state)
            emit(d, opts, hit=cell)
            print("\nBOOM 踩雷")
            if os.path.exists(commit_path(state)):  # 终局了：有承诺文件就顺手摊牌，别让玩家还要再来一句
                print()
                cmd_reveal(state)
            return
        flood(d, *cell)
        d["steps"] = d.get("steps", 0) + 1
        save(d, state)
        emit(d, opts)
        if W * H - len(d["mines"]) - len(set(d["opened"])) == 0:
            print("\nCLEAR 全清")
    elif act == "flag":
        f = set(d["flagged"])
        f.symmetric_difference_update({parse(pos[1])})
        d["flagged"] = sorted(f)
        d["steps"] = d.get("steps", 0) + 1
        save(d, state)
        emit(d, opts)
    elif act == "check":
        cmd_check(d, state)
    elif act == "hint":
        cmd_hint(d)
    elif act == "commit":
        cmd_commit(d, state)
    else:
        emit(d, opts)


if __name__ == "__main__":
    main()

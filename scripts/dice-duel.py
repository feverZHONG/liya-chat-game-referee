#!/usr/bin/env python3
"""掷骰决斗 CLI（收编自 QQ 版 qq-dice-duel）：双方各掷两枚六面骰，点数和大的赢这一掷，
平局不计分、接着掷；先赢 3 掷者胜。骰子由引擎按种子掷出——双方都看得见，没有暗牌；
公平靠「盐值承诺 + 种子复算」自证，不靠嘴。

用法:
  python3 dice-duel.py new      # 开局：封存盐+种子，打印可公布的 hash
  python3 dice-duel.py roll     # 掷一手（双方各两枚）：比分 + 这一掷双方点数 + 一句话形状
  python3 dice-duel.py show     # 当前局面（比分 + 逐掷经过）
  python3 dice-duel.py check    # 自洽校验 + 独立复算 hash 与全部骰谱（公平自证）
  python3 dice-duel.py reveal   # 摊开盐+种子（终局摊；局中摊＝这局作废）
  python3 dice-duel.py close    # 收桌一条龙：存档 + INDEX 重建 + 台账 + git 提示（终局才收）
  python3 dice-duel.py help     # 这张用法

骰面怎么来的（谁都能重算）：第 n 掷、某一方的第 i 枚骰 =
  sha256("<种子>|n|<player|liya>|i") 取首字节 %6+1，i ∈ {0,1}。
种子与盐封在 <根>/temp/dice-duel.commit.json，只有 hash 在开局时公布；
终局 `reveal` 摊开，阁下可自己把每一掷重算一遍。**承诺是发骰的前提**——
没有承诺文件就不发骰（`new` 一定先封存），所以「先公布后掷」不是嘴上说的。

本引擎只吐事实（点数 / 比分 / 局面），台词由本天使现编——老脚本那套预设台词池已删。
难度参数也已删：它在老脚本里从不影响任何结果，是个空壳选项。

可用 --state /path/to/xxx.json 换状态文件（默认=<数据根>/temp/dice-duel.json）。
--archive / --ledger 换归档与台账路径（close 用），--note 写台账备注，--no-ledger 只存档。
数据根：$GAME_HOME ＞ 往上找带 temp/ 或 workspace/ 的一层 ＞ 脚本上一级。
引擎放哪都能跑。别手改 json——改了 `check` 会当场指出来。
"""
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys

SCORE_TO_WIN = 3
DICE_SIDES = 6
PLAYER = "阁下"
ANGEL = "本天使"
SIDES = ("player", "liya")
SIDE_CN = {"player": PLAYER, "liya": ANGEL}
FACE = "\u2680\u2681\u2682\u2683\u2684\u2685"   # ⚀⚁⚂⚃⚄⚅

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
DEFAULT_STATE = os.path.join(STATE_DIR, "dice-duel.json")
DEFAULT_ARCHIVE = os.path.join(RECORDS_DIR, "dice-duel")
DEFAULT_LEDGER = os.path.join(RECORDS_DIR, "game-ledger", "ledger.json")
LEDGER_SCRIPT = os.path.join(HERE, "ledger.py")
GAME_NAME = "掷骰决斗"
OPPONENT = "阁下"
RESULT_CN = {"player": f"{PLAYER}胜", "liya": f"{ANGEL}胜"}

USAGE = """掷骰决斗 · 命令
  new                          开局（封存盐+种子，打印可公布的 hash）
  roll                         掷一手（双方各两枚六面骰，和大的赢这一掷）
  show                         当前局面（比分 + 逐掷经过）
  check                        自洽校验 + 独立复算 hash 与全部骰谱
  reveal                       摊开盐+种子（终局摊；局中摊＝这局作废）
  close                        收桌：存档 + INDEX 重建 + 台账 + git 提示（终局才收）

规则：先赢 3 掷者胜，平局不计分、接着掷。骰子由引擎掷（双方都看得见），
     承诺与种子复算见 check / reveal。new 不带参数（本玩法没有难度）。
覆盖开关：--state 路径 / --archive 路径 / --ledger 路径 / --note 文本 / --no-ledger
"""


# ---------- 参数 ----------
DEFAULTS = {"state": DEFAULT_STATE, "archive": DEFAULT_ARCHIVE,
            "ledger": DEFAULT_LEDGER, "note": None, "no_ledger": False}


def parse_argv(argv):
    opts, pos, i = dict(DEFAULTS), [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--state", "--archive", "--ledger", "--note"):
            if i + 1 >= len(argv):
                sys.exit(f"{a} 后面缺参数")
            opts[a[2:]] = argv[i + 1]
            i += 2
            continue
        if a.startswith(("--state=", "--archive=", "--ledger=", "--note=")):
            k, v = a[2:].split("=", 1)
            opts[k] = v
            i += 1
            continue
        if a == "--no-ledger":
            opts["no_ledger"] = True
            i += 1
            continue
        pos.append(a)
        i += 1
    return pos, opts


# ---------- 状态 ----------
def load(state):
    if not os.path.exists(state):
        sys.exit(f"没有这盘棋: {state}（先 new 开一盘，或看 {STATE_DIR} 下有什么）")
    with open(state, encoding="utf-8") as f:
        d = json.load(f)
    d.setdefault("history", [])
    d.setdefault("rolls", 0)
    return d


def save(d, state):
    """落盘：显式列 key（家族坑）——新加的字段忘了列进这里会被静默吞掉。
    先 makedirs：空数据根第一盘 new 不许崩。"""
    os.makedirs(os.path.dirname(os.path.abspath(state)), exist_ok=True)
    with open(state, "w", encoding="utf-8") as f:
        json.dump({
            "game": GAME_NAME,
            "target": SCORE_TO_WIN,
            "player_score": d["player_score"],
            "liya_score": d["liya_score"],
            "rolls": d["rolls"],
            "history": d["history"],
            "over": d.get("over", 0),
            "winner": d.get("winner"),
            "void": d.get("void", 0),
            "closed_as": d.get("closed_as"),
        }, f, ensure_ascii=False, indent=1)


def commit_path(state):
    """dice-duel.json -> dice-duel.commit.json。"""
    return (state[:-5] if state.endswith(".json") else state) + ".commit.json"


def read_commit(state, hard=True):
    path = commit_path(state)
    if not os.path.exists(path):
        if hard:
            sys.exit(f"没有承诺文件: {path}\n"
                     f"承诺是发骰的前提（先封存、后掷），丢了就重开一盘：new。")
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def hash_of(salt, seed):
    return hashlib.sha256((salt + "|" + seed).encode()).hexdigest()


def derive_pair(seed, n, side):
    """第 n 掷、某一方的两枚骰：sha256("<种子>|n|side|i") 取首字节 %6+1。"""
    pair = []
    for i in (0, 1):
        dig = hashlib.sha256(f"{seed}|{n}|{side}|{i}".encode()).digest()
        pair.append(dig[0] % DICE_SIDES + 1)
    return pair


def faces(pair, sep=" "):
    return sep.join(FACE[x - 1] for x in pair)


def ties_of(d):
    return sum(1 for h in d["history"] if h["result"] == "tie")


def state_cn(d):
    if d.get("void"):
        return "已作废（局中摊过牌）"
    if d.get("over"):
        return "终局 · " + RESULT_CN[d["winner"]]
    return "进行中"


# ---------- 渲染（只给事实，不给台词） ----------
def score_line(d):
    s = f"比分 {PLAYER} {d['player_score']} : {d['liya_score']} {ANGEL}"
    if d.get("over"):
        return s + f"｜终局 · {RESULT_CN[d['winner']]}"
    return (s + f"｜{PLAYER}还差 {SCORE_TO_WIN - d['player_score']} 掷，"
            f"{ANGEL}还差 {SCORE_TO_WIN - d['liya_score']} 掷")


def render_roll(d):
    """一手一块：这一掷双方点数 + 结果 + 比分与距离。"""
    h = d["history"][-1]
    pl, ly = h["player"], h["liya"]
    L = [f"🎲 掷骰决斗 · 第 {h['n']} 掷",
         f"  {ANGEL} {faces(ly)} = {sum(ly)}",
         f"  {PLAYER}   {faces(pl)} = {sum(pl)}"]
    L.append("  平局，不计分，接着掷" if h["result"] == "tie"
             else f"  {SIDE_CN[h['result']]}拿下这一掷")
    L.append("  " + score_line(d))
    return L


def render_show(d):
    if not d["history"]:
        return [f"🎲 掷骰决斗 · {PLAYER} 0 : 0 {ANGEL}（还没掷过）",
                "  " + state_cn(d) + "｜下一步：roll"]
    ty = ties_of(d)
    L = [f"🎲 掷骰决斗 · 比分 {PLAYER} {d['player_score']} : {d['liya_score']} {ANGEL}"
         f"（共 {d['rolls']} 掷，平局 {ty}）"]
    for h in d["history"]:
        pl, ly = h["player"], h["liya"]
        mark = "平" if h["result"] == "tie" else SIDE_CN[h["result"]]
        L.append(f"  {h['n']:>2}. {PLAYER} {faces(pl, '')} {sum(pl):>2}"
                 f" ｜ {ANGEL} {faces(ly, '')} {sum(ly):>2} → {mark}")
    if d.get("void"):
        L.append("  这局已作废（局中摊过牌）——要接着玩先 new 开新盘")
    elif d.get("over"):
        L.append(f"  终局 · {RESULT_CN[d['winner']]}"
                 f"（reveal 摊盐与种子可自己重算；close 收桌入档）")
    else:
        L.append("  下一步：roll")
    return L


# ---------- 公平自证 ----------
def check_lines(d, state):
    """自洽校验 + 独立复算：哈希、全部骰谱、分数守恒。close 也照抄这份。"""
    hist = d["history"]
    ty = ties_of(d)
    L = [f"手数 {d['rolls']}（有效 {d['rolls'] - ty} ｜ 平局 {ty}）",
         f"比分 {PLAYER} {d['player_score']} : {d['liya_score']} {ANGEL}｜状态 {state_cn(d)}"]
    mark = lambda ok: "一致 ✓" if ok else "对不上 ✗"          # noqa: E731
    faces_ok = all(1 <= x <= DICE_SIDES for h in hist for x in h["player"] + h["liya"])
    L.append(f"骰面 ∈ 1..{DICE_SIDES} → {mark(faces_ok)}")
    wp = sum(1 for h in hist if h["result"] == "player")
    wl = sum(1 for h in hist if h["result"] == "liya")
    scored_ok = (wp == d["player_score"] and wl == d["liya_score"]
                 and 0 <= d["player_score"] <= SCORE_TO_WIN
                 and 0 <= d["liya_score"] <= SCORE_TO_WIN)
    if d.get("over"):
        w = d["winner"]
        other = "liya" if w == "player" else "player"
        scored_ok = (scored_ok and w in SIDES
                     and d[w + "_score"] == SCORE_TO_WIN
                     and d[other + "_score"] < SCORE_TO_WIN)
    L.append(f"分数与骰谱对账（0..{SCORE_TO_WIN}）→ {mark(scored_ok)}"
             f"（骰谱 {PLAYER} {wp} / {ANGEL} {wl}）")
    cm = read_commit(state, hard=False) or {}
    if not cm:
        L.append("承诺文件缺失 → 无法复算 ✗")
        L.append("独立复算 → 对不上 ✗")
        return L
    h_ok = hash_of(cm["salt"], cm["seed"]) == cm["hash"]
    L.append(f"承诺哈希 sha256(盐|种子) → {mark(h_ok)}")
    dice_ok = all(derive_pair(cm["seed"], h["n"], side) == h[side]
                  for h in hist for side in SIDES)
    L.append(f"全部 {d['rolls']} 掷按种子重算 → {mark(dice_ok)}")
    L.append("独立复算 → " + mark(h_ok and dice_ok and faces_ok and scored_ok))
    return L


def reveal_lines(state):
    """摊牌：盐 + 种子 + 自己独立复算一遍哈希（对不上会打 ✗）。"""
    cm = read_commit(state) or {}
    again = hash_of(cm["salt"], cm["seed"])
    return [f"盐  : {cm['salt']}",
            f"种子: {cm['seed']}",
            f"公布哈希: {cm['hash']}",
            f"独立复算: {again} " + ("→ 一致 ✓" if again == cm["hash"] else "→ 对不上 ✗"),
            "复算口径: 第 n 掷某一方的第 i 枚 = sha256(\"<种子>|n|player|liya|i\") "
            "取首字节 %6+1（i=0,1）；双方各两枚，和大的赢这一掷。"]


def cmd_reveal(state):
    d = load(state)
    if d.get("void") and not d.get("over"):
        print("\n".join(reveal_lines(state)))
        print("\n这局早就作废了（局中摊过牌）——作废的盘不收桌，要接着玩先 new。")
        return
    print("\n".join(reveal_lines(state)))
    if not d.get("over"):
        d["void"] = 1
        save(d, state)
        print("\n局中摊牌＝这局作废：种子已经公开，后面每一掷谁都能提前算出来，"
              "接着掷不作数。")
        print("要接着玩，先 new 开新盘（作废的这盘不收桌、不入档）。")


# ---------- 动作 ----------
def cmd_new(state, opts):
    old = None
    if os.path.exists(state):
        try:
            with open(state, encoding="utf-8") as f:
                old = json.load(f)
        except ValueError:
            old = None
    if old and not old.get("over") and not old.get("closed_as") and not old.get("void") \
            and old.get("rolls"):
        print(f"注意：上一盘还没打完（第 {old['rolls']} 掷 · "
              f"{PLAYER} {old.get('player_score')} : {old.get('liya_score')} {ANGEL}）"
              f"就被顶掉了——状态文件会被覆盖。")
    old_cp = commit_path(state)
    if os.path.exists(old_cp):  # 旧盘残留的承诺不清掉，新盘的种子就不算「先封后掷」
        os.remove(old_cp)
        print("旧的公平承诺文件已清掉（上一盘结束或作废）")

    salt, seed = secrets.token_hex(16), secrets.token_hex(16)
    h = hash_of(salt, seed)
    cp = commit_path(state)
    os.makedirs(os.path.dirname(os.path.abspath(cp)), exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump({"algo": "sha256(盐|种子)", "salt": salt, "seed": seed, "hash": h},
                  f, ensure_ascii=False, indent=1)
    d = {"player_score": 0, "liya_score": 0, "rolls": 0, "history": [],
         "over": 0, "winner": None, "void": 0, "closed_as": None}
    save(d, state)

    print(f"🎲 掷骰决斗 · 开局（各掷两枚六面骰，先赢 {SCORE_TO_WIN} 掷者胜，平局不计分）")
    print(f"  承诺已封存，可公布: {h}")
    print(f"  骰面由种子确定性推出，终局 reveal 摊开盐与种子，阁下可把每一掷自己重算一遍。")
    print(f"  状态：{_rel_or_abs(state)}")
    print(f"  承诺：{_rel_or_abs(cp)}（终局前别公开）")
    print(f"  下一步：roll 掷一手。")


def cmd_roll(state):
    d = load(state)
    if d.get("void"):
        sys.exit("这局已作废（局中摊过牌）——要接着玩先 new 开新盘。")
    if d.get("over"):
        sys.exit(f"这局已经打完了（{RESULT_CN[d['winner']]}）——要再来一盘先 new。")
    cm = read_commit(state)          # 承诺先在，才发骰
    n = d["rolls"] + 1
    pl = derive_pair(cm["seed"], n, "player")
    ly = derive_pair(cm["seed"], n, "liya")
    ps, ls = sum(pl), sum(ly)
    res = "player" if ps > ls else ("liya" if ls > ps else "tie")
    if res != "tie":
        d[res + "_score"] += 1
    d["history"].append({"n": n, "player": pl, "liya": ly, "result": res})
    d["rolls"] = n
    if res != "tie" and d[res + "_score"] >= SCORE_TO_WIN:
        d["over"] = 1
        d["winner"] = res
    save(d, state)
    print("\n".join(render_roll(d)))
    if d["over"]:                    # 终局顺手摊牌，别让阁下还要再来一句
        print()
        print("\n".join(reveal_lines(state)))


# ---------- 收桌（存档 + INDEX + 台账，一条命令） ----------
META_RE = re.compile(r"<!-- meta (\{.*?\}) -->")

INDEX_HEAD = """# 掷骰决斗对局存档

> 一盘一档：`<日期>-先3胜-<结果>-阁下.md`，同名 `.json` 是原始状态文件（含逐掷骰谱），
> `.commit.json` 是那盘的公平承诺（盐 + 种子，终局才公开）。
> 引擎与规矩：`skills/chat-game-referee/games/dice-duel/rules.md`
> **收桌**：`python3 skills/chat-game-referee/scripts/dice-duel.py close` —— 本 INDEX 由它自动重建，别手改。

## 对局

| 日期 | 盘制 | 结果 | 手数 | 平局 |
|------|------|------|------|------|
"""

INDEX_TAIL = """
## 存档规矩

- 终局（先赢 3 掷）才收；`close` 把 `temp/dice-duel.json` 与
  `temp/dice-duel.commit.json` 拷成一档，并重建本页。
- **`temp/dice-duel.json` 只活在当前这盘**——开新盘就被覆盖，要留必须先收桌。
- 逐掷经过 / 自洽校验 / 承诺复算一律由 `close` 从状态与 `check`/`reveal` 直接生成，**别手写**。
- 口径：引擎执骰，胜负按盘算——**阁下先赢 3 掷 = 本天使负，本天使先赢 3 掷 = 阁下负**（台账照这个记）。
- 局中 `reveal` ＝这局作废，不收桌、不入档（种子公开后接着掷不作数）。
- **种子只在这份存档、`.commit.json` 与 `temp/` 的活盘里**——对局进行中别把种子写进
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
    """终局谓词：先赢 SCORE_TO_WIN 掷的那方胜；没完 → None（本玩法没有和棋）。"""
    return d.get("winner") if d.get("over") and d.get("winner") in SIDES else None


def build_archive_md(d, state, slug):
    """一盘的账写成给人看的 md。逐掷经过 / 校验 / 复算全部照抄引擎自己的输出。"""
    res_cn = RESULT_CN[d["winner"]]
    ty = ties_of(d)
    meta = {"slug": slug, "date": slug[:10], "game": GAME_NAME,
            "mode": f"先{SCORE_TO_WIN}胜", "result": res_cn,
            "end": "win" if d["winner"] == "liya" else "loss",
            "winner": d["winner"], "moves": d["rolls"],
            "score": f"{d['player_score']}:{d['liya_score']}", "ties": ty,
            "opponent": OPPONENT}
    L = [f"# 掷骰决斗存档 · {slug[:10]}", "",
         f"<!-- meta {json.dumps(meta, ensure_ascii=False)} -->", "",
         f"**盘制** 各掷两枚六面骰（先赢 {SCORE_TO_WIN} 掷者胜）"
         f"｜**手数** {d['rolls']}（有效 {d['rolls'] - ty} ｜ 平局 {ty}）",
         f"**结果：{res_cn}**（比分 {PLAYER} {d['player_score']} : "
         f"{d['liya_score']} {ANGEL}）", "",
         "## 逐掷经过（照抄 `show`）", "", "```"]
    L += render_show(d)
    L += ["```", "", "## 公平自证（照抄 `check` / `reveal`）", "", "```"]
    L += check_lines(d, state)
    L += ["```", ""]
    if os.path.exists(commit_path(state)):
        L += ["```"] + reveal_lines(state) + ["```", ""]
    else:
        L += ["- 这盘的承诺文件丢了——公平自证只有上面那些。", ""]
    L += ["## 口径", "",
          "- 引擎执骰（双方骰子都由种子掷出，全程明牌），胜负按盘算："
          f"**{PLAYER}先赢 {SCORE_TO_WIN} 掷 = 本天使负，"
          f"{ANGEL}先赢 {SCORE_TO_WIN} 掷 = {PLAYER}负**。",
          "- 骰面可复算：第 n 掷某方第 i 枚 = sha256(\"<种子>|n|player|liya|i\") 取首字节 %6+1。",
          "- 平局不计分、不换种子，接着掷（同一副骰子的第 n+1 掷）。",
          "- 原始状态文件（含逐掷骰谱）：同目录同名 `.json`；公平承诺：同名 `.commit.json`。"]
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
                   f"| {meta.get('moves','')} | {meta.get('ties','')} |")
    out.append("")
    for meta, fn in rows:
        base = fn[:-3]
        links = [f"[{meta.get('date','')} · {meta.get('score','')} · {meta.get('result','')}]({fn})",
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
    if d.get("void"):
        sys.exit("这局已经作废了（局中摊过牌）——作废的盘不收桌，要留档就 new 重开一盘。")
    end = end_state(d)
    if not end:
        sys.exit("还没收桌——分出胜负才收（没终局的盘不存档）。")
    if d.get("closed_as"):
        sys.exit(f"这盘已经收过了（{d['closed_as']}）——要再收先 new 开新盘。")
    res_cn = RESULT_CN[end]
    archive = opts["archive"]
    base_slug = f"{datetime.date.today().isoformat()}-先{SCORE_TO_WIN}胜-{res_cn}-{OPPONENT}"
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
        f.write(build_archive_md(d, state, slug))
    n = rebuild_index(archive)
    d["closed_as"] = slug          # 落了戳：同一盘再收会被挡，免得重复记账
    save(d, state)

    ty = ties_of(d)
    print(f"  收桌：先{SCORE_TO_WIN}胜 · {res_cn} · {d['rolls']} 掷（平局 {ty}）"
          f" · 比分 {PLAYER} {d['player_score']} : {d['liya_score']} {ANGEL}")
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
        note = opts.get("note") or (f"引擎执骰：先{SCORE_TO_WIN}胜 · "
                                    f"{PLAYER} {d['player_score']} : {d['liya_score']} {ANGEL}"
                                    f"（{d['rolls']} 掷，含平局 {ty}）")
        r = subprocess.run([sys.executable, script, "--ledger", ledger, "add", GAME_NAME,
                            "win" if end == "liya" else "loss",
                            "--moves", str(d["rolls"]), "--note", note,
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


def main():
    pos, opts = parse_argv(sys.argv[1:])
    state = opts["state"]
    act = pos[0] if pos else "show"

    if act in ("help", "-h", "--help"):
        print(USAGE)
        return
    if act == "new":
        if len(pos) > 1:
            sys.exit(f"new 不带参数（本玩法没有难度，那个空壳选项已删）——"
                     f"多了个「{pos[1]}」看不懂。\n{USAGE}")
        cmd_new(state, opts)
        return
    if act == "roll":
        cmd_roll(state)
        return
    if act == "reveal":
        cmd_reveal(state)
        return
    if act == "close":
        cmd_close(state, opts)
        return
    if act == "show":
        print("\n".join(render_show(load(state))))
        return
    if act == "check":
        print("\n".join(check_lines(load(state), state)))
        return
    sys.exit(f"没有这个命令: {act}\n{USAGE}")


if __name__ == "__main__":
    main()

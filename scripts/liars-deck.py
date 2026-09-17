#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""liars-deck.py — 天界骗子牌（Liar's Deck）· 聊天版 1v1

骗子酒馆的卡牌模式：20 张（6Q / 6K / 6A / 2 王），每人 5 张手牌；每局系统定一张
目标牌，轮流出 1–3 张、宣称「全是目标牌」（可真可假）；对方二选一：质疑 / 接牌。
质疑 → 翻牌：有假牌 → 出牌者吃枪；全真 → 质疑者吃枪。左轮 6 仓 1 弹、中弹出局。

规则（细账见 games/liars-deck/rules.md；官方口径 2026-09-15 核对过）：
  手牌出空 → 该方本轮跳过回合；1v1 下只剩对方有牌 → 对方**必须**质疑最后一手，
  谁吃枪看那一手真假（出空的最后一手必须是真的，否则自己吃枪）。
  没有「认了 / 弃权」——官方回合只有「出牌 / 叫 LIAR」两个动作。

公平（本天使既坐庄又下桌，承诺哈希照做）：
  · 游戏级：开局承诺两把左轮的弹仓位置——之后谁都换不了；
  · 每局级：发牌后承诺双方手牌（按发牌那一刻的原始手牌算，出牌后仍可复算）；
  · AI 只看自己的手牌，阁下的手牌不进决策路径（公开出牌史目前也没进）。

蒙眼（2026-09-15 阁下批「甲」+「丙」）：
  · 文字盘面**不打印阁下手牌明文**（只给张数）——本天使全程不知道阁下手里是什么；
    阁下的牌面只走牌桌图（阁下视角，他的牌明）；本天使不 vision 读图、不读状态 json。
  · 每次运行把本天使可见的输出追加进 <state>.audit.log —— 阁下随时可查本天使看到了什么。
  · AI 出牌 / 质疑都带随机 —— 阁下也反推不出本天使手牌结构。

用法：
  python3 scripts/liars-deck.py new [--level 1|2|3] [--force] [--png]
  python3 scripts/liars-deck.py show [--player] [--png]
  python3 scripts/liars-deck.py play 1 3               # 阁下出牌：牌桌图上的编号（1–3 个）→ 引擎当场应手
  python3 scripts/liars-deck.py play QQJ               # 牌面写法仍在，但本天使会看见你出哪几张（不推荐）
  python3 scripts/liars-deck.py open [--png]           # 阁下质疑本天使的上一次出牌
  python3 scripts/liars-deck.py check                  # 公平自证：复算承诺哈希
  python3 scripts/liars-deck.py reveal                 # 摊牌（终局用；局中摊 = 这局作废）
  python3 scripts/liars-deck.py close [--note 文本]     # 收桌一条龙：存档 + INDEX + 台账（终局才收）

顺手（2026-09-15 阁下：「刚刚一系列行动能用一条 CLI 解决」）：
  · **一手一条命令** —— 每次运行都同时打两段：`── 发群` 整块照贴阁下，末行「本天使手牌（暗牌，别贴进群）」
    自用。不必再补跑一次 `show --player` 去挑可贴的那半。
  · **收桌一条命令** —— `close` 把存档（json + 审计日志）、md、INDEX 重建、台账一笔全办完，
    再打一行现成的 git 命令。终局态才收（没收桌会挡）。
"""

import argparse
import datetime
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys

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
DEFAULT_STATE = os.path.join(STATE_DIR, "liars-deck.json")
DEFAULT_ARCHIVE = os.path.join(RECORDS_DIR, "liars-deck")
DEFAULT_LEDGER = os.path.join(RECORDS_DIR, "game-ledger", "ledger.json")
LEDGER_SCRIPT = os.path.join(HERE, "ledger.py")
GAME_NAME = "骗子牌"
OPPONENT = "阁下"
TARGETS = ("Q", "K", "A")
JOKER = "J"          # 王，万能
CARDS_PER_HAND = 5
CHAMBERS = 6
MAX_PLAY = 3


# ---------- 牌 ----------

def full_deck():
    return [c for c in TARGETS for _ in range(6)] + [JOKER, JOKER]


def deal():
    deck = full_deck()
    random.shuffle(deck)
    return sorted(deck[:CARDS_PER_HAND]), sorted(deck[CARDS_PER_HAND:2 * CARDS_PER_HAND])


def parse_cards(raw):
    """认 `play QQJ` / `play Q Q J` / `play 王 王` —— 阁下怎么打都得认。"""
    s = "".join(raw or []).upper().replace("王", "J").replace("JOKER", "J")
    s = re.sub(r"[^QKAJ]", "", s)
    return list(s) or None


def parse_play(raw):
    """出牌解析，两条路：

      · 编号（推荐）：`play 1 3` / `play 13` → 牌桌图上的第几张。
        本天使只看见数字、看不见牌面 —— 出牌这条路也就不漏牌了。
      · 牌面（兼容）：`play QQJ` —— 能出，但命令文本会暴露阁下出的是哪几张。

    返回 ("idx", [1, 3]) / ("face", ["Q","Q","K"])；两种写法混着来 → None（别静默丢数字）。
    """
    s = "".join(raw or []).strip()
    if not s:
        return None
    norm = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if norm.isdigit():
        return "idx", [int(ch) for ch in norm]
    if any(ch.isdigit() for ch in norm):
        return None
    face = parse_cards(raw)
    return ("face", face) if face else None


# ---------- 状态 ----------

def commit_of(salt, *parts):
    raw = "|".join([salt] + [",".join(map(str, p)) if isinstance(p, (list, tuple)) else str(p)
                             for p in parts])
    return hashlib.sha256(raw.encode()).hexdigest()


def load(state):
    if not os.path.exists(state):
        sys.exit("还没有这一桌——先 new。")
    with open(state, encoding="utf-8") as f:
        return json.load(f)


def save(d, state):
    parent = os.path.dirname(state)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(state, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")


def new_round(d, starter):
    """重发牌：每人 5 张 + 新目标牌 + 重新承诺；starter 先出。"""
    d["round"] = d.get("round", 0) + 1
    lh, ph = deal()
    d["dealt"] = {"liya": lh, "player": ph}
    d["liya"]["hand"], d["player"]["hand"] = list(lh), list(ph)
    d["target"] = random.choice(TARGETS)
    d["starter"] = starter
    d["awaiting"] = starter
    d["last_play"] = None
    d["final"] = False
    d["played"] = {"liya": [], "player": []}
    d["salt_r"] = os.urandom(8).hex()
    d["commit_r"] = commit_of(d["salt_r"], lh, ph)


def other(who):
    return "liya" if who == "player" else "player"


def shoot(d, who):
    """对着自己扣扳机：弹仓对上了就出局，否则指针走一格。"""
    p = d[who]
    if p["chamber"] == p["bullet"]:
        p["alive"] = False
        return True
    p["chamber"] = (p["chamber"] + 1) % CHAMBERS
    return False


# ---------- 出牌 / 结算 ----------

def do_play(d, who, cards):
    for c in cards:
        d[who]["hand"].remove(c)
    d["last_play"] = {"by": who, "cards": list(cards)}
    d["played"][who].extend(cards)
    d["awaiting"] = other(who)
    d["final"] = (len(d[who]["hand"]) == 0)   # 出空了 → 只剩对方有牌，对方必须质疑
    return d["final"]


def resolve_challenge(d, challenger):
    """质疑结算。challenger: 'player' | 'liya'。"""
    play = d["last_play"]
    target = d["target"]
    cards = play["cards"]
    fakes = [c for c in cards if c != target and c != JOKER]
    liar = len(fakes) > 0
    loser = play["by"] if liar else challenger
    died = shoot(d, loser)
    res = {"target": target, "cards": cards, "fakes": fakes, "liar": liar,
           "played_by": play["by"], "challenger": challenger, "loser": loser, "died": died}
    d.setdefault("chain", []).append({          # 逐局经过（收桌时写进存档 md）
        "round": d.get("round"), "target": target, "by": play["by"], "cards": list(cards),
        "fakes": list(fakes), "liar": liar, "challenger": challenger,
        "loser": loser, "died": died})
    if died:
        d["over"] = other(loser)
        res["over"] = d["over"]
    else:
        new_round(d, starter=loser)
        res["next_round"] = d["round"]
        res["next_first"] = loser
    return res


# ---------- AI（只吃自己的手牌 + 公开信息） ----------

def ai_p_true(d, k):
    """本天使视角：阁下这 k 张「全是目标牌」的粗概率（超几何近似）。"""
    hand = d["liya"]["hand"]
    target = d["target"]
    ours_t = sum(1 for c in hand if c == target or c == JOKER)
    ours_f = sum(1 for c in hand if c != target and c != JOKER)
    pool_t = max(0, (6 + 2) - ours_t)     # 目标 + 王，剩下 15 张里的数
    pool = 20 - len(hand)
    p = 1.0
    for i in range(k):
        p *= max(0, pool_t - i) / max(1, pool - i)
    return p


def ai_deck_challenge(d, k, level):
    thr = 0.4 if level >= 3 else (0.5 if level == 2 else 0.62)
    thr += random.uniform(-0.05, 0.05)     # 抖动：阁下摸不准确切的质疑门槛
    return ai_p_true(d, k) < thr


def ai_deck_play(d, level):
    """带随机的出牌：张数、真/假配比都从分布里抽 —— 阁下反推不出本天使手牌结构。

    硬约束：这一手若会把牌出空就不能带假牌 —— 1v1 下对手必然强制质疑，
    出空带假 = 自己白吃一枪（官方口径「最后一手必须是真的」）。
    """
    hand = d["liya"]["hand"]
    target = d["target"]
    cap = len(hand)
    reals = [c for c in hand if c == target or c == JOKER]
    fakes = [c for c in hand if c != target and c != JOKER]

    # 掺假路线：真假都有时按档位抽个概率（越高档越敢混）
    if reals and fakes and random.random() < 0.08 * level:
        pool = reals[:] + fakes[:]
        random.shuffle(pool)
        cards = pool[:random.randint(1, min(MAX_PLAY, len(pool)))]
        if len(cards) >= cap:                      # 会出空 → 只能全真才安全
            cards = [c for c in cards if c == target or c == JOKER] or reals[:1]
        return cards or reals[:1]

    if reals:
        hi = MAX_PLAY if level >= 2 else 1
        return reals[:random.randint(1, min(hi, len(reals)))]

    # 全假手：只能诈，张数随机（低档保守）；别一次出空
    n = min(random.randint(1, 2 if level >= 3 else 1), len(fakes))
    if n >= cap > 1:
        n = cap - 1
    return fakes[:n]


def pump(d, note=""):
    """轮到本天使时自动走：质疑 或 出牌；连走直到轮到阁下 / 收桌。"""
    guard = 0
    while not d.get("over") and d.get("awaiting") == "liya" and guard < 30:
        guard += 1
        lp = d.get("last_play")
        if lp and lp["by"] == "player":
            if d.get("final") or ai_deck_challenge(d, len(lp["cards"]), d.get("level", 2)):
                res = resolve_challenge(d, "liya")
                note += "\n  本天使：质疑！" + reveal_text(d, res)
                continue
        cards = ai_deck_play(d, d.get("level", 2))
        do_play(d, "liya", cards)
        note += f"\n  本天使出 {len(cards)} 张，宣称全是「{d['target']}」。"
    return note


# ---------- 渲染 ----------

def render(d, view="liya"):
    """盘面文字。**默认 view='liya' 也只打「可发群」的安全块** —— 本天使的手牌单独缀在末尾、
    标着「别贴进群」，照贴时跳过那一行即可（一手一条命令，不必为挑可贴的半边再补跑一次）。"""
    out = []
    head = f"第 {d.get('round', 1)} 局 · 目标牌「{d.get('target', '?')}」"
    if d.get("over"):
        head += f" · 终局（{'本天使' if d['over'] == 'liya' else '阁下'}赢）"
    elif d.get("final"):
        head += " · 本天使出空了——阁下必须质疑：open"
    else:
        head += f" · 轮到{'阁下' if d.get('awaiting') == 'player' else '本天使'}"
    out.append("  " + head)
    out.append(f"  阁下的手牌：{len(d['player']['hand'])} 张"
               f"{'（空）' if not d['player']['hand'] else ''}"
               f"（牌面+编号都在牌桌图上；出牌敲编号，本天使只看见数字）")
    out.append(f"  本天使的手牌：{len(d['liya']['hand'])} 张（扣着）")
    out.append(f"  左轮：本天使 第 {d['liya']['chamber']} 仓 ｜ 阁下 第 {d['player']['chamber']} 仓"
               f"（各 6 仓 1 弹，位置已承诺）")
    lp = d.get("last_play")
    if lp:
        who = "本天使" if lp["by"] == "liya" else "阁下"
        out.append(f"  上一次出牌：{who} 出 {len(lp['cards'])} 张，宣称「{d['target']}」")
    else:
        out.append("  上一次出牌：——（本局开张）")
    played = d.get("played") or {}
    out.append(f"  本局已出：阁下 {len(played.get('player') or [])} 张"
               f" ｜ 本天使 {len(played.get('liya') or [])} 张（都没摊——开牌才见真章）")
    text = "\n".join(out)
    if view == "liya":
        text += f"\n  本天使手牌（暗牌，别贴进群）：{' '.join(d['liya']['hand']) or '（空）'}"
    return text


CJK_FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
NUM_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
CARD_FILL = {"Q": (240, 228, 200), "K": (226, 214, 196), "A": (238, 224, 214), "J": (222, 214, 236)}


def _font(path, size):
    from PIL import ImageFont
    return (ImageFont.truetype(path, size) if os.path.exists(path)
            else ImageFont.load_default())


def render_png(d, out, reveal=False, cell=76, ss=2):
    """阁下视角的牌桌：阁下的牌明、本天使的牌扣着（reveal 才翻）。"""
    from PIL import Image, ImageDraw
    c = cell * ss
    gap = 18 * ss
    W = max(820, 5 * c + 4 * gap + 120 * ss)
    H = 560 * ss
    bg, ink, soft = (246, 241, 230), (64, 56, 46), (150, 140, 124)
    img = Image.new("RGB", (W, H), bg)
    dr = ImageDraw.Draw(img)
    f_title = _font(CJK_FONT, 30 * ss)
    f_mid = _font(CJK_FONT, 22 * ss)
    f_small = _font(CJK_FONT, 18 * ss)
    f_card = _font(NUM_FONT, int(c * 0.5))
    f_big = _font(CJK_FONT, 40 * ss)

    dr.text((46 * ss, 34 * ss), "天界骗子牌", font=f_title, fill=ink)
    dr.text((W - 46 * ss, 44 * ss), f"第 {d.get('round', 1)} 局", font=f_mid, fill=soft, anchor="ra")
    dr.line([46 * ss, 92 * ss, W - 46 * ss, 92 * ss], fill=(214, 205, 188), width=ss)

    def card(x, y, ch, face_up):
        dr.rounded_rectangle([x, y, x + c, y + c], radius=c * 0.14,
                             fill=CARD_FILL.get(ch, (240, 236, 226)) if face_up else (196, 184, 166),
                             outline=(96, 86, 72), width=ss)
        if face_up:
            dr.text((x + c / 2, y + c / 2), ch, font=f_card, fill=(48, 40, 32), anchor="mm")
        else:
            dr.text((x + c / 2, y + c / 2), "?", font=f_card, fill=(120, 108, 92), anchor="mm")

    dr.text((46 * ss, 116 * ss), f"本天使的手牌 · {len(d['liya']['hand'])} 张", font=f_mid, fill=ink)
    y0 = 150 * ss
    for i, ch in enumerate(d["liya"]["hand"]):
        card(46 * ss + i * (c + gap), y0, ch, reveal)
    y1 = y0 + c + 56 * ss
    dr.text((46 * ss, y1 - 34 * ss), f"阁下的手牌 · {len(d['player']['hand'])} 张", font=f_mid, fill=ink)
    for i, ch in enumerate(d["player"]["hand"]):
        x = 46 * ss + i * (c + gap)
        card(x, y1, ch, True)
        dr.text((x + c / 2, y1 + c + 15 * ss), f"[{i + 1}]", font=f_small, fill=soft, anchor="mm")

    y2 = y1 + c + 40 * ss
    dr.line([46 * ss, y2, W - 46 * ss, y2], fill=(214, 205, 188), width=ss)
    t = d.get("target", "?")
    dr.text((46 * ss, y2 + 26 * ss), "目标牌", font=f_mid, fill=soft)
    dr.text((46 * ss, y2 + 66 * ss), f"「{t}」", font=f_big, fill=ink)
    lp = d.get("last_play")
    if lp:
        who = "本天使" if lp["by"] == "liya" else "阁下"
        dr.text((260 * ss, y2 + 34 * ss), f"上一次出牌：{who} 出 {len(lp['cards'])} 张", font=f_mid, fill=soft)
    for i in range(CHAMBERS):
        cx = W - 46 * ss - (CHAMBERS - i) * 30 * ss
        cy = y2 + 70 * ss
        dr.ellipse([cx, cy - 10 * ss, cx + 20 * ss, cy + 10 * ss],
                   outline=(120, 108, 92), width=ss,
                   fill=(180, 168, 150) if i == d["player"]["chamber"] else bg)
    dr.text((W - 46 * ss - CHAMBERS * 30 * ss, y2 + 96 * ss), "阁下的左轮", font=f_small, fill=soft, anchor="ra")
    if ss != 1:
        img = img.resize((img.width // ss, img.height // ss), Image.LANCZOS)
    img.save(out)
    return out


def emit(d, state, opts, view="liya", reveal=False, extra="", head=""):
    """一条命令出两段：`── 发群` 段整块照贴，末行「别贴进群」自用。图默认跟着出。"""
    print("  ── 发群（以下整块照贴）")
    if head:
        print(head)
    print(render(d, view="player"))
    if extra:
        print(extra)
    print("  ── 发群到此")
    if view == "liya":
        print(f"  本天使手牌（暗牌，别贴进群）：{' '.join(d['liya']['hand']) or '（空）'}")
    if opts.get("png"):
        render_png(d, opts["png"], reveal=reveal or bool(d.get("over")))
        print("  图：", opts["png"])


def reveal_text(d, res):
    t = res["target"]
    who = "本天使" if res["played_by"] == "liya" else "阁下"
    lines = [f"\n  ── 翻牌 · 目标是「{t}」",
             f"  {who}出的 {len(res['cards'])} 张：{' '.join(res['cards'])}",
             "  有假牌——质疑成立！" if res["liar"] else "  全是目标牌——质疑失败。",
             f"  {('本天使' if res['loser'] == 'liya' else '阁下')}对着自己扣扳机……"
             + ("砰！中弹出局。" if res["died"] else "咔，空仓。")]
    lines.append(f"  左轮指针：本天使 第 {d['liya']['chamber']} 仓 ｜ 阁下 第 {d['player']['chamber']} 仓")
    if res.get("over"):
        lines.append(f"  ** {'本天使' if res['over'] == 'liya' else '阁下'}赢下这一桌。")
    else:
        lines.append(f"  新一局（第 {res['next_round']} 局）：目标「{d['target']}」，"
                     f"{'阁下' if res['next_first'] == 'player' else '本天使'}先出。")
    return "\n".join(lines)


# ---------- 收桌（存档 + INDEX + 台账，一条命令） ----------

RESULT_CN = {"liya": "莉娅胜", "player": "阁下胜"}
META_RE = re.compile(r"<!-- meta (\{.*?\}) -->")

INDEX_HEAD = """# 骗子牌对局存档

> 一桌一档：`<日期>-<局制>-<结果>-<对手>.md`，同名 `.json` 是原始状态文件，
> `-audit.log` 是那盘本天使可见的输出（蒙眼条款的审计凭证）。
> 引擎与规矩：`skills/chat-game-referee/games/liars-deck/rules.md`
> **收桌**：`python3 skills/chat-game-referee/scripts/liars-deck.py close` —— 本 INDEX 由它自动重建，别手改。

## 对局

| 日期 | 局制 | 结果 | 对局 | 局数 | 档位 |
|------|------|------|------|------|------|
"""

INDEX_TAIL = """
## 存档规矩

- 收盘后 `close` 自动把 `temp/liars-deck.json` 与 `temp/liars-deck.audit.log` 拷成一档，并重建本页。
- **`temp/liars-deck.json` 只活在当前这桌**——开新桌就被覆盖，要留必须先收桌。
- 经过 / 摊牌 / 承诺复算一律由 `close` 从终局状态与 `chain` 直接生成，**别手写**。
- **对局进行中不回填本目录**：牌面只有摊开之后才是公开信息。
- **审计日志含本天使自己手牌的明文**——对局中途翻＝看本天使的牌，审计请等收桌。
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


def build_archive_md(d, slug):
    """一桌的账写成给人看的 md。经过/摊牌/复算全部照抄引擎自己的结算，一手不手写。"""
    date = slug[:10]
    winner = d["over"]
    res_cn = RESULT_CN[winner]
    meta = {"slug": slug, "date": date, "game": GAME_NAME, "mode": "1v1", "result": res_cn,
            "winner": winner, "rounds": d.get("round", 1), "level": d.get("level", 2),
            "opponent": OPPONENT}
    L = [f"# 骗子牌对局存档 · {date}", "",
         f"<!-- meta {json.dumps(meta, ensure_ascii=False)} -->", "",
         f"**局制** 1v1（20 张：6Q / 6K / 6A + 2 王）｜**档位** {meta['level']}｜阁下先出",
         f"**局数** {meta['rounds']} 局收桌｜**结果：{res_cn}**", "", "## 经过", ""]
    chain = d.get("chain") or []
    if not chain:
        L.append("- （这桌由旧版引擎落盘，没有逐局记录；看下面的摊牌账。）")
    for e in chain:
        who = "本天使" if e["by"] == "liya" else OPPONENT
        ch = "本天使" if e["challenger"] == "liya" else OPPONENT
        loser = "本天使" if e["loser"] == "liya" else OPPONENT
        L += [f"### 第 {e['round']} 局 · 目标「{e['target']}」",
              f"- {who}出 {len(e['cards'])} 张：{' '.join(e['cards'])}，宣称全是「{e['target']}」",
              f"- {ch}质疑 → " + ("有假牌，质疑成立" if e["liar"] else "全是目标牌，质疑失败"),
              f"- {loser}对着自己扣扳机 → " + ("砰，中弹出局" if e["died"] else "咔，空仓"), ""]

    L += ["## 终局摊牌（照抄 `reveal`）", "", "```",
          f"  本天使：{' '.join(d['liya']['hand']) or '（空）'} ｜ 弹仓 {d['liya']['bullet']}",
          f"  阁下：{' '.join(d['player']['hand']) or '（空）'} ｜ 弹仓 {d['player']['bullet']}",
          "```", "",
          f"- 开局发牌（发牌那一刻的原始手牌）：本天使 `{' '.join(d['dealt']['liya'])}`"
          f" ｜ 阁下 `{' '.join(d['dealt']['player'])}`", ""]

    ok_g = commit_of(d["salt_g"], d["liya"]["bullet"], d["player"]["bullet"]) == d["commit_g"]
    ok_r = commit_of(d["salt_r"], d["dealt"]["liya"], d["dealt"]["player"]) == d["commit_r"]
    L += ["## 公平自证（照抄 `check`）", "", "```",
          f"游戏级承诺（子弹）：{d['commit_g']}",
          f"  独立复算 → {'一致 ✓' if ok_g else '对不上 ✗'}",
          f"本局承诺（手牌）：{d['commit_r']}",
          f"  独立复算 → {'一致 ✓' if ok_r else '对不上 ✗'}", "```", "",
          "- 蒙眼：出牌敲图上编号，命令文本里不带牌面；文字层双方手牌只报张数，牌面只走图；",
          "  本天使全程未 vision 读牌桌图、未 read 状态 json（对局进行中）。",
          f"- 审计日志：同目录 `{slug}-audit.log`。", ""]
    return "\n".join(L) + "\n"


def rebuild_index(archive_dir):
    """INDEX 从各档 md 的 meta 重建 —— 存档是唯一事实源，手改 INDEX 会被下一次收桌冲掉。"""
    rows = []
    for fn in sorted(os.listdir(archive_dir)):
        if fn.endswith(".md") and fn != "INDEX.md":
            meta = _meta_of(os.path.join(archive_dir, fn))
            if meta:
                rows.append((meta, fn))
    rows.sort(key=lambda r: (r[0].get("date", ""), r[1]), reverse=True)
    out = [INDEX_HEAD.rstrip("\n")]
    for meta, fn in rows:
        out.append(f"| {meta.get('date','')} | {meta.get('mode','')} | {meta.get('result','')} "
                   f"| {meta.get('opponent','')} vs 莉娅 | {meta.get('rounds','')} | {meta.get('level','')} |")
    out.append("")
    for meta, fn in rows:
        base = fn[:-3]
        links = [f"[{meta.get('date','')} · {meta.get('mode','')} · {meta.get('result','')}]({fn})",
                 f"[原始状态 json]({base}.json)"]
        if os.path.exists(os.path.join(archive_dir, base + "-audit.log")):
            links.append(f"[审计日志]({base}-audit.log)")
        out.append("- " + "｜".join(links))
    out.append(INDEX_TAIL)
    with open(os.path.join(archive_dir, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return len(rows)


def _ledger_script():
    for c in (LEDGER_SCRIPT, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.py")):
        if os.path.exists(c):
            return c
    return None


def cmd_close(a):
    if getattr(a, "no_ledger", False):
        a.ledger = None
    d = load(a.state)
    if not d.get("over"):
        sys.exit("还没收桌——终局才存档（想硬收就 new --force 重开，这桌不存档）。")
    if d.get("archived_as"):
        sys.exit(f"这桌已经收过了（{d['archived_as']}）——要再收先 new --force 开新桌。")
    winner = d["over"]
    res_cn = RESULT_CN[winner]
    base_slug = (f"{datetime.date.today().isoformat()}-1v1-"
                 f"{'胜' if winner == 'liya' else '负'}-{OPPONENT}vs莉娅")
    slug, i = base_slug, 2
    while os.path.exists(os.path.join(a.archive, slug + ".md")):   # 同一天连开两桌不许互相盖
        slug, i = f"{base_slug}-{i}", i + 1
    os.makedirs(a.archive, exist_ok=True)
    dst_json = os.path.join(a.archive, slug + ".json")
    shutil.copyfile(a.state, dst_json)
    src_log = audit_log_path(a.state)
    dst_log = os.path.join(a.archive, slug + "-audit.log")
    if os.path.exists(src_log):
        shutil.copyfile(src_log, dst_log)
    md_path = os.path.join(a.archive, slug + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(build_archive_md(d, slug))
    n = rebuild_index(a.archive)
    d["archived_as"] = slug          # 落了戳：同一桌再收会被挡，免得重复记账
    save(d, a.state)

    print(f"  收桌：{d.get('round', 1)} 局 · {res_cn}")
    print(f"  存档：{_rel_or_abs(dst_json)}")
    print(f"        {_rel_or_abs(md_path)}")
    if os.path.exists(dst_log):
        print(f"        {_rel_or_abs(dst_log)}")
    print(f"  INDEX：{_rel_or_abs(os.path.join(a.archive, 'INDEX.md'))}（按存档重建，现有 {n} 档）")

    if a.ledger:
        script = _ledger_script()
        if not script:
            print("  台账：找不到 ledger.py，跳过（存档已落）。")
            return
        note = a.note or f"{d.get('round', 1)} 局收桌，{res_cn}"
        r = subprocess.run([sys.executable, script, "--ledger", a.ledger, "add", GAME_NAME,
                            "win" if winner == "liya" else "loss",
                            "--level", str(d.get("level", 2)),
                            "--moves", str(d.get("round", 1)), "--note", note,
                            "--source", _rel_or_abs(dst_json)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print("  台账：" + r.stdout.strip().replace("\n", "\n        "))
        else:
            print(f"  台账：记不上 → {r.stderr.strip()}")
        print(f"  git：cd {ROOT} && git add {_rel_or_abs(a.archive)} {_rel_or_abs(a.ledger)}"
              f" && git commit -q -m \"{GAME_NAME}:{slug[5:]}\"")
    else:
        print(f"  git：cd {ROOT} && git add {_rel_or_abs(a.archive)}"
              f" && git commit -q -m \"{GAME_NAME}:{slug[5:]}\"")


# ---------- 命令 ----------

def cmd_new(a):
    if os.path.exists(a.state) and not a.force:
        old = load(a.state)
        if not old.get("over"):
            sys.exit("已经有一桌在打了（用 --force 重开，旧桌作废）。")
    d = {"version": 1, "level": a.level, "round": 0, "over": None,
         "liya": {"hand": [], "chamber": 0, "bullet": random.randrange(CHAMBERS), "alive": True},
         "player": {"hand": [], "chamber": 0, "bullet": random.randrange(CHAMBERS), "alive": True},
         "dealt": None, "target": None, "awaiting": "player", "last_play": None,
         "final": False, "played": {"liya": [], "player": []}, "chain": []}
    d["salt_g"] = os.urandom(8).hex()
    d["commit_g"] = commit_of(d["salt_g"], d["liya"]["bullet"], d["player"]["bullet"])
    new_round(d, starter="player")
    save(d, a.state)
    emit(d, a.state, a.opts,
         head="  开局：每人 5 张。游戏级承诺哈希（两把左轮的弹仓位置，之后谁都换不了）：\n"
              f"  {d['commit_g']}\n"
              f"  本局承诺哈希（双方手牌）：{d['commit_r']}")


def cmd_show(a):
    d = load(a.state)
    emit(d, a.state, a.opts, view="player" if a.player else "liya",
         reveal=bool(d.get("over")))


def cmd_play(a):
    d = load(a.state)
    if d.get("over"):
        sys.exit("这一桌收了，开新的。")
    if d.get("awaiting") != "player":
        sys.exit("现在轮到本天使——等本天使走完再动。")
    if d.get("final"):
        sys.exit("本天使手牌出空了——阁下必须质疑（open）。")
    parsed = parse_play(a.cards)
    if not parsed:
        sys.exit("出 1–3 张：图上编号（例 play 1 3）或牌面（例 play QQJ）；两种别混着写。")
    mode, payload = parsed
    have = list(d["player"]["hand"])
    tip = ""
    if mode == "idx":
        ids = payload
        if len(ids) > MAX_PLAY:
            sys.exit("一次最多出 3 张。")
        if len(set(ids)) != len(ids):
            sys.exit("同一个编号不能出两次。")
        bad = [i for i in ids if not (1 <= i <= len(have))]
        if bad:
            sys.exit(f"编号 {'、'.join(map(str, bad))} 不存在——阁下现在手里 {len(have)} 张"
                     f"（编号看牌桌图）。")
        cards = [have[i - 1] for i in ids]
    else:
        cards = payload
        if not (1 <= len(cards) <= MAX_PLAY):
            sys.exit("出 1–3 张：图上编号（例 play 1 3）或牌面（例 play QQJ）。")
        for c in cards:
            if c not in have:
                sys.exit("出牌得报自己手里真有的牌——用图上编号更稳（例 play 1 3）。")
            have.remove(c)
        tip = "\n  ⚠️ 牌面出牌，本天使看得见你出了哪几张——下次敲图上编号（例 play 1 3）。"
    note = f"  阁下出 {len(cards)} 张，宣称全是「{d['target']}」。" + tip
    if do_play(d, "player", cards):
        res = resolve_challenge(d, "liya")     # 出空 → 只剩本天使有牌，必须质疑（官方：must call LIAR）
        note += "\n  本天使：质疑！" + reveal_text(d, res)
    note = pump(d, note)
    save(d, a.state)
    emit(d, a.state, a.opts, reveal=bool(d.get("over")), extra=note)


def cmd_open(a):
    d = load(a.state)
    if d.get("over"):
        sys.exit("这一桌收了。")
    lp = d.get("last_play")
    if not lp or lp["by"] != "liya":
        sys.exit("本天使还没出牌，阁下质疑什么？")
    res = resolve_challenge(d, "player")
    note = "\n  阁下：质疑！" + reveal_text(d, res)
    note = pump(d, note)
    save(d, a.state)
    # 摊牌只摊「被质疑的那一手」——牌面在 note 的文字层里。绝不把本天使整手牌画进图
    # （那是对手白赚下一局的手牌信息，2026-09-15 实战漏过一次）。终局才全开。
    emit(d, a.state, a.opts, reveal=bool(d.get("over")), extra=note)


def cmd_check_only(a):
    cmd_check(load(a.state))


def cmd_check(d):
    ok_g = commit_of(d["salt_g"], d["liya"]["bullet"], d["player"]["bullet"]) == d["commit_g"]
    ok_r = commit_of(d["salt_r"], d["dealt"]["liya"], d["dealt"]["player"]) == d["commit_r"]
    print(f"游戏级承诺（子弹）：{d['commit_g']}")
    print(f"  独立复算 → {'一致 ✓' if ok_g else '对不上 ✗'}")
    print(f"本局承诺（手牌）：{d['commit_r']}")
    print(f"  独立复算 → {'一致 ✓' if ok_r else '对不上 ✗'}")


def cmd_reveal(a):
    d = load(a.state)
    print(f"  本天使：{' '.join(d['liya']['hand']) or '（空）'} ｜ 弹仓 {d['liya']['bullet']}")
    if not d.get("over"):
        # 局中只摊本天使这半 —— 蒙眼条款（否则等于本天使偷看阁下暗牌）
        print("  阁下的牌不摊（局中摊＝这局作废；要真摊先收桌或 new --force）。")
    else:
        print(f"  阁下：{' '.join(d['player']['hand']) or '（空）'} ｜ 弹仓 {d['player']['bullet']}")
    cmd_check(d)
    print("  （局中摊牌 = 这局作废；要接着玩就 new --force。）")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state", default=argparse.SUPPRESS)
    common.add_argument("--png", nargs="?", const=True, default=argparse.SUPPRESS,
                        help="牌桌图路径（可省 = 写 <state同名>.png；默认就出图）")
    common.add_argument("--no-png", action="store_true", default=argparse.SUPPRESS,
                        help="这一手不出图")

    p = argparse.ArgumentParser(prog="liars-deck.py", description="天界骗子牌 · 聊天版")
    p.add_argument("--state", default=DEFAULT_STATE)
    p.add_argument("--png", nargs="?", const=True, default=None,
                   help="牌桌图路径（可省 = 写 <state同名>.png；默认就出图）")
    p.add_argument("--no-png", action="store_true", help="这一手不出图")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", parents=[common])
    n.add_argument("--level", type=int, default=2, choices=(1, 2, 3))
    n.add_argument("--force", action="store_true")
    n.set_defaults(fn=cmd_new)

    s = sub.add_parser("show", parents=[common])
    s.add_argument("--player", action="store_true")
    s.set_defaults(fn=cmd_show)

    pl = sub.add_parser("play", parents=[common])
    pl.add_argument("cards", nargs="+")
    pl.set_defaults(fn=cmd_play)

    sub.add_parser("open", parents=[common]).set_defaults(fn=cmd_open)
    sub.add_parser("check", parents=[common]).set_defaults(fn=cmd_check_only)
    sub.add_parser("reveal", parents=[common]).set_defaults(fn=cmd_reveal)

    cl = sub.add_parser("close", parents=[common])
    cl.add_argument("--note", default=None, help="台账那笔的备注（默认「N 局收桌，谁胜」）")
    cl.add_argument("--archive", default=DEFAULT_ARCHIVE, help="存档目录")
    cl.add_argument("--ledger", default=DEFAULT_LEDGER, help="台账文件")
    cl.add_argument("--no-ledger", action="store_true", help="只存档、不记台账")
    cl.set_defaults(fn=cmd_close)
    return p


class _Tee:
    """把本天使可见的输出同时留一份进审计日志 —— 阁下随时可查本天使看到了什么。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def audit_log_path(state):
    return (state[:-5] if state.endswith(".json") else state) + ".audit.log"


# ── 维护闸 · 2026-09-15 阁下令 ──────────────────────────────────────
# 骗子牌尚有若干问题待修（已知：cmd_open 质疑结算用 reveal=True 把本天使整手牌
# 摊给阁下），修好之前谁都不许碰：开局 / 推进一律挡下，只留只读与收尾。
# 解除：MAINTENANCE["on"] = False（记得同步 temp/ 里的部署副本）；
# 维护作业（自测 / 修完验收）用环境变量 LIARS_MAINT_OVERRIDE=1 旁路。
MAINTENANCE = {"on": True, "since": "2026-09-15"}
_MAINT_ALLOWED = {"cmd_show", "cmd_check_only", "cmd_close"}


def maintenance_gate(a):
    if os.environ.get("LIARS_MAINT_OVERRIDE") == "1":
        return
    if not MAINTENANCE["on"] or getattr(a.fn, "__name__", "") in _MAINT_ALLOWED:
        return
    print(f"⛔ 骗子牌 · 维护中（{MAINTENANCE['since']} 起）\n"
          f"   还有问题没修完，修好之前谁都不许碰。\n"
          f"   只看：show / check；收尾：close。")
    raise SystemExit(1)


def main():
    a = build_parser().parse_args()
    maintenance_gate(a)
    if getattr(a, "no_png", False):
        a.png = None
    elif a.png is True or a.png is None:
        a.png = (a.state[:-5] if a.state.endswith(".json") else a.state) + ".png"
    a.opts = {"png": a.png}

    log = None
    orig_out, orig_err = sys.stdout, sys.stderr
    try:
        log = open(audit_log_path(a.state), "a", encoding="utf-8")
        log.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} | "
                  f"{' '.join(sys.argv[1:])} =====\n")
        log.flush()
        sys.stdout = _Tee(orig_out, log)
        sys.stderr = _Tee(orig_err, log)
    except OSError:
        log = None
    try:
        a.fn(a)
    except SystemExit as e:
        # sys.exit(消息) 的文本本来由解释器最后打印（那时 Tee 已摘、进不了日志）——
        # 这里自己先写一遍，再以整数码退出，避免重复打印
        if e.code not in (0, None):
            print(e.code, file=sys.stderr)
            raise SystemExit(1)
        raise
    finally:
        # 先摘掉 Tee 再关日志——否则解释器退出时 flush 已关闭的文件，退出码会被弄脏
        sys.stdout, sys.stderr = orig_out, orig_err
        if log:
            log.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""liars-dice.py — 天界大话骰 · 聊天版

从老仓库（终端时代的游戏厅）的 liars-dice/ 搬过来：规则引擎、AI（会学习的
对手激进估计）、斋/飞/劈 全留；终端那套 input() 主循环和渲染全丢，改成
「一手一命令 + 状态落盘 + 字符盘面 / --png 出图」。

规则（房源版）：
  每人 5 颗骰，输一颗少一颗，掉光出局。
  「1」万能（除非叫「斋」）；顺子算 0 颗，豹子额外 +1 颗。
  叫骰只能往上叫；斋 → 只能跟斋或「飞」破斋；「劈」= 双倍赌注的质疑。

变体（opt-in，new 时 --variant 开；默认全关 = 上面这套房源版）：
  fanpi  反劈：开 1 码 / 劈 2 码 / 反劈 4 码，双向可反。
  minbid 起叫下限：每局第一手设下限——喊 1 / 叫斋 = 存活人数，非斋 = 总骰数×0.3 向上取整。
  quan   围骰/全骰：围骰（1+单一点数）该点 +1、全骰 +2；被开者判输时手里是围骰/全骰 → 惩罚翻倍。

公平（本天使既是庄家又是玩家，这条必须做进结构里）：
  · 开局把双方骰子一起做盐值承诺，哈希当场公布——之后谁都不能换骰子；
  · **AI 的决策只吃本天使自己的手牌 + 公开的叫骰历史**，阁下的手牌一个字
    都不进 AI 的决策路径（cli 里连参数都不传）；
  · **蒙眼**（2026-09-15 补，与骗子牌同一套）：文字层对双方手牌一律只报颗数，
    **骰面只走棋谱图**（阁下视角那张：他的骰明、本天使的扣着）——本天使的上下文里
    从头到尾没有阁下的骰面明文；出牌走命令本来就不带骰面（叫骰是「3个5」）。
    本天使三不做：不 vision 读棋谱图、不 read 状态 json、不翻含暗牌的输出。
    `reveal` 局中只摊本天使那半（局中摊全＝作弊，也是这盘作废）。
  · **审计**：每次运行把本天使可见的输出追加进 `<state>.audit.log` 供阁下审计
    （⚠️ 含本天使自己手牌的明文，审计请等收桌；阁下的骰面对局中只有颗数，开盅后才入账）。
  · 默认出图（`--no-png` 关）——骰面全靠它，别关。
  · 跨局「对手画像」只从**公开信息**（叫骰 / 谁质疑 / 摊牌后算出的实际颗数）提炼，
    同样碰不到阁下的手牌；存 workspace/records/liars-dice/profile.json。

用法：
  python3 scripts/liars-dice.py new [--dice 5] [--level 1|2|3] [--variant fanpi|minbid|quan] [--variants all] [--force] [--png]
  python3 scripts/liars-dice.py show [--player] [--png] [--no-png]   # 每手默认出棋谱图（--no-png 关）
  python3 scripts/liars-dice.py bid <数量个点数[斋|飞]> [--png]   # 阁下叫完，引擎立刻出本天使的应手（跟 / 开 / 劈）
  python3 scripts/liars-dice.py open [--png]                     # 阁下开盅（质疑）；本天使劈过时 = 认账
  python3 scripts/liars-dice.py pi   [--png]                     # 阁下劈（双倍）
  python3 scripts/liars-dice.py fanpi [--png]                    # 阁下反劈（本天使劈过后，赌注再翻倍）
  python3 scripts/liars-dice.py check                            # 公平自证：复算承诺哈希
  python3 scripts/liars-dice.py profile [--path P] [--clear]     # 跨局对手画像 看 / 清
  python3 scripts/liars-dice.py reveal                           # 摊牌（终局用；局中摊 = 这盘作废）
  python3 scripts/liars-dice.py close [--note 文本]              # 收桌一条龙：存档 + INDEX 重建 + 台账 + git 提示（终局才收）

顺手（跟骗子牌同一套）：
  · **一手一条命令** —— 每次运行的输出分两段：`── 发群` 到 `── 发群到此` 之间整块照贴，
    末尾那行「本天使的骰盅（暗牌，别贴进群）」自用；不必再补跑一次 `show --player` 去挑。
  · **收桌一条命令** —— `close` 把存档、md、INDEX 重建、台账一笔全办完，再打一行现成 git 命令。
    终局态才收；同一盘重收会被挡；同一天连开两盘 slug 自动加尾号。
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
import time

INITIAL_DICE = 5
FACE_CHARS = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10}
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
DEFAULT_STATE = os.path.join(STATE_DIR, "liars-dice.json")
DEFAULT_PROFILE = os.path.join(RECORDS_DIR, "liars-dice", "profile.json")
DEFAULT_ARCHIVE = os.path.join(RECORDS_DIR, "liars-dice")
DEFAULT_LEDGER = os.path.join(RECORDS_DIR, "game-ledger", "ledger.json")
LEDGER_SCRIPT = os.path.join(HERE, "ledger.py")
GAME_NAME = "大话骰"
OPPONENT = "阁下"
MAX_QTY = 30


# ---------- 骰子 ----------

def roll(n):
    return sorted(random.randint(1, 6) for _ in range(n))


def is_straight(hand):
    """顺子：各点不同（≥2 颗才算）"""
    return len(hand) >= 2 and len(set(hand)) == len(hand)


def is_leopard(hand):
    """豹子：全同点（≥2 颗才算）"""
    return len(hand) >= 2 and len(set(hand)) == 1


def is_weiquan(hand):
    """围骰/全骰的牌型：全同点，或只有「1」和另一种点数（≥2 颗）——百度 3.0 那两种加成型。"""
    if len(hand) < 2:
        return False
    faces = set(hand)
    return len(faces) == 1 or (len(faces) == 2 and 1 in faces)


def count_bonus(hand, face, zhai, quan):
    """围骰/全骰加成。房源版：全同点 +1；quan 开：全骰 +2、围骰该点 +1（仅非斋）。"""
    if len(hand) < 2:
        return 0
    faces = set(hand)
    if len(faces) == 1:
        return 2 if quan else 1
    if quan and not zhai and len(faces) == 2 and 1 in faces:
        other = next(iter(faces - {1}))
        return 1 if face == other else 0
    return 0


def count_face(hand, face, wild=True):
    c = sum(1 for d in hand if d == face)
    if wild and face != 1:
        c += sum(1 for d in hand if d == 1)
    return c


def total_actual(hand, face, zhai=False, quan=False):
    """一副手牌里某点数的实际颗数（含顺子 / 围骰 / 全骰修正）"""
    if is_straight(hand):
        return 0
    base = count_face(hand, face, wild=not zhai)
    if base > 0:
        base += count_bonus(hand, face, zhai, quan)
    return base


# ---------- 变体（opt-in；默认全关 = 房源版） ----------

VARIANT_NAMES = ("fanpi", "minbid", "quan")
VARIANT_LABEL = {"fanpi": "反劈", "minbid": "起叫下限", "quan": "围骰/全骰"}


def var(d, name):
    return name in (d.get("variants") or [])


def min_bid(d, face, zhai):
    """起叫下限（minbid 变体；默认 1 = 不设限）。总骰数×0.3 向上取整 = 人数×1.5 的等价换算。"""
    if not var(d, "minbid"):
        return 1
    alive = (1 if d["liya"]["count"] > 0 else 0) + (1 if d["player"]["count"] > 0 else 0)
    if face == 1 or zhai:
        return max(1, alive)
    total = d["liya"]["count"] + d["player"]["count"]
    return max(1, -(-total * 3 // 10))


def hand_str(hand, hidden=False):
    if not hand:
        return "（空）"
    if hidden:
        return " ".join("🎲" for _ in hand)
    return " ".join(f"{FACE_CHARS[d]}{d}" for d in hand)


# ---------- 叫骰 ----------

def bid_str(b):
    if not b:
        return "—"
    s = f"{b['qty']}个{b['face']}"
    if b.get("zhai"):
        s += "斋"
    if b.get("fei"):
        s += "飞"
    return s


def valid_next(b, last, floor=1):
    """b 能不能接在 last 后面；floor = 起叫下限（只在 last is None 时用）"""
    if last is None:
        return floor <= b["qty"] <= MAX_QTY and 1 <= b["face"] <= 6

    if b.get("fei"):
        # 飞：只能跟在斋后面，数量 ≥ 上一注 ×2
        return bool(last.get("zhai")) and b["qty"] >= last["qty"] * 2 and 1 <= b["face"] <= 6

    if last.get("zhai") and not b.get("zhai"):
        return False  # 对方叫斋，不飞就别想脱斋

    if not last.get("zhai") and b.get("zhai"):
        # 斋化：同数换更大点，或加数量
        if b["qty"] > last["qty"]:
            return 1 <= b["face"] <= 6
        return b["qty"] == last["qty"] and b["face"] > last["face"]

    if b["qty"] > last["qty"]:
        return 1 <= b["face"] <= 6
    if b["qty"] == last["qty"]:
        return b["face"] > last["face"]
    return False


def parse_bid(text):
    """认「3个5」「3个5斋」「6个5飞」「三个5」——阁下怎么打都得认"""
    t = re.sub(r"\s+", "", (text or "").strip())
    if not t:
        return None
    m = re.fullmatch(r"(?:(\d+)|([一二两三四五六七八九十]))个?([1-6])(斋|飞)?", t)
    if not m:
        m = re.fullmatch(r"(?:(\d+)|([一二两三四五六七八九十]))个([1-6])(斋|飞)?", t)
    if not m:
        return None
    qty = int(m.group(1)) if m.group(1) else CN_NUM[m.group(2)]
    face = int(m.group(3))
    tag = m.group(4)
    if not (1 <= qty <= MAX_QTY):
        return None
    return {"qty": qty, "face": face, "zhai": tag == "斋", "fei": tag == "飞", "by": None}


# ---------- 对手画像（跨局；只吃公开信息 —— 叫骰 / 谁质疑 / 摊牌后的实际颗数） ----------

def _blank_profile():
    return {"version": 1, "updated": "",
            "player": {"bids": 0, "qty_sum": 0.0, "dice_sum": 0, "zhai": 0, "fei": 0,
                       "challenges": 0, "pi": 0, "fanpi": 0,
                       "tested": 0, "tested_true": 0}}


def load_profile(path):
    p = _blank_profile()
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            for k, v in raw.items():
                if k == "player" and isinstance(v, dict):
                    p["player"].update(v)
                elif k in p:
                    p[k] = v
        except Exception:
            pass
    return p


def save_profile(p, path):
    if not path:
        return
    p["updated"] = time.strftime("%Y-%m-%d %H:%M")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
        f.write("\n")


def profile_bid(d, b):
    """记阁下一手叫骰。b 是公开信息，摸不到任何暗牌。"""
    path = d.get("profile")
    if not path:
        return
    p = load_profile(path)
    pl = p["player"]
    pl["bids"] += 1
    pl["qty_sum"] += b["qty"]
    pl["dice_sum"] += d["player"]["count"]
    pl["zhai"] += 1 if b.get("zhai") else 0
    pl["fei"] += 1 if b.get("fei") else 0
    save_profile(p, path)


def profile_settle(d, res, kind):
    """记一次质疑结算。只用公开信息：叫骰 / 谁质疑 / 摊牌后算出的实际颗数。"""
    path = d.get("profile")
    if not path:
        return
    p = load_profile(path)
    pl = p["player"]
    if res["bid"].get("by") == "player":
        pl["tested"] += 1
        if res["bidder_wins"]:
            pl["tested_true"] += 1
    if res["challenger"] == "player":
        pl["challenges"] += 1
        if kind == "pi":
            pl["pi"] += 1
        elif kind == "fanpi":
            pl["fanpi"] += 1
    save_profile(p, path)


def profile_read(d):
    """从画像提炼给 AI 用的先验；样本不够就不给（不硬凑）。"""
    path = d.get("profile")
    if not path or not os.path.exists(path):
        return None
    pl = load_profile(path)["player"]
    out = {}
    if pl["bids"] >= 3 and pl["dice_sum"] > 0:
        out["aggr"] = max(0.0, min(1.0, (pl["qty_sum"] / pl["dice_sum"] - 0.2) / 0.6))
    if pl["tested"] >= 3:
        out["bluff"] = 1.0 - pl["tested_true"] / pl["tested"]
    if pl["challenges"] + pl["bids"] >= 6:
        out["open_rate"] = pl["challenges"] / (pl["challenges"] + pl["bids"])
    return out or None


def blended_aggr(d):
    """本轮激进估计：场内 aggr 上混一半画像（有画像才混）。"""
    base = d.get("aggr", 0.5)
    pr = profile_read(d) or {}
    if "aggr" in pr:
        base = 0.5 * base + 0.5 * pr["aggr"]
    return base


# ---------- 状态 ----------

def load(state):
    if not os.path.exists(state):
        sys.exit("还没有这一盘——先 new。")
    with open(state, encoding="utf-8") as f:
        return json.load(f)


def save(d, state):
    parent = os.path.dirname(state)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(state, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")


def commit_of(salt, liya, player):
    """盐值承诺：盐 | 本天使的骰子 | 阁下的骰子"""
    raw = f"{salt}|{','.join(map(str, liya))}|{','.join(map(str, player))}"
    return hashlib.sha256(raw.encode()).hexdigest()


def deal(d):
    """新一局：双方按剩余骰数重摇 + 顺子重摇（双方同一把尺子）+ 重新承诺"""
    d["round"] = d.get("round", 0) + 1
    n_l, n_p = d["liya"]["count"], d["player"]["count"]
    lh, ph = roll(n_l), roll(n_p)
    for _ in range(8):
        if not is_straight(lh) and not is_straight(ph):
            break
        lh, ph = roll(n_l), roll(n_p)
    d["liya"]["hand"], d["player"]["hand"] = lh, ph
    d["salt"] = os.urandom(8).hex()
    d["commit"] = commit_of(d["salt"], lh, ph)
    d["last_bid"] = None
    d["chain"] = []
    d["over"] = None
    d.pop("_pending", None)


def whose_turn(d):
    if d.get("over"):
        return "—"
    last = d.get("last_bid")
    return "player" if (last is None or last.get("by") == "liya") else "liya"


# ---------- AI（只吃自己的手牌 + 公开历史） ----------

def ai_effective(hand, face, zhai=False, quan=False):
    """本天使自己手牌上某点数的颗数——与 total_actual 同一把尺子。"""
    return total_actual(hand, face, zhai, quan)


def ai_estimate(hand, aggr):
    """对手某点数上的期望颗数：只按骰子数与概率估，不看牌"""
    return max(0.0, len(hand) * (1 / 6 + 1 / 6) + aggr * 0.5)


def ai_pick(hand, last, level=2, aggr=0.5, floor=1, quan=False):
    """返回一个叫骰 dict；None = 本天使认怂要开。floor 只在起叫（last is None）时生效。"""
    if last is not None and (last.get("zhai") or last.get("fei")):
        # 斋里：能飞就飞，飞不动就跟着叫斋
        if last["qty"] * 2 <= MAX_QTY:
            best_face, best_cnt = 1, 0
            for f in range(1, 7):
                c = ai_effective(hand, f, False, quan)
                if c > best_cnt:
                    best_face, best_cnt = f, c
            return {"qty": last["qty"] * 2, "face": best_face, "zhai": False,
                    "fei": True, "by": "liya"}
        best_face, best_cnt = 1, 0
        for f in range(1, 7):
            c = ai_effective(hand, f, True, quan)
            if c > best_cnt:
                best_face, best_cnt = f, c
        cand = []
        if last["qty"] + 1 <= MAX_QTY:
            cand.append({"qty": last["qty"] + 1, "face": best_face, "zhai": True,
                         "fei": False, "by": "liya"})
        for f in range(last["face"] + 1, 7):
            cand.append({"qty": last["qty"], "face": f, "zhai": True, "fei": False,
                         "by": "liya"})
        return cand[0] if cand else None

    best_face, best_cnt = 1, 0
    for f in range(1, 7):
        c = ai_effective(hand, f, False, quan)
        if c > best_cnt:
            best_face, best_cnt = f, c

    opp = ai_estimate(hand, aggr)
    if last is None:
        return {"qty": max(floor, best_cnt + 1), "face": best_face, "zhai": False,
                "fei": False, "by": "liya"}

    cand = []
    if last["qty"] + 1 <= MAX_QTY:
        cand.append({"qty": last["qty"] + 1, "face": best_face, "zhai": False,
                     "fei": False, "by": "liya"})
    for f in range(last["face"] + 1, 7):
        if best_cnt + opp >= last["qty"]:
            cand.append({"qty": last["qty"], "face": f, "zhai": False, "fei": False,
                         "by": "liya"})
    safe = max(last["qty"] + 1, int((best_cnt + opp) * 0.8) + 1)
    if safe <= MAX_QTY:
        cand.append({"qty": safe, "face": best_face, "zhai": False, "fei": False,
                     "by": "liya"})
    cand = [b for b in cand if valid_next(b, last)]
    if not cand:
        return None
    cand.sort(key=lambda b: b["qty"])
    if level <= 1:
        return cand[0]
    if level == 2:
        mid = max(1, len(cand) // 2 + 1)
        return random.choice(cand[:mid])
    idx = min(len(cand) - 1, max(0, len(cand) - 2))
    return cand[idx]


def ai_should_challenge(bid, hand, level=2, aggr=0.5, quan=False, bluff=None):
    """只吃自己的手牌 + 概率估计（bluff = 画像给的对家诈叫率）"""
    if not bid:
        return False
    mine = ai_effective(hand, bid["face"], bool(bid.get("zhai")), quan)
    opp = ai_estimate(hand, aggr) * (0.4 if bid.get("zhai") else 1.0)
    est = mine + opp
    thr = 1.3 if level <= 1 else (1.2 if level == 2 else 1.1)
    if len(hand) <= 2:
        thr = 1.15
    if bluff is not None:
        thr = max(0.9, thr - 0.3 * (bluff - 0.5))   # 画像说他爱诈 → 门槛压低，多开
    return bid["qty"] > est * thr


def ai_should_pi(bid, hand, level=2, aggr=0.5, quan=False, bluff=None):
    """劈（双倍质疑）：比普通质疑更要命，只在极度确信吹破时才下。"""
    if not bid:
        return False
    mine = ai_effective(hand, bid["face"], bool(bid.get("zhai")), quan)
    opp = ai_estimate(hand, aggr) * (0.4 if bid.get("zhai") else 1.0)
    est = mine + opp
    k = 1.6 if level >= 2 else 1.9
    if bluff is not None:
        k = max(1.25, k - 0.5 * (bluff - 0.5))
    return bid["qty"] > est * k


def ai_should_fanpi(bid, hand, level=2, aggr=0.5, quan=False):
    """被劈时反劈：这一注是本天使叫的，估得越稳越想翻倍（诈叫时不敢反）。"""
    if not bid:
        return False
    mine = ai_effective(hand, bid["face"], bool(bid.get("zhai")), quan)
    opp = ai_estimate(hand, aggr) * (0.4 if bid.get("zhai") else 1.0)
    return (mine + opp) >= bid["qty"] * (1.15 if level >= 2 else 1.25)


# ---------- 结算 ----------

def resolve(d, challenger, penalty=1, kind="open"):
    """质疑结算。challenger: 'player' | 'liya'；penalty: 1 开 / 2 劈 / 4 反劈。返回结果 dict。"""
    bid = d["last_bid"]
    face, zhai = bid["face"], bool(bid.get("zhai"))
    quan = var(d, "quan")
    lh, ph = d["liya"]["hand"], d["player"]["hand"]
    l_cnt = total_actual(lh, face, zhai, quan)
    p_cnt = total_actual(ph, face, zhai, quan)
    actual = l_cnt + p_cnt
    bidder_wins = actual >= bid["qty"]
    bidder = bid["by"]
    challenger_wins = not bidder_wins
    loser = bidder if challenger_wins else challenger

    pen_quan = False
    if quan and loser == bidder and is_weiquan(d[loser]["hand"]):
        # 被开者（叫骰方）手里是围骰/全骰还输 → 惩罚翻倍（百度 4.0 版）
        penalty *= 2
        pen_quan = True
    d[loser]["count"] = max(0, d[loser]["count"] - penalty)

    res = {"bid": bid, "actual": actual, "liya_count": l_cnt, "player_count": p_cnt,
           "bidder": bidder, "challenger": challenger, "bidder_wins": bidder_wins,
           "loser": loser, "penalty": penalty, "is_pi": penalty >= 2, "pen_quan": pen_quan,
           "liya_hand": lh, "player_hand": ph,
           "liya_left": d["liya"]["count"], "player_left": d["player"]["count"]}
    # 逐局经过：chain 每局都会被 deal 清掉，不在这儿留一份，收桌就只剩终局那一手
    d.setdefault("history", []).append({
        "round": d.get("round", 1), "kind": kind, "bid": dict(bid),
        "chain": [dict(b) for b in d.get("chain", [])],
        "liya_hand": list(lh), "player_hand": list(ph),
        "liya_count": l_cnt, "player_count": p_cnt, "actual": actual,
        "bidder": bidder, "bidder_wins": bidder_wins, "challenger": challenger,
        "loser": loser, "penalty": penalty, "pen_quan": pen_quan,
        "liya_left": res["liya_left"], "player_left": res["player_left"]})
    profile_settle(d, res, kind)
    if d[loser]["count"] <= 0:
        d["over"] = "player" if loser == "liya" else "liya"
        res["over"] = d["over"]
    else:
        nxt = "player" if loser == "liya" else "liya"
        deal(d)
        if nxt == "liya":
            # 输家先叫：轮到本天使，就当场把起叫开出来
            b = ai_pick(d["liya"]["hand"], None, d.get("level", 2), d.get("aggr", 0.5))
            if b:
                b["by"] = "liya"
                d["chain"].append(b)
                d["last_bid"] = b
        res["next_round"] = d["round"]
        res["next_first"] = nxt
        res["commit"] = d["commit"]
    return res


# ---------- 渲染 ----------

def stats_line(d):
    return (f"本天使 {d['liya']['count']} 颗 ｜ 阁下 {d['player']['count']} 颗 ｜ "
            f"第 {d.get('round', 1)} 局 ｜ 叫骰 {len(d.get('chain', []))} 手")


def render(d, view="liya"):
    out = []
    head = f"第 {d.get('round', 1)} 局"
    if d.get("over"):
        head += f" · 终局（{'本天使' if d['over'] == 'liya' else '阁下'}赢）"
    elif d.get("_pending"):
        head += " · 本天使劈了——等阁下表态（fanpi 反劈 / open 认账）"
    else:
        head += f" · 轮到{'阁下' if whose_turn(d) == 'player' else '本天使'}"
    out.append(f"  {head}")
    if d.get("variants"):
        out.append("  变体：" + "、".join(VARIANT_LABEL[v] for v in d["variants"]))
    pr = profile_read(d)
    if pr:
        bits = []
        if "open_rate" in pr:
            bits.append(f"开盅率 {pr['open_rate'] * 100:.0f}%")
        if "bluff" in pr:
            bits.append(f"诈叫率 {pr['bluff'] * 100:.0f}%")
        if "aggr" in pr:
            bits.append(f"叫骰激进 {pr['aggr'] * 100:.0f}%")
        if bits:
            out.append("  本天使记着阁下：" + " ｜ ".join(bits) + "（跨局画像）")
    if view == "liya":
        out.append(f"  本天使的骰盅（暗牌，别贴进群）：{hand_str(d['liya']['hand'])}")
    out.append(f"  阁下的骰盅：{len(d['player']['hand'])} 颗"
               f"{'（空）' if not d['player']['hand'] else ''}"
               f"（骰面在棋谱图上；开盅才见真章）")
    out.append(f"  当前叫骰：{('本天使' if d['last_bid'].get('by') == 'liya' else '阁下') + ' ' + bid_str(d['last_bid']) if d.get('last_bid') else '（还没人叫，阁下起叫）'}")
    out.append(f"  {stats_line(d)}")
    if d.get("chain"):
        recent = " → ".join(f"{'莉' if b.get('by') == 'liya' else '阁'}{bid_str(b)}"
                           for b in d["chain"][-6:])
        out.append(f"  叫骰链：{recent}")
    return "\n".join(out)


CJK_FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
NUM_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
PIP = {1: [(1, 1)], 2: [(0, 0), (2, 2)], 3: [(0, 0), (1, 1), (2, 2)],
       4: [(0, 0), (0, 2), (2, 0), (2, 2)],
       5: [(0, 0), (0, 2), (1, 1), (2, 0), (2, 2)],
       6: [(0, 0), (0, 2), (1, 0), (1, 2), (2, 0), (2, 2)]}


def _font(path, size):
    from PIL import ImageFont
    return (ImageFont.truetype(path, size) if os.path.exists(path)
            else ImageFont.load_default())


def _text(dr, xy, s, font, fill, anchor="la"):
    dr.text(xy, s, font=font, fill=fill, anchor=anchor)


def _die(dr, x, y, c, ss, face=None, color=(252, 250, 244), edge=(96, 86, 72)):
    """画一颗骰子：face=None 是扣着的（骰盅里的暗牌）"""
    r = c * 0.16
    dr.rounded_rectangle([x, y, x + c, y + c], radius=r, fill=color, outline=edge, width=ss)
    if face is None:
        _text(dr, (x + c / 2, y + c / 2), "?", _font(NUM_FONT, int(c * 0.52)),
              (150, 140, 124), anchor="mm")
        return
    pip = c * 0.115
    step = c * 0.27
    cx, cy = x + c / 2, y + c / 2
    for (i, j) in PIP[face]:
        px, py = cx + (i - 1) * step, cy + (j - 1) * step
        dr.ellipse([px - pip, py - pip, px + pip, py + pip], fill=(38, 32, 26))


def render_png(d, out, reveal=False, cell=64, ss=2):
    """阁下视角的棋谱：阁下的骰明、本天使的骰扣着（开盅 / --reveal 才翻）。"""
    from PIL import Image, ImageDraw
    c = cell * ss
    W = max(760, 5 * c + 4 * 18 * ss + 120 * ss)
    H = 560 * ss
    bg, ink, soft = (246, 241, 230), (64, 56, 46), (150, 140, 124)
    img = Image.new("RGB", (W, H), bg)
    dr = ImageDraw.Draw(img)
    f_title = _font(CJK_FONT, 30 * ss)
    f_mid = _font(CJK_FONT, 22 * ss)
    f_small = _font(CJK_FONT, 18 * ss)
    f_big = _font(CJK_FONT, 40 * ss)

    _text(dr, (46 * ss, 34 * ss), "天界大话骰", f_title, ink)
    _text(dr, (W - 46 * ss, 44 * ss), f"第 {d.get('round', 1)} 局", f_mid, soft, anchor="ra")
    dr.line([46 * ss, 92 * ss, W - 46 * ss, 92 * ss], fill=(214, 205, 188), width=ss)

    # 本天使的骰盅
    _text(dr, (46 * ss, 116 * ss), f"本天使的骰盅 · {d['liya']['count']} 颗", f_mid, ink)
    x0, y0 = 46 * ss, 150 * ss
    for i, v in enumerate(d["liya"]["hand"]):
        _die(dr, x0 + i * (c + 18 * ss), y0, c, ss,
             face=v if reveal else None)

    # 阁下的手牌
    y1 = y0 + c + 56 * ss
    _text(dr, (46 * ss, y1 - 34 * ss), f"阁下的骰盅 · {d['player']['count']} 颗", f_mid, ink)
    for i, v in enumerate(d["player"]["hand"]):
        _die(dr, 46 * ss + i * (c + 18 * ss), y1, c, ss, face=v)

    # 当前叫骰
    y2 = y1 + c + 40 * ss
    dr.line([46 * ss, y2, W - 46 * ss, y2], fill=(214, 205, 188), width=ss)
    bid = d.get("last_bid")
    if bid:
        who = "本天使" if bid.get("by") == "liya" else "阁下"
        _text(dr, (46 * ss, y2 + 30 * ss), f"当前叫骰：{who}", f_mid, soft)
        _text(dr, (46 * ss, y2 + 74 * ss), bid_str(bid), f_big, ink)
    else:
        _text(dr, (46 * ss, y2 + 44 * ss), "还没人叫——阁下起叫", f_big, soft)

    # 叫骰链
    if d.get("chain"):
        chain = " → ".join(f"{'莉' if b.get('by') == 'liya' else '阁'}{bid_str(b)}"
                           for b in d["chain"][-7:])
        _text(dr, (46 * ss, H - 44 * ss), f"叫骰链：{chain}", f_small, soft)

    if ss != 1:
        img = img.resize((img.width // ss, img.height // ss), Image.LANCZOS)
    img.save(out)
    return out


def emit(d, state, opts, view="liya", reveal=False, extra="", head=""):
    """一条命令出两段（铁律 11）：`── 发群` 段整块照贴，末行「别贴进群」自用。

    发群段走 `view="player"` —— 不打印本天使的骰盅明文；本天使那行缀在块外，照贴时跳过。
    """
    print("  ── 发群（以下整块照贴）")
    if head:
        print(head)
    print(render(d, view="player"))
    if extra:
        print(extra)
    print("  ── 发群到此")
    if view == "liya":
        print(f"  本天使的骰盅（暗牌，别贴进群）：{hand_str(d['liya']['hand'])}")
    if opts.get("png"):
        render_png(d, opts["png"], reveal=reveal or bool(d.get("over")))
        print("  图：", opts["png"])


# ---------- 公平自证 ----------

def cmd_check(d):
    print("\n".join(cmd_check_lines(d)))


# ---------- 命令 ----------

def parse_variants(a):
    """把 --variant（可重复）+ --variants（逗号 / all）并成去重的变体名列表。"""
    raw = list(getattr(a, "variant", None) or [])
    more = getattr(a, "variants", None)
    if more:
        raw += [x for x in re.split(r"[,\s]+", more) if x]
    if "all" in raw:
        return list(VARIANT_NAMES)
    bad = [x for x in raw if x not in VARIANT_NAMES]
    if bad:
        sys.exit(f"不认识的变体：{'、'.join(bad)}（可选：{'、'.join(VARIANT_NAMES)}，或 all）。")
    out = []
    for x in raw:
        if x not in out:
            out.append(x)
    return out


def cmd_new(a):
    if os.path.exists(a.state) and not a.force:
        old = load(a.state)
        if not old.get("over"):
            sys.exit("已经有一盘在打了（用 --force 重开，旧盘作废）。")
    d = {"version": 1, "level": a.level, "aggr": 0.5, "dice": a.dice,
         "variants": parse_variants(a),
         "profile": None if a.no_profile else a.profile,
         "liya": {"count": a.dice, "hand": []},
         "player": {"count": a.dice, "hand": []},
         "round": 0, "last_bid": None, "chain": [], "history": []}
    deal(d)
    save(d, a.state)
    head = (f"  开局：每人 {a.dice} 颗骰。承诺哈希（现在就公布，之后谁都换不了骰子）：\n"
            f"  {d['commit']}")
    if d["variants"]:
        head += "\n  变体开：" + "、".join(f"{VARIANT_LABEL[v]}（{v}）" for v in d["variants"])
    emit(d, a.state, a.opts, view="liya", head=head)


def cmd_show(a):
    d = load(a.state)
    emit(d, a.state, a.opts, view="player" if a.player else "liya",
         reveal=bool(d.get("over")))


def cmd_bid(a):
    d = load(a.state)
    if d.get("over"):
        sys.exit("这盘已经收了，开新的。")
    if d.get("_pending"):
        sys.exit("本天使刚劈了这一注——要反劈就 `fanpi`，认账就 `open`。")
    if whose_turn(d) != "player":
        sys.exit(f"现在轮到本天使（上一条：{bid_str(d['last_bid'])}）。")
    b = parse_bid(a.text)
    if b is None:
        sys.exit("没读懂这个叫骰。格式：数量个点数[斋|飞]，如「3个5」「3个5斋」「6个5飞」。")
    floor = min_bid(d, b["face"], bool(b.get("zhai"))) if d.get("last_bid") is None else 1
    if not valid_next(b, d.get("last_bid"), floor):
        last = d.get("last_bid")
        if last is None and floor > 1:
            sys.exit(f"起叫有下限：这一手至少 {floor} 个（喊 1 / 斋按存活人数，非斋按总骰数的三成）。")
        hint = "；当前在「斋」，要么叫斋要么飞" if last and last.get("zhai") else ""
        sys.exit(f"不能接在 {bid_str(last)} 后面——数量要加，或同数量换更大点{hint}。")
    b["by"] = "player"
    d["chain"].append(b)
    d["last_bid"] = b
    profile_bid(d, b)

    # 阁下叫完，轮到本天使——AI 只吃自己的手牌 + 公开历史
    hand = d["liya"]["hand"]
    aggr0 = d.get("aggr", 0.5)
    player_bids = [x for x in d["chain"] if x.get("by") == "player"][-3:]
    if player_bids:
        avg = sum(x["qty"] for x in player_bids) / len(player_bids)
        d["aggr"] = max(0.1, min(1.0, aggr0 * 0.7 + (avg / 5.0) * 0.3))
    aggr = blended_aggr(d)
    level = d.get("level", 2)
    quan = var(d, "quan")
    bluff = (profile_read(d) or {}).get("bluff")

    note = ""
    if ai_should_challenge(b, hand, level, aggr, quan, bluff):
        if var(d, "fanpi") and ai_should_pi(b, hand, level, aggr, quan, bluff):
            d["_pending"] = True
            note = (f"\n  本天使：劈！（{bid_str(b)} 赌注翻倍）"
                    f"——要反劈就 `fanpi`，认账就直接 `open`。")
        else:
            res = resolve(d, "liya")
            note = f"\n  本天使：开！（质疑 {bid_str(res['bid'])}）"
            note += reveal_text(d, res)
    else:
        mine = ai_pick(hand, b, level, aggr, 1, quan)
        if mine is None:
            res = resolve(d, "liya")
            note = f"\n  本天使：……开。（叫不动了）"
            note += reveal_text(d, res)
        else:
            d["chain"].append(mine)
            d["last_bid"] = mine
            note = f"\n  本天使接：{bid_str(mine)}"
    save(d, a.state)
    emit(d, a.state, a.opts, reveal=bool(d.get("over")), extra=note)


def cmd_open(a):
    _settle(a, "open")


def cmd_pi(a):
    _settle(a, "pi")


def cmd_fanpi(a):
    _settle(a, "fanpi")


def _settle(a, mode):
    """阁下表态：open 开盅 / pi 劈 / fanpi 反劈。本天使劈过时 open=认账、fanpi=反劈。"""
    d = load(a.state)
    if d.get("over"):
        sys.exit("这盘已经收了。")
    pend = d.pop("_pending", None)
    if pend:
        if mode not in ("open", "fanpi"):
            sys.exit("本天使刚劈了这一注——只能 `open`（认账）或 `fanpi`（反劈）。")
        penalty = 4 if mode == "fanpi" else 2
        res = resolve(d, "liya", penalty=penalty, kind="open")
        save(d, a.state)
        head = "\n  阁下：反劈！" if mode == "fanpi" else "\n  阁下：认劈。"
        emit(d, a.state, a.opts, reveal=bool(d.get("over")), extra=head + reveal_text(d, res))
        return

    last = d.get("last_bid")
    if not last or last.get("by") != "liya":
        sys.exit("本天使还没叫骰，阁下开什么开？")
    if mode == "fanpi":
        sys.exit("本天使没劈阁下的骰，没得反劈。")
    if mode == "pi":
        if var(d, "fanpi") and ai_should_fanpi(last, d["liya"]["hand"], d.get("level", 2),
                                               d.get("aggr", 0.5), var(d, "quan")):
            penalty, head = 4, "\n  阁下：劈！\n  本天使：反劈！"
        else:
            penalty, head = 2, "\n  阁下：劈！"
    else:
        penalty, head = 1, "\n  阁下：开！"
    res = resolve(d, "player", penalty=penalty, kind=mode)
    save(d, a.state)
    # 开盅的骰面在 reveal_text 文字层里；resolve 内部已经 deal 了新一局的骰，
    # 图上再 reveal=True 就是把本天使下一局的骰白送给阁下（与骗子牌 cmd_open 同源，2026-09-15 一并修）。
    emit(d, a.state, a.opts, reveal=bool(d.get("over")), extra=head + reveal_text(d, res))


def reveal_text(d, res):
    face, zhai = res["bid"]["face"], bool(res["bid"].get("zhai"))
    tag = "斋（1 不算万能）" if zhai else "非斋（1 万能）"
    out = [f"\n  ── 开盅 · 叫的是「{bid_str(res['bid'])}」· {tag}",
           f"  本天使：{hand_str(res['liya_hand'])}  → 这个点数 {res['liya_count']} 颗",
           f"  阁下：{hand_str(res['player_hand'])}  → 这个点数 {res['player_count']} 颗",
           f"  实际合计 {res['actual']} 颗 ｜ 叫 {res['bid']['qty']} 颗 → "
           f"{'叫骰成立' if res['bidder_wins'] else '叫骰吹破'}",
           f"  {('本天使' if res['loser'] == 'liya' else '阁下')}掉 "
           f"{res['penalty']} 颗"
           + ("（围骰/全骰翻倍）" if res.get("pen_quan") else "")
           + f" ｜ 剩：本天使 {res['liya_left']} 颗、阁下 {res['player_left']} 颗"]
    if res.get("over"):
        out.append(f"  ** {'本天使' if res['over'] == 'liya' else '阁下'}赢下这一盘 —— 对方骰子掉光了。")
    else:
        out.append(f"  新一局（第 {res['next_round']} 局）："
                   f"{'阁下' if res['next_first'] == 'player' else '本天使'}先叫；"
                   f"承诺哈希 {d['commit']}")
    return "\n".join(out)


def cmd_reveal(a):
    d = load(a.state)
    print(f"  本天使：{hand_str(d['liya']['hand'])}")
    if not d.get("over"):
        # 局中只摊本天使这半 —— 蒙眼条款（否则等于本天使偷看阁下暗牌）
        print("  阁下的骰盅不摊（局中摊＝这盘作废；要真摊先收桌或 new --force）。")
    else:
        print(f"  阁下：{hand_str(d['player']['hand'])}")
    cmd_check(d)
    print("  （局中摊牌 = 这盘作废；要接着玩就 new --force。）")


def cmd_check_only(a):
    cmd_check(load(a.state))


def cmd_profile(a):
    path = a.path or DEFAULT_PROFILE
    if a.clear:
        if os.path.exists(path):
            os.remove(path)
            print(f"画像已清：{path}")
        else:
            print(f"没有画像可清：{path}")
        return
    p = load_profile(path)
    pl = p["player"]
    print(f"对手画像 · {path}")
    print(f"  更新 {p.get('updated') or '—'}")
    print(f"  叫骰 {pl['bids']} 手 ｜ 质疑 {pl['challenges']} 次（其中劈 {pl['pi']} / 反劈 {pl['fanpi']}）")
    print(f"  斋 {pl['zhai']} ｜ 飞 {pl['fei']} ｜ 摊牌复核 {pl['tested']} 手（属实 {pl['tested_true']}）")
    pr = profile_read({"profile": path})
    if pr:
        label = {"aggr": "叫骰激进", "bluff": "诈叫率", "open_rate": "开盅率"}
        print("  提炼：" + "、".join(f"{label.get(k, k)} {v * 100:.0f}%" for k, v in pr.items()))
    else:
        print("  样本还不够，提炼先不给（不硬凑）。")


# ---------- 收桌（存档 + INDEX + 台账，一条命令） ----------
RESULT_CN = {"liya": "莉娅胜", "player": "阁下胜"}
OPEN_CN = {1: "开", 2: "劈（双倍）", 4: "反劈（四倍）"}
META_RE = re.compile(r"<!-- meta (\{.*?\}) -->")

INDEX_HEAD = """# 大话骰对局存档

> 一盘一档：`<日期>-<N骰>-<结果>-阁下vs莉娅.md`，同名 `.json` 是原始状态文件（含双方骰子）。
> `profile.json`（若有）是跨局对手画像，不吃暗牌、只从公开信息提炼。
> 引擎与规矩：`skills/chat-game-referee/games/liars-dice/rules.md`
> **收桌**：`python3 skills/chat-game-referee/scripts/liars-dice.py close` —— 本 INDEX 由它自动重建，别手改。

## 对局

| 日期 | 局制 | 结果 | 局数 | 档位 | 变体 |
|------|------|------|------|------|------|
"""

INDEX_TAIL = """
## 存档规矩

- 一方骰子掉光才收；`close` 把 `temp/liars-dice.json` 拷成一档，并重建本页。
- **`temp/liars-dice.json` 只活在当前这盘**——开新盘就被覆盖，要留必须先收桌。
- 逐局经过 / 摊牌 / 承诺复算一律由 `close` 从存档的 `history` + `chain` 现生成，**别手写**。
- 承诺哈希开局公布（游戏每局的骰子都封在里面），复算用 `check`。
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


def _who(x):
    return "本天使" if x == "liya" else OPPONENT


def _round_block(e):
    """一局经过——从 history 里那一笔现生成，一手不手写。"""
    L = [f"### 第 {e['round']} 局"]
    ch = e.get("chain") or []
    if ch:
        L.append("- 叫骰链：" + " → ".join(
            f"{'莉' if b.get('by') == 'liya' else '阁'}{bid_str(b)}" for b in ch))
    L.append(f"- 开盅（{_who(e['challenger'])}）——本天使 {hand_str(e['liya_hand'])}"
             f" → 这个点数 {e['liya_count']} 颗；阁下 {hand_str(e['player_hand'])}"
             f" → {e['player_count']} 颗")
    L.append(f"- 实际合计 {e['actual']} 颗 vs 叫 {e['bid']['qty']} 颗"
             f"（{bid_str(e['bid'])}）→ " + ("叫骰成立" if e["bidder_wins"] else "叫骰吹破"))
    L.append(f"- {_who(e['challenger'])}：{OPEN_CN.get(e['penalty'], str(e['penalty']) + ' 倍')}"
             f" ｜ {_who(e['loser'])}掉 {e['penalty']} 颗"
             + ("（围骰/全骰翻倍）" if e.get("pen_quan") else "")
             + f" → 剩：本天使 {e['liya_left']} 颗、阁下 {e['player_left']} 颗")
    return L


def build_archive_md(d, slug):
    """一盘的账写成给人看的 md。经过 / 摊牌 / 复算全部照抄引擎自己的结算，一手不手写。"""
    winner = d["over"]
    res_cn = RESULT_CN[winner]
    dice = d.get("dice") or max(d["liya"]["count"], d["player"]["count"], 1)
    level = d.get("level", 2)
    variants = d.get("variants") or []
    meta = {"slug": slug, "date": slug[:10], "game": GAME_NAME, "mode": f"{dice}骰",
            "result": res_cn, "winner": winner, "rounds": d.get("round", 1),
            "level": level, "variants": variants, "opponent": OPPONENT}
    L = [f"# 大话骰对局存档 · {slug[:10]}", "",
         f"<!-- meta {json.dumps(meta, ensure_ascii=False)} -->", "",
         f"**局制** 每人 {dice} 颗（输一局掉一颗 / 劈 2 / 反劈 4，掉光出局）｜**档位** {level}",
         "**变体** " + ("、".join(f"{VARIANT_LABEL[v]}（{v}）" for v in variants)
                        if variants else "无（房源版）"),
         f"**局数** {d.get('round', 1)} 局收桌｜**结果：{res_cn}**（对方骰子掉光）", "",
         "## 逐局经过（从存档 `history` 现生成）", ""]
    hist = d.get("history") or []
    if not hist:
        L.append("- （这盘由旧版引擎落盘，没有逐局记录；看下面的摊牌与叫骰链。）")
        if d.get("chain"):
            L.append("- 最后一局叫骰链：" + " → ".join(
                f"{'莉' if b.get('by') == 'liya' else '阁'}{bid_str(b)}" for b in d["chain"]))
        L.append("")
    for e in hist:
        L += _round_block(e) + [""]

    L += ["## 终局摊牌（照抄 `reveal`）", "", "```",
          f"本天使：{hand_str(d['liya']['hand'])}（剩 {d['liya']['count']} 颗）",
          f"阁下：{hand_str(d['player']['hand'])}（剩 {d['player']['count']} 颗）", "```", "",
          "## 公平自证（照抄 `check`）", "", "```",
          *cmd_check_lines(d), "```", "",
          "## 口径", "",
          "- 本天使既坐庄又下桌：AI 只吃自己的手牌 + 公开叫骰史，阁下的手牌一个字不进决策路径。",
          "- **蒙眼**：文字层双方只报颗数，**骰面只走棋谱图**——本天使全程未 vision 读图、未 read 状态 json（对局进行中）。",
          "- 审计：每次运行本天使可见的输出落 `<state>.audit.log`（⚠️ 含本天使手牌明文，审计请等收桌）。",
          "- 跨局对手画像（`profile.json`）只从公开信息提炼：叫骰 / 谁质疑 / 摊牌后的实际颗数。",
          "- 原始状态文件（含双方骰子）：同目录同名 `.json`。"]
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
        names = [str(VARIANT_LABEL.get(v, v)) for v in (meta.get("variants") or [])]
        vs = "、".join(names) or "—"
        out.append(f"| {meta.get('date','')} | {meta.get('mode','')} | {meta.get('result','')} "
                   f"| {meta.get('rounds','')} | {meta.get('level','')} | {vs} |")
    out.append("")
    for meta, fn in rows:
        base = fn[:-3]
        out.append(f"- [{meta.get('date','')} · {meta.get('mode','')} · {meta.get('result','')}]"
                   f"({fn})｜[原始状态 json]({base}.json)")
    out.append(INDEX_TAIL)
    with open(os.path.join(archive, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return len(rows)


def _ledger_script():
    for c in (LEDGER_SCRIPT, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.py")):
        if os.path.exists(c):
            return c
    return None


def cmd_check_lines(d):
    """cmd_check 的文本版（close 要照抄进存档 md）。"""
    if "salt" not in d:
        return ["这盘没有承诺记录。"]
    now = commit_of(d["salt"], d["liya"]["hand"], d["player"]["hand"])
    return [f"承诺哈希（开局公布的那条）：{d['commit']}",
            f"独立复算：{now}",
            "独立复算 → 一致 ✓" if now == d["commit"] else "独立复算 → 对不上 ✗"]


def cmd_close(a):
    d = load(a.state)
    if not d.get("over"):
        sys.exit("还没收桌——一方骰子掉光才收（没终局的盘不存档）。")
    if d.get("closed_as"):
        sys.exit(f"这盘已经收过了（{d['closed_as']}）——要再收先 new 开新盘。")
    winner = d["over"]
    res_cn = RESULT_CN[winner]
    dice = d.get("dice") or max(d["liya"]["count"], d["player"]["count"], 1)
    archive = a.archive
    base_slug = (f"{datetime.date.today().isoformat()}-{dice}骰-{res_cn}-{OPPONENT}vs莉娅")
    slug, i = base_slug, 2
    while os.path.exists(os.path.join(archive, slug + ".md")):   # 同一天连开两盘不许互相盖
        slug, i = f"{base_slug}-{i}", i + 1
    os.makedirs(archive, exist_ok=True)
    dst_json = os.path.join(archive, slug + ".json")
    shutil.copyfile(a.state, dst_json)
    md_path = os.path.join(archive, slug + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(build_archive_md(d, slug))
    n = rebuild_index(archive)
    d["closed_as"] = slug          # 落了戳：同一盘再收会被挡，免得重复记账
    save(d, a.state)

    print(f"  收桌：{dice} 骰 · {d.get('round', 1)} 局 · {res_cn}")
    print(f"  存档：{_rel_or_abs(dst_json)}")
    print(f"        {_rel_or_abs(md_path)}")
    print(f"  INDEX：{_rel_or_abs(os.path.join(archive, 'INDEX.md'))}（按存档重建，现有 {n} 档）")

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
        print(f"  git：cd {ROOT} && git add {_rel_or_abs(archive)} {_rel_or_abs(a.ledger)}"
              f" && git commit -q -m \"{GAME_NAME}:{slug[5:]}\"")
    else:
        print(f"  git：cd {ROOT} && git add {_rel_or_abs(archive)}"
              f" && git commit -q -m \"{GAME_NAME}:{slug[5:]}\"")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state", default=argparse.SUPPRESS)
    common.add_argument("--png", nargs="?", const=True, default=argparse.SUPPRESS,
                        help="棋谱图路径（可省 = 写 <state同名>.png；默认就出图）")
    common.add_argument("--no-png", action="store_true", default=argparse.SUPPRESS,
                        help="这一手不出图")

    p = argparse.ArgumentParser(prog="liars-dice.py", description="天界大话骰 · 聊天版")
    p.add_argument("--state", default=DEFAULT_STATE)
    p.add_argument("--png", nargs="?", const=True, default=None,
                   help="棋谱图路径（可省 = 写 <state同名>.png；默认就出图）")
    p.add_argument("--no-png", action="store_true", help="这一手不出图")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", parents=[common])
    n.add_argument("--dice", type=int, default=INITIAL_DICE)
    n.add_argument("--level", type=int, default=2, choices=(1, 2, 3))
    n.add_argument("--variant", action="append", choices=VARIANT_NAMES,
                   help="开一个变体（可重复）：fanpi 反劈 / minbid 起叫下限 / quan 围骰全骰")
    n.add_argument("--variants", metavar="N,N|all", help="逗号分隔的变体，或 all")
    n.add_argument("--profile", default=DEFAULT_PROFILE, help="对手画像文件")
    n.add_argument("--no-profile", action="store_true", help="这盘不记画像")
    n.add_argument("--force", action="store_true"); n.set_defaults(fn=cmd_new)

    s = sub.add_parser("show", parents=[common])
    s.add_argument("--player", action="store_true")
    s.set_defaults(fn=cmd_show)

    b = sub.add_parser("bid", parents=[common]); b.add_argument("text"); b.set_defaults(fn=cmd_bid)
    sub.add_parser("open", parents=[common]).set_defaults(fn=cmd_open)
    sub.add_parser("pi", parents=[common]).set_defaults(fn=cmd_pi)
    sub.add_parser("fanpi", parents=[common]).set_defaults(fn=cmd_fanpi)
    sub.add_parser("reveal", parents=[common]).set_defaults(fn=cmd_reveal)
    sub.add_parser("check", parents=[common]).set_defaults(fn=cmd_check_only)
    pf = sub.add_parser("profile", parents=[common])
    pf.add_argument("--path", default=None, help="画像文件（默认 workspace/records/liars-dice/profile.json）")
    pf.add_argument("--clear", action="store_true")
    pf.set_defaults(fn=cmd_profile)

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
# 大话骰尚有若干问题待修，修好之前谁都不许碰：开局 / 推进一律挡下，
# 只留只读与收尾。
# 解除：MAINTENANCE["on"] = False（记得同步 temp/ 里的部署副本）；
# 维护作业（自测 / 修完验收）用环境变量 LIARS_MAINT_OVERRIDE=1 旁路。
MAINTENANCE = {"on": True, "since": "2026-09-15"}
_MAINT_ALLOWED = {"cmd_show", "cmd_check_only", "cmd_close"}


def maintenance_gate(a):
    if os.environ.get("LIARS_MAINT_OVERRIDE") == "1":
        return
    if not MAINTENANCE["on"] or getattr(a.fn, "__name__", "") in _MAINT_ALLOWED:
        return
    print(f"⛔ 大话骰 · 维护中（{MAINTENANCE['since']} 起）\n"
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

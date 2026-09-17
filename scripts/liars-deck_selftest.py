#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""liars-deck_selftest.py — 聊天版骗子牌自测（临时盘，不碰数据根的 temp/）

跑法：python3 scripts/liars-deck_selftest.py
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "liars-deck.py")

_spec = importlib.util.spec_from_file_location("ldk", SCRIPT)
LD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(LD)

FAIL = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


def run(state, *args, bypass=True):
    env = dict(os.environ)
    if bypass:
        env["LIARS_MAINT_OVERRIDE"] = "1"
    else:
        env.pop("LIARS_MAINT_OVERRIDE", None)
    r = subprocess.run([sys.executable, SCRIPT, "--state", state, *args],
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def backs(png, y, n=5):
    """那片区域里有几张是扣着的（背面灰 196,184,166）——抓「图把暗牌画明」的回归。"""
    from PIL import Image
    im = Image.open(png).convert("RGB")
    return sum(1 for i in range(n)
               if all(abs(a - b) < 18 for a, b in
                      zip(im.getpixel((46 + 94 * i + 16, y)), (196, 184, 166))))


def seed(path, *, target="Q", liya_hand=(), player_hand=(), awaiting="player",
         last_play=None, final=False, lchamber=0, pchamber=0, lbullet=3, pbullet=5,
         over=None, round=2, level=2, played=None):
    """摆一个已知局面；两份承诺按状态重算，保证 check 能对上"""
    d = {"version": 1, "level": level, "round": round, "over": over,
         "liya": {"hand": list(liya_hand), "chamber": lchamber, "bullet": lbullet, "alive": True},
         "player": {"hand": list(player_hand), "chamber": pchamber, "bullet": pbullet, "alive": True},
         "dealt": {"liya": list(liya_hand), "player": list(player_hand)},
         "target": target, "awaiting": awaiting, "last_play": last_play, "final": final,
         "played": played or {"liya": [], "player": []}, "chain": [],
         "salt_g": "gamegamegamegame", "salt_r": "roundroundroundr"}
    d["commit_g"] = LD.commit_of(d["salt_g"], lbullet, pbullet)
    d["commit_r"] = LD.commit_of(d["salt_r"], d["dealt"]["liya"], d["dealt"]["player"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    return d


def main():
    d = tempfile.mkdtemp(prefix="ldk-selftest-")
    st = os.path.join(d, "ldk.json")
    try:
        # ── 维护闸（2026-09-15 阁下令）──
        rc, out = run(st, "new", bypass=False)
        check("维护闸：推进命令被挡", rc != 0 and "维护中" in out, out.strip())
        rc2, out2 = run(st, "show", bypass=False)
        check("维护闸：只读命令放行", "维护中" not in out2, out2.strip())

        # ── 开局 ──
        rc, out = run(st, "new")
        s = json.load(open(st, encoding="utf-8"))
        check("new 跑通 + 落盘", rc == 0 and os.path.exists(st), out.strip())
        check("双方各 5 张", len(s["liya"]["hand"]) == 5 and len(s["player"]["hand"]) == 5)
        check("牌只在 Q/K/A/J", set(s["liya"]["hand"] + s["player"]["hand"]) <= {"Q", "K", "A", "J"})
        check("目标牌合法", s["target"] in ("Q", "K", "A"))
        check("两份承诺各 64 位", len(s["commit_g"]) == 64 and len(s["commit_r"]) == 64)
        check("游戏级承诺 = 独立复算（子弹）",
              s["commit_g"] == LD.commit_of(s["salt_g"], s["liya"]["bullet"], s["player"]["bullet"]))
        check("开局轮到阁下", "轮到阁下" in out, out)
        check("本天使视角带暗牌警示", "别贴进群" in out)

        # ── 视角隔离 + 公平自证 ──
        rc, out = run(st, "show", "--player")
        check("show --player 不泄本天使手牌", "本天使的手牌：5 张（扣着）" in out, out)
        rc, out = run(st, "check")
        check("check → 两条都一致 ✓", out.count("一致 ✓") == 2, out)

        # ── 蒙眼（文字层不给任何一方暗牌明文）──
        # 哨兵一律用**受控局面**：随机发牌下「本天使手牌」那行会打印本天使自己的牌，
        # 而两副手牌同花（点数组合相同）的概率约 3%（20 张牌只有 4 种点数，重复度高）——
        # 拿随机局的手牌当哨兵会随机转红（实战踩过）
        seed(st, player_hand=["J", "J", "K", "K", "K"], liya_hand=["A", "A", "Q", "Q", "Q"],
             awaiting="player")
        rc, out = run(st, "show", "--no-png")
        check("蒙眼：本天使视角不含阁下手牌明文", "J J K K K" not in out, out)
        check("蒙眼：阁下视角也不含（牌面走图）",
              "J J K K K" not in run(st, "show", "--player", "--no-png")[1])
        check("蒙眼：手牌只报张数", "阁下的手牌：5 张" in out, out)

        # ── 出牌：合法 / 规则挡 ──
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=["Q", "K", "A", "A", "K"])
        rc, out = run(st, "play", "QK")
        check("play 合法出牌跑通", rc == 0 and "阁下出 2 张" in out, out)
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=["Q", "K", "A", "A", "K"])
        rc, out = run(st, "play", "K", "K")
        check("手里没有的牌被挡", rc != 0 and "真有的牌" in out, out)
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=["Q", "K", "A", "A", "K"])
        rc, out = run(st, "play", "QKAJ")
        check("超过 3 张被挡", rc != 0, out)
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=["Q", "K", "A", "A", "K"],
             awaiting="liya")
        rc, out = run(st, "play", "Q")
        check("轮到本天使时阁下不能出", rc != 0 and "轮到本天使" in out, out)

        # ── 编号出牌（出牌这条路也不漏牌面）──
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=["Q", "K", "A", "A", "K"])
        rc, out = run(st, "play", "1", "3")
        # 哨兵用阁下那串完整手牌（明文打印才会整串出现）；别拿 "K Q" 这种两字组合，
        # 本天使质疑后重发牌，新手牌排序里真可能蹦出 K Q —— 会随机误伤
        check("编号出牌跑通（1 3 = 第 1、3 张）",
              rc == 0 and "阁下出 2 张" in out and "Q K A J A" not in out, out)
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=["Q", "K", "A", "A", "K"])
        rc, out = run(st, "play", "9")
        check("编号超范围被挡、不列手牌", rc != 0 and "编号 9 不存在" in out and "Q K A J A" not in out,
              out)
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=["Q", "K", "A", "A", "K"])
        rc, out = run(st, "play", "1", "1")
        check("编号重复被挡", rc != 0 and "不能出两次" in out, out)
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=["Q", "K", "A", "A", "K"])
        rc, out = run(st, "play", "1", "J")
        check("编号+牌面混写被挡", rc != 0, out)
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=["Q", "K", "A", "A", "K"])
        rc, out = run(st, "play", "QK")
        check("牌面出牌仍可用、但提示会暴露", rc == 0 and "本天使看得见" in out, out)

        # ── 已出记录只报张数（原来把阁下出过的牌印成明文）──
        # 本天使手牌给成 A A，免得自用行跟哨兵「K Q」撞车
        seed(st, player_hand=["Q", "K"], liya_hand=["A", "A"],
             played={"liya": [], "player": ["K", "Q"]}, awaiting="player")
        rc, out = run(st, "show", "--no-png")
        check("已出只报张数、不印阁下出过的牌面", "本局已出：阁下 2 张" in out and "K Q" not in out, out)
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=[],
             last_play={"by": "liya", "cards": ["Q", "Q"]}, awaiting="player", final=True)
        rc, out = run(st, "play", "Q")
        check("本天使出空后阁下必须质疑，出牌被挡", rc != 0 and "必须质疑" in out, out)

        # ── 质疑：本天使出假牌 → 本天使吃枪 ──
        seed(st, target="Q", liya_hand=["Q", "A", "J"], player_hand=["K", "K", "K", "A", "A"],
             last_play={"by": "liya", "cards": ["K", "K"]}, awaiting="player")
        rc, out = run(st, "open")
        check("出假牌 → 质疑成立", "有假牌——质疑成立！" in out, out)
        check("出假牌方（本天使）吃枪", "本天使对着自己扣扳机" in out, out)
        check("空仓不死、指针走一格", "空仓" in out and "第 1 仓" in out, out)
        # 结算图不许摊本天使整手牌（2026-09-15 实战漏过：cmd_open 曾用 reveal=True，
        # 把结算后新一局的本天使手牌整手画进图里送对手）
        _png = os.path.join(d, "ldk.png")
        _n = len(json.load(open(st, encoding="utf-8"))["liya"]["hand"])
        check("阁下质疑的结算图：本天使那排扣着（不露整手牌）",
              backs(_png, 166, _n) == _n, f"{_n} 张里只 {backs(_png, 166, _n)} 张扣着")
        check("正对照：阁下那排是明的（不是背面灰）",
              backs(_png, 298, 5) == 0, f"阁下排有 {backs(_png, 298, 5)} 张判成背面")
        t = json.load(open(st, encoding="utf-8"))
        check("没死后开新局（输家先出 → 本天使已自动出牌，轮阁下）",
              t["round"] == 3 and t["awaiting"] == "player", f'round={t["round"]} awaiting={t["awaiting"]}')

        # ── 质疑：本天使出真牌 → 质疑者吃枪 ──
        seed(st, target="Q", liya_hand=["A", "A", "K"], player_hand=["K", "K", "K", "A", "A"],
             last_play={"by": "liya", "cards": ["Q", "J"]}, awaiting="player")
        rc, out = run(st, "open")
        check("全真 → 质疑失败", "全是目标牌——质疑失败。" in out, out)
        check("质疑者（阁下）吃枪", "阁下对着自己扣扳机" in out, out)

        # ── 中弹出局 ──
        seed(st, target="Q", liya_hand=["Q", "A", "J"], player_hand=["K", "K", "K", "A", "A"],
             last_play={"by": "liya", "cards": ["K", "K"]}, awaiting="player", lbullet=0, lchamber=0)
        rc, out = run(st, "open")
        check("弹仓对上 → 中弹出局", "砰！中弹出局。" in out, out)
        t = json.load(open(st, encoding="utf-8"))
        check("终局写进状态（over=player）", t.get("over") == "player", str(t.get("over")))

        # ── 阁下出空 → 本天使自动质疑 ──
        seed(st, target="Q", liya_hand=["K", "K", "A"], player_hand=["Q", "Q"],
             awaiting="player")
        rc, out = run(st, "play", "QQ")
        check("阁下出空 → 本天使自动质疑", rc == 0 and "质疑" in out, out)

        # ── 出空 = 强制质疑（官方口径；旧 pass 已废除）──
        seed(st, target="Q", liya_hand=[], player_hand=["K", "K", "K", "A", "A"],
             last_play={"by": "liya", "cards": ["Q", "Q"]}, awaiting="player", final=True)
        rc, out = run(st, "open")
        check("出空 + 最后一手真 → 质疑者吃枪", rc == 0 and "全是目标牌——质疑失败。" in out
              and "阁下对着自己扣扳机" in out, out)
        seed(st, target="Q", liya_hand=[], player_hand=["K", "K", "K", "A", "A"],
             last_play={"by": "liya", "cards": ["K", "K"]}, awaiting="player", final=True)
        rc, out = run(st, "open")
        check("出空 + 最后一手假 → 出空方吃枪（最后一手必须真）",
              "有假牌——质疑成立！" in out and "本天使对着自己扣扳机" in out, out)
        rc, out = run(st, "pass")
        check("pass 子命令已废除", rc != 0, out)

        # ── AI 概率（只吃自己手牌）──
        d2 = {"liya": {"hand": ["Q", "Q", "Q", "Q", "Q"]}, "target": "Q"}
        check("本天使手里 5 张 Q → 对家全真的概率低", LD.ai_p_true(d2, 2) < 0.1,
              str(LD.ai_p_true(d2, 2)))
        d3 = {"liya": {"hand": ["K", "K", "K", "K", "K"]}, "target": "Q"}
        check("本天使没 Q/王 → 对家 2 张全真概率更低", LD.ai_p_true(d3, 2) < 0.35,
              str(LD.ai_p_true(d3, 2)))
        check("池里不够 → 概率归零", LD.ai_p_true({"liya": {"hand": ["K"] * 5}, "target": "Q"}, 9) == 0.0)
        outs = {tuple(LD.ai_deck_play({"liya": {"hand": ["Q", "Q", "K", "K", "A"]}, "target": "Q"}, 3))
                for _ in range(80)}
        check("AI 出牌带随机（多次调用不唯一）", len(outs) > 1, str(outs)[:120])
        lens = {len(LD.ai_deck_play({"liya": {"hand": ["K", "K"]}, "target": "Q"}, 3)) for _ in range(40)}
        check("AI 全假手不会一次出空（出空=必被强制质疑）", lens == {1}, str(lens))

        # ── 篡改可测 ──
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=["Q", "K", "A", "A", "K"])
        t = json.load(open(st, encoding="utf-8"))
        t["liya"]["bullet"] = (t["liya"]["bullet"] + 1) % 6
        json.dump(t, open(st, "w", encoding="utf-8"), ensure_ascii=False)
        rc, out = run(st, "check")
        check("改过子弹 → 对不上 ✗", "对不上 ✗" in out, out)

        # ── 出图（默认出；--no-png 关）──
        seed(st, target="K", liya_hand=["Q", "Q", "J"], player_hand=["K", "K", "A", "A", "J"],
             last_play={"by": "player", "cards": ["K", "K"]}, awaiting="liya")
        auto = os.path.join(d, "ldk.png")
        png = os.path.join(d, "x.png")
        rc, out = run(st, "show", "--png", png)
        check("--png 指定路径出图（>5KB）", rc == 0 and os.path.exists(png) and os.path.getsize(png) > 5000,
              out or "文件没生成")
        if os.path.exists(auto):
            os.remove(auto)
        rc, out = run(st, "show")
        check("默认就出图 → 写 <state同名>.png", os.path.exists(auto), out)
        if os.path.exists(auto):
            os.remove(auto)
        rc, out = run(st, "show", "--no-png")
        check("--no-png 关图", not os.path.exists(auto), out)

        # ── 蒙眼：局中 reveal 不摊阁下那半 ──
        seed(st, target="Q", liya_hand=["Q", "A", "J"], player_hand=["K", "K", "K", "A", "A"],
             last_play={"by": "player", "cards": ["K"]}, awaiting="liya")
        rc, out = run(st, "reveal")
        check("局中 reveal 不摊阁下暗牌", rc == 0 and "阁下：K K K" not in out, out)

        # ── 审计日志（阁下可查本天使看到了什么）──
        # 单独开一桌跑：日志是累积的，混着前面随机发牌的记录，受控哨兵会被别局的同花误伤
        st2 = os.path.join(d, "ldk2.json")
        seed(st2, player_hand=["J", "J", "K", "K", "K"], liya_hand=["A", "A", "Q", "Q", "Q"],
             awaiting="player")
        run(st2, "show", "--no-png")
        alog = os.path.join(d, "ldk2.audit.log")
        check("审计日志自动落盘且非空", os.path.exists(alog) and os.path.getsize(alog) > 0)
        logtxt = open(alog, encoding="utf-8").read()
        check("对局中：审计日志里没有阁下手牌明文", "J J K K K" not in logtxt, logtxt[-300:])
        # 正对照：摊开后阁下手牌**该**进日志 —— 证明上面那个哨兵不是空转
        seed(st2, player_hand=["J", "J", "K", "K", "K"], liya_hand=["A", "A", "Q", "Q", "Q"],
             awaiting="player", over="liya")
        run(st2, "reveal", "--no-png")
        check("正对照：摊牌后阁下手牌进日志（哨兵有效）",
              "J J K K K" in open(alog, encoding="utf-8").read())

        # ── 一手一条命令：同一次运行给出「可贴块 + 自用行」──
        seed(st, player_hand=["Q", "K", "A", "J", "A"], liya_hand=["Q", "K", "A", "A", "K"])
        rc, out = run(st, "play", "1", "3", "--no-png")
        check("一手一条命令：同一次运行给出可贴块 + 自用行",
              "── 发群（以下整块照贴）" in out and "── 发群到此" in out and "别贴进群" in out
              and out.index("── 发群到此") < out.index("别贴进群"), out)
        seg = out[out.index("── 发群（以下整块照贴）"):out.index("── 发群到此")]
        check("可贴块里没有阁下手牌明文", "Q K A J A" not in seg, seg)

        # ── 收桌：一条命令办完（存档 + INDEX + 台账）──
        seed(st, target="K", liya_hand=["J", "J", "K", "K", "Q"], player_hand=["K", "Q", "Q"],
             awaiting="player", over="liya")
        t = json.load(open(st, encoding="utf-8"))
        t["chain"] = [{"round": 1, "target": "K", "by": "player", "cards": ["A", "Q"],
                       "fakes": ["A", "Q"], "liar": True, "challenger": "liya",
                       "loser": "player", "died": True}]
        json.dump(t, open(st, "w", encoding="utf-8"), ensure_ascii=False)
        arch = os.path.join(d, "arch")
        led = os.path.join(d, "led.json")
        rc, out = run(st, "close", "--archive", arch, "--ledger", led, "--no-png")
        check("close 跑通", rc == 0, out)
        jsons = [f for f in os.listdir(arch) if f.endswith(".json")]
        mds = [f for f in os.listdir(arch) if f.endswith(".md") and f != "INDEX.md"]
        check("close 落 json + md + INDEX",
              len(jsons) == 1 and len(mds) == 1 and os.path.exists(os.path.join(arch, "INDEX.md")),
              str(sorted(os.listdir(arch))))
        body = open(os.path.join(arch, mds[0]), encoding="utf-8").read()
        check("存档 md 含逐局经过 + 摊牌 + 承诺复算",
              "阁下出 2 张：A Q" in body and "本天使：J J K K Q" in body
              and body.count("一致 ✓") == 2, body)
        idx = open(os.path.join(arch, "INDEX.md"), encoding="utf-8").read()
        check("INDEX 自动重建、含这一档", "莉娅胜" in idx and mds[0] in idx, idx)
        lj = json.load(open(led, encoding="utf-8"))
        check("台账记了一笔（骗子牌 · 胜）",
              bool(lj["log"]) and lj["log"][-1]["game"] == "骗子牌"
              and lj["log"][-1]["result"] == "win", str(lj))
        rc, out = run(st, "close", "--archive", arch, "--ledger", led, "--no-png")
        check("close 幂等（重收被挡、不多开一档、不重复记账）",
              rc != 0 and "已经收过" in out
              and len([f for f in os.listdir(arch) if f.endswith(".md") and f != "INDEX.md"]) == 1
              and len(json.load(open(led, encoding="utf-8"))["log"]) == 1, out)

        seed(st, liya_hand=["Q"], player_hand=["K"], awaiting="player")
        rc, out = run(st, "close", "--archive", arch, "--ledger", led, "--no-png")
        check("没收桌时 close 被挡", rc != 0 and "还没收桌" in out, out)
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""liars-dice_selftest.py — 聊天版大话骰自测（临时盘，不碰数据根的 temp/）

跑法：python3 scripts/liars-dice_selftest.py
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "liars-dice.py")

_spec = importlib.util.spec_from_file_location("ld", SCRIPT)
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


def dark_pips(png, y0, y1, x0=46, x1=440):
    """数一片区域里的深色像素（骰子的黑点）。扣着的骰画「?」（灰）——不该有黑点。"""
    from PIL import Image
    im = Image.open(png).convert("RGB")
    return sum(1 for y in range(y0, y1) for x in range(x0, x1)
               if all(v < 85 for v in im.getpixel((x, y))))


def seed(path, liya_hand, player_hand, last_bid, *, lcount=None, pcount=None, over=None,
         variants=None, pending=False):
    """摆一个已知局面；承诺哈希按真实骰子重算，保证 check 能对上"""
    d = {"version": 1, "level": 2, "aggr": 0.5, "variants": list(variants or []),
         "liya": {"count": lcount if lcount is not None else len(liya_hand), "hand": list(liya_hand)},
         "player": {"count": pcount if pcount is not None else len(player_hand), "hand": list(player_hand)},
         "round": 2, "last_bid": last_bid, "chain": [last_bid] if last_bid else [],
         "over": over, "salt": "deadbeefdeadbeef",
         "profile": os.path.join(os.path.dirname(os.path.abspath(path)), "profile.json")}
    if pending:
        d["_pending"] = True
    d["commit"] = LD.commit_of(d["salt"], d["liya"]["hand"], d["player"]["hand"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    return d


def B(qty, face, by="liya", zhai=False, fei=False):
    return {"qty": qty, "face": face, "zhai": zhai, "fei": fei, "by": by}


def main():
    d = tempfile.mkdtemp(prefix="ld-selftest-")
    st = os.path.join(d, "ld.json")
    try:
        # ── 维护闸（2026-09-15 阁下令）──
        rc, out = run(st, "new", bypass=False)
        check("维护闸：推进命令被挡", rc != 0 and "维护中" in out, out.strip())
        rc2, out2 = run(st, "show", bypass=False)
        check("维护闸：只读命令放行", "维护中" not in out2, out2.strip())

        # ── 开局 ──
        rc, out = run(st, "new", "--dice", "5")
        s = json.load(open(st, encoding="utf-8"))
        check("new 跑通 + 落盘", rc == 0 and os.path.exists(st), out.strip())
        check("双方各 5 颗", len(s["liya"]["hand"]) == 5 and len(s["player"]["hand"]) == 5)
        check("承诺哈希 64 位", len(s.get("commit", "")) == 64)
        check("承诺 = 独立复算", s["commit"] == LD.commit_of(s["salt"], s["liya"]["hand"], s["player"]["hand"]))
        check("开局轮到阁下", "轮到阁下" in out, out)
        check("本天使视角带暗牌警示", "别贴进群" in out)

        # ── 视角隔离 ──
        rc, out = run(st, "show", "--player")
        check("show --player 不泄本天使暗牌", "本天使的骰盅" not in out and "阁下的骰盅" in out, out)
        rc, out = run(st, "check")
        check("check → 一致 ✓", "一致 ✓" in out, out)

        # ── 蒙眼（文字层不给任何一方骰面明文；骰面只走图）──
        # 哨兵用受控局面：随机局里本天使自用行会打印本天使自己的骰，撞车会随机转红
        seed(st, [6, 6, 5], [3, 3, 4], B(3, 4, by="player"))
        rc, out = run(st, "show")
        check("蒙眼：本天使视角不含阁下骰面明文", "⚂3 ⚂3 ⚃4" not in out, out)
        check("蒙眼：阁下只报颗数（骰面走图）", "阁下的骰盅：3 颗" in out, out)
        check("蒙眼：本天使视角也不含阁下骰面（--player 同理）",
              "⚂3 ⚂3 ⚃4" not in run(st, "show", "--player")[1])

        # ── 顺子不出现 ──
        ok = True
        for _ in range(8):
            run(st, "new", "--dice", "5", "--force")
            t = json.load(open(st, encoding="utf-8"))
            if LD.is_straight(t["liya"]["hand"]) or LD.is_straight(t["player"]["hand"]):
                ok = False
        check("双方顺子都重摇（8 次开局）", ok)

        # ── 叫骰：中文数字容错 ──
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], None)
        rc, out = run(st, "bid", "三个5")
        t = json.load(open(st, encoding="utf-8"))
        check("中文数字叫骰认（三个5）", rc == 0 and t["chain"][0]["qty"] == 3, out)

        # ── 非法叫骰被挡 ──
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], B(5, 6))
        rc, out = run(st, "bid", "2个3")
        t = json.load(open(st, encoding="utf-8"))
        check("低于上家被挡", rc != 0 and len(t["chain"]) == 1, out)

        # ── 斋里不飞不给脱斋 ──
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], B(3, 4, zhai=True))
        rc1, out1 = run(st, "bid", "4个5")
        rc2, out2 = run(st, "bid", "6个5飞")
        check("斋状态裸叫被挡", rc1 != 0 and "斋" in out1, out1)
        check("飞破斋放行", rc2 == 0, out2)

        # ── 时机 ──
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], B(3, 4, by="player"))
        rc, out = run(st, "bid", "4个6")
        check("轮到本天使时阁下不能叫", rc != 0 and "轮到本天使" in out, out)
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], None)
        rc, out = run(st, "open")
        check("没人叫时不能开", rc != 0, out)

        # ── 结算：吹破（本天使输）──
        seed(st, [3, 3, 3, 3, 3], [1, 1, 2, 2, 2], B(9, 3))
        rc, out = run(st, "open")
        check("豹子 +1 算对（9个3 → 实际 8）", "实际合计 8 颗" in out, out)
        check("吹破判定", "叫骰吹破" in out, out)
        check("叫骰者（本天使）掉 1", "本天使掉 1 颗" in out, out)
        # 结算图不许摊本天使下一局的骰（2026-09-15 修：_settle 曾用 reveal=True，
        # 而 resolve 内部已经 deal 了新骰 —— 图亮了就是把新骰白送阁下）
        _png = os.path.join(d, "ld.png")
        check("阁下开的结算图：本天使那排扣着（无点数）",
              dark_pips(_png, 150, 214) == 0, f"深色点数像素={dark_pips(_png, 150, 214)}")
        check("正对照：阁下那排是明的（有点数）",
              dark_pips(_png, 270, 334) > 0, "阁下排一个黑点都没有——扫描区域不对？")

        # ── 结算：斋（1 不算万能）──
        seed(st, [3, 3, 3, 3, 3], [1, 1, 2, 2, 2], B(6, 3, zhai=True))
        rc, out = run(st, "open")
        check("斋里 1 不算（实际 6）", "实际合计 6 颗" in out, out)
        check("叫骰成立", "叫骰成立" in out, out)
        check("质疑者（阁下）掉 1", "阁下掉 1 颗" in out, out)

        # ── 劈：双倍 ──
        seed(st, [2, 2, 2, 2, 2], [5, 5, 5, 5, 5], B(3, 2))
        rc, out = run(st, "pi")
        check("劈 → 双倍惩罚（阁下掉 2）", "阁下掉 2 颗" in out, out)

        # ── 终局 ──
        seed(st, [1], [2], B(1, 6), lcount=1, pcount=1)
        rc, out = run(st, "open")
        check("掉光判终局", "本天使赢下这一盘" in out, out)
        t = json.load(open(st, encoding="utf-8"))
        check("终局写进状态（over=liya）", t.get("over") == "liya")

        # ── 篡改可测 ──
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], None)
        t = json.load(open(st, encoding="utf-8"))
        t["liya"]["hand"] = [6, 6, 6, 6, 6]
        json.dump(t, open(st, "w", encoding="utf-8"), ensure_ascii=False)
        rc, out = run(st, "check")
        check("改过骰子 → 对不上 ✗", "对不上 ✗" in out, out)

        # ── 变体：开关落盘 + show 公开 ──
        rc, out = run(st, "new", "--dice", "5", "--variant", "fanpi", "--variant", "minbid", "--force")
        t = json.load(open(st, encoding="utf-8"))
        check("变体开关落盘", t.get("variants") == ["fanpi", "minbid"], str(t.get("variants")))
        check("开局报变体", "反劈" in out and "起叫下限" in out, out)
        rc, out = run(st, "show")
        check("show 公开变体", "变体：" in out and "反劈" in out, out)
        rc, out = run(st, "new", "--dice", "5", "--variants", "all", "--force")
        t = json.load(open(st, encoding="utf-8"))
        check("--variants all → 三个", sorted(t.get("variants", [])) == ["fanpi", "minbid", "quan"], out)
        rc, out = run(st, "new", "--dice", "5", "--variant", "nope", "--force")
        check("不认识的变体被挡", rc != 0, out)

        # ── 起叫下限（minbid）──
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], None, variants=["minbid"])
        rc, out = run(st, "bid", "2个5")
        check("minbid：非斋起叫 <⌈10×0.3⌉ 被挡", rc != 0 and "下限" in out, out)
        rc, out = run(st, "bid", "3个5")
        check("minbid：非斋起叫 ≥3 放行", rc == 0, out)
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], None, variants=["minbid"])
        rc, out = run(st, "bid", "1个1")
        check("minbid：喊 1 起叫 <2 被挡", rc != 0, out)
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], None, variants=["minbid"])
        rc, out = run(st, "bid", "1个5斋")
        check("minbid：斋起叫 <2 被挡", rc != 0, out)
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], None)
        rc, out = run(st, "bid", "1个5")
        check("minbid 默认关：1个5 放行", rc == 0, out)

        # ── 围骰 / 全骰（quan）──
        seed(st, [1, 1, 1, 1, 1], [6, 6, 6, 6, 6], B(14, 6), variants=["quan"])
        rc, out = run(st, "open")
        check("quan：5个1 非斋当 7个6（合计 14）", "实际合计 14 颗" in out and "叫骰成立" in out, out)
        seed(st, [1, 1, 4, 4, 4], [1, 1, 2, 2, 2], B(9, 4), variants=["quan"])
        rc, out = run(st, "open")
        check("quan：围骰 2个1+3个4 = 6个4", "本天使：⚀1 ⚀1 ⚃4 ⚃4 ⚃4  → 这个点数 6 颗" in out, out)
        check("quan：被开者围骰判输 → 惩罚翻倍（掉 2）", "本天使掉 2 颗" in out and "翻倍" in out, out)
        seed(st, [1, 1, 4, 4, 4], [1, 1, 2, 2, 2], B(8, 4))
        rc, out = run(st, "open")
        check("quan 默认关：围骰不加成（合计 7 颗）", "实际合计 7 颗" in out, out)

        # ── 反劈（fanpi）──
        seed(st, [2, 2, 2, 2, 2], [5, 5, 5, 5, 5], B(3, 2), variants=["fanpi"])
        rc, out = run(st, "pi")
        check("fanpi：阁下劈、本天使反劈 → 阁下掉 4", "反劈" in out and "阁下掉 4 颗" in out, out)
        seed(st, [6, 6, 3, 4, 4], [1, 2, 2, 3, 5], B(5, 6), variants=["fanpi"])
        rc, out = run(st, "pi")
        check("fanpi：本天使估不稳 → 不反劈（劈 2 倍）", "本天使：反劈！" not in out and "本天使掉 2 颗" in out, out)
        seed(st, [6, 6, 6, 6, 6], [1, 1, 2, 2, 2], B(9, 6, by="player"), variants=["fanpi"], pending=True)
        rc, out = run(st, "fanpi")
        check("fanpi：本天使劈阁下 → 阁下反劈（4）", "阁下：反劈！" in out and "阁下掉 4 颗" in out, out)
        seed(st, [6, 6, 6, 6, 6], [1, 1, 2, 2, 2], B(9, 6, by="player"), variants=["fanpi"], pending=True)
        rc, out = run(st, "open")
        check("fanpi：本天使劈阁下 → 阁下认账（2）", "认劈" in out and "阁下掉 2 颗" in out, out)
        seed(st, [6, 6, 6, 6, 6], [1, 1, 2, 2, 2], B(9, 6, by="player"), variants=["fanpi"], pending=True)
        rc, out = run(st, "bid", "10个6")
        check("fanpi：待表态时不能再叫", rc != 0, out)

        # ── B · 跨局对手画像（只吃公开信息）──
        prof = os.path.join(d, "profile.json")
        if os.path.exists(prof):
            os.remove(prof)
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], B(4, 5, by="liya"))
        rc, out = run(st, "bid", "5个5")
        p = json.load(open(prof, encoding="utf-8"))
        check("画像记阁下叫骰（端到端）", p["player"]["bids"] == 1 and p["player"]["qty_sum"] == 5, str(p["player"]))
        os.remove(prof)   # 单测前清空，避免端到端那手的干扰
        LD.profile_settle({"profile": prof},
                          {"bid": B(4, 5, by="player"), "challenger": "player", "bidder_wins": True}, "pi")
        p = json.load(open(prof, encoding="utf-8"))
        check("画像记摊牌复核 + 劈", p["player"]["tested"] == 1 and p["player"]["tested_true"] == 1
              and p["player"]["pi"] == 1, str(p["player"]))
        json.dump({"version": 1, "updated": "", "player": {
            "bids": 10, "qty_sum": 8.0, "dice_sum": 10, "zhai": 0, "fei": 0,
            "challenges": 8, "pi": 2, "fanpi": 0, "tested": 10, "tested_true": 2}},
            open(prof, "w", encoding="utf-8"), ensure_ascii=False)
        pr = LD.profile_read({"profile": prof})
        check("画像提炼：诈叫率 0.8 / 激进 1.0",
              round(pr["bluff"], 3) == 0.8 and round(pr["aggr"], 3) == 1.0, str(pr))
        check("画像混合 aggr（0.5×0.2+0.5×1.0）",
              abs(LD.blended_aggr({"aggr": 0.2, "profile": prof}) - 0.6) < 1e-9)
        check("画像：诈叫率高 → 门槛压低、更爱开",
              LD.ai_should_challenge(B(4, 3), [6, 6, 6, 3, 3], 3, 0.5, False, 0.8) is True
              and LD.ai_should_challenge(B(4, 3), [6, 6, 6, 3, 3], 3, 0.5, False, None) is False)
        rc, out = run(st, "profile", "--path", prof)
        check("profile 命令能看", "对手画像" in out and "诈叫率" in out, out)
        rc, out = run(st, "profile", "--path", prof, "--clear")
        check("profile --clear 清掉", rc == 0 and not os.path.exists(prof), out)
        rc, out = run(st, "new", "--dice", "5", "--no-profile", "--force")
        tt = json.load(open(st, encoding="utf-8"))
        check("--no-profile 关掉画像", tt.get("profile") is None, out)

        # ── 出图 ──
        seed(st, [1, 2, 3, 4, 5], [6, 5, 4, 3, 2], B(3, 4))
        png = os.path.join(d, "x.png")
        rc, out = run(st, "show", "--png", png)
        check("--png 出图（>5KB）", rc == 0 and os.path.getsize(png) > 5000 if os.path.exists(png) else False,
              out or "文件没生成")
        rc, out = run(st, "show", "--png")
        check("--png 省路径 → 写 <state同名>.png", os.path.exists(os.path.join(d, "ld.png")), out)
        # ── 一手一条命令：同一次运行给「可贴块 + 自用行」──
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], B(3, 4, by="player"))
        rc, out = run(st, "show")
        check("一手一条命令：同一次运行给出发群块 + 自用行",
              "── 发群（以下整块照贴）" in out and "── 发群到此" in out
              and "别贴进群" in out and out.index("── 发群到此") < out.index("别贴进群"), out)
        seg = out[out.index("── 发群（以下整块照贴）"):out.index("── 发群到此")]
        check("可贴块里没有本天使的骰盅明文", "本天使的骰盅" not in seg, seg)

        # ── 收桌：一条命令办完（存档 + INDEX + 台账）──
        seed(st, [1], [2], B(1, 6, by="player"), lcount=0, pcount=1, over="player")
        t = json.load(open(st, encoding="utf-8"))
        t["dice"] = 2
        t["history"] = [{"round": 1, "kind": "open", "bid": B(1, 6, by="player"),
                         "chain": [B(1, 6, by="player")], "liya_hand": [1], "player_hand": [2],
                         "liya_count": 0, "player_count": 0, "actual": 0,
                         "bidder": "player", "bidder_wins": False, "challenger": "liya",
                         "loser": "player", "penalty": 1, "pen_quan": False,
                         "liya_left": 0, "player_left": 1}]
        json.dump(t, open(st, "w", encoding="utf-8"), ensure_ascii=False)
        arch = os.path.join(d, "arch-ld")
        led = os.path.join(d, "led-ld.json")
        rc, out = run(st, "close", "--archive", arch, "--ledger", led)
        check("close 跑通", rc == 0, out)
        mds = [f for f in os.listdir(arch) if f.endswith(".md") and f != "INDEX.md"]
        check("close 落 json + md + INDEX",
              len(mds) == 1 and os.path.exists(os.path.join(arch, mds[0][:-3] + ".json"))
              and os.path.exists(os.path.join(arch, "INDEX.md")), str(sorted(os.listdir(arch))))
        body = open(os.path.join(arch, mds[0]), encoding="utf-8").read()
        check("存档 md 含逐局经过 + 摊牌 + 承诺复算",
              "### 第 1 局" in body and "实际合计 0 颗 vs 叫 1 颗" in body
              and "独立复算 → 一致 ✓" in body, body[:240])
        idx = open(os.path.join(arch, "INDEX.md"), encoding="utf-8").read()
        check("INDEX 自动重建、含这一档", mds[0] in idx and "阁下胜" in idx, idx[:240])
        lj = json.load(open(led, encoding="utf-8"))
        check("台账记了一笔（大话骰 · 负）",
              len(lj["log"]) == 1 and lj["log"][-1]["game"] == "大话骰"
              and lj["log"][-1]["result"] == "loss", str(lj))
        rc, out = run(st, "close", "--archive", arch, "--ledger", led)
        check("close 幂等（重收被挡、不多开一档）",
              rc != 0 and "已经收过" in out
              and len([f for f in os.listdir(arch) if f.endswith(".md")
                       and f != "INDEX.md"]) == 1, out)
        seed(st, [1, 2, 2, 3, 4], [3, 3, 5, 5, 6], B(3, 4, by="player"))
        rc, out = run(st, "close", "--archive", arch, "--ledger", led)
        check("没收桌时 close 被挡", rc != 0 and "还没收桌" in out, out)

        # ── 审计日志（阁下可查本天使看到了什么）──
        # 单独开一盘：日志是累积的，混着别局的明文会误伤哨兵
        st2 = os.path.join(d, "ld2.json")
        seed(st2, [6, 6, 5], [3, 3, 4], B(3, 4, by="player"))
        run(st2, "show")
        alog = os.path.join(d, "ld2.audit.log")
        check("审计日志自动落盘且非空", os.path.exists(alog) and os.path.getsize(alog) > 0)
        logtxt = open(alog, encoding="utf-8").read()
        check("对局中：审计日志里没有阁下骰面明文", "⚂3 ⚂3 ⚃4" not in logtxt, logtxt[-300:])
        seed(st2, [6, 6, 5], [3, 3, 4], B(3, 4, by="player"), over="liya")
        run(st2, "reveal")
        check("正对照：摊牌后阁下骰面进日志（哨兵有效）",
              "⚂3 ⚂3 ⚃4" in open(alog, encoding="utf-8").read())
        seed(st2, [6, 6, 5], [3, 3, 4], B(3, 4, by="player"))
        rc, out = run(st2, "reveal")
        check("局中 reveal 不摊阁下暗牌", rc == 0 and "⚂3 ⚂3 ⚃4" not in out, out)
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

#!/usr/bin/env python3
"""掷骰决斗 CLI 上桌前的自测（回归）。全绿才上桌；有 FAIL 就非零退出。

用法:
  python3 scripts/dice-duel_selftest.py                  # 自测同目录的 dice-duel.py
  python3 scripts/dice-duel_selftest.py /path/to/xxx.py  # 自测别的盘面 CLI
  python3 scripts/dice-duel_selftest.py --keep           # 保留临时目录看现场

覆盖（按真实对局顺序，临时盘、不碰正式 temp/）:
  new 落盘 + 承诺文件（hash == sha256(盐|种子)）→ roll 连掷到终局（平局不涨分）
  → 终局后 roll 被挡 → check「独立复算 → 一致 ✓」→ reveal 摊盐种子、selftest 自己重算每一掷
  → close（存档 + INDEX 重建 + 台账一笔）→ 重收被挡 → 同日两盘 slug 加尾号
  → 没终局 close 被挡 → 局中 reveal 作废（roll / close 都被挡）→ new 重开清掉旧承诺
  → 空数据根首次 new 不崩

为什么要有这个：
  引擎的必崩点不在主流程上——「空数据根第一盘」（save 少了 makedirs）、
  「终局态渲染」、「重收落戳」「同日撞 slug」这些手工点两下根本碰不到，上桌才炸。
  断言咬不变量（分数守恒 / 骰面范围 / 状态可解析 / 复算一致），不咬随机出来的比分。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

SELF = os.path.dirname(os.path.abspath(__file__))
SCORE_TO_WIN = 3
SIDES = ("player", "liya")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def derive(seed, n, side, i):
    """独立实现一遍骰面推导（跟引擎那份各写各的，才叫独立复算）。"""
    return hashlib.sha256(f"{seed}|{n}|{side}|{i}".encode()).digest()[0] % 6 + 1


class Harness:
    def __init__(self, cli):
        self.work = tempfile.mkdtemp(prefix="dice-duel-selftest-")
        self.cli = os.path.join(self.work, "cli.py")
        shutil.copy(cli, self.cli)
        # 引擎会 shell out 给**同级**的兄弟脚本（ledger.py 等）——一起拷过去，
        # 否则副本在临时目录里找不到它们，收尾的台账那笔就记不上。
        src = os.path.dirname(os.path.abspath(cli))
        for f in os.listdir(src):
            if f.endswith(".py") and not f.endswith("_selftest.py"):
                shutil.copy(os.path.join(src, f), os.path.join(self.work, f))
        self.results = []

    def run(self, *args, state=None, env=None):
        cmd = [sys.executable, self.cli, *args]
        if state:
            cmd += ["--state", state]
        e = dict(os.environ)
        if env:
            e.update(env)
        p = subprocess.run(cmd, capture_output=True, text=True, env=e)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def check(self, title, ok, detail=""):
        self.results.append((title, bool(ok)))
        print(("PASS " if ok else "FAIL ") + title
              + (f"  [{detail}]" if detail else ""))


def commit_of(state):
    return (state[:-5] if state.endswith(".json") else state) + ".commit.json"


def play_to_end(h, state, cap=200):
    """连掷到终局。每手重读落盘状态（别拿开局那份 stale 快照），不变量逐手对账。"""
    bad = {"face": [], "score": [], "tie": [], "rolls": []}
    ties = 0
    last = ""
    d = load(state)
    for _ in range(cap):
        d = load(state)
        if d.get("over"):
            break
        bp, bl = d["player_score"], d["liya_score"]
        code, out = h.run("roll", state=state)
        last = out
        if code != 0 or "Traceback" in out:
            h.check("掷到终局不报错", False, out.strip()[-200:])
            return None, out, bad
        d = load(state)
        hh = d["history"][-1]
        faces = hh["player"] + hh["liya"]
        if not all(1 <= x <= 6 for x in faces):
            bad["face"].append(faces)
        if hh["result"] == "tie":
            ties += 1
            if (d["player_score"], d["liya_score"]) != (bp, bl):
                bad["tie"].append((bp, bl, d["player_score"], d["liya_score"]))
        wp = sum(1 for x in d["history"] if x["result"] == "player")
        wl = sum(1 for x in d["history"] if x["result"] == "liya")
        if (wp, wl) != (d["player_score"], d["liya_score"]):
            bad["score"].append((wp, wl, d["player_score"], d["liya_score"]))
        if d["rolls"] != len(d["history"]):
            bad["rolls"].append((d["rolls"], len(d["history"])))
    d = load(state)
    h.check("连掷到终局（掷得出结果）", bool(d.get("over")),
            f"{d.get('rolls')} 掷 · 平局 {ties} · {d.get('player_score')}:{d.get('liya_score')}")
    h.check("每一掷骰面 ∈ 1..6", not bad["face"], str(bad["face"][:3]))
    h.check("分数守恒 = 骰谱计数（0..3）", not bad["score"], str(bad["score"][:3]))
    h.check("平局不涨分", not bad["tie"], f"平局 {ties} 次 ｜ {bad['tie'][:3]}")
    h.check("手数与骰谱长度一致", not bad["rolls"], str(bad["rolls"][:3]))
    if d.get("over"):
        h.check("终局分恰为 3 分、对手低于 3",
                d[d["winner"] + "_score"] == SCORE_TO_WIN
                and d[("liya" if d["winner"] == "player" else "player") + "_score"] < SCORE_TO_WIN,
                f"{d['winner']} {d['player_score']}:{d['liya_score']}")
    return d, last, bad


def main():
    argv = [a for a in sys.argv[1:] if a != "--keep"]
    keep = "--keep" in sys.argv
    cli = argv[0] if argv else os.path.join(SELF, "dice-duel.py")
    if not os.path.exists(cli):
        sys.exit(f"找不到 CLI: {cli}")

    h = Harness(cli)
    st = os.path.join(h.work, "duel.json")
    arch = os.path.join(h.work, "arch")
    led = os.path.join(h.work, "led.json")

    # ---- 第一盘：承诺 → 连掷到终局 → 自证 → 收桌 ----
    rc, out = h.run("new", state=st)
    h.check("new 跑通（新建路径）", rc == 0 and "Traceback" not in out,
            out.strip().splitlines()[-1] if out.strip() else "")
    if not os.path.exists(st):
        h.check("新盘已落盘", False, st)
        sys.exit(1)
    try:
        d = load(st)
        h.check("新盘 JSON 可解析", True)
    except Exception as e:  # noqa: BLE001
        h.check("新盘 JSON 可解析", False, repr(e))
        sys.exit(1)
    h.check("开局态：比分 0:0、0 掷、未终局",
            d["player_score"] == 0 and d["liya_score"] == 0
            and d["rolls"] == 0 and not d.get("over"),
            json.dumps({k: d[k] for k in ("player_score", "liya_score", "rolls", "over")}))

    cpath = commit_of(st)
    h.check("承诺文件落盘名与文档一致（<state>.commit.json）",
            os.path.exists(cpath) and not os.path.exists(st + ".commit.json"), cpath)

    cm = load(cpath) if os.path.exists(cpath) else {}
    h.check("承诺含 algo/salt/seed/hash",
            all(k in cm for k in ("algo", "salt", "seed", "hash")), str(sorted(cm)))
    local_hash = hashlib.sha256((cm.get("salt", "") + "|" + cm.get("seed", "")).encode()).hexdigest()
    h.check("哈希独立复算一致（sha256(盐|种子)）", local_hash == cm.get("hash"), local_hash[:16])
    pub = next((l for l in out.splitlines() if "承诺已封存" in l), "")
    h.check("开局打印「承诺已封存，可公布: <hash>」且 hash 相同",
            bool(pub) and cm.get("hash", "") in pub, pub.strip())

    rc, out = h.run("new", "easy", state=os.path.join(h.work, "junk.json"))
    h.check("难度参数已删（new easy 被挡）", rc != 0 and "难度" in out, out.strip()[:60])

    rc, out = h.run("check", state=st)
    h.check("0 掷的 check 也自洽（独立复算一致）",
            rc == 0 and "独立复算 → 一致 ✓" in out and "对不上" not in out,
            out.strip().replace("\n", " / "))

    d, last, _ = play_to_end(h, st)
    if not d:
        sys.exit(1)

    rc, out = h.run("roll", state=st)
    h.check("终局后 roll 被挡", rc != 0 and "打完" in out, out.strip()[:60])
    rc, out = h.run("show", state=st)
    h.check("终局后 show 不崩且带终局行", rc == 0 and "Traceback" not in out and "终局" in out,
            out.strip().splitlines()[-1])
    h.check("终局那一手自动摊牌（带独立复算）", "独立复算" in (last or ""), "第 %s 掷" % d["rolls"])

    rc, out = h.run("check", state=st)
    h.check("check 全自洽：分数对账 + 独立复算 → 一致 ✓",
            rc == 0 and "独立复算 → 一致 ✓" in out and "对不上" not in out
            and "分数与骰谱对账" in out, out.strip().replace("\n", " / "))

    rc, out = h.run("reveal", state=st)
    h.check("reveal 摊出盐与种子、哈希复算一致",
            rc == 0 and cm["salt"] in out and cm["seed"] in out and "一致 ✓" in out
            and "对不上" not in out, out.strip().replace("\n", " | ")[:120])
    same = all(derive(cm["seed"], hh["n"], side, i) == hh[side][i]
               for hh in d["history"] for side in SIDES for i in (0, 1))
    h.check("全部 %d 掷能按种子重算出来（selftest 独立实现）" % d["rolls"], same)
    d = load(st)
    h.check("终局摊牌不作废（void 仍为 0）", not d.get("void"))

    rc, out = h.run("close", "--archive", arch, "--ledger", led, state=st)
    h.check("close 跑通", rc == 0, out.strip()[-160:])
    mds = [f for f in os.listdir(arch) if f.endswith(".md") and f != "INDEX.md"]
    jsons = [f for f in os.listdir(arch) if f.endswith(".json")]
    h.check("close 落 md + json + 承诺 + INDEX",
            len(mds) == 1 and len(jsons) == 2 and os.path.exists(os.path.join(arch, "INDEX.md")),
            str(sorted(os.listdir(arch))))
    body = open(os.path.join(arch, mds[0]), encoding="utf-8").read()
    h.check("存档 md 含 meta + 口径 + 逐掷经过 + 公平自证",
            "<!-- meta " in body and "## 口径" in body
            and "## 逐掷经过" in body and "独立复算 → 一致 ✓" in body, body[:160])
    res_cn = {"player": "阁下胜", "liya": "本天使胜"}[d["winner"]]
    h.check("存档 md 记了比分与结果",
            f"{d['player_score']} : {d['liya_score']}" in body
            and f"结果：{res_cn}" in body, res_cn)
    idx = open(os.path.join(arch, "INDEX.md"), encoding="utf-8").read()
    h.check("INDEX 自动重建、含这一档", mds[0] in idx and "先3胜" in idx, idx.splitlines()[0])
    lj = json.load(open(led, encoding="utf-8"))
    want = "win" if d["winner"] == "liya" else "loss"
    h.check("台账记了一笔（掷骰决斗 · 阁下赢 = 本天使负）",
            len(lj["log"]) == 1 and lj["log"][-1]["game"] == "掷骰决斗"
            and lj["log"][-1]["result"] == want and lj["log"][-1]["moves"] == d["rolls"],
            json.dumps(lj["log"][-1], ensure_ascii=False)[:160])

    rc, out = h.run("close", "--archive", arch, "--ledger", led, state=st)
    h.check("close 重收被挡、不多开一档",
            rc != 0 and "已经收过" in out
            and len([f for f in os.listdir(arch) if f.endswith(".md")
                     and f != "INDEX.md"]) == 1, out.strip()[:60])

    # ---- 同日再开两盘：slug 尾号 ----
    for i in (1, 2):
        stx = os.path.join(h.work, f"duelx{i}.json")
        rc, out = h.run("new", state=stx)
        play_to_end(h, stx)
        rc, out = h.run("close", "--archive", arch, "--ledger", led, state=stx)
        h.check(f"同日第 {i + 1} 盘 close 跑通", rc == 0, out.strip()[-120:])
    mds2 = sorted(f for f in os.listdir(arch) if f.endswith(".md") and f != "INDEX.md")
    h.check("同日连开两盘 slug 加尾号、不互相盖",
            len(mds2) == 3 and any(f.endswith("-2.md") for f in mds2), str(mds2))
    lj = json.load(open(led, encoding="utf-8"))
    h.check("台账共三笔、游戏名一致",
            len(lj["log"]) == 3 and {r["game"] for r in lj["log"]} == {"掷骰决斗"},
            str([r["result"] for r in lj["log"]]))

    # ---- 没终局 / 局中摊牌 ----
    st3 = os.path.join(h.work, "duel3.json")
    h.run("new", state=st3)
    rc, out = h.run("close", "--archive", arch, "--ledger", led, state=st3)
    h.check("没收桌时 close 被挡", rc != 0 and "还没收桌" in out, out.strip()[:60])

    h.run("roll", state=st3)
    rc, out = h.run("reveal", state=st3)
    h.check("局中 reveal 摊牌并说明这局作废", rc == 0 and "作废" in out and "盐" in out,
            out.strip().replace("\n", " | ")[-120:])
    h.check("局中摊牌把状态标成作废", bool(load(st3).get("void")))
    rc, out = h.run("roll", state=st3)
    h.check("作废后 roll 被挡", rc != 0 and "作废" in out, out.strip()[:60])
    rc, out = h.run("close", "--archive", arch, "--ledger", led, state=st3)
    h.check("作废盘 close 被挡", rc != 0 and "作废" in out, out.strip()[:60])
    h.check("作废盘没被收进档",
            len([f for f in os.listdir(arch) if f.endswith(".md")
                 and f != "INDEX.md"]) == 3)

    rc, out = h.run("new", state=st3)
    d3 = load(st3)
    h.check("作废后 new 重开：清掉旧承诺、状态归零",
            rc == 0 and "旧的公平承诺文件已清掉" in out
            and d3["rolls"] == 0 and not d3.get("void") and not d3.get("over"),
            out.strip().splitlines()[-1])

    # ---- 平局分支：真掷出一次平局（和相等 ≈11.3%/掷），断言不吃分 ----
    st4 = os.path.join(h.work, "duel4.json")
    h.run("new", state=st4)
    tie_ok, tie_info, tie_out = False, "没掷到平局", ""
    for _ in range(80):
        d4 = load(st4)
        if d4.get("over"):          # 这盘打完还没平局 → 换一盘接着找
            h.run("new", state=st4)
            continue
        bp, bl = d4["player_score"], d4["liya_score"]
        _, tout = h.run("roll", state=st4)
        d4 = load(st4)
        hh = d4["history"][-1]
        if hh["result"] == "tie":
            tie_ok = (d4["player_score"], d4["liya_score"]) == (bp, bl)
            tie_info = f"第 {hh['n']} 掷 {hh['player']}={sum(hh['player'])} vs {hh['liya']}={sum(hh['liya'])}"
            tie_out = tout
            break
    h.check("平局不吃分、接着掷（平局分支实测）", tie_ok, tie_info)
    h.check("平局那一手渲染成「平局」", "平局" in tie_out,
            next((l for l in tie_out.splitlines() if "平局" in l), "")[:60])

    # ---- 负对照：手改骰面必须被抓住（证明上面那些「一致 ✓」不是空转）----
    st5 = os.path.join(h.work, "duel5.json")
    shutil.copyfile(st4, st5)
    shutil.copyfile(commit_of(st4), commit_of(st5))
    d5 = load(st5)
    d5["history"][0]["player"] = [6, 6]
    with open(st5, "w", encoding="utf-8") as f:
        json.dump(d5, f, ensure_ascii=False)
    rc, out = h.run("check", state=st5)
    h.check("负对照：手改骰面被 check 抓住（对不上 ✗）",
            rc == 0 and "对不上 ✗" in out and "独立复算 → 对不上" in out,
            out.strip().replace("\n", " / ")[:160])
    rc, out = h.run("close", "--archive", arch, "--ledger", led, state=st5)
    h.check("没过终局的盘 close 仍被挡（负对照盘）", rc != 0 and "还没收桌" in out,
            out.strip()[:60])

    # ---- 空数据根首次 new（save 少了 makedirs 就崩在这儿）----
    empty = os.path.join(h.work, "emptyroot")
    os.makedirs(empty, exist_ok=True)
    rc, out = h.run("new", env={"GAME_HOME": empty})
    got = os.path.join(empty, "temp", "dice-duel.json")
    h.check("空数据根首次 new 不崩、落 <根>/temp/dice-duel.json",
            rc == 0 and "Traceback" not in out and os.path.exists(got)
            and os.path.exists(os.path.join(empty, "temp", "dice-duel.commit.json")),
            out.strip()[-120:])

    failed = [t for t, ok in h.results if not ok]
    print("\n" + ("全绿 ✓" if not failed else f"FAIL {len(failed)}/{len(h.results)}: "
                 + ", ".join(failed)))
    print(f"PASS {len(h.results) - len(failed)}/{len(h.results)}")
    print("临时盘:", h.work)
    if not keep:
        shutil.rmtree(h.work, ignore_errors=True)
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()

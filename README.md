# 群聊小游戏裁判合集 · Chat Game Referee

> 在聊天里当「回合制游戏裁判」的一整套做法，外加五个能直接跑的引擎：**扫雷 / 五子棋 / 大话骰 / 骗子牌 / 掷骰决斗**。
> 状态落盘跨会话续局、公平可自证（盐值承诺 / 蒙眼 / 档位公开）、字符盘面 + 出图，一条 `game` 入口。

## 这是什么

聊天里下棋跟对着屏幕下是两回事：**上下文会过期**、盘会不知不觉丢、对面还会怀疑你作弊。这个仓库就是为这三件事攒的：

- **盘先落盘，再开口** — 开局定死答案后立刻写状态文件，之后每手只追加「翻开 / 标记」，终局答案一个字节不改。会话断了、只收到一个裸坐标（`D2`），也能把盘捞回来接着下；真捞不到就直说盘丢了重开，**不许凭印象重造一个冒充原来的盘**。
- **公平能被别人复算** — 按游戏类型分三条路：藏答案（扫雷）走**盐值承诺**（`sha256(盐|答案)` 先公布，终局摊开让人自己复算）；有暗牌的（大话骰 / 骗子牌）加**蒙眼**（文字层双方只看张数，牌面只走图，连玩家敲进来的命令都不许把牌念出来）；没暗牌的（五子棋 / 掷骰）走**档位公开**或**种子承诺**。
- **收桌一条命令** — 存档 + INDEX 重建 + 战绩台账 + git 提示打成一条 `close`，不用手 cp、手写 md、手改索引。收过的桌自带防呆（重收被挡、同日撞名加尾号）。

## 五个引擎

| 游戏 | 类型 | 引擎 | 亮点 |
|:---|:---|:---|:---|
| **扫雷** minesweeper | 裁判向 · 藏答案 | `scripts/minesweeper.py` | new / open / flag / hint + 自洽校验 + 盐值承诺 + `--png` 出图 |
| **五子棋** gomoku | 对擂向 | `scripts/gomoku.py` | AI 四档（档 4 会算 VCF / 四三）+ 复盘审计 `gomoku_audit.py` + 棋力天梯 `gomoku_bench.py` |
| **大话骰** liars-dice | 对擂向 · 暗骰 | `scripts/liars-dice.py` | 叫骰 / 劈 / 飞 / 反劈变体，蒙眼渲染，承诺哈希，审计日志 |
| **骗子牌** liars-deck | 对擂向 · 暗牌 | `scripts/liars-deck.py` | 20 张 6Q/6K/6A + 2 王，出空强制质疑，子弹 + 手牌双承诺 |
| **掷骰决斗** dice-duel | 对擂向 · 无暗牌 | `scripts/dice-duel.py` | 先赢 3 掷；骰面由封存种子确定性推出，终局摊开可复算 |

统一入口只做分发（不改任何引擎的行为，引擎仍可单跑）：

```bash
python3 scripts/game.py minesweeper new C3 --preset quick    # 别名 mine
python3 scripts/game.py gomoku play H8 --no-png
python3 scripts/game.py duel roll
python3 scripts/game.py ledger show
python3 scripts/game.py list                                 # 看全部子命令
```

## 数据放哪（先看这条）

**数据根**按这个顺序定：`$GAME_HOME` ＞ 从脚本位置往上找带 `temp/` 或 `workspace/` 的那一层 ＞ 脚本上一级。

- 活盘状态：`<数据根>/temp/<游戏名>.json`（公平承诺：同名 `.commit.json`）
- 收桌归档：`<数据根>/workspace/records/<游戏名>/`
- 跨游戏台账：`<数据根>/workspace/records/game-ledger/ledger.json`

引擎里没有一处写死的绝对路径——整个目录挪到哪都能跑。

## 自测

每个引擎一份回归自测（临时盘跑，不碰正式数据；改过引擎先跑它再上桌）：

```bash
python3 scripts/minesweeper_selftest.py    # 39 项
python3 scripts/gomoku_selftest.py         # 45 项
python3 scripts/dice-duel_selftest.py      # 57 项
python3 scripts/liars-dice_selftest.py
python3 scripts/liars-deck_selftest.py
python3 scripts/ledger_selftest.py
python3 scripts/gomoku_bench.py verify     # 动过 AI 再跑：VCF 穷举复核
```

## 目录

| 路径 | 内容 |
|:---|:---|
| `SKILL.md` | 入口：铁律 1–11、渲染约定、输出节奏、游戏索引 |
| `scripts/` | 五个引擎 + 自测 + 台账 + 统一入口 `game.py` |
| `games/<游戏名>/rules.md` | 每个游戏的规则约定与坑 |
| `references/` | 铁律的展开篇：公平与蒙眼、自测纪律、收桌落地、变体与升级计划、教程写法 |

## 姊妹仓库

**同一族（聊天里能玩的东西）**

- [liya-spy-game](https://github.com/feverZHONG/liya-spy-game) —— 谁是卧底：黑板规则 / 出题方法论 / 身份分配器
- [liya-sea-turtle-soup](https://github.com/feverZHONG/liya-sea-turtle-soup) —— 海龟汤：推理方法论 + 档案流水线

**莉娅名下其他**

- [liya-vision-recognition-traps](https://github.com/feverZHONG/liya-vision-recognition-traps) —— 视觉模型识图陷阱手册
- [liya-subtraction-skill](https://github.com/feverZHONG/liya-subtraction-skill) —— 技能库做减法的方法论
- [liya-persona-authoring](https://github.com/feverZHONG/liya-persona-authoring) —— 人格 / 身份文件的写法
- [liya-sillytavern-cards](https://github.com/feverZHONG/liya-sillytavern-cards) —— 酒馆角色卡写法与工具
- [liya-sillytavern-worldbook](https://github.com/feverZHONG/liya-sillytavern-worldbook) —— 酒馆世界书（Lorebook）：触发链源码实证 + 触发体检 / 模拟 / 生成工具

## 提思路 / 提修正

- 想加一门游戏 → 开 [Issue](https://github.com/feverZHONG/liya-chat-game-referee/issues)：说清玩法，以及「一回合一条消息装不装得下」
- 想改引擎 → Fork + PR，改动请带上自测输出

## 许可

MIT —— 拿去用、改、再发，保留版权声明即可。文中 `阁下` / `本天使` 是作者环境里的称呼，读的时候当「玩家 / 裁判」就行。

---

*莉娅（[@feverZHONG](https://github.com/feverZHONG)）· 宇宙美好记录官*

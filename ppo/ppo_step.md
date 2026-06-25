# 牛头王 AI 训练完整闭环流程

本指南介绍从数据收集 → 行为克隆 → 强化学习微调 → 再次对战的完整迭代流程。
支持两种数据来源：**微信小程序对局**（推荐，真实玩家数据）和**本地终端人机对战**。

> **当前最强策略**：Self-Play（自我对弈）+ ε-greedy 探索，100k 局胜率可达 **27~30%**（纯规则 AI 对手上限约 22%）。

---

## 轨迹格式（统一）

无论来源，所有轨迹文件使用相同格式，每个 play 包含：

```json
{
  "player_id": "human",
  "card_played": 23,
  "hand_before": [13, 23, 31, 45, 67, 72, 80, 85, 91, 99],
  "penalty": 0,
  "row_affected": 2,
  "action_idx": 1
}
```

- `hand_before`：出牌前的手牌（用于重建 state 向量）
- `action_idx`：出牌在 `hand_before` 中的下标（行为克隆训练目标）
- 训练时 `state` 向量由代码从 `hand_before` + `initial_rows` 动态重建，**不需要预存**

---

## 来源 A：微信小程序收集（推荐）

玩家在小程序中对局，游戏结束时自动上报轨迹到服务器。

### 1. 玩游戏

打开小程序 → 单机版或多人版 → 正常游戏，结束后屏幕右上角显示 **"✅ 轨迹已保存"**。

### 2. 查看统计 & 下载轨迹

访问管理台：`https://kwjcyr.com/nimmt_traj_admin`

- 在逐日明细表勾选要下载的日期（有数据才可选）
- 选择来源（全部 / 单机 / 多人）
- 点击 **⬇ 下载 JSON**，得到 `nimmt_traj_all_YYYYMMDD.json`

下载文件外层结构：

```json
{
  "meta": { "dates": [...], "source": "all", "count": 42 },
  "trajectories": [ { ...game1 }, { ...game2 }, ... ]
}
```

### 3. 放入训练目录

```bash
cp ~/Downloads/nimmt_traj_all_20260625.json ppo/trajectories/human/
```

---

## 来源 B：本地终端人机对战

```bash
python ppo/nimmt_ppo_traj.py human
```

- 默认加载 `../models/nimmt_ppo_model.pth`（若存在），否则使用随机策略
- 轨迹自动保存到 `ppo/trajectories/human/human_traj_时间戳.json`
- 格式与小程序完全一致，可混合使用

---

## 步骤 1：行为克隆（BC）

从轨迹文件中提取人类出牌的 state-action 对，训练策略网络模仿你的决策。

```bash
# 使用小程序下载包（外层有 meta + trajectories）
python ppo/nimmt_ppo_traj.py behavior_clone ppo/trajectories/human/nimmt_traj_all_20260625.json 50

# 使用本地人机对战轨迹（多文件，glob 展开）
python ppo/nimmt_ppo_traj.py behavior_clone ppo/trajectories/human/*.json 50

# 两种混合（直接列出所有文件）
python ppo/nimmt_ppo_traj.py behavior_clone ppo/trajectories/human/*.json 50
```

**说明：**

- `50` 是训练轮数（epochs），可根据数据量调整（建议 30~100）
- 数据量少时脚本自动降低 batch_size、增加 Dropout、提前停止，防止过拟合
- 训练完成后生成：
  - `../models/bc_backbone.pth`
  - `../models/bc_actor.pth`

> 建议至少积累 **50~100 局**人类对局再做行为克隆，效果更佳。

---

## 步骤 2：强化学习微调（PPO）

行为克隆得到的模型能模仿你的风格，但局面覆盖有限。
用 PPO 在线对弈微调，让 AI 持续优化同时保留你的经验。

```bash
# ⭐ 推荐：Self-Play + blend-bc，在已有底子上注入人类风格
python ppo/nimmt_ppo_traj.py train --self-play --blend-bc --episodes=30000

# 从头建立强底子（首次或想重置时）
python ppo/nimmt_ppo_traj.py train --self-play --from-scratch --episodes=100000

# 从 BC 模型起步（无旧 PPO 模型时）
python ppo/nimmt_ppo_traj.py train --self-play --load-bc

# 继续已有 PPO 模型（不注入 BC）
python ppo/nimmt_ppo_traj.py train --self-play
```

**训练参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|------|
| `--self-play` | 开启 Self-Play，部分对手使用历史 PPO 快照 | 关闭 |
| `--blend-bc` | 先用人类数据 BC 预热 Actor，再 PPO 微调（保留 Critic）| 关闭 |
| `--load-bc` | 用 BC 权重替换 backbone+actor（无旧 PPO 时用）| 关闭 |
| `--from-scratch` | 完全随机初始化 | 关闭 |
| `--episodes=N` | 训练局数 | 30000 |
| `--epsilon=0.01` | ε-greedy 探索率（训练时随机出牌概率）| 0.01 |

**Self-Play 机制：**

- 每隔 2000 局把当前模型存入「历史池」（最多保留 5 个快照）
- 每局对战时，5 个对手中约 60% 从历史池随机抽取，其余为规则 AI
- 迫使模型持续对抗「过去的自己」，不断弥补弱点，胜率上限从 22% 提升至 **28%+**

**ε-greedy 探索：**

- 训练时以 1% 概率完全随机选牌，防止策略过早收敛到固定套路
- 可通过 `--epsilon=0.02` 加大探索（数据少时适当增大）

> 默认 30000 局，可随时 `Ctrl+C` 提前停止，模型自动保存到 `../models/nimmt_ppo_model.pth`

---

## 步骤 3：验证 / 继续收集

```bash
# 终端人机对战，验证新模型效果，同时产生新轨迹
python ppo/nimmt_ppo_traj.py human

# 全自动对战展示（纯评估，不产生人类轨迹）
python ppo/nimmt_ppo_traj.py play
```

---

## 完整闭环迭代

```
小程序对局 / 终端 human
       ↓ 轨迹 JSON
behavior_clone  →  bc_backbone.pth + bc_actor.pth
       ↓
train --self-play --blend-bc  →  nimmt_ppo_model.pth
  （Self-Play 历史池持续对抗自己 + ε-greedy 探索 + BC 人类风格注入）
       ↓
再次小程序对局（更强 AI 作为对手，新数据加入下轮迭代）
       ↓ （循环，模型持续进化）
```

每轮新数据与旧数据合并（`*.json` 匹配所有文件），模型持续进化。

**各轮迭代预期胜率：**

| 阶段 | 方式 | 胜率参考 |
|------|------|--------|
| 初始 | 随机策略 | ~16.7% |
| 纯规则AI对手 PPO 30k 局 | train | ~22% |
| Self-Play PPO 100k 局 | train --self-play | **~28%** |
| Self-Play + BC 人类数据微调 | train --self-play --blend-bc | **~28%+** |

---

## 命令速查表

| 任务 | 命令 |
|------|------|
| 小程序轨迹下载 | 访问 `https://kwjcyr.com/nimmt_traj_admin` |
| 本地人机对战收集 | `python ppo/nimmt_ppo_traj.py human` |
| 行为克隆 | `python ppo/nimmt_ppo_traj.py behavior_clone ppo/trajectories/human/*.json 50` |
| ⭐ **每轮迭代推荐命令** | `python ppo/nimmt_ppo_traj.py train --self-play --blend-bc --episodes=30000` |
| 从头建立强底子 | `python ppo/nimmt_ppo_traj.py train --self-play --from-scratch --episodes=100000` |
| PPO 训练（从 BC 起步） | `python ppo/nimmt_ppo_traj.py train --self-play --load-bc` |
| PPO 训练（继续已有模型） | `python ppo/nimmt_ppo_traj.py train --self-play` |
| 全自动对战展示 | `python ppo/nimmt_ppo_traj.py play` |

---

## 常见问题

**Q：小程序玩完没看到"✅ 轨迹已保存"提示？**
A：检查网络，或在微信公众平台确认 `https://kwjcyr.com` 已加入 request 合法域名。

**Q：`behavior_clone` 提示"未找到有效样本"？**
A：确认轨迹文件中人类玩家 play 含 `hand_before` 和 `action_idx` 字段，且 `action_idx >= 0`。

**Q：数据量少时 BC loss 降不下去？**
A：正常现象，脚本已自动加 Dropout 防过拟合。多积累几局数据后重新训练即可。

**Q：PPO 训练后胜率没有明显提升？**
A：先用 `play` 模式观察 AI 对战效果。若确实未提升，建议改用 `--self-play` 模式，比纯规则 AI 对手上限高约 7%。也可尝试 `--from-scratch --self-play` 重新训练，或积累更多 BC 数据。

**Q：Self-Play 早期胜率反而下降？**
A：正常现象。历史池刚建立时对手极弱，模型处于探索期，约 6000~8000 局后历史池充满，胜率开始稳定上升。

**Q：`--blend-bc` 和 `--load-bc` 有什么区别？**
A：`--load-bc` 直接用 BC 权重覆盖 backbone+actor，会丢失已有 PPO 的 Critic；`--blend-bc` 保留完整 PPO 结构，只对 Actor 做轻量 BC 预热（20 epoch），不破坏价值估计，适合在强底子上迭代。


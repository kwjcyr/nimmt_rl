# 牛头王 AI 训练完整闭环流程

本指南介绍从数据收集 → 行为克隆 → 强化学习微调 → 再次对战的完整迭代流程。
支持两种数据来源：**微信小程序对局**（推荐，真实玩家数据）和**本地终端人机对战**。

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
用 PPO 在线自我对弈微调，让 AI 持续优化同时保留你的经验。

```bash
# 从 BC 模型起步（推荐首次使用）
python ppo/nimmt_ppo_traj.py train --load-bc

# 从已有 PPO 模型继续训练
python ppo/nimmt_ppo_traj.py train

# 完全从头开始
python ppo/nimmt_ppo_traj.py train --from-scratch
```

**训练过程：**

- 与 5 个规则 AI 在线对弈，每 10 局更新一次策略
- 默认 30000 局，可随时 `Ctrl+C` 提前停止，模型自动保存
- 观察胜率稳定在 20%+ 时即可停止
- 模型保存到 `../models/nimmt_ppo_model.pth`

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
train --load-bc  →  nimmt_ppo_model.pth
       ↓
再次小程序对局（更强 AI 作为对手，新数据加入下轮迭代）
```

每轮新数据与旧数据合并（`*.json` 匹配所有文件），模型持续进化。

---

## 命令速查表

| 任务 | 命令 |
|------|------|
| 小程序轨迹下载 | 访问 `https://kwjcyr.com/nimmt_traj_admin` |
| 本地人机对战收集 | `python ppo/nimmt_ppo_traj.py human` |
| 行为克隆（小程序包） | `python ppo/nimmt_ppo_traj.py behavior_clone ppo/trajectories/human/nimmt_traj_all_*.json 50` |
| 行为克隆（所有本地文件） | `python ppo/nimmt_ppo_traj.py behavior_clone ppo/trajectories/human/*.json 50` |
| PPO 训练（从 BC 起步） | `python ppo/nimmt_ppo_traj.py train --load-bc` |
| PPO 训练（继续已有模型） | `python ppo/nimmt_ppo_traj.py train` |
| PPO 训练（从头开始） | `python ppo/nimmt_ppo_traj.py train --from-scratch` |
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
A：先用 `play` 模式观察 AI 对战效果。若确实未提升，可尝试 `train --from-scratch` 重新训练，或增加 BC 数据量后重新做行为克隆。


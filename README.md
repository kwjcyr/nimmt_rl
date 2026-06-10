# 🐂 牛头王 6 Nimmt!

> 经典桌游《6 Nimmt!》的数字实现版本，支持命令行对战、网页对战，以及三种强化学习策略（Q-Learning、DQN、PPO）训练、对比与 Arena 人机对战。

---

## 📁 项目结构

```
nimmt/
├── ql/
│   └── nimmt_ql.py          # Q-Learning 强化学习训练 + 对战
├── dqn/
│   └── nimmt_dqn.py         # DQN（深度 Q 网络）训练 + 对战
├── ppo/
│   └── nimmt_ppo.py         # PPO（近端策略优化）训练 + 对战
├── interactive/
│   ├── nimmt.html           # 网页版（推荐，浏览器直接打开）
│   ├── nimmt.py             # 命令行版（1人类 + 5 规则AI）
│   ├── nimmt_arena.py       # Arena 对战：真人 vs PPO/DQN/QL/规则AI
│   └── nimmt_compare.py     # 三种 RL 一键训练 + 胜率对比
├── models/
│   ├── nimmt_q_table.pkl    # 训练好的 Q 表
│   ├── nimmt_dqn_model.pth  # 训练好的 DQN 模型
│   └── nimmt_ppo_model.pth  # 训练好的 PPO 模型
├── test/
│   └── test_model.py        # 模型加载验证脚本
├── requirements.txt         # Python 依赖
└── README.md
```

---

## 🎮 游戏规则

- 共 **100 张牌**（1–100），6 位玩家各发 **10 张**
- 牌桌摆 **5 列**，每列初始 1 张底牌（按升序排列）
- 每轮所有玩家**同时出 1 张牌**，按牌面从小到大依次放入牌桌

### 放牌规则

| 情况 | 结果 |
|------|------|
| 正常放入 | 放到末尾最大且比自己小的那列 |
| 你的牌是该列第 **7 张** | 💥 收走整列，获得该列全部牛头罚分 |
| 你的牌比**所有列末尾**都小 | 😱 必须自选一列收走 |

### 牛头数（罚分）

| 牌号规律 | 牛头数 |
|---------|-------|
| 普通牌 | 🐂 ×1 |
| 5 的倍数 | 🐂 ×2 |
| 10 的倍数 | 🐂 ×3 |
| 11 的倍数 | 🐂 ×5 |
| 55 | 🐂 ×7（最多）|

### 胜负

- 任意玩家累计牛头超过 **66 分**时游戏结束
- **牛头最少**者获胜 🏆

---

## 🚀 快速开始

### 方式一：网页版（最简单，推荐发给朋友）

直接双击打开 `interactive/nimmt.html`，或右键用浏览器打开。**无需安装任何东西！**

---

### 方式二：安装依赖（DQN / PPO / Arena 需要）

```bash
pip install -r requirements.txt
```

---

### 方式三：命令行版（1人类 + 5 规则AI）

```bash
python3 interactive/nimmt.py
```

---

### 方式四：Arena 对战（真人 vs 三种 RL + 2 规则AI）

```bash
python3 interactive/nimmt_arena.py
```

玩家配置：

| 座位 | 玩家 | 策略 |
|------|------|------|
| 0 | 😀 你 (Human) | 手动出牌 |
| 1 | 🤖 PPO | 近端策略优化神经网络 |
| 2 | 🔵 DQN | 深度 Q 网络 |
| 3 | 🟢 QL | Q-Learning 表格 |
| 4 | 🔴 AI甲 | greedy 规则 |
| 5 | 🟡 AI乙 | safe 规则 |

如果没有训练过模型，可以先一键训练：

```bash
python3 interactive/nimmt_arena.py train   # 先训练三种模型再进入对战
```

---

### 方式五：RL 单独训练 / 对战

```bash
# Q-Learning
python3 ql/nimmt_ql.py train   # 训练，保存 models/nimmt_q_table.pkl
python3 ql/nimmt_ql.py play    # 用模型对战展示
python3 ql/nimmt_ql.py         # 先训练再展示

# DQN
python3 dqn/nimmt_dqn.py train  # 保存 models/nimmt_dqn_model.pth
python3 dqn/nimmt_dqn.py play
python3 dqn/nimmt_dqn.py

# PPO
python3 ppo/nimmt_ppo.py train  # 保存 models/nimmt_ppo_model.pth
python3 ppo/nimmt_ppo.py play
python3 ppo/nimmt_ppo.py
```

### 方式六：三种算法一键对比

```bash
python3 interactive/nimmt_compare.py
```

### 方式七：模型加载验证

```bash
python3 test/test_model.py
```

---

## 🤖 规则 AI 策略说明

| AI | 策略 | 行为 |
|----|------|------|
| AI-甲 | greedy | 70% 选低风险，20% 选中间，10% 赌一把出最大牌 |
| AI-乙 | safe | 90% 选最安全，10% 随机 |
| AI-丙 | greedy | 同甲，激进型 |
| AI-丁 | random | 完全随机，最不可预测 |
| AI-戊 | safe | 保守型 |

---

## 🧠 强化学习算法原理

本项目把"出哪张牌"建模为一个**马尔科夫决策过程（MDP）**：

| MDP 要素 | 牛头王中的对应 |
|---------|-------------|
| 状态 $s$ | 手牌 + 牌桌 5 列 + 当前累计罚分 |
| 动作 $a$ | 从手牌中选第 $i$ 张出（$i \in \{0,\ldots,n{-}1\}$） |
| 奖励 $r$ | $-$本轮获得牛头数；终局排名奖励 $\{+8,+4,+1,-2,-5,-8\}$ |
| 转移 $P$ | 其他玩家的出牌随机性决定下一局面 |

目标：找到策略 $\pi(a|s)$ 使期望累计回报最大：

$$G_t = \sum_{k=0}^{\infty} \gamma^k r_{t+k+1}$$

---

### 1. Q-Learning（`nimmt_ql.py`）

#### 核心思想

用一张**哈希表**存储所有状态-动作对的 Q 值 $Q(s,a)$，代表"在状态 $s$ 下执行动作 $a$ 的期望回报"。

#### Bellman 最优方程

$$Q^*(s,a) = \mathbb{E}\left[r + \gamma \max_{a'} Q^*(s', a')\right]$$

#### 更新规则（TD 目标）

$$Q(s,a) \leftarrow Q(s,a) + \alpha \left[\underbrace{r + \gamma \max_{a'} Q(s',a')}_{\text{TD 目标}} - Q(s,a)\right]$$

| 符号 | 含义 | 本项目取值 |
|------|------|---------|
| $\alpha$ | 学习率，控制每步更新幅度 | 0.1 |
| $\gamma$ | 折扣因子，未来奖励权重 | 0.95 |
| $\varepsilon$ | 探索率，随训练从 1.0 衰减到 0.05 | 0.9995/局衰减 |

#### 探索策略（ε-greedy）

$$a = \begin{cases} \text{随机动作} & \text{以概率 } \varepsilon \\ \arg\max_{a} Q(s,a) & \text{以概率 } 1-\varepsilon \end{cases}$$

#### 状态编码（6 维离散特征）

| 特征 | 取值范围 |
|------|---------|
| 手牌剩余数量 | 0–10 |
| 安全牌数（放入不触发收牌） | 0–10 |
| 危险牌数（放入会触发收牌） | 0–10 |
| 最危险列剩余格数 | 1–6 |
| 全场最小末尾值（离散 10 档） | 0–9 |
| 自身得分档（离散 6 档） | 0–5 |

---

### 2. DQN（Deep Q-Network，`nimmt_dqn.py`）

#### 核心思想

Q-Learning 的状态空间离散化会丢失精度，DQN 用**神经网络** $Q_\theta(s,a)$ 来近似 Q 函数，可直接输入连续状态向量。

#### 状态向量（36 维）

$$s = \bigl[\underbrace{c_1/100,\ b_1/7,\ \ldots,\ c_{10}/100,\ b_{10}/7}_{20\text{维手牌}},\ \underbrace{t_1/100,\ l_1/6,\ h_1/35,\ \ldots}_{15\text{维牌桌}},\ \underbrace{\text{score}/66}_{1\text{维}}\bigr]$$

其中 $c_i$ 为第 $i$ 张手牌，$b_i$ 为其牛头数，$t_j/l_j/h_j$ 分别为第 $j$ 列末尾牌、已有张数、列牛头数。

#### 网络结构

$$s \xrightarrow{\text{Linear}(36\to128)} \text{ReLU} \xrightarrow{\text{Linear}(128\to128)} \text{ReLU} \xrightarrow{\text{Linear}(128\to64)} \text{ReLU} \xrightarrow{\text{Linear}(64\to10)} Q(s,\cdot)$$

#### 损失函数

$$\mathcal{L}(\theta) = \mathbb{E}_{(s,a,r,s')\sim\mathcal{D}}\left[\left(r + \gamma \max_{a'} Q_{\bar\theta}(s',a') - Q_\theta(s,a)\right)^2\right]$$

其中 $\bar\theta$ 为 **Target Network** 的参数，每 200 步从 $\theta$ 复制一次，防止目标值随训练漂移造成不稳定。

#### Experience Replay

每步将 $(s, a, r, s', \text{done})$ 存入容量为 20000 的循环缓冲区 $\mathcal{D}$，每次从中**随机采样** batch=64 条进行梯度更新，打破时序相关性。

| 参数 | 取值 |
|------|------|
| 网络结构 | 36 → 128 → 128 → 64 → 10 |
| 学习率 | 1e-3 (Adam) |
| $\gamma$ | 0.95 |
| Replay Buffer | 20000 |
| Batch Size | 64 |
| Target Network 更新频率 | 每 200 步 |
| 梯度裁剪 | grad_norm ≤ 1.0 |

---

### 3. PPO（Proximal Policy Optimization，`nimmt_ppo.py`）

#### 核心思想

Q-Learning / DQN 属于**值函数**方法（学 Q 值再推导策略）。PPO 是**策略梯度**方法，直接对策略 $\pi_\theta(a|s)$ 求梯度，通过 Actor-Critic 架构同时学策略和价值函数。

#### Actor-Critic 架构

```
输入 s (36维)
    │
    ├─ 共享主干: Linear(36→128) → Tanh → Linear(128→128) → Tanh
    │
    ├─ Actor Head: Linear(128→10) → softmax → 动作概率分布 π(a|s)
    └─ Critic Head: Linear(128→1) → 状态价值 V(s)
```

#### 策略梯度目标

原始策略梯度（REINFORCE）：

$$\nabla_\theta J(\theta) = \mathbb{E}_{\pi_\theta}\left[\nabla_\theta \log \pi_\theta(a|s) \cdot A(s,a)\right]$$

其中优势函数 $A(s,a) = Q(s,a) - V(s)$ 衡量"该动作比平均水平好多少"。

#### GAE（Generalized Advantage Estimation）

直接用 $r - V(s)$ 估计优势方差很大。GAE 用 TD 残差的指数加权平均来降低方差：

$$\delta_t = r_t + \gamma V(s_{t+1}) - V(s_t)$$

$$\hat{A}_t = \sum_{l=0}^{\infty} (\gamma\lambda)^l \delta_{t+l}$$

| $\lambda$ 取值 | 效果 |
|--------------|------|
| $\lambda=0$ | 纯 TD，低方差高偏差 |
| $\lambda=1$ | 纯 MC，低偏差高方差 |
| **本项目 $\lambda=0.95$** | **平衡偏差与方差** |

#### PPO Clipped Objective

朴素策略梯度每次更新后策略可能变化过大导致训练崩溃。PPO 引入重要性采样比率 $r_t(\theta) = \dfrac{\pi_\theta(a_t|s_t)}{\pi_{\theta_\text{old}}(a_t|s_t)}$，并对其裁剪：

$$\mathcal{L}^{\text{CLIP}}(\theta) = \mathbb{E}_t\left[\min\left(r_t(\theta)\hat{A}_t,\ \text{clip}(r_t(\theta),\ 1-\varepsilon,\ 1+\varepsilon)\hat{A}_t\right)\right]$$

其中 $\varepsilon=0.2$ 限制每次更新幅度，防止策略步子迈太大。

#### 完整损失函数

$$\mathcal{L}(\theta) = -\mathcal{L}^{\text{CLIP}} + c_1 \mathcal{L}^{\text{VF}} - c_2 \mathcal{H}[\pi_\theta]$$

| 项 | 含义 | 系数 |
|----|------|------|
| $\mathcal{L}^{\text{CLIP}}$ | 策略提升目标（取负因为做梯度上升） | — |
| $\mathcal{L}^{\text{VF}} = (V_\theta(s) - V^\text{target})^2$ | Critic 均方误差 | $c_1=0.5$ |
| $\mathcal{H}[\pi_\theta] = -\sum_a \pi\log\pi$ | 策略熵，鼓励探索 | $c_2=0.01$ |

#### 训练流程

```
每 10 局收集轨迹数据
    │
    ├─ 计算 GAE 优势 Â 和回报目标 V_target
    │
    └─ 重复 4 轮（PPO epochs）:
         随机打乱数据 → 按 batch=64 更新网络
         （梯度裁剪 grad_norm ≤ 0.5）
```

| 参数 | 取值 |
|------|------|
| 学习率 | 3e-4 (Adam) |
| $\gamma$ | 0.95 |
| GAE $\lambda$ | 0.95 |
| Clip $\varepsilon$ | 0.2 |
| Critic 系数 $c_1$ | 0.5 |
| 熵系数 $c_2$ | 0.01 |
| PPO epochs | 4 |
| 轨迹收集频率 | 每 10 局 |

---

### 算法对比总结

| 维度 | Q-Learning | DQN | PPO |
|------|-----------|-----|-----|
| 策略表示 | 哈希表 Q(s,a) | 神经网络 Q(s,a) | 神经网络 π(a\|s) |
| 状态输入 | 离散特征 | 连续向量 | 连续向量 |
| 样本效率 | 低 | 中（Replay Buffer）| 中（多 epoch）|
| 训练稳定性 | 高（无梯度）| 中（Target Network）| 高（Clipped Loss）|
| 依赖 | 纯 Python | PyTorch | PyTorch |
| 训练时间（30k 局）| ~20s | ~4min | ~72s |
| 胜率（30k 局） | 17.0% | 13.3% | **22.3%** |

> Q-Learning 虽然是最古老的方法，但在小规模问题上速度极快；
> DQN 适合状态空间更大的场景，30k 局仍在热身阶段；
> PPO 凭借策略梯度 + 熵正则化在探索与利用之间取得最佳平衡，表现最强。

---

## 📦 依赖

```bash
pip install -r requirements.txt
```

| 模块 | 依赖 |
|------|------|
| `interactive/nimmt.html` | 无（浏览器直接打开） |
| `interactive/nimmt.py` + `ql/` | Python 3.8+ 标准库 |
| `dqn/` + `ppo/` + `interactive/nimmt_arena.py` | `torch>=2.0` + `numpy>=1.24` |

---

## 📊 对战结果（均训练 30000 局，评估 3000 局）

| 策略 | **胜率** | 前3率 | 平均排名 | 平均罚分 | 训练耗时 |
|------|--------|-------|--------|--------|--------|
| 🥇 **PPO** | **22.3%** | 58.3% | 3.17 | 10.0🐂 | ~72s |
| 🥈 **Q-Learning** | 17.0% | 52.7% | 3.40 | 11.6🐂 | ~20s |
| 🥉 **DQN** | 13.3% | 42.0% | 3.80 | 13.4🐂 | ~4min |
| ⬇️ 随机基准 | 12.0% | 43.6% | 3.76 | 13.6🐂 | — |

> 理论随机基准胜率（6人均匀分布）：**16.7%**

### 结论

- **PPO 最强**：策略梯度 + GAE + Clipped Loss 三重机制，学到最稳健的策略，胜率 22.3%，平均罚分仅 10 牛
- **Q-Learning 轻量高效**：无需深度学习库，20 秒训练即可超越随机基准，适合快速验证
- **DQN 潜力大**：神经网络方法在数据量少时不如表格法，训练局数增加到 100k+ 预计可超越 Q-Learning


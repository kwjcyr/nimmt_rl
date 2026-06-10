"""
牛头王 (6 Nimmt!) DQN 强化学习

算法：Deep Q-Network
  - 用神经网络近似 Q(s,a)，比表格 Q-Learning 泛化能力更强
  - Experience Replay：随机采样历史经验，打破时序相关性
  - Target Network：固定目标网络，提升训练稳定性

用法：
  python3 nimmt_dqn.py train    # 训练并保存模型
  python3 nimmt_dqn.py play     # 用训练好的模型对战展示
  python3 nimmt_dqn.py          # 先训练再对战
"""

import os
import random
import sys
from collections import deque

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

# =========================================================
#  游戏常量
# =========================================================
TOTAL_CARDS = 100
NUM_ROWS    = 5
MAX_ROW     = 6
HAND_SIZE   = 10
END_SCORE   = 66
NUM_PLAYERS = 6

MODEL_PATH  = "nimmt_dqn_model.pth"
AI_STRATEGIES = ["greedy", "safe", "greedy", "random", "safe"]


def get_bulls(card):
    if card == 55: return 7
    if card % 11 == 0: return 5
    if card % 10 == 0: return 3
    if card % 5 == 0: return 2
    return 1

def row_bulls(row):
    return sum(get_bulls(c) for c in row)

def shuffle_deal():
    deck = list(range(1, TOTAL_CARDS + 1))
    random.shuffle(deck)
    hands = [sorted(deck[i*HAND_SIZE:(i+1)*HAND_SIZE]) for i in range(NUM_PLAYERS)]
    start = NUM_PLAYERS * HAND_SIZE
    rows  = [[c] for c in sorted(deck[start:start+NUM_ROWS])]
    return hands, rows

def find_best_row(rows, card):
    best, min_diff = -1, float('inf')
    for r, row in enumerate(rows):
        tail = row[-1]
        if tail < card and (card - tail) < min_diff:
            min_diff = card - tail
            best = r
    return best

def place_card(rows, card, chosen_row=None):
    br = find_best_row(rows, card)
    if br == -1:
        r = chosen_row
        penalty = row_bulls(rows[r])
        rows[r] = [card]
    elif len(rows[br]) >= MAX_ROW:
        penalty = row_bulls(rows[br])
        rows[br] = [card]
    else:
        rows[br].append(card)
        penalty = 0
    return penalty

def ai_choose_card(hand, rows, strategy="greedy"):
    if strategy == "random":
        return random.choice(hand)
    scored = []
    for card in hand:
        br = find_best_row(rows, card)
        if br == -1:
            risk = min(row_bulls(r) for r in rows) + 80
        elif len(rows[br]) >= MAX_ROW:
            risk = row_bulls(rows[br]) + 40
        else:
            risk = len(rows[br]) * 3
        scored.append((card, risk + random.uniform(-5, 5)))
    scored.sort(key=lambda x: x[1])
    if strategy == "safe":
        return scored[0][0]
    r = random.random()
    if r < 0.70:
        pool = scored[:max(1, len(scored)//3)]
    elif r < 0.90:
        mid = len(scored)//2
        pool = scored[max(0,mid-1):mid+2]
    else:
        return hand[-1]
    return random.choice(pool)[0]

def ai_choose_row(rows):
    return min(range(NUM_ROWS), key=lambda r: row_bulls(rows[r]))


# =========================================================
#  状态向量（连续，供神经网络使用）
#  维度：10*2（手牌） + 5*3（牌桌） + 1（得分） = 36
# =========================================================
STATE_DIM  = HAND_SIZE * 2 + NUM_ROWS * 3 + 1
ACTION_DIM = HAND_SIZE   # 最多10个动作（选第几张牌）


def encode_state(hand, rows, my_score):
    """
    手牌：每张 [牌面/100, 牛头/7]，不足10张补0
    牌桌：每列 [末尾牌/100, 已填/6, 本列牛头/35]
    得分：[累计/66]
    """
    vec = []
    for i in range(HAND_SIZE):
        if i < len(hand):
            vec += [hand[i] / 100.0, get_bulls(hand[i]) / 7.0]
        else:
            vec += [0.0, 0.0]
    for row in rows:
        vec += [row[-1] / 100.0, len(row) / MAX_ROW, row_bulls(row) / 35.0]
    vec.append(min(my_score, END_SCORE) / END_SCORE)
    return np.array(vec, dtype=np.float32)


# =========================================================
#  DQN 网络
# =========================================================
class DQNNet(nn.Module):
    def __init__(self, state_dim=STATE_DIM, action_dim=ACTION_DIM):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 128),
            nn.ReLU(),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, action_dim)
        )

    def forward(self, x):
        return self.net(x)


# =========================================================
#  经验回放池
# =========================================================
class ReplayBuffer:
    def __init__(self, capacity=20000):
        self.buf = deque(maxlen=capacity)

    def push(self, state, action, reward, next_state, done):
        self.buf.append((state, action, reward, next_state, done))

    def sample(self, batch_size):
        batch = random.sample(self.buf, batch_size)
        s, a, r, ns, d = zip(*batch)
        return (np.array(s), np.array(a), np.array(r, dtype=np.float32),
                np.array(ns), np.array(d, dtype=np.float32))

    def __len__(self):
        return len(self.buf)


# =========================================================
#  DQN Agent
# =========================================================
class DQNAgent:
    def __init__(self, lr=1e-3, gamma=0.95, epsilon=1.0,
                 epsilon_min=0.05, epsilon_decay=0.9995,
                 batch_size=64, target_update=200):
        self.gamma         = gamma
        self.epsilon       = epsilon
        self.epsilon_min   = epsilon_min
        self.epsilon_decay = epsilon_decay
        self.batch_size    = batch_size
        self.target_update = target_update
        self.learn_step    = 0

        self.net    = DQNNet()
        self.target = DQNNet()
        self.target.load_state_dict(self.net.state_dict())
        self.target.eval()

        self.optimizer = optim.Adam(self.net.parameters(), lr=lr)
        self.loss_fn   = nn.MSELoss()
        self.buffer    = ReplayBuffer()

    def get_action(self, hand, rows, my_score, training=True):
        n = len(hand)
        if training and random.random() < self.epsilon:
            return random.randint(0, n - 1)

        state = encode_state(hand, rows, my_score)
        with torch.no_grad():
            q = self.net(torch.FloatTensor(state))
        # mask 掉超出手牌数的无效动作
        q_valid = q[:n]
        return int(torch.argmax(q_valid).item())

    def store(self, hand, rows, score, action, reward, next_hand, next_rows, next_score, done):
        s  = encode_state(hand, rows, score)
        ns = encode_state(next_hand, next_rows, next_score)
        self.buffer.push(s, action, reward, ns, done)

    def learn(self):
        if len(self.buffer) < self.batch_size:
            return

        s, a, r, ns, d = self.buffer.sample(self.batch_size)
        s  = torch.FloatTensor(s)
        ns = torch.FloatTensor(ns)
        a  = torch.LongTensor(a)
        r  = torch.FloatTensor(r)
        d  = torch.FloatTensor(d)

        # 当前 Q 值
        q_curr = self.net(s).gather(1, a.unsqueeze(1)).squeeze(1)

        # 目标 Q 值（用 target 网络）
        with torch.no_grad():
            q_next = self.target(ns).max(1)[0]
            q_target = r + self.gamma * q_next * (1 - d)

        loss = self.loss_fn(q_curr, q_target)
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.net.parameters(), 1.0)
        self.optimizer.step()

        self.learn_step += 1
        if self.learn_step % self.target_update == 0:
            self.target.load_state_dict(self.net.state_dict())

        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

    def save(self, path=MODEL_PATH):
        torch.save(self.net.state_dict(), path)
        print(f"💾 DQN 模型已保存: {path}")

    def load(self, path=MODEL_PATH):
        self.net.load_state_dict(torch.load(path, map_location="cpu"))
        self.net.eval()
        self.epsilon = self.epsilon_min
        print(f"📂 DQN 模型已加载: {path}")


# =========================================================
#  训练一局
# =========================================================
def run_episode(agent, training=True):
    hands, rows = shuffle_deal()
    scores = [0] * NUM_PLAYERS

    for _ in range(HAND_SIZE):
        hand_b  = hands[0][:]
        rows_b  = [r[:] for r in rows]
        score_b = scores[0]

        action_idx = agent.get_action(hands[0], rows, scores[0], training=training)
        chosen = [None] * NUM_PLAYERS
        chosen[0] = hands[0][action_idx]
        for i in range(1, NUM_PLAYERS):
            chosen[i] = ai_choose_card(hands[i], rows, AI_STRATEGIES[i-1])
        for i in range(NUM_PLAYERS):
            hands[i].remove(chosen[i])

        order = sorted(range(NUM_PLAYERS), key=lambda i: chosen[i])
        round_pen = [0] * NUM_PLAYERS
        for pi in order:
            card = chosen[pi]
            br = find_best_row(rows, card)
            if br == -1:
                r = ai_choose_row(rows)
                pen = place_card(rows, card, r)
            else:
                pen = place_card(rows, card)
            round_pen[pi] = pen
            scores[pi] += pen

        reward = -round_pen[0]
        done   = (len(hands[0]) == 0) or (max(scores) >= END_SCORE)

        if training:
            agent.store(hand_b, rows_b, score_b, action_idx,
                        reward, hands[0], rows, scores[0], done)
            agent.learn()

        if done:
            break

    # 排名奖励
    ranking = sorted(range(NUM_PLAYERS), key=lambda i: scores[i])
    agent_rank = ranking.index(0)
    if training:
        rank_reward = [+5, +3, +1, -1, -3, -5][agent_rank]
        agent.store(hands[0], rows, scores[0], 0,
                    rank_reward, [], rows, scores[0], True)
        agent.learn()

    return scores[0], agent_rank


# =========================================================
#  训练
# =========================================================
def train(episodes=30000):
    agent = DQNAgent()
    print("=" * 55)
    print("🧠 牛头王 DQN 训练")
    print(f"   局数: {episodes}  |  网络: 36→128→128→64→10")
    print("=" * 55)

    penalties, ranks = [], []
    for ep in range(1, episodes + 1):
        pen, rank = run_episode(agent, training=True)
        penalties.append(pen)
        ranks.append(rank)

        if ep % 2000 == 0:
            avg_pen  = sum(penalties[-2000:]) / 2000
            avg_rank = sum(ranks[-2000:]) / 2000 + 1
            win_rate = ranks[-2000:].count(0) / 2000 * 100
            print(f"  Ep {ep:6d} | ε={agent.epsilon:.3f} | "
                  f"平均罚分={avg_pen:.1f}🐂 | 平均排名={avg_rank:.2f} | "
                  f"胜率={win_rate:.1f}%")

    agent.save(MODEL_PATH)
    print("\n✅ DQN 训练完成！")
    return agent


# =========================================================
#  对战展示
# =========================================================
PLAYER_NAMES = ["RL(DQN)", "AI-甲", "AI-乙", "AI-丙", "AI-丁", "AI-戊"]

def play(agent=None):
    if agent is None:
        agent = DQNAgent()
        if os.path.exists(MODEL_PATH):
            agent.load(MODEL_PATH)
        else:
            print("⚠️  未找到模型，使用随机策略")

    hands, rows = shuffle_deal()
    scores = [0] * NUM_PLAYERS
    print("\n" + "=" * 60)
    print("  🐂 牛头王对战（DQN vs 5 AI）")
    print("=" * 60)

    for rnd in range(1, HAND_SIZE + 1):
        print(f"\n  第 {rnd} 轮")
        action_idx = agent.get_action(hands[0], rows, scores[0], training=False)
        chosen = [None] * NUM_PLAYERS
        chosen[0] = hands[0][action_idx]
        for i in range(1, NUM_PLAYERS):
            chosen[i] = ai_choose_card(hands[i], rows, AI_STRATEGIES[i-1])
        for i in range(NUM_PLAYERS):
            hands[i].remove(chosen[i])

        print(f"  DQN 出牌: {chosen[0]}({'🐂'*get_bulls(chosen[0])})"
              f"  AI: {[chosen[i] for i in range(1,NUM_PLAYERS)]}")

        order = sorted(range(NUM_PLAYERS), key=lambda i: chosen[i])
        for pi in order:
            card = chosen[pi]
            br = find_best_row(rows, card)
            if br == -1:
                r = ai_choose_row(rows)
                pen = place_card(rows, card, r)
                if pen: print(f"  {PLAYER_NAMES[pi]}: {card} 收走列{r+1} 💥-{pen}🐂")
            else:
                pen = place_card(rows, card)
                if pen: print(f"  {PLAYER_NAMES[pi]}: {card} 列满收走 💥-{pen}🐂")
            scores[pi] += pen
        if max(scores) >= END_SCORE:
            break

    print("\n  🏆 最终排名：")
    medals = ["🥇","🥈","🥉","4️⃣ ","5️⃣ ","6️⃣ "]
    for rank, pi in enumerate(sorted(range(NUM_PLAYERS), key=lambda i: scores[i])):
        print(f"  {medals[rank]} {PLAYER_NAMES[pi]:10s}: {scores[pi]}🐂")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    if mode == "train":
        train()
    elif mode == "play":
        play()
    else:
        agent = train()
        play(agent)


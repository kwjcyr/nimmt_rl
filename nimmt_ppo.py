"""
牛头王 (6 Nimmt!) PPO 强化学习

算法：Proximal Policy Optimization (PPO)
  - Actor-Critic 架构：策略网络(Actor) + 价值网络(Critic) 共享主干
  - Clipped Surrogate Objective：限制策略更新幅度，防止过大更新
  - GAE (Generalized Advantage Estimation)：低方差优势估计
  - 多 epoch 更新：每局数据重复使用多次，样本效率高

用法：
  python3 nimmt_ppo.py train    # 训练并保存模型
  python3 nimmt_ppo.py play     # 用训练好的模型对战展示
  python3 nimmt_ppo.py          # 先训练再对战
"""

import os
import random
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.distributions import Categorical

# =========================================================
#  游戏常量
# =========================================================
TOTAL_CARDS  = 100
NUM_ROWS     = 5
MAX_ROW      = 6
HAND_SIZE    = 10
END_SCORE    = 66
NUM_PLAYERS  = 6

MODEL_PATH   = "nimmt_ppo_model.pth"
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
#  状态编码（与 DQN 版相同，36 维）
# =========================================================
STATE_DIM  = HAND_SIZE * 2 + NUM_ROWS * 3 + 1
ACTION_DIM = HAND_SIZE


def encode_state(hand, rows, my_score):
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
#  Actor-Critic 网络（共享主干）
# =========================================================
class ActorCritic(nn.Module):
    def __init__(self, state_dim=STATE_DIM, action_dim=ACTION_DIM):
        super().__init__()
        # 共享主干
        self.backbone = nn.Sequential(
            nn.Linear(state_dim, 128),
            nn.Tanh(),
            nn.Linear(128, 128),
            nn.Tanh(),
        )
        # Actor head：输出 logits
        self.actor_head = nn.Linear(128, action_dim)
        # Critic head：输出状态价值
        self.critic_head = nn.Linear(128, 1)

    def forward(self, x):
        feat  = self.backbone(x)
        logit = self.actor_head(feat)
        value = self.critic_head(feat).squeeze(-1)
        return logit, value

    def get_action(self, state_vec, n_valid):
        """采样动作，返回 (action_idx, log_prob, entropy, value)"""
        x      = torch.FloatTensor(state_vec).unsqueeze(0)
        logit, value = self(x)
        # mask 无效动作（超出手牌数）
        mask = torch.full((1, ACTION_DIM), float('-inf'))
        mask[0, :n_valid] = 0.0
        logit_masked = logit + mask
        dist   = Categorical(logits=logit_masked)
        action = dist.sample()
        return (action.item(),
                dist.log_prob(action),
                dist.entropy(),
                value.squeeze(0))


# =========================================================
#  PPO Agent
# =========================================================
class PPOAgent:
    def __init__(self, lr=3e-4, gamma=0.95, lam=0.95,
                 clip_eps=0.2, value_coef=0.5, entropy_coef=0.01,
                 ppo_epochs=4, batch_size=64):
        self.gamma        = gamma
        self.lam          = lam
        self.clip_eps     = clip_eps
        self.value_coef   = value_coef
        self.entropy_coef = entropy_coef
        self.ppo_epochs   = ppo_epochs
        self.batch_size   = batch_size

        self.net       = ActorCritic()
        self.optimizer = optim.Adam(self.net.parameters(), lr=lr)

        # 轨迹缓冲（每局清空）
        self.reset_buffer()

    def reset_buffer(self):
        self.buf_states  = []
        self.buf_actions = []
        self.buf_logprobs= []
        self.buf_rewards = []
        self.buf_values  = []
        self.buf_dones   = []
        self.buf_nvalid  = []

    def store(self, state, action, logprob, reward, value, done, n_valid):
        self.buf_states.append(state)
        self.buf_actions.append(action)
        self.buf_logprobs.append(logprob.item())
        self.buf_rewards.append(reward)
        self.buf_values.append(value.item())
        self.buf_dones.append(done)
        self.buf_nvalid.append(n_valid)

    # ---- GAE 优势估计 ----
    def compute_gae(self):
        T = len(self.buf_rewards)
        advantages = [0.0] * T
        last_gae   = 0.0
        for t in reversed(range(T)):
            done  = float(self.buf_dones[t])
            next_v = self.buf_values[t+1] if t+1 < T else 0.0
            delta  = (self.buf_rewards[t]
                      + self.gamma * next_v * (1 - done)
                      - self.buf_values[t])
            last_gae = delta + self.gamma * self.lam * (1 - done) * last_gae
            advantages[t] = last_gae
        returns = [advantages[t] + self.buf_values[t] for t in range(T)]
        return advantages, returns

    # ---- PPO 更新 ----
    def update(self):
        if len(self.buf_rewards) == 0:
            return

        advantages, returns = self.compute_gae()

        states   = torch.FloatTensor(np.array(self.buf_states))
        actions  = torch.LongTensor(self.buf_actions)
        old_lp   = torch.FloatTensor(self.buf_logprobs)
        advs     = torch.FloatTensor(advantages)
        rets     = torch.FloatTensor(returns)
        nvalids  = self.buf_nvalid

        # 归一化优势
        advs = (advs - advs.mean()) / (advs.std() + 1e-8)

        T = len(self.buf_rewards)
        for _ in range(self.ppo_epochs):
            idx = list(range(T))
            random.shuffle(idx)
            for start in range(0, T, self.batch_size):
                b = idx[start:start+self.batch_size]
                b_s  = states[b]
                b_a  = actions[b]
                b_olp= old_lp[b]
                b_adv= advs[b]
                b_ret= rets[b]
                b_nv = [nvalids[i] for i in b]

                logits, values = self.net(b_s)
                # 按各样本有效动作数构建 mask
                masks = torch.full_like(logits, float('-inf'))
                for k, nv in enumerate(b_nv):
                    masks[k, :nv] = 0.0
                logits = logits + masks
                dist   = Categorical(logits=logits)
                new_lp = dist.log_prob(b_a)
                entropy= dist.entropy().mean()

                # Clipped surrogate loss
                ratio  = torch.exp(new_lp - b_olp)
                surr1  = ratio * b_adv
                surr2  = torch.clamp(ratio, 1 - self.clip_eps, 1 + self.clip_eps) * b_adv
                actor_loss  = -torch.min(surr1, surr2).mean()
                critic_loss = nn.functional.mse_loss(values, b_ret)
                loss = (actor_loss
                        + self.value_coef * critic_loss
                        - self.entropy_coef * entropy)

                self.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.net.parameters(), 0.5)
                self.optimizer.step()

        self.reset_buffer()

    def get_action_eval(self, hand, rows, my_score):
        """推理时贪心选最大概率动作"""
        state = encode_state(hand, rows, my_score)
        n = len(hand)
        x = torch.FloatTensor(state).unsqueeze(0)
        with torch.no_grad():
            logit, _ = self.net(x)
        logit[0, n:] = float('-inf')
        return int(torch.argmax(logit[0, :n]).item())

    def save(self, path=MODEL_PATH):
        torch.save(self.net.state_dict(), path)
        print(f"💾 PPO 模型已保存: {path}")

    def load(self, path=MODEL_PATH):
        self.net.load_state_dict(torch.load(path, map_location="cpu"))
        self.net.eval()
        print(f"📂 PPO 模型已加载: {path}")


# =========================================================
#  训练一局
# =========================================================
def run_episode(agent, training=True):
    hands, rows = shuffle_deal()
    scores = [0] * NUM_PLAYERS

    for step in range(HAND_SIZE):
        state = encode_state(hands[0], rows, scores[0])
        n = len(hands[0])

        if training:
            action_idx, logprob, entropy, value = agent.net.get_action(state, n)
        else:
            action_idx = agent.get_action_eval(hands[0], rows, scores[0])
            logprob, value = None, None

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
            agent.store(state, action_idx, logprob, reward, value, done, n)

        if done:
            break

    # 终局排名奖励
    ranking = sorted(range(NUM_PLAYERS), key=lambda i: scores[i])
    agent_rank = ranking.index(0)
    if training:
        final_state = encode_state(hands[0], rows, scores[0])
        _, _, _, final_val = agent.net.get_action(final_state, max(len(hands[0]), 1))
        rank_reward = [+8, +4, +1, -2, -5, -8][agent_rank]
        agent.store(final_state, 0,
                    torch.tensor(0.0, requires_grad=False),
                    rank_reward, final_val, True, max(len(hands[0]), 1))

    return scores[0], agent_rank


# =========================================================
#  训练
# =========================================================
def train(episodes=30000):
    agent = PPOAgent()
    print("=" * 60)
    print("🧠 牛头王 PPO 训练")
    print(f"   局数: {episodes}  |  Actor-Critic 共享主干 128×128")
    print(f"   GAE λ=0.95  |  Clip ε=0.2  |  PPO epochs=4")
    print("=" * 60)

    penalties, ranks = [], []
    update_every = 10   # 每 10 局更新一次

    for ep in range(1, episodes + 1):
        pen, rank = run_episode(agent, training=True)
        penalties.append(pen)
        ranks.append(rank)

        if ep % update_every == 0:
            agent.update()

        if ep % 2000 == 0:
            avg_pen  = sum(penalties[-2000:]) / 2000
            avg_rank = sum(ranks[-2000:]) / 2000 + 1
            win_rate = ranks[-2000:].count(0) / 2000 * 100
            print(f"  Ep {ep:6d} | 平均罚分={avg_pen:.1f}🐂 | "
                  f"平均排名={avg_rank:.2f} | 胜率={win_rate:.1f}%")

    agent.save(MODEL_PATH)
    print("\n✅ PPO 训练完成！")
    return agent


# =========================================================
#  对战展示
# =========================================================
PLAYER_NAMES = ["RL(PPO)", "AI-甲", "AI-乙", "AI-丙", "AI-丁", "AI-戊"]

def play(agent=None):
    if agent is None:
        agent = PPOAgent()
        if os.path.exists(MODEL_PATH):
            agent.load(MODEL_PATH)
        else:
            print("⚠️  未找到模型，使用随机策略")

    hands, rows = shuffle_deal()
    scores = [0] * NUM_PLAYERS
    print("\n" + "=" * 60)
    print("  🐂 牛头王对战（PPO vs 5 AI）")
    print("=" * 60)

    for rnd in range(1, HAND_SIZE + 1):
        print(f"\n  第 {rnd} 轮")
        action_idx = agent.get_action_eval(hands[0], rows, scores[0])
        chosen = [None] * NUM_PLAYERS
        chosen[0] = hands[0][action_idx]
        for i in range(1, NUM_PLAYERS):
            chosen[i] = ai_choose_card(hands[i], rows, AI_STRATEGIES[i-1])
        for i in range(NUM_PLAYERS):
            hands[i].remove(chosen[i])

        print(f"  PPO 出牌: {chosen[0]}({'🐂'*get_bulls(chosen[0])})"
              f"  AI: {[chosen[i] for i in range(1, NUM_PLAYERS)]}")

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
                if pen: print(f"  {PLAYER_NAMES[pi]}: {card} 💥-{pen}🐂")
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


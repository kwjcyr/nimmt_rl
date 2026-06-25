"""
牛头王 (6 Nimmt!) PPO 强化学习 + 行为克隆

支持功能：
  train          : 在线强化学习训练（可基于已有模型继续）
  play           : 全自动对战展示，可选保存轨迹
  collect        : 批量收集含 PPO 的轨迹（用于离线训练）
  offline        : 离线 PPO 训练（从 collect 生成的 JSON）
  human          : 人机对战（自动保存轨迹，格式与小程序一致）
  behavior_clone : 从人机对战日志训练行为克隆模型
  load_bc        : 将行为克隆模型加载到 PPO 作为初始策略（与 train 配合）

用法示例：
  python nimmt_ppo_traj.py human                     # 人机对战，自动保存
  python nimmt_ppo_traj.py behavior_clone traj.json  # 训练行为克隆
  python nimmt_ppo_traj.py load_bc bc_model.pth      # 加载 BC 权重，然后可运行 train
  python nimmt_ppo_traj.py train                     # 继续强化学习（若已加载 BC 则使用之）
"""

import json
import os
import random
import sys
import uuid
from collections import defaultdict
from datetime import datetime

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.distributions import Categorical
from torch.utils.data import DataLoader, TensorDataset

# =========================================================
#  游戏常量
# =========================================================
TOTAL_CARDS  = 100
NUM_ROWS     = 5
MAX_ROW      = 6
HAND_SIZE    = 10
END_SCORE    = 66
NUM_PLAYERS  = 6

_DIR        = os.path.dirname(os.path.abspath(__file__))
_MODELS_DIR = os.path.join(_DIR, "..", "models")
os.makedirs(_MODELS_DIR, exist_ok=True)
MODEL_PATH  = os.path.join(_MODELS_DIR, "nimmt_ppo_model.pth")

# 轨迹默认保存目录（人机对战自动保存）
_DEFAULT_TRAJ_DIR = os.path.join(_DIR, "trajectories", "human")
os.makedirs(_DEFAULT_TRAJ_DIR, exist_ok=True)

# BC 模型保存路径（可选）
BC_MODEL_PATH = os.path.join(_MODELS_DIR, "bc_model.pth")

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
#  状态编码（36 维）
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
#  Actor-Critic 网络
# =========================================================
class ActorCritic(nn.Module):
    def __init__(self, state_dim=STATE_DIM, action_dim=ACTION_DIM, dropout=0.0):
        super().__init__()
        self.backbone = nn.Sequential(
            nn.Linear(state_dim, 128),
            nn.Tanh(),
            nn.Dropout(p=dropout),
            nn.Linear(128, 128),
            nn.Tanh(),
            nn.Dropout(p=dropout),
        )
        self.actor_head = nn.Linear(128, action_dim)
        self.critic_head = nn.Linear(128, 1)

    def forward(self, x):
        feat  = self.backbone(x)
        logit = self.actor_head(feat)
        value = self.critic_head(feat).squeeze(-1)
        return logit, value

    def get_action(self, state_vec, n_valid):
        x      = torch.FloatTensor(state_vec).unsqueeze(0)
        logit, value = self(x)
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
                masks = torch.full_like(logits, float('-inf'))
                for k, nv in enumerate(b_nv):
                    masks[k, :nv] = 0.0
                logits = logits + masks
                dist   = Categorical(logits=logits)
                new_lp = dist.log_prob(b_a)
                entropy= dist.entropy().mean()

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

    # ---------- 静态方法：离线训练 ----------
    @staticmethod
    def train_from_buffer(agent, buffer, ppo_epochs=None, batch_size=None):
        if ppo_epochs is None:
            ppo_epochs = agent.ppo_epochs
        if batch_size is None:
            batch_size = agent.batch_size

        states = torch.FloatTensor(np.array(buffer['states']))
        actions = torch.LongTensor(buffer['actions'])
        old_lp = torch.FloatTensor(buffer['logprobs'])
        rewards = torch.FloatTensor(buffer['rewards'])
        values = torch.FloatTensor(buffer['values'])
        dones = torch.BoolTensor(buffer['dones'])
        nvalids = buffer['nvalids']

        T = len(rewards)
        advantages = torch.zeros(T)
        last_gae = 0.0
        for t in reversed(range(T)):
            next_v = values[t+1] if t+1 < T else 0.0
            delta = rewards[t] + agent.gamma * next_v * (1 - float(dones[t])) - values[t]
            last_gae = delta + agent.gamma * agent.lam * (1 - float(dones[t])) * last_gae
            advantages[t] = last_gae
        returns = advantages + values

        advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

        indices = list(range(T))
        for _ in range(ppo_epochs):
            random.shuffle(indices)
            for start in range(0, T, batch_size):
                b = indices[start:start+batch_size]
                b_s = states[b]
                b_a = actions[b]
                b_olp = old_lp[b]
                b_adv = advantages[b]
                b_ret = returns[b]
                b_nv = [nvalids[i] for i in b]

                logits, values_pred = agent.net(b_s)
                masks = torch.full_like(logits, float('-inf'))
                for k, nv in enumerate(b_nv):
                    masks[k, :nv] = 0.0
                logits = logits + masks
                dist = Categorical(logits=logits)
                new_lp = dist.log_prob(b_a)
                entropy = dist.entropy().mean()

                ratio = torch.exp(new_lp - b_olp)
                surr1 = ratio * b_adv
                surr2 = torch.clamp(ratio, 1 - agent.clip_eps, 1 + agent.clip_eps) * b_adv
                actor_loss = -torch.min(surr1, surr2).mean()
                critic_loss = nn.functional.mse_loss(values_pred, b_ret)
                loss = actor_loss + agent.value_coef * critic_loss - agent.entropy_coef * entropy

                agent.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(agent.net.parameters(), 0.5)
                agent.optimizer.step()

    def get_action_eval(self, hand, rows, my_score):
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

    # ---------- 加载行为克隆权重 ----------
    def load_bc_weights(self, backbone_path, actor_path):
        """加载行为克隆训练得到的 backbone 和 actor_head 权重"""
        self.net.backbone.load_state_dict(torch.load(backbone_path, map_location="cpu"))
        self.net.actor_head.load_state_dict(torch.load(actor_path, map_location="cpu"))
        print(f"📂 已加载行为克隆模型: backbone={backbone_path}, actor={actor_path}")

# =========================================================
#  在线训练一局（原版）
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

def train(episodes=30000, from_scratch=False, load_bc=False):
    """
    在线训练 PPO。
    - from_scratch: 若 True 则完全随机初始化
    - load_bc: 若 True 则加载行为克隆模型作为初始策略（需先训练 BC）
    """
    agent = PPOAgent()

    bc_loaded = False
    if load_bc:
        # 加载 BC 权重（优先级高于已有 PPO 模型）
        bc_backbone = os.path.join(_MODELS_DIR, "bc_backbone.pth")
        bc_actor = os.path.join(_MODELS_DIR, "bc_actor.pth")
        if os.path.exists(bc_backbone) and os.path.exists(bc_actor):
            agent.load_bc_weights(bc_backbone, bc_actor)
            bc_loaded = True
        else:
            print("⚠️  未找到 BC 模型，将从头开始训练")
            from_scratch = True

    # 只有在未加载 BC 且非从头开始时，才加载已有 PPO 模型
    # 避免 BC 权重被旧 PPO 模型覆盖
    if not bc_loaded and not from_scratch and os.path.exists(MODEL_PATH):
        agent.load(MODEL_PATH)
        print("📂 加载已有 PPO 模型，继续训练...")
    elif not bc_loaded and not from_scratch:
        print("🆕 未找到已有模型，从头开始训练...")

    print("=" * 60)
    print("🧠 牛头王 PPO 在线训练")
    print(f"   局数: {episodes}  |  Actor-Critic 共享主干 128×128")
    print("=" * 60)

    penalties, ranks = [], []
    update_every = 10  # 每 10 局更新一次，积累足够样本降低梯度方差

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
#  全自动对战展示（支持保存轨迹）
# =========================================================
PLAYER_NAMES = ["RL(PPO)", "AI-甲", "AI-乙", "AI-丙", "AI-丁", "AI-戊"]

def play(agent=None, save_path=None):
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
    if save_path:
        print(f"  💾 轨迹将保存至: {save_path}")
    print("=" * 60)

    players_config = [
        {"id": "ppo_0", "type": "ppo", "model": "current"},
        {"id": "ai_greedy_1", "type": "rule", "strategy": "greedy"},
        {"id": "ai_safe_2", "type": "rule", "strategy": "safe"},
        {"id": "ai_greedy_3", "type": "rule", "strategy": "greedy"},
        {"id": "ai_random_4", "type": "rule", "strategy": "random"},
        {"id": "ai_safe_5", "type": "rule", "strategy": "safe"},
    ]
    game_record = {
        "game_id": f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
        "timestamp": datetime.now().isoformat(),
        "players": players_config,
        "initial_rows": [row.copy() for row in rows],
        "rounds": []
    }

    for rnd in range(1, HAND_SIZE + 1):
        print(f"\n  第 {rnd} 轮")
        action_idx = agent.get_action_eval(hands[0], rows, scores[0])
        chosen = [None] * NUM_PLAYERS
        chosen[0] = hands[0][action_idx]
        for i in range(1, NUM_PLAYERS):
            chosen[i] = ai_choose_card(hands[i], rows, AI_STRATEGIES[i-1])
        hands_before = [hand.copy() for hand in hands]

        for i in range(NUM_PLAYERS):
            hands[i].remove(chosen[i])

        print(f"  PPO 出牌: {chosen[0]}({'🐂'*get_bulls(chosen[0])})"
              f"  AI: {[chosen[i] for i in range(1, NUM_PLAYERS)]}")

        order = sorted(range(NUM_PLAYERS), key=lambda i: chosen[i])
        round_plays = []
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

            if save_path:
                play_info = {
                    "player_id": players_config[pi]['id'],
                    "card_played": card,
                    "hand_before": hands_before[pi],
                    "penalty": pen,
                    "row_affected": r if br == -1 else br,
                }
                # 如果是 PPO 玩家，可加入 RL 字段（但 play 模式通常不存）
                round_plays.append(play_info)

        if save_path:
            round_record = {
                "round": rnd,
                "plays": round_plays,
                "scores_after": scores.copy()
            }
            game_record["rounds"].append(round_record)

        if max(scores) >= END_SCORE:
            break

    ranking = sorted(range(NUM_PLAYERS), key=lambda i: scores[i])
    print("\n  🏆 最终排名：")
    medals = ["🥇","🥈","🥉","4️⃣ ","5️⃣ ","6️⃣ "]
    for rank, pi in enumerate(ranking):
        print(f"  {medals[rank]} {PLAYER_NAMES[pi]:10s}: {scores[pi]}🐂")

    if save_path:
        game_record["final_scores"] = scores
        game_record["ranking"] = ranking
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(game_record, f, indent=2, ensure_ascii=False)
        print(f"\n💾 该局轨迹已保存至: {save_path}")

# =========================================================
#  批量轨迹收集（含 PPO，用于离线训练）
# =========================================================
def run_game_collect_all(players_config, ppo_agents):
    hands, rows = shuffle_deal()
    scores = [0] * len(players_config)
    game_record = {
        "game_id": f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
        "timestamp": datetime.now().isoformat(),
        "players": players_config,
        "initial_rows": [row.copy() for row in rows],
        "rounds": []
    }

    for round_num in range(1, HAND_SIZE + 1):
        hands_before = [hand.copy() for hand in hands]
        chosen_cards = [None] * len(players_config)
        rl_data_per_player = {}

        for idx, player in enumerate(players_config):
            pid = player['id']
            if player['type'] == 'ppo':
                agent = ppo_agents[pid]
                state = encode_state(hands[idx], rows, scores[idx])
                n = len(hands[idx])
                action_idx, logprob, _, value = agent.net.get_action(state, n)
                chosen_cards[idx] = hands[idx][action_idx]
                rl_data_per_player[pid] = {
                    "state": state.tolist(),
                    "action": action_idx,
                    "logprob": logprob.item(),
                    "value": value.item(),
                    "n_valid": n,
                    "done": False
                }
            elif player['type'] == 'rule':
                strategy = player.get('strategy', 'greedy')
                chosen_cards[idx] = ai_choose_card(hands[idx], rows, strategy)
            else:
                chosen_cards[idx] = random.choice(hands[idx])
            hands[idx].remove(chosen_cards[idx])

        order = sorted(range(len(players_config)), key=lambda i: chosen_cards[i])
        round_plays = []
        for idx in order:
            card = chosen_cards[idx]
            br = find_best_row(rows, card)
            if br == -1:
                r = ai_choose_row(rows)
                pen = place_card(rows, card, r)
            else:
                pen = place_card(rows, card)
            scores[idx] += pen

            play_info = {
                "player_id": players_config[idx]['id'],
                "card_played": card,
                "hand_before": hands_before[idx],
                "penalty": pen,
                "row_affected": r if br == -1 else br,
            }
            pid = players_config[idx]['id']
            if pid in rl_data_per_player:
                play_info["rl"] = rl_data_per_player[pid].copy()
                play_info["rl"]["reward"] = -pen
            round_plays.append(play_info)

        round_record = {
            "round": round_num,
            "plays": round_plays,
            "scores_after": scores.copy()
        }
        game_record["rounds"].append(round_record)

        if max(scores) >= END_SCORE or all(len(h) == 0 for h in hands):
            ranking = sorted(range(len(players_config)), key=lambda i: scores[i])
            for rank_pos, idx in enumerate(ranking):
                pid = players_config[idx]['id']
                if pid in rl_data_per_player:
                    for play in round_plays:
                        if play['player_id'] == pid:
                            if 'rl' in play:
                                play['rl']['done'] = True
                                rank_reward_list = [8, 4, 1, -2, -5, -8]
                                play['rl']['reward'] += rank_reward_list[rank_pos]
                            break
            break

    game_record["final_scores"] = scores
    game_record["ranking"] = sorted(range(len(players_config)), key=lambda i: scores[i])
    return game_record

def collect_games_to_json(num_games, filepath="trajectories.json", num_ppo=3):
    players_config = []
    ppo_agents = {}
    for i in range(NUM_PLAYERS):
        if i < num_ppo:
            pid = f"ppo_{i}"
            players_config.append({"id": pid, "type": "ppo", "model": "current"})
            ppo_agents[pid] = PPOAgent()
        else:
            strategy = AI_STRATEGIES[(i - num_ppo) % len(AI_STRATEGIES)]
            players_config.append({"id": f"ai_{strategy}_{i}", "type": "rule", "strategy": strategy})

    all_games = []
    for g in range(num_games):
        game_record = run_game_collect_all(players_config, ppo_agents)
        all_games.append(game_record)
        if (g + 1) % 100 == 0:
            print(f"已收集 {g+1} 局")

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(all_games, f, indent=2, ensure_ascii=False)
    print(f"✅ 保存 {num_games} 局轨迹至 {filepath}")

# =========================================================
#  离线 PPO 训练（从 collect 生成的 JSON）
# =========================================================
def train_from_json(filepath, model_save_path=MODEL_PATH, ppo_epochs=4, batch_size=64):
    with open(filepath, 'r', encoding='utf-8') as f:
        games = json.load(f)

    buffer = defaultdict(list)

    for game in games:
        ppo_player_ids = [p['id'] for p in game['players'] if p['type'] == 'ppo']
        if not ppo_player_ids:
            continue
        for round_data in game['rounds']:
            for play in round_data['plays']:
                pid = play['player_id']
                if pid not in ppo_player_ids:
                    continue
                if 'rl' not in play:
                    continue
                rl = play['rl']
                buffer['states'].append(rl['state'])
                buffer['actions'].append(rl['action'])
                buffer['logprobs'].append(rl['logprob'])
                buffer['rewards'].append(rl['reward'])
                buffer['values'].append(rl['value'])
                buffer['dones'].append(rl.get('done', False))
                buffer['nvalids'].append(rl['n_valid'])

    if len(buffer['states']) == 0:
        print("⚠️  未找到任何有效样本，训练终止。")
        return None

    agent = PPOAgent(ppo_epochs=ppo_epochs, batch_size=batch_size)
    PPOAgent.train_from_buffer(agent, buffer, ppo_epochs, batch_size)
    agent.save(model_save_path)
    print(f"✅ 离线训练完成，模型已保存至 {model_save_path}")
    return agent

# =========================================================
#  人机对战（格式与小程序一致：hand_before + action_idx，无 state 向量）
# =========================================================
def human_play(agent=None, save_path=None):
    if agent is None:
        agent = PPOAgent()
        if os.path.exists(MODEL_PATH):
            agent.load(MODEL_PATH)
        else:
            print("⚠️  未找到模型，PPO 使用随机策略")

    hands, rows = shuffle_deal()
    scores = [0] * NUM_PLAYERS
    human_idx = 0

    players_config = [
        {"id": "human", "type": "human"},
        {"id": "ai_greedy_1", "type": "rule", "strategy": "greedy"},
        {"id": "ai_safe_2", "type": "rule", "strategy": "safe"},
        {"id": "ai_greedy_3", "type": "rule", "strategy": "greedy"},
        {"id": "ai_random_4", "type": "rule", "strategy": "random"},
        {"id": "ai_safe_5", "type": "rule", "strategy": "safe"},
    ]
    PLAYER_NAMES_HUMAN = ["🧑 You", "AI-甲", "AI-乙", "AI-丙", "AI-丁", "AI-戊"]

    game_record = {
        "game_id": f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
        "timestamp": datetime.now().isoformat(),
        "players": players_config,
        "initial_rows": [row.copy() for row in rows],
        "rounds": []
    }

    print("\n" + "=" * 60)
    print("  🐂 牛头王 · 人机对战")
    print("  您将作为第一个玩家（🧑 You）与其他 5 个 AI 对战")
    print("  输入手牌前的数字编号来出牌，按 Enter 确认")
    if save_path:
        print(f"  💾 轨迹将保存至: {save_path}")
    else:
        print(f"  💾 轨迹将自动保存至: {_DEFAULT_TRAJ_DIR}/ 目录")
    print("=" * 60)

    for rnd in range(1, HAND_SIZE + 1):
        print(f"\n--- 第 {rnd} 轮 ---")
        print("  桌面：")
        for r, row in enumerate(rows):
            tail = row[-1] if row else "空"
            bulls = row_bulls(row)
            print(f"    列 {r+1}: {row}  (尾牌 {tail}, 牛头 {bulls})")
        print(f"  当前分数: {[f'{PLAYER_NAMES_HUMAN[i]}={scores[i]}' for i in range(NUM_PLAYERS)]}")

        hand = hands[human_idx]
        print(f"\n  🧑 您的手牌: {hand}")
        print("  请选择一张牌的编号 (0~{}):".format(len(hand)-1))
        while True:
            try:
                choice = input("  >>> ").strip()
                if not choice:
                    continue
                idx = int(choice)
                if 0 <= idx < len(hand):
                    human_card = hand[idx]
                    break
                else:
                    print("  编号超出范围，请重新输入")
            except ValueError:
                print("  请输入有效整数")

        chosen = [None] * NUM_PLAYERS
        chosen[human_idx] = human_card
        for i in range(1, NUM_PLAYERS):
            chosen[i] = ai_choose_card(hands[i], rows, AI_STRATEGIES[i-1])
        hands_before = [hand.copy() for hand in hands]

        for i in range(NUM_PLAYERS):
            hands[i].remove(chosen[i])

        order = sorted(range(NUM_PLAYERS), key=lambda i: chosen[i])
        round_plays = []
        for pi in order:
            card = chosen[pi]
            br = find_best_row(rows, card)
            if br == -1:
                r = ai_choose_row(rows)
                pen = place_card(rows, card, r)
            else:
                pen = place_card(rows, card)
            scores[pi] += pen

            if pi == human_idx:
                print(f"  🧑 您出 {card}，罚 {pen} 牛头")
            else:
                print(f"  {PLAYER_NAMES_HUMAN[pi]} 出 {card}，罚 {pen} 牛头")

            play_info = {
                "player_id": players_config[pi]['id'],
                "card_played": card,
                "hand_before": hands_before[pi],
                "penalty": pen,
                "row_affected": r if br == -1 else br,
            }
            # 人类玩家记录 action_idx（出牌在 hand_before 中的下标，用于行为克隆）
            if pi == human_idx:
                try:
                    play_info["action_idx"] = hands_before[pi].index(card)
                except ValueError:
                    play_info["action_idx"] = -1
            round_plays.append(play_info)

        round_record = {
            "round": rnd,
            "plays": round_plays,
            "scores_after": scores.copy()
        }
        game_record["rounds"].append(round_record)

        if max(scores) >= END_SCORE or all(len(h) == 0 for h in hands):
            break

    ranking = sorted(range(NUM_PLAYERS), key=lambda i: scores[i])
    print("\n  🏆 最终排名：")
    medals = ["🥇","🥈","🥉","4️⃣ ","5️⃣ ","6️⃣ "]
    for rank, pi in enumerate(ranking):
        print(f"  {medals[rank]} {PLAYER_NAMES_HUMAN[pi]:10s}: {scores[pi]}🐂")

    if save_path is None:
        filename = f"human_traj_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        save_path = os.path.join(_DEFAULT_TRAJ_DIR, filename)
    game_record["final_scores"] = scores
    game_record["ranking"] = ranking
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(game_record, f, indent=2, ensure_ascii=False)
    print(f"\n💾 本局轨迹已保存至: {save_path}")

# =========================================================
#  行为克隆训练（从人机对战日志）
# =========================================================
def _reconstruct_rows_at_round(game, target_round_idx):
    """
    从 initial_rows 和历史 plays 重建第 target_round_idx 轮出牌 *前* 的棋盘状态。
    小程序上报的 traj 没有存每轮的 rows，需要从头模拟推算。
    """
    rows = [list(r) for r in game.get('initial_rows', [])]
    if not rows:
        # 没有 initial_rows，无法重建，返回空
        return rows
    for ri, rdata in enumerate(game.get('rounds', [])):
        if ri >= target_round_idx:
            break
        for play in rdata.get('plays', []):
            card = play.get('card_played')
            row_idx = play.get('row_affected', -1)
            if card is None or row_idx < 0:
                continue
            if row_idx < len(rows):
                # 判断是收走（penalty>0 且是收走操作）还是追加
                penalty = play.get('penalty', 0)
                if penalty > 0 or len(rows[row_idx]) >= MAX_ROW:
                    rows[row_idx] = [card]
                else:
                    rows[row_idx].append(card)
    return rows


def _extract_samples_from_game(game):
    """
    从单局 game dict 提取 (state_vec, action_idx) 样本列表。
    统一格式（小程序 / human 命令）：play 含 hand_before + action_idx，
    state 向量在此处从 hand_before + 重建的 rows + 历史得分 动态计算。
    """
    samples = []
    # 找人类玩家 id
    human_ids = set()
    for p in game.get('players', []):
        if p.get('type') == 'human':
            human_ids.add(p.get('id', 'human'))
    if not human_ids:
        human_ids = {'human'}

    # 玩家 id 列表，用于从 scores_after 定位得分
    player_ids = [p.get('id') for p in game.get('players', [])]

    for ri, rdata in enumerate(game.get('rounds', [])):
        for play in rdata.get('plays', []):
            pid = play.get('player_id', '')
            if pid not in human_ids:
                continue
            action_idx = play.get('action_idx', -1)
            if action_idx < 0:
                continue
            hand_before = play.get('hand_before')
            if not hand_before:
                continue

            # 重建该轮出牌前的棋盘
            rows = _reconstruct_rows_at_round(game, ri)
            if not rows:
                continue

            # 取上一轮结束时的得分
            if ri == 0:
                my_score = 0
            else:
                prev_scores = game['rounds'][ri - 1].get('scores_after', [])
                hi = player_ids.index(pid) if pid in player_ids else 0
                my_score = prev_scores[hi] if hi < len(prev_scores) else 0

            state_vec = encode_state(hand_before, rows, my_score)
            samples.append((state_vec.tolist(), action_idx))

    return samples


def train_behavior_clone(json_paths, epochs=50, batch_size=64, lr=1e-3):
    """
    从人类轨迹 JSON 文件中提取 state-action 对，训练行为克隆模型。
    统一使用 hand_before + action_idx 格式（小程序上报 / human 命令生成均可）。
    外层结构支持：
      {"meta":..., "trajectories":[...]}  ← 小程序管理台下载包
      [game1, game2, ...]                 ← 列表
      {game dict}                         ← 单局
    json_paths 可以是单个路径字符串，或多个路径的列表，支持 glob（*.json）。
    """
    # 展平路径列表
    if isinstance(json_paths, str):
        json_paths = [json_paths]
    flat_paths = []
    for item in json_paths:
        if isinstance(item, list):
            flat_paths.extend(item)
        else:
            flat_paths.append(item)
    json_paths = flat_paths

    states = []
    actions = []

    for json_path in json_paths:
        if not os.path.isfile(json_path):
            print(f"⚠️  文件不存在，跳过: {json_path}")
            continue
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 支持三种顶层结构：
        #   1. {"meta":..., "trajectories": [...]}  ← 小程序下载包
        #   2. [game1, game2, ...]                  ← 旧格式列表
        #   3. {game dict}                          ← 单局
        if isinstance(data, dict) and 'trajectories' in data:
            games = data['trajectories']
        elif isinstance(data, list):
            games = data
        else:
            games = [data]

        for game in games:
            for sv, ai in _extract_samples_from_game(game):
                states.append(sv)
                actions.append(ai)

    if not states:
        print("⚠️  未找到有效样本（请确保日志包含 'state'/'hand_before' 和 'action_idx'）")
        return None

    n_samples = len(states)
    print(f"📊 共收集到 {n_samples} 条人类样本")

    X = torch.FloatTensor(np.array(states))
    y = torch.LongTensor(actions)

    # 数据量少时自动调整：减少 batch_size、加 Dropout 防过拟合
    actual_batch = min(batch_size, max(8, n_samples // 4))
    dropout_rate = 0.3 if n_samples < 100 else 0.1
    # early stop：当数据量少时，避免过拟合，损失低于阈值即停止
    early_stop_loss = 0.3 if n_samples < 50 else 0.2

    print(f"   batch_size={actual_batch}, dropout={dropout_rate}, early_stop_loss={early_stop_loss}")

    dataset = TensorDataset(X, y)
    dataloader = DataLoader(dataset, batch_size=actual_batch, shuffle=True)

    # 行为克隆专用：带 Dropout 防过拟合，weight_decay 加 L2 正则
    model = ActorCritic(dropout=dropout_rate)
    params = list(model.backbone.parameters()) + list(model.actor_head.parameters())
    optimizer = optim.Adam(params, lr=lr, weight_decay=1e-3)
    criterion = nn.CrossEntropyLoss()

    model.train()
    for epoch in range(epochs):
        total_loss = 0
        for X_batch, y_batch in dataloader:
            optimizer.zero_grad()
            logits, _ = model(X_batch)
            loss = criterion(logits, y_batch)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        avg_loss = total_loss / len(dataloader)
        if (epoch+1) % 10 == 0:
            print(f"Epoch {epoch+1}/{epochs}, Loss: {avg_loss:.4f}")
        # early stopping：loss 已经足够低，继续训练只会过拟合
        if avg_loss < early_stop_loss:
            print(f"⏹  Early stop at epoch {epoch+1}, Loss={avg_loss:.4f} < {early_stop_loss}")
            break

    model_save_prefix = os.path.join(_MODELS_DIR, "bc")
    backbone_path = model_save_prefix + "_backbone.pth"
    actor_path = model_save_prefix + "_actor.pth"
    torch.save(model.backbone.state_dict(), backbone_path)
    torch.save(model.actor_head.state_dict(), actor_path)
    print(f"✅ 行为克隆模型已保存: {backbone_path} 和 {actor_path}")
    return model

# =========================================================
#  主入口
# =========================================================
if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 1 and args[0] == "train":
        from_scratch = "--from-scratch" in args
        load_bc = "--load-bc" in args
        train(from_scratch=from_scratch, load_bc=load_bc)
    elif len(args) >= 1 and args[0] == "play":
        save_path = None
        if len(args) >= 3 and args[1] == "--save":
            save_path = args[2]
        agent = PPOAgent()
        if os.path.exists(MODEL_PATH):
            agent.load(MODEL_PATH)
        play(agent, save_path)
    elif len(args) >= 1 and args[0] == "collect":
        num = int(args[1]) if len(args) > 1 else 100
        path = args[2] if len(args) > 2 else "trajectories.json"
        collect_games_to_json(num, path)
    elif len(args) >= 1 and args[0] == "offline":
        path = args[1] if len(args) > 1 else "trajectories.json"
        model_path = args[2] if len(args) > 2 else MODEL_PATH
        train_from_json(path, model_path)
    elif len(args) >= 1 and args[0] == "human":
        save_path = args[1] if len(args) > 1 else None
        agent = PPOAgent()
        if os.path.exists(MODEL_PATH):
            agent.load(MODEL_PATH)
        human_play(agent, save_path)
    elif len(args) >= 1 and args[0] == "behavior_clone":
        # 解析参数：文件路径（多个），最后可选一个数字作为 epochs
        paths = []
        epochs = 50
        for arg in args[1:]:
            if arg.isdigit():
                epochs = int(arg)
            else:
                paths.append(arg)
        if not paths:
            print("用法: python nimmt_ppo.py behavior_clone <file1.json> [file2.json ...] [epochs]")
            sys.exit(1)
        train_behavior_clone(paths, epochs=epochs)
    else:
        # 默认：训练 + 对战
        agent = train()
        play(agent)
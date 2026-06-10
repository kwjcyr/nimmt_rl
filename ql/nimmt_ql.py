"""
牛头王 (6 Nimmt!) Q-Learning 强化学习

算法：表格型 Q-Learning（状态离散化 + ε-greedy 探索）

状态设计（轻量离散化）：
  - 手牌中各张牌相对牌桌的"位置风险"概况
  - 当前最危险的列（剩余格数最少）
  - 自身得分段

动作：从手牌中选第几张（0~9）

奖励：
  - 本轮获得牛头数取负（越少越好）
  - 游戏结束排名奖励

用法：
  python3 nimmt_ql.py train      # 训练并保存 Q 表
  python3 nimmt_ql.py play       # 用训练好的策略对战
  python3 nimmt_ql.py            # 先训练再对战
"""

import os
import pickle
import random
import sys
from collections import defaultdict

# =========================================================
#  游戏常量
# =========================================================
TOTAL_CARDS = 100
NUM_ROWS    = 5
MAX_ROW     = 6       # 每列最多6张，第7张触发收牌
HAND_SIZE   = 10
END_SCORE   = 66
NUM_PLAYERS = 6

_DIR        = os.path.dirname(os.path.abspath(__file__))
_MODELS_DIR = os.path.join(_DIR, "..", "models")
MODEL_PATH  = os.path.join(_MODELS_DIR, "nimmt_q_table.pkl")


def get_bulls(card: int) -> int:
    if card == 55: return 7
    if card % 11 == 0: return 5
    if card % 10 == 0: return 3
    if card % 5 == 0: return 2
    return 1


def row_bulls(row):
    return sum(get_bulls(c) for c in row)


# =========================================================
#  游戏核心
# =========================================================
def shuffle_deal():
    deck = list(range(1, TOTAL_CARDS + 1))
    random.shuffle(deck)
    hands = [sorted(deck[i*HAND_SIZE:(i+1)*HAND_SIZE]) for i in range(NUM_PLAYERS)]
    start = NUM_PLAYERS * HAND_SIZE
    rows  = [[c] for c in sorted(deck[start:start+NUM_ROWS])]
    return hands, rows


def find_best_row(rows, card):
    """返回最合适的列索引，-1表示牌比所有末尾都小"""
    best, min_diff = -1, float('inf')
    for r, row in enumerate(rows):
        tail = row[-1]
        if tail < card and (card - tail) < min_diff:
            min_diff = card - tail
            best = r
    return best


def place_card(rows, card, chosen_row=None):
    """放牌，返回获得的牛头数罚分"""
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


# =========================================================
#  状态离散化
#  轻量、有意义的特征，避免状态爆炸
# =========================================================
def encode_state(hand, rows, my_score):
    """
    状态编码 → 离散 tuple（作为 Q 表的 key）

    特征：
    1. 手牌数量（1~10）
    2. 手牌中"安全牌"数量（放入后不触发收牌）
    3. 手牌中"危险牌"数量（放入后列满或牌最小）
    4. 最危险列的剩余格数（1=快满了）
    5. 全场最小末尾值 / 10 离散化（0~9）
    6. 我的得分段（0~5）
    """
    safe, danger = 0, 0
    for card in hand:
        br = find_best_row(rows, card)
        if br == -1:
            danger += 1
        elif len(rows[br]) >= MAX_ROW:
            danger += 1
        else:
            safe += 1

    # 最危险列（剩余格数最少）
    min_remaining = min(MAX_ROW - len(r) for r in rows)

    # 全场最小末尾值
    min_tail = min(r[-1] for r in rows)
    min_tail_bin = min(min_tail // 10, 9)

    # 得分段
    score_bin = min(my_score // 11, 5)  # 0-10, 11-21, ...

    return (len(hand), safe, danger, min_remaining, min_tail_bin, score_bin)


def encode_card_action(hand, rows, card_idx):
    """
    动作特征：对第 card_idx 张牌的"局部特征"
    返回离散 tuple，拼到状态后组成完整 Q-key
    """
    if card_idx >= len(hand):
        return (0, 0, 0)  # 无效动作占位

    card = hand[card_idx]
    br = find_best_row(rows, card)

    if br == -1:
        # 必须收列，选最小罚分
        min_pen = min(row_bulls(r) for r in rows)
        risk = min(min_pen, 7)
        slot = 0
    elif len(rows[br]) >= MAX_ROW:
        risk = min(row_bulls(rows[br]), 7)
        slot = 0
    else:
        risk = 0
        slot = MAX_ROW - len(rows[br])  # 剩余格数

    # 牌面分段
    card_bin = (card - 1) // 10  # 0~9

    return (risk, slot, card_bin)


# =========================================================
#  规则 AI（对手）
# =========================================================
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

        noise = random.uniform(-5, 5)
        scored.append((card, risk + noise))

    if strategy == "safe":
        scored.sort(key=lambda x: x[1])
        return scored[0][0]
    else:  # greedy + 偶尔赌
        scored.sort(key=lambda x: x[1])
        r = random.random()
        if r < 0.70:
            pool = scored[:max(1, len(scored)//3)]
        elif r < 0.90:
            mid = len(scored)//2
            pool = scored[max(0,mid-1):mid+2]
        else:
            return hand[-1]  # 出最大牌
        return random.choice(pool)[0]


def ai_choose_row(rows):
    return min(range(NUM_ROWS), key=lambda r: row_bulls(rows[r]))


# =========================================================
#  Q-Learning Agent
# =========================================================
class QLearningAgent:
    def __init__(self, alpha=0.1, gamma=0.95, epsilon=1.0, epsilon_min=0.05, epsilon_decay=0.9995):
        self.alpha   = alpha          # 学习率
        self.gamma   = gamma          # 折扣因子
        self.epsilon = epsilon        # 初始探索率
        self.epsilon_min = epsilon_min
        self.epsilon_decay = epsilon_decay

        # Q 表：defaultdict → Q(s,a) 默认为 0
        self.q_table = defaultdict(float)

    def _key(self, state, action_feat):
        return state + action_feat

    def get_action(self, hand, rows, my_score, training=True):
        """ε-greedy 选牌"""
        state = encode_state(hand, rows, my_score)
        n = len(hand)

        if training and random.random() < self.epsilon:
            return random.randint(0, n - 1)

        # 选 Q 值最大的动作
        best_idx, best_q = 0, float('-inf')
        for i in range(n):
            feat = encode_card_action(hand, rows, i)
            q = self.q_table[self._key(state, feat)]
            if q > best_q:
                best_q = q
                best_idx = i
        return best_idx

    def update(self, hand, rows, my_score, action_idx,
               reward, next_hand, next_rows, next_score, done):
        """Q-Learning 更新"""
        state      = encode_state(hand, rows, my_score)
        action_feat = encode_card_action(hand, rows, action_idx)
        key = self._key(state, action_feat)

        if done:
            target = reward
        else:
            # max Q(s', a')
            next_state = encode_state(next_hand, next_rows, next_score)
            max_next_q = max(
                self.q_table[self._key(next_state, encode_card_action(next_hand, next_rows, i))]
                for i in range(len(next_hand))
            ) if next_hand else 0.0
            target = reward + self.gamma * max_next_q

        # 更新
        self.q_table[key] += self.alpha * (target - self.q_table[key])

    def decay_epsilon(self):
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

    def save(self, path=MODEL_PATH):
        with open(path, 'wb') as f:
            pickle.dump(dict(self.q_table), f)
        print(f"💾 Q 表已保存: {path}（共 {len(self.q_table)} 个状态-动作对）")

    def load(self, path=MODEL_PATH):
        with open(path, 'rb') as f:
            data = pickle.load(f)
        self.q_table = defaultdict(float, data)
        self.epsilon = self.epsilon_min  # 推理时不再探索
        print(f"📂 Q 表已加载: {path}（共 {len(self.q_table)} 个状态-动作对）")


# =========================================================
#  训练
# =========================================================
AI_STRATEGIES = ["greedy", "safe", "greedy", "random", "safe"]


def run_episode(agent, training=True):
    """跑一局游戏，返回 agent 的总罚分和最终排名"""
    hands, rows = shuffle_deal()
    scores = [0] * NUM_PLAYERS

    for _ in range(HAND_SIZE):
        hand_before = hands[0][:]
        rows_before  = [r[:] for r in rows]
        score_before = scores[0]

        # Agent 选牌
        action_idx = agent.get_action(hands[0], rows, scores[0], training=training)
        chosen = [None] * NUM_PLAYERS
        chosen[0] = hands[0][action_idx]

        # AI 选牌
        for i in range(1, NUM_PLAYERS):
            chosen[i] = ai_choose_card(hands[i], rows, AI_STRATEGIES[i-1])

        # 从手牌移除
        for i in range(NUM_PLAYERS):
            hands[i].remove(chosen[i])

        # 按牌面从小到大放牌
        order = sorted(range(NUM_PLAYERS), key=lambda i: chosen[i])
        round_penalty = [0] * NUM_PLAYERS

        for pi in order:
            card = chosen[pi]
            br = find_best_row(rows, card)
            if br == -1:
                r = 0 if pi != 0 else ai_choose_row(rows)  # 简化：AI和agent都选最小罚分列
                if pi != 0:
                    r = ai_choose_row(rows)
                penalty = place_card(rows, card, r)
            else:
                penalty = place_card(rows, card)
            round_penalty[pi] = penalty
            scores[pi] += penalty

        # Q-Learning 更新（只更新 agent）
        reward = -round_penalty[0]  # 牛头越少越好
        done = (len(hands[0]) == 0) or (max(scores) >= END_SCORE)

        if training:
            agent.update(
                hand_before, rows_before, score_before,
                action_idx, reward,
                hands[0], rows, scores[0],
                done
            )

        if done:
            break

    # 游戏结束排名奖励
    ranking = sorted(range(NUM_PLAYERS), key=lambda i: scores[i])
    agent_rank = ranking.index(0)  # 0=第1名(最好)

    if training:
        rank_reward = [+5, +3, +1, -1, -3, -5][agent_rank]
        # 终局奖励更新最后一步
        agent.update(
            hands[0], rows, scores[0],
            0, rank_reward, [], rows, scores[0], True
        )

    agent.decay_epsilon()
    return scores[0], agent_rank


def train(episodes=30000):
    agent = QLearningAgent()
    print("=" * 55)
    print("🏋️  牛头王 Q-Learning 训练")
    print(f"   局数: {episodes}  |  学习率: {agent.alpha}  |  折扣: {agent.gamma}")
    print("=" * 55)

    stats = {"penalties": [], "ranks": []}

    for ep in range(1, episodes + 1):
        penalty, rank = run_episode(agent, training=True)
        stats["penalties"].append(penalty)
        stats["ranks"].append(rank)

        if ep % 2000 == 0:
            recent_pen  = sum(stats["penalties"][-2000:]) / 2000
            recent_rank = sum(stats["ranks"][-2000:]) / 2000 + 1
            win_rate    = stats["ranks"][-2000:].count(0) / 2000 * 100
            print(f"  Ep {ep:6d} | ε={agent.epsilon:.3f} | "
                  f"平均罚分={recent_pen:.1f}🐂 | 平均排名={recent_rank:.2f} | "
                  f"胜率={win_rate:.1f}% | Q表={len(agent.q_table)}条")

    agent.save(MODEL_PATH)
    print("\n✅ 训练完成！")
    return agent


# =========================================================
#  对战（命令行）
# =========================================================
PLAYER_NAMES = ["你(RL)", "AI-甲", "AI-乙", "AI-丙", "AI-丁", "AI-戊"]


def play(agent=None):
    if agent is None:
        agent = QLearningAgent()
        if os.path.exists(MODEL_PATH):
            agent.load(MODEL_PATH)
        else:
            print("⚠️  未找到训练模型，使用随机策略")

    hands, rows = shuffle_deal()
    scores = [0] * NUM_PLAYERS

    print("\n" + "=" * 60)
    print("  🐂 牛头王对战（RL Agent vs 5 AI）")
    print("=" * 60)

    def show_rows():
        print("\n  📋 牌桌：")
        for r, row in enumerate(rows):
            cards = "  ".join(f"{c}({'🐂'*get_bulls(c)})" for c in row)
            slots = f"[{len(row)}/{MAX_ROW}]"
            print(f"  列{r+1} {slots}: {cards}")

    def show_scores():
        print("\n  📊 得分：")
        for i, (name, s) in enumerate(zip(PLAYER_NAMES, scores)):
            bar = "▓" * min(s, 20)
            print(f"    {name:10s}: {s:3d}🐂  {bar}")

    show_rows()

    for rnd in range(1, HAND_SIZE + 1):
        print(f"\n{'─'*60}")
        print(f"  第 {rnd} 轮")
        print(f"{'─'*60}")

        # Agent 选牌（不探索）
        action_idx = agent.get_action(hands[0], rows, scores[0], training=False)
        chosen = [None] * NUM_PLAYERS
        chosen[0] = hands[0][action_idx]
        for i in range(1, NUM_PLAYERS):
            chosen[i] = ai_choose_card(hands[i], rows, AI_STRATEGIES[i-1])

        for i in range(NUM_PLAYERS):
            hands[i].remove(chosen[i])

        print(f"  RL Agent 出牌: {chosen[0]} ({'🐂'*get_bulls(chosen[0])})")
        print(f"  AI 出牌: {[chosen[i] for i in range(1, NUM_PLAYERS)]}")

        order = sorted(range(NUM_PLAYERS), key=lambda i: chosen[i])
        for pi in order:
            card = chosen[pi]
            br = find_best_row(rows, card)
            if br == -1:
                r = ai_choose_row(rows)
                penalty = place_card(rows, card, r)
                print(f"  {PLAYER_NAMES[pi]}: {card} → 收走第{r+1}列 💥-{penalty}🐂")
            elif len(rows[br]) >= MAX_ROW:
                penalty = place_card(rows, card)
                print(f"  {PLAYER_NAMES[pi]}: {card} → 列满收走 💥-{penalty}🐂")
            else:
                penalty = place_card(rows, card)
                if penalty == 0:
                    pass  # 静默放入
            scores[pi] += penalty

        show_rows()

        if max(scores) >= END_SCORE:
            print(f"\n  ⚠️  有人超过 {END_SCORE} 分，提前结束！")
            break

    # 结果
    print("\n" + "=" * 60)
    ranking = sorted(range(NUM_PLAYERS), key=lambda i: scores[i])
    print("  🏆 最终排名：")
    medals = ["🥇","🥈","🥉","4️⃣ ","5️⃣ ","6️⃣ "]
    for rank, pi in enumerate(ranking):
        print(f"  {medals[rank]}  {PLAYER_NAMES[pi]:10s}: {scores[pi]}🐂")
    agent_rank = ranking.index(0) + 1
    print(f"\n  RL Agent 排名第 {agent_rank} 名")
    print("=" * 60)


# =========================================================
#  入口
# =========================================================
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"

    if mode == "train":
        train(episodes=30000)
    elif mode == "play":
        play()
    else:
        # 先训练，再跑一局展示
        agent = train(episodes=30000)
        print("\n\n--- 用训练好的策略跑一局展示 ---")
        play(agent)


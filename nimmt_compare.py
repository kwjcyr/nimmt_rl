"""
牛头王三种 RL 策略对比

  python3 nimmt_compare.py

流程：
  1. 分别训练 Q-Learning、DQN、PPO（可调整各自训练局数）
  2. 用训练好的策略各跑 N 局评估
  3. 汇总输出对比表格（胜率、平均罚分、平均排名）
"""

import os
import random
import sys
import time

# 把当前目录加入 PATH，方便 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# =========================================================
#  共享游戏逻辑（与三个 RL 文件保持一致）
# =========================================================
TOTAL_CARDS  = 100
NUM_ROWS     = 5
MAX_ROW      = 6
HAND_SIZE    = 10
END_SCORE    = 66
NUM_PLAYERS  = 6
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


def run_one_game(get_action_fn):
    """
    get_action_fn(hand, rows, score) -> card_index
    返回 (my_score, rank_0based)
    """
    hands, rows = shuffle_deal()
    scores = [0] * NUM_PLAYERS

    for _ in range(HAND_SIZE):
        action_idx = get_action_fn(hands[0], rows, scores[0])
        chosen = [None] * NUM_PLAYERS
        chosen[0] = hands[0][action_idx]
        for i in range(1, NUM_PLAYERS):
            chosen[i] = ai_choose_card(hands[i], rows, AI_STRATEGIES[i-1])
        for i in range(NUM_PLAYERS):
            hands[i].remove(chosen[i])

        order = sorted(range(NUM_PLAYERS), key=lambda i: chosen[i])
        for pi in order:
            card = chosen[pi]
            br = find_best_row(rows, card)
            if br == -1:
                r = ai_choose_row(rows)
                pen = place_card(rows, card, r)
            else:
                pen = place_card(rows, card)
            scores[pi] += pen

        if max(scores) >= END_SCORE or len(hands[0]) == 0:
            break

    ranking = sorted(range(NUM_PLAYERS), key=lambda i: scores[i])
    return scores[0], ranking.index(0)


def evaluate(get_action_fn, n_games=3000):
    penalties, ranks = [], []
    for _ in range(n_games):
        pen, rank = run_one_game(get_action_fn)
        penalties.append(pen)
        ranks.append(rank)
    win_rate  = ranks.count(0) / n_games * 100
    avg_pen   = sum(penalties) / n_games
    avg_rank  = sum(ranks) / n_games + 1
    top3_rate = sum(1 for r in ranks if r < 3) / n_games * 100
    return dict(win_rate=win_rate, avg_pen=avg_pen,
                avg_rank=avg_rank, top3_rate=top3_rate)


# =========================================================
#  随机 baseline
# =========================================================
def eval_random(eval_eps=3000):
    def rand_action(hand, rows, score):
        return random.randint(0, len(hand) - 1)
    return evaluate(rand_action, eval_eps)


# =========================================================
#  Q-Learning（直接调用 nimmt_ql.run_episode）
# =========================================================
def train_and_eval_ql(train_eps=30000, eval_eps=3000):
    import importlib
    ql = importlib.import_module("nimmt_ql")

    agent = ql.QLearningAgent()
    print(f"\n[Q-Learning] 训练 {train_eps} 局...")
    t0 = time.time()

    for _ in range(train_eps):
        ql.run_episode(agent, training=True)

    print(f"   训练耗时: {time.time()-t0:.1f}s | Q表大小: {len(agent.q_table)}")

    def ql_action(hand, rows, score):
        return agent.get_action(hand, rows, score, training=False)

    print(f"   评估 {eval_eps} 局...")
    result = evaluate(ql_action, eval_eps)
    return result


# =========================================================
#  DQN（直接调用 nimmt_dqn.run_episode）
# =========================================================
def train_and_eval_dqn(train_eps=30000, eval_eps=3000):
    import importlib
    dqn = importlib.import_module("nimmt_dqn")

    agent = dqn.DQNAgent()
    print(f"\n[DQN] 训练 {train_eps} 局...")
    t0 = time.time()

    for _ in range(train_eps):
        dqn.run_episode(agent, training=True)

    print(f"   训练耗时: {time.time()-t0:.1f}s")

    def dqn_action(hand, rows, score):
        return agent.get_action(hand, rows, score, training=False)

    print(f"   评估 {eval_eps} 局...")
    result = evaluate(dqn_action, eval_eps)
    return result


# =========================================================
#  PPO（直接调用 nimmt_ppo.run_episode）
# =========================================================
def train_and_eval_ppo(train_eps=30000, eval_eps=3000):
    import importlib
    ppo = importlib.import_module("nimmt_ppo")

    agent = ppo.PPOAgent()
    print(f"\n[PPO] 训练 {train_eps} 局...")
    t0 = time.time()
    update_every = 10

    for ep in range(1, train_eps + 1):
        ppo.run_episode(agent, training=True)
        if ep % update_every == 0:
            agent.update()

    print(f"   训练耗时: {time.time()-t0:.1f}s")

    def ppo_action(hand, rows, score):
        return agent.get_action_eval(hand, rows, score)

    print(f"   评估 {eval_eps} 局...")
    result = evaluate(ppo_action, eval_eps)
    return result


# =========================================================
#  打印对比表格
# =========================================================
def print_table(results):
    print("\n" + "=" * 75)
    print("  📊 牛头王强化学习策略对比 (6 人局，5 AI + RL 玩家)")
    print("=" * 75)
    print(f"  {'策略':<14} {'胜率':>8} {'前3率':>8} {'平均排名':>10} {'平均罚分':>10}")
    print("-" * 75)
    sorted_r = sorted(results.items(), key=lambda x: -x[1]['win_rate'])
    for name, r in sorted_r:
        bar = "█" * int(r['win_rate'] / 2)
        print(f"  {name:<14} {r['win_rate']:>7.1f}% {r['top3_rate']:>7.1f}% "
              f"{r['avg_rank']:>10.2f} {r['avg_pen']:>10.1f}🐂  {bar}")
    print("-" * 75)
    winner = max(results, key=lambda k: results[k]['win_rate'])
    print(f"\n  🏆 最优策略: {winner}  胜率 {results[winner]['win_rate']:.1f}%")
    print("  ⚠️  随机基准胜率理论值 ≈ 16.7% (均匀分布)")
    print("=" * 75)


# =========================================================
#  主入口
# =========================================================
TRAIN_EPS = 30000   # 可调大
EVAL_EPS  = 3000

if __name__ == "__main__":
    print("🐂 牛头王 RL 三算法对比")
    print(f"   每种算法训练 {TRAIN_EPS} 局，评估 {EVAL_EPS} 局\n")

    results = {}

    # 随机 baseline
    print("[Random] 评估中...")
    results["随机策略"] = eval_random(EVAL_EPS)
    r = results["随机策略"]
    print(f"   胜率={r['win_rate']:.1f}%  平均罚分={r['avg_pen']:.1f}")

    # Q-Learning
    results["Q-Learning"] = train_and_eval_ql(TRAIN_EPS, EVAL_EPS)
    r = results["Q-Learning"]
    print(f"   胜率={r['win_rate']:.1f}%  平均罚分={r['avg_pen']:.1f}")

    # DQN
    results["DQN"] = train_and_eval_dqn(TRAIN_EPS, EVAL_EPS)
    r = results["DQN"]
    print(f"   胜率={r['win_rate']:.1f}%  平均罚分={r['avg_pen']:.1f}")

    # PPO
    results["PPO"] = train_and_eval_ppo(TRAIN_EPS, EVAL_EPS)
    r = results["PPO"]
    print(f"   胜率={r['win_rate']:.1f}%  平均罚分={r['avg_pen']:.1f}")

    print_table(results)


"""
牛头王 Arena 对战版
==============================
玩家配置（6人局）：
  玩家0  真人（Human）         ← 你
  玩家1  RL-PPO               ← 训练好的 PPO 模型
  玩家2  RL-DQN               ← 训练好的 DQN 模型
  玩家3  RL-QLearning         ← 训练好的 Q 表
  玩家4  AI-规则（greedy）
  玩家5  AI-规则（safe）

用法：
  python3 nimmt_arena.py          # 直接对战（需要事先训练过模型）
  python3 nimmt_arena.py train    # 先训练三种模型再对战

如果模型文件不存在，对应 RL 玩家会自动降级为规则 AI。
"""

import os
import random
import sys

# =========================================================
#  路径
# =========================================================
_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

# =========================================================
#  游戏常量
# =========================================================
TOTAL_CARDS = 100
NUM_ROWS    = 5
MAX_ROW     = 6
HAND_SIZE   = 10
END_SCORE   = 66
NUM_PLAYERS = 6


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
        pool = scored[max(0, mid-1):mid+2]
    else:
        return hand[-1]
    return random.choice(pool)[0]

def ai_choose_row(rows):
    return min(range(NUM_ROWS), key=lambda r: row_bulls(rows[r]))


# =========================================================
#  加载 RL 模型（失败则降级为规则 AI）
# =========================================================
def load_ppo():
    try:
        import nimmt_ppo as ppo_mod
        agent = ppo_mod.PPOAgent()
        path = os.path.join(_DIR, "nimmt_ppo_model.pth")
        if os.path.exists(path):
            agent.load(path)
            print("  ✅ PPO 模型加载成功")
            return lambda hand, rows, score: agent.get_action_eval(hand, rows, score)
        else:
            print("  ⚠️  PPO 模型文件不存在，降级为规则 AI")
    except Exception as e:
        print(f"  ⚠️  PPO 加载失败（{e}），降级为规则 AI")
    return lambda hand, rows, score: hand.index(ai_choose_card(hand, rows, "greedy"))


def load_dqn():
    try:
        import nimmt_dqn as dqn_mod
        agent = dqn_mod.DQNAgent()
        path = os.path.join(_DIR, "nimmt_dqn_model.pth")
        if os.path.exists(path):
            agent.load(path)
            print("  ✅ DQN 模型加载成功")
            return lambda hand, rows, score: agent.get_action(hand, rows, score, training=False)
        else:
            print("  ⚠️  DQN 模型文件不存在，降级为规则 AI")
    except Exception as e:
        print(f"  ⚠️  DQN 加载失败（{e}），降级为规则 AI")
    return lambda hand, rows, score: hand.index(ai_choose_card(hand, rows, "greedy"))


def load_ql():
    try:
        import nimmt_ql as ql_mod
        agent = ql_mod.QLearningAgent()
        path = os.path.join(_DIR, "nimmt_q_table.pkl")
        if os.path.exists(path):
            agent.load(path)
            print("  ✅ Q-Learning 模型加载成功")
            return lambda hand, rows, score: agent.get_action(hand, rows, score, training=False)
        else:
            print("  ⚠️  Q 表文件不存在，降级为规则 AI")
    except Exception as e:
        print(f"  ⚠️  Q-Learning 加载失败（{e}），降级为规则 AI")
    return lambda hand, rows, score: hand.index(ai_choose_card(hand, rows, "safe"))


# =========================================================
#  显示工具
# =========================================================
MEDALS = ["🥇", "🥈", "🥉", "4️⃣ ", "5️⃣ ", "6️⃣ "]
BULL_ICONS = {1:"🐂", 2:"🐂🐂", 3:"🐂🐂🐂", 5:"🐂×5", 7:"🐂×7"}

def bull_str(card):
    b = get_bulls(card)
    return BULL_ICONS.get(b, f"🐂×{b}")

def display_board(rows, scores, names):
    print("\n  ┌─── 牌桌 ──────────────────────────────────────────")
    for i, row in enumerate(rows):
        bulls = row_bulls(row)
        cards_str = "  ".join(f"{c:3d}" for c in row)
        slots = "[ ]" * (MAX_ROW - len(row))
        print(f"  │ 列{i+1}[{len(row)}/{MAX_ROW}] {cards_str}  {slots}  共{bulls}🐂")
    print("  └────────────────────────────────────────────────")
    print("  📊 得分：" + "  ".join(f"{names[i]}:{scores[i]}🐂" for i in range(NUM_PLAYERS)))

def display_hand(hand, name="你"):
    print(f"\n  🃏 {name} 的手牌：")
    for i, card in enumerate(hand):
        b = get_bulls(card)
        print(f"     [{i+1}] {card:3d}  {bull_str(card)}")


# =========================================================
#  人类选牌
# =========================================================
def human_choose_card(hand, rows, scores, name="你"):
    display_hand(hand, name)
    while True:
        try:
            inp = input(f"\n  请出牌 (输入序号 1~{len(hand)})：").strip()
            idx = int(inp) - 1
            if 0 <= idx < len(hand):
                return idx
            print(f"  ❌ 请输入 1~{len(hand)} 之间的数字")
        except (ValueError, EOFError):
            print("  ❌ 无效输入，请重试")


def human_choose_row(rows, card, name="你"):
    """当出的牌比所有列末尾都小时，手动选择收哪列"""
    print(f"\n  ⚠️  你出的牌 {card} 比所有列末尾都小，必须选一列收走！")
    for i, row in enumerate(rows):
        print(f"     [{i+1}] 列{i+1}：{row}  共{row_bulls(row)}🐂")
    while True:
        try:
            inp = input(f"  请选择收走哪列 (1~{NUM_ROWS})：").strip()
            idx = int(inp) - 1
            if 0 <= idx < NUM_ROWS:
                return idx
            print(f"  ❌ 请输入 1~{NUM_ROWS} 之间的数字")
        except (ValueError, EOFError):
            print("  ❌ 无效输入，请重试")


# =========================================================
#  Arena 对战主逻辑
# =========================================================
def arena(ppo_fn, dqn_fn, ql_fn):
    NAMES = ["😀 你(Human)", "🤖 PPO", "🔵 DQN", "🟢 QL", "🔴 AI甲", "🟡 AI乙"]
    SHORT = ["Human", "PPO", "DQN", "QL", "AI甲", "AI乙"]

    hands, rows = shuffle_deal()
    scores = [0] * NUM_PLAYERS

    print("\n" + "=" * 60)
    print("  🐂 牛头王 Arena  —  Human vs PPO vs DQN vs QL vs AI×2")
    print("=" * 60)

    for rnd in range(1, HAND_SIZE + 1):
        print(f"\n{'─'*60}")
        print(f"  第 {rnd} 轮  （剩余 {len(hands[0])} 张手牌）")
        display_board(rows, scores, SHORT)

        # ---- 各玩家选牌 ----
        chosen_idx = [None] * NUM_PLAYERS
        chosen_card = [None] * NUM_PLAYERS

        # 真人
        chosen_idx[0]  = human_choose_card(hands[0], rows, scores, NAMES[0])
        chosen_card[0] = hands[0][chosen_idx[0]]

        # PPO
        chosen_idx[1]  = ppo_fn(hands[1], rows, scores[1])
        chosen_card[1] = hands[1][chosen_idx[1]]

        # DQN
        chosen_idx[2]  = dqn_fn(hands[2], rows, scores[2])
        chosen_card[2] = hands[2][chosen_idx[2]]

        # Q-Learning
        chosen_idx[3]  = ql_fn(hands[3], rows, scores[3])
        chosen_card[3] = hands[3][chosen_idx[3]]

        # 规则 AI
        chosen_card[4] = ai_choose_card(hands[4], rows, "greedy")
        chosen_idx[4]  = hands[4].index(chosen_card[4])
        chosen_card[5] = ai_choose_card(hands[5], rows, "safe")
        chosen_idx[5]  = hands[5].index(chosen_card[5])

        # 从手牌移除
        for i in range(NUM_PLAYERS):
            hands[i].remove(chosen_card[i])

        # 展示本轮出牌
        print(f"\n  📤 本轮出牌：")
        for i in range(NUM_PLAYERS):
            print(f"     {NAMES[i]:<16}  出牌: {chosen_card[i]:3d}  {bull_str(chosen_card[i])}")

        # ---- 按牌面从小到大放牌 ----
        order = sorted(range(NUM_PLAYERS), key=lambda i: chosen_card[i])
        print(f"\n  🃏 放牌顺序（小→大）：")
        for pi in order:
            card = chosen_card[pi]
            br = find_best_row(rows, card)

            if br == -1:
                # 必须收一列
                if pi == 0:
                    # 真人自己选
                    r = human_choose_row(rows, card, NAMES[0])
                else:
                    r = ai_choose_row(rows)
                pen = place_card(rows, card, r)
                scores[pi] += pen
                msg = f"比所有列末尾小，收走列{r+1} 💥-{pen}🐂" if pen else f"比所有列末尾小，收走列{r+1}"
            else:
                pen = place_card(rows, card)
                scores[pi] += pen
                msg = f"💥 列满收走 -{pen}🐂" if pen else f"放入列{find_best_row(rows, card)+2 if br == -1 else br+1}"  # 已放入，br+1 只是显示用
                if pen == 0:
                    msg = "✅ 安全放入"
                else:
                    msg = f"💥 触发收牌 -{pen}🐂"

            mark = " ← 你！" if pi == 0 else ""
            print(f"     {NAMES[pi]:<16}  {card:3d}  {msg}{mark}")

        if max(scores) >= END_SCORE or len(hands[0]) == 0:
            print("\n  ⏹  游戏结束条件触发！")
            break

    # ---- 最终排名 ----
    print("\n" + "=" * 60)
    print("  🏆 最终结果")
    print("=" * 60)
    ranking = sorted(range(NUM_PLAYERS), key=lambda i: scores[i])
    for rank, pi in enumerate(ranking):
        bar = "🐂" * min(scores[pi], 20)
        mark = " ← 你！" if pi == 0 else ""
        print(f"  {MEDALS[rank]}  {NAMES[pi]:<16}  {scores[pi]:3d}🐂  {bar}{mark}")

    human_rank = ranking.index(0) + 1
    print(f"\n  😀 你的最终排名：第 {human_rank} 名（共 {NUM_PLAYERS} 人）")
    if human_rank == 1:
        print("  🎉 恭喜！你赢了！")
    elif human_rank <= 3:
        print("  👍 不错，前三名！")
    else:
        print("  💪 继续努力，下次更好！")
    print("=" * 60)

    return ranking


# =========================================================
#  训练三种模型
# =========================================================
def train_all(episodes=30000):
    print("\n📚 训练三种 RL 模型（各 30000 局）...\n")

    print("── Q-Learning ──")
    import nimmt_ql as ql_mod
    ql_agent = ql_mod.QLearningAgent()
    for _ in range(episodes):
        ql_mod.run_episode(ql_agent, training=True)
    ql_agent.save(os.path.join(_DIR, "nimmt_q_table.pkl"))

    print("\n── DQN ──")
    import nimmt_dqn as dqn_mod
    dqn_agent = dqn_mod.DQNAgent()
    for ep in range(1, episodes + 1):
        dqn_mod.run_episode(dqn_agent, training=True)
        if ep % 5000 == 0:
            print(f"  DQN ep {ep}/{episodes}")
    dqn_agent.save(os.path.join(_DIR, "nimmt_dqn_model.pth"))

    print("\n── PPO ──")
    import nimmt_ppo as ppo_mod
    ppo_agent = ppo_mod.PPOAgent()
    for ep in range(1, episodes + 1):
        ppo_mod.run_episode(ppo_agent, training=True)
        if ep % 10 == 0:
            ppo_agent.update()
        if ep % 5000 == 0:
            print(f"  PPO ep {ep}/{episodes}")
    ppo_agent.save(os.path.join(_DIR, "nimmt_ppo_model.pth"))

    print("\n✅ 全部训练完成！\n")


# =========================================================
#  主入口
# =========================================================
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "play"

    if mode == "train":
        train_all()

    print("=" * 60)
    print("  🐂 牛头王 Arena —— 加载 RL 模型")
    print("=" * 60)
    ppo_fn = load_ppo()
    dqn_fn = load_dqn()
    ql_fn  = load_ql()

    while True:
        arena(ppo_fn, dqn_fn, ql_fn)
        try:
            again = input("\n  再来一局？(y/n)：").strip().lower()
        except EOFError:
            break
        if again != 'y':
            print("  👋 再见！")
            break


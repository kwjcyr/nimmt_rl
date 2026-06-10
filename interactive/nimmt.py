"""
6 Nimmt! (牛头王) - 人机对战版

规则：
- 104张牌（1-104），每张有不同牛头数（罚分）
- 5列牌桌，每列最多5张
- 所有玩家同时出牌，从小到大依次放入最合适的列
- 如果你的牌是某列第6张，拿走整列（获得罚分）
- 如果你的牌比所有列末尾都小，自选一列拿走
- 累计分数超过66分时游戏结束，分数最少者获胜

玩家：1个人类 + 5个AI
"""

import random
import time

# ===== 牌面设计 =====

def get_bull_count(card: int) -> int:
    """计算一张牌的牛头数（罚分）"""
    if card == 55:
        return 7
    elif card % 11 == 0:
        return 5
    elif card % 10 == 0:
        return 3
    elif card % 5 == 0:
        return 2
    else:
        return 1


def card_str(card: int) -> str:
    """牌的显示字符串"""
    bulls = get_bull_count(card)
    bull_icon = "🐂" * bulls
    return f"{card:3d}({bull_icon})"


# ===== 游戏核心逻辑 =====

class NimmtGame:
    """
    牛头王游戏

    牌桌: 5列，每列最多5张（第6张触发拿牌）
    玩家: 1人类 + 5 AI
    """

    TOTAL_CARDS = 100
    NUM_ROWS = 5          # 牌桌列数
    MAX_ROW_LEN = 6       # 每列最多6张（放第7张时触发拿走）
    HAND_SIZE = 10        # 每人手牌数
    END_SCORE = 66        # 超过此分数游戏结束

    def __init__(self, ai_strategies=None):
        # 玩家名
        self.player_names = ["你", "A", "B", "C", "D", "E"]
        self.num_players = len(self.player_names)
        self.human_idx = 0  # 人类玩家索引

        # AI 策略
        self.ai_strategies = ai_strategies or ["greedy"] * 5

        # 游戏状态
        self.hands = [[] for _ in range(self.num_players)]   # 手牌
        self.scores = [0] * self.num_players                  # 累计牛头数
        self.rows = [[] for _ in range(self.NUM_ROWS)]        # 牌桌5列
        self.round_num = 0

    def deal(self):
        """洗牌并发牌"""
        deck = list(range(1, self.TOTAL_CARDS + 1))
        random.shuffle(deck)

        # 每人发 HAND_SIZE 张
        for i in range(self.num_players):
            self.hands[i] = sorted(deck[i * self.HAND_SIZE: (i + 1) * self.HAND_SIZE])

        # 牌桌每列放1张作为初始牌（按升序排列，小的在列1，大的在列5）
        start_idx = self.num_players * self.HAND_SIZE
        base_cards = sorted(deck[start_idx:start_idx + self.NUM_ROWS])
        for r in range(self.NUM_ROWS):
            self.rows[r] = [base_cards[r]]

    def get_bull_total(self, cards: list) -> int:
        """计算一组牌的总牛头数"""
        return sum(get_bull_count(c) for c in cards)

    def find_best_row(self, card: int) -> int:
        """
        找到牌应该放的列：
        - 放在末尾最大且比card小的列
        - 若多列满足，取差值最小的
        - 若没有合适列（card比所有末尾都小），返回 -1
        """
        best_row = -1
        min_diff = float('inf')

        for r in range(self.NUM_ROWS):
            tail = self.rows[r][-1]
            if tail < card:
                diff = card - tail
                if diff < min_diff:
                    min_diff = diff
                    best_row = r

        return best_row

    def place_card(self, card: int, chosen_row: int = None) -> int:
        """
        放置一张牌到牌桌

        Args:
            card: 要放的牌
            chosen_row: 当card比所有列末尾都小时，强制指定拿走哪列（从0开始）

        Returns:
            int: 本次获得的牛头数罚分
        """
        best_row = self.find_best_row(card)
        penalty = 0

        if best_row == -1:
            # 牌比所有列末尾都小，必须拿走一列
            r = chosen_row
            penalty = self.get_bull_total(self.rows[r])
            self.rows[r] = [card]  # 拿走后用本牌开新列
        elif len(self.rows[best_row]) >= self.MAX_ROW_LEN:
            # 该列已满5张，拿走整列
            penalty = self.get_bull_total(self.rows[best_row])
            self.rows[best_row] = [card]
        else:
            # 正常放入
            self.rows[best_row].append(card)

        return penalty

    # ===== AI 策略 =====

    def ai_choose_card(self, player_idx: int) -> int:
        """AI 选牌策略"""
        strategy = self.ai_strategies[player_idx - 1]
        hand = self.hands[player_idx]

        if strategy == "greedy":
            return self._ai_greedy(hand)
        elif strategy == "safe":
            return self._ai_safe(hand)
        elif strategy == "random":
            return random.choice(hand)
        else:
            return self._ai_greedy(hand)

    def _ai_greedy(self, hand: list) -> int:
        """
        贪心策略：选出放置后罚分期望最低的牌
        - 优先选能正常放入的牌（不触发拿走）
        - 其次选拿走列中牛头数最少的
        """
        best_card = None
        best_score = float('inf')

        for card in hand:
            best_row = self.find_best_row(card)
            if best_row == -1:
                # 必须拿走某列，选最小罚分列
                min_penalty = min(self.get_bull_total(self.rows[r]) for r in range(self.NUM_ROWS))
                score = min_penalty + 100  # 加权惩罚
            elif len(self.rows[best_row]) >= self.MAX_ROW_LEN:
                # 会触发拿走
                score = self.get_bull_total(self.rows[best_row]) + 50
            else:
                # 安全放入，剩余空位越多越好
                remaining = self.MAX_ROW_LEN - len(self.rows[best_row])
                score = -remaining  # 空位多=好

            if score < best_score:
                best_score = score
                best_card = card

        return best_card

    def _ai_safe(self, hand: list) -> int:
        """
        保守策略：尽量选择放入后离列满最远的牌
        """
        safe_cards = []
        risky_cards = []

        for card in hand:
            best_row = self.find_best_row(card)
            if best_row == -1:
                risky_cards.append((card, 999))
            elif len(self.rows[best_row]) >= self.MAX_ROW_LEN:
                risky_cards.append((card, self.get_bull_total(self.rows[best_row])))
            else:
                remaining = self.MAX_ROW_LEN - len(self.rows[best_row])
                safe_cards.append((card, remaining))

        if safe_cards:
            # 选剩余空位最多的（最安全）
            safe_cards.sort(key=lambda x: -x[1])
            return safe_cards[0][0]
        else:
            # 没有安全牌，选罚分最小的
            risky_cards.sort(key=lambda x: x[1])
            return risky_cards[0][0]

    def ai_choose_row_to_take(self, player_idx: int) -> int:
        """AI 被迫拿走某列时，选牛头数最少的列"""
        min_bulls = float('inf')
        best_row = 0
        for r in range(self.NUM_ROWS):
            bulls = self.get_bull_total(self.rows[r])
            if bulls < min_bulls:
                min_bulls = bulls
                best_row = r
        return best_row

    # ===== 显示 =====

    def display_rows(self):
        """显示当前牌桌状态"""
        print("\n  📋 当前牌桌：")
        print("  " + "─" * 60)
        for r in range(self.NUM_ROWS):
            row_str = "  ".join(card_str(c) for c in self.rows[r])
            slots_left = self.MAX_ROW_LEN - len(self.rows[r])
            slot_str = f"  [剩余{slots_left}格]" if slots_left > 0 else "  ⚠️ 已满!"
            print(f"  列{r+1}: {row_str}{slot_str}")
        print("  " + "─" * 60)

    def display_hand(self, player_idx: int):
        """显示玩家手牌"""
        hand = self.hands[player_idx]
        print("\n  🃏 你的手牌：")
        for i, card in enumerate(hand):
            bulls = get_bull_count(card)
            print(f"    [{i+1}] {card:3d}  🐂×{bulls}")

    def display_scores(self):
        """显示分数排行"""
        print("\n  📊 当前分数（牛头数越少越好）：")
        print("  " + "─" * 40)
        ranking = sorted(enumerate(self.scores), key=lambda x: x[1])
        for rank, (idx, score) in enumerate(ranking, 1):
            name = self.player_names[idx]
            bar = "▓" * min(score, 30) + ("..." if score > 30 else "")
            marker = " ← 你" if idx == self.human_idx else ""
            print(f"  {rank}名  {name:6s}: {score:3d}分  {bar}{marker}")
        print("  " + "─" * 40)

    # ===== 游戏流程 =====

    def human_choose_card(self) -> int:
        """人类选牌"""
        hand = self.hands[self.human_idx]
        self.display_hand(self.human_idx)
        self.display_rows()

        while True:
            try:
                choice = input(f"\n  请选择出哪张牌 [1-{len(hand)}]: ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(hand):
                    return hand[idx]
                else:
                    print(f"  ❌ 请输入 1 到 {len(hand)} 之间的数字")
            except ValueError:
                print("  ❌ 请输入数字")

    def human_choose_row(self) -> int:
        """人类被迫选择拿走某列"""
        print("\n  ⚠️  你的牌比所有列末尾都小，必须拿走一列！")
        self.display_rows()

        for r in range(self.NUM_ROWS):
            bulls = self.get_bull_total(self.rows[r])
            print(f"    [列{r+1}] 牛头数: {bulls} 🐂")

        while True:
            try:
                choice = input(f"\n  请选择拿走哪列 [1-{self.NUM_ROWS}]: ").strip()
                idx = int(choice) - 1
                if 0 <= idx < self.NUM_ROWS:
                    return idx
                else:
                    print(f"  ❌ 请输入 1 到 {self.NUM_ROWS} 之间的数字")
            except ValueError:
                print("  ❌ 请输入数字")

    def play_round(self):
        """进行一轮游戏（所有玩家出1张牌）"""
        self.round_num += 1
        print(f"\n{'='*65}")
        print(f"  🎮 第 {self.round_num} 轮  |  手牌剩余: {len(self.hands[0])} 张")
        print(f"{'='*65}")

        # 所有玩家选牌
        chosen_cards = {}

        # 先让人类选
        chosen_cards[self.human_idx] = self.human_choose_card()
        print(f"\n  ✅ 你选择了: {card_str(chosen_cards[self.human_idx])}")

        # AI 选牌
        print("\n  🤖 AI 正在思考...")
        time.sleep(0.5)
        for i in range(1, self.num_players):
            chosen_cards[i] = self.ai_choose_card(i)

        # 从手牌中移除选出的牌
        for i in range(self.num_players):
            self.hands[i].remove(chosen_cards[i])

        # 按牌面从小到大排序放牌
        order = sorted(range(self.num_players), key=lambda i: chosen_cards[i])

        print(f"\n  📤 本轮出牌（从小到大）：")
        print("  " + "─" * 55)

        round_penalties = [0] * self.num_players

        for i in order:
            card = chosen_cards[i]
            name = self.player_names[i]
            marker = "← 你" if i == self.human_idx else ""

            best_row = self.find_best_row(card)

            if best_row == -1:
                # 需要选列
                if i == self.human_idx:
                    chosen_row = self.human_choose_row()
                else:
                    chosen_row = self.ai_choose_row_to_take(i)
                penalty = self.place_card(card, chosen_row)
                print(f"  {name:6s}: {card_str(card)}  → 拿走第{chosen_row+1}列  💥 -{penalty}🐂 {marker}")
            elif len(self.rows[best_row]) >= self.MAX_ROW_LEN:
                penalty = self.place_card(card)
                print(f"  {name:6s}: {card_str(card)}  → 第{best_row+1}列已满，拿走!  💥 -{penalty}🐂 {marker}")
            else:
                penalty = self.place_card(card)
                print(f"  {name:6s}: {card_str(card)}  → 放入第{best_row+1}列  {marker}")

            round_penalties[i] = penalty
            self.scores[i] += penalty

        # 显示本轮结算
        print("\n  📊 本轮结算：")
        any_penalty = False
        for i in range(self.num_players):
            if round_penalties[i] > 0:
                name = self.player_names[i]
                marker = "← 你" if i == self.human_idx else ""
                print(f"    💀 {name}: -{round_penalties[i]}🐂 (累计: {self.scores[i]}) {marker}")
                any_penalty = True
        if not any_penalty:
            print("    🎉 本轮无人受罚！")

        # 显示牌桌
        self.display_rows()

    def play_game(self):
        """进行完整一局游戏"""
        print("╔" + "═"*63 + "╗")
        print("║" + " "*20 + "🐂 牛头王 6 Nimmt! 🐂" + " "*20 + "║")
        print("╚" + "═"*63 + "╝")
        print(f"""
  规则提示：
  • 104张牌（1-104），同时出牌，从小到大依次上桌
  • 放到末尾最大且比自己小的那列
  • 你是第6张 → 拿走整列（获得罚分🐂）
  • 你的牌比所有列都小 → 自选一列拿走
  • 分数超过 {self.END_SCORE} 分时游戏结束，牛头最少者获胜！

  玩家：{', '.join(self.player_names)}
""")
        input("  按 Enter 开始游戏...")

        # 发牌
        self.deal()

        # 显示初始牌桌
        print("\n  🎲 初始牌桌：")
        self.display_rows()
        print(f"\n  每人手牌 {self.HAND_SIZE} 张，共 {self.HAND_SIZE} 轮")
        input("\n  按 Enter 开始第1轮...")

        # 逐轮进行
        while len(self.hands[0]) > 0:
            self.play_round()

            # 检查是否有人超过66分
            max_score = max(self.scores)
            if max_score >= self.END_SCORE:
                print(f"\n  ⚠️  有玩家超过 {self.END_SCORE} 分，游戏结束！")
                break

            if len(self.hands[0]) > 0:
                input("\n  按 Enter 继续下一轮...")

        # 游戏结束
        self.game_over()

    def game_over(self):
        """游戏结束，显示最终结果"""
        print(f"\n{'='*65}")
        print("  🏆 游戏结束！最终排名：")
        print(f"{'='*65}")

        ranking = sorted(enumerate(self.scores), key=lambda x: x[1])

        for rank, (idx, score) in enumerate(ranking, 1):
            name = self.player_names[idx]
            marker = " ← 你" if idx == self.human_idx else ""
            medal = ["🥇", "🥈", "🥉", "4️⃣ ", "5️⃣ ", "6️⃣ "][rank - 1]
            print(f"  {medal}  {rank}名  {name:6s}: {score:3d} 🐂{marker}")

        winner_idx, winner_score = ranking[0]
        winner_name = self.player_names[winner_idx]

        print(f"\n{'='*65}")
        if winner_idx == self.human_idx:
            print("  🎉🎉🎉 恭喜！你赢了！牛头最少！🎉🎉🎉")
        else:
            human_rank = next(r for r, (i, _) in enumerate(ranking, 1) if i == self.human_idx)
            print(f"  😢 {winner_name} 获胜！你排第 {human_rank} 名。再接再厉！")
        print(f"{'='*65}\n")


def main():
    # 5个AI使用不同策略，增加多样性
    strategies = ["greedy", "safe", "greedy", "random", "safe"]

    game = NimmtGame(ai_strategies=strategies)
    game.play_game()

    # 再来一局？
    while True:
        again = input("\n  再来一局？(y/n): ").strip().lower()
        if again == 'y':
            game = NimmtGame(ai_strategies=strategies)
            game.play_game()
        else:
            print("\n  👋 再见！")
            break


if __name__ == "__main__":
    main()


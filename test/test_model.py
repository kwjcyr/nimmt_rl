"""测试新目录结构下所有模块能否正常导入和加载模型"""
import os
import sys

# test/ 的上一级就是 nimmt/ 根目录
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(_TEST_DIR)          # nimmt/
for sub in ("ql", "dqn", "ppo"):
    sys.path.insert(0, os.path.join(root, sub))

models_dir = os.path.join(root, "models")

# 测试 QL
import nimmt_ql as ql_mod
ql_agent = ql_mod.QLearningAgent()
ql_path = os.path.join(models_dir, "nimmt_q_table.pkl")
ql_agent.load(ql_path)
print(f"✅ QL  加载成功，Q表大小: {len(ql_agent.q_table)}")

# 测试 DQN
import nimmt_dqn as dqn_mod
dqn_agent = dqn_mod.DQNAgent()
dqn_path = os.path.join(models_dir, "nimmt_dqn_model.pth")
dqn_agent.load(dqn_path)
print(f"✅ DQN 加载成功")

# 测试 PPO
import nimmt_ppo as ppo_mod
ppo_agent = ppo_mod.PPOAgent()
ppo_path = os.path.join(models_dir, "nimmt_ppo_model.pth")
ppo_agent.load(ppo_path)
print(f"✅ PPO 加载成功")

# 测试出牌
hand = list(range(1,11))
rows = [[5],[20],[40],[60],[80]]
print("QL 出牌:", ql_agent.get_action(hand, rows, 0, training=False))
print("DQN 出牌:", dqn_agent.get_action(hand, rows, 0, training=False))
print("PPO 出牌:", ppo_agent.get_action_eval(hand, rows, 0))
print("\n🎉 新目录结构路径验证全部通过！")


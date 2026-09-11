"""tests/strategy/test_rl_portfolio.py — RL 组合管理单元测试。"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from aipha.strategy.rl_portfolio import (
    PortfolioEnv,
    RewardType,
    RLPortfolioConfig,
    RLTrainer,
    SimplePolicyAgent,
    TrainResult,
)

# PortfolioEnv.__init__ 要建 gymnasium.spaces.Box，缺包时直接 raise ImportError
# （rl_portfolio.py:88）。gymnasium 是可选依赖（RL 研究线），不装也不该让
# tests/strategy 挂红。只跳真正依赖它的两个类：Config / SimplePolicyAgent
# 是纯 numpy，照常跑。
requires_gym = pytest.mark.skipif(
    importlib.util.find_spec("gymnasium") is None,
    reason="PortfolioEnv 依赖可选包 gymnasium（pip install gymnasium）",
)


# ── 配置测试 ──


class TestRLPortfolioConfig:
    def test_defaults(self):
        cfg = RLPortfolioConfig()
        assert cfg.n_assets == 10
        assert cfg.feature_dim == 20
        assert cfg.lookback == 20
        assert cfg.reward_type == RewardType.LOG_RETURN
        assert cfg.cash_weight is True

    def test_custom(self):
        cfg = RLPortfolioConfig(n_assets=5, feature_dim=10, cash_weight=False)
        assert cfg.n_assets == 5
        assert cfg.cash_weight is False


# ── 环境测试 ──


@requires_gym
class TestPortfolioEnv:
    @pytest.fixture
    def small_env(self):
        cfg = RLPortfolioConfig(n_assets=3, feature_dim=4, lookback=5)
        data = np.random.randn(50, 3, 4).astype(np.float32) * 0.02
        return PortfolioEnv(cfg, data=data)

    def test_obs_shape(self, small_env: PortfolioEnv):
        obs, info = small_env.reset(seed=42)
        # lookback * n_assets * feat + n_act (3+1 cash)
        expected = 5 * 3 * 4 + 4
        assert obs.shape == (expected,)

    def test_action_shape(self, small_env: PortfolioEnv):
        assert small_env.action_space.shape == (4,)  # 3 assets + cash

    def test_step_returns_tuple(self, small_env: PortfolioEnv):
        small_env.reset(seed=0)
        action = np.array([0.3, 0.3, 0.3, 0.1], dtype=np.float32)
        result = small_env.step(action)
        assert len(result) == 5  # obs, reward, terminated, truncated, info
        obs, reward, terminated, truncated, info = result
        assert isinstance(reward, float)
        assert "portfolio_value" in info

    def test_episode_terminates(self, small_env: PortfolioEnv):
        small_env.reset(seed=0)
        steps = 0
        done = False
        while not done:
            action = np.ones(4) / 4
            _, _, terminated, truncated, _ = small_env.step(action)
            done = terminated or truncated
            steps += 1
        assert steps == 50 - 5  # total - lookback

    def test_no_cash(self):
        cfg = RLPortfolioConfig(n_assets=3, feature_dim=4, lookback=5, cash_weight=False)
        env = PortfolioEnv(cfg, data=np.random.randn(30, 3, 4).astype(np.float32) * 0.01)
        assert env.action_space.shape == (3,)
        obs, _ = env.reset()
        assert obs.shape == (5 * 3 * 4 + 3,)

    def test_reward_sharpe(self):
        cfg = RLPortfolioConfig(
            n_assets=3, feature_dim=4, lookback=5,
            reward_type=RewardType.SHARPE,
        )
        env = PortfolioEnv(cfg, data=np.random.randn(30, 3, 4).astype(np.float32) * 0.01)
        env.reset(seed=7)
        for _ in range(10):
            _, reward, _, _, _ = env.step(np.ones(4) / 4)
        assert isinstance(reward, float)


# ── 代理测试 ──


class TestSimplePolicyAgent:
    def test_action_shape(self):
        agent = SimplePolicyAgent(obs_dim=64, act_dim=4)
        obs = np.random.randn(64).astype(np.float32)
        action = agent.select_action(obs)
        assert action.shape == (4,)
        assert np.allclose(action.sum(), 1.0, atol=0.05)  # 近似归一化

    def test_action_non_negative(self):
        agent = SimplePolicyAgent(obs_dim=64, act_dim=4)
        obs = np.random.randn(64).astype(np.float32)
        action = agent.select_action(obs)
        assert (action >= 0).all()

    def test_train_step(self):
        agent = SimplePolicyAgent(obs_dim=10, act_dim=3)
        batch = {
            "obs": [np.random.randn(10) for _ in range(5)],
            "actions": [np.array([0.3, 0.3, 0.4]) for _ in range(5)],
            "returns": [0.1, 0.2, -0.1, 0.3, 0.05],
        }
        result = agent.train_step(batch)
        assert "loss" in result

    def test_end_episode(self):
        agent = SimplePolicyAgent(obs_dim=10, act_dim=3)
        for _ in range(5):
            obs = np.random.randn(10).astype(np.float32)
            act = agent.select_action(obs)
            agent.store_transition(obs, act, 0.01)
        result = agent.end_episode()
        assert "loss" in result
        # 缓冲已清空
        assert len(agent._obs_buf) == 0


# ── 训练器测试 ──


@requires_gym
class TestRLTrainer:
    def test_train_short(self):
        cfg = RLPortfolioConfig(n_assets=3, feature_dim=4, lookback=5)
        data = np.random.randn(30, 3, 4).astype(np.float32) * 0.01
        env = PortfolioEnv(cfg, data=data)
        obs_dim = env.observation_space.shape[0]
        act_dim = env.action_space.shape[0]
        agent = SimplePolicyAgent(obs_dim, act_dim)
        trainer = RLTrainer(agent, env, episodes=5)
        result = trainer.train()
        assert isinstance(result, TrainResult)
        assert result.total_episodes == 5
        assert len(result.episode_rewards) == 5
        assert result.final_weights is not None
        assert result.final_weights.shape == (act_dim,)

    def test_evaluate(self):
        cfg = RLPortfolioConfig(n_assets=3, feature_dim=4, lookback=5)
        data = np.random.randn(30, 3, 4).astype(np.float32) * 0.01
        env = PortfolioEnv(cfg, data=data)
        obs_dim = env.observation_space.shape[0]
        act_dim = env.action_space.shape[0]
        agent = SimplePolicyAgent(obs_dim, act_dim)
        metrics = RLTrainer(agent, env, episodes=3).evaluate(n_episodes=3)
        assert "mean_reward" in metrics
        assert "mean_final_value" in metrics

"""强化学习组合管理框架 — PPO/SAC 权重输出。

基于 gymnasium 标准接口构建金融投资组合环境，
支持 PPO / SAC 等策略梯度算法训练。

理论基础：
- Schulman et al. (2017): PPO (Proximal Policy Optimization)
- Haarnoja et al. (2018): SAC (Soft Actor-Critic)
- FinRL (Liu et al. 2021): 金融 RL 框架参考
- 知识库 N4: RL 组合管理探索

状态空间 (observation):
    因子值 + 制度状态 + 当前持仓 + 账户信息
动作空间 (action):
    连续目标权重向量 (Dirichlet → softmax 归一化)
奖励函数 (reward):
    风险调整收益 (Sharpe-like / log-return / drawdown penalty)

与现有架构关系：
- 训练完毕后可转换为 Strategy.on_bar() 信号
- 不替换事件驱动回测引擎，仅作为 alpha 生成器

使用示例::

    env = PortfolioEnv(config)
    agent = PPOAgent(config)
    agent.train(env, episodes=500)
    weights = agent.predict(obs)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces

    _HAS_GYM = True
except ImportError:
    _HAS_GYM = False

logger = logging.getLogger(__name__)


# ── 配置 ──


class RewardType(Enum):
    """奖励函数类型。"""
    LOG_RETURN = "log_return"
    SHARPE = "sharpe"
    SORTINO = "sortino"


@dataclass
class RLPortfolioConfig:
    """RL 组合配置。"""
    n_assets: int = 10
    feature_dim: int = 20
    lookback: int = 20
    reward_type: RewardType = RewardType.LOG_RETURN
    risk_free_rate: float = 0.02  # §13.1 全局统一 RISK_FREE_RATE_DEFAULT=0.02
    transaction_cost: float = 0.001
    max_drawdown_penalty: float = 0.5
    cash_weight: bool = True  # 是否包含现金资产


# ── 环境 ──


class PortfolioEnv:
    """金融投资组合 gymnasium 环境。

    observation: (lookback, n_assets, feature_dim) flatten
    action: n_assets (+1 if cash_weight) 连续权重, softmax 归一化
    reward: 风险调整收益
    """

    def __init__(self, config: RLPortfolioConfig, data: Optional[np.ndarray] = None):
        if not _HAS_GYM:
            raise ImportError("gymnasium is required: pip install gymnasium")
        self.config = config
        n_act = config.n_assets + (1 if config.cash_weight else 0)
        self._n_act = n_act
        obs_size = config.lookback * config.n_assets * config.feature_dim + n_act
        self._obs_space = spaces.Box(-np.inf, np.inf, shape=(obs_size,), dtype=np.float32)
        self._act_space = spaces.Box(0.0, 1.0, shape=(n_act,), dtype=np.float32)
        # data: (T, n_assets, feature_dim) — features 含 return 列
        if data is not None:
            self._data = data.astype(np.float32)
        else:
            self._data = np.random.randn(
                252, config.n_assets, config.feature_dim,
            ).astype(np.float32)
        self._weights = np.zeros(n_act, dtype=np.float32)
        self._step_idx = config.lookback
        self._portfolio_value = 1.0
        self._peak_value = 1.0
        self._return_history: list[float] = []

    @property
    def observation_space(self) -> Any:
        return self._obs_space

    @property
    def action_space(self) -> Any:
        return self._act_space

    def reset(self, *, seed: int | None = None) -> tuple[np.ndarray, dict]:
        if seed is not None:
            np.random.seed(seed)
        self._step_idx = self.config.lookback
        self._weights = np.zeros(self._n_act, dtype=np.float32)
        self._portfolio_value = 1.0
        self._peak_value = 1.0
        self._return_history = []
        return self._get_obs(), {}

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict]:
        action = np.clip(action, 0.0, None)
        total = action.sum()
        weights = action / total if total > 1e-8 else np.ones_like(action) / len(action)
        # 交易成本 = 仓位变动绝对值之和 × cost_rate
        turnover = float(np.abs(weights - self._weights).sum())
        cost = turnover * self.config.transaction_cost
        # 资产收益: 取 data 中第 0 列作为 return
        t = self._step_idx
        if t >= len(self._data):
            return self._get_obs(), 0.0, True, False, {"truncated": True}
        asset_ret = self._data[t, :, 0]  # (n_assets,)
        if self.config.cash_weight:
            all_ret = np.append(asset_ret, 0.0)  # 现金收益 = 0
        else:
            all_ret = asset_ret
        port_ret = float(np.dot(weights, all_ret)) - cost
        old_value = self._portfolio_value
        self._portfolio_value *= (1.0 + port_ret)
        self._peak_value = max(self._peak_value, self._portfolio_value)
        self._return_history.append(port_ret)
        reward = self._compute_reward(old_value, self._portfolio_value, cost)
        self._weights = weights.copy()
        self._step_idx += 1
        done = self._step_idx >= len(self._data)
        info = {"portfolio_value": self._portfolio_value, "turnover": turnover}
        return self._get_obs(), reward, done, False, info

    def _compute_reward(self, old_val: float, new_val: float, cost: float) -> float:
        ret = (new_val - old_val) / old_val if old_val > 0 else 0.0
        dd = (self._peak_value - new_val) / self._peak_value if self._peak_value > 0 else 0.0
        dd_penalty = self.config.max_drawdown_penalty * max(dd - 0.1, 0.0)
        if self.config.reward_type == RewardType.LOG_RETURN:
            return float(np.log1p(ret)) - dd_penalty
        elif self.config.reward_type == RewardType.SHARPE:
            if len(self._return_history) < 5:
                return ret - dd_penalty
            arr = np.array(self._return_history[-20:])
            std = arr.std() + 1e-8
            return float(arr.mean() / std) - dd_penalty
        return ret - dd_penalty

    def _get_obs(self) -> np.ndarray:
        t = self._step_idx
        lb = self.config.lookback
        start = max(0, t - lb)
        window = self._data[start:t]  # (lb, n_assets, feat)
        if len(window) < lb:
            pad = np.zeros((lb - len(window), self.config.n_assets, self.config.feature_dim), dtype=np.float32)
            window = np.concatenate([pad, window], axis=0)
        flat = window.flatten()
        return np.concatenate([flat, self._weights]).astype(np.float32)


# ── Agent 抽象 ──


class RLAgent(ABC):
    """RL 代理抽象基类。"""

    @abstractmethod
    def select_action(self, obs: np.ndarray) -> np.ndarray:
        pass

    @abstractmethod
    def train_step(self, batch: dict[str, np.ndarray]) -> dict[str, float]:
        pass

    def predict(self, obs: np.ndarray) -> np.ndarray:
        """预测目标权重。"""
        return self.select_action(obs)


class SimplePolicyAgent(RLAgent):
    """简单策略梯度代理 (REINFORCE)，可替换为 PPO/SAC。

    使用 softmax 输出权重，REINFORCE log-prob × return 更新。
    生产建议替换为 stable-baselines3 PPO/SAC。
    """

    def __init__(self, obs_dim: int, act_dim: int, lr: float = 1e-3):
        self.obs_dim = obs_dim
        self.act_dim = act_dim
        self.lr = lr
        # 线性策略: W (obs_dim, act_dim) + b (act_dim,)
        rng = np.random.default_rng(42)
        self._W = rng.normal(0, 0.01, (obs_dim, act_dim)).astype(np.float32)
        self._b = np.zeros(act_dim, dtype=np.float32)
        # 经验缓冲
        self._obs_buf: list[np.ndarray] = []
        self._act_buf: list[np.ndarray] = []
        self._rew_buf: list[float] = []

    def select_action(self, obs: np.ndarray) -> np.ndarray:
        logits = obs @ self._W + self._b
        # softmax → 权重
        logits = logits - logits.max()
        exp_l = np.exp(logits)
        weights = exp_l / (exp_l.sum() + 1e-8)
        # 加探索噪声
        noise = np.random.dirichlet(np.ones(self.act_dim) * 10.0)
        weights = 0.9 * weights + 0.1 * noise
        return weights.astype(np.float32)

    def store_transition(self, obs: np.ndarray, act: np.ndarray, rew: float) -> None:
        """存储一步转移。"""
        self._obs_buf.append(obs)
        self._act_buf.append(act)
        self._rew_buf.append(rew)

    def train_step(self, batch: dict[str, np.ndarray]) -> dict[str, float]:
        obs = batch.get("obs")
        actions = batch.get("actions")
        returns = batch.get("returns")
        if obs is None or actions is None or returns is None:
            return {"loss": 0.0}
        # 标准化 returns
        ret_arr = np.array(returns, dtype=np.float32)
        ret_std = ret_arr.std() + 1e-8
        ret_norm = (ret_arr - ret_arr.mean()) / ret_std
        # REINFORCE 梯度: dW += lr * return * (action - softmax(obs @ W))
        obs_arr = np.array(obs, dtype=np.float32)
        act_arr = np.array(actions, dtype=np.float32)
        logits = obs_arr @ self._W + self._b
        logits = logits - logits.max(axis=1, keepdims=True)
        exp_l = np.exp(logits)
        probs = exp_l / (exp_l.sum(axis=1, keepdims=True) + 1e-8)
        grad = act_arr - probs  # (batch, act_dim)
        dW = obs_arr.T @ (grad * ret_norm[:, None]) / len(obs_arr)
        db = (grad * ret_norm[:, None]).mean(axis=0)
        self._W += self.lr * dW
        self._b += self.lr * db
        loss = -float((ret_norm * np.log((probs * act_arr).sum(axis=1) + 1e-8)).mean())
        return {"loss": loss}

    def end_episode(self) -> dict[str, float]:
        """结束一个 episode，执行更新并清空缓冲。"""
        if not self._obs_buf:
            return {"loss": 0.0}
        # 计算折扣回报
        gamma = 0.99
        G = 0.0
        returns: list[float] = []
        for r in reversed(self._rew_buf):
            G = r + gamma * G
            returns.insert(0, G)
        batch = {
            "obs": self._obs_buf,
            "actions": self._act_buf,
            "returns": returns,
        }
        result = self.train_step(batch)
        self._obs_buf.clear()
        self._act_buf.clear()
        self._rew_buf.clear()
        return result


# ── 训练器 ──


@dataclass
class TrainResult:
    """训练结果。"""
    episode_rewards: list[float] = field(default_factory=list)
    final_weights: Optional[np.ndarray] = None
    total_episodes: int = 0
    best_reward: float = float("-inf")


class RLTrainer:
    """RL 训练循环管理器。"""

    def __init__(self, agent: RLAgent, env: PortfolioEnv, episodes: int = 500):
        self.agent = agent
        self.env = env
        self.episodes = episodes

    def train(self) -> TrainResult:
        """执行训练循环。"""
        result = TrainResult()
        for ep in range(self.episodes):
            obs, _ = self.env.reset(seed=ep)
            total_reward = 0.0
            done = False
            while not done:
                action = self.agent.select_action(obs)
                next_obs, reward, terminated, truncated, info = self.env.step(action)
                done = terminated or truncated
                total_reward += reward
                # 存储转移 (仅 SimplePolicyAgent 支持)
                if hasattr(self.agent, "store_transition"):
                    self.agent.store_transition(obs, action, reward)
                obs = next_obs
            # episode 结束后更新
            if hasattr(self.agent, "end_episode"):
                self.agent.end_episode()
            result.episode_rewards.append(total_reward)
            if total_reward > result.best_reward:
                result.best_reward = total_reward
            if (ep + 1) % 50 == 0:
                avg = np.mean(result.episode_rewards[-50:])
                logger.info("Episode %d/%d  avg_reward=%.4f", ep + 1, self.episodes, avg)
        result.total_episodes = self.episodes
        # 最终预测
        obs, _ = self.env.reset()
        result.final_weights = self.agent.predict(obs)
        return result

    def evaluate(self, n_episodes: int = 10) -> dict[str, float]:
        """评估已训练代理的表现。"""
        rewards: list[float] = []
        final_values: list[float] = []
        for ep in range(n_episodes):
            obs, _ = self.env.reset(seed=1000 + ep)
            total_reward = 0.0
            done = False
            while not done:
                action = self.agent.predict(obs)
                obs, reward, terminated, truncated, info = self.env.step(action)
                done = terminated or truncated
                total_reward += reward
            rewards.append(total_reward)
            final_values.append(info.get("portfolio_value", 1.0))
        return {
            "mean_reward": float(np.mean(rewards)),
            "std_reward": float(np.std(rewards)),
            "mean_final_value": float(np.mean(final_values)),
            "best_reward": float(np.max(rewards)),
        }

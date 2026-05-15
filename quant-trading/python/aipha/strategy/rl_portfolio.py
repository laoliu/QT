"""强化学习投资组合 — RL 环境 + 简单策略梯度 Agent"""
from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]


class RewardType(Enum):
    LOG_RETURN = "log_return"
    SHARPE = "sharpe"
    SORTINO = "sortino"


@dataclass
class RLPortfolioConfig:
    n_assets: int = 10
    feature_dim: int = 20
    lookback: int = 20
    reward_type: RewardType = RewardType.LOG_RETURN
    risk_free_rate: float = 0.02
    transaction_cost: float = 0.001
    max_drawdown_penalty: float = 0.5
    cash_weight: bool = True


@dataclass
class TrainResult:
    episode_rewards: list[float] = field(default_factory=list)
    final_weights: Any = None
    total_episodes: int = 0
    best_reward: float = float("-inf")


class _Space:
    """Minimal space wrapper with .shape attribute."""
    def __init__(self, shape: tuple[int, ...]):
        self.shape = shape


class PortfolioEnv:
    """简化投资组合强化学习环境"""

    def __init__(self, config: RLPortfolioConfig, data: Any = None):
        self._config = config
        self._data = data
        self._step_idx = 0
        self._portfolio_value = 1.0
        self._weights: Any = None
        self._peak_value = 1.0
        n_act = config.n_assets + 1 if config.cash_weight else config.n_assets
        self._n_act = n_act
        obs_dim = config.lookback * config.n_assets * config.feature_dim + n_act
        self._obs_dim = obs_dim

    @property
    def observation_space(self) -> _Space:
        return _Space((self._obs_dim,))

    @property
    def action_space(self) -> _Space:
        return _Space((self._n_act,))

    def _get_obs(self) -> Any:
        if np is None:
            return None
        cfg = self._config
        if self._data is not None:
            end = self._step_idx + cfg.lookback
            window = self._data[self._step_idx:end]  # (lookback, n_assets, feat)
            flat_market = window.flatten()
        else:
            flat_market = np.zeros(cfg.lookback * cfg.n_assets * cfg.feature_dim)
        w = self._weights if self._weights is not None else np.ones(self._n_act) / self._n_act
        return np.concatenate([flat_market, w]).astype(np.float32)

    def reset(self, *, seed: int | None = None) -> tuple[Any, dict[str, Any]]:
        self._step_idx = 0
        self._portfolio_value = 1.0
        self._peak_value = 1.0
        if np is not None:
            if seed is not None:
                np.random.seed(seed)
            self._weights = np.ones(self._n_act) / self._n_act
            return self._get_obs(), {}
        return None, {}

    def step(self, action: Any) -> tuple[Any, float, bool, bool, dict[str, Any]]:
        if np is None:
            return None, 0.0, True, False, {}
        self._step_idx += 1
        old_val = self._portfolio_value
        cfg = self._config
        # Get returns from data if available
        idx = self._step_idx + cfg.lookback - 1
        if self._data is not None and idx < len(self._data):
            # Use actual data returns
            prices = self._data[idx]  # (n_assets, feat)
            returns = prices[:, 0] if prices.ndim > 1 else prices  # first feature as return proxy
        else:
            returns = np.random.randn(cfg.n_assets) * 0.02
        if cfg.cash_weight:
            returns = np.append(returns, 0.0)
        # 交易成本
        weight_diff = np.abs(action - self._weights) if self._weights is not None else 0
        cost = np.sum(weight_diff) * cfg.transaction_cost
        portfolio_return = float(np.dot(action, returns) - cost)
        self._portfolio_value *= (1 + portfolio_return)
        self._peak_value = max(self._peak_value, self._portfolio_value)
        self._weights = action
        reward = self._compute_reward(old_val, self._portfolio_value, cost)
        terminated = (self._data is not None and self._step_idx >= len(self._data) - cfg.lookback)
        truncated = False
        obs = self._get_obs()
        return obs, reward, terminated, truncated, {"portfolio_value": self._portfolio_value}

    def _compute_reward(self, old_val: float, new_val: float, cost: float) -> float:
        cfg = self._config
        if cfg.reward_type == RewardType.LOG_RETURN:
            r = math.log(new_val / old_val) if old_val > 0 else 0.0
        else:
            r = (new_val - old_val) / old_val if old_val > 0 else 0.0
        # 回撤惩罚
        dd = (self._peak_value - new_val) / self._peak_value if self._peak_value > 0 else 0
        return r - cfg.max_drawdown_penalty * max(0, dd - 0.05)


class RLAgent(ABC):
    @abstractmethod
    def select_action(self, obs: Any) -> Any: ...
    @abstractmethod
    def train_step(self, batch: Any) -> float: ...
    def predict(self, obs: Any) -> Any:
        return self.select_action(obs)


class SimplePolicyAgent(RLAgent):
    """简单策略梯度 Agent"""

    def __init__(self, obs_dim: int, act_dim: int, lr: float = 1e-3):
        self._obs_dim = obs_dim
        self._act_dim = act_dim
        self._lr = lr
        self._obs_buf: list[Any] = []
        self._act_buf: list[Any] = []
        self._rew_buf: list[float] = []
        if np is not None:
            self._params = np.random.randn(obs_dim, act_dim) * 0.01
        else:
            self._params = None

    def select_action(self, obs: Any) -> Any:
        if np is None or self._params is None:
            return None
        flat = obs.flatten()[:self._obs_dim] if obs.ndim > 1 else obs[:self._obs_dim]
        logits = flat @ self._params
        # softmax
        exp_l = np.exp(logits - np.max(logits))
        weights = exp_l / (exp_l.sum() + 1e-9)
        return weights

    def store_transition(self, obs: Any, act: Any, rew: float) -> None:
        self._obs_buf.append(obs)
        self._act_buf.append(act)
        self._rew_buf.append(rew)

    def train_step(self, batch: Any) -> dict[str, float]:
        if np is None:
            return {"loss": 0.0}
        # Use batch dict if provided and buffer is empty
        obs_list = self._obs_buf
        act_list = self._act_buf
        rew_list = self._rew_buf
        if not obs_list and isinstance(batch, dict):
            obs_list = batch.get("obs", [])
            act_list = batch.get("actions", [])
            rew_list = batch.get("returns", [])
        if not obs_list:
            return {"loss": 0.0}
        total_loss = 0.0
        for obs, act, rew in zip(obs_list, act_list, rew_list):
            flat = obs.flatten()[:self._obs_dim] if obs.ndim > 1 else obs[:self._obs_dim]
            grad = np.outer(flat, act) * rew
            self._params += self._lr * grad
            total_loss += abs(rew)
        avg_loss = total_loss / len(obs_list)
        self._obs_buf.clear()
        self._act_buf.clear()
        self._rew_buf.clear()
        return {"loss": avg_loss}

    def end_episode(self) -> dict[str, float]:
        return self.train_step(None)


class RLTrainer:
    def __init__(self, agent: RLAgent, env: PortfolioEnv, episodes: int = 500):
        self._agent = agent
        self._env = env
        self._episodes = episodes

    def train(self) -> TrainResult:
        result = TrainResult()
        for ep in range(self._episodes):
            obs, info = self._env.reset()
            ep_reward = 0.0
            done = False
            while not done:
                action = self._agent.select_action(obs)
                obs, reward, terminated, truncated, info = self._env.step(action)
                if isinstance(self._agent, SimplePolicyAgent):
                    self._agent.store_transition(obs, action, reward)
                ep_reward += reward
                done = terminated or truncated
            if isinstance(self._agent, SimplePolicyAgent):
                self._agent.end_episode()
            result.episode_rewards.append(ep_reward)
            if ep_reward > result.best_reward:
                result.best_reward = ep_reward
                result.final_weights = self._env._weights
        result.total_episodes = self._episodes
        if result.final_weights is None and self._env._weights is not None:
            result.final_weights = self._env._weights
        return result

    def evaluate(self, n_episodes: int = 10) -> dict[str, float]:
        rewards: list[float] = []
        final_values: list[float] = []
        for _ in range(n_episodes):
            obs, _ = self._env.reset()
            total = 0.0
            done = False
            while not done:
                action = self._agent.predict(obs)
                obs, reward, terminated, truncated, info = self._env.step(action)
                total += reward
                done = terminated or truncated
            rewards.append(total)
            final_values.append(info.get("portfolio_value", 1.0))
        avg = sum(rewards) / max(len(rewards), 1)
        avg_fv = sum(final_values) / max(len(final_values), 1)
        return {"mean_reward": avg, "min_reward": min(rewards, default=0), "max_reward": max(rewards, default=0), "mean_final_value": avg_fv}

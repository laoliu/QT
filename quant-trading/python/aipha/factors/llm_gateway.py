"""统一 LLM 调用网关 (TS-4, R156)

支持主模型（DeepSeek）和验证模型（Claude）的双模型架构。
提供同步/异步接口，内置重试、token 预算控制、审计日志。

配置优先级：
  .env LLM_PRIMARY_API_KEY / LLM_VALIDATOR_API_KEY → 构造函数参数
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """LLM 调用结果。"""

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0


@dataclass
class LLMGatewayConfig:
    """网关配置。"""

    primary_model: str = "deepseek-chat"
    primary_base_url: str = "https://api.deepseek.com/v1"
    primary_api_key: str = ""

    validator_model: str = "claude-3-5-sonnet-20241022"
    validator_base_url: str = "https://api.anthropic.com/v1"
    validator_api_key: str = ""

    validator_sample_rate: float = 0.10
    max_retries: int = 3
    timeout_s: float = 60.0


class LLMGateway:
    """统一 LLM 调用网关（主模型 + 验证模型）。"""

    def __init__(self, config: LLMGatewayConfig | None = None) -> None:
        self._cfg = config or LLMGatewayConfig()
        self._load_env_keys()
        self._total_tokens = 0

    def _load_env_keys(self) -> None:
        """从环境变量加载 API key（如构造函数未提供）。"""
        if not self._cfg.primary_api_key:
            self._cfg.primary_api_key = os.environ.get("LLM_PRIMARY_API_KEY", "")
        if not self._cfg.validator_api_key:
            self._cfg.validator_api_key = os.environ.get("LLM_VALIDATOR_API_KEY", "")

    def call_primary(
        self, prompt: str, max_tokens: int = 2000
    ) -> LLMResponse:
        """调用主模型（DeepSeek）。"""
        return self._call_openai_compatible(
            prompt=prompt,
            max_tokens=max_tokens,
            model=self._cfg.primary_model,
            base_url=self._cfg.primary_base_url,
            api_key=self._cfg.primary_api_key,
        )

    def call_validator(
        self, prompt: str, max_tokens: int = 2000
    ) -> LLMResponse:
        """调用验证模型（Claude）。"""
        return self._call_openai_compatible(
            prompt=prompt,
            max_tokens=max_tokens,
            model=self._cfg.validator_model,
            base_url=self._cfg.validator_base_url,
            api_key=self._cfg.validator_api_key,
        )

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    def _call_openai_compatible(
        self,
        prompt: str,
        max_tokens: int,
        model: str,
        base_url: str,
        api_key: str,
    ) -> LLMResponse:
        """OpenAI-compatible API 调用（同步 httpx）。"""
        import httpx

        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }

        for attempt in range(1, self._cfg.max_retries + 1):
            t0 = time.monotonic()
            try:
                resp = httpx.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self._cfg.timeout_s,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                pt = usage.get("prompt_tokens", 0)
                ct = usage.get("completion_tokens", 0)
                self._total_tokens += pt + ct
                elapsed = (time.monotonic() - t0) * 1000
                logger.debug(
                    "LLM %s: %d+%d tokens, %.0fms",
                    model, pt, ct, elapsed,
                )
                return LLMResponse(
                    text=text, model=model,
                    prompt_tokens=pt, completion_tokens=ct,
                    latency_ms=elapsed,
                )
            except Exception as exc:
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s",
                    attempt, self._cfg.max_retries, exc,
                )
                if attempt == self._cfg.max_retries:
                    raise
                time.sleep(min(2 ** attempt, 10))

"""三层组合配置加载器 — combos + environments + instances 合并。

用法::

    from aipha.combo_config_loader import load_combo_config

    merged = load_combo_config(
        combo_path="configs/combos/v6d.yml",
        env_path="configs/environments/paper_s4.yml",
    )
    # merged 是一个标准 dict，可直接传入 ComboOrchestrator.from_yaml 流程

三层合并优先级::

    Layer 1 (lowest):  environment profile  → 运行环境默认值
    Layer 2:           combo definition     → 策略参数覆盖环境默认
    Layer 3 (highest): instance override    → 临时覆写（极少使用）

合并完成后执行模板变量替换（如 ``{combo_id}``）。

.. seealso:: :func:`aipha.config_resolver.resolve_config_refs` 处理 ``_preset`` 引用
"""

from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from aipha.config_resolver import _merge_preset, resolve_config_refs

logger = logging.getLogger(__name__)

# 模板变量正则：{variable_name}
_TEMPLATE_RE = re.compile(r"\{(\w+)\}")

# combo 文件的元数据字段（不进入最终合并结果）
_COMBO_META_KEYS = frozenset({"combo_id", "version"})

# 环境文件的元数据字段（不进入最终合并结果）
_ENV_META_KEYS = frozenset({"env_id"})


def load_combo_config(
    combo_path: str | Path,
    env_path: str | Path | None = None,
    override_path: str | Path | None = None,
    *,
    base_dir: str | Path = ".",
) -> dict[str, Any]:
    """加载并合并三层组合配置，返回 OrchestratorConfig 兼容的 dict。

    Parameters
    ----------
    combo_path:
        组合定义文件路径（Layer 1 策略定义）。
    env_path:
        环境 Profile 路径（Layer 2 运行环境），可选。
    override_path:
        实例覆写文件路径（Layer 3 临时覆写），可选。
    base_dir:
        所有相对路径的基准目录。

    Returns
    -------
    dict[str, Any]
        合并后的配置字典，兼容 ``ComboOrchestrator.from_yaml()`` 输入格式。

    Raises
    ------
    FileNotFoundError
        配置文件不存在。
    ValueError
        combo 文件缺少必需字段 ``combo_id``。
    """
    base = Path(base_dir)

    # ── 1. 加载组合定义 (Layer 1) ──
    combo_raw = _load_yaml(combo_path, base)
    combo_id = combo_raw.get("combo_id")
    if not combo_id:
        msg = f"combo 文件缺少必需字段 'combo_id': {combo_path}"
        raise ValueError(msg)
    logger.info("加载组合定义: %s (combo_id=%s)", combo_path, combo_id)

    # ── 2. 加载环境 Profile (Layer 2) ──
    env_data: dict[str, Any] = {}
    env_id: str | None = None
    if env_path:
        env_data = _load_yaml(env_path, base)
        env_id = env_data.get("env_id")
        logger.info("加载环境 Profile: %s (env_id=%s)", env_path, env_id)

    # ── 3. 加载实例覆写 (Layer 3) ──
    override_data: dict[str, Any] = {}
    if override_path:
        override_data = _load_yaml(override_path, base)
        logger.info("加载实例覆写: %s", override_path)

    # ── 4. 三层合并: env < combo < override ──
    # 剥离元数据字段
    combo_params = {
        k: v for k, v in combo_raw.items() if k not in _COMBO_META_KEYS
    }
    env_params = {
        k: v for k, v in env_data.items() if k not in _ENV_META_KEYS
    }

    # 第一次合并：env（基底） ← combo（覆盖）
    merged = _merge_preset(env_params, combo_params)

    # 第二次合并：merged ← override
    if override_data:
        merged = _merge_preset(merged, override_data)

    # ── 5. 生成 profile ──
    if "profile" not in merged:
        merged["profile"] = _build_profile(combo_id, env_id)

    # ── 6. 模板变量替换 ──
    template_vars = {
        "combo_id": combo_id,
        "env_id": env_id or "",
        "profile": merged.get("profile", combo_id),
    }
    merged = _substitute_templates(merged, template_vars)

    # ── 7. 记录配置来源 ──
    merged["_config_source"] = {
        "combo": str(combo_path),
        "environment": str(env_path) if env_path else None,
        "override": str(override_path) if override_path else None,
        "combo_id": combo_id,
        "env_id": env_id,
    }

    # ── 8. §13.17 风控节完整性早期检测 ──
    _risk = merged.get("risk")
    if _risk is None:
        logger.warning(
            "配置合并结果缺少 risk: 节 (combo=%s, env=%s)。"
            "combo-only 模式下 risk 参数通常由 environment 提供，"
            "该配置无法通过 paper/live 运行时的 fail-closed 门禁。"
            "请指定 env_path 或在 combo 文件中显式定义 risk: 节。",
            combo_path,
            env_path,
        )
    elif isinstance(_risk, dict):
        _REQUIRED_RISK_FIELDS = ("max_drawdown", "max_position_weight")
        _missing = [f for f in _REQUIRED_RISK_FIELDS if f not in _risk]
        if _missing:
            logger.warning(
                "risk: 节缺少推荐字段 %s (combo=%s, env=%s)。"
                "paper/live 运行时的 fail-closed 门禁可能阻止执行。",
                _missing,
                combo_path,
                env_path,
            )

    logger.info(
        "配置合并完成: profile=%s, combo=%s, env=%s",
        merged.get("profile"),
        combo_path,
        env_path,
    )
    return merged


def _load_yaml(path: str | Path, base_dir: Path) -> dict[str, Any]:
    """加载 YAML 文件并解析 _preset 引用。"""
    full_path = base_dir / path if not Path(path).is_absolute() else Path(path)
    if not full_path.exists():
        msg = f"配置文件不存在: {full_path}"
        raise FileNotFoundError(msg)

    with open(full_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        msg = f"配置文件格式错误（应为 dict）: {full_path}"
        raise TypeError(msg)

    # 解析 _preset 引用链（如 paper_s4.yml -> _base.yml）
    resolved = resolve_config_refs(raw, base_dir=base_dir)
    return resolved


def _substitute_templates(
    data: dict[str, Any],
    variables: dict[str, str],
) -> dict[str, Any]:
    """递归替换字典中所有字符串值的模板变量 ``{key}``。"""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = _TEMPLATE_RE.sub(
                lambda m: variables.get(m.group(1), m.group(0)),
                value,
            )
        elif isinstance(value, dict):
            result[key] = _substitute_templates(value, variables)
        elif isinstance(value, list):
            result[key] = [
                _substitute_templates(item, variables)
                if isinstance(item, dict)
                else (
                    _TEMPLATE_RE.sub(
                        lambda m: variables.get(m.group(1), m.group(0)),
                        item,
                    )
                    if isinstance(item, str)
                    else item
                )
                for item in value
            ]
        else:
            result[key] = value
    return result


def _build_profile(combo_id: str, env_id: str | None) -> str:
    """根据 combo_id 和 env_id 生成 profile 名称。"""
    if env_id and env_id != "_base":
        return f"{combo_id}_{env_id}"
    return combo_id

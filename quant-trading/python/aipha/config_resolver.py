"""配置预设解析器 — 展开 ``_preset`` 引用并合并参数。

用法::

    from aipha.config.config_resolver import resolve_config_refs

    # raw_config 是从 YAML 加载的原始 dict
    resolved = resolve_config_refs(raw_config, base_dir=".")

``_preset`` 字段指向一个 YAML 文件路径（相对于 base_dir），解析器会：

1. 加载预设文件中的所有键值对作为 **默认值**
2. 组件 YAML 中显式指定的参数 **覆盖** 预设值
3. 递归处理嵌套的 dict（如 ``factor_weights``）
4. 处理完成后删除 ``_preset`` 键

支持的位置：
- 顶层（回测 YAML 的 ``_preset``）
- ``components[]`` 列表中每个组件的 ``_preset``
- ``pillars.{name}`` 中每个组件的 ``_preset``
- 组件内 ``config`` 子字典的 ``_preset``
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PRESET_KEY = "_preset"


def resolve_config_refs(
    raw: dict[str, Any],
    base_dir: str | Path = ".",
) -> dict[str, Any]:
    """展开配置中所有 ``_preset`` 引用，返回合并后的新 dict。

    Parameters
    ----------
    raw:
        从 YAML 加载的原始配置字典。
    base_dir:
        预设文件路径的基准目录（通常是项目根目录）。

    Returns
    -------
    dict[str, Any]
        展开后的配置字典（深拷贝，不修改原始输入）。
    """
    result = copy.deepcopy(raw)
    base = Path(base_dir)

    # 1) 顶层 _preset
    if _PRESET_KEY in result:
        preset = _load_preset(result[_PRESET_KEY], base)
        result = _merge_preset(preset, result)

    # 2) components 列表
    if "components" in result and isinstance(result["components"], list):
        result["components"] = [
            _resolve_component(comp, base) for comp in result["components"]
        ]

    # 3) pillars dict
    if "pillars" in result and isinstance(result["pillars"], dict):
        for name, pillar in result["pillars"].items():
            if isinstance(pillar, dict):
                result["pillars"][name] = _resolve_component(pillar, base)

    # 4) 顶层 dict 值中的 _preset（r115 平铺格式: ml: {_preset: ...}）
    for key, value in list(result.items()):
        if key in ("components", "pillars", "risk", "fill_model", "governance"):
            continue  # 已处理或不应展开
        if isinstance(value, dict) and _PRESET_KEY in value:
            result[key] = _resolve_component(value, base)

    return result


def _resolve_component(
    comp: dict[str, Any],
    base: Path,
) -> dict[str, Any]:
    """解析单个组件的 ``_preset`` 引用。"""
    # 组件顶层 _preset
    if _PRESET_KEY in comp:
        preset = _load_preset(comp[_PRESET_KEY], base)
        comp = _merge_preset(preset, comp)

    # config 子字典 _preset（orchestrator 原生格式）
    if "config" in comp and isinstance(comp["config"], dict):
        cfg = comp["config"]
        if _PRESET_KEY in cfg:
            preset = _load_preset(cfg[_PRESET_KEY], base)
            comp["config"] = _merge_preset(preset, cfg)

    return comp


def _load_preset(preset_path: str, base: Path) -> dict[str, Any]:
    """加载预设 YAML 文件。"""
    path = base / preset_path
    if not path.exists():
        msg = f"预设文件不存在: {path}"
        raise FileNotFoundError(msg)

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        msg = f"预设文件格式错误（应为 dict）: {path}"
        raise TypeError(msg)

    logger.debug("加载预设: %s (%d 个参数)", preset_path, len(data))
    return data


def _merge_preset(
    preset: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """将预设作为默认值，override 中的显式值覆盖预设。

    - 对嵌套 dict 递归合并
    - ``_preset`` 键在合并后删除
    """
    merged = dict(preset)  # 预设作为基础
    for key, value in override.items():
        if key == _PRESET_KEY:
            continue  # 跳过 _preset 键本身
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            # 递归合并嵌套 dict（如 factor_weights）
            merged[key] = _merge_preset(merged[key], value)
        else:
            merged[key] = value
    return merged

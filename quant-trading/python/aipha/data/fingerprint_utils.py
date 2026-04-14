"""数据指纹工具：支持实验可复现性"""

import hashlib
import json
from datetime import datetime
from typing import Any, Dict

from aipha.data.calendar import CNStockCalendar
from aipha.data.provider_base import BarProvider


class FingerprintMismatchError(Exception):
    """指纹不匹配错误"""

    pass


def generate_experiment_fingerprint(
    *,
    provider: BarProvider,
    calendar: CNStockCalendar,
    config: Dict[str, Any],
    git_commit: str | None = None,
    random_seed: int | None = None,
) -> Dict[str, Any]:
    """生成完整的实验指纹（用于可复现性）
    
    Args:
        provider: 数据提供者
        calendar: 交易日历
        config: 实验配置（股票池、日期范围、频率等）
        git_commit: Git commit hash
        random_seed: 随机种子
        
    Returns:
        包含所有可复现性信息的字典
    """
    provider_fp = provider.fingerprint()

    fingerprint = {
        # 数据源指纹
        "provider": provider_fp.provider,
        "provider_snapshot": provider_fp.snapshot,
        "provider_adjust": provider_fp.adjust,
        "provider_schema": provider_fp.schema,
        # 日历指纹
        "calendar": calendar.fingerprint(),
        # 实验配置
        "config": config,
        "config_hash": _hash_dict(config),
        # 代码版本
        "git_commit": git_commit or "unknown",
        # 随机种子
        "random_seed": random_seed,
        # 时间戳
        "timestamp": datetime.now().isoformat(),
        # 整体哈希（用于快速比对）
        "fingerprint_hash": None,  # 稍后填充
    }

    # 计算整体哈希（排除 timestamp，因为每次生成都不同）
    hashable_fp = {k: v for k, v in fingerprint.items() if k not in ("timestamp", "fingerprint_hash")}
    fingerprint["fingerprint_hash"] = _hash_dict(hashable_fp)

    return fingerprint


def validate_fingerprint(
    fp1: Dict[str, Any],
    fp2: Dict[str, Any],
    *,
    ignore_fields: list[str] | None = None,
) -> bool:
    """验证两个指纹是否匹配（用于检查可复现性）
    
    Args:
        fp1: 第一个指纹
        fp2: 第二个指纹
        ignore_fields: 忽略的字段列表（例如 timestamp）
        
    Returns:
        bool: 是否匹配
        
    Raises:
        FingerprintMismatchError: 不匹配时抛出，包含详细差异信息
    """
    if ignore_fields is None:
        ignore_fields = ["timestamp"]

    mismatches = []

    # 检查所有字段
    all_keys = set(fp1.keys()) | set(fp2.keys())
    for key in all_keys:
        if key in ignore_fields:
            continue

        val1 = fp1.get(key)
        val2 = fp2.get(key)

        if val1 != val2:
            mismatches.append(f"{key}: {val1} != {val2}")

    if mismatches:
        raise FingerprintMismatchError(
            f"指纹不匹配，发现 {len(mismatches)} 处差异:\n" + "\n".join(mismatches)
        )

    return True


def compare_fingerprints(fp1: Dict[str, Any], fp2: Dict[str, Any]) -> Dict[str, tuple]:
    """比较两个指纹，返回差异字典
    
    Args:
        fp1: 第一个指纹
        fp2: 第二个指纹
        
    Returns:
        差异字典 {field: (value1, value2)}
    """
    differences = {}

    all_keys = set(fp1.keys()) | set(fp2.keys())
    for key in all_keys:
        val1 = fp1.get(key)
        val2 = fp2.get(key)

        if val1 != val2:
            differences[key] = (val1, val2)

    return differences


def _hash_dict(d: Dict[str, Any]) -> str:
    """计算字典的 SHA-256 哈希
    
    Args:
        d: 字典
        
    Returns:
        十六进制哈希字符串
    """
    # 将字典序列化为稳定的 JSON 字符串
    json_str = json.dumps(d, sort_keys=True, default=str)
    return hashlib.sha256(json_str.encode()).hexdigest()


def save_fingerprint(fingerprint: Dict[str, Any], filepath: str) -> None:
    """保存指纹到文件
    
    Args:
        fingerprint: 指纹字典
        filepath: 文件路径
    """
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(fingerprint, f, indent=2, default=str, ensure_ascii=False)


def load_fingerprint(filepath: str) -> Dict[str, Any]:
    """从文件加载指纹
    
    Args:
        filepath: 文件路径
        
    Returns:
        指纹字典
    """
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

"""Reproducibility utilities for experiment tracking.

This module provides tools to ensure experiments are reproducible:
- Automatic git commit capture
- Configuration file hashing
- Data fingerprinting
- Seed management

Requirements from copilot-instructions.md:
> 强制记录实验指纹：代码版本（commit）、配置文件路径与 hash、
> 数据版本/快照标识、运行时间、随机种子
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

# ============================================================================
# Git 信息捕获
# ============================================================================


def get_git_commit(short: bool = False) -> str:
    """获取当前 Git commit hash

    Args:
        short: 是否返回短版本 (7位)

    Returns:
        Commit hash 或 "unknown" (如果不在 git repo 中)
    """
    try:
        cmd = ["git", "rev-parse", "--short" if short else "", "HEAD"]
        cmd = [c for c in cmd if c]  # Remove empty strings
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout is not None:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, AttributeError):
        pass
    return "unknown"


def get_git_dirty() -> bool:
    """检查工作目录是否有未提交更改

    Returns:
        True 如果有未提交更改
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout is not None:
            return len(result.stdout.strip()) > 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, AttributeError):
        pass
    return False


def get_git_info() -> dict[str, Any]:
    """获取完整 Git 信息

    Returns:
        包含 commit、short_commit、dirty、branch 的字典
    """
    info = {
        "commit": get_git_commit(short=False),
        "short_commit": get_git_commit(short=True),
        "dirty": get_git_dirty(),
        "branch": "unknown",
    }

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            if result.stdout is not None:
                info["branch"] = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, AttributeError):
        pass

    return info


# ============================================================================
# 配置文件哈希
# ============================================================================


def hash_config_file(path: str | Path) -> str:
    """计算配置文件的 SHA256 哈希

    Args:
        path: 配置文件路径

    Returns:
        SHA256 哈希 (前16位)

    Raises:
        FileNotFoundError: 文件不存在
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "rb") as f:
        content = f.read()

    return hashlib.sha256(content).hexdigest()[:16]


def hash_config_dict(config: dict[str, Any]) -> str:
    """计算配置字典的 SHA256 哈希

    Args:
        config: 配置字典

    Returns:
        SHA256 哈希 (前16位)
    """
    # 标准化 JSON 序列化（排序键）
    json_str = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()[:16]


# ============================================================================
# 数据指纹
# ============================================================================


def compute_data_fingerprint(
    data: Any,
    method: str = "content",
) -> str:
    """计算数据指纹

    Args:
        data: 数据对象 (dict, list, DataFrame, 或其他可哈希对象)
        method: 指纹方法
            - "content": 基于内容哈希
            - "shape": 基于形状和统计信息

    Returns:
        数据指纹 (16位十六进制)
    """
    if method == "shape":
        return _compute_shape_fingerprint(data)
    else:
        return _compute_content_fingerprint(data)


def _compute_content_fingerprint(data: Any) -> str:
    """基于内容计算指纹"""
    try:
        # 尝试转为 JSON
        json_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode("utf-8")).hexdigest()[:16]
    except (TypeError, ValueError):
        # 回退到 repr
        return hashlib.sha256(repr(data).encode("utf-8")).hexdigest()[:16]


def _compute_shape_fingerprint(data: Any) -> str:
    """基于形状和统计信息计算指纹"""
    info: dict[str, Any] = {"type": type(data).__name__}

    if hasattr(data, "shape"):
        info["shape"] = list(data.shape)
    elif hasattr(data, "__len__"):
        info["length"] = len(data)

    if isinstance(data, dict):
        info["keys"] = sorted(str(k) for k in data.keys())[:100]

    # 如果是数值数据，添加统计信息
    try:
        arr = np.asarray(data)
        if np.issubdtype(arr.dtype, np.number):
            info["mean"] = float(np.nanmean(arr))
            info["std"] = float(np.nanstd(arr))
            info["min"] = float(np.nanmin(arr))
            info["max"] = float(np.nanmax(arr))
    except (TypeError, ValueError):
        pass

    return hashlib.sha256(json.dumps(info, sort_keys=True).encode()).hexdigest()[:16]


# ============================================================================
# 随机种子管理
# ============================================================================


def set_global_seed(seed: int) -> None:
    """设置全局随机种子

    同时设置:
    - Python random
    - NumPy random
    - 环境变量 PYTHONHASHSEED

    Args:
        seed: 随机种子
    """
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)


def generate_seed() -> int:
    """生成随机种子

    Returns:
        随机种子 (0 到 2^31-1)
    """
    return random.randint(0, 2**31 - 1)


# ============================================================================
# 实验指纹
# ============================================================================


@dataclass(frozen=True, slots=True)
class ExperimentFingerprint:
    """实验指纹

    用于确保实验可复现，记录关键环境信息。

    Attributes:
        git_commit: Git commit hash (完整)
        git_short: Git commit hash (短)
        git_dirty: 是否有未提交更改
        git_branch: Git 分支名
        config_path: 配置文件路径（可选）
        config_hash: 配置哈希
        data_fingerprint: 数据指纹
        random_seed: 随机种子
        run_time: 运行时间
        python_version: Python 版本
    """

    git_commit: str
    git_short: str
    git_dirty: bool
    git_branch: str
    config_path: str | None
    config_hash: str
    data_fingerprint: str
    random_seed: int
    run_time: datetime
    python_version: str = field(default_factory=lambda: _get_python_version())

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "git_commit": self.git_commit,
            "git_short": self.git_short,
            "git_dirty": self.git_dirty,
            "git_branch": self.git_branch,
            "config_path": self.config_path,
            "config_hash": self.config_hash,
            "data_fingerprint": self.data_fingerprint,
            "random_seed": self.random_seed,
            "run_time": self.run_time.isoformat(),
            "python_version": self.python_version,
        }

    def summary(self) -> str:
        """生成摘要文本"""
        dirty_marker = " (dirty)" if self.git_dirty else ""
        lines = [
            "=== 实验指纹 ===",
            f"Git: {self.git_short}{dirty_marker} @ {self.git_branch}",
            f"配置哈希: {self.config_hash}",
            f"数据指纹: {self.data_fingerprint}",
            f"随机种子: {self.random_seed}",
            f"运行时间: {self.run_time.isoformat()}",
        ]
        if self.config_path:
            lines.insert(2, f"配置文件: {self.config_path}")
        return "\n".join(lines)


def _get_python_version() -> str:
    """获取 Python 版本"""
    import sys

    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def create_fingerprint(
    config: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
    data: Any = None,
    random_seed: int | None = None,
    set_seed: bool = True,
) -> ExperimentFingerprint:
    """创建实验指纹

    自动捕获 Git 信息、计算配置哈希、设置随机种子。

    Args:
        config: 配置字典（如果提供，用于计算哈希）
        config_path: 配置文件路径（如果提供，从文件计算哈希）
        data: 数据对象（用于计算数据指纹）
        random_seed: 随机种子（如果不提供，自动生成）
        set_seed: 是否设置全局随机种子

    Returns:
        ExperimentFingerprint
    """
    # Git 信息
    git_info = get_git_info()

    # 配置哈希
    config_hash = "none"
    config_path_str = None
    if config_path is not None:
        config_path_str = str(config_path)
        try:
            config_hash = hash_config_file(config_path)
        except FileNotFoundError:
            config_hash = "file_not_found"
    elif config is not None:
        config_hash = hash_config_dict(config)

    # 数据指纹
    if data is not None:
        data_fp = compute_data_fingerprint(data)
    else:
        data_fp = "none"

    # 随机种子
    if random_seed is None:
        random_seed = generate_seed()

    if set_seed:
        set_global_seed(random_seed)

    return ExperimentFingerprint(
        git_commit=git_info["commit"],
        git_short=git_info["short_commit"],
        git_dirty=git_info["dirty"],
        git_branch=git_info["branch"],
        config_path=config_path_str,
        config_hash=config_hash,
        data_fingerprint=data_fp,
        random_seed=random_seed,
        run_time=datetime.now(),
    )


# ============================================================================
# 指纹验证
# ============================================================================


def verify_fingerprint(
    fingerprint: ExperimentFingerprint,
    config: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
    data: Any = None,
) -> dict[str, bool]:
    """验证实验指纹

    检查当前环境是否与记录的指纹匹配。

    Args:
        fingerprint: 待验证的指纹
        config: 配置字典
        config_path: 配置文件路径
        data: 数据对象

    Returns:
        验证结果字典，键为检查项，值为是否通过
    """
    results: dict[str, bool] = {}

    # Git 检查
    git_info = get_git_info()
    results["git_commit"] = git_info["commit"] == fingerprint.git_commit
    results["git_dirty"] = git_info["dirty"] == fingerprint.git_dirty

    # 配置检查
    if config_path is not None:
        try:
            current_hash = hash_config_file(config_path)
            results["config_hash"] = current_hash == fingerprint.config_hash
        except FileNotFoundError:
            results["config_hash"] = False
    elif config is not None:
        current_hash = hash_config_dict(config)
        results["config_hash"] = current_hash == fingerprint.config_hash

    # 数据检查
    if data is not None:
        current_fp = compute_data_fingerprint(data)
        results["data_fingerprint"] = current_fp == fingerprint.data_fingerprint

    return results


# ============================================================================
# 导出指纹
# ============================================================================


def save_fingerprint(fingerprint: ExperimentFingerprint, path: str | Path) -> None:
    """保存指纹到文件

    Args:
        fingerprint: 实验指纹
        path: 保存路径 (JSON)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(fingerprint.to_dict(), f, indent=2, ensure_ascii=False)


def load_fingerprint(path: str | Path) -> ExperimentFingerprint:
    """从文件加载指纹

    Args:
        path: 文件路径

    Returns:
        ExperimentFingerprint
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return ExperimentFingerprint(
        git_commit=data["git_commit"],
        git_short=data["git_short"],
        git_dirty=data["git_dirty"],
        git_branch=data["git_branch"],
        config_path=data.get("config_path"),
        config_hash=data["config_hash"],
        data_fingerprint=data["data_fingerprint"],
        random_seed=data["random_seed"],
        run_time=datetime.fromisoformat(data["run_time"]),
        python_version=data.get("python_version", "unknown"),
    )

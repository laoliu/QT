"""实验可复现性 — Git 指纹 / 配置哈希 / 数据指纹 / 全局种子"""
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


# ── Git 信息 ──────────────────────────────────────────────────

def get_git_commit(short: bool = False) -> str:
    try:
        cmd = ["git", "rev-parse", "--short" if short else "", "HEAD"]
        cmd = [c for c in cmd if c]
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def get_git_dirty() -> bool:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], stderr=subprocess.DEVNULL,
        ).decode().strip()
        return len(out) > 0
    except Exception:
        return False


def get_git_info() -> dict[str, Any]:
    commit = get_git_commit()
    short = get_git_commit(short=True)
    dirty = get_git_dirty()
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        branch = "unknown"
    return {"commit": commit, "short": short, "dirty": dirty, "branch": branch}


# ── 哈希 ──────────────────────────────────────────────────────

def hash_config_file(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        return "missing"
    content = p.read_bytes()
    return hashlib.sha256(content).hexdigest()[:16]


def hash_config_dict(config: dict) -> str:
    text = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def compute_data_fingerprint(data: Any, method: str = "content") -> str:
    if method == "shape":
        return _compute_shape_fingerprint(data)
    return _compute_content_fingerprint(data)


def _compute_content_fingerprint(data: Any) -> str:
    if isinstance(data, np.ndarray):
        return hashlib.sha256(data.tobytes()).hexdigest()[:16]
    try:
        import pandas as pd
        if isinstance(data, (pd.DataFrame, pd.Series)):
            return hashlib.sha256(
                pd.util.hash_pandas_object(data).values.tobytes()
            ).hexdigest()[:16]
    except ImportError:
        pass
    text = json.dumps(data, sort_keys=True, default=str) if not isinstance(data, str) else data
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _compute_shape_fingerprint(data: Any) -> str:
    if isinstance(data, np.ndarray):
        info = f"ndarray:{data.shape}:{data.dtype}"
    elif hasattr(data, "shape"):
        info = f"df:{data.shape}"
    elif isinstance(data, (list, tuple)):
        info = f"seq:{len(data)}"
    elif isinstance(data, dict):
        info = f"dict:{len(data)}"
    else:
        info = str(type(data))
    return hashlib.sha256(info.encode()).hexdigest()[:16]


# ── 种子管理 ──────────────────────────────────────────────────

def set_global_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


def generate_seed() -> int:
    return random.randint(0, 2**31 - 1)


def _get_python_version() -> str:
    import sys
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


# ── 指纹 ──────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ExperimentFingerprint:
    git_commit: str
    git_short: str
    git_dirty: bool
    git_branch: str
    config_path: str
    config_hash: str
    data_fingerprint: str
    random_seed: int
    run_time: str
    python_version: str

    def to_dict(self) -> dict:
        return {
            "git_commit": self.git_commit,
            "git_short": self.git_short,
            "git_dirty": self.git_dirty,
            "git_branch": self.git_branch,
            "config_path": self.config_path,
            "config_hash": self.config_hash,
            "data_fingerprint": self.data_fingerprint,
            "random_seed": self.random_seed,
            "run_time": self.run_time,
            "python_version": self.python_version,
        }

    def summary(self) -> str:
        dirty_tag = " [DIRTY]" if self.git_dirty else ""
        return (
            f"Experiment @ {self.git_short}{dirty_tag} ({self.git_branch})\n"
            f"  Config: {self.config_path} [{self.config_hash}]\n"
            f"  Data: [{self.data_fingerprint}]\n"
            f"  Seed: {self.random_seed} | Python {self.python_version}\n"
            f"  Time: {self.run_time}"
        )


def create_fingerprint(
    config: dict,
    config_path: str | Path = "",
    data: Any = None,
    random_seed: int | None = None,
    set_seed: bool = True,
) -> ExperimentFingerprint:
    seed = random_seed or generate_seed()
    if set_seed:
        set_global_seed(seed)
    git = get_git_info()
    data_fp = compute_data_fingerprint(data) if data is not None else "none"
    return ExperimentFingerprint(
        git_commit=git["commit"], git_short=git["short"],
        git_dirty=git["dirty"], git_branch=git["branch"],
        config_path=str(config_path), config_hash=hash_config_dict(config),
        data_fingerprint=data_fp, random_seed=seed,
        run_time=datetime.now().isoformat(), python_version=_get_python_version(),
    )


def verify_fingerprint(
    fingerprint: ExperimentFingerprint,
    config: dict,
    config_path: str | Path = "",
    data: Any = None,
) -> dict[str, bool]:
    results = {"config_hash": hash_config_dict(config) == fingerprint.config_hash}
    if data is not None:
        results["data_fingerprint"] = compute_data_fingerprint(data) == fingerprint.data_fingerprint
    git = get_git_info()
    results["git_commit"] = git["commit"] == fingerprint.git_commit
    results["git_dirty_match"] = git["dirty"] == fingerprint.git_dirty
    return results


def save_fingerprint(fingerprint: ExperimentFingerprint, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(fingerprint.to_dict(), indent=2), encoding="utf-8")


def load_fingerprint(path: str | Path) -> ExperimentFingerprint:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return ExperimentFingerprint(**raw)

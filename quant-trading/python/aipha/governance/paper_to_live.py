"""Paper→Live 转换工具 — S4→S5 切换自动化。

执行 Paper Trading 到 Live Trading 的配置切换:
1. 验证 S4 门禁全部通过
2. 注册组合到 Live system_settings.json
3. 验证 QMT 环境变量
4. 生成转换审计日志
5. 提供 rollback 回退接口
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

_PROJECT_ROOT = PROJECT_ROOT
_LIVE_SETTINGS = _PROJECT_ROOT / "configs" / "live" / "system_settings.json"
_LIVE_STATE = _PROJECT_ROOT / "data" / "live_state"
_AUDIT_DIR = _PROJECT_ROOT / "output" / "transition_audit"


@dataclass
class TransitionResult:
    """转换结果。"""

    combo_id: str
    success: bool = False
    timestamp: str = ""
    steps_completed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rollback_info: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        status = "✅ SUCCESS" if self.success else "❌ FAILED"
        return (
            f"Transition [{self.combo_id}]: {status} "
            f"({len(self.steps_completed)} steps)"
        )


def check_qmt_environment() -> tuple[bool, list[str]]:
    """检查 QMT 实盘环境变量是否配置。"""
    required = ["MINI_QMT_PATH", "TRADING_ACCOUNT_ID"]
    optional = ["MINI_QMT_PATH_2", "TRADING_ACCOUNT_ID_2"]
    missing = [k for k in required if not os.environ.get(k)]
    warnings = [f"{k} 未设置 (备用券商)" for k in optional if not os.environ.get(k)]
    if missing:
        return False, [f"必需环境变量缺失: {', '.join(missing)}"]
    return True, warnings


def register_live_combo(
    combo_id: str,
    *,
    settings_path: Path | None = None,
) -> tuple[bool, str]:
    """将组合注册到 Live system_settings.json。"""
    path = settings_path or _LIVE_SETTINGS
    if not path.exists():
        return False, f"system_settings.json 不存在: {path}"

    data = json.loads(path.read_text(encoding="utf-8"))
    selected = data.get("selected_portfolios", [])

    if combo_id in selected:
        return True, f"{combo_id} 已在 selected_portfolios 中"

    # 备份
    backup_path = path.with_suffix(f".{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak")
    shutil.copy2(path, backup_path)

    selected.append(combo_id)
    data["selected_portfolios"] = selected
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return True, f"已添加 {combo_id} (备份: {backup_path.name})"


def prepare_live_state_dir(combo_id: str) -> Path:
    """创建 Live 状态目录。"""
    state_dir = _LIVE_STATE / combo_id
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def write_transition_audit(
    combo_id: str,
    result: TransitionResult,
    *,
    audit_dir: Path | None = None,
) -> Path:
    """写入转换审计日志。"""
    out_dir = audit_dir or _AUDIT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_file = out_dir / f"transition_{combo_id}_{ts}.json"
    audit_data = {
        "combo_id": combo_id,
        "timestamp": result.timestamp,
        "success": result.success,
        "steps_completed": result.steps_completed,
        "errors": result.errors,
        "rollback_info": result.rollback_info,
    }
    audit_file.write_text(
        json.dumps(audit_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return audit_file


def execute_transition(
    combo_id: str,
    live_yaml_path: str | Path,
    *,
    skip_gate: bool = False,
    dry_run: bool = False,
    settings_path: Path | None = None,
) -> TransitionResult:
    """执行 Paper→Live 转换。

    Args:
        combo_id: 组合 ID
        live_yaml_path: Live 配置文件路径
        skip_gate: 跳过 S4 门禁检查 (仅用于测试)
        dry_run: 试运行模式，不实际修改配置
        settings_path: 覆盖 system_settings.json 路径
    """
    result = TransitionResult(
        combo_id=combo_id,
        timestamp=datetime.now().isoformat(timespec="seconds"),
    )

    # Step 1: 验证 Live YAML 存在
    live_path = Path(live_yaml_path)
    if not live_path.exists():
        result.errors.append(f"Live 配置文件不存在: {live_path}")
        result.success = False
        return result
    result.steps_completed.append("live_yaml_exists")

    # Step 2: S4 门禁验证
    if not skip_gate:
        from aipha.governance.go_live_gate import run_go_live_gate

        gate = run_go_live_gate(combo_id, live_yaml_path)
        if not gate.all_passed:
            failed = [c for c in gate.checks if not c.passed]
            result.errors.append(
                f"门禁未通过: {[c.name for c in failed]}"
            )
            result.success = False
            return result
    result.steps_completed.append("gate_check_passed")

    # Step 3: QMT 环境检查
    qmt_ok, qmt_msgs = check_qmt_environment()
    if not qmt_ok:
        result.errors.extend(qmt_msgs)
        result.success = False
        return result
    result.steps_completed.append("qmt_env_verified")

    # Step 4: 注册到 Live 系统
    if dry_run:
        result.steps_completed.append("register_skipped_dry_run")
    else:
        ok, msg = register_live_combo(combo_id, settings_path=settings_path)
        if not ok:
            result.errors.append(f"注册失败: {msg}")
            result.success = False
            return result
        result.steps_completed.append(f"registered: {msg}")
        result.rollback_info["settings_registered"] = True

    # Step 5: 创建 Live 状态目录
    if not dry_run:
        state_dir = prepare_live_state_dir(combo_id)
        result.steps_completed.append(f"state_dir: {state_dir}")
        result.rollback_info["state_dir"] = str(state_dir)

    # Step 6: 写入审计日志
    result.success = True
    write_transition_audit(combo_id, result)
    result.steps_completed.append("audit_written")

    return result


def rollback_transition(
    combo_id: str,
    *,
    settings_path: Path | None = None,
) -> bool:
    """回退 Paper→Live 转换 (从 selected_portfolios 移除)。"""
    path = settings_path or _LIVE_SETTINGS
    if not path.exists():
        logger.error("system_settings.json 不存在")
        return False

    data = json.loads(path.read_text(encoding="utf-8"))
    selected = data.get("selected_portfolios", [])
    if combo_id not in selected:
        logger.info(f"{combo_id} 不在 selected_portfolios 中")
        return True

    selected.remove(combo_id)
    data["selected_portfolios"] = selected
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"已从 selected_portfolios 移除 {combo_id}")
    return True

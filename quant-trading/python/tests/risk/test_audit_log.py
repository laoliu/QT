"""
审计日志单元测试
"""

import json
from pathlib import Path

import pytest

from aipha.risk.audit_log import (
    AuditEvent,
    AuditEventType,
    AuditLevel,
    AuditLogConfig,
    AuditLogger,
    SensitiveDataMasker,
)


# ============================================================================
# SensitiveDataMasker Tests
# ============================================================================


class TestSensitiveDataMasker:
    """敏感数据脱敏测试"""

    def test_masker_account(self):
        """脱敏账户类字段"""
        data = {"account_id": "12345678", "name": "test"}
        masked = SensitiveDataMasker.mask_dict(data)
        # account_id 匹配 SENSITIVE_PATTERNS 中 "account.*id"
        assert masked["account_id"] != "12345678"
        assert "****" in masked["account_id"]
        # 非敏感字段不变
        assert masked["name"] == "test"

    def test_masker_token(self):
        """脱敏 Token 字段"""
        data = {"api_token": "sk-abcdefghijklmnop"}
        masked = SensitiveDataMasker.mask_dict(data)
        assert masked["api_token"] != "sk-abcdefghijklmnop"
        assert "*" in masked["api_token"]

    def test_masker_password(self):
        """脱敏密码字段"""
        data = {"password": "mysecretpassword"}
        masked = SensitiveDataMasker.mask_dict(data)
        assert masked["password"] != "mysecretpassword"
        assert "*" in masked["password"]

    def test_masker_nested_dict(self):
        """嵌套字典脱敏"""
        data = {"config": {"api_key": "secretkey123", "host": "localhost"}}
        masked = SensitiveDataMasker.mask_dict(data)
        assert masked["config"]["api_key"] != "secretkey123"
        assert masked["config"]["host"] == "localhost"

    def test_masker_non_sensitive(self):
        """非敏感字段不变"""
        data = {"symbol": "600000.SH", "qty": 100}
        masked = SensitiveDataMasker.mask_dict(data)
        assert masked["symbol"] == "600000.SH"
        assert masked["qty"] == 100

    def test_mask_value_short_string(self):
        """短字符串完全掩盖"""
        result = SensitiveDataMasker.mask_value("ab", keep_chars=4)
        assert result == "**"

    def test_mask_value_normal_string(self):
        """正常长度字符串部分保留"""
        result = SensitiveDataMasker.mask_value("12345678", keep_chars=4)
        # 前 2 + 后 2 保留，中间 4 个 * 
        assert result[0:2] == "12"
        assert result[-2:] == "78"
        assert "*" in result


# ============================================================================
# AuditLogger Tests
# ============================================================================


class TestAuditLogger:
    """审计日志记录器测试"""

    def test_logger_log_event(self, tmp_path: Path):
        """记录事件并检查内部列表"""
        config = AuditLogConfig(log_dir=tmp_path, console_output=False)
        logger = AuditLogger(config=config)

        logger.log(
            AuditEventType.ORDER_FILLED,
            AuditLevel.TRADE,
            "Order filled: A x100",
            data={"symbol": "A", "qty": 100},
            symbol="A",
        )

        assert len(logger._events) == 1
        event = logger._events[0]
        assert event.event_type == AuditEventType.ORDER_FILLED
        assert event.level == AuditLevel.TRADE
        assert event.symbol == "A"
        logger.close()

    def test_logger_system_start_stop(self, tmp_path: Path):
        """系统启停生命周期事件"""
        config = AuditLogConfig(log_dir=tmp_path, console_output=False)
        logger = AuditLogger(config=config)

        logger.log_system_start({"initial_capital": 100000})
        logger.log_system_stop("normal")

        assert len(logger._events) == 2
        assert logger._events[0].event_type == AuditEventType.SYSTEM_START
        assert logger._events[1].event_type == AuditEventType.SYSTEM_STOP
        logger.close()

    def test_logger_jsonl_output(self, tmp_path: Path):
        """验证 JSONL 文件写入"""
        config = AuditLogConfig(log_dir=tmp_path, console_output=False)
        logger = AuditLogger(config=config)

        logger.log_system_start({"initial_capital": 100000})
        logger.log(
            AuditEventType.ORDER_FILLED,
            AuditLevel.TRADE,
            "fill test",
        )
        logger.close()

        # 查找 JSONL 文件
        jsonl_files = list(tmp_path.glob("audit_*.jsonl"))
        assert len(jsonl_files) == 1

        lines = jsonl_files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

        # 每行都是有效 JSON
        for line in lines:
            parsed = json.loads(line)
            assert "event_type" in parsed
            assert "timestamp" in parsed
            assert "session_id" in parsed

    def test_logger_session_id(self, tmp_path: Path):
        """自动生成 session ID 格式"""
        config = AuditLogConfig(log_dir=tmp_path, console_output=False)
        logger = AuditLogger(config=config)

        # 格式：YYYYMMDD_HHMMSS_xxxxxxxx
        assert len(logger.session_id) > 0
        parts = logger.session_id.split("_")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 6  # HHMMSS
        assert len(parts[2]) == 8  # hex hash
        logger.close()

    def test_logger_custom_session_id(self, tmp_path: Path):
        """使用自定义 session ID"""
        config = AuditLogConfig(log_dir=tmp_path, console_output=False)
        logger = AuditLogger(config=config, session_id="test-session-001")
        assert logger.session_id == "test-session-001"
        logger.close()

    def test_logger_get_events_filter(self, tmp_path: Path):
        """按事件类型过滤"""
        config = AuditLogConfig(log_dir=tmp_path, console_output=False)
        logger = AuditLogger(config=config)

        logger.log_system_start({"initial_capital": 100000})
        logger.log(AuditEventType.ORDER_FILLED, AuditLevel.TRADE, "fill 1")
        logger.log(AuditEventType.ORDER_FILLED, AuditLevel.TRADE, "fill 2")
        logger.log_system_stop("normal")

        fills = logger.get_events(event_type=AuditEventType.ORDER_FILLED)
        assert len(fills) == 2

        starts = logger.get_events(event_type=AuditEventType.SYSTEM_START)
        assert len(starts) == 1
        logger.close()

    def test_logger_context_manager(self, tmp_path: Path):
        """上下文管理器使用"""
        config = AuditLogConfig(log_dir=tmp_path, console_output=False)
        with AuditLogger(config=config) as logger:
            logger.log_system_start({})
            assert len(logger._events) == 1
        # 退出后文件句柄已关闭
        assert logger._file_handle is None


class TestAuditLogCleanup:
    """审计日志自动清理测试"""

    def test_cleanup_deletes_old_files(self, tmp_path: Path):
        """超过 retention_days 的日志应被删除。"""
        config = AuditLogConfig(
            log_dir=tmp_path, retention_days=30, console_output=False,
        )
        # 创建旧日志（60天前）和新日志（今天）
        from datetime import datetime, timedelta
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
        new_date = datetime.now().strftime("%Y%m%d")
        (tmp_path / f"audit_{old_date}.jsonl").write_text("{}")
        (tmp_path / f"audit_{new_date}.jsonl").write_text("{}")

        with AuditLogger(config=config) as logger:
            deleted = logger.cleanup_old_logs()
        assert deleted == 1
        assert not (tmp_path / f"audit_{old_date}.jsonl").exists()
        assert (tmp_path / f"audit_{new_date}.jsonl").exists()

    def test_cleanup_keeps_recent_files(self, tmp_path: Path):
        """retention_days 内的日志不应被删除。"""
        config = AuditLogConfig(
            log_dir=tmp_path, retention_days=90, console_output=False,
        )
        from datetime import datetime, timedelta
        recent = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        (tmp_path / f"audit_{recent}.jsonl").write_text("{}")
        with AuditLogger(config=config) as logger:
            deleted = logger.cleanup_old_logs()
        assert deleted == 0
        assert (tmp_path / f"audit_{recent}.jsonl").exists()

    def test_cleanup_ignores_non_matching_files(self, tmp_path: Path):
        """非审计日志文件不应被误删。"""
        config = AuditLogConfig(
            log_dir=tmp_path, retention_days=1, console_output=False,
        )
        (tmp_path / "random_file.txt").write_text("keep")
        (tmp_path / "audit_notadate.jsonl").write_text("keep")
        with AuditLogger(config=config) as logger:
            deleted = logger.cleanup_old_logs()
        assert deleted == 0
        assert (tmp_path / "random_file.txt").exists()
        assert (tmp_path / "audit_notadate.jsonl").exists()

    def test_cleanup_zero_retention_does_nothing(self, tmp_path: Path):
        """retention_days=0 时不删除任何文件。"""
        config = AuditLogConfig(
            log_dir=tmp_path, retention_days=0, console_output=False,
        )
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(days=9999)).strftime("%Y%m%d")
        (tmp_path / f"audit_{old}.jsonl").write_text("{}")
        with AuditLogger(config=config) as logger:
            deleted = logger.cleanup_old_logs()
        assert deleted == 0

    def test_cleanup_multiple_old_files(self, tmp_path: Path):
        """批量删除多个过期日志。"""
        config = AuditLogConfig(
            log_dir=tmp_path, retention_days=5, console_output=False,
        )
        from datetime import datetime, timedelta
        for days_ago in [10, 20, 30]:
            d = (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d")
            (tmp_path / f"audit_{d}.jsonl").write_text("{}")
        with AuditLogger(config=config) as logger:
            deleted = logger.cleanup_old_logs()
        assert deleted == 3

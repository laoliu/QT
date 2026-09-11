#!/usr/bin/env python3
"""回测索引数据库备份（cron 每日）

对 ``output/backtest_index.db`` 做在线备份 + 完整性校验 + 过期清理。

为什么不用 ``shutil.copy``：SQLite 在有写事务时文件并非一致状态，直接拷
出来的副本可能损坏，而「损坏的备份」比没有备份更糟 —— 它会让人以为有
后路。这里用 SQLite 官方的在线备份 API（``Connection.backup()``），
它在源库并发写入时也能拿到一致快照。

备份完成后立刻校验副本本身（``PRAGMA integrity_check``），校验不过就
删掉并按失败返回，绝不留下一个看起来存在、实际打不开的 .db。

用法：
    python scripts/ops/db_backup.py
    python scripts/ops/db_backup.py --verify-only
    python scripts/ops/db_backup.py --backup-dir /mnt/backup --keep-days 60
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from aipha.backtest.result_store import DEFAULT_DB_PATH
from aipha.utils.paths import PROJECT_ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 备份文件名前缀 —— cleanup 靠它 + 时间戳定位自己的产物，不碰目录里
# 其他文件（备份盘常和别的东西共用）。
BACKUP_PREFIX = "backtest_index_"
BACKUP_TS_FORMAT = "%Y%m%d_%H%M%S"

# 默认备份目录：<项目根>/output/backups
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "output" / "backups"


def verify_db_integrity(db_path: str | Path) -> bool:
    """校验 SQLite 文件完整性。

    Args:
        db_path: 数据库路径

    Returns:
        文件存在且 ``PRAGMA integrity_check`` 返回 ok 时 True，否则 False
    """
    path = Path(db_path)
    if not path.is_file():
        logger.error("数据库不存在: %s", path)
        return False

    conn = None
    try:
        # 只读打开：校验动作本身不应该有机会改坏文件
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        # 非 SQLite 文件 / 头部损坏都落在这里
        logger.error("数据库完整性校验失败 %s: %s", path, exc)
        return False
    finally:
        if conn is not None:
            conn.close()

    ok = bool(row) and str(row[0]).lower() == "ok"
    if not ok:
        logger.error("数据库完整性校验未通过 %s: %s", path, row)
    return ok


def cleanup_old_backups(
    backup_dir: str | Path,
    keep_days: int = 30,
) -> int:
    """删除超过保留期的备份，返回删除个数。

    时间取自**文件名里的时间戳**而不是 mtime：备份文件被 rsync / 移动盘
    搬过之后 mtime 会变成搬运时间，按 mtime 判断会把老备份当新的留下、
    把新备份当老的删掉。文件名解析不出时间戳的一律跳过 ——
    删除动作只在能证明「这个文件确实过期」时才做。
    """
    d = Path(backup_dir)
    if not d.is_dir():
        return 0

    cutoff = datetime.now() - timedelta(days=keep_days)
    removed = 0
    for f in sorted(d.glob(f"{BACKUP_PREFIX}*.db")):
        ts_part = f.stem[len(BACKUP_PREFIX):]
        try:
            ts = datetime.strptime(ts_part, BACKUP_TS_FORMAT)
        except ValueError:
            logger.warning("跳过无法解析时间戳的文件: %s", f.name)
            continue
        if ts >= cutoff:
            continue
        try:
            f.unlink()
        except OSError as exc:
            logger.warning("删除过期备份失败 %s: %s", f.name, exc)
            continue
        removed += 1
        logger.info("已删除过期备份: %s", f.name)

    return removed


def backup_database(
    db_path: str | Path = DEFAULT_DB_PATH,
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    keep_days: int = 30,
    notify: bool = False,
) -> Path | None:
    """在线备份数据库，返回备份文件路径；失败返回 None。

    Args:
        db_path: 源数据库
        backup_dir: 备份目录（不存在则创建）
        keep_days: 备份保留天数
        notify: 失败时是否发飞书告警（cron 里建议开）

    Returns:
        备份文件 Path；源库不存在、备份或校验失败时 None
    """
    src = Path(db_path)
    dest_dir = Path(backup_dir)

    if not src.is_file():
        msg = f"源数据库不存在，跳过备份: {src}"
        logger.error(msg)
        if notify:
            _send_alert("数据库备份失败", msg)
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime(BACKUP_TS_FORMAT)
    dest = dest_dir / f"{BACKUP_PREFIX}{ts}.db"

    src_conn = None
    dest_conn = None
    try:
        src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        dest_conn = sqlite3.connect(str(dest))
        # SQLite 在线备份 API：源库有并发写也能拿到一致快照
        src_conn.backup(dest_conn)
    except sqlite3.DatabaseError as exc:
        msg = f"数据库备份失败 {src} → {dest}: {exc}"
        logger.error(msg)
        dest.unlink(missing_ok=True)
        if notify:
            _send_alert("数据库备份失败", msg)
        return None
    finally:
        if dest_conn is not None:
            dest_conn.close()
        if src_conn is not None:
            src_conn.close()

    # 校验副本本身 —— 备份不校验等于没备份
    if not verify_db_integrity(dest):
        msg = f"备份文件完整性校验未通过，已删除: {dest}"
        logger.error(msg)
        dest.unlink(missing_ok=True)
        if notify:
            _send_alert("数据库备份失败", msg)
        return None

    size_mb = dest.stat().st_size / 1024 / 1024
    logger.info("备份完成: %s (%.2f MB)", dest, size_mb)

    removed = cleanup_old_backups(dest_dir, keep_days=keep_days)
    if removed:
        logger.info("清理过期备份 %d 个（保留 %d 天）", removed, keep_days)

    return dest


def _send_alert(title: str, body: str) -> None:
    """发送告警 —— 统一飞书通道（与 aipha/runner/daily_utils.py 同口径）。"""
    try:
        from aipha.monitor.feishu_alert import send_feishu_alert

        send_feishu_alert(title, body, level="ERROR")
    except Exception as exc:  # noqa: BLE001 - 告警失败不能影响备份主流程
        logger.warning("飞书告警失败: %s", exc)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码。"""
    parser = argparse.ArgumentParser(description="回测索引数据库备份")
    parser.add_argument(
        "--db-path", default=str(DEFAULT_DB_PATH), help="源数据库路径",
    )
    parser.add_argument(
        "--backup-dir", default=str(DEFAULT_BACKUP_DIR), help="备份目录",
    )
    parser.add_argument(
        "--keep-days", type=int, default=30, help="备份保留天数（默认 30）",
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="只做完整性校验，不备份",
    )
    parser.add_argument(
        "--notify", action="store_true", help="失败时发送飞书告警",
    )
    args = parser.parse_args(argv)

    if args.verify_only:
        ok = verify_db_integrity(args.db_path)
        logger.info("完整性校验: %s", "通过" if ok else "未通过")
        return 0 if ok else 1

    result = backup_database(
        db_path=args.db_path,
        backup_dir=args.backup_dir,
        keep_days=args.keep_days,
        notify=args.notify,
    )
    return 0 if result is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())

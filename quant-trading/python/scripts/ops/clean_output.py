#!/usr/bin/env python3
"""output/ 目录散落文件整理

回测/脚本长年往 ``output/`` 根下直接扔日志、临时 json、一次性脚本，
时间长了几千个文件混在策略目录之间，既拖慢文件浏览也让人不敢删。
本脚本按后缀分类，把**根目录下的散落文件**归档到 ``output/_archive/<类别>/``。

安全约定（这是无人值守脚本，删错了没有回收站）：

- **默认 dry-run**：不带参数只打印预览，一个文件都不动。
- ``--execute`` 执行归档（move 到 ``_archive/``，可逆）。
- 真删除还要再加 ``--delete``，即 ``--execute --delete`` 两个开关同时给
  才会 unlink。单个开关手滑打错不会造成不可逆后果。
- 子目录一律不碰（策略产物目录都在子目录里），
  ``KEEP_FILES`` 里的文件也不碰。

用法：
    python scripts/ops/clean_output.py                    # 预览
    python scripts/ops/clean_output.py --execute          # 归档
    python scripts/ops/clean_output.py --execute --delete # 删除（谨慎）
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

from aipha.utils.paths import PROJECT_ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"

ARCHIVE_DIRNAME = "_archive"

# 根目录下这些文件是**在用的**，永不归档也永不删除。
# backtest_index.db 是回测索引主库（result_store 的默认落点），
# 归档它等于把整个回测历史搬走。
KEEP_FILES: frozenset[str] = frozenset({
    "backtest_index.db",
    "backtest_index.db-wal",
    "backtest_index.db-shm",
    "mining.db",
    ".gitignore",
    ".gitkeep",
    "README.md",
})

# 后缀 → 类别。类别名同时是 _archive/ 下的子目录名。
_SUFFIX_CATEGORY: dict[str, str] = {
    ".log": "logs",
    ".txt": "logs",
    ".out": "logs",
    ".json": "json_archive",
    ".jsonl": "json_archive",
    ".csv": "json_archive",
    ".yaml": "config_archive",
    ".yml": "config_archive",
    ".html": "html_archive",
    ".htm": "html_archive",
    ".md": "html_archive",
    ".py": "script_archive",
    ".sh": "script_archive",
}

MISC_CATEGORY = "misc"


def classify_file(name: str | Path) -> str:
    """按文件名后缀返回归档类别；未知后缀归 ``misc``。"""
    suffix = Path(name).suffix.lower()
    return _SUFFIX_CATEGORY.get(suffix, MISC_CATEGORY)


def scan_scattered_files(output_dir: str | Path) -> list[Path]:
    """列出 output/ **根目录下**的散落文件（不含子目录、不含保留文件）。

    只看根目录一层：子目录是策略产物的正常归属，递归下去就会把
    正在用的回测结果一起卷走。
    """
    d = Path(output_dir)
    if not d.is_dir():
        return []

    files: list[Path] = []
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        if p.name in KEEP_FILES:
            continue
        files.append(p)
    return files


def preview(output_dir: str | Path) -> dict[str, list[Path]]:
    """按类别分组返回待处理文件；没有散落文件时返回 ``{}``。"""
    groups: dict[str, list[Path]] = {}
    for f in scan_scattered_files(output_dir):
        groups.setdefault(classify_file(f.name), []).append(f)
    return groups


def execute_archive(output_dir: str | Path) -> tuple[int, int]:
    """把散落文件移动到 ``output/_archive/<类别>/``。

    Returns:
        (移动文件数, 移动字节数)
    """
    d = Path(output_dir)
    groups = preview(d)
    if not groups:
        return 0, 0

    archive_root = d / ARCHIVE_DIRNAME
    moved = 0
    nbytes = 0
    for category, files in groups.items():
        target_dir = archive_root / category
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            size = _safe_size(f)
            dest = _unique_dest(target_dir / f.name)
            try:
                shutil.move(str(f), str(dest))
            except OSError as exc:
                logger.warning("归档失败 %s: %s", f.name, exc)
                continue
            moved += 1
            nbytes += size
    logger.info(
        "已归档 %d 个文件（%.2f MB）→ %s",
        moved, nbytes / 1024 / 1024, archive_root,
    )
    return moved, nbytes


def execute_delete(output_dir: str | Path) -> tuple[int, int]:
    """直接删除散落文件（不归档）。

    Returns:
        (删除文件数, 删除字节数)
    """
    files = scan_scattered_files(output_dir)
    deleted = 0
    nbytes = 0
    for f in files:
        size = _safe_size(f)
        try:
            f.unlink()
        except OSError as exc:
            logger.warning("删除失败 %s: %s", f.name, exc)
            continue
        deleted += 1
        nbytes += size
    logger.info("已删除 %d 个文件（%.2f MB）", deleted, nbytes / 1024 / 1024)
    return deleted, nbytes


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _unique_dest(dest: Path) -> Path:
    """目标已存在时加 ``_1`` / ``_2`` 后缀，绝不覆盖已归档的同名文件。"""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for i in range(1, 1000):
        candidate = dest.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    return dest.with_name(f"{stem}_{id(dest)}{suffix}")


def _print_preview(groups: dict[str, list[Path]]) -> None:
    total = sum(len(v) for v in groups.values())
    total_bytes = sum(_safe_size(f) for files in groups.values() for f in files)
    print(f"待处理散落文件: {total} 个（{total_bytes / 1024 / 1024:.2f} MB）")
    for category in sorted(groups):
        files = groups[category]
        cat_bytes = sum(_safe_size(f) for f in files)
        print(f"  [{category}] {len(files)} 个，{cat_bytes / 1024:.1f} KB")
        for f in files[:10]:
            print(f"    - {f.name}")
        if len(files) > 10:
            print(f"    ... 还有 {len(files) - 10} 个")


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码。"""
    parser = argparse.ArgumentParser(description="output/ 目录散落文件整理")
    parser.add_argument(
        "--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="output 目录",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="真正执行（不给此开关只预览，不动任何文件）",
    )
    parser.add_argument(
        "--delete", action="store_true",
        help="删除而不是归档；必须与 --execute 同时给",
    )
    args = parser.parse_args(argv)

    out = Path(args.output_dir)
    if not out.is_dir():
        logger.error("output 目录不存在: %s", out)
        return 1

    groups = preview(out)
    if not groups:
        logger.info("没有散落文件，无需整理: %s", out)
        return 0

    if not args.execute:
        _print_preview(groups)
        print("\n[dry-run] 未做任何改动。加 --execute 归档，"
              "或 --execute --delete 删除。")
        return 0

    if args.delete:
        # 不可逆操作，日志里留下明确记录（cron 日志是唯一的事后线索）
        logger.warning("即将删除 %d 个散落文件（不可恢复）",
                       sum(len(v) for v in groups.values()))
        execute_delete(out)
    else:
        execute_archive(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

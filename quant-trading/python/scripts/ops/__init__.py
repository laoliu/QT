"""运维脚本包（cron / 容器定时任务入口）。

这里放的都是**无人值守**运行的脚本：数据库备份、NAV 偏差监控、输出目录
清理、因子衰减报告、状态陈旧检查等。约定：

1. 每个模块提供 ``main(argv: list[str] | None = None) -> int``，
   返回值即进程退出码（0 正常 / 1 异常），便于 cron 与容器健康检查判断。
2. **有破坏性的操作一律默认 dry-run**，真正动文件必须显式传参
   （见 ``clean_output`` 的 ``--execute``）。无人值守 + 默认删除
   = 迟早删掉不该删的东西。
3. 告警统一走 ``aipha.monitor.feishu_alert``，不在这里自建通道。

模块同时支持两种导入方式（测试两种都用）::

    from scripts.ops.db_backup import backup_database   # 包路径
    import data_preheat                                 # scripts/ops 在 sys.path 上

因此各模块之间**不做相对导入** —— 跨模块共享的东西放 ``aipha/`` 下，
否则换一种导入方式就崩。
"""

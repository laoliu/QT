# QMT 只读实机验收记录

验收日期：2026-10-18（Asia/Shanghai）。此记录是该时间点的观察，不表示服务当前始终在线。

## 环境与路径

- 完整联储证券 QMT 客户端运行 BRIDGE v24；miniQMT 不可用。
- `BRIDGE.py → RECEIVER :18889 → 网关交易请求/响应队列 → QMT get_trade_detail_data`。
- 仓库应用仍使用 Paper broker；本次通过独立只读探测请求 QMT，未切换应用的交易模式。
- 用户确认配置的是实盘资金账号，账户实际资金为零且无持仓。

## 已观察到的结果

执行命令：

```powershell
python scripts/qmt-readonly-probe.py --receiver-url http://127.0.0.1:18889 --timeout 60
```

09:25:10–09:25:13，QMT 的 `BRIDGE_DEBUG.log` 依次记录 `connect`、`query_account`、`query_positions`、`query_orders`，各请求均回传 `ok=True`。进程在约 4 秒内以退出码 0 结束，返回：

```json
{
  "ok": true,
  "transport": "receiver",
  "connected": { "status": "already_connected", "mode": "strategy" },
  "account": { "cash": 0, "frozen_cash": 0, "total_asset": 0, "market_value": 0 },
  "positionCount": 0,
  "orderCount": 0,
  "orderScope": "broker_query_not_local_history"
}
```

记录省略了资金账号标识。实际探测同时校验连接结果和账户返回的账号与配置一致。后续健康检查返回 `bridge_connected=true`，最后轮询距检查时间约 0.6 秒。

09:29:27–09:29:30，另经 RECEIVER `/request`、`/result/<id>` 请求 `000300.SH` 的 5 根日线。QMT 日志确认 `ContextInfo.get_market_data` 返回 DataFrame 并推送 5 根 K 线；日期为 2026-10-12 至 2026-10-16，最后收盘为 4170.613，OHLCV 均有效。该次 `download_history_data` 报连接警告，随后从 QMT 已有行情数据成功读取，因此只能证明本地已有行情的传输，不能证明在线历史下载或实时行情刷新。

## 回放与验收范围

`order-manager-paper.integration.test.ts` 使用 PaperBroker 完成订单与交易的持久化、重建 broker/manager、查询历史记录；`qmt-broker.test.ts` 使用 QMT 响应替身验证 broker 与 OrderManager 的历史合并及重启回放。`order-manager.test.ts` 检查模式分文件与统一 provenance 保存，`remote-qmt-broker.test.ts` 检查查询时保留本地订单标识及来源。

这些测试覆盖历史存储和查询契约；本次真实账户为空，因此没有实盘成交样本用于回放。实机探测只证明完整客户端查询链路可用，不包含下单、撤单、成交回报或连续交易日稳定性验收。

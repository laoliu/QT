# QMT 只读实机验收记录

验收日期：2026-10-18（Asia/Shanghai）。此记录是该时间点的观察，不表示服务当前始终在线。

## 23:29—23:34 MiniQMT 更新后只读检查

BIOS 更新重启后，用户自行启动 MiniQMT。实际观察到 `D:/st_lc/bin.x64/XtMiniQmt.exe`、18888 数据桥接在线；`/health` 返回 `xtdata=true`、`trading=true`、`trading_status=connected`。资金查询返回的账号与本机配置一致，持仓 10 项；账号及具体资产金额不入本记录。18889 RECEIVER、18891 管理服务当时未监听，本次未启动或重启它们。下方“miniQMT 不可用、完整客户端及空账户”的记录是当天早先观察，已不能代表当前状态。

经正在运行的 18888 桥接，沪深300及平安银行行情可读，沪深300最近 5 根日线截止 2026-10-16。周日读取到本地行情不能证明下一交易日盘中刷新或在线补数正常。

实机发现 `query_stock_orders` 在当前 SDK 的参数名称为 `cancelable_only`，旧代码传入 `cancel_able_only` 导致 HTTP 委托查询失败。两个 HTTP 桥接及独立 broker 已统一用第二个位置参数 `False` 请求全部委托，兼容两种参数拼写；没有扩大下单/撤单能力。独立 broker 还遇到当前 `XtAsset` 不提供 `profit/profit_ratio`，现保留必需资产字段，将可选字段缺失列入 `_unavailable_fields`；兼容默认值不代表实际收益为零。

`tests/execution/test_qmt_bridge_contract.py` **13 passed**，覆盖新旧 SDK 拼写、单笔/列表/股票过滤、已成交委托不被排除，以及缺少可选收益字段；均为离线替身，无真实下单。随后 `scripts/qmt-readonly-probe.py --timeout 20` 实机退出 0，资金账号匹配、持仓 10、委托 0，独立查询连接已断开。全部请求仅限连接/查询/断开，没有下单或撤单。

23:33 经用户明确授权“允许，仅重启 18888 桥接”，只停止原桥接 PID 28136，并使用相同解释器、入口、监听地址、端口、QMT/mini 路径和账户参数启动新桥接 PID 26768，日志独立保存。MiniQMT PID 20460 及其启动时间保持不变；未启动 18889/18891，未改 Paper 部署或交易模式。

**修复现已加载至在线 18888 桥接。** `/health` 返回 `xtdata=true`、`trading=true`、`trading_status=connected`；HTTP 资金账号匹配、持仓 10 项、委托列表成功返回 0 项，原参数名错误未复现。两个品种行情及沪深300最近 5 根日线仍可读。截至 23:34，本次开机以来新增 WHEA 和 Python Application Error 1000 均为 0；Paper 任务仍启用、下次 2026-10-19 18:30。该结果是只读查询验收，不包含下单、撤单、成交回报、盘中刷新或长期稳定性验收。带哈希的脱敏摘要见 [MiniQMT 证据](evidence/miniqmt_post_bios_20261018.json)。

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

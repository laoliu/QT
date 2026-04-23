/**
 * 波段交易策略回测
 * Usage:
 *   npx tsx quant-trading/src/engine/_run-swing-backtest.ts           # 全部20只代表股
 *   npx tsx quant-trading/src/engine/_run-swing-backtest.ts 600519    # 单只
 *   npx tsx quant-trading/src/engine/_run-swing-backtest.ts --akshare # 用akshare数据源
 */
import { runBacktest } from "./strategy-registry.js";
import { todayCST } from "../utils/cst-time.js";

const dataProvider = process.argv.includes("--demo")
  ? "demo"
  : process.argv.includes("--akshare")
    ? "akshare"
    : undefined;

// 精选20ֻ — 覆盖不同行业和波动特征的代表性个股
const defaultStocks = [
  // 大消费（低波动，适合均值回归）
  { code: "600519", name: "贵州茅台" },
  { code: "000858", name: "五粮液" },
  { code: "603288", name: "海天味业" },
  // 金融（中低波动）
  { code: "600036", name: "招商银行" },
  { code: "601318", name: "中国平安" },
  // 新能源（高波动，适合区间突破/KDJ）
  { code: "300750", name: "宁德时代" },
  { code: "002594", name: "比亚迪" },
  { code: "300274", name: "阳光电源" },
  // 科技/半导体（高波动）
  { code: "002230", name: "科大讯飞" },
  { code: "300059", name: "东方财富" },
  { code: "688981", name: "中芯国际" },
  // 医药（中波动）
  { code: "600276", name: "恒瑞医药" },
  { code: "300760", name: "迈瑞医疗" },
  // 家电（中低波动）
  { code: "000333", name: "美的集团" },
  { code: "000651", name: "格力电器" },
  // 周期（高波动，适合波段）
  { code: "601899", name: "紫金矿业" },
  { code: "600309", name: "万华化学" },
  // 蓝筹稳健
  { code: "600900", name: "长江电力" },
  { code: "601888", name: "中国中免" },
  { code: "600406", name: "国电南瑞" },
];

const singleArg = process.argv.find((a, i) => i >= 2 && !a.startsWith("--"));
const stocks = singleArg ? [{ code: singleArg, name: singleArg }] : defaultStocks;

// 波段交易核心策略
const strategies = [
  { id: "mean-reversion", short: "均值回归" },
  { id: "rsi-reversal", short: "RSI反转" },
  { id: "vwap-reversion", short: "VWAP回归" },
  { id: "mean-reversion-zscore", short: "ZScore均回" },
  { id: "williams-r", short: "威廉指标" },
  { id: "range-breakout", short: "区间突破" },
  { id: "double-bottom", short: "双底形态" },
  { id: "cta-kdj", short: "KDJ" },
  { id: "bollinger-breakout", short: "布林突破" },
  { id: "stochastic-rsi", short: "StochRSI" },
];

// Provider config
const providerConfig: Record<string, unknown> = {};
if (dataProvider) {
  providerConfig.dataProvider = dataProvider;
} else {
  providerConfig.tradingAccount = {
    broker: "remote-qmt",
    qmt: { windowsBridgeUrl: "http://192.168.3.30:58088" },
  };
}

console.log(`\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`);
console.log(`  波段交易策略回测  |  ${todayCST()}`);
console.log(`  数据源: ${dataProvider ?? "qmt-remote (auto)"}`);
console.log(`  标的数: ${stocks.length}  |  策略数: ${strategies.length}`);
console.log(`  回测区间: 2024-06-01 ~ 2025-12-31 (Լ18个月)`);
console.log(`  初始资金: ¥100,000  |  佣金万3 + 印花税千0.5`);
console.log(`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n`);

type Row = {
  stock: string;
  name: string;
  strategy: string;
  totalReturn: number;
  maxDrawdown: number;
  winRate: number;
  sharpe: number;
  trades: number;
  profitFactor: number;
  finalValue: number;
};
const rows: Row[] = [];

for (const stock of stocks) {
  console.log(`▶ ${stock.code} ${stock.name}`);
  for (const strat of strategies) {
    try {
      const result = await runBacktest(
        {
          strategyId: strat.id,
          symbols: [stock.code],
          startDate: "2024-06-01",
          endDate: "2025-12-31",
          initialCash: 100000,
          engine: "builtin",
          commission: 0.0003,
          stampDuty: 0.0005,
          slippage: 0.001,
        },
        providerConfig as never,
      );
      rows.push({
        stock: stock.code,
        name: stock.name,
        strategy: strat.short,
        totalReturn: result.totalReturn,
        maxDrawdown: result.maxDrawdown,
        winRate: result.winRate,
        sharpe: result.sharpeRatio,
        trades: result.totalTrades,
        profitFactor: result.profitFactor ?? 0,
        finalValue: result.finalValue,
      });
      const ret =
        result.totalReturn >= 0
          ? `+${result.totalReturn.toFixed(1)}%`
          : `${result.totalReturn.toFixed(1)}%`;
      process.stdout.write(
        `  ${strat.short.padEnd(10)} ${ret.padStart(8)}  回撤 ${result.maxDrawdown.toFixed(1).padStart(5)}%  胜率 ${result.winRate.toFixed(0).padStart(3)}%  交易${String(result.totalTrades).padStart(3)}次  盈亏比${(result.profitFactor ?? 0).toFixed(2).padStart(5)}\n`,
      );
    } catch (e) {
      process.stdout.write(`  ${strat.short.padEnd(10)}   ❌ ${(e as Error).message?.slice(0, 50) ?? "ERROR"}\n`);
    }
  }
  console.log();
}

// ── 策略综合排行榜 ──
console.log("\n" + "═".repeat(100));
console.log("  波段策略综合排行榜（所有标的平均）");
console.log("═".repeat(100));

const stratSummary = strategies.map((s) => {
  const sRows = rows.filter((r) => r.strategy === s.short);
  const n = sRows.length || 1;
  return {
    strategy: s.short,
    avgReturn: sRows.reduce((a, r) => a + r.totalReturn, 0) / n,
    avgDrawdown: sRows.reduce((a, r) => a + r.maxDrawdown, 0) / n,
    avgWinRate: sRows.reduce((a, r) => a + r.winRate, 0) / n,
    avgSharpe: sRows.reduce((a, r) => a + r.sharpe, 0) / n,
    avgPF: sRows.reduce((a, r) => a + r.profitFactor, 0) / n,
    totalTrades: sRows.reduce((a, r) => a + r.trades, 0),
    winCount: sRows.filter((r) => r.totalReturn > 0).length,
    stockCount: sRows.length,
  };
});
stratSummary.sort((a, b) => b.avgReturn - a.avgReturn);

console.log(
  `  ${"策略".padEnd(12)} ${"平均收益".padStart(8)} ${"平均回撤".padStart(8)} ${"平均胜率".padStart(8)} ${"平均夏普".padStart(8)} ${"盈亏比".padStart(8)} ${"盈利票".padStart(8)} ${"总交易".padStart(6)}`,
);
console.log("─".repeat(100));
for (const s of stratSummary) {
  const ret = s.avgReturn >= 0 ? `+${s.avgReturn.toFixed(2)}%` : `${s.avgReturn.toFixed(2)}%`;
  console.log(
    `  ${s.strategy.padEnd(12)} ${ret.padStart(8)} ${(s.avgDrawdown.toFixed(2) + "%").padStart(8)} ${(s.avgWinRate.toFixed(1) + "%").padStart(8)} ${s.avgSharpe.toFixed(3).padStart(8)} ${s.avgPF.toFixed(2).padStart(8)} ${(s.winCount + "/" + s.stockCount).padStart(8)} ${String(s.totalTrades).padStart(6)}`,
  );
}
console.log("═".repeat(100));

// ── 各标的最优波段策略 ──
console.log("\n  各标的最优波段策略:");
console.log("─".repeat(80));
for (const stock of stocks) {
  const sRows = rows.filter((r) => r.stock === stock.code);
  if (sRows.length === 0) continue;
  const best = sRows.reduce((a, b) => (a.totalReturn > b.totalReturn ? a : b));
  const ret =
    best.totalReturn >= 0 ? `+${best.totalReturn.toFixed(2)}%` : `${best.totalReturn.toFixed(2)}%`;
  console.log(
    `  ${stock.code} ${stock.name.padEnd(10)} → ${best.strategy.padEnd(10)} ${ret.padStart(9)}  回撤${best.maxDrawdown.toFixed(1).padStart(5)}%  胜率${best.winRate.toFixed(0).padStart(3)}%  Sharpe ${best.sharpe.toFixed(2)}`,
  );
}

// ── 风险调整后排名（按夏普比+盈亏比综合评分）──
console.log("\n  风险调整后综合评分（夏普×0.4 + 盈亏比×0.3 + 胜率×0.3）:");
console.log("─".repeat(80));
const scoredStrategies = stratSummary.map((s) => ({
  ...s,
  compositeScore:
    s.avgSharpe * 0.4 + s.avgPF * 0.3 + (s.avgWinRate / 100) * 0.3,
}));
scoredStrategies.sort((a, b) => b.compositeScore - a.compositeScore);
for (const [i, s] of scoredStrategies.entries()) {
  console.log(
    `  ${String(i + 1).padStart(2)}. ${s.strategy.padEnd(12)} 综合${s.compositeScore.toFixed(3).padStart(6)}  夏普${s.avgSharpe.toFixed(3).padStart(7)}  盈亏比${s.avgPF.toFixed(2).padStart(5)}  胜率${(s.avgWinRate.toFixed(1) + "%").padStart(6)}  收益${(s.avgReturn >= 0 ? "+" : "") + s.avgReturn.toFixed(2) + "%"}`,
  );
}
console.log();

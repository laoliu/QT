/**
 * Multi-stock backtest comparison runner.
 * Usage:
 *   npx tsx extensions/quant-trading/src/engine/_run-backtest.ts           # all default stocks
 *   npx tsx extensions/quant-trading/src/engine/_run-backtest.ts 600519    # single stock
 *   npx tsx extensions/quant-trading/src/engine/_run-backtest.ts --akshare # force akshare
 */
import { runBacktest } from "./strategy-registry.js";

// Data source
const dataProvider = process.argv.includes("--demo")
  ? "demo"
  : process.argv.includes("--akshare")
    ? "akshare"
    : undefined;

// Stock universe — 100 popular A-shares across sectors
const defaultStocks = [
  // 白酒
  { code: "600519", name: "贵州茅台" },
  { code: "000858", name: "五粮液" },
  { code: "000568", name: "泸州老窖" },
  { code: "002304", name: "洋河股份" },
  { code: "000799", name: "酒鬼酒" },
  // 银行
  { code: "600036", name: "招商银行" },
  { code: "000001", name: "平安银行" },
  { code: "601398", name: "工商银行" },
  { code: "601939", name: "建设银行" },
  { code: "600000", name: "浦发银行" },
  { code: "601166", name: "兴业银行" },
  // 保险/券商
  { code: "601318", name: "中国平安" },
  { code: "601601", name: "中国太保" },
  { code: "600030", name: "中信证券" },
  { code: "601688", name: "华泰证券" },
  // ҽҩ
  { code: "600276", name: "恒瑞医药" },
  { code: "300760", name: "迈瑞医疗" },
  { code: "000538", name: "云南白药" },
  { code: "600196", name: "复星医药" },
  { code: "300122", name: "智飞生物" },
  // 新能源
  { code: "300750", name: "宁德时代" },
  { code: "002594", name: "比亚迪" },
  { code: "601012", name: "隆基绿能" },
  { code: "300274", name: "阳光电源" },
  { code: "002459", name: "晶澳科技" },
  // 家电
  { code: "000333", name: "美的集团" },
  { code: "000651", name: "格力电器" },
  { code: "002032", name: "苏泊尔" },
  { code: "600690", name: "海尔智家" },
  // 科技/半导体
  { code: "002230", name: "科大讯飞" },
  { code: "688981", name: "中芯国际" },
  { code: "002049", name: "紫光国微" },
  { code: "603501", name: "韦尔股份" },
  { code: "688012", name: "中微公司" },
  // 互联网/软件
  { code: "002415", name: "海康威视" },
  { code: "300033", name: "同花顺" },
  { code: "688111", name: "金山办公" },
  { code: "002241", name: "歌尔股份" },
  // 消费
  { code: "603288", name: "海天味业" },
  { code: "600887", name: "伊利股份" },
  { code: "000895", name: "双汇发展" },
  { code: "002714", name: "牧原股份" },
  { code: "300498", name: "温氏股份" },
  // 地产/建材
  { code: "000002", name: "万科A" },
  { code: "001979", name: "招商蛇口" },
  { code: "600585", name: "海螺水泥" },
  { code: "002271", name: "东方雨虹" },
  // 公用事业/交运
  { code: "600900", name: "长江电力" },
  { code: "600886", name: "国电电力" },
  { code: "601857", name: "中国石油" },
  { code: "600028", name: "中国石化" },
  // 汽车
  { code: "600104", name: "上汽集团" },
  { code: "000625", name: "长安汽车" },
  { code: "601238", name: "广汽集团" },
  { code: "002920", name: "德赛西威" },
  // 钢铁/有色
  { code: "600019", name: "宝钢股份" },
  { code: "601899", name: "紫金矿业" },
  { code: "002466", name: "天齐锂业" },
  { code: "002460", name: "赣锋锂业" },
  { code: "603993", name: "洛阳钼业" },
  // 军工
  { code: "600893", name: "航发动力" },
  { code: "000768", name: "中航西飞" },
  { code: "600760", name: "中航沈飞" },
  { code: "002179", name: "中航光电" },
  // 电子
  { code: "002475", name: "立讯精密" },
  { code: "300661", name: "圣邦股份" },
  { code: "603986", name: "兆易创新" },
  { code: "300782", name: "׿ʤ΢" },
  // 通信
  { code: "600031", name: "三一重工" },
  { code: "000963", name: "华东医药" },
  { code: "601138", name: "工业富联" },
  { code: "002352", name: "顺丰控股" },
  // 化工
  { code: "600309", name: "万华化学" },
  { code: "002129", name: "中环股份" },
  { code: "000661", name: "长春高新" },
  { code: "600436", name: "片仔癀" },
  // 传媒/游戏
  { code: "002602", name: "世纪华通" },
  { code: "300413", name: "芒果超媒" },
  // 机械
  { code: "601100", name: "恒立液压" },
  { code: "300124", name: "汇川技术" },
  { code: "002008", name: "大族激光" },
  // 食品饮料
  { code: "600600", name: "青岛啤酒" },
  { code: "002507", name: "涪陵榨菜" },
  // 纺织服装
  { code: "603868", name: "飞科电器" },
  // 环保
  { code: "300070", name: "碧水源" },
  // 物流
  { code: "002120", name: "韵达股份" },
  // 计算机
  { code: "000977", name: "浪潮信息" },
  { code: "002236", name: "大华股份" },
  // 大盘蓝筹补充
  { code: "600050", name: "中国联通" },
  { code: "601668", name: "中国建筑" },
  { code: "601288", name: "农业银行" },
  { code: "601988", name: "中国银行" },
  { code: "600009", name: "上海机场" },
  { code: "600406", name: "国电南瑞" },
  { code: "601888", name: "中国中免" },
  { code: "300059", name: "东方财富" },
  { code: "600048", name: "保利发展" },
  { code: "601225", name: "陕西煤业" },
  { code: "600089", name: "特变电工" },
  { code: "002812", name: "恩捷股份" },
];

// If a specific code is passed (not a flag), run single stock
const singleArg = process.argv.find((a, i) => i >= 2 && !a.startsWith("--"));
const stocks = singleArg ? [{ code: singleArg, name: singleArg }] : defaultStocks;

const strategies = [
  { id: "dual-ma-crossover", short: "DualMA" },
  { id: "momentum-breakout", short: "Momentum" },
  { id: "rsi-filter", short: "RSI" },
  { id: "macd-confirmation", short: "MACD" },
  { id: "composite", short: "Composite" },
  { id: "scored", short: "Scored" },
  { id: "enhanced-composite", short: "Enhanced" },
  { id: "rsi-enhanced", short: "RSI+" },
  { id: "macd-divergence", short: "MACDDiv" },
  { id: "rsi-macd-combined", short: "RSI+MACD" },
];

// Provider config
const providerConfig: Record<string, unknown> = {};
if (dataProvider) {
  providerConfig.dataProvider = dataProvider;
} else {
  providerConfig.tradingAccount = {
    broker: "remote-qmt",
    qmt: { windowsBridgeUrl: "http://192.168.3.30:18888" },
  };
}

console.log(`\n数据源: ${dataProvider ?? "qmt-fallback (auto)"}`);
console.log(`标的数: ${stocks.length}`);
console.log(`策略数: ${strategies.length}\n`);

// Collect all results: { stockCode: { strategyId: result } }
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
};
const rows: Row[] = [];

for (const stock of stocks) {
  console.log(`▶ ${stock.code} ${stock.name} ...`);
  for (const strat of strategies) {
    try {
      const result = await runBacktest(
        {
          strategyId: strat.id,
          symbols: [stock.code],
          startDate: "2024-01-01",
          endDate: "2025-03-22",
          initialCash: 1000000,
          engine: "builtin",
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
      });
      const ret =
        result.totalReturn >= 0
          ? `+${result.totalReturn.toFixed(1)}%`
          : `${result.totalReturn.toFixed(1)}%`;
      process.stdout.write(
        `  ${strat.short.padEnd(10)} ${ret.padStart(8)}  DD ${result.maxDrawdown.toFixed(1).padStart(5)}%  WR ${result.winRate.toFixed(0).padStart(3)}%  T=${result.totalTrades}\n`,
      );
    } catch {
      process.stdout.write(`  ${strat.short.padEnd(10)}   ERROR\n`);
    }
  }
  console.log();
}

// ── Summary table ──
console.log("\n" + "═".repeat(110));
console.log("  综合排行榜 — 按策略汇总（所有标的平均）");
console.log("═".repeat(110));

const stratSummary = strategies.map((s) => {
  const sRows = rows.filter((r) => r.strategy === s.short);
  const n = sRows.length || 1;
  return {
    strategy: s.short,
    avgReturn: sRows.reduce((a, r) => a + r.totalReturn, 0) / n,
    avgDrawdown: sRows.reduce((a, r) => a + r.maxDrawdown, 0) / n,
    avgWinRate: sRows.reduce((a, r) => a + r.winRate, 0) / n,
    avgSharpe: sRows.reduce((a, r) => a + r.sharpe, 0) / n,
    totalTrades: sRows.reduce((a, r) => a + r.trades, 0),
    winCount: sRows.filter((r) => r.totalReturn > 0).length,
    stockCount: sRows.length,
  };
});
stratSummary.sort((a, b) => b.avgReturn - a.avgReturn);

console.log(
  `  ${"策略".padEnd(12)} ${"平均收益".padStart(8)} ${"平均回撤".padStart(8)} ${"平均胜率".padStart(8)} ${"平均夏普".padStart(8)} ${"盈利票数".padStart(8)} ${"总交易".padStart(6)}`,
);
console.log("─".repeat(110));
for (const s of stratSummary) {
  const ret = s.avgReturn >= 0 ? `+${s.avgReturn.toFixed(2)}%` : `${s.avgReturn.toFixed(2)}%`;
  console.log(
    `  ${s.strategy.padEnd(12)} ${ret.padStart(8)} ${(s.avgDrawdown.toFixed(2) + "%").padStart(8)} ${(s.avgWinRate.toFixed(1) + "%").padStart(8)} ${s.avgSharpe.toFixed(3).padStart(8)} ${(s.winCount + "/" + s.stockCount).padStart(8)} ${String(s.totalTrades).padStart(6)}`,
  );
}
console.log("═".repeat(110));

// ── Per-stock best strategy ──
console.log("\n  各标的最优策略:");
console.log("─".repeat(70));
for (const stock of stocks) {
  const sRows = rows.filter((r) => r.stock === stock.code);
  if (sRows.length === 0) continue;
  const best = sRows.reduce((a, b) => (a.totalReturn > b.totalReturn ? a : b));
  const ret =
    best.totalReturn >= 0 ? `+${best.totalReturn.toFixed(2)}%` : `${best.totalReturn.toFixed(2)}%`;
  console.log(
    `  ${stock.code} ${stock.name.padEnd(16)} → ${best.strategy.padEnd(10)} ${ret}  DD ${best.maxDrawdown.toFixed(1)}%  WR ${best.winRate.toFixed(0)}%`,
  );
}
console.log();

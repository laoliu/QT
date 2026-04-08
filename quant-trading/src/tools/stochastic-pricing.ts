/**
 * stochastic_pricing tool — AI-callable tool for stochastic pricing,
 * numerical methods, hedging simulation, risk measurement, and volatility modeling.
 *
 * Covers:
 *   - Monte Carlo option pricing (European, Asian, Barrier)
 *   - Binomial tree option pricing (CRR model)
 *   - Delta hedging simulation & frequency analysis
 *   - VaR/CVaR risk measurement (parametric, historical, Monte Carlo)
 *   - Volatility models: SABR calibration, SVI fitting, Dupire local vol, IV surface interpolation
 *
 * Engine delegates to Python:
 *   monte_carlo_engine.py, binomial_tree.py, delta_hedge_sim.py, var_engine.py, vol_models.py
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createStochasticPricingTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "stochastic_pricing",
    description:
      "随机定价与风险分析工具：蒙特卡洛模拟定价、二叉树定价、Delta对冲模拟、VaR/CVaR风险度量。\n\n" +
      "engine=monte_carlo:\n" +
      "  action=price_european 欧式期权MC定价（与BSM对比）\n" +
      "  action=price_asian 亚式期权MC定价（算术/几何平均）\n" +
      "  action=price_barrier 障碍期权MC定价（敲入/敲出）\n" +
      "  action=greeks 通过有限差分计算Greek字母\n" +
      "  action=path_stats GBM路径统计分析\n" +
      "  action=simulate 生成可视化路径数据\n\n" +
      "engine=binomial_tree:\n" +
      "  action=price CRR二叉树定价（欧式/美式）\n" +
      "  action=convergence 收敛性分析\n" +
      "  action=compare ŷʽvs美式对比\n\n" +
      "engine=delta_hedge:\n" +
      "  action=simulate 单路径Delta对冲模拟\n" +
      "  action=ensemble 多路径P&L分布分析\n" +
      "  action=frequency 对冲频率权衡分析\n\n" +
      "engine=var:\n" +
      "  action=parametric 参数法VaR/CVaR\n" +
      "  action=historical 历史模拟法VaR/CVaR\n" +
      "  action=monte_carlo 蒙特卡洛VaR/CVaR\n" +
      "  action=stress_test 压力测试\n" +
      "  action=backtest VaR回测（Kupiec检验）\n" +
      "  action=compare 三种方法对比\n\n" +
      "engine=vol_models:\n" +
      "  action=sabr_calibrate SABR模型校准（拟合市场微笑）\n" +
      "  action=sabr_smile 从SABR参数生成波动率微笑曲线\n" +
      "  action=sabr_price 单行权价SABR隐含波动率\n" +
      "  action=svi_calibrate SVI参数化校准\n" +
      "  action=dupire Dupire局部波动率提取\n" +
      "  action=interpolate IV曲面插值与平滑\n" +
      "  action=term_structure ATM波动率期限结构分析",
    parameters: {
      type: "object" as const,
      properties: {
        engine: {
          type: "string",
          enum: ["monte_carlo", "binomial_tree", "delta_hedge", "var", "vol_models"],
          description:
            "计算引擎: monte_carlo=蒙特卡洛模拟, binomial_tree=二叉树, " +
            "delta_hedge=Delta对冲模拟, var=VaR风险度量, vol_models=波动率模型",
        },
        action: {
          type: "string",
          description: "操作类型（每个引擎支持不同的action，见工具描述）",
        },
        // Common option parameters
        spotPrice: {
          type: "number",
          description: "标的现价",
        },
        strike: {
          type: "number",
          description: "行权价",
        },
        timeToExpiry: {
          type: "number",
          description: "到期时间（年），如0.25=3个月",
        },
        volatility: {
          type: "number",
          description: "波动率，如0.2=20%",
        },
        riskFreeRate: {
          type: "number",
          description: "无风险利率，默认0.02",
        },
        optionType: {
          type: "string",
          enum: ["call", "put"],
          description: "期权类型",
        },
        dividendYield: {
          type: "number",
          description: "股息率",
        },
        exerciseType: {
          type: "string",
          enum: ["european", "american"],
          description: "行权方式（二叉树专用）",
        },
        // Monte Carlo specific
        nPaths: {
          type: "number",
          description: "模拟路径数（默认100000，最大1000000）",
        },
        nSteps: {
          type: "number",
          description: "时间步数（默认252=日度，最大1000）",
        },
        antithetic: {
          type: "boolean",
          description: "是否使用对偶变量法减小方差（默认true）",
        },
        controlVariate: {
          type: "boolean",
          description: "是否使用控制变量法减小方差",
        },
        // Asian option specific
        averageType: {
          type: "string",
          enum: ["arithmetic", "geometric"],
          description: "亚式期权平均方式",
        },
        // Barrier option specific
        barrier: {
          type: "number",
          description: "障碍价格",
        },
        barrierType: {
          type: "string",
          enum: ["up-and-out", "up-and-in", "down-and-out", "down-and-in"],
          description: "障碍类型",
        },
        // Delta hedge specific
        position: {
          type: "string",
          enum: ["short", "long"],
          description: "持仓方向：short=卖出期权并对冲，long=买入期权并对冲",
        },
        hedgeFreq: {
          type: "number",
          description: "ÿN步对冲一次（1=每步，5=每周）",
        },
        transactionCost: {
          type: "number",
          description: "每股交易成本",
        },
        realizedVol: {
          type: "number",
          description: "实际波动率（用于vol mismatch分析）",
        },
        nSimulations: {
          type: "number",
          description: "模拟次数（ensemble/frequency分析用）",
        },
        // VaR specific
        returns: {
          type: "array",
          items: { type: "number" },
          description: "收益率序列（日收益率数组）",
        },
        prices: {
          type: "array",
          items: { type: "number" },
          description: "价格序列（自动计算对数收益率）",
        },
        confidence: {
          type: "number",
          description: "置信水平（默认0.95=95%）",
        },
        holdingPeriod: {
          type: "number",
          description: "持有期（天，默认1）",
        },
        portfolioValue: {
          type: "number",
          description: "投资组合价值（默认1000000）",
        },
        // Stress test specific
        positions: {
          type: "array",
          items: { type: "object" },
          description: "持仓列表 [{name, weight, beta}, ...]",
        },
        scenarios: {
          type: "array",
          items: { type: "object" },
          description: "压力情景 [{name, marketReturn, volShock}, ...]",
        },
        seed: {
          type: "number",
          description: "随机种子（用于可重复模拟）",
        },
        // Vol models specific
        forwardPrice: {
          type: "number",
          description: "远期价格（SABR/SVI/期限结构用）",
        },
        strikes: {
          type: "array",
          items: { type: "number" },
          description: "行权价数组（波动率校准用）",
        },
        marketVols: {
          type: "array",
          items: { type: "number" },
          description: "市场隐含波动率数组（与strikes一一对应）",
        },
        beta: {
          type: "number",
          description: "SABR β参数（CEV指数，0=正态，0.5=默认，1=对数正态）",
        },
        alpha: {
          type: "number",
          description: "SABR α参数（初始波动率水平）",
        },
        rho: {
          type: "number",
          description: "SABR ρ参数（价格-波动率相关性，-1<ρ<1）",
        },
        nu: {
          type: "number",
          description: "SABR ν参数（vol-of-vol）",
        },
        strikeRange: {
          type: "array",
          items: { type: "number" },
          description: "行权价范围 [low, high]（生成微笑曲线用）",
        },
        nPoints: {
          type: "number",
          description: "微笑曲线点数（默认50）",
        },
        expiries: {
          type: "array",
          items: { type: "number" },
          description: "到期时间数组（年，Dupire/插值/期限结构用）",
        },
        ivMatrix: {
          type: "array",
          items: { type: "array", items: { type: "number" } },
          description: "IV矩阵 [expiry_idx][strike_idx]（Dupire/插值用）",
        },
        targetStrikes: {
          type: "array",
          items: { type: "number" },
          description: "目标行权价（插值用）",
        },
        targetExpiries: {
          type: "array",
          items: { type: "number" },
          description: "目标到期时间（插值用）",
        },
        atmVols: {
          type: "array",
          items: { type: "number" },
          description: "ATM波动率数组（期限结构分析用）",
        },
      },
      required: ["engine", "action"],
    },

    async execute(args: Record<string, unknown>) {
      const engine = args.engine as string;
      const action = args.action as string;

      // Determine which Python engine to call
      const engineMap: Record<string, string> = {
        monte_carlo: "engines/monte_carlo_engine.py",
        binomial_tree: "engines/binomial_tree.py",
        delta_hedge: "engines/delta_hedge_sim.py",
        var: "engines/var_engine.py",
        vol_models: "engines/vol_models.py",
      };

      const scriptPath = engineMap[engine];
      if (!scriptPath) {
        return { error: `未知引擎: ${engine}，可选: ${Object.keys(engineMap).join(", ")}` };
      }

      // Forward all args to the Python engine
      const pythonArgs: Record<string, unknown> = { action };

      // Map common parameters
      const paramKeys = [
        "spotPrice",
        "strike",
        "timeToExpiry",
        "volatility",
        "riskFreeRate",
        "optionType",
        "dividendYield",
        "exerciseType",
        "nPaths",
        "nSteps",
        "antithetic",
        "controlVariate",
        "averageType",
        "barrier",
        "barrierType",
        "position",
        "hedgeFreq",
        "transactionCost",
        "realizedVol",
        "nSimulations",
        "returns",
        "prices",
        "confidence",
        "holdingPeriod",
        "portfolioValue",
        "positions",
        "scenarios",
        "seed",
        "productType",
        "frequencies",
        "assetsReturns",
        "weights",
        "stepRange",
        "forwardPrice",
        "strikes",
        "marketVols",
        "beta",
        "alpha",
        "rho",
        "nu",
        "strikeRange",
        "nPoints",
        "expiries",
        "ivMatrix",
        "targetStrikes",
        "targetExpiries",
        "atmVols",
      ];

      for (const key of paramKeys) {
        if (args[key] !== undefined) {
          pythonArgs[key] = args[key];
        }
      }

      const result = await runPythonScript<Record<string, unknown>>(scriptPath, pythonArgs, config);

      if (!result.ok || !result.data) {
        return { error: result.error ?? `${engine} 引擎执行失败` };
      }

      return result.data;
    },
  };
}

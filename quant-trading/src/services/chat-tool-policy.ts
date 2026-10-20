import type { ChatTool } from "./ai-chat.js";

type Policy = { actions?: readonly string[]; force?: Record<string, unknown>; omit?: readonly string[] };

// Both the model schema and execution enforce this policy. New tools/actions
// never become available just because the global registry gained them.
export const CHAT_TOOL_POLICIES: Record<string, Policy> = {
  market_data: { actions: ["quote", "klines", "search", "fundamentals", "tick"] },
  portfolio: { actions: ["show", "summary", "live_orders", "live_trades", "daily_pnl"] },
  risk_analysis: { actions: ["portfolio_risk", "position_risk", "backtest_risk", "earnings_risk", "cross_portfolio_risk"] },
  fund_flow: { actions: ["stock", "sector", "northbound", "rank"] },
  dragon_tiger: { actions: ["daily", "stock", "institution", "desk", "hot_money"] },
  lianban_analysis: { actions: ["scan", "candidates", "detail"] },
  sector_analysis: { actions: ["ranking", "detail", "history", "rotation", "momentum", "hot"] },
  sector_synergy: { actions: ["scan", "hot", "analyze", "config"] },
  options_data: { actions: ["chain", "quote", "contracts", "klines", "iv_surface", "greeks", "search"] },
  stochastic_pricing: { actions: ["price_european", "price_asian", "price_barrier", "greeks", "path_stats", "simulate", "price", "convergence", "compare", "ensemble", "frequency", "parametric", "historical", "monte_carlo", "stress_test", "backtest", "sabr_calibrate", "sabr_smile", "sabr_price", "svi_calibrate", "dupire", "interpolate", "term_structure"] },
  options_strategy: { actions: ["recommend", "payoff", "scenario", "compare", "optimal_strike", "greeks", "list"] },
  news_summary: { actions: ["market_news", "stock_news", "sentiment", "events"] },
  macro_data: { actions: ["indicators", "history", "calendar", "regime"] },
  earnings_calendar: { actions: ["upcoming", "stock_forecast", "surprise_history", "analyst_ratings"] },
  institutional_flow: { actions: ["fund_holdings", "holding_changes", "block_trades", "insider_trades", "smart_money_signal"] },
  social_sentiment: { actions: ["hot_stocks", "stock_sentiment", "sentiment_history", "retail_flow"] },
  chip_accumulation: { actions: ["detect", "scan", "detail", "ambush_scan"] },
  value_investing: { actions: ["intrinsic_value", "moat", "quality_score", "margin_of_safety", "comprehensive"] },
  financial_calculator: { actions: ["dcf", "ddm", "pe_valuation", "pb_valuation", "ps_valuation", "ev_ebitda", "peg", "wacc", "compare", "tax_calculation"] },
  correlation_analysis: { actions: ["pair", "matrix", "find_pairs", "spread"] },
  tech_moat: { actions: ["analyze", "compare"] },
  cfo_assistant: { actions: ["three_statements", "dupont", "ratios", "peer_compare", "trend", "red_flags", "comprehensive"] },
  data_analysis: { actions: ["statistics", "cross_market", "regression", "distribution", "anomaly", "regime", "macro", "macro_correlation", "sentiment_backtest"] },
  stock_screen: { actions: ["screen", "rank", "rps", "run_screener", "list_screeners", "peer_compare", "scan_history", "scan_compare", "stock_frequency"], force: { persist_results: false } },
  stock_recommend: { force: { persist_results: false } },
  backtest: { actions: ["list", "run"], force: { persist_results: false } },
  aipha_backtest: { actions: ["run"] },
  portfolio_backtest: { actions: ["run", "compare", "summary"] },
  strategy_router: { actions: ["profile", "recommend", "recommend_with_regime", "route_portfolio", "profile_route_backtest", "routing_table", "enrich_with_routing", "get_regime"] },
  strategy_explorer: { actions: ["explore", "recommend"], force: { saveBest: false } },
  strategy_optimizer: { actions: ["optimize", "validate_signals"] },
  position_sizing: { actions: ["compute", "config"], force: { require_equity: true } },
  portfolio_optimizer: { actions: ["optimize", "combine_strategies", "detect_regime"] },
  portfolio_rebalance: { actions: ["plan"] },
  performance_attribution: { actions: ["full_report", "by_strategy", "by_symbol", "by_period", "chart_data", "text_summary", "html_summary"] },
  recommend_tracker: { actions: ["list", "stats", "detail", "review"] },
  trade_journal: { actions: ["view", "search", "recent", "summary", "tags"] },
  trading_signal: { actions: ["recent", "by_strategy", "by_symbol", "stats", "hit_rate"] },
  watchlist: { actions: ["list", "batch_quote"] },
  favorites: { actions: ["list", "search", "tags", "categories"] },
  strategy_health: { actions: ["check_all", "check", "list"] },
  factor_analysis: { actions: ["list", "compute"] },
  ml_ranking: { actions: ["predict", "evaluate", "list_models"], omit: ["outputPath", "outputDir", "planPath", "prices", "accountValue", "confirm"] },
  cb_analysis: { actions: ["list_bonds", "metrics", "factors", "select", "delta_zone"] },
  strategy_validation: { actions: ["quality_gates", "walk_forward", "pbo"] },
  data_cache: { actions: ["stats"] },
  research_operations: { actions: ["overview", "predictions"] },
};

export function createChatTools(tools: ChatTool[]): Map<string, ChatTool> {
  const result = new Map<string, ChatTool>();
  for (const tool of tools) {
    if (!Object.hasOwn(CHAT_TOOL_POLICIES, tool.name)) continue;
    const policy = CHAT_TOOL_POLICIES[tool.name];
    if (!policy) continue;
    const parameters = structuredClone(tool.parameters ?? { type: "object", properties: {} });
    const properties = (parameters.properties ?? {}) as Record<string, Record<string, unknown>>;
    const advertised = properties.action?.enum;
    const actions = policy.actions?.filter((a) => !Array.isArray(advertised) || advertised.includes(a));
    if (actions && !actions.length) continue;
    if (actions) properties.action = { type: "string", enum: actions, description: "可用查询/分析操作" };
    for (const key of [...Object.keys(policy.force ?? {}), ...(policy.omit ?? [])]) delete properties[key];
    parameters.properties = properties;
    parameters.additionalProperties = false;
    const required = Array.isArray(parameters.required) ? parameters.required as string[] : [];
    parameters.required = [...new Set([...required.filter((key) => key in properties), ...(actions ? ["action"] : [])])];
    result.set(tool.name, {
      ...tool, parameters,
      description: `${(tool.description ?? tool.name).split(/\n|•|action=/)[0]!.trim()}${actions ? ` 可用操作：${actions.join(", ")}。` : ""}仅查询和分析；不会执行交易、训练或修改策略配置。`,
      execute: async (args) => {
        if (!args || typeof args !== "object" || Array.isArray(args)) throw new Error("工具参数必须是对象");
        if (actions && (typeof args.action !== "string" || !actions.includes(args.action))) throw new Error(`${tool.name} 未授权的 action；仅允许：${actions.join(", ")}`);
        for (const key of Object.keys(args)) {
          if (!Object.hasOwn(properties, key) && !Object.hasOwn(policy.force ?? {}, key)) throw new Error(`${tool.name} 不支持参数：${key}`);
        }
        for (const key of parameters.required as string[]) {
          if (args[key] == null) throw new Error(`${tool.name} 缺少参数：${key}`);
        }
        for (const [key, value] of Object.entries(args)) {
          const schema = properties[key];
          if (schema && Array.isArray(schema.enum) && !schema.enum.includes(value)) throw new Error(`${tool.name} 参数 ${key} 不在可用范围内`);
        }
        return tool.execute({ ...args, ...policy.force });
      },
    });
  }
  return result;
}

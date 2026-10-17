/**
 * breakout_monitor tool — AI-callable for compound breakout entry automation.
 *
 * Actions:
 *   add_setup      — Add a breakout monitoring setup
 *   list_setups    — List active/all setups
 *   cancel_setup   — Cancel a setup
 *   add_eod_rule   — Add an end-of-day MA exit rule
 *   list_eod_rules — List EOD exit rules
 *   remove_eod_rule— Remove an EOD exit rule
 */
import type { QuantTradingRuntime } from "../runtime.js";
import type { BreakoutMonitor, VolumeCondition } from "../services/breakout-monitor.js";
import type { QuantTradingConfig } from "../types.js";

export function createBreakoutMonitorTool(
  _api: QuantTradingRuntime,
  _config: QuantTradingConfig,
  monitor: BreakoutMonitor,
) {
  return {
    name: "breakout_monitor",
    description:
      "突破监控工具。设置复合条件突破入场自动化（价格突破+量能确认+持续确认+时间窗口），以及收盘均线检查自动退出。\n" +
      "• action=add_setup — 新增突破监控\n" +
      "  支持: 价格触发线、量能确认（5分钟量>30分钟均量×N倍）、持续确认（保持N个周期）、\n" +
      "  时间窗口、止损价、回踩入场模式、下单方式(市价/限价/止损限价)\n" +
      "• action=list_setups — 查看监控列表\n" +
      "• action=cancel_setup — 取消监控\n" +
      "• action=add_eod_rule — 新增收盘均线检查规则（收盘跌破均线自动退出）\n" +
      "• action=list_eod_rules — 查看收盘检查规则\n" +
      "• action=remove_eod_rule — 删除收盘检查规则",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "add_setup",
            "list_setups",
            "cancel_setup",
            "add_eod_rule",
            "list_eod_rules",
            "remove_eod_rule",
          ],
          description: "操作类型",
        },
        // ── add_setup params ──
        symbol: {
          type: "string",
          description: "股票代码，如 600979",
        },
        trigger_price: {
          type: "number",
          description: "突破触发价格 (price >= trigger_price 时触发)",
        },
        buffer_pct: {
          type: "number",
          description: "入场价格缓冲（默认0.002=+0.2%），实际买入价=trigger_price×(1+buffer_pct)",
        },
        volume_min_ratio: {
          type: "number",
          description: "量能确认: 最近N分钟成交量/前段均量 >= 此倍数才触发 (如1.5=放量1.5倍)",
        },
        volume_period_minutes: {
          type: "number",
          description: "量能确认: 最近时段分钟数 (默认5=最近5分钟)",
        },
        volume_baseline_periods: {
          type: "number",
          description: "量能确认: 基准平均的时段数 (默认6, 5分钟×6=30分钟基准)",
        },
        hold_checks: {
          type: "number",
          description: "持续确认: 价格需保持在触发线上方N个检查周期 (默认1=无需额外确认)",
        },
        time_windows: {
          type: "string",
          description: '时间窗口限制 (逗号分隔)，如 "09:45-10:30,14:00-15:00"',
        },
        order_type: {
          type: "string",
          enum: ["market", "limit", "stop_limit"],
          description: "下单方式 (默认limit)",
        },
        quantity: {
          type: "number",
          description: "买入数量 (股)",
        },
        stop_loss_price: {
          type: "number",
          description: "止损价格 (可选)",
        },
        enable_pullback: {
          type: "boolean",
          description: "启用回踩入场模式: 首次突破后等价格回踩再入场 (默认false)",
        },
        pullback_pct: {
          type: "number",
          description: "回踩幅度 (默认0.005=0.5%)，价格回落至trigger_price×(1+pullback_pct)处入场",
        },
        expires_hours: {
          type: "number",
          description: "过期时间 (Сʱ)，超过后自动失效",
        },
        description: {
          type: "string",
          description: "自定义描述",
        },
        // ── cancel_setup ──
        setup_id: {
          type: "string",
          description: "监控ID (action=cancel_setup时必填)",
        },
        // ── add_eod_rule params ──
        ma_period: {
          type: "number",
          description: "收盘检查: 均线周期 (如10=10日均线)",
        },
        ma_type: {
          type: "string",
          enum: ["sma", "ema"],
          description: "均线类型 (默认sma)",
        },
        ma_buffer_pct: {
          type: "number",
          description: "收盘检查: 跌破均线缓冲 (默认0=精确跌破)",
        },
        exit_action: {
          type: "string",
          enum: ["close_all", "reduce_half"],
          description: "收盘操作: close_all=清仓, reduce_half=减半 (默认close_all)",
        },
        // ── remove_eod_rule ──
        rule_id: {
          type: "string",
          description: "EOD规则ID (action=remove_eod_rule时必填)",
        },
        // ── list ──
        active_only: {
          type: "boolean",
          description: "仅显示活跃项 (默认false)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "add_setup": {
          const symbol = args.symbol as string;
          const triggerPrice = args.trigger_price as number;
          const quantity = args.quantity as number;

          if (!symbol) return { error: "请提供股票代码 (symbol)" };
          if (!triggerPrice) return { error: "请提供触发价格 (trigger_price)" };
          if (!quantity) return { error: "请提供买入数量 (quantity)" };

          let volumeCondition: VolumeCondition | undefined;
          if (args.volume_min_ratio != null) {
            volumeCondition = {
              minRatio: args.volume_min_ratio as number,
              periodMinutes: (args.volume_period_minutes as number) ?? 5,
              baselinePeriods: (args.volume_baseline_periods as number) ?? 6,
            };
          }

          const timeWindows = args.time_windows
            ? (args.time_windows as string).split(",").map((s) => s.trim())
            : undefined;

          let expiresAt: string | undefined;
          if (args.expires_hours != null) {
            expiresAt = new Date(
              Date.now() + (args.expires_hours as number) * 3600_000,
            ).toISOString();
          }

          try {
            const id = monitor.addSetup({
              symbol,
              triggerPrice,
              description: args.description as string | undefined,
              bufferPct: args.buffer_pct as number | undefined,
              volumeCondition,
              holdChecks: args.hold_checks as number | undefined,
              timeWindows,
              orderType: args.order_type as "market" | "limit" | "stop_limit" | undefined,
              quantity,
              stopLossPrice: args.stop_loss_price as number | undefined,
              enablePullback: args.enable_pullback as boolean | undefined,
              pullbackPct: args.pullback_pct as number | undefined,
              expiresAt,
            });

            const setup = monitor.listSetups().find((s) => s.id === id);
            return {
              action: "add_setup",
              setupId: id,
              symbol,
              triggerPrice,
              quantity,
              hasVolumeCondition: !!volumeCondition,
              holdChecks: setup?.holdChecks ?? 1,
              timeWindows: timeWindows ?? "all",
              orderType: setup?.orderType ?? "limit",
              enablePullback: setup?.enablePullback ?? false,
              message: `✅ 突破监控已设置: ${setup?.description ?? id}${volumeCondition ? ` (需放量${volumeCondition.minRatio}倍)` : ""}`,
            };
          } catch (err) {
            return { error: err instanceof Error ? err.message : String(err) };
          }
        }

        case "list_setups": {
          const activeOnly = (args.active_only as boolean) ?? false;
          const setups = monitor.listSetups(activeOnly);
          return {
            action: "list_setups",
            count: setups.length,
            setups: setups.map((s) => ({
              id: s.id,
              symbol: s.symbol,
              triggerPrice: s.triggerPrice,
              status: s.status,
              description: s.description,
              holdChecks: s.holdChecks,
              currentHoldCount: s.currentHoldCount,
              hasVolumeCondition: !!s.volumeCondition,
              volumeMinRatio: s.volumeCondition?.minRatio,
              enablePullback: s.enablePullback,
              orderType: s.orderType,
              quantity: s.quantity,
              createdAt: s.createdAt,
              triggeredAt: s.triggeredAt,
              orderResult: s.orderResult,
            })),
          };
        }

        case "cancel_setup": {
          const setupId = args.setup_id as string;
          if (!setupId) return { error: "请提供监控ID (setup_id)" };
          const ok = monitor.cancelSetup(setupId);
          return ok
            ? { action: "cancel_setup", setupId, message: `🗑️ 突破监控 ${setupId} 已取消` }
            : { error: `未找到可取消的监控 ${setupId}` };
        }

        case "add_eod_rule": {
          const symbol = args.symbol as string;
          const maPeriod = args.ma_period as number;
          if (!symbol) return { error: "请提供股票代码 (symbol)" };
          if (!maPeriod) return { error: "请提供均线周期 (ma_period)" };

          const id = monitor.addEodRule({
            symbol,
            maPeriod,
            maType: args.ma_type as "sma" | "ema" | undefined,
            bufferPct: args.ma_buffer_pct as number | undefined,
            exitAction: args.exit_action as "close_all" | "reduce_half" | undefined,
            description: args.description as string | undefined,
          });

          const rule = monitor.listEodRules().find((r) => r.id === id);
          return {
            action: "add_eod_rule",
            ruleId: id,
            symbol,
            maPeriod,
            exitAction: rule?.exitAction ?? "close_all",
            message: `✅ 收盘检查已设置: ${rule?.description ?? id}`,
          };
        }

        case "list_eod_rules": {
          const rules = monitor.listEodRules();
          return {
            action: "list_eod_rules",
            count: rules.length,
            rules: rules.map((r) => ({
              id: r.id,
              symbol: r.symbol,
              maPeriod: r.maPeriod,
              maType: r.maType,
              bufferPct: r.bufferPct,
              exitAction: r.exitAction,
              active: r.active,
              description: r.description,
            })),
          };
        }

        case "remove_eod_rule": {
          const ruleId = args.rule_id as string;
          if (!ruleId) return { error: "请提供规则ID (rule_id)" };
          const ok = monitor.removeEodRule(ruleId);
          return ok
            ? { action: "remove_eod_rule", ruleId, message: `🗑️ 收盘检查规则 ${ruleId} 已删除` }
            : { error: `未找到规则 ${ruleId}` };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

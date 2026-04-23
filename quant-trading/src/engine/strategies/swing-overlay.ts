/**
 * Swing Overlay Strategy (持仓波段套利).
 *
 * Hybrid grid + mean-reversion strategy designed for oscillating uptrend
 * stocks. Maintains a core position and uses a swing portion for
 * buy-low/sell-high trades.
 *
 * Features:
 *   - Dynamic grid center follows MA (adapts to uptrend)
 *   - BIAS-based entry/exit with volume confirmation
 *   - T+1 aware (no same-bar sell after buy)
 *   - Core/swing position split
 *
 *   swingOverlay(coreRatio, gridPct, gridCount, biasBuy, biasSell, volMult, maxHoldBars, maPeriod)
 */
import type { BuiltinStrategy } from "./indicators.js";
import { getCloses, sma } from "./indicators.js";

/**
 * 持仓波段套利策略
 *
 * Split position into core (untouched) and swing (trade actively).
 * Grid center follows MA to adapt to trending markets.
 * Buy when BIAS oversold or price hits lower grid; sell at upper grid or BIAS overbought.
 */
export function swingOverlay(
  coreRatio = 0.6,
  gridPct = 3.0,
  gridCount = 3,
  biasBuy = -5.0,
  biasSell = 3.5,
  volMult = 1.5,
  maxHoldBars = 8,
  maPeriod = 20,
): BuiltinStrategy {
  let maValues: number[] = [];
  let volMaValues: number[] = [];

  // State
  let direction = 0; // 1 = holding swing longs, 0 = swing portion is flat
  let entryBar = -1;
  let entryPrice = 0;
  let roundTrips = 0;

  function buildGridLevels(center: number): number[] {
    const pct = gridPct / 100;
    const levels: number[] = [];
    for (let i = -gridCount; i <= gridCount; i++) {
      const level = center * (1 + i * pct);
      if (level > 0) levels.push(level);
    }
    return levels.sort((a, b) => a - b);
  }

  function findGridIndex(price: number, levels: number[]): number {
    for (let i = levels.length - 1; i >= 0; i--) {
      if (price >= levels[i]!) return i;
    }
    return 0;
  }

  return {
    name: `持仓波段(核心${(coreRatio * 100).toFixed(0)}%,格距${gridPct}%,${gridCount}层,BIAS[${biasBuy}/${biasSell}])`,
    init(ctx) {
      const closes = getCloses(ctx);
      maValues = sma(closes, maPeriod);
      // Compute volume MA
      const volumes = ctx.bars.map((b) => b.volume);
      volMaValues = sma(volumes, 20);
      direction = 0;
      entryBar = -1;
      entryPrice = 0;
      roundTrips = 0;
    },
    next(ctx) {
      const i = ctx.index;
      const bar = ctx.bars[i]!;
      const close = bar.close;
      const ma = maValues[i]!;

      if (Number.isNaN(ma) || ma <= 0 || i < maPeriod) return null;

      // Compute BIAS
      const bias = ((close - ma) / ma) * 100;

      // Volume spike check
      const volMa = volMaValues[i] ?? 0;
      const isVolSpike = volMa > 0 && bar.volume >= volMa * volMult;

      // Dynamic grid levels centered on MA
      const gridLevels = buildGridLevels(ma);
      const centerIdx = Math.floor(gridLevels.length / 2);
      const gridIdx = findGridIndex(close, gridLevels);

      // ── SELL (高抛) ── swing portion exits
      if (direction === 1) {
        // T+1: cannot sell on the same bar as entry
        if (i <= entryBar) return null;

        const holdBars = i - entryBar;
        let sellReason = "";

        if (bias >= biasSell) {
          sellReason = `BIAS高抛: BIAS=${bias.toFixed(2)}% ≥ ${biasSell}%`;
        } else if (gridIdx >= centerIdx + 1) {
          const gp = gridLevels[gridIdx] ?? close;
          sellReason = `网格高抛: 触及第${gridIdx - centerIdx}层上网格 ¥${gp.toFixed(2)}`;
        } else if (holdBars >= maxHoldBars) {
          sellReason = `持仓超时: ${holdBars}天 ≥ ${maxHoldBars}天`;
        }

        if (sellReason) {
          direction = 0;
          roundTrips++;
          const pnl = entryPrice > 0 ? ((close - entryPrice) / entryPrice) * 100 : 0;
          entryBar = -1;
          entryPrice = 0;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "sell" as const,
            price: close,
            reason: `[波段高抛] ${sellReason}, 损益${pnl >= 0 ? "+" : ""}${pnl.toFixed(1)}%, 往返第${roundTrips}次`,
            strategyId: "swing-overlay",
          };
        }
      }

      // ── BUY (低吸) ── swing portion enters
      if (direction === 0) {
        let buyReason = "";

        // Condition 1: BIAS oversold + volume spike
        if (bias <= biasBuy && isVolSpike) {
          const vr = volMa > 0 ? (bar.volume / volMa).toFixed(1) : "?";
          buyReason = `BIAS低吸: BIAS=${bias.toFixed(2)}% ≤ ${biasBuy}%, 量比=${vr}x`;
        }
        // Condition 2: Price at or below lower grid with mild volume
        else if (gridIdx <= centerIdx - 1 && volMa > 0 && bar.volume > volMa * 0.8) {
          const gp = gridLevels[gridIdx] ?? close;
          buyReason = `网格低吸: 触及第${centerIdx - gridIdx}层下网格 ¥${gp.toFixed(2)}`;
        }

        if (buyReason) {
          direction = 1;
          entryBar = i;
          entryPrice = close;
          return {
            timestamp: bar.timestamp,
            symbol: "",
            direction: "buy" as const,
            price: close,
            reason: `[波段低吸] ${buyReason}`,
            strategyId: "swing-overlay",
          };
        }
      }

      return null;
    },
  };
}

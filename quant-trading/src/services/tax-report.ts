/**
 * TaxReportService — tax calculation and cost tracking for trading.
 *
 * Provides:
 *   - Year-to-date realized capital gains/losses
 *   - Stamp duty and commission cost tracking
 *   - Annual tax summary report
 *   - Trade cost breakdown by period
 *
 * Reads from orders/trades JSON files and dividend records.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

export interface TradeCostSummary {
  /** Total commission paid. */
  totalCommission: number;
  /** Total stamp duty (印花税) paid. */
  totalStampDuty: number;
  /** Total transfer fees (过户费). */
  totalTransferFee: number;
  /** Total slippage cost. */
  totalSlippage: number;
  /** Total of all fees. */
  totalFees: number;
  /** Number of trades. */
  tradeCount: number;
  /** Average fee per trade. */
  avgFeePerTrade: number;
}

export interface RealizedGainRecord {
  symbol: string;
  buyDate: string;
  sellDate: string;
  buyPrice: number;
  sellPrice: number;
  quantity: number;
  grossPnl: number;
  fees: number;
  netPnl: number;
  holdingDays: number;
}

export interface TaxSummary {
  /** Period. */
  fromDate: string;
  toDate: string;
  /** Total realized gains. */
  totalGains: number;
  /** Total realized losses. */
  totalLosses: number;
  /** Net realized P&L. */
  netRealizedPnl: number;
  /** Total dividend income. */
  dividendIncome: number;
  /** Total fees paid. */
  totalFees: number;
  /** Stamp duty subtotal. */
  stampDutyTotal: number;
  /** Number of winning trades. */
  winCount: number;
  /** Number of losing trades. */
  lossCount: number;
  /** Win rate. */
  winRate: number;
  /** Top gainers. */
  topGainers: Array<{ symbol: string; netPnl: number }>;
  /** Top losers. */
  topLosers: Array<{ symbol: string; netPnl: number }>;
  /** Monthly breakdown. */
  monthlyPnl: Record<string, number>;
}

export interface TaxReportOptions {
  dataDir?: string | null;
}

interface TradeFileRecord {
  symbol?: string;
  code?: string;
  entryPrice?: number;
  exitPrice?: number;
  buyPrice?: number;
  sellPrice?: number;
  quantity?: number;
  size?: number;
  shares?: number;
  entryTime?: string;
  exitTime?: string;
  buyDate?: string;
  sellDate?: string;
  createdAt?: string;
  closedAt?: string;
  netPnl?: number;
  pnl?: number;
  profit?: number;
  commission?: number;
  fees?: number;
  stampDuty?: number;
  transferFee?: number;
  slippage?: number;
}

export class TaxReportService {
  private readonly dataDir: string;

  constructor(opts: TaxReportOptions = {}) {
    this.dataDir = opts.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  }

  /** Load trades from a trades JSON file. */
  private async loadTrades(mode: "paper" | "live"): Promise<TradeFileRecord[]> {
    const filePath = path.join(this.dataDir, `trades-${mode}.json`);
    try {
      const raw = await fs.readFile(filePath, "utf-8");
      const data = JSON.parse(raw);
      return Array.isArray(data) ? data : data.trades ?? [];
    } catch {
      return [];
    }
  }

  /** Compute realized gains for a date range. */
  async getRealizedGains(
    fromDate: string,
    toDate: string,
    mode: "paper" | "live" = "paper",
  ): Promise<RealizedGainRecord[]> {
    const trades = await this.loadTrades(mode);
    const results: RealizedGainRecord[] = [];

    for (const t of trades) {
      const sellDate = t.exitTime ?? t.sellDate ?? t.closedAt ?? "";
      const dateOnly = sellDate.slice(0, 10);
      if (!dateOnly || dateOnly < fromDate || dateOnly > toDate) continue;

      const symbol = t.symbol ?? t.code ?? "";
      const buyPrice = t.entryPrice ?? t.buyPrice ?? 0;
      const sellPrice = t.exitPrice ?? t.sellPrice ?? 0;
      const quantity = t.quantity ?? t.size ?? t.shares ?? 0;
      const netPnl = t.netPnl ?? t.pnl ?? t.profit ?? 0;
      const fees = (t.commission ?? 0) + (t.stampDuty ?? 0) + (t.transferFee ?? 0);
      const grossPnl = netPnl + fees;

      const buyDate = (t.entryTime ?? t.buyDate ?? t.createdAt ?? "").slice(0, 10);
      const holdingDays = buyDate
        ? Math.max(
            1,
            Math.round(
              (new Date(dateOnly).getTime() - new Date(buyDate).getTime()) / 86400000,
            ),
          )
        : 0;

      results.push({
        symbol,
        buyDate,
        sellDate: dateOnly,
        buyPrice,
        sellPrice,
        quantity,
        grossPnl: Number(grossPnl.toFixed(2)),
        fees: Number(fees.toFixed(2)),
        netPnl: Number(netPnl.toFixed(2)),
        holdingDays,
      });
    }

    return results;
  }

  /** Compute trade cost summary for a date range. */
  async getTradeCosts(
    fromDate: string,
    toDate: string,
    mode: "paper" | "live" = "paper",
  ): Promise<TradeCostSummary> {
    const trades = await this.loadTrades(mode);
    let totalCommission = 0;
    let totalStampDuty = 0;
    let totalTransferFee = 0;
    let totalSlippage = 0;
    let tradeCount = 0;

    for (const t of trades) {
      const sellDate = (t.exitTime ?? t.sellDate ?? t.closedAt ?? "").slice(0, 10);
      if (!sellDate || sellDate < fromDate || sellDate > toDate) continue;

      totalCommission += t.commission ?? t.fees ?? 0;
      totalStampDuty += t.stampDuty ?? 0;
      totalTransferFee += t.transferFee ?? 0;
      totalSlippage += t.slippage ?? 0;
      tradeCount++;
    }

    const totalFees = totalCommission + totalStampDuty + totalTransferFee + totalSlippage;

    return {
      totalCommission: Number(totalCommission.toFixed(2)),
      totalStampDuty: Number(totalStampDuty.toFixed(2)),
      totalTransferFee: Number(totalTransferFee.toFixed(2)),
      totalSlippage: Number(totalSlippage.toFixed(2)),
      totalFees: Number(totalFees.toFixed(2)),
      tradeCount,
      avgFeePerTrade: tradeCount > 0 ? Number((totalFees / tradeCount).toFixed(2)) : 0,
    };
  }

  /** Generate a comprehensive tax summary. */
  async getTaxSummary(
    fromDate: string,
    toDate: string,
    mode: "paper" | "live" = "paper",
    dividendIncome = 0,
  ): Promise<TaxSummary> {
    const gains = await this.getRealizedGains(fromDate, toDate, mode);
    const costs = await this.getTradeCosts(fromDate, toDate, mode);

    let totalGains = 0;
    let totalLosses = 0;
    let winCount = 0;
    let lossCount = 0;
    const symbolPnl = new Map<string, number>();
    const monthlyPnl: Record<string, number> = {};

    for (const g of gains) {
      if (g.netPnl >= 0) {
        totalGains += g.netPnl;
        winCount++;
      } else {
        totalLosses += g.netPnl;
        lossCount++;
      }
      symbolPnl.set(g.symbol, (symbolPnl.get(g.symbol) ?? 0) + g.netPnl);
      const month = g.sellDate.slice(0, 7);
      monthlyPnl[month] = (monthlyPnl[month] ?? 0) + g.netPnl;
    }

    const symbolPnlArr = [...symbolPnl.entries()]
      .map(([symbol, netPnl]) => ({ symbol, netPnl: Number(netPnl.toFixed(2)) }))
      .sort((a, b) => b.netPnl - a.netPnl);

    const topGainers = symbolPnlArr.filter((s) => s.netPnl > 0).slice(0, 5);
    const topLosers = symbolPnlArr.filter((s) => s.netPnl < 0).slice(-5).reverse();

    const totalTrades = winCount + lossCount;

    return {
      fromDate,
      toDate,
      totalGains: Number(totalGains.toFixed(2)),
      totalLosses: Number(totalLosses.toFixed(2)),
      netRealizedPnl: Number((totalGains + totalLosses).toFixed(2)),
      dividendIncome: Number(dividendIncome.toFixed(2)),
      totalFees: costs.totalFees,
      stampDutyTotal: costs.totalStampDuty,
      winCount,
      lossCount,
      winRate: totalTrades > 0 ? Number((winCount / totalTrades).toFixed(4)) : 0,
      topGainers,
      topLosers,
      monthlyPnl: Object.fromEntries(
        Object.entries(monthlyPnl).map(([k, v]) => [k, Number(v.toFixed(2))]),
      ),
    };
  }
}

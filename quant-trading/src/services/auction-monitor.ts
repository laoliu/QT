/**
 * AuctionMonitorService — 0212 (MW-5)
 *
 * 功能:
 *  - 9:15~9:25 060248
 *  - 14:57~15:00 01060212
 *  - 0403/撤单检测
 *  - 070500
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { AlertStore, AlertSeverity } from "./alert-store.js";
import type { NotificationService, NotificationMessage } from "./notification-service.js";

// ── Types ────────────────────────────────────────────────────

export interface AuctionMonitorConfig {
  enabled: boolean;
  /** 000107 */
  watchSymbols: string[];
  /** 074405 (1901, 08 3) */
  priceMoveThreshold: number;
  /** 004405 (06, 08 500) */
  volumeThreshold: number;
  /** 检查间隔 (毫秒, 08 15000 — 02020109) */
  checkIntervalMs: number;
}

export interface AuctionSnapshot {
  symbol: string;
  name: string;
  auctionPrice: number;
  prevClose: number;
  changePct: number;
  auctionVolume: number;   // 手
  auctionAmount: number;   // 06
  isLimitUp: boolean;
  isLimitDown: boolean;
}

const DEFAULT_CONFIG: AuctionMonitorConfig = {
  enabled: true,
  watchSymbols: [],
  priceMoveThreshold: 3,
  volumeThreshold: 500,
  checkIntervalMs: 15_000,
};

// ── Service ──────────────────────────────────────────────────

export class AuctionMonitorService {
  private config: AuctionMonitorConfig;
  private timer: ReturnType<typeof setInterval> | null = null;
  private alertedKeys = new Set<string>();
  private sessionDate: string | null = null;

  constructor(
    private api: OpenClawPluginApi,
    private tradingConfig: QuantTradingConfig,
    private alertStore: AlertStore | null,
    private notificationService: NotificationService | null,
    config?: Partial<AuctionMonitorConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  setWatchSymbols(symbols: string[]): void {
    this.config.watchSymbols = [...symbols];
  }

  /** Start monitoring — should be triggered at 9:14 and 14:56 */
  start(): void {
    if (!this.config.enabled || this.config.watchSymbols.length === 0) return;
    this.stop();

    // Reset alerts for new session
    const today = new Date().toISOString().slice(0, 10);
    if (this.sessionDate !== today) {
      this.alertedKeys.clear();
      this.sessionDate = today;
      this.resetSniper();
    }

    this.timer = setInterval(() => {
      void this.check();
    }, this.config.checkIntervalMs);
    console.log("[AuctionMonitor] started");
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Check current auction status */
  async check(): Promise<AuctionSnapshot[]> {
    if (this.config.watchSymbols.length === 0) return [];

    const symbolList = JSON.stringify(this.config.watchSymbols);
    const script = `
import akshare as ak
import json

symbols = ${symbolList}

try:
    # Use real-time quotes as proxy for auction data
    df = ak.stock_zh_a_spot_em()
    if df is None or len(df) == 0:
        print(json.dumps({"snapshots": []}))
    else:
        snapshots = []
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).strip()
            if code not in symbols:
                continue
            last = float(row.get("04", 0) or 0)
            prev = float(row.get("昨收", 0) or 0)
            change_pct = float(row.get("07", 0) or 0)
            vol = float(row.get("00", 0) or 0) / 100  # 手
            amt = float(row.get("00", 0) or 0) / 10000  # 06

            is_limit_up = change_pct >= 9.9
            is_limit_down = change_pct <= -9.9

            snapshots.append({
                "symbol": code,
                "name": str(row.get("名称", "")),
                "auctionPrice": last,
                "prevClose": prev,
                "changePct": round(change_pct, 2),
                "auctionVolume": round(vol, 2),
                "auctionAmount": round(amt, 2),
                "isLimitUp": is_limit_up,
                "isLimitDown": is_limit_down,
            })
        print(json.dumps({"snapshots": snapshots}, ensure_ascii=False))

except Exception as e:
    print(json.dumps({"snapshots": [], "error": str(e)}))
`;

    try {
      const result = await runInlinePython<{ snapshots: AuctionSnapshot[] }>(script, this.tradingConfig);
      const snapshots: AuctionSnapshot[] = result.data?.snapshots ?? [];
      await this.evaluate(snapshots);
      // Collect snapshot for sniper analysis (异动量大单抢筹检测)
      this.collectSniperSnapshot(snapshots);
      return snapshots;
    } catch {
      return [];
    }
  }

  private async evaluate(snapshots: AuctionSnapshot[]): Promise<void> {
    for (const snap of snapshots) {
      // Limit up/down alert
      if (snap.isLimitUp || snap.isLimitDown) {
        const kind = snap.isLimitUp ? "05" : "05";
        await this.emit(
          `${snap.symbol}_limit`,
          snap.isLimitDown ? "critical" : "warning",
          `集合竞价${kind}: ${snap.name}(${snap.symbol}) ${kind}开盘 ${snap.auctionPrice}`,
          snap as unknown as Record<string, unknown>,
        );
      }

      // Price move alert
      if (Math.abs(snap.changePct) >= this.config.priceMoveThreshold && !snap.isLimitUp && !snap.isLimitDown) {
        const dir = snap.changePct > 0 ? "11" : "03";
        await this.emit(
          `${snap.symbol}_move`,
          "warning",
          `集合竞价${dir}: ${snap.name}(${snap.symbol}) ${snap.changePct.toFixed(2)}% 12 ${snap.auctionPrice}`,
          snap as unknown as Record<string, unknown>,
        );
      }

      // Large volume alert
      if (snap.auctionAmount >= this.config.volumeThreshold) {
        await this.emit(
          `${snap.symbol}_vol`,
          "info",
          `0217: ${snap.name}(${snap.symbol}) 16 ${snap.auctionAmount.toFixed(0)}万`,
          snap as unknown as Record<string, unknown>,
        );
      }
    }
  }

  private async emit(
    key: string, severity: AlertSeverity,
    message: string, metadata: Record<string, unknown>,
  ): Promise<void> {
    if (this.alertedKeys.has(key)) return;
    this.alertedKeys.add(key);

    this.alertStore?.add(severity, "signal", message, metadata);

    if (this.notificationService) {
      const msg: NotificationMessage = {
        title: "0212",
        content: message,
        severity,
        category: "alert",
      };
      void this.notificationService.send(msg);
    }

    console.log(`[AuctionMonitor] ${severity}: ${message}`);
  }

  // ── Auction Sniper: 异动量大单抢筹检测 ─────────────────────────

  /** Accumulated snapshots for sniper analysis across polling cycles */
  private sniperSnapshots: Record<string, unknown>[][] = [];

  /** Collect a snapshot batch for sniper analysis during each poll cycle */
  private collectSniperSnapshot(snapshots: AuctionSnapshot[]): void {
    const batch = snapshots.map((s) => ({
      symbol: s.symbol,
      name: s.name,
      timestamp: new Date().toTimeString().slice(0, 8),  // HH:MM:SS
      auction_price: s.auctionPrice,
      prev_close: s.prevClose,
      change_pct: s.changePct,
      volume: s.auctionVolume,
      amount: s.auctionAmount,
    }));
    this.sniperSnapshots.push(batch);
  }

  /**
   * Run sniper analysis after auction ends (9:25+).
   *
   * 主力异动量大单抢筹检测:
   *  - 分析 9:24 020418
   *  - 061900
   *  - 评估隐蔽抢筹概率
   *
   * Returns signals sorted by score.
   */
  async runSniperAnalysis(minScore = 35): Promise<SniperAnalysisResult[]> {
    if (this.sniperSnapshots.length < 2) {
      console.log("[AuctionSniper] 0804082批数据");
      return [];
    }

    const script = `
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engines.auction_sniper import scan_auction_sniper

snapshots_data = json.loads('''${JSON.stringify(this.sniperSnapshots)}''')
min_score = ${minScore}
results = scan_auction_sniper(min_score=min_score, snapshots_data=snapshots_data)
print(json.dumps({"signals": results}, ensure_ascii=False))
`;

    try {
      const result = await runInlinePython<{ signals: SniperAnalysisResult[] }>(script, this.tradingConfig);
      const signals = result.data?.signals ?? [];

      // Emit alerts for strong/moderate signals
      for (const sig of signals) {
        if (sig.signal_type === "strong" || sig.signal_type === "moderate") {
          const severity: AlertSeverity = sig.signal_type === "strong" ? "critical": "warning";
          const label = sig.signal_type === "strong" ? "97 强烈抢筹": "73 中等抢筹";
          await this.emit(
            `${sig.symbol}_sniper`,
            severity,
            `${label}: ${sig.name}(${sig.symbol}) 竞价尾部跳涨${sig.late_price_jump?.toFixed(2)}% `
            + `00${sig.late_amount?.toFixed(0)}万 ÷${sig.score}`,
            sig as unknown as Record<string, unknown>,
          );
        }
      }

      return signals;
    } catch (err) {
      console.log(`[AuctionSniper] Analysis error: ${err}`);
      return [];
    }
  }

  /**
   * Post-open confirmation: 开盘后点火拉升确认.
   *
   * Call at 9:31+ to check whether sniper signals are confirmed.
   */
  async confirmPostOpen(
    symbol: string,
    openPrice: number,
    currentPrice: number,
    highPrice: number,
  ): Promise<SniperAnalysisResult | null> {
    const script = `
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engines.auction_sniper import AuctionSniperEngine, AuctionSnapshot

engine = AuctionSniperEngine()
snapshots_data = json.loads('''${JSON.stringify(this.sniperSnapshots)}''')
for batch in snapshots_data:
    for s in batch:
        engine.add_snapshot(AuctionSnapshot(**s))

engine.analyze("${symbol}")
sig = engine.confirm("${symbol}", ${openPrice}, ${currentPrice}, ${highPrice})
print(json.dumps({"signal": sig.to_dict()}, ensure_ascii=False))
`;

    try {
      const result = await runInlinePython<{ signal: SniperAnalysisResult }>(script, this.tradingConfig);
      const sig = result.data?.signal ?? null;

      if (sig?.post_open_confirmed) {
        await this.emit(
          `${symbol}_sniper_confirmed`,
          "critical",
          `73 抢筹确认: ${sig.name}(${sig.symbol}) 开盘后点火拉升 `
          + `02${sig.post_open_change_pct?.toFixed(2)}%`,
          sig as unknown as Record<string, unknown>,
        );
      } else if (sig?.post_open_confirmed === false) {
        await this.emit(
          `${symbol}_sniper_failed`,
          "info",
          `74 ā02: ${sig.name}(${sig.symbol}) 开盘后未延续拉升`,
          sig as unknown as Record<string, unknown>,
        );
      }

      return sig;
    } catch (err) {
      console.log(`[AuctionSniper] Confirm error: ${err}`);
      return null;
    }
  }

  /** Reset sniper state for new trading day */
  resetSniper(): void {
    this.sniperSnapshots = [];
  }
}

// ── Sniper Types ─────────────────────────────────────────────────

export interface SniperAnalysisResult {
  symbol: string;
  name: string;
  signal_type: "strong" | "moderate" | "weak" | "none";
  score: number;
  prev_close: number;
  final_auction_price: number;
  gap_up_pct: number;
  early_stability_score: number;
  late_surge_score: number;
  volume_spike_score: number;
  order_imbalance_score: number;
  early_change_range: number;
  late_price_jump: number;
  late_volume_ratio: number;
  late_amount: number;
  signals: string[];
  risk_notes: string[];
  post_open_confirmed?: boolean | null;
  open_price: number;
  post_open_high: number;
  post_open_change_pct: number;
}

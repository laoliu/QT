/**
 * Strategy Fitness Analysis Page — self-contained HTML SPA for
 * strategy-stock fitness analysis, global ranking display, and per-stock queries.
 */
import {
  THEME_CSS_VARS,
  BASE_CSS_RESET,
  HEADER_CSS,
  TOAST_CSS,
  KPI_CSS,
  SHARED_JS_HELPERS,
  SHARED_JS_API,
} from "./_shared.js";

export function getStrategyFitnessPageHtml(baseUrl: string, apiToken?: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 策略适配分析</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>🎯</text></svg>">
<style>
${THEME_CSS_VARS}
${BASE_CSS_RESET}
${HEADER_CSS}
${TOAST_CSS}
${KPI_CSS}
.container{max-width:1400px;margin:0 auto;padding:20px 24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}
.card h3{margin-bottom:12px;font-size:15px;display:flex;align-items:center;gap:8px}
.grid{display:grid;gap:16px}
.grid-2{grid-template-columns:1fr 1fr}
.grid-3{grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:6px;font-size:13px;cursor:pointer;border:1px solid var(--border);background:var(--bg3);color:var(--text);transition:all .15s}
.btn:hover{background:var(--border)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-primary:hover{opacity:.85}
.btn-danger{background:var(--red);color:#fff;border-color:var(--red)}
.btn-sm{padding:4px 10px;font-size:12px}
label{display:block;font-size:13px;color:var(--text-dim);margin-bottom:4px}
select,input[type=text],input[type=number]{width:100%;padding:6px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px}
select:focus,input:focus{outline:none;border-color:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500;font-size:12px}
.metric-good{color:var(--green)}
.metric-bad{color:var(--red)}
.metric-ok{color:var(--yellow)}
.empty{text-align:center;padding:40px;color:var(--text-dim)}
.form-row{display:flex;gap:12px;align-items:flex-end;margin-bottom:12px;flex-wrap:wrap}
.form-row>div{flex:1;min-width:140px}
.progress-bar{background:var(--bg3);border-radius:6px;height:24px;overflow:hidden;position:relative;margin:12px 0}
.progress-bar .fill{background:var(--accent);height:100%;border-radius:6px;transition:width .5s}
.progress-bar .label{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:11px;font-weight:600;z-index:1}
.rank-badge{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;font-size:12px;font-weight:700}
.rank-1{background:var(--green);color:#000}
.rank-2{background:var(--accent);color:#000}
.rank-3{background:var(--yellow);color:#000}
.rank-n{background:var(--bg3);color:var(--text)}
.status-idle{color:var(--text-dim)}
.status-running{color:var(--accent)}
.status-done{color:var(--green)}
.status-error{color:var(--red)}
.score-bar{display:inline-block;height:6px;border-radius:3px;vertical-align:middle;margin-left:6px}
nav{display:flex;gap:4px;background:var(--bg2);padding:0 24px;border-bottom:1px solid var(--border)}
nav button{background:none;border:none;color:var(--text-dim);padding:10px 16px;font-size:14px;cursor:pointer;border-bottom:2px solid transparent;transition:all .2s}
nav button:hover{color:var(--text)}
nav button.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-content{display:none}
.tab-content.active{display:block}
.clickable-row{cursor:pointer;transition:background .15s}
.clickable-row:hover{background:var(--bg3)}
.batch-detail{margin-top:16px;border-top:1px solid var(--border);padding-top:16px}
.batch-detail h4{font-size:14px;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.back-link{cursor:pointer;color:var(--accent);font-size:13px;display:inline-flex;align-items:center;gap:4px;margin-bottom:12px}
.back-link:hover{text-decoration:underline}
.clickable-num{color:var(--accent);cursor:pointer;text-decoration:underline;text-decoration-style:dotted}
.clickable-num:hover{text-decoration-style:solid}
.batch-sub{display:none}
.batch-sub.active{display:block}
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:1000;justify-content:center;align-items:center}
.modal-overlay.show{display:flex}
.modal{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:20px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;position:relative}
.modal h3{margin-bottom:12px;font-size:15px}
.modal .close-btn{position:absolute;top:12px;right:16px;background:none;border:none;color:var(--text-dim);font-size:20px;cursor:pointer}
.modal .close-btn:hover{color:var(--text)}
.modal table{font-size:12px}
.modal th,.modal td{padding:6px 10px}
@media(max-width:768px){.grid-2{grid-template-columns:1fr}.form-row{flex-direction:column}}
</style>
</head>
<body>
<header>
  <h1>🎯 策略适配分析</h1>
  <div class="header-right">
    <span id="statusBadge" class="status-idle">● 空闲</span>
    <a href="/quant/admin/ui">← 返回管理面板</a>
  </div>
</header>
<nav>
  <button class="active" onclick="switchTab('run')">🚀 运行分析</button>
  <button onclick="switchTab('history')">📋 历史记录</button>
  <button onclick="switchTab('aggregate')">🌐 全局汇总</button>
</nav>
<div class="container">

<!-- Tab 1: Run Analysis -->
<div id="tab-run" class="tab-content active">
  <div class="card">
    <h3>⚙️ 运行参数</h3>
    <div class="form-row">
      <div>
        <label>股票范围</label>
        <select id="universe">
          <option value="csi300">沪深300 (~300只)</option>
          <option value="csi500">中证500 (~500只)</option>
          <option value="csi1000" selected>中证1000 (~1000只)</option>
          <option value="all_a">全部A股 (~5000只)</option>
        </select>
      </div>
      <div>
        <label>回测年数</label>
        <select id="backtestYears">
          <option value="1">1年</option>
          <option value="2">2年</option>
          <option value="3">3年</option>
          <option value="5" selected>5年</option>
        </select>
      </div>
      <div>
        <label>每股 Top N</label>
        <input type="number" id="topNPerStock" value="5" min="3" max="10">
      </div>
      <div>
        <label>全局 Top N</label>
        <input type="number" id="topNGlobal" value="10" min="5" max="30">
      </div>
      <div>
        <label>并行数</label>
        <input type="number" id="workers" value="8" min="1" max="16">
      </div>
      <div>
        <label>初始资金</label>
        <input type="number" id="initialCapital" value="1000000" min="100000" step="100000">
      </div>
    </div>
    <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px">
      <button class="btn btn-primary" id="btnRun" onclick="startAnalysis()">🚀 开始分析</button>
      <button class="btn" id="btnRefresh" onclick="checkStatus()">🔄 刷新状态</button>
      <span id="runStatus" style="font-size:13px;color:var(--text-dim)"></span>
    </div>
  </div>

  <!-- Strategy Selection -->
  <div class="card">
    <h3>📋 策略选择 <span style="font-size:12px;font-weight:400;color:var(--text-dim)">(选择要参与适配分析的策略)</span></h3>
    <div style="margin-bottom:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <button class="btn btn-sm" onclick="toggleAllStrategies(true)">全选</button>
      <button class="btn btn-sm" onclick="toggleAllStrategies(false)">全不选</button>
      <button class="btn btn-sm" onclick="toggleAlphaStrategies()" style="border-color:var(--purple);color:var(--purple)">Alpha策略</button>
      <button class="btn btn-sm" onclick="toggleBuiltinStrategies()" style="border-color:var(--accent);color:var(--accent)">Builtin策略</button>
      <span id="strategyCountBadge" style="font-size:12px;color:var(--text-dim)"></span>
    </div>
    <div style="margin-bottom:6px">
      <label style="font-weight:600;color:var(--purple);font-size:12px;margin-bottom:2px">
        <input type="checkbox" id="includeAlpha" checked onchange="updateStrategyCount()" style="vertical-align:middle;margin-right:4px">
        Alpha量化策略 (~65个: 多因子/轮动/事件驱动)
      </label>
    </div>
    <div style="font-size:12px;color:var(--text-dim);margin-bottom:10px;border-bottom:1px solid var(--border);padding-bottom:8px">
      Alpha策略由Python aipha引擎运行，包含红利质量、动量加速、指数增强等65个因子/轮动策略。勾选即全部参与分析。
    </div>
    <div style="margin-bottom:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap">
      <label style="font-weight:600;color:var(--accent);font-size:12px;min-width:auto">默认策略</label>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('default',true)">全选</button>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('default',false)">全不选</button>
    </div>
    <div class="form-row" style="flex-wrap:wrap;gap:3px 6px;margin-bottom:8px">
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="momentum" checked onchange="updateStrategyCount()">趋势动量</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="value" checked onchange="updateStrategyCount()">价值投资</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="growth" checked onchange="updateStrategyCount()">成长投资</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="swing" checked onchange="updateStrategyCount()">波段交易</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="mixed" checked onchange="updateStrategyCount()">混合策略</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="manipulation" checked onchange="updateStrategyCount()">跟庄策略</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="spread-arbitrage" checked onchange="updateStrategyCount()">价差套利</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="rsi-reversal" checked onchange="updateStrategyCount()">RSI反转</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="vwap-reversion" checked onchange="updateStrategyCount()">VWAP回归</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="mean-reversion-zscore" checked onchange="updateStrategyCount()">均值回归Z-Score</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="williams-r" checked onchange="updateStrategyCount()">威廉指标反转</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="momentum-breakout" checked onchange="updateStrategyCount()">动量突破</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="range-breakout" checked onchange="updateStrategyCount()">区间突破</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="dual-ma-crossover" checked onchange="updateStrategyCount()">双均线交叉</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="trend-combo" checked onchange="updateStrategyCount()">趋势合并</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="double-bottom" checked onchange="updateStrategyCount()">双底双顶</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="macd-divergence" checked onchange="updateStrategyCount()">MACD背离</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="volume-confirmation" checked onchange="updateStrategyCount()">成交量确认</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="gated" checked onchange="updateStrategyCount()">条件门控</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="scored" checked onchange="updateStrategyCount()">加权仓位</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="mean-reversion" checked onchange="updateStrategyCount()">均值回归</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="macd-confirmation" checked onchange="updateStrategyCount()">MACD确认</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="default" value="cta-kdj" checked onchange="updateStrategyCount()">KDJ随机</label>
    </div>
    <div style="margin-bottom:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap">
      <label style="font-weight:600;color:var(--accent);font-size:12px;min-width:auto">组合/增强</label>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('combo',true)">全选</button>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('combo',false)">全不选</button>
    </div>
    <div class="form-row" style="flex-wrap:wrap;gap:3px 6px;margin-bottom:8px">
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="combo" value="composite" checked onchange="updateStrategyCount()">综合投票</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="combo" value="enhanced-composite" checked onchange="updateStrategyCount()">增强综合</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="combo" value="rsi-filter" checked onchange="updateStrategyCount()">RSI动量过滤</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="combo" value="rsi-enhanced" checked onchange="updateStrategyCount()">RSI增强选股</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="combo" value="rsi-macd-combined" checked onchange="updateStrategyCount()">RSI+MACD组合</label>
    </div>
    <div style="margin-bottom:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap">
      <label style="font-weight:600;color:var(--accent);font-size:12px;min-width:auto">扩展技术指标</label>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('tech',true)">全选</button>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('tech',false)">全不选</button>
    </div>
    <div class="form-row" style="flex-wrap:wrap;gap:3px 6px;margin-bottom:8px">
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="trix-momentum" checked onchange="updateStrategyCount()">TRIX动量</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="mfi-oscillator" checked onchange="updateStrategyCount()">MFI资金流</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="aroon-trend" checked onchange="updateStrategyCount()">阿隆趋势</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="cmf-divergence" checked onchange="updateStrategyCount()">蔡金资金流背离</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="elder-ray" checked onchange="updateStrategyCount()">Elder Ray力量</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="supertrend" checked onchange="updateStrategyCount()">超级趋势</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="hull-ma-crossover" checked onchange="updateStrategyCount()">Hull MA交叉</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="dema-crossover" checked onchange="updateStrategyCount()">DEMA交叉</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="triple-ma-crossover" checked onchange="updateStrategyCount()">三重均线交叉</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="ma-envelope" checked onchange="updateStrategyCount()">MA包络</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="connors-rsi" checked onchange="updateStrategyCount()">Connors RSI</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="kama-trend" checked onchange="updateStrategyCount()">KAMA自适应趋势</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="tech" value="demarker" checked onchange="updateStrategyCount()">DeMARK指标</label>
    </div>
    <div style="margin-bottom:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap">
      <label style="font-weight:600;color:var(--accent);font-size:12px;min-width:auto">CTA经典</label>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('cta',true)">全选</button>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('cta',false)">全不选</button>
    </div>
    <div class="form-row" style="flex-wrap:wrap;gap:3px 6px;margin-bottom:8px">
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="cta-trend" checked onchange="updateStrategyCount()">CTA趋势跟踪</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="dual-thrust" checked onchange="updateStrategyCount()">Dual Thrust双推力</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="turtle-breakout" checked onchange="updateStrategyCount()">海龟突破</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="macd-trend" checked onchange="updateStrategyCount()">MACD趋势</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="bollinger-breakout" checked onchange="updateStrategyCount()">布林带突破</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="keltner-channel" checked onchange="updateStrategyCount()">肯特纳通道</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="r-breaker" checked onchange="updateStrategyCount()">R-Breaker反转</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="aberration" checked onchange="updateStrategyCount()">标准差偏离</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="ttm-squeeze" checked onchange="updateStrategyCount()">TTM挤压</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="dmi-adx" checked onchange="updateStrategyCount()">DMI+ADX</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="parabolic-sar" checked onchange="updateStrategyCount()">抛物线SAR</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="volatility-target" checked onchange="updateStrategyCount()">波动率目标</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="momentum-rotation" checked onchange="updateStrategyCount()">动量轮动</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="obv-trend" checked onchange="updateStrategyCount()">OBV趋势</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="ichimoku" checked onchange="updateStrategyCount()">一目均衡表</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="grid-trading" checked onchange="updateStrategyCount()">网格交易</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="multi-ma-convergence" checked onchange="updateStrategyCount()">多均线收敛</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="donchian-mid-reversion" checked onchange="updateStrategyCount()">唐奇安中线回归</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="pivot-point" checked onchange="updateStrategyCount()">枢轴点</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="chandelier-exit" checked onchange="updateStrategyCount()">烛台止损</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="roc-momentum" checked onchange="updateStrategyCount()">ROC动量</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="cci-divergence" checked onchange="updateStrategyCount()">CCI背离</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="ema-crossover" checked onchange="updateStrategyCount()">EMA均线交叉</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="atr-trailing" checked onchange="updateStrategyCount()">ATR追踪止损</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="stochastic-rsi" checked onchange="updateStrategyCount()">随机RSI</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="macd-histogram-reversal" checked onchange="updateStrategyCount()">MACD柱状反转</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="cta" value="bollinger-bandwidth" checked onchange="updateStrategyCount()">布林带带宽</label>
    </div>
    <div style="margin-bottom:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap">
      <label style="font-weight:600;color:var(--accent);font-size:12px;min-width:auto">CTA扩展</label>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('ctaext',true)">全选</button>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('ctaext',false)">全不选</button>
    </div>
    <div class="form-row" style="flex-wrap:wrap;gap:3px 6px;margin-bottom:8px">
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-awesome-oscillator" checked onchange="updateStrategyCount()">AO振荡器</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-elder-force" checked onchange="updateStrategyCount()">Elder力量指数</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-demarker" checked onchange="updateStrategyCount()">CTA DeMARK</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-ultimate-oscillator" checked onchange="updateStrategyCount()">终极振荡器</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-adx-breakout" checked onchange="updateStrategyCount()">ADX突破</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-trix-trend" checked onchange="updateStrategyCount()">TRIX趋势</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-hull-ma-trend" checked onchange="updateStrategyCount()">Hull MA趋势</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-mass-index" checked onchange="updateStrategyCount()">质量指数</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-chaikin-oscillator" checked onchange="updateStrategyCount()">蔡金振荡器</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-coppock" checked onchange="updateStrategyCount()">Coppock曲线</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-rsi-divergence" checked onchange="updateStrategyCount()">RSI背离</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-volume-breakout" checked onchange="updateStrategyCount()">成交量突破</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-adaptive-ma" checked onchange="updateStrategyCount()">自适应均线</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-ma-envelope" checked onchange="updateStrategyCount()">CTA MA包络</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-connors-rsi" checked onchange="updateStrategyCount()">CTA Connors RSI</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-aroon-oscillator" checked onchange="updateStrategyCount()">阿隆振荡器</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-mfi-trend" checked onchange="updateStrategyCount()">MFI趋势</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-dema-crossover" checked onchange="updateStrategyCount()">CTA DEMA交叉</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-triple-ma" checked onchange="updateStrategyCount()">CTA三重均线</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="ctaext" value="cta-supertrend" checked onchange="updateStrategyCount()">CTA超级趋势</label>
    </div>
    <div style="margin-bottom:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap">
      <label style="font-weight:600;color:var(--accent);font-size:12px;min-width:auto;opacity:0.5">T0日内 (A股暂不支持)</label>
    </div>
    <div class="form-row" style="flex-wrap:wrap;gap:3px 6px;margin-bottom:8px">
      <label style="min-width:auto;opacity:0.5"><input type="checkbox" class="fit-builtin" data-group="t0" value="t0-day-trading" disabled onchange="updateStrategyCount()">T0日内交易</label>
      <label style="min-width:auto;opacity:0.5"><input type="checkbox" class="fit-builtin" data-group="t0" value="t0-enhanced" disabled onchange="updateStrategyCount()">T0增强</label>
    </div>
    <div style="margin-bottom:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap">
      <label style="font-weight:600;color:var(--accent);font-size:12px;min-width:auto">Python趋势</label>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('pytrend',true)">全选</button>
      <button class="btn btn-sm" style="padding:1px 6px;font-size:10px" onclick="toggleGroup('pytrend',false)">全不选</button>
    </div>
    <div class="form-row" style="flex-wrap:wrap;gap:3px 6px;margin-bottom:8px">
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pytrend" value="ema-cross" checked onchange="updateStrategyCount()">EMA均线交叉</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pytrend" value="adx-trend" checked onchange="updateStrategyCount()">ADX趋势强度</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pytrend" value="aroon" checked onchange="updateStrategyCount()">阿隆指标</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pytrend" value="vortex" checked onchange="updateStrategyCount()">漩涡指标</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pytrend" value="cci" checked onchange="updateStrategyCount()">CCI顺势</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pytrend" value="trix" checked onchange="updateStrategyCount()">TRIX平滑</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pytrend" value="dmi" checked onchange="updateStrategyCount()">趋向指标DMI</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pytrend" value="dema-cross" checked onchange="updateStrategyCount()">DEMA交叉</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pytrend" value="tema-cross" checked onchange="updateStrategyCount()">TEMA交叉</label>
    </div>
    <div style="margin-bottom:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap">
      <label style="font-weight:600;color:var(--accent);font-size:12px;min-width:auto">Python通道</label>
    </div>
    <div class="form-row" style="flex-wrap:wrap;gap:3px 6px;margin-bottom:8px">
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pychannel" value="donchian-breakout" checked onchange="updateStrategyCount()">唐奇安突破</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pychannel" value="atr-channel" checked onchange="updateStrategyCount()">ATR通道</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pychannel" value="envelope" checked onchange="updateStrategyCount()">包络线</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pychannel" value="price-channel-breakout" checked onchange="updateStrategyCount()">价格通道突破</label>
    </div>
    <div style="margin-bottom:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap">
      <label style="font-weight:600;color:var(--accent);font-size:12px;min-width:auto">Python指标</label>
    </div>
    <div class="form-row" style="flex-wrap:wrap;gap:3px 6px;margin-bottom:8px">
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pyind" value="stochastic" checked onchange="updateStrategyCount()">随机振荡器</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pyind" value="mfi" checked onchange="updateStrategyCount()">MFI资金流</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pyind" value="cmf" checked onchange="updateStrategyCount()">蔡金资金流</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pyind" value="adl" checked onchange="updateStrategyCount()">聚散指标ADL</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pyind" value="roc" checked onchange="updateStrategyCount()">变化率ROC</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pyind" value="vwap" checked onchange="updateStrategyCount()">VWAP均价</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pyind" value="adaptive-ma" checked onchange="updateStrategyCount()">自适应均线</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pyind" value="obv" checked onchange="updateStrategyCount()">能量潮OBV</label>
    </div>
    <div style="margin-bottom:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap">
      <label style="font-weight:600;color:var(--accent);font-size:12px;min-width:auto">Python形态</label>
    </div>
    <div class="form-row" style="flex-wrap:wrap;gap:3px 6px;margin-bottom:8px">
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pypattern" value="engulfing" checked onchange="updateStrategyCount()">吞没形态</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pypattern" value="hammer" checked onchange="updateStrategyCount()">锤子线</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pypattern" value="doji" checked onchange="updateStrategyCount()">十字星</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pypattern" value="morning-star" checked onchange="updateStrategyCount()">启明星</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pypattern" value="three-soldiers" checked onchange="updateStrategyCount()">三兵三鸦</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pypattern" value="harami" checked onchange="updateStrategyCount()">孕线形态</label>
    </div>
    <div style="margin-bottom:4px;display:flex;gap:4px;align-items:center;flex-wrap:wrap">
      <label style="font-weight:600;color:var(--accent);font-size:12px;min-width:auto">Python组合</label>
    </div>
    <div class="form-row" style="flex-wrap:wrap;gap:3px 6px;margin-bottom:8px">
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pycombo" value="macd-rsi-combo" checked onchange="updateStrategyCount()">MACD+RSI组合</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pycombo" value="bollinger-rsi-combo" checked onchange="updateStrategyCount()">布林+RSI组合</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pycombo" value="ma-volume-combo" checked onchange="updateStrategyCount()">均线+量能组合</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pycombo" value="kdj-macd-combo" checked onchange="updateStrategyCount()">KDJ+MACD组合</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pycombo" value="ichimoku-adx-combo" checked onchange="updateStrategyCount()">一目+ADX组合</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pycombo" value="multi-tf-ma" checked onchange="updateStrategyCount()">多周期均线</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pycombo" value="volatility-breakout" checked onchange="updateStrategyCount()">波动率突破</label>
      <label style="min-width:auto"><input type="checkbox" class="fit-builtin" data-group="pycombo" value="regime-switch" checked onchange="updateStrategyCount()">趋势/震荡切换</label>
    </div>
  </div>

  <div id="progressSection" style="display:none">
    <div class="card">
      <h3>📊 运行进度</h3>
      <div class="progress-bar">
        <div class="fill" id="progressFill" style="width:0%"></div>
        <div class="label" id="progressLabel">0%</div>
      </div>
      <div id="progressDetail" style="font-size:13px;color:var(--text-dim);margin-top:8px"></div>
    </div>
  </div>

  <div id="resultSection" style="display:none">
    <div class="card">
      <h3>✅ 运行结果</h3>
      <div class="grid grid-3" id="resultKpis"></div>
      <div id="resultPreview" style="margin-top:16px"></div>
    </div>
  </div>
</div>

<!-- Tab 2: History (batch selector + batch details) -->
<div id="tab-history" class="tab-content">
  <div class="card">
    <h3>📋 分析历史</h3>
    <button class="btn btn-sm" onclick="loadHistory()" style="margin-bottom:12px">🔄 刷新</button>
    <div id="historyTable"><div class="empty">点击刷新加载</div></div>
  </div>
  <!-- Batch detail panel: shown when a batch is selected -->
  <div id="batchDetail" style="display:none">
    <div class="card">
      <span class="back-link" onclick="hideBatchDetail()">← 返回历史列表</span>
      <h3 id="batchDetailTitle">📊 批次详情</h3>
      <!-- Sub-tabs for this batch -->
      <div style="display:flex;gap:4px;margin-bottom:16px;border-bottom:1px solid var(--border);padding-bottom:8px">
        <button class="btn btn-sm" id="batchSubGlobal" onclick="switchBatchSub('global')" style="border-color:var(--accent);color:var(--accent)">🏆 全局排名</button>
        <button class="btn btn-sm" id="batchSubAllstocks" onclick="switchBatchSub('allstocks')">📋 个股Top策略</button>
        <button class="btn btn-sm" id="batchSubStock" onclick="switchBatchSub('stock')">📊 个股查询</button>
      </div>
      <!-- Sub-tab: Global ranking for this batch -->
      <div id="batchSub-global" class="batch-sub active">
        <div id="batchGlobalTable"><div class="empty">加载中...</div></div>
      </div>
      <!-- Sub-tab: All stocks top strategy for this batch -->
      <div id="batchSub-allstocks" class="batch-sub" style="display:none">
        <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
          <span id="batchAllStocksCount" style="font-size:12px;color:var(--text-dim)"></span>
          <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
            <button class="btn btn-sm" id="btnBatchPrev" onclick="batchAllStocksPage(-1)" disabled>← 上一页</button>
            <span id="batchAllStocksPageInfo" style="font-size:12px;color:var(--text-dim)"></span>
            <button class="btn btn-sm" id="btnBatchNext" onclick="batchAllStocksPage(1)">下一页 →</button>
          </div>
        </div>
        <div id="batchAllStocksTable"><div class="empty">加载中...</div></div>
      </div>
      <!-- Sub-tab: Stock query for this batch -->
      <div id="batchSub-stock" class="batch-sub" style="display:none">
        <div class="form-row" style="margin-bottom:8px">
          <div style="flex:2"><input type="text" id="batchQuerySymbol" placeholder="输入股票代码查询（如 600519）" maxlength="6"></div>
          <div><button class="btn btn-sm btn-primary" onclick="queryBatchStock()">🔍 查询</button></div>
        </div>
        <div id="batchStockResult"><div class="empty">输入代码查询该批次的个股适配</div></div>
      </div>
    </div>
  </div>
</div>

<!-- Tab 3: Aggregate Global Ranking -->
<div id="tab-aggregate" class="tab-content">
  <div class="card">
    <h3>🌐 全局汇总排名</h3>
    <p style="font-size:13px;color:var(--text-dim);margin-bottom:12px">跨所有历史批次聚合的策略排名 — 将所有批次中每只股票的策略得分相加，排出全局最优策略。删除某个批次后此排名自动更新。</p>
    <button class="btn btn-sm" onclick="loadAggregateGlobal()" style="margin-bottom:12px">🔄 刷新</button>
    <div id="aggregateGlobalTable"><div class="empty">点击刷新加载数据</div></div>
  </div>
</div>

</div>
<div class="toast" id="toast"></div>
<script>
var B='${b}';
var TOKEN='${apiToken ?? ""}';
${SHARED_JS_HELPERS}
${SHARED_JS_API}

// ── Tab switching ─────────────────────────────────────
function switchTab(id){
  document.querySelectorAll('.tab-content').forEach(function(el){el.classList.remove('active')});
  document.querySelectorAll('nav button').forEach(function(el){el.classList.remove('active')});
  var tab=document.getElementById('tab-'+id);
  if(tab)tab.classList.add('active');
  var btns=document.querySelectorAll('nav button');
  var map={run:0,history:1,aggregate:2};
  if(map[id]!==undefined&&btns[map[id]])btns[map[id]].classList.add('active');
  if(id==='history')loadHistory();
  if(id==='aggregate')loadAggregateGlobal();
}

// ── Batch sub-tab switching ──────────────────────────
function switchBatchSub(sub){
  document.querySelectorAll('.batch-sub').forEach(function(el){el.classList.remove('active');el.style.display='none'});
  var el=document.getElementById('batchSub-'+sub);
  if(el){el.classList.add('active');el.style.display='block'}
  // Highlight button
  document.getElementById('batchSubGlobal').style.borderColor=sub==='global'?'var(--accent)':'var(--border)';
  document.getElementById('batchSubGlobal').style.color=sub==='global'?'var(--accent)':'var(--text)';
  document.getElementById('batchSubAllstocks').style.borderColor=sub==='allstocks'?'var(--accent)':'var(--border)';
  document.getElementById('batchSubAllstocks').style.color=sub==='allstocks'?'var(--accent)':'var(--text)';
  document.getElementById('batchSubStock').style.borderColor=sub==='stock'?'var(--accent)':'var(--border)';
  document.getElementById('batchSubStock').style.color=sub==='stock'?'var(--accent)':'var(--text)';
  // Auto-load data
  if(sub==='allstocks'&&_currentBatchId)loadBatchAllStocks();
}

// ── Strategy selection helpers ────────────────────────
function getSelectedBuiltinStrategies(){
  var checked=document.querySelectorAll('.fit-builtin:checked');
  var arr=[];
  checked.forEach(function(el){arr.push(el.value)});
  return arr;
}
function updateStrategyCount(){
  var builtinCount=document.querySelectorAll('.fit-builtin:checked').length;
  var totalBuiltin=document.querySelectorAll('.fit-builtin').length;
  var alphaChecked=document.getElementById('includeAlpha').checked;
  var alphaStr=alphaChecked?'Alpha ~65':'Alpha 0';
  document.getElementById('strategyCountBadge').textContent=
    '已选: '+alphaStr+' + Builtin '+builtinCount+'/'+totalBuiltin+' 个策略';
}
function toggleAllStrategies(on){
  document.getElementById('includeAlpha').checked=on;
  document.querySelectorAll('.fit-builtin').forEach(function(el){el.checked=on});
  updateStrategyCount();
}
function toggleAlphaStrategies(){
  var el=document.getElementById('includeAlpha');
  el.checked=!el.checked;
  updateStrategyCount();
}
function toggleBuiltinStrategies(){
  var all=document.querySelectorAll('.fit-builtin');
  var checked=document.querySelectorAll('.fit-builtin:checked');
  var on=checked.length<all.length/2;
  all.forEach(function(el){el.checked=on});
  updateStrategyCount();
}
function toggleGroup(group,on){
  document.querySelectorAll('.fit-builtin[data-group="'+group+'"]').forEach(function(el){el.checked=on});
  updateStrategyCount();
}
// Init count on load
setTimeout(updateStrategyCount,100);

// ── Start Analysis ────────────────────────────────────
var _pollTimer=null;
function startAnalysis(){
  // Clear stale poll timer from any previous run
  if(_pollTimer){clearInterval(_pollTimer);_pollTimer=null}
  var btn=document.getElementById('btnRun');
  btn.disabled=true;
  btn.textContent='⏳ 启动中...';
  document.getElementById('runStatus').textContent='正在提交...';
  document.getElementById('progressSection').style.display='block';
  document.getElementById('resultSection').style.display='none';
  updateProgress(0,'running');
  document.getElementById('progressDetail').textContent='正在提交...';  

  var body={
    universe:document.getElementById('universe').value,
    backtest_years:parseInt(document.getElementById('backtestYears').value)||5,
    top_n_per_stock:parseInt(document.getElementById('topNPerStock').value)||5,
    top_n_global:parseInt(document.getElementById('topNGlobal').value)||10,
    workers:parseInt(document.getElementById('workers').value)||8,
    initial_capital:parseInt(document.getElementById('initialCapital').value)||1000000,
    include_alpha:document.getElementById('includeAlpha').checked,
    builtin_strategies:getSelectedBuiltinStrategies(),
  };

  if(!body.include_alpha&&body.builtin_strategies.length===0){
    toast('❌ 请至少选择一类策略');
    btn.disabled=false;
    btn.textContent='🚀 开始分析';
    return;
  }

  api('/quant/admin/api/strategy-fitness/run',{
    method:'POST',
    body:JSON.stringify(body),
    _timeout:4*3600000
  }).then(function(data){
    if(_pollTimer){clearInterval(_pollTimer);_pollTimer=null}
    btn.disabled=false;
    btn.textContent='🚀 开始分析';
    if(data.ok||data.batch_id){
      toast('✅ 分析完成');
      showResult(data);
      updateProgress(100,'done');
      document.getElementById('runStatus').textContent='已完成 ('+data.batch_id+')';
      var doneDetail='完成: '+(data.total_stocks||'-')+' 只股票, '+(data.fitness_records||0)+' 条记录';
      if(data.elapsed_seconds)doneDetail+=', 耗时 '+formatDuration(data.elapsed_seconds);
      document.getElementById('progressDetail').textContent=doneDetail;
      // Auto-refresh history and global ranking to show new batch
      loadHistory();
      loadAggregateGlobal();
    }else{
      toast('❌ '+(data.error||'分析失败'));
      document.getElementById('runStatus').textContent='失败: '+(data.error||'');
      document.getElementById('progressDetail').textContent='失败: '+(data.error||'');
    }
  }).catch(function(e){
    btn.disabled=false;
    btn.textContent='🚀 开始分析';
    document.getElementById('runStatus').textContent='错误: '+e.message;
  });

  // Poll progress
  _pollTimer=setInterval(checkStatus,5000);
}

function checkStatus(){
  api('/quant/admin/api/strategy-fitness/status',{_timeout:10000}).then(function(data){
    if(!data)return;
    var badge=document.getElementById('statusBadge');
    if(data.status==='starting'||data.status==='loading_klines'||data.status==='loading_fundamentals'||data.status==='building_bars'){
      badge.className='status-running';
      badge.textContent='● 准备中';
      document.getElementById('progressSection').style.display='block';
      var detail2=data.phase||'正在准备...';
      if(data.elapsed_seconds)detail2+=' | 耗时: '+formatDuration(data.elapsed_seconds);
      updateProgress(0,'running');
      document.getElementById('progressDetail').textContent=detail2;
    }else if(data.status==='running'||data.status==='alpha_done'){
      badge.className='status-running';
      badge.textContent='● 运行中';
      document.getElementById('progressSection').style.display='block';
      var total=data.total_strategies>0?data.total_strategies:(data.total_stocks||1);
      var done=typeof data.completed_strategies==='number'?data.completed_strategies:(data.completed_stocks||0);
      var pct=Math.min(Math.round(done/total*100),100);
      updateProgress(pct,'running');
      var detail=data.phase||(data.completed_strategies?'策略: '+data.completed_strategies+'/'+data.total_strategies:'已完成: '+data.completed_stocks+'/'+data.total_stocks+' 只股票');
      if(data.elapsed_seconds)detail+=' | 耗时: '+formatDuration(data.elapsed_seconds);
      if(data.estimated_remaining)detail+=' | 预计剩余: '+formatDuration(data.estimated_remaining);
      document.getElementById('progressDetail').textContent=detail;
    }else if(data.status==='done'){
      badge.className='status-done';
      badge.textContent='● 已完成';
      updateProgress(100,'done');
      if(data.results){
        document.getElementById('progressDetail').textContent=
          '完成: '+data.completed_stocks+' 只股票, '+
          (data.results.fitness_records||0)+' 条记录, '+
          '耗时 '+formatDuration(data.elapsed_seconds);
      }
      if(_pollTimer){clearInterval(_pollTimer);_pollTimer=null}
    }else if(data.status==='error'){
      badge.className='status-error';
      badge.textContent='● 错误';
      if(_pollTimer){clearInterval(_pollTimer);_pollTimer=null}
    }else{
      badge.className='status-idle';
      badge.textContent='● 空闲';
    }
  }).catch(function(){});
}

function updateProgress(pct,status){
  var fill=document.getElementById('progressFill');
  var label=document.getElementById('progressLabel');
  fill.style.width=pct+'%';
  label.textContent=pct+'%';
  if(status==='done')fill.style.background='var(--green)';
  else fill.style.background='var(--accent)';
}

function formatDuration(sec){
  if(!sec||sec<0)return '-';
  var h=Math.floor(sec/3600);
  var m=Math.floor((sec%3600)/60);
  var s=sec%60;
  if(h>0)return h+'时'+m+'分';
  if(m>0)return m+'分'+s+'秒';
  return s+'秒';
}

function showResult(data){
  document.getElementById('resultSection').style.display='block';
  var kpis='';
  kpis+=kpiHtml(data.total_stocks||'-','股票数');
  var stratLabel=(data.total_strategies||'-');
  if(data.alpha_strategies&&data.builtin_strategies){
    stratLabel=data.total_strategies+' (Alpha:'+data.alpha_strategies+' Builtin:'+data.builtin_strategies+')';
  }
  kpis+=kpiHtml(stratLabel,'策略数');
  kpis+=kpiHtml(data.fitness_records||'-','适配记录');
  kpis+=kpiHtml(formatDuration(data.elapsed_seconds),'总耗时');
  kpis+=kpiHtml(data.batch_id||'-','批次ID');
  document.getElementById('resultKpis').innerHTML=kpis;

  // Show global top strategies preview
  var strats=data.global_top_strategies||[];
  if(strats.length>0){
    var html='<h4 style="margin-bottom:8px;font-size:14px">🏆 全局最优策略预览</h4>';
    html+='<table><tr><th>排名</th><th>策略</th><th>总得分</th><th>覆盖股票数</th><th>平均得分</th></tr>';
    strats.forEach(function(s){
      html+='<tr><td>'+rankBadge(s.rank)+'</td><td>'+esc(s.strategy_id)+'</td>';
      html+='<td class="metric-good">'+num(s.total_score)+'</td>';
      html+='<td>'+s.stock_count+'</td><td>'+num(s.avg_score)+'</td></tr>';
    });
    html+='</table>';
    document.getElementById('resultPreview').innerHTML=html;
  }
}

// ── History ──────────────────────────────────────────
var _currentBatchId=null;
function loadHistory(){
  hideBatchDetail();
  document.getElementById('historyTable').innerHTML='<div class="loading">加载中...</div>';
  api('/quant/admin/api/strategy-fitness/history',{_timeout:15000}).then(function(data){
    var batches=data.batches||[];
    if(!batches.length){
      document.getElementById('historyTable').innerHTML='<div class="empty">暂无历史记录</div>';
      return;
    }
    var html='<table><tr><th>批次ID</th><th>时间</th><th>股票数</th><th>策略数</th><th></th><th></th></tr>';
    batches.forEach(function(b){
      html+='<tr>';
      html+='<td class="clickable-row" onclick="showBatchDetail(\\''+esc(b.batchId)+'\\')">'+esc(b.batchId)+'</td>';
      html+='<td>'+esc(b.createdAt||'-')+'</td>';
      html+='<td>'+b.stockCount+'</td>';
      html+='<td>'+b.strategyCount+'</td>';
      html+='<td><button class="btn btn-sm" onclick="showBatchDetail(\\''+esc(b.batchId)+'\\')">查看详情 →</button></td>';
      html+='<td><button class="btn btn-sm btn-danger" onclick="event.stopPropagation();deleteBatch(\\''+esc(b.batchId)+'\\')">🗑 删除</button></td>';
      html+='</tr>';
    });
    html+='</table>';
    document.getElementById('historyTable').innerHTML=html;
  }).catch(function(){
    document.getElementById('historyTable').innerHTML='<div class="empty">加载失败</div>';
  });
}

function deleteBatch(batchId){
  if(!confirm('确定要删除批次 '+batchId+' 的所有数据吗？\\n删除后全局汇总排名将自动更新。'))return;
  api('/quant/admin/api/strategy-fitness/delete-batch',{
    method:'POST',
    body:JSON.stringify({batch_id:batchId}),
    _timeout:15000
  }).then(function(data){
    if(data.ok){
      toast('✅ 已删除批次 '+batchId);
      if(_currentBatchId===batchId)hideBatchDetail();
      loadHistory();
    }else{
      toast('❌ 删除失败: '+(data.error||''));
    }
  }).catch(function(e){
    toast('❌ 删除失败: '+e.message);
  });
}

function showBatchDetail(batchId){
  _currentBatchId=batchId;
  document.getElementById('batchDetail').style.display='block';
  document.getElementById('batchDetailTitle').textContent='📊 批次详情 — '+batchId;
  document.getElementById('batchGlobalTable').innerHTML='<div class="loading">加载中...</div>';
  document.getElementById('batchStockResult').innerHTML='<div class="empty">输入代码查询该批次的个股适配</div>';
  document.getElementById('batchAllStocksTable').innerHTML='<div class="empty">点击"个股Top策略"标签加载</div>';
  switchBatchSub('global');
  // Load global ranking for this batch
  api('/quant/admin/api/strategy-fitness/global?batch_id='+encodeURIComponent(batchId),{_timeout:15000}).then(function(data){
    var strats=data.strategies||[];
    if(!strats.length){
      document.getElementById('batchGlobalTable').innerHTML='<div class="empty">该批次无全局排名数据</div>';
      return;
    }
    var maxScore=Math.max.apply(null,strats.map(function(s){return s.total_score||0}))||1;
    var html='<table><tr><th>排名</th><th>策略</th><th>总得分</th><th>覆盖股票</th><th>平均得分</th><th>最高</th><th>最低</th><th>分布</th></tr>';
    strats.forEach(function(s){
      var barW=Math.round((s.total_score/maxScore)*100);
      html+='<tr><td>'+rankBadge(s.rank)+'</td>';
      html+='<td><strong>'+esc(s.strategy_id)+'</strong></td>';
      html+='<td class="metric-good" style="font-weight:700">'+num(s.total_score)+'</td>';
      html+='<td>'+s.stock_count+'</td>';
      html+='<td>'+num(s.avg_score)+'</td>';
      html+='<td>'+num(s.max_score)+'</td>';
      html+='<td>'+num(s.min_score)+'</td>';
      html+='<td><span class="score-bar" style="width:'+barW+'px;background:var(--accent)"></span></td></tr>';
    });
    html+='</table>';
    document.getElementById('batchGlobalTable').innerHTML=html;
  }).catch(function(){
    document.getElementById('batchGlobalTable').innerHTML='<div class="empty">加载失败</div>';
  });
}

function hideBatchDetail(){
  _currentBatchId=null;
  document.getElementById('batchDetail').style.display='none';
}

// ── Batch: All Stocks Top Strategy ───────────────────
var _batchAllStocksOffset=0;
var _batchAllStocksLimit=50;
var _batchAllStocksTotal=0;
function loadBatchAllStocks(offset){
  if(!_currentBatchId)return;
  if(offset!==undefined)_batchAllStocksOffset=offset;
  document.getElementById('batchAllStocksTable').innerHTML='<div class="loading">加载中...</div>';
  api('/quant/admin/api/strategy-fitness/all-stocks?batch_id='+encodeURIComponent(_currentBatchId)+'&limit='+_batchAllStocksLimit+'&offset='+_batchAllStocksOffset,{_timeout:15000}).then(function(data){
    var stocks=data.stocks||[];
    _batchAllStocksTotal=data.total||0;
    document.getElementById('batchAllStocksCount').textContent='共 '+_batchAllStocksTotal+' 只个股';
    var curPage=Math.floor(_batchAllStocksOffset/_batchAllStocksLimit)+1;
    var totalPages=Math.ceil(_batchAllStocksTotal/_batchAllStocksLimit)||1;
    document.getElementById('batchAllStocksPageInfo').textContent='第 '+curPage+'/'+totalPages+' 页';
    document.getElementById('btnBatchPrev').disabled=(_batchAllStocksOffset<=0);
    document.getElementById('btnBatchNext').disabled=(_batchAllStocksOffset+_batchAllStocksLimit>=_batchAllStocksTotal);
    if(!stocks.length){
      document.getElementById('batchAllStocksTable').innerHTML='<div class="empty">暂无数据</div>';
      return;
    }
    var html='<table><tr><th>#</th><th>股票代码</th><th>股票名称</th><th>最优策略</th><th>综合得分</th><th>总收益</th><th>年化收益</th><th>夏普</th><th>最大回撤</th><th>胜率</th><th>交易数</th></tr>';
    stocks.forEach(function(s,i){
      var idx=_batchAllStocksOffset+i+1;
      html+='<tr class="clickable-row" onclick="batchJumpToStock(\\''+esc(s.symbol||s.stockName)+'\\')">';
      html+='<td style="color:var(--text-dim)">'+idx+'</td>';
      html+='<td><strong>'+esc(s.symbol)+'</strong></td>';
      html+='<td>'+esc(s.stockName||s.stock_name||'-')+'</td>';
      html+='<td>'+esc(s.strategyId||s.strategy_id||'-')+'</td>';
      html+='<td class="metric-good" style="font-weight:700">'+num(s.compositeScore||s.composite_score)+'</td>';
      html+='<td style="color:'+pctColor(((s.totalReturn||s.total_return||0))*100)+'">'+pct(((s.totalReturn||s.total_return||0))*100)+'</td>';
      html+='<td style="color:'+pctColor(((s.annualizedReturn||s.annualized_return||0))*100)+'">'+pct(((s.annualizedReturn||s.annualized_return||0))*100)+'</td>';
      html+='<td>'+((s.sharpeRatio||s.sharpe_ratio)!=null?(s.sharpeRatio||s.sharpe_ratio).toFixed(2):'-')+'</td>';
      html+='<td class="metric-bad">'+pct(-((s.maxDrawdown||s.max_drawdown||0))*100)+'</td>';
      html+='<td>'+((((s.winRate||s.win_rate||0)))*100).toFixed(1)+'%</td>';
      html+='<td>'+(s.totalTrades||s.total_trades||0)+'</td>';
      html+='</tr>';
    });
    html+='</table>';
    document.getElementById('batchAllStocksTable').innerHTML=html;
  }).catch(function(){
    document.getElementById('batchAllStocksTable').innerHTML='<div class="empty">加载失败</div>';
  });
}
function batchAllStocksPage(dir){
  var newOffset=_batchAllStocksOffset+(dir*_batchAllStocksLimit);
  if(newOffset<0)newOffset=0;
  if(newOffset>=_batchAllStocksTotal&&_batchAllStocksTotal>0)return;
  loadBatchAllStocks(newOffset);
}
function batchJumpToStock(sym){
  document.getElementById('batchQuerySymbol').value=sym;
  switchBatchSub('stock');
  queryBatchStock();
}

// ── Batch: Stock Query ──────────────────────────────
function queryBatchStock(){
  if(!_currentBatchId){toast('无批次');return}
  var sym=document.getElementById('batchQuerySymbol').value.trim();
  if(!sym||sym.length<6){toast('请输入6位股票代码');return}
  document.getElementById('batchStockResult').innerHTML='<div class="loading">查询中...</div>';
  api('/quant/admin/api/strategy-fitness/stock?symbol='+encodeURIComponent(sym)+'&batch_id='+encodeURIComponent(_currentBatchId),{_timeout:15000}).then(function(data){
    var strats=data.strategies||[];
    if(!strats.length){
      document.getElementById('batchStockResult').innerHTML='<div class="empty">该批次无 '+esc(sym)+' 的数据</div>';
      return;
    }
    var html='<div style="font-size:13px;font-weight:600;margin-bottom:8px">'+esc(sym)+' Top '+strats.length+' 策略</div>';
    html+='<table><tr><th>#</th><th>策略</th><th>综合得分</th><th>总收益</th><th>年化收益</th><th>夏普</th><th>最大回撤</th><th>胜率</th><th>盈亏比</th><th>交易数</th></tr>';
    strats.forEach(function(s){
      html+='<tr><td>'+rankBadge(s.rank)+'</td>';
      html+='<td><strong>'+esc(s.strategy_id)+'</strong></td>';
      html+='<td class="metric-good" style="font-weight:700">'+num(s.composite_score)+'</td>';
      html+='<td style="color:'+pctColor((s.total_return||0)*100)+'">'+pct((s.total_return||0)*100)+'</td>';
      html+='<td style="color:'+pctColor((s.annualized_return||0)*100)+'">'+pct((s.annualized_return||0)*100)+'</td>';
      html+='<td>'+(s.sharpe_ratio!=null?s.sharpe_ratio.toFixed(2):'-')+'</td>';
      html+='<td class="metric-bad">'+pct(-(s.max_drawdown||0)*100)+'</td>';
      html+='<td>'+((s.win_rate||0)*100).toFixed(1)+'%</td>';
      html+='<td>'+(s.profit_loss_ratio!=null?s.profit_loss_ratio.toFixed(2):'-')+'</td>';
      html+='<td>'+(s.total_trades||0)+'</td>';
      html+='</tr>';
    });
    html+='</table>';
    document.getElementById('batchStockResult').innerHTML=html;
  }).catch(function(){
    document.getElementById('batchStockResult').innerHTML='<div class="empty">查询失败</div>';
  });
}

document.addEventListener('keypress',function(e){
  if(e.key==='Enter'&&document.activeElement&&document.activeElement.id==='batchQuerySymbol')queryBatchStock();
});

// ── Aggregate Global Ranking ─────────────────────────
function loadAggregateGlobal(){
  document.getElementById('aggregateGlobalTable').innerHTML='<div class="loading">加载中...</div>';
  api('/quant/admin/api/strategy-fitness/aggregate-global?top_n=20',{_timeout:15000}).then(function(data){
    var strats=data.strategies||[];
    if(!strats.length){
      document.getElementById('aggregateGlobalTable').innerHTML='<div class="empty">暂无数据，请先运行分析</div>';
      return;
    }
    var maxScore=Math.max.apply(null,strats.map(function(s){return Math.abs(s.total_score)||0}))||1;
    var html='<div style="font-size:12px;color:var(--text-dim);margin-bottom:12px">跨 '+(data.total_batch_count||'?')+' 个批次聚合</div>';
    html+='<table><tr><th>排名</th><th>策略ID</th><th>总得分</th><th>出现次数</th><th>覆盖股票数</th><th>参与批次</th><th>平均得分</th><th>最高</th><th>最低</th><th>分布</th></tr>';
    strats.forEach(function(s){
      var barW=Math.max(2,Math.round((Math.abs(s.total_score)/maxScore)*100));
      var barColor=s.total_score>=0?'var(--green)':'var(--red)';
      html+='<tr><td>'+rankBadge(s.rank)+'</td>';
      html+='<td><strong>'+esc(s.strategy_id)+'</strong></td>';
      html+='<td style="font-weight:700;color:'+(s.total_score>=0?'var(--green)':'var(--red)')+'">'+num(s.total_score)+'</td>';
      html+='<td>'+s.appearance_count+'</td>';
      html+='<td>'+s.stock_count+'</td>';
      html+='<td>'+s.batch_count+'</td>';
      html+='<td>'+num(s.avg_score)+'</td>';
      html+='<td>'+num(s.max_score)+'</td>';
      html+='<td>'+num(s.min_score)+'</td>';
      html+='<td><span class="score-bar" style="width:'+barW+'px;background:'+barColor+'"></span></td>';
      html+='</tr>';
    });
    html+='</table>';
    document.getElementById('aggregateGlobalTable').innerHTML=html;
  }).catch(function(){
    document.getElementById('aggregateGlobalTable').innerHTML='<div class="empty">加载失败</div>';
  });
}

// ── Helpers ────────────────────────────────────────────
function rankBadge(r){
  var cls=r===1?'rank-1':r===2?'rank-2':r===3?'rank-3':'rank-n';
  return '<span class="rank-badge '+cls+'">#'+r+'</span>';
}

// Initial status check
checkStatus();
</script>
</body>
</html>`;
}

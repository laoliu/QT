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
  <button onclick="switchTab('pool')">📊 策略池回测</button>
  <button onclick="switchTab('autoassign')">🎯 智能分配</button>
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

<!-- Tab 5: Auto-Assign (fitness-based per-stock strategy assignment) -->
<div id="tab-autoassign" class="tab-content">
  <div class="card">
    <h3>🎯 智能策略分配 <span style="font-size:12px;font-weight:400;color:var(--text-dim)">基于适配分析，为每只股票分配最优策略</span></h3>
    <p style="font-size:13px;color:var(--text-dim);margin-bottom:16px">
      根据 fitness 分析结果，每只股票选择 composite_score 最高的策略（rank=1），并结合策略池回测表现进行组合加权。
      出现在高表现池组合中的股票会获得分数加成，确保个股适配与组合验证双重保障。
    </p>
    <div style="display:flex;gap:12px;align-items:center;margin-bottom:16px;flex-wrap:wrap">
      <div style="flex:0 0 auto">
        <label style="margin-bottom:0;font-size:12px">使用前 N 个策略</label>
        <input type="number" id="autoAssignTopN" value="10" min="1" max="100" style="width:70px;margin-top:2px" onchange="loadAutoAssignPreview()">
      </div>
      <button class="btn btn-primary" id="btnAutoAssignPreview" onclick="loadAutoAssignPreview()">🔍 预览分配方案</button>
      <button class="btn" onclick="loadAutoAssignPreview()" style="font-size:12px">🔄 刷新</button>
      <span id="autoAssignStatus" style="font-size:13px;color:var(--text-dim)"></span>
    </div>
  </div>
  <div id="autoAssignPreviewSection" style="display:none">
    <div class="card">
      <h3>📋 分配方案预览</h3>
      <div class="grid grid-3" id="autoAssignKpis" style="margin-bottom:16px"></div>
      <div id="autoAssignTable"></div>
      <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border);display:flex;gap:12px;align-items:center;flex-wrap:wrap">
        <div style="flex:0 0 auto">
          <label style="margin-bottom:0">每策略资金池（万元，留空不限制）</label>
          <input type="number" id="autoAssignBudget" placeholder="如 10" min="1" style="width:120px;margin-top:4px">
        </div>
        <button class="btn btn-primary" id="btnAutoAssignApply" onclick="applyAutoAssign()" style="margin-top:18px">🚀 确认分配</button>
        <span id="autoAssignApplyStatus" style="font-size:13px;color:var(--text-dim);margin-top:18px"></span>
      </div>
    </div>
  </div>
  <div id="autoAssignResultSection" style="display:none">
    <div class="card">
      <h3>✅ 分配结果</h3>
      <div id="autoAssignResult"></div>
    </div>
  </div>
</div>

<!-- Tab 4: Pool Strategy Backtest -->
<div id="tab-pool" class="tab-content">
  <div class="card">
    <h3>📊 策略池回测</h3>
    <p style="font-size:13px;color:var(--text-dim);margin-bottom:12px">
      选择股票池和策略类型，进行 N 年组合回测。对比不同池×策略组合的收益、回撤、夏普等指标。
    </p>
    <div class="form-row">
      <div style="min-width:350px">
        <label>选择股票池（可多选）</label>
        <div id="poolCheckboxes" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:4px">
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer"><input type="checkbox" value="auto_momentum" checked> 强势动量</label>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer"><input type="checkbox" value="auto_tech" checked> 科创精选</label>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer"><input type="checkbox" value="auto_mixed" checked> 综合最优</label>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer"><input type="checkbox" value="auto_hybrid" checked> 蓝筹科技混合</label>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer"><input type="checkbox" value="auto_mega"> 超大盘蓝筹</label>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer"><input type="checkbox" value="auto_value"> 深度价值</label>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer"><input type="checkbox" value="auto_growth"> 高成长</label>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer"><input type="checkbox" value="auto_dip"> 超跌反弹</label>
        </div>
      </div>
    </div>
    <div class="form-row">
      <div>
        <label>策略类型（可多选）</label>
        <div style="display:flex;gap:8px;margin-top:4px">
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer"><input type="checkbox" value="mixed" id="stMixed" checked> 混合策略 (MA+动量)</label>
          <label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer"><input type="checkbox" value="momentum" id="stMomentum" checked> 趋势动量</label>
        </div>
      </div>
      <div>
        <label>回测年限</label>
        <select id="poolPeriodYears">
          <option value="1">1年</option>
          <option value="2">2年</option>
          <option value="3">3年</option>
          <option value="5" selected>5年</option>
        </select>
      </div>
      <div>
        <label>调仓频率（天）</label>
        <select id="poolRebalanceDays">
          <option value="3">3天</option>
          <option value="5" selected>5天</option>
          <option value="10">10天</option>
          <option value="20">20天</option>
        </select>
      </div>
      <div style="display:flex;align-items:flex-end">
        <button class="btn btn-primary" id="poolRunBtn" onclick="runPoolBacktest()">▶ 开始回测</button>
      </div>
    </div>
  </div>

  <!-- Progress -->
  <div class="card" id="poolProgressCard" style="display:none">
    <h3>⏳ 回测进度</h3>
    <div class="progress-bar">
      <div class="fill" id="poolProgressFill" style="width:0%"></div>
      <div class="label" id="poolProgressLabel">0%</div>
    </div>
    <p id="poolProgressPhase" style="font-size:12px;color:var(--text-dim)"></p>
  </div>

  <!-- Results -->
  <div class="card" id="poolResultCard" style="display:none">
    <h3>📈 回测结果</h3>
    <div id="poolResultSummary" style="margin-bottom:12px"></div>
    <div style="overflow-x:auto">
      <table id="poolResultTable">
        <thead><tr>
          <th>排名</th><th>股票池</th><th>策略</th><th>股票数</th>
          <th>总收益</th><th>年化收益</th><th>最大回撤</th>
          <th>夏普</th><th>胜率</th><th>利润因子</th>
          <th>卡尔马</th><th>累计倍数</th><th>综合分</th>
          <th>操作</th>
        </tr></thead>
        <tbody id="poolResultBody"></tbody>
      </table>
    </div>
  </div>

  <!-- Equity Curve -->
  <div class="card" id="poolEquityCard" style="display:none">
    <h3>📉 净值曲线</h3>
    <canvas id="poolEquityCanvas" width="1200" height="300" style="width:100%;max-height:300px"></canvas>
  </div>

  <!-- History -->
  <div class="card">
    <h3>📋 历史回测记录</h3>
    <button class="btn btn-sm" onclick="loadPoolHistory()" style="margin-bottom:12px">🔄 刷新</button>
    <div id="poolHistoryTable"><div class="empty">点击刷新或运行回测后查看</div></div>
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
  var map={run:0,history:1,aggregate:2,pool:3,autoassign:4};
  if(map[id]!==undefined&&btns[map[id]])btns[map[id]].classList.add('active');
  if(id==='history')loadHistory();
  if(id==='aggregate')loadAggregateGlobal();
  if(id==='pool')loadPoolHistory();
  if(id==='autoassign')loadAutoAssignPreview();
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
    if((data.ok||data.batch_id)&&!data.error){
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

// ── Pool Backtest ──────────────────────────────────────
var _poolRunning=false;
var _poolTimer=null;
var _poolLastResult=null;

// 策略池 label 映射
var POOL_LABELS={
  auto_momentum:'强势动量',auto_tech:'科创精选',auto_mixed:'综合最优',
  auto_hybrid:'蓝筹科技混合',auto_mega:'超大盘蓝筹',auto_value:'深度价值',
  auto_growth:'高成长',auto_dip:'超跌反弹'
};
var STRATEGY_LABELS={mixed:'混合策略',momentum:'趋势动量'};

async function runPoolBacktest(){
  if(_poolRunning){toast('回测正在运行中...','warn');return;}
  // 收集选中的池
  var poolChecked=[];
  document.querySelectorAll('#poolCheckboxes input:checked').forEach(function(el){poolChecked.push(el.value)});
  if(!poolChecked.length){toast('请至少选择一个股票池','warn');return;}
  // 收集策略
  var strategies=[];
  if(document.getElementById('stMixed').checked)strategies.push('mixed');
  if(document.getElementById('stMomentum').checked)strategies.push('momentum');
  if(!strategies.length){toast('请至少选择一种策略','warn');return;}

  var periodYears=parseInt(document.getElementById('poolPeriodYears').value)||5;
  var rebalanceDays=parseInt(document.getElementById('poolRebalanceDays').value)||5;

  // 先获取股票池的股票列表 — 通过 pool_optimizer
  toast('正在获取股票池列表...','info');
  var poolSymbols={};
  try{
    var poolRes=await api('/quant/admin/api/strategy-fitness/pools');
    if(poolRes&&poolRes.pools){
      poolRes.pools.forEach(function(p){
        if(poolChecked.indexOf(p.key)>=0){
          poolSymbols[p.key]={label:p.label||POOL_LABELS[p.key]||p.key,symbols:p.symbols||[]};
        }
      });
    }
  }catch(e){
    toast('获取股票池失败: '+e,'error');return;
  }

  // 构建 pool_configs: 每个 pool×strategy 一个条目
  var poolConfigs=[];
  poolChecked.forEach(function(poolKey){
    var info=poolSymbols[poolKey];
    if(!info||!info.symbols||!info.symbols.length){return;}
    strategies.forEach(function(st){
      poolConfigs.push({
        pool_key:poolKey,pool_label:info.label,
        strategy_type:st,symbols:info.symbols
      });
    });
  });

  if(!poolConfigs.length){toast('选中的股票池无可用股票数据','warn');return;}

  _poolRunning=true;
  document.getElementById('poolRunBtn').disabled=true;
  document.getElementById('poolProgressCard').style.display='block';
  document.getElementById('poolResultCard').style.display='none';
  document.getElementById('poolEquityCard').style.display='none';

  // Start progress polling
  _poolTimer=setInterval(checkPoolProgress,2000);

  try{
    var result=await api('/quant/admin/api/strategy-fitness/pool-backtest',{
      method:'POST',
      body:JSON.stringify({
        pool_configs:poolConfigs,
        period_years:periodYears,
        rebalance_days:rebalanceDays,
        initial_capital:1000000
      }),
      _timeout:30*60*1000
    });
    _poolRunning=false;
    document.getElementById('poolRunBtn').disabled=false;
    if(_poolTimer){clearInterval(_poolTimer);_poolTimer=null;}
    document.getElementById('poolProgressCard').style.display='none';

    if(result&&result.ok){
      _poolLastResult=result;
      renderPoolResults(result);
      toast('回测完成: '+result.completed+' 组合, 耗时 '+result.elapsed_seconds+'s','success');
      loadPoolHistory();
    }else{
      toast('回测失败: '+(result&&result.error||'未知错误'),'error');
    }
  }catch(e){
    _poolRunning=false;
    document.getElementById('poolRunBtn').disabled=false;
    if(_poolTimer){clearInterval(_poolTimer);_poolTimer=null;}
    document.getElementById('poolProgressCard').style.display='none';
    toast('回测请求失败: '+e,'error');
  }
}

async function checkPoolProgress(){
  try{
    var p=await api('/quant/admin/api/strategy-fitness/pool-backtest/status');
    if(!p||p.status==='idle')return;
    var pct=p.total>0?Math.round(p.completed/p.total*100):0;
    document.getElementById('poolProgressFill').style.width=pct+'%';
    document.getElementById('poolProgressLabel').textContent=pct+'%';
    document.getElementById('poolProgressPhase').textContent=(p.phase||'')+
      (p.elapsed_seconds?' ('+p.elapsed_seconds+'s)':'');
  }catch(e){}
}

function renderPoolResults(result){
  document.getElementById('poolResultCard').style.display='block';
  var summary='共 <b>'+result.total_tasks+'</b> 组合, 成功 <b>'+result.completed+
    '</b>, 失败 <b>'+result.failed+'</b>, 耗时 <b>'+result.elapsed_seconds+'</b>s';
  document.getElementById('poolResultSummary').innerHTML=summary;

  var rows=result.results||[];
  var tbody=document.getElementById('poolResultBody');
  if(!rows.length){tbody.innerHTML='<tr><td colspan="14" class="empty">无结果</td></tr>';return;}

  var html='';
  rows.forEach(function(r){
    var stLabel=STRATEGY_LABELS[r.strategy_type]||r.strategy_type;
    html+='<tr>';
    html+='<td>'+rankBadge(r.rank)+'</td>';
    html+='<td>'+esc(r.pool_label)+'</td>';
    html+='<td>'+esc(stLabel)+'</td>';
    html+='<td>'+r.symbol_count+'</td>';
    html+='<td class="'+(r.total_return_pct>=0?'metric-good':'metric-bad')+'">'+(r.total_return_pct>=0?'+':'')+r.total_return_pct+'%</td>';
    html+='<td class="'+(r.annualized_return_pct>=0?'metric-good':'metric-bad')+'">'+(r.annualized_return_pct>=0?'+':'')+r.annualized_return_pct+'%</td>';
    html+='<td class="'+(r.max_drawdown_pct<=20?'metric-good':r.max_drawdown_pct<=40?'metric-ok':'metric-bad')+'">'+r.max_drawdown_pct+'%</td>';
    html+='<td class="'+(r.sharpe>=1?'metric-good':r.sharpe>=0.5?'metric-ok':'metric-bad')+'">'+r.sharpe+'</td>';
    html+='<td class="'+(r.win_rate_pct>=50?'metric-good':'metric-bad')+'">'+r.win_rate_pct+'%</td>';
    html+='<td class="'+(r.profit_factor>=1.5?'metric-good':r.profit_factor>=1?'metric-ok':'metric-bad')+'">'+r.profit_factor+'</td>';
    html+='<td>'+r.calmar+'</td>';
    html+='<td>'+(r.cum_multiple||'-')+'x</td>';
    html+='<td><span class="score-bar" style="width:'+Math.min(100,Math.max(5,r.composite_score))+'px;background:'+(r.composite_score>=50?'var(--green)':r.composite_score>=30?'var(--yellow)':'var(--red)')+'"></span> '+r.composite_score+'</td>';
    html+='<td><button class="btn btn-sm" onclick="showPoolEquity('+rows.indexOf(r)+')">📈</button></td>';
    html+='</tr>';
  });
  tbody.innerHTML=html;
}

function showPoolEquity(idx){
  if(!_poolLastResult||!_poolLastResult.results||!_poolLastResult.results[idx])return;
  var r=_poolLastResult.results[idx];
  var curve=r.equity_curve;
  if(!curve||!curve.length){toast('无净值数据','warn');return;}

  document.getElementById('poolEquityCard').style.display='block';
  var canvas=document.getElementById('poolEquityCanvas');
  var ctx=canvas.getContext('2d');
  var W=canvas.width,H=canvas.height;
  ctx.clearRect(0,0,W,H);

  var vals=curve.map(function(p){return p.value});
  var minV=Math.min.apply(null,vals)*0.98;
  var maxV=Math.max.apply(null,vals)*1.02;
  var range=maxV-minV||1;

  // Background
  ctx.fillStyle='#1a1a2e';ctx.fillRect(0,0,W,H);

  // Grid lines
  ctx.strokeStyle='#333';ctx.lineWidth=0.5;
  for(var g=0;g<5;g++){
    var gy=H*0.1+g*(H*0.8/4);
    ctx.beginPath();ctx.moveTo(40,gy);ctx.lineTo(W-10,gy);ctx.stroke();
    var gv=maxV-(g/(4))*(range);
    ctx.fillStyle='#888';ctx.font='10px monospace';ctx.fillText(Math.round(gv).toLocaleString(),0,gy+3);
  }

  // Line
  ctx.beginPath();ctx.strokeStyle='#4fc3f7';ctx.lineWidth=1.5;
  for(var i=0;i<curve.length;i++){
    var x=40+(W-50)*i/(curve.length-1);
    var y=H*0.1+(maxV-vals[i])/range*(H*0.8);
    if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  }
  ctx.stroke();

  // Title
  var stLabel=STRATEGY_LABELS[r.strategy_type]||r.strategy_type;
  ctx.fillStyle='#fff';ctx.font='bold 13px sans-serif';
  ctx.fillText(r.pool_label+' × '+stLabel+'  |  '+r.period_years+'年  |  年化 '+(r.annualized_return_pct>=0?'+':'')+r.annualized_return_pct+'%  夏普 '+r.sharpe,50,20);

  // Dates
  ctx.fillStyle='#888';ctx.font='10px monospace';
  if(curve.length>0)ctx.fillText(curve[0].date,40,H-5);
  if(curve.length>1)ctx.fillText(curve[curve.length-1].date,W-90,H-5);
}

async function loadPoolHistory(){
  try{
    var data=await api('/quant/admin/api/strategy-fitness/pool-backtest/history');
    if(!data||!data.records){document.getElementById('poolHistoryTable').innerHTML='<div class="empty">暂无历史记录</div>';return;}
    var records=data.records;
    if(!records.length){document.getElementById('poolHistoryTable').innerHTML='<div class="empty">暂无历史记录</div>';return;}
    // Group by run_id
    var runs={};var runOrder=[];
    records.forEach(function(r){
      if(!runs[r.run_id]){runs[r.run_id]=[];runOrder.push(r.run_id)}
      runs[r.run_id].push(r);
    });

    var html='<table><thead><tr><th>批次</th><th>时间</th><th>组合数</th><th>最佳组合</th><th>最佳年化</th><th>最佳夏普</th><th>操作</th></tr></thead><tbody>';
    runOrder.forEach(function(runId){
      var items=runs[runId];
      var best=items[0];
      items.forEach(function(it){
        if((it.annualized_return_pct||0)>(best.annualized_return_pct||0))best=it;
      });
      html+='<tr>';
      html+='<td style="font-family:monospace;font-size:12px">'+esc(runId)+'</td>';
      html+='<td>'+esc(items[0].created_at||'')+'</td>';
      html+='<td>'+items.length+'</td>';
      html+='<td>'+esc(best.pool_label)+' × '+esc(STRATEGY_LABELS[best.strategy_type]||best.strategy_type)+'</td>';
      html+='<td class="'+(best.annualized_return_pct>=0?'metric-good':'metric-bad')+'">'+(best.annualized_return_pct>=0?'+':'')+
        (best.annualized_return_pct||0).toFixed(2)+'%</td>';
      html+='<td class="'+(best.sharpe>=1?'metric-good':'metric-bad')+'">'+(best.sharpe||0).toFixed(2)+'</td>';
      html+='<td>';
      html+='<button class="btn btn-sm btn-primary" style="margin-right:4px" onclick="applyPoolCombo(\\''+esc(runId)+'\\',\\''+esc(best.pool_key)+'\\',\\''+esc(best.strategy_type)+'\\',\\''+esc(best.pool_label)+' × '+esc(STRATEGY_LABELS[best.strategy_type]||best.strategy_type)+'\\','+best.symbol_count+','+(best.annualized_return_pct||0).toFixed(2)+','+(best.sharpe||0).toFixed(2)+')">📋 应用</button>';
      html+='<button class="btn btn-sm btn-danger" onclick="deletePoolRun(\\''+esc(runId)+'\\')">🗑️</button>';
      html+='</td>';
      html+='</tr>';
    });
    html+='</tbody></table>';
    document.getElementById('poolHistoryTable').innerHTML=html;
  }catch(e){
    document.getElementById('poolHistoryTable').innerHTML='<div class="empty">加载失败: '+e+'</div>';
  }
}

async function deletePoolRun(runId){
  if(!confirm('确认删除批次 '+runId+' 的所有回测记录？'))return;
  try{
    var res=await api('/quant/admin/api/strategy-fitness/pool-backtest/delete',{
      method:'POST',body:JSON.stringify({run_id:runId})
    });
    if(res&&res.ok){toast('已删除','success');loadPoolHistory();}
    else{toast('删除失败','error');}
  }catch(e){toast('删除失败: '+e,'error');}
}

async function applyPoolCombo(runId,poolKey,strategyType,comboLabel,symbolCount,annReturn,sharpe){
  // 先获取当前 broker 类型，让用户明确知道应用到什么环境
  var brokerLabel='未知';
  try{
    var st=await api('/quant/admin/api/engine-status');
    var bt=st&&st.brokerType||'paper';
    brokerLabel=bt==='paper'?'🟡 虚拟盘 (Paper)':'🔴 实盘 ('+bt+')';
  }catch(e){brokerLabel='⚠️ 无法获取';}
  var budgetStr=prompt(
    '请设置该策略的独立资金池（万元）\\n\\n'+
    '🔧 当前交易模式：'+brokerLabel+'\\n'+
    '组合：'+comboLabel+'\\n'+
    '股票数：'+symbolCount+'\\n'+
    '回测年化：'+(annReturn>=0?'+':'')+annReturn+'%\\n'+
    '夏普比率：'+sharpe+'\\n\\n'+
    '策略将仅使用该预算进行交易，与其他持仓隔离：',
    '10'
  );
  if(budgetStr===null)return;
  var budgetWan=parseFloat(budgetStr);
  if(isNaN(budgetWan)||budgetWan<=0){toast('❌ 请输入有效的资金预算（万元）','error');return;}
  var capitalBudget=budgetWan*10000;
  var msg='确认应用策略？\\n\\n'+
    '🔧 交易模式：'+brokerLabel+'\\n'+
    '💰 资金池：'+budgetWan+' 万元（¥'+capitalBudget.toLocaleString()+'）\\n'+
    '组合：'+comboLabel+'\\n'+
    '股票数：'+symbolCount+'\\n\\n'+
    '⚠️ 该策略独立使用 '+budgetWan+' 万资金池，不影响其他持仓';
  if(!confirm(msg))return;
  try{
    var res=await api('/quant/admin/api/strategy-fitness/pool-backtest/apply',{
      method:'POST',body:JSON.stringify({run_id:runId,pool_key:poolKey,strategy_type:strategyType,capital_budget:capitalBudget})
    });
    if(res&&res.ok){
      var modeTag=res.brokerType==='paper'?'[虚拟盘]':'[实盘]';
      toast('✅ '+modeTag+' '+res.message+' 资金池:'+budgetWan+'万 (ID: '+(res.strategy&&res.strategy.id||'')+')', 'success');
      loadPoolHistory();
    }else{
      toast('❌ '+(res&&res.error||'应用失败'),'error');
    }
  }catch(e){toast('❌ 应用失败: '+e,'error');}
}

// ── Auto-Assign (fitness-based per-stock strategy) ────
var _autoAssignPreview=null;

async function loadAutoAssignPreview(){
  document.getElementById('autoAssignStatus').textContent='加载中...';
  document.getElementById('autoAssignPreviewSection').style.display='none';
  document.getElementById('autoAssignResultSection').style.display='none';
  try{
    var topN=parseInt(document.getElementById('autoAssignTopN').value)||10;
    var data=await api('/quant/admin/api/strategy-fitness/auto-assign/preview?top_n='+topN,{_timeout:30000});
    if(!data||!data.ok){
      document.getElementById('autoAssignStatus').textContent='❌ '+(data&&data.error||'加载失败');
      return;
    }
    _autoAssignPreview=data;
    document.getElementById('autoAssignStatus').textContent='批次: '+data.batch_id;
    document.getElementById('autoAssignPreviewSection').style.display='block';

    // KPIs
    var kpiHtml='';
    kpiHtml+='<div class="kpi-card"><div class="kpi-value">'+(data.selected_stocks||data.total_stocks)+'<span style="font-size:12px;color:var(--text-dim)"> / '+data.total_stocks+'</span></div><div class="kpi-label">选中 / 全部股票</div></div>';
    kpiHtml+='<div class="kpi-card"><div class="kpi-value">'+(data.selected_strategies||data.total_strategies)+'<span style="font-size:12px;color:var(--text-dim)"> / '+data.total_strategies+'</span></div><div class="kpi-label">选中 / 全部策略 (Top '+topN+')</div></div>';
    kpiHtml+='<div class="kpi-card"><div class="kpi-value">'+(data.existing_auto_assign_count||0)+'</div><div class="kpi-label">已有自动分配 (将被替换)</div></div>';
    var pb=data.pool_boost_info;
    if(pb&&pb.enabled){
      kpiHtml+='<div class="kpi-card" style="border-color:var(--accent)"><div class="kpi-value" style="color:var(--accent)">'+pb.boosted_stocks+'</div><div class="kpi-label">🚀 池回测加权股票</div></div>';
    }else{
      kpiHtml+='<div class="kpi-card"><div class="kpi-value" style="color:var(--text-dim)">-</div><div class="kpi-label">池回测加权 (无数据)</div></div>';
    }
    document.getElementById('autoAssignKpis').innerHTML=kpiHtml;
    // Pool boost combos tip
    if(pb&&pb.enabled&&pb.pool_combos&&pb.pool_combos.length){
      document.getElementById('autoAssignKpis').insertAdjacentHTML('afterend',
        '<div style="font-size:11px;color:var(--text-dim);margin-bottom:12px">📊 组合加权来源: '+pb.pool_combos.map(function(c){return esc(c)}).join(', ')+'</div>');
    }

    // Strategy table
    var strats=data.strategies||[];
    if(!strats.length){
      document.getElementById('autoAssignTable').innerHTML='<div class="empty">无数据</div>';
      return;
    }
    var maxScore=Math.max.apply(null,strats.map(function(s){return Math.abs(s.totalScore)||0}))||1;
    var hasBoosted=data.pool_boost_info&&data.pool_boost_info.enabled;
    var html='<table><thead><tr><th>#</th><th>策略</th><th>股票数</th><th>平均分</th><th>总分</th><th>分布</th>'+(hasBoosted?'<th>🚀 加权</th>':'')+'<th>股票列表</th></tr></thead><tbody>';
    strats.forEach(function(s,idx){
      var isSelected=s.selected!==false;
      var rowStyle=isSelected?'':'opacity:0.4;text-decoration:line-through';
      var barW=Math.max(4,Math.round((Math.abs(s.totalScore)/maxScore)*120));
      var barColor=s.avgScore>=30?'var(--green)':s.avgScore>=15?'var(--yellow)':'var(--red)';
      // Count boosted stocks in this group
      var boostedInGroup=s.stocks.filter(function(st){return st.poolBoost}).length;
      html+='<tr style="'+rowStyle+'">';
      html+='<td style="font-size:11px;color:var(--text-dim)">'+(idx+1)+'</td>';
      html+='<td><strong>'+esc(s.strategyId)+'</strong>'+(isSelected?'':' <span style="font-size:10px;color:var(--red)">跳过</span>')+'</td>';
      html+='<td style="font-weight:600">'+s.stockCount+'</td>';
      html+='<td class="'+(s.avgScore>=30?'metric-good':s.avgScore>=15?'metric-ok':'metric-bad')+'">'+s.avgScore.toFixed(2)+'</td>';
      html+='<td style="font-weight:600">'+s.totalScore.toFixed(1)+'</td>';
      html+='<td><span class="score-bar" style="width:'+barW+'px;background:'+barColor+'"></span></td>';
      if(hasBoosted){
        html+='<td style="font-size:12px">';
        if(boostedInGroup>0) html+='<span style="color:var(--accent)">'+boostedInGroup+'/'+s.stockCount+'</span>';
        else html+='<span style="color:var(--text-dim)">-</span>';
        html+='</td>';
      }
      // Stock list (show first 8, with boost indicator)
      var stocksStr=s.stocks.slice(0,8).map(function(st){
        var label=st.symbol+(st.name?' '+st.name:'');
        if(st.poolBoost) label+=' 🚀';
        return label;
      }).join(', ');
      if(s.stocks.length>8)stocksStr+=' ... 等 '+s.stocks.length+' 只';
      html+='<td style="font-size:11px;color:var(--text-dim);max-width:400px;white-space:normal;word-break:break-all">'+esc(stocksStr)+'</td>';
      html+='</tr>';
    });
    html+='</tbody></table>';
    document.getElementById('autoAssignTable').innerHTML=html;
  }catch(e){
    document.getElementById('autoAssignStatus').textContent='❌ 加载失败: '+e;
  }
}

async function applyAutoAssign(){
  if(!_autoAssignPreview||!_autoAssignPreview.ok){
    toast('请先加载预览','warn');return;
  }
  // Get broker type for user confirmation
  var brokerLabel='未知';
  try{
    var st=await api('/quant/admin/api/engine-status');
    var bt=st&&st.brokerType||'paper';
    brokerLabel=bt==='paper'?'🟡 虚拟盘 (Paper)':'🔴 实盘 ('+bt+')';
  }catch(e){brokerLabel='⚠️ 无法获取';}

  var budgetInput=document.getElementById('autoAssignBudget').value;
  var budgetWan=budgetInput?parseFloat(budgetInput):0;
  var budgetDesc=budgetWan>0?budgetWan+' 万元/策略':'不限制';
  var data_topN=parseInt(document.getElementById('autoAssignTopN').value)||10;

  var msg='确认执行智能分配？\\n\\n'+
    '🔧 交易模式：'+brokerLabel+'\\n'+
    '📊 将创建 Top '+(data_topN||10)+' 策略（共 '+(_autoAssignPreview.selected_strategies||_autoAssignPreview.total_strategies)+' 个）\\n'+
    '📈 覆盖 '+(_autoAssignPreview.selected_stocks||_autoAssignPreview.total_stocks)+' 只股票\\n'+
    '💰 每策略资金池：'+budgetDesc+'\\n';
  if(_autoAssignPreview.pool_boost_info&&_autoAssignPreview.pool_boost_info.enabled){
    msg+='🚀 策略池加权: '+_autoAssignPreview.pool_boost_info.boosted_stocks+' 只股票已加权\\n';
  }
  if(_autoAssignPreview.existing_auto_assign_count>0){
    msg+='\\n⚠️ 将替换 '+_autoAssignPreview.existing_auto_assign_count+' 个已有的自动分配策略';
  }
  msg+='\\n\\n综合 fitness 个股分析 + 策略池回测组合加权。';

  if(!confirm(msg))return;

  document.getElementById('btnAutoAssignApply').disabled=true;
  document.getElementById('autoAssignApplyStatus').textContent='分配中...';

  try{
    var body={
      batch_id:_autoAssignPreview.batch_id,
      replace_existing:true,
      top_n:data_topN
    };
    if(budgetWan>0)body.capital_budget_per_strategy=budgetWan*10000;

    var res=await api('/quant/admin/api/strategy-fitness/auto-assign',{
      method:'POST',body:JSON.stringify(body)
    });

    document.getElementById('btnAutoAssignApply').disabled=false;
    if(res&&res.ok){
      document.getElementById('autoAssignApplyStatus').textContent='';
      var modeTag=res.brokerType==='paper'?'[虚拟盘]':'[实盘]';
      toast('✅ '+modeTag+' '+res.message,'success');

      // Show result
      document.getElementById('autoAssignResultSection').style.display='block';
      var rhtml='<div style="margin-bottom:12px;font-size:14px;font-weight:600;color:var(--green)">'+esc(res.message)+'</div>';
      if(res.removed_count>0){
        rhtml+='<div style="margin-bottom:8px;font-size:12px;color:var(--text-dim)">已替换 '+res.removed_count+' 个旧策略</div>';
      }
      rhtml+='<table><thead><tr><th>策略ID</th><th>活跃策略ID</th><th>股票数</th><th>平均分</th><th>股票预览</th></tr></thead><tbody>';
      (res.strategies||[]).forEach(function(s){
        rhtml+='<tr>';
        rhtml+='<td><strong>'+esc(s.strategyId)+'</strong></td>';
        rhtml+='<td style="font-family:monospace;font-size:11px">'+esc(s.id)+'</td>';
        rhtml+='<td style="font-weight:600">'+s.stockCount+'</td>';
        rhtml+='<td class="'+(s.avgScore>=30?'metric-good':'metric-ok')+'">'+s.avgScore+'</td>';
        rhtml+='<td style="font-size:11px;color:var(--text-dim)">'+esc((s.symbols||[]).join(', '))+(s.stockCount>10?' ...':'')+'</td>';
        rhtml+='</tr>';
      });
      rhtml+='</tbody></table>';
      document.getElementById('autoAssignResult').innerHTML=rhtml;

      // Reload preview to update existing count
      loadAutoAssignPreview();
    }else{
      document.getElementById('autoAssignApplyStatus').textContent='';
      toast('❌ '+(res&&res.error||'分配失败'),'error');
    }
  }catch(e){
    document.getElementById('btnAutoAssignApply').disabled=false;
    document.getElementById('autoAssignApplyStatus').textContent='';
    toast('❌ 分配失败: '+e,'error');
  }
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

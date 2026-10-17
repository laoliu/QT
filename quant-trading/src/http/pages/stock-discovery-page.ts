/**
 * Stock Discovery Page — 多维度选股引擎
 *
 * UI for running stock discovery using various algorithms:
 *  - 低位潜力股: 52w low + F-Score + capital flow
 *  - 困境反转股: EPS turnaround + gross margin improvement
 *  - 相对强度(RS): O'Neil RS Rating >= 75
 *  - 板块补涨: Sector leader + individual laggard
 *  - Magic Formula: High ROIC + low EV/EBIT
 *  - CAN SLIM: Growth + momentum
 *  - 量价异动: Volume-price anomaly
 *  - 综合选股: Multi-strategy combo
 */

export function getStockDiscoveryPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>选股引擎 — Quant Trading 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>🔍</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff;--teal:#39d353}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.breadcrumb{background:var(--bg);padding:8px 24px;font-size:12px;border-bottom:1px solid var(--border)}

/* Header */
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}
header h1{font-size:20px;font-weight:700;display:flex;align-items:center;gap:8px}
.header-actions{display:flex;gap:8px}

/* Layout */
.container{max-width:1400px;margin:0 auto;padding:20px 24px}
.layout{display:grid;grid-template-columns:320px 1fr;gap:20px;align-items:start}
@media(max-width:900px){.layout{grid-template-columns:1fr}}

/* Sidebar */
.sidebar{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:20px;position:sticky;top:20px}
.sidebar h2{font-size:14px;font-weight:600;margin-bottom:14px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.5px}

/* Strategy cards */
.strategy-grid{display:flex;flex-direction:column;gap:8px;margin-bottom:20px}
.strategy-card{padding:10px 14px;border-radius:8px;border:1px solid var(--border);cursor:pointer;transition:all .15s;position:relative}
.strategy-card:hover{border-color:var(--accent)}
.strategy-card.active{border-color:var(--accent);background:rgba(88,166,255,.08)}
.strategy-card .s-name{font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px}
.strategy-card .s-desc{font-size:11px;color:var(--text-dim);margin-top:3px;line-height:1.4}
.strategy-card .s-tag{font-size:10px;background:var(--bg3);border:1px solid var(--border);border-radius:3px;padding:1px 5px;color:var(--text-dim)}

/* Form controls */
.form-group{margin-bottom:14px}
.form-label{font-size:12px;color:var(--text-dim);margin-bottom:5px;display:block}
select,input[type=number],input[type=text]{width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:7px 10px;color:var(--text);font-size:13px}
select:focus,input:focus{outline:none;border-color:var(--accent)}
.checkbox-row{display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer}
.checkbox-row input[type=checkbox]{cursor:pointer;width:14px;height:14px}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 18px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px;font-weight:500;transition:all .15s;white-space:nowrap}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:var(--accent);color:#000;border-color:var(--accent);width:100%;justify-content:center;padding:10px}
.btn-primary:hover:not(:disabled){background:#79b8ff;border-color:#79b8ff;color:#000}
.btn-danger{background:rgba(248,81,73,.15);color:var(--red);border-color:var(--red)}
.btn-danger:hover{background:rgba(248,81,73,.25)}

/* Main content area */
.main-panel{display:flex;flex-direction:column;gap:16px}

/* KPI bar */
.kpi-bar{display:flex;gap:12px;flex-wrap:wrap}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;text-align:center;flex:1;min-width:100px}
.kpi-val{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi-label{font-size:11px;color:var(--text-dim);margin-top:3px}

/* Status banner */
.status-banner{padding:10px 16px;border-radius:8px;border:1px solid var(--border);font-size:13px;display:flex;align-items:center;gap:8px}
.status-banner.running{border-color:var(--yellow);background:rgba(210,153,34,.08);color:var(--yellow)}
.status-banner.done{border-color:var(--green);background:rgba(63,185,80,.08);color:var(--green)}
.status-banner.error{border-color:var(--red);background:rgba(248,81,73,.08);color:var(--red)}
.status-banner.warn{border-color:#e3a008;background:rgba(227,160,8,.08);color:#e3a008}
.status-banner.idle{display:none}

/* Results table */
.results-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.results-header{padding:14px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.results-header h3{font-size:14px;font-weight:600}
.filter-row{display:flex;gap:8px;align-items:center}
.filter-input{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:5px 10px;color:var(--text);font-size:12px;width:140px}
.filter-input:focus{outline:none;border-color:var(--accent)}

table{width:100%;border-collapse:collapse;font-size:12px}
th{padding:10px 12px;border-bottom:1px solid var(--border);color:var(--text-dim);font-size:11px;text-align:left;white-space:nowrap;cursor:pointer;user-select:none}
th:hover{color:var(--accent)}
td{padding:9px 12px;border-bottom:1px solid var(--border)}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.025)}
.rank-badge{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:50%;font-size:11px;font-weight:700}
.rank-1{background:rgba(210,153,34,.3);color:var(--yellow)}
.rank-2{background:rgba(139,148,158,.2);color:var(--text)}
.rank-3{background:rgba(188,140,255,.2);color:var(--purple)}
.rank-n{background:var(--bg3);color:var(--text-dim)}
.code-link{font-family:monospace;color:var(--accent);font-size:12px}
.score-bar{display:flex;align-items:center;gap:6px}
.score-fill{height:6px;border-radius:3px;background:var(--accent);min-width:2px;transition:width .3s}
.score-num{font-size:12px;font-weight:600;min-width:35px;text-align:right}
.signals-cell{max-width:320px}
.signal-pill{display:inline-block;background:rgba(88,166,255,.1);border:1px solid rgba(88,166,255,.2);border-radius:10px;padding:2px 7px;font-size:10px;color:var(--accent);margin:1px;white-space:nowrap}
.signal-pill.warn{background:rgba(248,81,73,.1);border-color:rgba(248,81,73,.2);color:var(--red)}
.strat-badge{display:inline-block;background:var(--bg3);border:1px solid var(--border);border-radius:3px;padding:1px 5px;font-size:10px;color:var(--text-dim);margin:1px}

.factors-toggle{cursor:pointer;color:var(--text-dim);font-size:11px;text-decoration:underline dotted}
.factors-detail{display:none;margin-top:4px;font-size:10px;color:var(--text-dim);line-height:1.6}

/* Empty state */
.empty{padding:60px;text-align:center;color:var(--text-dim)}
.empty .icon{font-size:48px;margin-bottom:12px}

/* Spinner */
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid rgba(88,166,255,.3);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}

/* History panel */
.history-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-top:0}
.history-header{padding:12px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;cursor:pointer;user-select:none}
.history-header h3{font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px;color:var(--text-dim)}
.history-header:hover h3{color:var(--text)}
.history-toggle{font-size:11px;color:var(--text-dim)}
.history-list{padding:10px 14px;display:flex;flex-direction:column;gap:8px}
.history-item{background:var(--bg3);border:1px solid var(--border);border-radius:8px;overflow:hidden}
.history-item-head{padding:9px 12px;display:flex;align-items:center;gap:10px;cursor:pointer;flex-wrap:wrap}
.history-item-head:hover{background:rgba(255,255,255,.03)}
.history-strat{font-size:12px;font-weight:600;flex:1;min-width:80px}
.history-meta{font-size:11px;color:var(--text-dim);display:flex;gap:10px;flex-wrap:wrap}
.history-pill{display:inline-block;font-size:10px;background:rgba(88,166,255,.1);border:1px solid rgba(88,166,255,.2);color:var(--accent);border-radius:10px;padding:1px 6px}
.history-item-body{padding:8px 12px;border-top:1px solid var(--border);display:none}
.history-item-body.open{display:block}
.history-top-list{display:flex;flex-wrap:wrap;gap:6px;font-size:11px}
.history-top-stock{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:4px 8px;display:flex;flex-direction:column;gap:1px;min-width:90px}
.history-top-stock .hs-code{font-family:monospace;color:var(--accent)}
.history-top-stock .hs-name{color:var(--text);font-size:10px}
.history-top-stock .hs-score{color:var(--text-dim);font-size:10px}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">🏠 首页</a> / <span>🔍 选股引擎</span></div>
<header>
  <h1>🔍 多维度选股引擎</h1>
  <div class="header-actions">
    <a class="btn" href="${b}/quant/admin/planner">📋 投资规划</a>
    <a class="btn" href="${b}/quant/admin/ui">返回管理面板</a>
  </div>
</header>

<div class="container">
<div class="layout">

<!-- ── Sidebar ── -->
<div class="sidebar">
  <h2>选股策略</h2>
  <div class="strategy-grid" id="strategyGrid">
    <!-- Populated by JS -->
  </div>

  <h2 style="margin-top:20px">参数设置</h2>

  <div class="form-group">
    <label class="form-label">股票池</label>
    <select id="universeSelect">
      <option value="csi500">中证500 (~500只)</option>
      <option value="csi300">沪深300 (~300只)</option>
      <option value="csi1000">中证1000 (~1000只)</option>
      <option value="all">全市场 (较慢)</option>
    </select>
  </div>

  <div class="form-group">
    <label class="form-label">最多返回数量</label>
    <input type="number" id="limitInput" value="20" min="5" max="100">
  </div>

  <div class="form-group">
    <label class="checkbox-row">
      <input type="checkbox" id="withKlines" checked>
      <span>加载K线数据 (信号更准确)</span>
    </label>
  </div>

  <div class="form-group" id="klineDaysGroup">
    <label class="form-label">K线天数</label>
    <input type="number" id="klineDays" value="252" min="60" max="500">
  </div>

  <div class="form-group">
    <label class="form-label">自定义股票池 (代码用逗号分隔，留空则用上方股票池)</label>
    <input type="text" id="customSymbols" placeholder="如: 600519,000858,300750">
  </div>

  <button class="btn btn-primary" id="runBtn" onclick="runDiscovery()">
    🚀 开始选股
  </button>
  <button class="btn btn-danger" id="stopBtn" onclick="stopDiscovery()" style="display:none;margin-top:8px;width:100%;justify-content:center">
    ⏹ 停止
  </button>
</div>

<!-- ── Main Panel ── -->
<div class="main-panel">

  <!-- Status banner -->
  <div class="status-banner idle" id="statusBanner">
    <span class="spinner" id="spinnerIcon" style="display:none"></span>
    <span id="statusText"></span>
  </div>

  <!-- KPIs -->
  <div class="kpi-bar" id="kpiBar" style="display:none">
    <div class="kpi"><div class="kpi-val" id="kpiUniverse">-</div><div class="kpi-label">股票池总量</div></div>
    <div class="kpi"><div class="kpi-val" id="kpiScreened">-</div><div class="kpi-label">筛选结果</div></div>
    <div class="kpi"><div class="kpi-val" id="kpiStrategy">-</div><div class="kpi-label">当前策略</div></div>
    <div class="kpi"><div class="kpi-val" id="kpiTime">-</div><div class="kpi-label">耗时(秒)</div></div>
  </div>

  <!-- Results -->
  <div class="results-card" id="resultsCard" style="display:none">
    <div class="results-header">
      <h3 id="resultsTitle">选股结果</h3>
      <div class="filter-row">
        <input class="filter-input" type="text" id="filterInput" placeholder="搜索代码/名称" oninput="filterResults()">
        <button class="btn" onclick="exportCsv()">📥 导出 CSV</button>
      </div>
    </div>
    <div style="overflow-x:auto">
      <table id="resultsTable">
        <thead id="tableHead"></thead>
        <tbody id="tableBody"></tbody>
      </table>
    </div>
    <div class="empty" id="noResults" style="display:none">
      <div class="icon">🔍</div>
      <div>未找到符合条件的股票</div>
    </div>
  </div>

  <!-- Placeholder -->
  <div class="empty" id="placeholder">
    <div class="icon">🔍</div>
    <div style="font-size:15px;font-weight:600;margin-bottom:8px">选择策略，开始选股</div>
    <div style="font-size:13px;color:var(--text-dim);max-width:400px;margin:0 auto;line-height:1.6">
      在左侧选择选股策略，配置参数后点击「开始选股」，系统将在全市场或指定股票池中筛选符合条件的个股。
    </div>
  </div>

  <!-- History -->
  <div class="history-card" id="historyCard" style="display:none">
    <div class="history-header" onclick="toggleHistory()">
      <h3>🕘 历史运行记录 <span id="historyCount" style="font-size:11px;font-weight:400"></span></h3>
      <span class="history-toggle" id="historyToggleIcon">▼</span>
    </div>
    <div id="historyList" class="history-list"></div>
  </div>

</div>
</div>
</div>

<script>
const B = '${b}';
let allResults = [];
let currentStrategy = 'low_position';
let isCombo = false;

// ── Strategy definitions (will be overridden by API) ─────────────
const STRATEGY_DEFS = [
  { id: 'low_position',      icon: '📉', name: '低位潜力股',        color: '#3fb950', tag: '均值回归' },
  { id: 'turnaround',        icon: '🔄', name: '困境反转股',        color: '#d29922', tag: '高赔率' },
  { id: 'relative_strength', icon: '💪', name: '相对强度(RS)',       color: '#58a6ff', tag: '强者恒强' },
  { id: 'sector_laggard',    icon: '🎯', name: '板块补涨股',        color: '#bc8cff', tag: '轮动' },
  { id: 'value_quality',     icon: '💎', name: 'Magic Formula',     color: '#39d353', tag: '价值' },
  { id: 'growth_momentum',   icon: '📈', name: 'CAN SLIM 成长',     color: '#db6d28', tag: '成长' },
  { id: 'volume_anomaly',    icon: '📊', name: '量价异动',           color: '#58a6ff', tag: '技术' },
  { id: 'fscore',            icon: '🏆', name: 'F-Score 价值质量',  color: '#39d353', tag: '基本面' },
  { id: 'combo',             icon: '🧩', name: '综合选股 (推荐)',   color: '#bc8cff', tag: '多策略' },
];

function buildStrategyGrid() {
  const grid = document.getElementById('strategyGrid');
  grid.innerHTML = '';
  for (const s of STRATEGY_DEFS) {
    const card = document.createElement('div');
    card.className = 'strategy-card' + (s.id === currentStrategy ? ' active' : '');
    card.style.borderLeftColor = s.color;
    card.style.borderLeftWidth = '3px';
    card.innerHTML = \`
      <div class="s-name">\${s.icon} \${s.name} <span class="s-tag">\${s.tag}</span></div>
    \`;
    card.onclick = () => selectStrategy(s.id);
    card.dataset.id = s.id;
    grid.appendChild(card);
  }
}

function selectStrategy(id) {
  currentStrategy = id;
  isCombo = (id === 'combo');
  document.querySelectorAll('.strategy-card').forEach(c => {
    c.classList.toggle('active', c.dataset.id === id);
  });
}

function esc(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function num(v, d = 2) { return v != null ? Number(v).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }) : '-'; }

async function runDiscovery() {
  const runBtn = document.getElementById('runBtn');
  const stopBtn = document.getElementById('stopBtn');
  runBtn.disabled = true;
  stopBtn.style.display = 'flex';

  setStatus('running', '🔄 正在加载行情数据并执行选股...');
  document.getElementById('placeholder').style.display = 'none';
  document.getElementById('kpiBar').style.display = 'none';
  document.getElementById('resultsCard').style.display = 'none';

  const universe = document.getElementById('universeSelect').value;
  const limit = parseInt(document.getElementById('limitInput').value) || 20;
  const withKlines = document.getElementById('withKlines').checked;
  const klineDays = parseInt(document.getElementById('klineDays').value) || 252;
  const customRaw = document.getElementById('customSymbols').value.trim();
  const symbols = customRaw ? customRaw.split(/[,\\s]+/).filter(Boolean) : [];

  try {
    const resp = await fetch(B + '/quant/admin/api/stock-discovery/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        strategy: currentStrategy,
        universe,
        limit,
        with_klines: withKlines,
        kline_days: klineDays,
        symbols,
      }),
    });

    const data = await resp.json();
    runBtn.disabled = false;
    stopBtn.style.display = 'none';

    if (!resp.ok || !data.ok) {
      setStatus('error', '❌ 错误: ' + (data.error || '请求失败'));
      return;
    }

    allResults = data.results || [];
    const stratLabel = data.strategy_label || currentStrategy;
    const elapsed = data.elapsed_seconds != null ? data.elapsed_seconds + 's' : '-';
    const offlineTag = data.is_offline ? ' ⚠️ 离线缓存模式' : (data.is_sina_fallback ? ' 📡 新浪备用源' : '');
    setStatus('done', \`✅ 选股完成 — 策略: \${esc(stratLabel)}, 耗时 \${elapsed}\${offlineTag}\`);

    // KPIs
    document.getElementById('kpiBar').style.display = 'flex';
    document.getElementById('kpiUniverse').textContent = num(data.universe_count, 0);
    document.getElementById('kpiScreened').textContent = num(data.screened_count, 0);
    document.getElementById('kpiStrategy').textContent = stratLabel;
    document.getElementById('kpiTime').textContent = elapsed;

    // Render results
    renderResults(allResults, currentStrategy);
    loadHistory();

  } catch (e) {
    runBtn.disabled = false;
    stopBtn.style.display = 'none';
    setStatus('error', '❌ 网络错误: ' + e.message);
  }
}

async function stopDiscovery() {
  await fetch(B + '/quant/admin/api/stock-discovery/stop', { method: 'POST' });
  document.getElementById('runBtn').disabled = false;
  document.getElementById('stopBtn').style.display = 'none';
  setStatus('idle', '');
}

function setStatus(type, text) {
  const banner = document.getElementById('statusBanner');
  banner.className = 'status-banner ' + type;
  document.getElementById('statusText').textContent = text;
  document.getElementById('spinnerIcon').style.display = type === 'running' ? 'inline-block' : 'none';
  if (type === 'idle') banner.style.display = 'none';
  else banner.style.display = 'flex';
}

function renderResults(results, strategy) {
  const card = document.getElementById('resultsCard');
  const title = document.getElementById('resultsTitle');
  const stratDef = STRATEGY_DEFS.find(s => s.id === strategy);
  title.textContent = (stratDef ? stratDef.icon + ' ' + stratDef.name : strategy) + ' 结果 (' + results.length + '只)';

  card.style.display = 'block';

  const isComboMode = strategy === 'combo';

  // Table header
  const thead = document.getElementById('tableHead');
  if (isComboMode) {
    thead.innerHTML = \`<tr>
      <th onclick="sortBy('rank')">排名</th>
      <th onclick="sortBy('stock_id')">代码</th>
      <th>名称</th>
      <th onclick="sortBy('score')">综合得分</th>
      <th>命中策略</th>
      <th>核心信号</th>
    </tr>\`;
  } else {
    thead.innerHTML = \`<tr>
      <th onclick="sortBy('rank')">排名</th>
      <th onclick="sortBy('stock_id')">代码</th>
      <th>名称</th>
      <th onclick="sortBy('score')">得分</th>
      <th>关键信号</th>
      <th>详细指标</th>
    </tr>\`;
  }

  buildTableBody(results, isComboMode);
}

function buildTableBody(results, isComboMode) {
  const tbody = document.getElementById('tableBody');
  const noResults = document.getElementById('noResults');

  if (!results.length) {
    tbody.innerHTML = '';
    noResults.style.display = 'block';
    return;
  }
  noResults.style.display = 'none';

  const maxScore = results.length > 0 ? (results[0].score || 1) : 1;

  tbody.innerHTML = results.map((r, idx) => {
    const rank = r.rank || (idx + 1);
    const rankClass = rank === 1 ? 'rank-1' : rank === 2 ? 'rank-2' : rank === 3 ? 'rank-3' : 'rank-n';
    const scoreBarWidth = Math.max(2, Math.round((r.score / maxScore) * 80));
    const scorePct = Math.round((r.score / maxScore) * 100);

    // Signals
    const signals = (r.signals || []).slice(0, 5);
    const signalHtml = signals.map(s => {
      const isWarn = s.includes('⚠️') || s.includes('风险') || s.includes('负债');
      return \`<span class="signal-pill\${isWarn ? ' warn' : ''}">\${esc(s)}</span>\`;
    }).join('');

    if (isComboMode) {
      const strategies = (r.strategies_hit || []).map(s =>
        \`<span class="strat-badge">\${esc(s)}</span>\`).join('');
      return \`<tr>
        <td><span class="rank-badge \${rankClass}">\${rank}</span></td>
        <td><a class="code-link" href="\${B}/quant/admin/stock?symbol=\${esc(r.stock_id)}" target="_blank">\${esc(r.stock_id)}</a></td>
        <td style="font-weight:500">\${esc(r.name)}</td>
        <td>
          <div class="score-bar">
            <div class="score-fill" style="width:\${scoreBarWidth}px;background:var(--accent)"></div>
            <span class="score-num">\${num(r.score)}</span>
          </div>
        </td>
        <td>\${strategies}</td>
        <td class="signals-cell">\${signalHtml}</td>
      </tr>\`;
    }

    // Single strategy — show factors
    const factors = r.factors || {};
    const factorKeys = Object.keys(factors).filter(k => k !== 'price');
    const factorHtml = factorKeys.map(k => {
      const v = factors[k];
      const display = typeof v === 'number' ? num(v, 3) : esc(String(v));
      return \`<span style="margin-right:8px"><span style="color:var(--text-dim)">\${esc(k)}:</span> \${display}</span>\`;
    }).join('');

    return \`<tr>
      <td><span class="rank-badge \${rankClass}">\${rank}</span></td>
      <td><a class="code-link" href="\${B}/quant/admin/stock?symbol=\${esc(r.stock_id)}" target="_blank">\${esc(r.stock_id)}</a></td>
      <td style="font-weight:500">\${esc(r.name)}</td>
      <td>
        <div class="score-bar">
          <div class="score-fill" style="width:\${scoreBarWidth}px"></div>
          <span class="score-num">\${num(r.score)}</span>
        </div>
      </td>
      <td class="signals-cell">\${signalHtml}</td>
      <td style="font-size:10px;color:var(--text-dim);max-width:260px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="\${esc(factorHtml)}">\${factorHtml}</td>
    </tr>\`;
  }).join('');
}

let sortField = 'rank';
let sortAsc = true;
function sortBy(field) {
  if (sortField === field) sortAsc = !sortAsc;
  else { sortField = field; sortAsc = field === 'rank'; }
  const sorted = [...allResults].sort((a, b) => {
    const av = a[field] ?? 0, bv = b[field] ?? 0;
    return sortAsc ? (av < bv ? -1 : 1) : (av > bv ? -1 : 1);
  });
  buildTableBody(sorted, currentStrategy === 'combo');
}

function filterResults() {
  const q = document.getElementById('filterInput').value.trim().toLowerCase();
  const filtered = q
    ? allResults.filter(r =>
        (r.stock_id || '').includes(q) ||
        (r.name || '').toLowerCase().includes(q))
    : allResults;
  buildTableBody(filtered, currentStrategy === 'combo');
}

function exportCsv() {
  if (!allResults.length) return;
  const isComboMode = currentStrategy === 'combo';
  const headers = isComboMode
    ? ['排名', '代码', '名称', '综合得分', '命中策略', '信号']
    : ['排名', '代码', '名称', '得分', '信号', '指标'];

  const rows = allResults.map(r => {
    const signals = (r.signals || []).join('; ');
    const factors = Object.entries(r.factors || {}).map(([k,v]) => k+'='+v).join('; ');
    if (isComboMode) {
      return [r.rank, r.stock_id, r.name, r.score, (r.strategies_hit||[]).join('/'), signals];
    }
    return [r.rank, r.stock_id, r.name, r.score, signals, factors];
  });

  const csv = [headers, ...rows].map(row =>
    row.map(v => '"' + String(v ?? '').replace(/"/g, '""') + '"').join(',')
  ).join('\\n');

  const blob = new Blob(['\\uFEFF' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'stock_discovery_' + currentStrategy + '_' + new Date().toISOString().slice(0,10) + '.csv';
  a.click();
  URL.revokeObjectURL(url);
}

// ── K线设置联动 ────────────────────────────────────────────────
document.getElementById('withKlines').addEventListener('change', function() {
  document.getElementById('klineDaysGroup').style.display = this.checked ? 'block' : 'none';
});

// ── History ───────────────────────────────────────────────────
let historyOpen = true;

function toggleHistory() {
  historyOpen = !historyOpen;
  document.getElementById('historyList').style.display = historyOpen ? 'flex' : 'none';
  document.getElementById('historyToggleIcon').textContent = historyOpen ? '▼' : '▶';
}

function renderHistory(history) {
  const card = document.getElementById('historyCard');
  const list = document.getElementById('historyList');
  const countEl = document.getElementById('historyCount');
  if (!history || !history.length) return;
  card.style.display = 'block';
  countEl.textContent = '(' + history.length + ' 条)';
  list.innerHTML = history.map((h, idx) => {
    const stratDef = STRATEGY_DEFS.find(s => s.id === h.strategy);
    const icon = stratDef ? stratDef.icon : '📊';
    const label = h.strategy_label || (stratDef ? stratDef.name : h.strategy);
    const time = h.finished_at ? h.finished_at.replace('T', ' ').slice(0, 16) : '';
    const elapsed = h.elapsed_seconds != null ? h.elapsed_seconds + 's' : '';
    const offline = h.is_offline ? '<span class="history-pill" style="border-color:rgba(210,153,34,.4);color:var(--yellow);background:rgba(210,153,34,.1)">离线</span>' : (h.is_sina_fallback ? '<span class="history-pill" style="border-color:rgba(88,166,255,.4);color:var(--accent);background:rgba(88,166,255,.1)">新浪备用</span>' : '');
    const topHtml = (h.top_results || []).map(r => \`
      <div class="history-top-stock">
        <span class="hs-code"><a style="color:var(--accent)" href="\${B}/quant/admin/stock?symbol=\${esc(r.stock_id)}" target="_blank">\${esc(r.stock_id)}</a></span>
        <span class="hs-name">\${esc(r.name || '')}</span>
        <span class="hs-score">分: \${r.score != null ? Number(r.score).toFixed(1) : '-'}</span>
      </div>
    \`).join('');
    return \`
      <div class="history-item">
        <div class="history-item-head" onclick="toggleHistoryItem(this)">
          <span class="history-strat">\${icon} \${esc(label)}</span>
          <span class="history-meta">
            <span>\${esc(h.universe || '')}</span>
            <span>筛选 \${h.screened_count ?? '-'} 只</span>
            \${elapsed ? '<span>' + esc(elapsed) + '</span>' : ''}
            \${offline}
          </span>
          <span style="font-size:11px;color:var(--text-dim);white-space:nowrap">\${esc(time)}</span>
          <span style="font-size:10px;color:var(--text-dim)">▶</span>
        </div>
        <div class="history-item-body">
          <div style="font-size:11px;color:var(--text-dim);margin-bottom:6px">Top 结果:</div>
          <div class="history-top-list">\${topHtml || '<span style="color:var(--text-dim);font-size:11px">暂无数据</span>'}</div>
        </div>
      </div>
    \`;
  }).join('');
  list.style.display = historyOpen ? 'flex' : 'none';
}

function toggleHistoryItem(headEl) {
  const body = headEl.nextElementSibling;
  const arrow = headEl.querySelector('span:last-child');
  const isOpen = body.classList.toggle('open');
  if (arrow) arrow.textContent = isOpen ? '▼' : '▶';
}

async function loadHistory() {
  try {
    const resp = await fetch(B + '/quant/admin/api/stock-discovery/history');
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.ok && data.history) renderHistory(data.history);
  } catch { /* ignore */ }
}

// ── Init ──────────────────────────────────────────────────────
buildStrategyGrid();
loadHistory();
</script>
</body>
</html>`;
}

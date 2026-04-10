/**
 * Strategy Routing Page — self-contained HTML SPA for stock profiling
 * and adaptive strategy routing.
 *
 * Tabs:
 *  1. Profile  — analyze stock characteristics
 *  2. Route    — get strategy recommendations for a portfolio
 *  3. Table    — view/build the data-driven routing table
 */

/** Return the full HTML for the Strategy Routing page. */
export function getStrategyRoutingPageHtml(baseUrl: string, apiToken?: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 策略路由</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{font-size:18px;font-weight:600}
nav{display:flex;gap:4px;background:var(--bg2);padding:0 24px;border-bottom:1px solid var(--border)}
nav button{background:none;border:none;color:var(--text-dim);padding:10px 16px;font-size:14px;cursor:pointer;border-bottom:2px solid transparent;transition:all .2s}
nav button:hover{color:var(--text)}
nav button.active{color:var(--accent);border-bottom-color:var(--accent)}
.container{max-width:1400px;margin:0 auto;padding:20px 24px}
.tab-content{display:none}
.tab-content.active{display:block}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}
.card h3{margin-bottom:12px;font-size:15px}
.grid{display:grid;gap:16px}
.grid-2{grid-template-columns:repeat(auto-fill,minmax(400px,1fr))}
.grid-3{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.btn{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;border-radius:6px;font-size:13px;cursor:pointer;border:1px solid var(--border);background:var(--bg3);color:var(--text);transition:all .15s}
.btn:hover{background:var(--border)}
.btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-primary:hover{opacity:.85}
.btn-danger{background:var(--red);color:#fff;border-color:var(--red)}
.btn-sm{padding:4px 10px;font-size:12px}
label{display:block;font-size:13px;color:var(--text-dim);margin-bottom:4px}
select,input[type=text],input[type=number]{width:100%;padding:6px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px}
select:focus,input:focus{outline:none;border-color:var(--accent)}
.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.tag-low{background:#1a3f2a;color:#3fb950}
.tag-medium{background:#3f3a1a;color:#d29922}
.tag-high{background:#3f1a1a;color:#f85149}
.tag-uptrend{background:#1a3f2a;color:#3fb950}
.tag-sideways{background:#3f3a1a;color:#d29922}
.tag-downtrend{background:#3f1a1a;color:#f85149}
.tag-micro,.tag-small{background:#2a1a3f;color:#bc8cff}
.tag-mid{background:#1f3a5f;color:#58a6ff}
.tag-large{background:#3f3a1a;color:#d29922}
.tag-rule{background:#1f3a5f;color:#58a6ff}
.tag-data{background:#1a3f2a;color:#3fb950}
.tag-fallback{background:#3f3a1a;color:#d29922}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500;font-size:12px}
.metric-good{color:var(--green)}
.metric-bad{color:var(--red)}
.metric-ok{color:var(--yellow)}
.empty{text-align:center;padding:40px;color:var(--text-dim)}
.loading{text-align:center;padding:20px;color:var(--text-dim)}
.form-row{display:flex;gap:12px;align-items:flex-end;margin-bottom:12px;flex-wrap:wrap}
.form-row > div{flex:1;min-width:150px}
.profile-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:12px}
.profile-card h4{font-size:14px;margin-bottom:10px}
.profile-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
.profile-item{background:var(--bg3);border-radius:6px;padding:8px 12px}
.profile-item .label{font-size:11px;color:var(--text-dim)}
.profile-item .value{font-size:13px;font-weight:600;margin-top:2px}
.combo-box{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:10px 14px;margin-top:8px}
.combo-box .combo-name{font-weight:600;font-size:13px}
.combo-box .combo-strategies{color:var(--text-dim);font-size:12px;margin-top:4px}
.back-link{color:var(--accent);text-decoration:none;font-size:13px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.routing-entry{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:10px}
.routing-entry h4{font-size:13px;font-weight:600;margin-bottom:8px}
.routing-metrics{display:flex;gap:16px;flex-wrap:wrap;font-size:12px}
.routing-metrics span{color:var(--text-dim)}
.routing-metrics .val{color:var(--text);font-weight:600}
</style>
</head>
<body>
<header>
  <h1>\u{1f9ed} OpenClaw \u7b56\u7565\u8def\u7531</h1>
  <div style="display:flex;gap:12px;align-items:center">
    <a href="${b}/quant/admin/ui" class="back-link">\u2190 \u8fd4\u56de\u7ba1\u7406\u9762\u677f</a>
  </div>
</header>
<nav>
  <button class="active" onclick="switchTab('profile')">📊 股票画像</button>
  <button onclick="switchTab('route')">🧭 组合路由</button>
  <button onclick="switchTab('table')">📋 路由表</button>
  <button onclick="switchTab('prbt')">🔬 画像回测</button>
  <button onclick="switchTab('regime')">🌍 宏观周期</button>
</nav>

<div class="container">

<!-- ═══ Tab 1: Profile ═══ -->
<div id="tab-profile" class="tab-content active">
  <div class="card">
    <h3>股票特征画像</h3>
    <p style="color:var(--text-dim);font-size:13px;margin-bottom:12px">
      分析股票的波动率、趋势、市值、流动性、动量等特征，用于策略匹配。
    </p>
    <div class="form-row">
      <div style="flex:2">
        <label>标的代码 (逗号分隔)</label>
        <input type="text" id="profile-symbols" placeholder="600519,000858,300750" value="600519,000858,300750">
      </div>
      <div>
        <label>截止日期</label>
        <input type="text" id="profile-end-date" placeholder="YYYY-MM-DD">
      </div>
      <div>
        <button class="btn btn-primary" onclick="runProfile()">分析画像</button>
      </div>
    </div>
  </div>
  <div id="profile-results"></div>
</div>

<!-- ═══ Tab 2: Route ═══ -->
<div id="tab-route" class="tab-content">
  <div class="card">
    <h3>组合策略路由</h3>
    <p style="color:var(--text-dim);font-size:13px;margin-bottom:12px">
      基于股票画像，自动匹配最优策略组合。支持规则路由和数据驱动路由。
    </p>
    <div class="form-row">
      <div style="flex:2">
        <label>标的代码 (逗号分隔)</label>
        <input type="text" id="route-symbols" placeholder="600519,000858,300750,601318,000001" value="600519,000858,300750,601318,000001">
      </div>
      <div>
        <label>截止日期</label>
        <input type="text" id="route-end-date" placeholder="YYYY-MM-DD">
      </div>
      <div>
        <button class="btn btn-primary" onclick="runRoute()">路由分析</button>
      </div>
    </div>
  </div>
  <div id="route-summary"></div>
  <div id="route-details"></div>
</div>

<!-- ═══ Tab 3: Routing Table ═══ -->
<div id="tab-table" class="tab-content">
  <div class="card">
    <h3>数据驱动路由表</h3>
    <p style="color:var(--text-dim);font-size:13px;margin-bottom:12px">
      通过批量回测构建的路由表。每种画像分类对应最优策略组合。
    </p>
    <div class="form-row">
      <div>
        <button class="btn btn-primary" onclick="loadRoutingTable()">加载路由表</button>
      </div>
      <div>
        <button class="btn" onclick="showBuildForm()">🔄 重建路由表</button>
      </div>
    </div>
    <div id="build-form" style="display:none;margin-top:12px">
      <div class="form-row">
        <div>
          <label>起始日期</label>
          <input type="text" id="build-start-date" placeholder="YYYY-MM-DD">
        </div>
        <div>
          <label>结束日期</label>
          <input type="text" id="build-end-date" placeholder="YYYY-MM-DD">
        </div>
        <div>
          <label>每类最多股票数</label>
          <input type="number" id="build-max-stocks" value="10">
        </div>
        <div>
          <button class="btn btn-primary" onclick="buildRoutingTable()">开始构建</button>
        </div>
      </div>
    </div>
  </div>
  <div id="table-results"></div>
</div>

<!-- ═══ Tab 4: Profile Route Backtest ═══ -->
<div id="tab-prbt" class="tab-content">
  <div class="card">
    <h3>画像→路由→回测 一键验证</h3>
    <p style="color:var(--text-dim);font-size:13px;margin-bottom:12px">
      输入股票代码，自动完成画像分析→策略路由→组合回测全流程，验证自适应路由的实际表现。
    </p>
    <div class="form-row">
      <div style="flex:2">
        <label>标的代码 (逗号分隔)</label>
        <input type="text" id="prbt-symbols" placeholder="600519,000858,300750" value="600519,000858,300750">
      </div>
      <div>
        <label>起始日期</label>
        <input type="text" id="prbt-start-date" placeholder="YYYY-MM-DD">
      </div>
      <div>
        <label>结束日期</label>
        <input type="text" id="prbt-end-date" placeholder="YYYY-MM-DD">
      </div>
      <div>
        <button class="btn btn-primary" onclick="runPRBT()">开始验证</button>
      </div>
    </div>
  </div>
  <div id="prbt-results"></div>
</div>

<!-- ═══ Tab 5: Market Regime ═══ -->
<div id="tab-regime" class="tab-content">
  <div class="card">
    <h3>宏观经济周期</h3>
    <p style="color:var(--text-dim);font-size:13px;margin-bottom:12px">
      实时检测当前宏观经济周期（复苏/过热/滞胀/衰退），影响策略路由的投票门槛和风险偏好。
    </p>
    <div class="form-row">
      <div>
        <button class="btn btn-primary" onclick="loadRegime()">检测当前周期</button>
      </div>
    </div>
  </div>
  <div id="regime-results"></div>
</div>

</div><!-- container -->

<script>
const API_TOKEN = ${JSON.stringify(apiToken ?? "")};
const BASE = ${JSON.stringify(b)};

function headers() {
  const h = {"Content-Type":"application/json"};
  if (API_TOKEN) h["Authorization"] = "Bearer " + API_TOKEN;
  return h;
}

async function apiFetch(path, opts = {}) {
  const url = BASE + path;
  const res = await fetch(url, { headers: headers(), ...opts });
  return res.json();
}

// ── Tab switching ───────────────────────────────────────────
const TAB_NAMES = ['profile','route','table','prbt','regime'];
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelectorAll('nav button')[TAB_NAMES.indexOf(name)].classList.add('active');
}

// ── Tag helpers ─────────────────────────────────────────────
function tagHtml(cls, text) {
  return '<span class="tag tag-' + cls + '">' + text + '</span>';
}
function volatilityTag(v) {
  if (!v) return '';
  const m = {'low':'low','medium':'medium','high':'high'};
  return tagHtml(m[v.class] || 'medium', v.class + ' (' + v.annualized.toFixed(1) + '%)');
}
function trendTag(t) {
  if (!t) return '';
  return tagHtml(t.class, t.class);
}
function capTag(c) {
  if (!c) return '';
  return tagHtml(c.class, c.class + ' (' + c.value_yi.toFixed(0) + '亿)');
}
function sourceTag(s) {
  if (!s) return '';
  return tagHtml(s === 'data-driven' ? 'data' : s === 'rule-based' ? 'rule' : 'fallback', s);
}

function metricColor(val, good, bad) {
  if (val >= good) return 'metric-good';
  if (val <= bad) return 'metric-bad';
  return 'metric-ok';
}

// ── Profile ─────────────────────────────────────────────────
async function runProfile() {
  const symbols = document.getElementById('profile-symbols').value.trim();
  if (!symbols) return alert('请输入标的代码');
  const endDate = document.getElementById('profile-end-date').value.trim() || undefined;
  const el = document.getElementById('profile-results');
  el.innerHTML = '<div class="loading">分析中…</div>';

  const data = await apiFetch('/quant/admin/api/stock-profile', {
    method: 'POST',
    body: JSON.stringify({ symbols, endDate })
  });

  if (!data.ok && data.error) {
    el.innerHTML = '<div class="card"><p style="color:var(--red)">' + escHtml(data.error) + '</p></div>';
    return;
  }

  const profiles = data.profiles || [];
  if (!profiles.length) {
    el.innerHTML = '<div class="empty">无结果</div>';
    return;
  }

  el.innerHTML = profiles.map(p => {
    if (!p.ok) return '<div class="profile-card"><h4>' + escHtml(p.symbol) + '</h4><p style="color:var(--red)">' + escHtml(p.error || '分析失败') + '</p></div>';
    return '<div class="profile-card">' +
      '<h4>' + escHtml(p.symbol) + ' ' + escHtml(p.name || '') +
      ' <span style="color:var(--text-dim);font-size:12px;font-weight:normal">画像: ' + escHtml(p.profileKey || '') + '</span></h4>' +
      '<div class="profile-grid">' +
        profileItem('波动率', volatilityTag(p.volatility)) +
        profileItem('趋势', trendTag(p.trend)) +
        profileItem('市值', capTag(p.marketCap)) +
        profileItem('流动性', p.liquidity ? tagHtml(p.liquidity.class === 'high' ? 'high' : p.liquidity.class === 'low' ? 'low' : 'medium', p.liquidity.class + ' (' + p.liquidity.value.toFixed(1) + '%)') : '') +
        profileItem('动量', p.momentum ? tagHtml(p.momentum.class === 'positive' ? 'uptrend' : p.momentum.class === 'negative' ? 'downtrend' : 'sideways', p.momentum.class + ' (' + p.momentum.return20d.toFixed(1) + '%)') : '') +
        profileItem('量能', p.volumeTrend ? tagHtml(p.volumeTrend.class === 'expanding' ? 'high' : p.volumeTrend.class === 'shrinking' ? 'low' : 'medium', p.volumeTrend.class) : '') +
        profileItem('行业', '<span style="color:var(--text)">' + escHtml(p.sector || '-') + '</span>') +
      '</div></div>';
  }).join('');
}

function profileItem(label, valueHtml) {
  return '<div class="profile-item"><div class="label">' + label + '</div><div class="value">' + valueHtml + '</div></div>';
}

// ── Route ───────────────────────────────────────────────────
async function runRoute() {
  const symbols = document.getElementById('route-symbols').value.trim();
  if (!symbols) return alert('请输入标的代码');
  const endDate = document.getElementById('route-end-date').value.trim() || undefined;
  const sumEl = document.getElementById('route-summary');
  const detEl = document.getElementById('route-details');
  sumEl.innerHTML = '<div class="loading">路由分析中…</div>';
  detEl.innerHTML = '';

  const data = await apiFetch('/quant/admin/api/route-portfolio', {
    method: 'POST',
    body: JSON.stringify({ symbols, endDate })
  });

  if (!data.ok && data.error) {
    sumEl.innerHTML = '<div class="card"><p style="color:var(--red)">' + escHtml(data.error) + '</p></div>';
    return;
  }

  const recs = data.recommendations || [];
  if (!recs.length) {
    sumEl.innerHTML = '<div class="empty">无结果</div>';
    return;
  }

  // Group by combo
  const groups = {};
  for (const r of recs) {
    if (r.ok && r.combo) {
      const key = r.combo.name;
      if (!groups[key]) groups[key] = { combo: r.combo, stocks: [], source: r.source };
      groups[key].stocks.push(r.symbol);
    }
  }

  sumEl.innerHTML = '<div class="card"><h3>路由摘要</h3>' +
    '<table><tr><th>策略组合</th><th>方法</th><th>来源</th><th>股票数</th><th>股票</th></tr>' +
    Object.values(groups).map(g =>
      '<tr><td>' + escHtml(g.combo.name) + '</td>' +
      '<td>' + escHtml(g.combo.method) + '</td>' +
      '<td>' + sourceTag(g.source) + '</td>' +
      '<td>' + g.stocks.length + '</td>' +
      '<td style="color:var(--text-dim)">' + g.stocks.join(', ') + '</td></tr>'
    ).join('') + '</table></div>';

  detEl.innerHTML = '<h3 style="margin:16px 0 10px">详细推荐</h3>' +
    recs.map(r => {
      if (!r.ok) return '<div class="profile-card"><h4>' + escHtml(r.symbol) + '</h4><p style="color:var(--red)">' + escHtml(r.error || '失败') + '</p></div>';
      const p = r.profile || {};
      return '<div class="profile-card">' +
        '<h4>' + escHtml(r.symbol) + ' <span style="color:var(--text-dim);font-size:12px;font-weight:normal">' + escHtml(p.profileKey || '') + '</span> ' + sourceTag(r.source) + '</h4>' +
        (r.combo ? '<div class="combo-box"><div class="combo-name">' + escHtml(r.combo.name) + ' (' + escHtml(r.combo.method) + ')</div>' +
        '<div class="combo-strategies">子策略: ' + (r.combo.strategies || []).map(s => escHtml(s.strategyId || s)).join(' + ') + '</div></div>' : '') +
        (r.reason ? '<p style="color:var(--text-dim);font-size:12px;margin-top:6px">' + escHtml(r.reason) + '</p>' : '') +
        (r.performance ? '<div class="routing-metrics" style="margin-top:8px">' +
        '<span>收益: <span class="val ' + metricColor(r.performance.avgReturn, 10, 0) + '">' + r.performance.avgReturn.toFixed(1) + '%</span></span>' +
        '<span>夏普: <span class="val">' + r.performance.avgSharpe.toFixed(2) + '</span></span>' +
        '<span>回撤: <span class="val">' + r.performance.avgMaxDD.toFixed(1) + '%</span></span>' +
        '<span>胜率: <span class="val">' + r.performance.avgWinRate.toFixed(1) + '%</span></span>' +
        '<span>盈利率: <span class="val">' + escHtml(r.performance.positiveRate || '-') + '</span></span>' +
        '</div>' : '') +
        '</div>';
    }).join('');
}

// ── Routing Table ───────────────────────────────────────────
async function loadRoutingTable() {
  const el = document.getElementById('table-results');
  el.innerHTML = '<div class="loading">加载路由表…</div>';

  const data = await apiFetch('/quant/admin/api/routing-table');

  if (!data.ok && data.error) {
    el.innerHTML = '<div class="card"><p style="color:var(--red)">' + escHtml(data.error) + '</p></div>';
    return;
  }

  if (data.message) {
    el.innerHTML = '<div class="card"><p style="color:var(--text-dim)">' + escHtml(data.message) + '</p>' +
      (data.hint ? '<p style="color:var(--yellow);font-size:12px;margin-top:6px">' + escHtml(data.hint) + '</p>' : '') + '</div>';
    return;
  }

  const table = data.table || {};
  const keys = Object.keys(table);
  if (!keys.length) {
    el.innerHTML = '<div class="empty">路由表为空</div>';
    return;
  }

  el.innerHTML = '<p style="color:var(--text-dim);font-size:12px;margin-bottom:12px">共 ' + keys.length + ' 个画像分类</p>' +
    keys.map(k => {
      const e = table[k];
      return '<div class="routing-entry">' +
        '<h4>' + escHtml(k) + '</h4>' +
        '<div class="combo-box"><div class="combo-name">' + escHtml(e.combo?.name || '-') + ' (' + escHtml(e.combo?.method || '-') + ')</div>' +
        '<div class="combo-strategies">子策略: ' + (e.combo?.strategies || []).map(s => escHtml(s.strategyId || s)).join(' + ') + '</div></div>' +
        '<div class="routing-metrics" style="margin-top:8px">' +
        '<span>综合评分: <span class="val">' + (e.score ?? '-') + '</span></span>' +
        '<span>收益: <span class="val ' + metricColor(e.avgReturn || 0, 10, 0) + '">' + (e.avgReturn ?? 0).toFixed(1) + '%</span></span>' +
        '<span>夏普: <span class="val">' + (e.avgSharpe ?? 0).toFixed(2) + '</span></span>' +
        '<span>回撤: <span class="val">' + (e.avgMaxDD ?? 0).toFixed(1) + '%</span></span>' +
        '<span>胜率: <span class="val">' + (e.avgWinRate ?? 0).toFixed(1) + '%</span></span>' +
        '<span>盈利率: <span class="val">' + escHtml(e.positiveRate || '-') + '</span></span>' +
        '<span>股票数: <span class="val">' + (e.stockCount ?? 0) + '</span></span>' +
        '</div></div>';
    }).join('');
}

function showBuildForm() {
  const el = document.getElementById('build-form');
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

async function buildRoutingTable() {
  const startDate = document.getElementById('build-start-date').value.trim() || undefined;
  const endDate = document.getElementById('build-end-date').value.trim() || undefined;
  const maxStocks = parseInt(document.getElementById('build-max-stocks').value) || 10;

  const el = document.getElementById('table-results');
  el.innerHTML = '<div class="loading">构建路由表中… 这可能需要几分钟</div>';

  const data = await apiFetch('/quant/admin/api/routing-table', {
    method: 'POST',
    body: JSON.stringify({ startDate, endDate, maxStocks })
  });

  if (!data.ok && data.error) {
    el.innerHTML = '<div class="card"><p style="color:var(--red)">' + escHtml(data.error) + '</p></div>';
    return;
  }

  el.innerHTML = '<div class="card">' +
    '<h3 style="color:var(--green)">✓ 路由表构建完成</h3>' +
    '<div class="routing-metrics" style="margin-top:8px">' +
    '<span>总股票数: <span class="val">' + (data.totalStocks ?? '-') + '</span></span>' +
    '<span>画像分组: <span class="val">' + (data.profileGroups ?? '-') + '</span></span>' +
    '<span>路由条目: <span class="val">' + (data.routingEntries ?? '-') + '</span></span>' +
    '<span>耗时: <span class="val">' + (data.elapsed ?? '-') + '</span></span>' +
    '</div></div>';

  // Auto-reload the table
  setTimeout(() => loadRoutingTable(), 500);
}

function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Profile Route Backtest ──────────────────────────────────
async function runPRBT() {
  const symbols = document.getElementById('prbt-symbols').value.trim();
  if (!symbols) return alert('请输入标的代码');
  const startDate = document.getElementById('prbt-start-date').value.trim() || undefined;
  const endDate = document.getElementById('prbt-end-date').value.trim() || undefined;
  const el = document.getElementById('prbt-results');
  el.innerHTML = '<div class="loading">画像→路由→回测验证中… 每只约需数秒</div>';

  const data = await apiFetch('/quant/admin/api/profile-route-backtest', {
    method: 'POST',
    body: JSON.stringify({ symbols, startDate, endDate })
  });

  if (!data.ok && data.error) {
    el.innerHTML = '<div class="card"><p style="color:var(--red)">' + escHtml(data.error) + '</p></div>';
    return;
  }

  const results = data.results || [];
  if (!results.length) {
    el.innerHTML = '<div class="empty">无结果</div>';
    return;
  }

  const mc = function(v, good, bad) {
    if (v >= good) return 'metric-good';
    if (v <= bad) return 'metric-bad';
    return 'metric-ok';
  };

  el.innerHTML = '<p style="color:var(--text-dim);font-size:12px;margin-bottom:12px">共验证 ' + results.length + ' 只股票</p>' +
    '<table><tr><th>代码</th><th>名称</th><th>画像</th><th>策略组合</th><th>来源</th><th>收益%</th><th>夏普</th><th>回撤%</th><th>胜率%</th><th>交易数</th><th>状态</th></tr>' +
    results.map(function(r) {
      if (!r.ok) return '<tr><td>' + escHtml(r.symbol) + '</td><td colspan="10"><span style="color:var(--red)">' + escHtml(r.error || '失败') + '</span></td></tr>';
      var bt = r.backtest || {};
      return '<tr>' +
        '<td><strong>' + escHtml(r.symbol) + '</strong></td>' +
        '<td>' + escHtml(r.name || '-') + '</td>' +
        '<td><span class="tag tag-medium" style="font-size:10px">' + escHtml(r.profileKey || '-') + '</span></td>' +
        '<td>' + escHtml(r.combo?.name || '-') + '</td>' +
        '<td>' + sourceTag(r.routingSource) + '</td>' +
        '<td class="' + mc(bt.totalReturn || 0, 10, 0) + '"><strong>' + (bt.totalReturn || 0).toFixed(1) + '%</strong></td>' +
        '<td>' + (bt.sharpeRatio || 0).toFixed(2) + '</td>' +
        '<td style="color:var(--red)">' + (bt.maxDrawdown || 0).toFixed(1) + '%</td>' +
        '<td>' + (bt.winRate || 0).toFixed(1) + '%</td>' +
        '<td>' + (bt.totalTrades || 0) + '</td>' +
        '<td>' + (bt.totalReturn > 0 ? '<span class="badge badge-green">盈利</span>' : bt.totalReturn < -5 ? '<span class="badge badge-red">亏损</span>' : '<span class="badge badge-yellow">持平</span>') + '</td>' +
        '</tr>';
    }).join('') + '</table>' +
    '<p style="color:var(--text-dim);font-size:11px;margin-top:8px">画像分析→策略路由→组合回测完整流水线验证结果</p>';
}

// ── Market Regime ───────────────────────────────────────────
async function loadRegime() {
  const el = document.getElementById('regime-results');
  el.innerHTML = '<div class="loading">检测宏观周期中…</div>';

  const data = await apiFetch('/quant/admin/api/market-regime');

  if (!data.ok && data.error) {
    el.innerHTML = '<div class="card"><p style="color:var(--red)">' + escHtml(data.error) + '</p></div>';
    return;
  }

  const regime = data.regime || 'unknown';
  const regimeColors = {
    recovery: 'var(--green)',
    overheating: 'var(--yellow)',
    stagflation: 'var(--red)',
    recession: 'var(--red)',
    unknown: 'var(--text-dim)',
  };
  const regimeLabels = {
    recovery: '复苏期 🌱',
    overheating: '过热期 🔥',
    stagflation: '滞胀期 ⚠️',
    recession: '衰退期 ❄️',
    unknown: 'δ֪',
  };
  const regimeImplications = {
    recovery: '复苏期对权益资产最友好。路由使用正常信号门槛，可适度进取。',
    overheating: '过热期关注紧缩政策风险。路由维持正常门槛，注意获利了结。',
    stagflation: '滞胀期防御为主。路由将提高投票门槛(+1)，倾向保守策略。',
    recession: '衰退期关注利率债和高股息。路由将提高投票门槛(+1)，严格信号确认。',
    unknown: '数据不足，使用默认路由参数。',
  };

  const signals = data.signals || {};
  el.innerHTML = '<div class="card">' +
    '<h3 style="color:' + (regimeColors[regime] || 'var(--text)') + '">' +
    (regimeLabels[regime] || regime) + '</h3>' +
    '<p style="margin:8px 0">' + escHtml(data.description || '') + '</p>' +
    '<div class="routing-metrics" style="margin:12px 0">' +
    (signals.pmi != null ? '<span>PMI: <span class="val ' + (signals.pmi > 50 ? 'metric-good' : 'metric-bad') + '">' + signals.pmi + '</span></span>' : '') +
    (signals.cpi_yoy != null ? '<span>CPI(YoY): <span class="val">' + signals.cpi_yoy + '%</span></span>' : '') +
    (signals.m2_yoy != null ? '<span>M2(YoY): <span class="val">' + signals.m2_yoy + '%</span></span>' : '') +
    '</div>' +
    '<div style="background:rgba(136,166,255,.08);border-radius:8px;padding:12px;margin-top:12px">' +
    '<strong>对路由的影响:</strong> ' + (regimeImplications[regime] || '') +
    '</div></div>';
}
</script>
</body>
</html>`;
}

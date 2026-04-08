/**
 * Financial Data Hub Page — 金融数据中心
 *
 * Tabs:
 *  - 宏观经济 (Macro): GDP, CPI, PMI, LPR, M2
 *  - 财报日历 (Earnings): upcoming earnings disclosures
 *  - 机构持仓 (Institutional): fund/institutional holdings for a symbol
 *  - 舆情分析 (Sentiment): social sentiment for a symbol
 *  - 热门股票 (Hot Stocks): community discussion popularity ranking
 *
 * Data fetched from POST `/quant/admin/api/datahub`.
 */

export function getDatahubPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>金融数据中心 — OpenClaw 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{font-size:18px;font-weight:600}
.header-right{display:flex;align-items:center;gap:12px;font-size:13px}
.header-right a{color:var(--accent);text-decoration:none}
.breadcrumb{background:var(--bg);padding:6px 24px;font-size:12px;border-bottom:1px solid var(--border)}
.breadcrumb a{color:var(--accent)}
.tabs{display:flex;gap:0;background:var(--bg2);border-bottom:1px solid var(--border);padding:0 12px;overflow-x:auto}
.tab{padding:9px 14px;cursor:pointer;font-size:13px;color:var(--text-dim);border-bottom:2px solid transparent;transition:all .2s;white-space:nowrap;user-select:none}
.tab:hover{color:var(--text)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
main{max-width:1200px;margin:0 auto;padding:20px}
.tab-content{display:none}
.tab-content.active{display:block}

.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px;overflow:hidden;min-width:0}
.card h3{font-size:14px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.card .table-scroll{overflow-x:auto;margin:0 -16px;padding:0 16px}

.form-row{display:flex;gap:8px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
.form-row label{font-size:13px;color:var(--text-dim);min-width:60px}
.form-row input,.form-row select{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-size:13px}
.form-row input:focus,.form-row select:focus{outline:none;border-color:var(--accent)}
.form-row input::placeholder{color:var(--text-dim)}

.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}

table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--text-dim);font-weight:600;font-size:12px;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover{background:var(--bg3)}

.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}

.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;text-align:center;min-width:110px}
.kpi .label{font-size:11px;color:var(--text-dim);margin-bottom:4px}
.kpi .value{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .sub{font-size:11px;color:var(--text-dim);margin-top:2px}

.loading{text-align:center;padding:40px;color:var(--text-dim);font-size:14px}
.error-msg{background:rgba(248,81,73,.1);border:1px solid rgba(248,81,73,.3);border-radius:6px;padding:10px 14px;color:var(--red);font-size:13px;margin-bottom:12px}
.empty-msg{text-align:center;padding:40px;color:var(--text-dim);font-size:13px}

.pre-wrap{white-space:pre-wrap;word-break:break-all;font-family:monospace;font-size:12px;background:var(--bg3);border-radius:6px;padding:12px;max-height:500px;overflow-y:auto}

footer{text-align:center;padding:24px;font-size:12px;color:var(--text-dim);border-top:1px solid var(--border)}

@media(max-width:700px){
  .form-row{flex-direction:column;align-items:stretch}
  .form-row label{min-width:auto}
}
</style>
</head>
<body>
<header>
  <h1>🌐 金融数据中心</h1>
  <div class="header-right">
    <a href="${b}/quant/">← 首页</a>
    <a href="${b}/quant/admin/ui">管理面板</a>
  </div>
</header>
<div class="breadcrumb"><a href="${b}/quant/">首页</a> / 金融数据中心</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('macro')">🏛 宏观经济</div>
  <div class="tab" onclick="switchTab('earnings')">📅 财报日历</div>
  <div class="tab" onclick="switchTab('institutional')">🏦 机构持仓</div>
  <div class="tab" onclick="switchTab('sentiment')">💬 舆情分析</div>
  <div class="tab" onclick="switchTab('hotstocks')">🔥 热门股票</div>
</div>

<main>
<!-- ======== Macro ======== -->
<div class="tab-content active" id="tab-macro">
  <div class="card">
    <h3>🏛 宏观经济数据</h3>
    <div class="form-row">
      <label>指标:</label>
      <select id="macroIndicator">
        <option value="">综合概览 (regime)</option>
        <option value="gdp">GDP</option>
        <option value="cpi">CPI</option>
        <option value="pmi">PMI</option>
        <option value="lpr">LPR 利率</option>
        <option value="m2">M2 货币供应</option>
        <option value="shibor">SHIBOR</option>
      </select>
      <button class="btn btn-primary" onclick="fetchMacro()">查询</button>
    </div>
    <div id="macroResult"><div class="empty-msg">选择指标后点击查询</div></div>
  </div>
</div>

<!-- ======== Earnings ======== -->
<div class="tab-content" id="tab-earnings">
  <div class="card">
    <h3>📅 财报日历</h3>
    <div class="form-row">
      <label>天数:</label>
      <input type="number" id="earningsDays" value="30" min="1" max="180" style="width:80px">
      <button class="btn btn-primary" onclick="fetchEarnings()">查询</button>
    </div>
    <div id="earningsResult"><div class="empty-msg">点击查询查看近期财报</div></div>
  </div>
</div>

<!-- ======== Institutional ======== -->
<div class="tab-content" id="tab-institutional">
  <div class="card">
    <h3>🏦 机构持仓与变动</h3>
    <div class="form-row">
      <label>代码:</label>
      <input type="text" id="instSymbol" placeholder="例如 600519" style="width:140px"
        onkeydown="if(event.key==='Enter')fetchInstitutional()">
      <select id="instAction">
        <option value="smart_money_signal">聪明资金信号</option>
        <option value="block_trades">大宗交易</option>
      </select>
      <button class="btn btn-primary" onclick="fetchInstitutional()">查询</button>
    </div>
    <div id="instResult"><div class="empty-msg">输入股票代码查询机构数据</div></div>
  </div>
</div>

<!-- ======== Sentiment ======== -->
<div class="tab-content" id="tab-sentiment">
  <div class="card">
    <h3>💬 舆情分析</h3>
    <div class="form-row">
      <label>代码:</label>
      <input type="text" id="sentSymbol" placeholder="例如 600519" style="width:140px"
        onkeydown="if(event.key==='Enter')fetchSentiment()">
      <button class="btn btn-primary" onclick="fetchSentiment()">查询</button>
    </div>
    <div id="sentResult"><div class="empty-msg">输入股票代码查询舆情</div></div>
  </div>
</div>

<!-- ======== Hot Stocks ======== -->
<div class="tab-content" id="tab-hotstocks">
  <div class="card">
    <h3>🔥 今日社区讨论热度</h3>
    <div class="form-row">
      <label>数量:</label>
      <input type="number" id="hotLimit" value="20" min="5" max="100" style="width:80px">
      <button class="btn btn-primary" onclick="fetchHotStocks()">查询</button>
    </div>
    <div id="hotResult"><div class="loading">加载中…</div></div>
  </div>
</div>
</main>

<footer>OpenClaw 量化交易 — 金融数据中心</footer>

<script>
const BASE = "${b}";
const API = BASE + "/quant/admin/api/datahub";

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t, i) => {
    const tabs = ['macro','earnings','institutional','sentiment','hotstocks'];
    t.classList.toggle('active', tabs[i] === name);
  });
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  const el = document.getElementById('tab-' + name);
  if (el) el.classList.add('active');
  if (location.hash !== '#' + name) history.pushState(null, null, '#' + name);
}
window.addEventListener('popstate', () => {
  const h = (location.hash || '').replace('#', '');
  switchTab(h || 'macro');
});
(function() {
  const h = (location.hash || '').replace('#', '');
  if (h) switchTab(h);
})();

async function apiCall(action, params, retries) {
  retries = retries || 1;
  for (let i = 0; i <= retries; i++) {
    try {
      const resp = await fetch(API, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ action, ...params }),
      });
      return await resp.json();
    } catch(e) {
      if (i < retries) { await new Promise(r => setTimeout(r, 1000)); continue; }
      throw e;
    }
  }
}

function esc(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function numFmt(n) { return n == null ? '-' : Number(n).toLocaleString('zh-CN'); }

// ---- Macro ----
const REGIME_META = {
  overheating: { label: '过热期', color: '#e74c3c', icon: '🔥', advice: '关注紧缩政策风险，减少高beta敞口' },
  recovery:    { label: '复苏期', color: '#2ecc71', icon: '🌱', advice: '对权益资产最友好，可适度增加股票配置' },
  stagflation: { label: '滞胀期', color: '#e67e22', icon: '⚠️', advice: '防御为主，关注必需消费和贵金属' },
  recession:   { label: '衰退期', color: '#3498db', icon: '❄️', advice: '关注利率债和高股息防御标的' },
  unknown:     { label: 'δ֪',   color: '#95a5a6', icon: '❓', advice: '' },
};

function renderRegime(data) {
  const rm = REGIME_META[data.regime] || REGIME_META.unknown;
  const sig = data.signals || {};
  const pmi = sig.pmi != null ? sig.pmi.toFixed(1) : '-';
  const cpi = sig.cpi_yoy != null ? sig.cpi_yoy.toFixed(2) + '%' : '-';
  const m2 = sig.m2_yoy != null ? sig.m2_yoy.toFixed(2) + '%' : '-';
  const pmiOk = sig.pmi != null && sig.pmi > 50;
  const cpiHigh = sig.cpi_yoy != null && sig.cpi_yoy > 3;

  return '<div class="kpi-row">'
    + '<div class="kpi" style="border-left:4px solid ' + rm.color + '">'
    +   '<div class="label">宏观周期</div>'
    +   '<div class="value" style="color:' + rm.color + '">' + rm.icon + ' ' + rm.label + '</div>'
    + '</div>'
    + '<div class="kpi"><div class="label">PMI (制造业)</div>'
    +   '<div class="value" style="color:' + (pmiOk ? '#2ecc71' : '#e74c3c') + '">' + pmi
    +   ' <small>' + (pmiOk ? '扩张' : '收缩') + '</small></div></div>'
    + '<div class="kpi"><div class="label">CPI 同比</div>'
    +   '<div class="value" style="color:' + (cpiHigh ? '#e74c3c' : '#2ecc71') + '">' + cpi
    +   ' <small>' + (cpiHigh ? '通胀偏高' : '温和') + '</small></div></div>'
    + '<div class="kpi"><div class="label">M2 同比</div>'
    +   '<div class="value">' + m2 + '</div></div>'
    + '</div>'
    + '<div class="card" style="margin-top:12px;padding:12px 16px">'
    +   '<div style="font-size:14px;margin-bottom:6px">' + esc(data.description) + '</div>'
    +   '<div style="font-size:13px;color:#aaa">💡 ' + esc(rm.advice) + '</div>'
    +   '<div style="font-size:12px;color:#666;margin-top:8px">方法论: ' + esc(data.methodology || '') + '</div>'
    + '</div>';
}

const INDICATOR_COLUMNS = {
  gdp:    { cols: [['日期','date'],['GDP','gdp'],['GDP 同比','gdp_yoy']], pctCols: ['gdp_yoy'] },
  cpi:    { cols: [['日期','date'],['CPI','cpi'],['CPI 同比','cpi_yoy'],['CPI 环比','cpi_mom']], pctCols: ['cpi_yoy','cpi_mom'] },
  pmi:    { cols: [['日期','date'],['PMI','pmi'],['同比','pmi_yoy'],['非制造业','non_mfg_pmi']], pctCols: ['pmi_yoy'] },
  lpr:    { cols: [['日期','date'],['1年期 LPR','lpr_1y'],['5年期 LPR','lpr_5y']], pctCols: ['lpr_1y','lpr_5y'] },
  m2:     { cols: [['日期','date'],['M2 同比','m2_yoy']], pctCols: ['m2_yoy'] },
  shibor: { cols: [['日期','date'],['隔夜','overnight'],['1周','1w'],['1月','1m'],['3月','3m']], pctCols: ['overnight','1w','1m','3m'] },
};

function renderHistory(indicator, data) {
  const rows = data.data || [];
  if (!rows.length) return '<div class="empty-msg">暂无数据</div>';
  const meta = INDICATOR_COLUMNS[indicator];
  if (!meta) return '<div class="pre-wrap">' + esc(JSON.stringify(data, null, 2)) + '</div>';
  let html = '<div style="font-size:13px;color:#aaa;margin-bottom:8px">'
    + esc(indicator.toUpperCase()) + ' · 共 ' + data.count + ' 条 (' + esc(data.market) + ')</div>';
  html += '<div class="table-scroll"><table><thead><tr>';
  for (const [label] of meta.cols) html += '<th>' + esc(label) + '</th>';
  html += '</tr></thead><tbody>';
  for (const r of rows) {
    html += '<tr>';
    for (const [, key] of meta.cols) {
      const v = r[key];
      if (key === 'date') { html += '<td>' + esc(v) + '</td>'; continue; }
      const num = v != null ? Number(v) : null;
      const display = num != null ? (meta.pctCols.includes(key) ? num.toFixed(2) + '%' : numFmt(num)) : '-';
      html += '<td style="text-align:right">' + display + '</td>';
    }
    html += '</tr>';
  }
  html += '</tbody></table></div>';
  return html;
}

async function fetchMacro() {
  const indicator = document.getElementById('macroIndicator').value;
  const el = document.getElementById('macroResult');
  el.innerHTML = '<div class="loading">查询中…</div>';
  try {
    const d = await apiCall('macro', { indicator: indicator || undefined });
    if (d.error) { el.innerHTML = '<div class="error-msg">' + esc(d.error) + '</div>'; return; }
    const data = d.data || d;
    if (data.error) { el.innerHTML = '<div class="error-msg">' + esc(data.error) + '</div>'; return; }
    if (!indicator && data.regime) {
      el.innerHTML = renderRegime(data);
    } else if (indicator) {
      el.innerHTML = renderHistory(indicator, data);
    } else {
      el.innerHTML = '<div class="pre-wrap">' + esc(JSON.stringify(data, null, 2)) + '</div>';
    }
  } catch(e) {
    el.innerHTML = '<div class="error-msg">' + esc(e.message || '请求失败') + ' <a href="#" onclick="fetchMacro();return false" style="color:var(--accent)">重试</a></div>';
  }
}

// ---- Earnings ----
async function fetchEarnings() {
  const days = parseInt(document.getElementById('earningsDays').value) || 30;
  const el = document.getElementById('earningsResult');
  el.innerHTML = '<div class="loading">查询中…</div>';
  try {
    const d = await apiCall('earnings', { days });
    if (d.error) { el.innerHTML = '<div class="error-msg">' + esc(d.error) + '</div>'; return; }
    const data = d.data || d;
    if (data.error) { el.innerHTML = '<div class="error-msg">' + esc(data.error) + '</div>'; return; }
    const earnings = data.earnings || [];
    if (earnings.length === 0) {
      el.innerHTML = '<div class="empty-msg">近' + days + '天内暂无找到即将披露的财报</div>';
      return;
    }
    let html = '<div class="table-scroll"><table><thead><tr><th>披露日期</th><th>代码</th><th>名称</th></tr></thead><tbody>';
    for (const e of earnings.slice(0, 50)) {
      html += '<tr><td>' + esc(e.disclosure_date) + '</td><td>' + esc(e.symbol) + '</td><td>' + esc(e.name) + '</td></tr>';
    }
    html += '</tbody></table></div>';
    if (data.count) html = '<div style="margin-bottom:8px;font-size:13px">共 <b>' + esc(data.count) + '</b> 家即将披露</div>' + html;
    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = '<div class="error-msg">' + esc(e.message) + '</div>';
  }
}

// ---- Institutional ----
function renderSmartMoney(data) {
  const score = data.smart_money_score ?? 0;
  const scoreColor = score >= 70 ? 'var(--red)' : score >= 40 ? 'var(--yellow)' : 'var(--green)';
  const signals = data.signals || {};
  const details = data.details || [];
  let html = '<div class="kpi-row">';
  html += '<div class="kpi"><div class="label">聪明资金得分</div><div class="value" style="color:' + scoreColor + '">' + score + '</div><div class="sub">0-100</div></div>';
  if (signals.institutional_trend) {
    const trendColor = signals.institutional_trend === '增持' ? 'var(--red)' : signals.institutional_trend === '减持' ? 'var(--green)' : 'var(--text-dim)';
    html += '<div class="kpi"><div class="label">机构趋势</div><div class="value" style="font-size:18px;color:' + trendColor + '">' + esc(signals.institutional_trend) + '</div></div>';
  }
  if (signals.block_trade_premium != null) {
    html += '<div class="kpi"><div class="label">大宗交易溢价</div><div class="value" style="font-size:18px">' + Number(signals.block_trade_premium).toFixed(2) + '%</div></div>';
  }
  if (signals.main_force_flow_5d != null) {
    const flow = Number(signals.main_force_flow_5d);
    const flowColor = flow > 0 ? 'var(--red)' : flow < 0 ? 'var(--green)' : 'var(--text-dim)';
    html += '<div class="kpi"><div class="label">5日主力净流入</div><div class="value" style="font-size:18px;color:' + flowColor + '">' + numFmt(flow) + ' 万</div></div>';
  }
  html += '</div>';
  if (data.conclusion) {
    html += '<div style="margin-bottom:12px;padding:10px 14px;background:var(--bg3);border-radius:6px;font-size:13px"><strong>结论：</strong>' + esc(data.conclusion) + '</div>';
  }
  if (details.length) {
    html += '<div style="font-size:13px"><strong>信号明细：</strong><ul style="margin:6px 0 0 18px;list-style:disc">';
    details.forEach(d => { html += '<li>' + esc(d) + '</li>'; });
    html += '</ul></div>';
  }
  if (!details.length && !data.conclusion) {
    html += '<div class="empty-msg">暂无详细信号</div>';
  }
  return html;
}

function renderBlockTrades(data) {
  const trades = data.trades || [];
  if (!trades.length) return '<div class="empty-msg">暂无大宗交易数据</div>';
  let html = '<div style="margin-bottom:8px;font-size:13px;color:var(--text-dim)">共 ' + (data.count || trades.length) + ' 条记录</div>';
  html += '<div class="table-scroll"><table><thead><tr><th>日期</th><th>代码</th><th>名称</th><th>成交价</th><th>成交量(万股)</th><th>成交额(万元)</th><th>溢价率</th></tr></thead><tbody>';
  trades.forEach(t => {
    const prem = t.premium_pct != null ? (t.premium_pct > 0 ? '+' : '') + Number(t.premium_pct).toFixed(2) + '%' : '-';
    const premCls = t.premium_pct > 0 ? 'color:var(--red)' : t.premium_pct < 0 ? 'color:var(--green)' : '';
    html += '<tr><td>' + esc(t.date) + '</td><td>' + esc(t.symbol || t.code) + '</td><td>' + esc(t.name) + '</td><td>' + numFmt(t.price) + '</td><td>' + numFmt(t.volume) + '</td><td>' + numFmt(t.amount) + '</td><td style="' + premCls + '">' + prem + '</td></tr>';
  });
  html += '</tbody></table></div>';
  return html;
}

async function fetchInstitutional() {
  const symbol = document.getElementById('instSymbol').value.trim();
  const action = document.getElementById('instAction').value;
  const el = document.getElementById('instResult');
  if (!symbol && action === 'smart_money_signal') {
    el.innerHTML = '<div class="error-msg">请输入股票代码</div>';
    return;
  }
  el.innerHTML = '<div class="loading">查询中…</div>';
  try {
    const d = await apiCall('institutional', { symbol: symbol || undefined, sub_action: action });
    if (d.error) { el.innerHTML = '<div class="error-msg">' + esc(d.error) + '</div>'; return; }
    const data = d.data || d;
    if (data.error) { el.innerHTML = '<div class="error-msg">' + esc(data.error) + '</div>'; return; }
    if (action === 'smart_money_signal') {
      el.innerHTML = renderSmartMoney(data);
    } else if (action === 'block_trades') {
      el.innerHTML = renderBlockTrades(data);
    } else {
      el.innerHTML = '<div class="pre-wrap">' + esc(JSON.stringify(data, null, 2)) + '</div>';
    }
  } catch(e) {
    el.innerHTML = '<div class="error-msg">' + esc(e.message) + '</div>';
  }
}

// ---- Sentiment ----
function renderSentiment(data) {
  // social_sentiment.py returns: symbol, timestamp, rank, attention_score,
  // participation_score, composite_score, level, interpretation
  const cs = data.composite_score != null ? Number(data.composite_score) : null;
  const level = data.level || '';
  const interp = data.interpretation || '';
  const attn = data.attention_score != null ? Number(data.attention_score) : null;
  const part = data.participation_score != null ? Number(data.participation_score) : null;

  let html = '<div class="kpi-row">';
  if (cs != null) {
    const csColor = cs >= 80 ? 'var(--red)' : cs >= 50 ? 'var(--yellow)' : 'var(--green)';
    html += '<div class="kpi"><div class="label">综合热度</div><div class="value" style="color:' + csColor + '">' + cs.toFixed(1) + '</div><div class="sub">0 ~ 100</div></div>';
  }
  if (level) {
    const lvColor = level.includes('高') ? 'var(--red)' : level.includes('低') ? 'var(--green)' : 'var(--yellow)';
    html += '<div class="kpi"><div class="label">热度等级</div><div class="value" style="font-size:18px;color:' + lvColor + '">' + esc(level) + '</div></div>';
  }
  if (attn != null) {
    html += '<div class="kpi"><div class="label">关注度</div><div class="value" style="font-size:18px">' + attn.toFixed(1) + '</div></div>';
  }
  if (part != null) {
    html += '<div class="kpi"><div class="label">参与度</div><div class="value" style="font-size:18px">' + part.toFixed(1) + '</div></div>';
  }
  if (data.rank != null) {
    html += '<div class="kpi"><div class="label">热度排名</div><div class="value" style="font-size:18px">#' + data.rank + '</div></div>';
  }
  html += '</div>';
  if (interp) {
    html += '<div style="padding:10px 14px;background:var(--bg3);border-radius:6px;font-size:13px"><strong>解读：</strong>' + esc(interp) + '</div>';
  }
  if (cs == null && !level) {
    html += '<div class="empty-msg">暂无舆情数据</div>';
  }
  return html;
}

async function fetchSentiment() {
  const symbol = document.getElementById('sentSymbol').value.trim();
  const el = document.getElementById('sentResult');
  if (!symbol) { el.innerHTML = '<div class="error-msg">请输入股票代码</div>'; return; }
  el.innerHTML = '<div class="loading">查询中…</div>';
  try {
    const d = await apiCall('sentiment', { symbol });
    if (d.error) { el.innerHTML = '<div class="error-msg">' + esc(d.error) + '</div>'; return; }
    const data = d.data || d;
    if (data.error) { el.innerHTML = '<div class="error-msg">' + esc(data.error) + '</div>'; return; }
    el.innerHTML = renderSentiment(data);
  } catch(e) {
    el.innerHTML = '<div class="error-msg">' + esc(e.message) + '</div>';
  }
}

// ---- Hot Stocks ----
async function fetchHotStocks() {
  const limit = parseInt(document.getElementById('hotLimit').value) || 20;
  const el = document.getElementById('hotResult');
  el.innerHTML = '<div class="loading">查询中…</div>';
  try {
    const d = await apiCall('hot_stocks', { limit });
    if (d.error) { el.innerHTML = '<div class="error-msg">' + esc(d.error) + '</div>'; return; }
    const data = d.data || d;
    if (data.error) { el.innerHTML = '<div class="error-msg">' + esc(data.error) + '</div>'; return; }
    const stocks = data.stocks || data.hot_stocks || [];
    if (stocks.length === 0) {
      el.innerHTML = '<div class="empty-msg">暂无热门股票数据</div>';
      return;
    }
    let html = '<div class="table-scroll"><table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>热度</th><th>涨跌幅</th></tr></thead><tbody>';
    stocks.forEach((s, i) => {
      const chg = s.change_pct ?? s.pct_change ?? s.change;
      const chgStr = chg != null ? (chg > 0 ? '+' : '') + Number(chg).toFixed(2) + '%' : '-';
      const chgCls = chg > 0 ? 'color:var(--red)' : chg < 0 ? 'color:var(--green)' : '';
      const sym = s.symbol || s.code || '';
      const symLink = sym ? '<a href="' + BASE + '/quant/admin/stock?symbol=' + encodeURIComponent(sym) + '" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none" onmouseover="this.style.textDecoration=\\'underline\\'" onmouseout="this.style.textDecoration=\\'none\\'">' + esc(sym) + '</a>' : '-';
      html += '<tr><td>' + (i+1) + '</td><td>' + symLink + '</td><td>' + esc(s.name || '-') + '</td><td>' + numFmt(s.popularity || s.heat || s.hot_score || s.score) + '</td><td style="' + chgCls + '">' + chgStr + '</td></tr>';
    });
    html += '</tbody></table></div>';
    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = '<div class="error-msg">' + esc(e.message) + '</div>';
  }
}

// Auto-load hot stocks on tab open
fetchHotStocks();
</script>
</body>
</html>`;
}

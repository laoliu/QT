/**
 * Lianban Page — self-contained SPA for consecutive limit-up board analysis.
 *
 * Tabs: 梯队总览 (ladder scan), 二进三候选 (relay candidates), 个股轨迹 (stock detail)
 * Data fetched from `/quant/admin/api/lianban?action=scan|candidates|detail`.
 */

/** Return the full HTML for the lianban analysis page. */
export function getLianbanPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>连板分析 — OpenClaw 量化</title>
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

/* Cards & layout */
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px;overflow:hidden;min-width:0}
.card h3{font-size:14px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.cards-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;margin-bottom:16px}
.card .table-scroll{overflow-x:auto;margin:0 -16px;padding:0 16px}

/* KPIs */
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;text-align:center;min-width:110px}
.kpi .label{font-size:11px;color:var(--text-dim);margin-bottom:4px}
.kpi .value{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .sub{font-size:11px;color:var(--text-dim);margin-top:2px}

/* Tables */
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--text-dim);font-weight:600;font-size:12px;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover{background:var(--bg3)}

/* Badges */
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}
.badge-orange{background:rgba(219,109,40,.15);color:var(--orange)}

/* Score bar */
.score-bar{display:flex;align-items:center;gap:8px}
.score-fill{height:6px;border-radius:3px;transition:width .3s}
.score-track{flex:1;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}
.btn-sm{padding:4px 10px;font-size:12px}

/* Input */
.input{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:7px 12px;color:var(--text);font-size:13px}
.input:focus{outline:none;border-color:var(--accent)}
.input::placeholder{color:var(--text-dim)}

/* Loading */
.loading{text-align:center;padding:40px;color:var(--text-dim)}
.spinner{display:inline-block;width:20px;height:20px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* Sentiment meter */
.meter{display:flex;gap:2px;align-items:flex-end;height:24px;margin-top:4px}
.meter-bar{flex:1;border-radius:2px 2px 0 0;transition:height .3s}

/* Ladder */
.ladder-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)}
.ladder-level{font-weight:700;min-width:50px;font-size:14px}
.ladder-bar{height:20px;border-radius:4px;background:var(--accent);opacity:.7;transition:width .3s;min-width:2px}
.ladder-count{font-size:12px;color:var(--text-dim);min-width:30px}
.ladder-stocks{font-size:12px;color:var(--text-dim);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* Stock link */
.stock-link{color:var(--accent);cursor:pointer;font-weight:500}
.stock-link:hover{text-decoration:underline}

@media(max-width:700px){
  .cards-row{grid-template-columns:1fr}
  .kpi-row{flex-direction:column}
  table{font-size:12px}
  th,td{padding:5px 6px}
}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">🏠 首页</a> / <span style="color:var(--text)">🔥 连板分析</span></div>
<header>
  <h1>🔥 连板分析</h1>
  <div class="header-right">
    <a href="${b}/quant/dashboard/ui">📊 仪表盘</a>
    <a href="${b}/quant/admin/ui">🔧 管理</a>
    <span style="color:var(--text-dim)" id="clock"></span>
  </div>
</header>
<div class="tabs">
  <div class="tab active" data-tab="ladder">📊 梯队总览</div>
  <div class="tab" data-tab="zhaban">🚨 炸板预警</div>
  <div class="tab" data-tab="candidates">🎯 二进三候选</div>
  <div class="tab" data-tab="detail">🔍 个股轨迹</div>
</div>
<main>

<!-- ═══ Tab: Ladder Scan ═══ -->
<div class="tab-content active" id="tab-ladder">
  <div class="loading" id="ladder-loading"><div class="spinner"></div><br>加载连板梯队数据…</div>
  <div id="ladder-content" style="display:none">
    <div class="kpi-row" id="ladder-kpis"></div>
    <div class="cards-row">
      <div class="card">
        <h3>📊 连板梯队</h3>
        <div id="ladder-chart"></div>
      </div>
      <div class="card">
        <h3>📈 晋级率</h3>
        <div id="promo-info"></div>
      </div>
    </div>
    <div class="cards-row">
      <div class="card">
        <h3>🔥 各板块个股</h3>
        <div id="ladder-stocks"></div>
      </div>
      <div class="card">
        <h3>💥 炸板股</h3>
        <div id="failed-boards"></div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ Tab: Zhaban Prediction ═══ -->
<div class="tab-content" id="tab-zhaban">
  <div style="display:flex;gap:8px;align-items:center;margin-bottom:16px">
    <label style="font-size:13px;color:var(--text-dim)">最低风险分:</label>
    <select class="input" id="minRisk" style="width:80px" onchange="loadZhaban()">
      <option value="0">全部</option>
      <option value="30" selected>≥30</option>
      <option value="50">≥50</option>
      <option value="70">≥70</option>
    </select>
    <button class="btn btn-sm" onclick="loadZhaban()">🔄 刷新</button>
  </div>
  <div class="loading" id="zhaban-loading"><div class="spinner"></div><br>分析炸板风险中…</div>
  <div id="zhaban-content" style="display:none">
    <div class="kpi-row" id="zhaban-kpis"></div>
    <div id="zhaban-insights" class="card" style="display:none"></div>
    <div class="card">
      <h3>🚨 炸板风险排行</h3>
      <div style="overflow-x:auto" id="zhaban-table"></div>
    </div>
  </div>
</div>

<!-- ═══ Tab: Candidates ═══ -->
<div class="tab-content" id="tab-candidates">
  <div style="display:flex;gap:8px;align-items:center;margin-bottom:16px">
    <label style="font-size:13px;color:var(--text-dim)">最低连板数:</label>
    <select class="input" id="minBoards" style="width:80px" onchange="loadCandidates()">
      <option value="2" selected>2板</option>
      <option value="3">3板</option>
      <option value="4">4板</option>
    </select>
    <button class="btn btn-sm" onclick="loadCandidates()">🔄 刷新</button>
  </div>
  <div class="loading" id="cand-loading"><div class="spinner"></div><br>加载候选数据…</div>
  <div id="cand-content" style="display:none">
    <div class="card" id="cand-env"></div>
    <div class="card">
      <h3>🎯 候选列表</h3>
      <div style="overflow-x:auto" id="cand-table"></div>
    </div>
  </div>
</div>

<!-- ═══ Tab: Stock Detail ═══ -->
<div class="tab-content" id="tab-detail">
  <div style="display:flex;gap:8px;align-items:center;margin-bottom:16px">
    <input type="text" class="input" id="detailSymbol" placeholder="输入股票代码，如 600722" style="width:200px"
      onkeydown="if(event.key==='Enter')loadDetail()">
    <button class="btn btn-primary btn-sm" onclick="loadDetail()">查询</button>
  </div>
  <div class="loading" id="detail-loading" style="display:none"><div class="spinner"></div><br>加载个股数据…</div>
  <div id="detail-content" style="display:none"></div>
  <div id="detail-placeholder" class="card" style="text-align:center;padding:40px;color:var(--text-dim)">
    输入股票代码查询连板轨迹、板质评分、龙虎榜等信息
  </div>
</div>

</main>
<script>
const B = "${b}";
const API = B + "/quant/admin/api/lianban";

// ── Tab switching (with hash-based routing for browser back/forward) ──
function activateTab(name) {
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(x => x.classList.remove("active"));
  const tab = document.querySelector('.tab[data-tab="' + name + '"]');
  const content = document.getElementById("tab-" + name);
  if (tab) tab.classList.add("active");
  if (content) content.classList.add("active");
}
document.querySelectorAll(".tab").forEach(t => {
  t.addEventListener("click", () => {
    const tab = t.dataset.tab;
    activateTab(tab);
    if (location.hash !== "#" + tab) history.pushState(null, null, "#" + tab);
  });
});
window.addEventListener("popstate", () => {
  const h = (location.hash || "").replace("#", "");
  activateTab(h || "ladder");
});
(function() {
  const h = (location.hash || "").replace("#", "");
  if (h) activateTab(h);
})();

// ── Clock ──
function updateClock() {
  const now = new Date();
  document.getElementById("clock").textContent =
    now.toLocaleDateString("zh-CN") + " " + now.toLocaleTimeString("zh-CN", { hour12: false });
}
updateClock();
setInterval(updateClock, 1000);

// ── Helpers ──
function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
function scoreColor(s) { return s >= 70 ? "var(--green)" : s >= 50 ? "var(--yellow)" : "var(--red)"; }
function pctColor(v) { return v > 0 ? "var(--green)" : v < 0 ? "var(--red)" : "var(--text-dim)"; }
function riskColor(s) { return s >= 70 ? "var(--red)" : s >= 50 ? "var(--orange)" : s >= 30 ? "var(--yellow)" : "var(--green)"; }
function riskBadge(level) {
  const m = { "极高": "badge-red", "高": "badge-orange", "中": "badge-yellow", "低": "badge-green" };
  return m[level] || "badge-blue";
}
function badgeClass(type) {
  const m = { "换手板": "badge-green", "T字板": "badge-yellow", "一字板": "badge-red" };
  return m[type] || "badge-blue";
}

async function apiFetch(params, retries) {
  if (retries === undefined) retries = 1;
  const url = API + "?" + new URLSearchParams(params).toString();
  var lastErr;
  for (var attempt = 0; attempt <= retries; attempt++) {
    try {
      var ctrl = new AbortController();
      var timer = setTimeout(function() { ctrl.abort(); }, 60000);
      var resp = await fetch(url, { signal: ctrl.signal });
      clearTimeout(timer);
      if (!resp.ok) {
        var body = "";
        try { body = await resp.text(); } catch(e) {}
        var errMsg = "HTTP " + resp.status;
        if (body) { try { var j = JSON.parse(body); if (j.error) errMsg += ": " + j.error; } catch(e) { if (body.length < 200) errMsg += ": " + body; } }
        throw new Error(errMsg);
      }
      return resp.json();
    } catch(err) {
      lastErr = err;
      if (err.name === "AbortError") { lastErr = new Error("请求超时 (60s)"); break; }
      if (attempt < retries && (err.message === "Failed to fetch" || err.message === "Load failed")) {
        await new Promise(function(r) { setTimeout(r, 1000); });
        continue;
      }
    }
  }
  throw lastErr;
}

// ── Ladder Scan ──
async function loadLadder() {
  try {
    const d = await apiFetch({ action: "scan" });
    const s = d.summary || {};

    // KPIs
    const kpis = document.getElementById("ladder-kpis");
    kpis.innerHTML = [
      kpiHtml("涨停总数", s.total_zt || 0, ""),
      kpiHtml("最高连板", s.highest_board || 0, "板"),
      kpiHtml("情绪评分", s.sentiment_score || 0, "/100", sentimentColor(s.sentiment_score)),
      kpiHtml("情绪倾向", s.sentiment_level || "--", "", sentimentColor(s.sentiment_score)),
      kpiHtml("涨跌停比", s.zt_dt_ratio || 0, "", s.zt_dt_ratio >= 2 ? "var(--green)" : "var(--red)"),
      kpiHtml("炸板率", ((s.failure_rate || 0) * 100).toFixed(1) + "%", "", s.failure_rate < 0.3 ? "var(--green)" : "var(--red)"),
    ].join("");

    // Ladder chart
    const ladder = s.ladder || {};
    const sortedLevels = Object.keys(ladder).sort((a, b) => Number(b) - Number(a));
    // Use separate max for 2板+ so 1板 count doesn't squash other bars
    const counts2Plus = sortedLevels.filter(l => Number(l) >= 2).map(l => ladder[l]);
    const max2Plus = Math.max(...counts2Plus, 1);
    const ladderHtml = sortedLevels.map(level => {
      const count = ladder[level];
      const n = Number(level);
      const refMax = n >= 2 ? max2Plus : count;
      const pct = Math.max((count / refMax * 100), 3).toFixed(0);
      const stocks = (d.stocks_by_level || {})[level + "连板"] || [];
      const names = stocks.map(s => s.name).join("、");
      return '<div class="ladder-row">' +
        '<span class="ladder-level">' + level + '板</span>' +
        '<div style="flex:1;display:flex;align-items:center;gap:8px">' +
          '<div class="ladder-bar" style="width:' + pct + '%;background:' + (n >= 3 ? 'var(--red)' : n >= 2 ? 'var(--orange)' : 'var(--accent)') + '"></div>' +
          '<span class="ladder-count">' + count + '</span>' +
        '</div>' +
        (names ? '<span class="ladder-stocks" title="' + esc(names) + '">' + esc(names) + '</span>' : '') +
      '</div>';
    }).join("");
    document.getElementById("ladder-chart").innerHTML = ladderHtml || '<div style="color:var(--text-dim)">暂无数据</div>';

    // Promotion rates
    const promo = d.promotion || {};
    let promoHtml = "";
    for (const [label, info] of Object.entries(promo)) {
      promoHtml += '<div style="margin-bottom:12px">' +
        '<div style="font-weight:600;margin-bottom:4px">' + esc(label) + '</div>' +
        '<div>晋级率: <span style="font-weight:700;color:' + (parseFloat(info.rate) >= 30 ? 'var(--green)' : 'var(--red)') + '">' + esc(info.rate) + '</span>' +
        ' (' + info.promoted + '/' + info.total + ')</div>';
      if (info.promoted_stocks && info.promoted_stocks.length) {
        promoHtml += '<div style="font-size:12px;color:var(--text-dim);margin-top:4px">✅ 晋级: ' +
          info.promoted_stocks.map(s => '<a href="' + B + '/quant/admin/stock?symbol=' + encodeURIComponent(s.code) + '" target="_blank" class="stock-link">' + esc(s.name) + '</a>').join("、") + '</div>';
      }
      if (info.failed_stocks && info.failed_stocks.length) {
        promoHtml += '<div style="font-size:12px;color:var(--text-dim);margin-top:2px">❌ 失败: ' +
          info.failed_stocks.map(s => esc(s.name)).join("、") + '</div>';
      }
      promoHtml += '</div>';
    }
    document.getElementById("promo-info").innerHTML = promoHtml || '<div style="color:var(--text-dim)">暂无数据</div>';

    // Stocks by level
    let stocksHtml = "";
    const levels = d.stocks_by_level || {};
    for (const [levelLabel, stocks] of Object.entries(levels).sort((a, b) => {
      const na = parseInt(a[0]); const nb = parseInt(b[0]); return nb - na;
    })) {
      stocksHtml += '<div style="margin-bottom:12px"><div style="font-weight:600;margin-bottom:6px;color:var(--orange)">' + esc(levelLabel) + '</div>';
      stocksHtml += '<table><tr><th>代码</th><th>名称</th><th>行业</th><th>换手率</th><th>成交额</th></tr>';
      for (const s of stocks) {
        stocksHtml += '<tr>' +
          '<td><a href="' + B + '/quant/admin/stock?symbol=' + encodeURIComponent(s.code) + '" target="_blank" class="stock-link">' + esc(s.code) + '</a></td>' +
          '<td>' + esc(s.name) + '</td>' +
          '<td>' + esc(s.industry) + '</td>' +
          '<td>' + esc(s.turnover_rate) + '</td>' +
          '<td>' + esc(s.amount_yi) + '</td>' +
        '</tr>';
      }
      stocksHtml += '</table></div>';
    }
    document.getElementById("ladder-stocks").innerHTML = stocksHtml || '<div style="color:var(--text-dim)">暂无数据</div>';

    // Failed boards
    const failed = d.failed_boards || [];
    if (failed.length) {
      let fHtml = '<div class="table-scroll"><table><tr><th>代码</th><th>名称</th><th>涨跌幅</th><th>开板次数</th><th>行业</th></tr>';
      for (const s of failed) {
        fHtml += '<tr>' +
          '<td><a href="' + B + '/quant/admin/stock?symbol=' + encodeURIComponent(s.code) + '" target="_blank" class="stock-link">' + esc(s.code) + '</a></td>' +
          '<td>' + esc(s.name) + '</td>' +
          '<td style="color:' + pctColor(parseFloat(s.change_pct)) + '">' + esc(s.change_pct) + '</td>' +
          '<td>' + (s.open_count || 0) + '</td>' +
          '<td>' + esc(s.industry) + '</td>' +
        '</tr>';
      }
      fHtml += '</table></div>';
      document.getElementById("failed-boards").innerHTML = fHtml;
    } else {
      document.getElementById("failed-boards").innerHTML = '<div style="color:var(--text-dim)">暂无炸板股</div>';
    }

    document.getElementById("ladder-loading").style.display = "none";
    document.getElementById("ladder-content").style.display = "block";
  } catch (err) {
    document.getElementById("ladder-loading").innerHTML = '<div style="color:var(--red)">加载失败: ' + esc(err.message) + '</div>';
  }
}

// ── Candidates ──
async function loadCandidates() {
  const minBoards = document.getElementById("minBoards").value;
  document.getElementById("cand-loading").style.display = "block";
  document.getElementById("cand-content").style.display = "none";
  try {
    const d = await apiFetch({ action: "candidates", min_boards: minBoards, limit: "20" });
    const env = d.environment || {};

    // Environment card
    document.getElementById("cand-env").innerHTML =
      '<h3>🌡️ 市场环境</h3>' +
      '<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:13px">' +
        '<div>情绪: <span style="font-weight:700;color:' + sentimentColor(env.score) + '">' + (env.score || 0) + '/100 ' + esc(env.level || "") + '</span></div>' +
        '<div>适合接力: ' + (env.suitable ? '<span class="badge badge-green">✅ 是</span>' : '<span class="badge badge-red">⚠️ 否</span>') + '</div>' +
      '</div>' +
      (env.detail ? '<div style="font-size:12px;color:var(--text-dim);margin-top:6px">' + esc(env.detail) + '</div>' : '');

    // Candidates table
    const cands = d.candidates || [];
    if (cands.length) {
      let html = '<table><tr><th>#</th><th>代码</th><th>名称</th><th>连板</th><th>评分</th><th>换手率</th><th>成交额</th><th>封单</th><th>开板</th><th>行业</th><th>信号</th></tr>';
      for (const c of cands) {
        const sc = c.score || 0;
        html += '<tr>' +
          '<td>' + (c.rank || "") + '</td>' +
          '<td><a href="' + B + '/quant/admin/stock?symbol=' + encodeURIComponent(c.code) + '" target="_blank" class="stock-link">' + esc(c.code) + '</a></td>' +
          '<td>' + esc(c.name) + '</td>' +
          '<td><span class="badge badge-orange">' + c.lian_ban_count + '板</span></td>' +
          '<td><span style="color:' + scoreColor(sc) + ';font-weight:700">' + sc.toFixed(1) + '</span></td>' +
          '<td>' + (c.turnover_rate || 0).toFixed(1) + '%</td>' +
          '<td>' + (c.amount_yi || 0).toFixed(2) + '亿</td>' +
          '<td>' + (c.seal_amount_yi ? c.seal_amount_yi.toFixed(2) + '亿' : '--') + '</td>' +
          '<td>' + (c.open_count || 0) + '</td>' +
          '<td>' + esc(c.industry || "") + '</td>' +
          '<td style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis">' + (c.signals || []).join(", ") + '</td>' +
        '</tr>';
      }
      html += '</table>';
      document.getElementById("cand-table").innerHTML = html;
    } else {
      document.getElementById("cand-table").innerHTML = '<div style="color:var(--text-dim);padding:20px;text-align:center">暂无候选</div>';
    }

    document.getElementById("cand-loading").style.display = "none";
    document.getElementById("cand-content").style.display = "block";
  } catch (err) {
    document.getElementById("cand-loading").innerHTML = '<div style="color:var(--red)">加载失败: ' + esc(err.message) + '</div>';
  }
}

// ── Stock Detail ──
async function loadDetail() {
  const symbol = document.getElementById("detailSymbol").value.trim();
  if (!symbol) return;
  document.getElementById("detail-placeholder").style.display = "none";
  document.getElementById("detail-content").style.display = "none";
  document.getElementById("detail-loading").style.display = "block";
  try {
    const d = await apiFetch({ action: "detail", symbol: symbol });
    let html = "";

    // Header
    const poolName = d.pool_info ? d.pool_info.name : (d.zbgc_info ? d.zbgc_info.name : symbol);
    const isZhaBan = !!(d.zbgc_info && d.board_count === 0);
    html += '<div class="card">' +
      '<h3>' + (isZhaBan ? '💥' : '📊') + ' ' + esc(poolName) + ' (' + esc(d.symbol) + ') — ' +
      (isZhaBan ? '<span style="color:var(--red)">炸板</span>' : (d.board_count || 0) + '连板') + '</h3>';

    // ZhaBan (failed board) notice
    if (isZhaBan) {
      const z = d.zbgc_info;
      html += '<div style="background:rgba(248,81,73,.08);border:1px solid rgba(248,81,73,.3);border-radius:6px;padding:12px;margin-bottom:12px">' +
        '<div style="font-weight:600;color:var(--red);margin-bottom:6px">⚠️ 此股为炸板股 — 盘中曾触及涨停但未能封住</div>' +
        '<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:13px">' +
          '<div>涨跌幅: <span style="color:' + pctColor(z.change_pct || 0) + '">' + (z.change_pct || 0).toFixed(2) + '%</span></div>' +
          '<div>开板次数: <span class="badge badge-red">' + (z.open_count || 0) + '次</span></div>' +
          '<div>首次封板: ' + esc(z.first_board_time || "--") + '</div>' +
          '<div>换手率: ' + (z.turnover_rate || 0).toFixed(1) + '%</div>' +
          '<div>成交额: ' + (z.amount ? (z.amount / 1e8).toFixed(2) + '亿' : '--') + '</div>' +
          '<div>行业: ' + esc(z.industry || "--") + '</div>' +
        '</div>' +
      '</div>';
    }

    // Quality score (skip for zhaban stocks)
    const q = d.quality || {};
    const score = q.total_score || 0;
    if (!isZhaBan) {
    html += '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;margin-bottom:12px">' +
      '<div style="font-size:28px;font-weight:700;color:' + scoreColor(score) + '">' + score.toFixed(1) + '<span style="font-size:14px;color:var(--text-dim)">/100</span></div>' +
      '<div class="score-bar" style="flex:1;min-width:120px"><div class="score-track"><div class="score-fill" style="width:' + score + '%;background:' + scoreColor(score) + '"></div></div></div>' +
    '</div>';

    // Factor breakdown
    const f = q.factors || {};
    if (Object.keys(f).length) {
      html += '<div style="display:flex;gap:12px;flex-wrap:wrap;font-size:12px;margin-bottom:8px">';
      const labels = { board_type_score: "板型", turnover_score: "换手", volume_trend_score: "量能", count_score: "连板数", freshness_score: "新鲜度" };
      for (const [k, label] of Object.entries(labels)) {
        if (f[k] !== undefined && f[k] !== null) {
          html += '<span class="badge badge-blue">' + label + ': ' + Number(f[k]).toFixed(1) + '</span>';
        }
      }
      if (f.avg_turnover !== null && f.avg_turnover !== undefined) {
        html += '<span class="badge badge-purple">均换手: ' + f.avg_turnover + '%</span>';
      }
      html += '</div>';
    }

    // Warnings
    const warnings = q.warnings || [];
    if (warnings.length) {
      html += '<div style="margin-top:8px">';
      for (const w of warnings) {
        html += '<div class="badge badge-red" style="margin:2px">⚠️ ' + esc(w) + '</div>';
      }
      html += '</div>';
    }
    } // end if (!isZhaBan)
    html += '</div>';

    // Board history table
    const boards = d.boards || [];
    if (boards.length) {
      html += '<div class="card"><h3>📋 连板轨迹</h3>' +
        '<div style="overflow-x:auto"><table>' +
        '<tr><th>日期</th><th>板型</th><th>收盘</th><th>涨幅</th><th>成交量</th><th>成交额</th><th>开盘</th><th>最高</th><th>最低</th></tr>';
      for (const b of boards) {
        html += '<tr>' +
          '<td>' + esc(b.date || "--") + '</td>' +
          '<td><span class="badge ' + badgeClass(b.board_type) + '">' + esc(b.board_type) + '</span></td>' +
          '<td>' + (b.close || 0).toFixed(2) + '</td>' +
          '<td style="color:' + pctColor(b.change_pct) + '">' + (b.change_pct || 0).toFixed(2) + '%</td>' +
          '<td>' + formatVol(b.volume) + '</td>' +
          '<td>' + formatAmount(b.amount) + '</td>' +
          '<td>' + (b.open || 0).toFixed(2) + '</td>' +
          '<td>' + (b.high || 0).toFixed(2) + '</td>' +
          '<td>' + (b.low || 0).toFixed(2) + '</td>' +
        '</tr>';
      }
      html += '</table></div></div>';
    }

    // Pool info
    if (d.pool_info) {
      const p = d.pool_info;
      html += '<div class="card"><h3>📌 涨停池信息</h3>' +
        '<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:13px">' +
          '<div>首次封板: ' + esc(p.first_board_time || "--") + '</div>' +
          '<div>最后封板: ' + esc(p.last_board_time || "--") + '</div>' +
          '<div>连板数: <span class="badge badge-orange">' + (p.lian_ban_count || 0) + '</span></div>' +
          '<div>炸板: ' + (p.open_count || 0) + '次</div>' +
          '<div>封单: ' + (p.seal_amount ? (p.seal_amount / 1e8).toFixed(2) + '亿' : '--') + '</div>' +
          '<div>行业: ' + esc(p.industry || "--") + '</div>' +
        '</div></div>';
    }

    // Dragon tiger
    if (d.dragon_tiger && !d.dragon_tiger.error) {
      html += '<div class="card"><h3>🐉 龙虎榜</h3><div style="font-size:12px;color:var(--text-dim)">最近龙虎榜数据 (详细信息见个股详情页)</div></div>';
    }

    // Warnings from request
    const reqWarnings = d.warnings || [];
    if (reqWarnings.length) {
      html += '<div class="card" style="border-color:var(--yellow)"><h3>⚠️ 注意</h3>';
      for (const w of reqWarnings) {
        html += '<div style="font-size:12px;color:var(--yellow)">' + esc(w) + '</div>';
      }
      html += '</div>';
    }

    document.getElementById("detail-content").innerHTML = html;
    document.getElementById("detail-loading").style.display = "none";
    document.getElementById("detail-content").style.display = "block";
  } catch (err) {
    document.getElementById("detail-loading").style.display = "none";
    document.getElementById("detail-content").innerHTML = '<div class="card" style="color:var(--red)">查询失败: ' + esc(err.message) + '</div>';
    document.getElementById("detail-content").style.display = "block";
  }
}

function kpiHtml(label, value, suffix, color) {
  return '<div class="kpi"><div class="label">' + label + '</div><div class="value" style="color:' + (color || "var(--text)") + '">' + value + '<span style="font-size:12px;color:var(--text-dim)">' + (suffix || '') + '</span></div></div>';
}

function sentimentColor(score) {
  if (score >= 70) return "var(--green)";
  if (score >= 40) return "var(--yellow)";
  return "var(--red)";
}

function formatVol(v) {
  if (!v) return "--";
  if (v >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (v >= 1e4) return (v / 1e4).toFixed(0) + "万";
  return String(v);
}

function formatAmount(v) {
  if (!v) return "--";
  if (v >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (v >= 1e4) return (v / 1e4).toFixed(0) + "万";
  return v.toFixed(0);
}

// ── Init ──
loadLadder();
loadCandidates();
loadZhaban();

// ── Zhaban Prediction ──
async function loadZhaban() {
  const minRisk = document.getElementById("minRisk").value;
  document.getElementById("zhaban-loading").style.display = "block";
  document.getElementById("zhaban-content").style.display = "none";
  try {
    const d = await apiFetch({ action: "zhaban", min_risk: minRisk });

    // KPIs
    const rs = d.risk_summary || {};
    document.getElementById("zhaban-kpis").innerHTML = [
      kpiHtml("涨停总数", d.total_zt || 0, ""),
      kpiHtml("已炸板", d.total_zbgc || 0, "", "var(--red)"),
      kpiHtml("炸板率", ((d.env_failure_rate || 0) * 100).toFixed(1) + "%", "", d.env_failure_rate < 0.3 ? "var(--green)" : "var(--red)"),
      kpiHtml("极高风险", rs["极高"] || 0, "只", "var(--red)"),
      kpiHtml("高风险", rs["高"] || 0, "只", "var(--orange)"),
      kpiHtml("中风险", rs["中"] || 0, "只", "var(--yellow)"),
      kpiHtml("低风险", rs["低"] || 0, "只", "var(--green)"),
    ].join("");

    // Pattern Insights
    const insights = d.pattern_insights || [];
    const insightsEl = document.getElementById("zhaban-insights");
    if (insights.length) {
      insightsEl.innerHTML = '<h3>💡 规律发现</h3>' +
        insights.map(function(i) { return '<div style="font-size:13px;padding:4px 0;color:var(--yellow)">' + esc(i) + '</div>'; }).join("");
      insightsEl.style.display = "block";
    } else {
      insightsEl.style.display = "none";
    }

    // Table
    const preds = d.predictions || [];
    if (preds.length) {
      let html = '<table><tr><th>#</th><th>代码</th><th>名称</th><th>连板</th>' +
        '<th>风险分</th><th>风险</th><th>开板</th><th>封单比</th>' +
        '<th>首封</th><th>换手率</th><th>成交额</th><th>行业</th>' +
        '<th>风险因子</th></tr>';
      for (const p of preds) {
        const rc = riskColor(p.risk_score);
        const negSigs = (p.signals_negative || []).join("; ");
        const posSigs = (p.signals_positive || []).join("; ");
        let sigsHtml = '';
        if (negSigs) sigsHtml += '<span style="color:var(--red);font-size:11px">' + esc(negSigs) + '</span>';
        if (posSigs) sigsHtml += (negSigs ? '<br>' : '') + '<span style="color:var(--green);font-size:11px">' + esc(posSigs) + '</span>';
        html += '<tr>' +
          '<td>' + (p.rank || '') + '</td>' +
          '<td><a href="' + B + '/quant/admin/stock?symbol=' + encodeURIComponent(p.code) + '" target="_blank" class="stock-link">' + esc(p.code) + '</a></td>' +
          '<td>' + esc(p.name) + '</td>' +
          '<td><span class="badge badge-orange">' + (p.lian_ban_count || 0) + '板</span></td>' +
          '<td><span style="color:' + rc + ';font-weight:700;font-size:16px">' + (p.risk_score || 0).toFixed(1) + '</span></td>' +
          '<td><span class="badge ' + riskBadge(p.risk_level) + '">' + esc(p.risk_level || '') + '</span></td>' +
          '<td style="color:' + (p.open_count > 1 ? 'var(--red)' : 'var(--text)') + '">' + (p.open_count || 0) + '次</td>' +
          '<td style="color:' + ((p.seal_ratio || 0) < 0.3 ? 'var(--red)' : 'var(--text)') + '">' + (p.seal_ratio != null ? p.seal_ratio.toFixed(2) : '--') + '</td>' +
          '<td>' + esc(p.first_board_time || '--') + '</td>' +
          '<td>' + (p.turnover_rate || 0).toFixed(1) + '%</td>' +
          '<td>' + (p.amount_yi || 0).toFixed(2) + '亿</td>' +
          '<td>' + esc(p.industry || '') + '</td>' +
          '<td style="max-width:260px;white-space:normal;line-height:1.4">' + sigsHtml + '</td>' +
        '</tr>';
      }
      html += '</table>';
      document.getElementById("zhaban-table").innerHTML = html;
    } else {
      document.getElementById("zhaban-table").innerHTML = '<div style="color:var(--text-dim);padding:20px;text-align:center">暂无符合条件的预测</div>';
    }

    document.getElementById("zhaban-loading").style.display = "none";
    document.getElementById("zhaban-content").style.display = "block";
  } catch (err) {
    document.getElementById("zhaban-loading").innerHTML = '<div style="color:var(--red)">加载失败: ' + esc(err.message) + '</div>';
  }
}
</script>
</body>
</html>`;
}

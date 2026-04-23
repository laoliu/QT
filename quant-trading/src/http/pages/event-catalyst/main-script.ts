/**
 * Browser-side JavaScript for the Event Catalyst page.
 */
export function getEventCatalystMainScript(): string {
  return `
// ── Tab switching ──
document.querySelectorAll(".tab").forEach(t => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(x => x.classList.remove("active"));
    t.classList.add("active");
    document.getElementById("tab-" + t.dataset.tab).classList.add("active");
  });
});

// ── Clock ──
function updateClock() {
  const now = new Date();
  document.getElementById("clock").textContent =
    now.toLocaleDateString("zh-CN") + " " + now.toLocaleTimeString("zh-CN", { hour12: false });
}
updateClock();
setInterval(updateClock, 1000);

// ── Helpers ──
function esc(s) { const d = document.createElement("div"); d.textContent = String(s); return d.innerHTML; }
function pctColor(v) { return v > 0 ? "var(--red)" : v < 0 ? "var(--green)" : "var(--text-dim)"; }
function pctSign(v) { return v > 0 ? "+" + v.toFixed(2) + "%" : v.toFixed(2) + "%"; }
function scoreColor(s) { return s >= 70 ? "var(--green)" : s >= 40 ? "var(--yellow)" : "var(--text-dim)"; }
function flowBadge(trend) {
  if (trend === "strong") return '<span class="badge badge-green">资金流入</span>';
  if (trend === "weak") return '<span class="badge badge-yellow">弱流入</span>';
  return '<span class="badge badge-red">资金流出</span>';
}
function scarcityBadge(score) {
  if (score >= 0.8) return '<span class="badge badge-purple">高稀缺</span>';
  if (score >= 0.5) return '<span class="badge badge-blue">中稀缺</span>';
  if (score > 0) return '<span class="badge badge-yellow">低稀缺</span>';
  return '';
}

async function apiCall(action, extra) {
  const body = Object.assign({ action }, extra || {});
  const start = Date.now();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 120000);
  try {
    const r = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    const data = await r.json();
    // Ensure loading spinner is visible for at least 600ms
    const elapsed = Date.now() - start;
    if (elapsed < 600) await new Promise(ok => setTimeout(ok, 600 - elapsed));
    return data;
  } catch (e) {
    if (e.name === "AbortError") throw new Error("请求超时，请稍后重试");
    if (e.message === "Failed to fetch") throw new Error("无法连接服务器，请检查 Gateway 是否正在运行");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

function showLoading(id) {
  document.getElementById(id + "-loading").style.display = "";
  document.getElementById(id + "-content").style.display = "none";
  const ph = document.getElementById(id + "-placeholder");
  if (ph) ph.style.display = "none";
}
function showContent(id) {
  document.getElementById(id + "-loading").style.display = "none";
  document.getElementById(id + "-content").style.display = "";
}
function showError(id, msg) {
  document.getElementById(id + "-loading").style.display = "none";
  document.getElementById(id + "-content").style.display = "";
  document.getElementById(id + "-content").innerHTML =
    '<div class="card" style="color:var(--red)">⚠️ ' + esc(msg) + '</div>';
}
function completionBanner(msg) {
  const now = new Date();
  const ts = now.toLocaleTimeString("zh-CN", { hour12: false });
  return '<div style="padding:8px 14px;margin-bottom:12px;background:rgba(63,185,80,.12);border:1px solid var(--green);border-radius:6px;font-size:13px;color:var(--green);display:flex;align-items:center;gap:8px">' +
    '<span style="font-size:16px">✅</span> ' + esc(msg) + ' <span style="margin-left:auto;color:var(--text-dim);font-size:12px">' + ts + '</span></div>';
}

// ═══ Full Scan ═══
async function runScan() {
  showLoading("scan");
  try {
    const data = await apiCall("scan", {
      min_change_pct: parseFloat(document.getElementById("scanThreshold").value) || 2,
      require_fund_flow: document.getElementById("scanFlowReq").value === "true",
      top_n: parseInt(document.getElementById("scanTopN").value) || 10,
    });
    if (data.error) { showError("scan", data.error); return; }
    renderScan(data);
    showContent("scan");
    // Save to history
    try {
      await fetch(SCAN_HISTORY_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data }),
      });
      loadScanHistory();
    } catch (_) { /* history save failure is non-critical */ }
  } catch (e) { showError("scan", "请求失败: " + e.message); }
}

function renderScan(d) {
  const el = document.getElementById("scan-content");
  let html = completionBanner("扫描完成 — " + (d.triggered ? (d.triggeredCount || 0) + " 只美股触发" : (d.message || "今日无催化信号")));
  if (!d.triggered) {
    html += '<div class="empty-state" style="border:1px solid var(--border);border-radius:8px;padding:24px"><div class="icon">😴</div><h3>' + esc(d.message || "今日无事件催化信号") +
      '</h3><p>已检查 ' + (d.usChecked || 0) + ' 只美股科技龙头</p></div>';
    el.innerHTML = html;
    return;
  }

  html += '<div class="kpi-row">';
  html += '<div class="kpi"><div class="label">美股异动</div><div class="value" style="color:var(--red)">' + (d.triggeredCount || 0) + '</div><div class="sub">只触发</div></div>';
  html += '<div class="kpi"><div class="label">A股候选</div><div class="value" style="color:var(--accent)">' + (d.candidateCount || 0) + '</div><div class="sub">只匹配</div></div>';
  html += '<div class="kpi"><div class="label">精选推荐</div><div class="value" style="color:var(--green)">' + (d.pickCount || 0) + '</div><div class="sub">只入选</div></div>';
  html += '<div class="kpi"><div class="label">日期</div><div class="value" style="font-size:14px">' + esc(d.date || "") + '</div></div>';
  html += '</div>';

  // Triggered US stocks
  if (d.triggeredUS && d.triggeredUS.length) {
    html += '<div class="card"><h3>🇺🇸 触发美股</h3><div class="table-scroll"><table><tr><th>代码</th><th>名称</th><th>涨跌幅</th><th>新闻主题</th><th>头条</th></tr>';
    for (const u of d.triggeredUS) {
      html += '<tr><td><strong>' + esc(u.symbol) + '</strong></td><td>' + esc(u.name) + '</td>';
      html += '<td style="color:' + pctColor(u.changePct) + ';font-weight:700">' + pctSign(u.changePct) + '</td>';
      html += '<td>' + (u.newsThemes || []).map(t => '<span class="badge badge-blue">' + esc(t) + '</span>').join(" ") + '</td>';
      html += '<td style="white-space:normal;max-width:300px;font-size:12px;color:var(--text-dim)">' + (u.topNews || []).map(n => esc(n)).join("<br>") + '</td></tr>';
    }
    html += '</table></div></div>';
  }

  // Picks table
  if (d.picks && d.picks.length) {
    html += '<div class="card"><h3>🎯 精选A股标的</h3><div class="table-scroll"><table>';
    html += '<tr><th>#</th><th>代码</th><th>名称</th><th>综合分</th><th>催化分</th><th>稀缺性</th><th>板块</th><th>资金</th><th>信号</th></tr>';
    for (const p of d.picks) {
      const maxScore = Math.max(...d.picks.map(x => x.totalScore), 1);
      const pct = (p.totalScore / maxScore * 100).toFixed(0);
      html += '<tr>';
      html += '<td><strong>' + p.rank + '</strong></td>';
      html += '<td><a href="' + B + '/quant/admin/stock?symbol=' + encodeURIComponent(p.code) + '" target="_blank" class="stock-link">' + esc(p.code) + '</a></td>';
      html += '<td>' + esc(p.name) + '</td>';
      html += '<td><div class="score-bar"><span style="font-weight:700;color:' + scoreColor(p.totalScore) + '">' + p.totalScore.toFixed(1) + '</span>';
      html += '<div class="score-track"><div class="score-fill" style="width:' + pct + '%;background:' + scoreColor(p.totalScore) + '"></div></div></div></td>';
      html += '<td>' + p.catalystScore.toFixed(1) + '</td>';
      html += '<td>' + scarcityBadge(p.scarcityScore) + (p.scarcityScore > 0 ? ' ' + p.scarcityScore.toFixed(1) : '') + '</td>';
      html += '<td>' + (p.sectors || []).map(s => '<span class="badge badge-blue">' + esc(s) + '</span>').join(" ") + '</td>';
      html += '<td>' + (p.fundFlow ? flowBadge(p.fundFlow.trend) : '<span style="color:var(--text-dim)">-</span>') + '</td>';
      html += '<td style="white-space:normal;max-width:260px">' + (p.signals || []).map(s => {
        let cls = "signal-tag";
        if (s.includes("美股")) cls += " hot";
        else if (s.includes("资金")) cls += " flow";
        else if (s.includes("ϡȱ")) cls += " scarcity";
        return '<span class="' + cls + '">' + esc(s) + '</span>';
      }).join("") + '</td>';
      html += '</tr>';
    }
    html += '</table></div></div>';
  }

  el.innerHTML = html;
}

// ═══ Scan History ═══
async function loadScanHistory() {
  try {
    const r = await fetch(SCAN_HISTORY_API);
    const data = await r.json();
    if (data.status !== "ok" || !data.count) {
      document.getElementById("scan-history-panel").style.display = "none";
      return;
    }
    document.getElementById("scan-history-panel").style.display = "";
    document.getElementById("scan-history-count").textContent = "共 " + data.count + " 条记录";

    // Top picks frequency
    const topEl = document.getElementById("scan-history-top-picks");
    if (data.topPicks && data.topPicks.length) {
      topEl.style.display = "";
      let th = '<div style="font-size:12px;color:var(--text-dim);margin-bottom:6px">📊 高频入选标的:</div><div style="display:flex;flex-wrap:wrap;gap:6px">';
      for (const tp of data.topPicks) {
        th += '<span class="badge badge-blue" style="cursor:pointer" onclick="window.open(B+\\'/quant/admin/stock?symbol=' + encodeURIComponent(tp.code) + '\\',\\'_blank\\')">' + esc(tp.code) + ' ' + esc(tp.name) + ' <span style="opacity:.7">' + tp.count + '次(' + tp.pct + '%)</span></span>';
      }
      th += '</div>';
      topEl.innerHTML = th;
    } else {
      topEl.style.display = "none";
    }

    // History list
    const listEl = document.getElementById("scan-history-list");
    let lh = '';
    for (const e of data.entries) {
      const ts = new Date(e.timestamp).toLocaleString("zh-CN", { hour12: false });
      const trigBadge = e.triggered
        ? '<span class="badge badge-red">' + e.triggeredCount + '只触发</span>'
        : '<span class="badge badge-yellow">无催化</span>';
      const pickBadge = e.pickCount > 0 ? ' <span class="badge badge-green">' + e.pickCount + '只入选</span>' : '';
      lh += '<div class="history-item" onclick="viewScanHistory(\\'' + esc(e.id) + '\\')">';
      lh += '<span class="hi-version">' + esc(e.version) + '</span>';
      lh += '<span class="hi-meta">' + ts + ' · 阈值' + e.threshold + '% · ' + (e.requireFundFlow ? '资金验证' : '无验证') + '</span>';
      lh += '<span class="hi-stats">' + trigBadge + pickBadge + '</span>';
      lh += '<span class="hi-del" onclick="event.stopPropagation();deleteScanHistory(\\'' + esc(e.id) + '\\')" title="删除">🗑️</span>';
      lh += '</div>';
    }
    listEl.innerHTML = lh;
  } catch (_) {}
}

async function viewScanHistory(id) {
  showLoading("scan");
  try {
    const r = await fetch(SCAN_HISTORY_API + "?id=" + encodeURIComponent(id));
    const data = await r.json();
    if (data.status !== "ok" || !data.entry) { showError("scan", data.message || "快照加载失败"); return; }
    const entry = data.entry;
    // Reconstruct a scan-like data object for renderScan
    renderScan({
      triggered: entry.triggered,
      triggeredCount: entry.triggeredCount,
      candidateCount: entry.candidateCount,
      pickCount: entry.pickCount,
      date: entry.date,
      message: entry.triggered ? null : "该次扫描无事件催化信号",
      usChecked: 15,
      triggeredUS: entry.triggeredUS || [],
      picks: entry.picks || [],
    });
    // Prepend a history badge
    const el = document.getElementById("scan-content");
    const histBanner = '<div style="padding:6px 14px;margin-bottom:8px;background:rgba(88,166,255,.1);border:1px solid var(--accent);border-radius:6px;font-size:12px;color:var(--accent);display:flex;align-items:center;gap:8px">' +
      '<span>📋</span> 历史快照: <strong>' + esc(entry.version) + '</strong> · ' +
      new Date(entry.timestamp).toLocaleString("zh-CN", { hour12: false }) +
      ' · 阈值' + entry.threshold + '%' +
      (entry.requireFundFlow ? ' · 资金验证' : '') +
      '<button class="btn btn-sm" style="margin-left:auto" onclick="clearScanView()">返回</button>' +
      '</div>';
    el.innerHTML = histBanner + el.innerHTML;
    showContent("scan");
  } catch (e) { showError("scan", "请求失败: " + e.message); }
}

function clearScanView() {
  document.getElementById("scan-content").style.display = "none";
  document.getElementById("scan-content").innerHTML = "";
  document.getElementById("scan-placeholder").style.display = "";
}

async function deleteScanHistory(id) {
  if (!confirm("确定删除该扫描快照？")) return;
  try {
    await fetch(SCAN_HISTORY_API + "?id=" + encodeURIComponent(id), { method: "DELETE" });
    loadScanHistory();
    // If viewing that snapshot, clear the view
    clearScanView();
  } catch (_) {}
}

// Load scan history on page init
loadScanHistory();

// ═══ US Movers ═══
async function loadUS() {
  showLoading("us");
  try {
    const data = await apiCall("check_us", {
      min_change_pct: parseFloat(document.getElementById("usThreshold").value) || 2,
    });
    if (data.error) { showError("us", data.error); return; }
    renderUS(data);
    showContent("us");
  } catch (e) { showError("us", "请求失败: " + e.message); }
}

function renderUS(d) {
  const el = document.getElementById("us-content");
  const movers = d.allMovers || [];
  let html = completionBanner("美股数据已加载 — " + movers.length + " 只");
  if (!movers.length) {
    html += '<div class="empty-state" style="border:1px solid var(--border);border-radius:8px;padding:24px"><div class="icon">😴</div><h3>暂无美股行情数据</h3></div>';
    el.innerHTML = html;
    return;
  }

  html += '<div class="kpi-row">';
  html += '<div class="kpi"><div class="label">监控数</div><div class="value">' + movers.length + '</div></div>';
  html += '<div class="kpi"><div class="label">显著异动</div><div class="value" style="color:var(--red)">' + (d.significantCount || 0) + '</div><div class="sub">≥' + (d.threshold || 2) + '%</div></div>';
  html += '<div class="kpi"><div class="label">日期</div><div class="value" style="font-size:14px">' + esc(d.date || "") + '</div></div>';
  html += '</div>';

  html += '<div class="cards-row">';
  for (const m of movers) {
    const border = m.significant ? "border-color:var(--red)" : "";
    html += '<div class="mover-card" style="' + border + '">';
    html += '<div class="mover-header"><div><span class="mover-symbol">' + esc(m.symbol) + '</span><span class="mover-name">' + esc(m.name) + '</span></div>';
    html += '<div class="mover-change" style="color:' + pctColor(m.changePct) + '">' + pctSign(m.changePct) + '</div></div>';
    html += '<div style="font-size:12px;color:var(--text-dim)">价格 $' + (m.price || 0).toFixed(2) + ' · 新闻 ' + (m.newsCount || 0) + ' 条</div>';

    const themes = (m.detectedThemes || []).concat(m.mappedThemes || []);
    const uniqueThemes = [...new Set(themes)];
    if (uniqueThemes.length) {
      html += '<div class="mover-themes">' + uniqueThemes.map(t => '<span class="badge badge-purple">' + esc(t) + '</span>').join("") + '</div>';
    }
    if (m.topNews && m.topNews.length) {
      html += '<div class="mover-news">' + m.topNews.map(n => '• ' + esc(n)).join("<br>") + '</div>';
    }
    if (m.significant) {
      html += '<div style="margin-top:8px"><button class="btn btn-sm" onclick="autoMap(&#39;' + esc(m.symbol) + '&#39;)">🔗 查看A股对标</button></div>';
    }
    html += '</div>';
  }
  html += '</div>';

  el.innerHTML = html;
}

function autoMap(sym) {
  // Open A-share mapping in a new popup window
  const w = window.open("", "amap_" + sym, "width=860,height=700,scrollbars=yes,resizable=yes");
  if (!w) { alert("弹窗被拦截，请允许弹窗后重试"); return; }
  w.document.write('<html><head><meta charset="utf-8"><title>A股对标 — ' + esc(sym) + '</title>' +
    '<style>' +
    ':root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--purple:#bc8cff}' +
    '*{margin:0;padding:0;box-sizing:border-box}' +
    'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);padding:20px}' +
    'a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}' +
    '.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}' +
    '.card h3{font-size:15px;font-weight:600;margin-bottom:10px}' +
    'table{width:100%;border-collapse:collapse;font-size:13px}' +
    'th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--text-dim);font-weight:600;font-size:12px;white-space:nowrap}' +
    'td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}' +
    'tr:hover{background:var(--bg3)}' +
    '.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}' +
    '.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}' +
    '.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}' +
    '.badge-orange{background:rgba(219,109,40,.15);color:var(--orange)}' +
    '.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}' +
    '.signal-tag{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;margin:2px 4px 2px 0;background:var(--bg3);border:1px solid var(--border);color:var(--text-dim)}' +
    '.signal-tag.scarcity{background:rgba(188,140,255,.1);border-color:var(--purple);color:var(--purple)}' +
    '.spinner{display:inline-block;width:20px;height:20px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite}' +
    '@keyframes spin{to{transform:rotate(360deg)}}' +
    '.loading{text-align:center;padding:60px;color:var(--text-dim)}' +
    '.stock-link{color:var(--accent)}' +
    '</style></head><body>' +
    '<div class="loading"><div class="spinner"></div><br>正在加载 ' + esc(sym) + ' 的A股对标数据…</div>' +
    '</body></html>');
  w.document.close();
  // Fetch mapping data and render into the popup
  fetch(API, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "map_event", us_symbol: sym, themes: [] }),
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (!w || w.closed) return;
    var _esc = function(s) { var el = w.document.createElement("div"); el.textContent = String(s); return el.innerHTML; };
    var _scBadge = function(score) {
      if (score >= 0.8) return '<span class="badge badge-purple">高稀缺</span>';
      if (score >= 0.5) return '<span class="badge badge-blue">中稀缺</span>';
      if (score > 0) return '<span class="badge badge-yellow">低稀缺</span>';
      return '';
    };
    if (d.error) { w.document.body.innerHTML = '<div class="card" style="color:var(--red)">⚠️ ' + _esc(d.error) + '</div>'; return; }
    var h = '<div class="card"><h3>🔗 ' + _esc(d.usSymbol || sym) + ' (' + _esc(d.usName || "") + ') → A股对标</h3>';
    h += '<div style="margin-bottom:12px">';
    h += '<span style="font-size:12px;color:var(--text-dim)">主题: </span>';
    h += (d.usThemes || []).map(function(t) { return '<span class="badge badge-purple">' + _esc(t) + '</span>'; }).join(" ");
    if (d.filterThemes !== "all") {
      h += ' <span style="font-size:12px;color:var(--text-dim)">· ɸѡ: </span>';
      h += (d.filterThemes || []).map(function(t) { return '<span class="badge badge-orange">' + _esc(t) + '</span>'; }).join(" ");
    }
    h += ' <span style="font-size:12px;color:var(--text-dim)">· 共 ' + (d.candidateCount || 0) + ' 只标的</span>';
    h += '</div>';
    if (d.candidates && d.candidates.length) {
      h += '<div style="overflow-x:auto"><table>';
      h += '<tr><th>代码</th><th>名称</th><th>稀缺性</th><th>板块</th><th>标签</th><th>护城河</th></tr>';
      for (var i = 0; i < d.candidates.length; i++) {
        var c = d.candidates[i];
        h += '<tr>';
        h += '<td><a href="' + B + '/quant/admin/stock?symbol=' + encodeURIComponent(c.code) + '" target="_blank" class="stock-link">' + _esc(c.code) + '</a></td>';
        h += '<td>' + _esc(c.name) + '</td>';
        h += '<td>' + _scBadge(c.scarcityScore) + (c.scarcityScore > 0 ? ' ' + c.scarcityScore.toFixed(1) : ' -') + '</td>';
        h += '<td>' + (c.sectors || []).map(function(s) { return '<span class="badge badge-blue">' + _esc(s) + '</span>'; }).join(" ") + '</td>';
        h += '<td>' + (c.scarcityTags || []).map(function(t) { return '<span class="signal-tag scarcity">' + _esc(t) + '</span>'; }).join("") + '</td>';
        h += '<td style="white-space:normal;max-width:200px;font-size:12px;color:var(--text-dim)">' + _esc(c.moat || "-") + '</td>';
        h += '</tr>';
      }
      h += '</table></div>';
    } else {
      h += '<div style="text-align:center;padding:20px;color:var(--text-dim)">无匹配标的</div>';
    }
    h += '</div>';
    w.document.body.innerHTML = h;
  }).catch(function(e) {
    if (w && !w.closed) w.document.body.innerHTML = '<div class="card" style="color:var(--red)">⚠️ 请求失败: ' + e.message + '</div>';
  });
}

// ═══ Mapping ═══
async function loadMapping() {
  showLoading("map");
  try {
    const themes = document.getElementById("mapThemes").value.trim();
    const data = await apiCall("map_event", {
      us_symbol: document.getElementById("mapSymbol").value,
      themes: themes ? themes.split(",").map(s => s.trim()) : [],
    });
    if (data.error) { showError("map", data.error); return; }
    renderMapping(data);
    showContent("map");
  } catch (e) { showError("map", "请求失败: " + e.message); }
}

function renderMapping(d) {
  const el = document.getElementById("map-content");

  let html = completionBanner("映射完成 — " + esc(d.usSymbol || "") + " → " + (d.candidateCount || 0) + " 只A股");
  html += '<div class="card"><h3>🔗 ' + esc(d.usSymbol) + ' (' + esc(d.usName) + ') → A股对标</h3>';
  html += '<div style="margin-bottom:12px">';
  html += '<span style="font-size:12px;color:var(--text-dim)">主题: </span>';
  html += (d.usThemes || []).map(t => '<span class="badge badge-purple">' + esc(t) + '</span>').join(" ");
  if (d.filterThemes !== "all") {
    html += ' <span style="font-size:12px;color:var(--text-dim)">· ɸѡ: </span>';
    html += (d.filterThemes || []).map(t => '<span class="badge badge-orange">' + esc(t) + '</span>').join(" ");
  }
  html += ' <span style="font-size:12px;color:var(--text-dim)">· 共 ' + (d.candidateCount || 0) + ' 只标的</span>';
  html += '</div>';

  if (d.candidates && d.candidates.length) {
    html += '<div class="table-scroll"><table>';
    html += '<tr><th>代码</th><th>名称</th><th>稀缺性</th><th>板块</th><th>标签</th><th>护城河</th></tr>';
    for (const c of d.candidates) {
      html += '<tr>';
      html += '<td><a href="' + B + '/quant/admin/stock?symbol=' + encodeURIComponent(c.code) + '" target="_blank" class="stock-link">' + esc(c.code) + '</a></td>';
      html += '<td>' + esc(c.name) + '</td>';
      html += '<td>' + scarcityBadge(c.scarcityScore) + (c.scarcityScore > 0 ? ' ' + c.scarcityScore.toFixed(1) : ' -') + '</td>';
      html += '<td>' + (c.sectors || []).map(s => '<span class="badge badge-blue">' + esc(s) + '</span>').join(" ") + '</td>';
      html += '<td>' + (c.scarcityTags || []).map(t => '<span class="signal-tag scarcity">' + esc(t) + '</span>').join("") + '</td>';
      html += '<td style="white-space:normal;max-width:200px;font-size:12px;color:var(--text-dim)">' + esc(c.moat || "-") + '</td>';
      html += '</tr>';
    }
    html += '</table></div>';
  } else {
    html += '<div style="text-align:center;padding:20px;color:var(--text-dim)">无匹配标的</div>';
  }
  html += '</div>';

  el.innerHTML = html;
}

// ═══ Catalyst Picks ═══
async function loadPicks() {
  showLoading("picks");
  try {
    const data = await apiCall("catalyst_picks", {
      min_change_pct: parseFloat(document.getElementById("picksThreshold").value) || 2,
      require_fund_flow: document.getElementById("picksFlowReq").value === "true",
      top_n: parseInt(document.getElementById("picksTopN").value) || 10,
    });
    if (data.error) { showError("picks", data.error); return; }
    renderScan(data);
    document.getElementById("picks-content").innerHTML = document.getElementById("scan-content").innerHTML;
    showContent("picks");
  } catch (e) { showError("picks", "请求失败: " + e.message); }
}

// ═══ Sentiment Signal ═══
async function sentApiCall(action, extra) {
  const body = Object.assign({ action }, extra || {});
  const start = Date.now();
  const r = await fetch(SENT_API, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  const elapsed = Date.now() - start;
  if (elapsed < 600) await new Promise(ok => setTimeout(ok, 600 - elapsed));
  return data;
}

async function loadSentiment() {
  const sym = document.getElementById("sentSymbol").value.trim();
  if (!sym) { alert("请输入股票代码"); return; }
  showLoading("sent");
  try {
    const data = await sentApiCall("analyze", { symbol: sym });
    if (data.error) { showError("sent", data.error); return; }
    renderSentiment(data);
    showContent("sent");
  } catch (e) { showError("sent", "请求失败: " + e.message); }
}

function verdictColor(v) {
  if (v.includes("看多") || v.includes("利好") || v.includes("偏多")) return "var(--green)";
  if (v.includes("看空") || v.includes("利空") || v.includes("偏空")) return "var(--red)";
  return "var(--yellow)";
}
function credTierBadge(tier) {
  const m = { "官方权威": "badge-green", "权威媒体": "badge-blue", "一般媒体": "badge-yellow", "社交媒体": "badge-orange", "传闻/小道消息": "badge-red" };
  return '<span class="badge ' + (m[tier] || 'badge-yellow') + '">' + esc(tier) + '</span>';
}
function substanceBadge(label) {
  const m = { "真金白银": "badge-green", "有进展": "badge-blue", "概念炒作": "badge-red", "信息不足": "badge-yellow" };
  return '<span class="badge ' + (m[label] || 'badge-yellow') + '">' + esc(label) + '</span>';
}
function newsTitle(title, link) {
  const t = esc(title);
  if (link) return '<a href="' + esc(link) + '" target="_blank" rel="noopener" style="color:var(--text)">' + t + '</a>';
  return t;
}
function fmtTime(t) {
  if (!t) return '<span style="color:var(--text-dim)">—</span>';
  // "2026-03-16 17:31:43" → "03-16 17:31"
  const m = String(t).match(/(\\\\d{4})-(\\\\d{2})-(\\\\d{2})\\\\s+(\\\\d{2}):(\\\\d{2})/);
  if (m) return '<span style="color:var(--text-dim)">' + m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5] + '</span>';
  return '<span style="color:var(--text-dim)">' + esc(t) + '</span>';
}

function renderSentiment(d) {
  const el = document.getElementById("sent-content");
  let html = completionBanner("分析完成 — " + esc(d.verdict || "中性"));

  // KPIs
  html += '<div class="kpi-row">';
  html += '<div class="kpi"><div class="label">综合判定</div><div class="value" style="font-size:16px;color:' + verdictColor(d.verdict || '') + '">' + esc(d.verdict || '中性') + '</div><div class="sub">评分 ' + (d.compositeScore || 0).toFixed(3) + '</div></div>';
  const sig = d.signals || {};
  html += '<div class="kpi"><div class="label">利好信号</div><div class="value" style="color:var(--green)">' + (sig.bullishCount || 0) + '</div></div>';
  html += '<div class="kpi"><div class="label">利空信号</div><div class="value" style="color:var(--red)">' + (sig.bearishCount || 0) + '</div></div>';
  html += '<div class="kpi"><div class="label">新闻数</div><div class="value">' + (d.newsCount || 0) + '</div></div>';
  html += '</div>';

  // Position warning
  if (d.positionWarning) {
    html += '<div class="card" style="border-color:var(--yellow)"><h3>⚠️ 市场位置研判</h3>';
    html += '<div style="font-size:15px;font-weight:700;margin-bottom:8px">' + esc(d.positionWarning) + '</div>';
    const pos = d.position || {};
    if (pos.positionLabel) {
      html += '<div style="font-size:13px;color:var(--text-dim)">当前位置: <strong>' + esc(pos.positionLabel) + '</strong>';
      html += ' · 相对位 ' + ((pos.relativePosition || 0) * 100).toFixed(0) + '%';
      html += ' · 距60日高点 -' + (pos.dropFromHigh || 0).toFixed(1) + '%';
      html += ' · 均线: ' + esc(pos.ma_alignment || '') + '</div>';
    }
    const interp = pos.interpretation || {};
    if (interp.bullish_news) {
      html += '<div style="margin-top:8px;padding:8px;background:var(--bg3);border-radius:6px;font-size:12px">';
      html += '<div>🟢 收到利好时: <strong>' + esc(interp.bullish_news.label || '') + '</strong></div>';
      html += '<div style="color:var(--text-dim)">' + esc(interp.bullish_news.advice || '') + '</div></div>';
    }
    if (interp.bearish_news) {
      html += '<div style="margin-top:4px;padding:8px;background:var(--bg3);border-radius:6px;font-size:12px">';
      html += '<div>🔴 收到利空时: <strong>' + esc(interp.bearish_news.label || '') + '</strong></div>';
      html += '<div style="color:var(--text-dim)">' + esc(interp.bearish_news.advice || '') + '</div></div>';
    }
    html += '</div>';
  }

  // Bullish signals
  const bulls = sig.bullishSignals || [];
  if (bulls.length) {
    html += '<div class="card"><h3>🟢 利好信号</h3><div class="table-scroll"><table>';
    html += '<tr><th>标题</th><th>时间</th><th>来源</th><th>可信度</th><th>实质</th><th>类别</th><th>权重</th></tr>';
    for (const b of bulls) {
      html += '<tr><td style="white-space:normal;max-width:280px">' + newsTitle(b.title, b.link) + '</td>';
      html += '<td>' + fmtTime(b.time) + '</td>';
      html += '<td>' + esc(b.source) + '</td>';
      html += '<td>' + credTierBadge(b.credibility?.tier || '') + '</td>';
      html += '<td>' + substanceBadge(b.substance?.label || '') + '</td>';
      html += '<td><span class="badge badge-green">' + esc(b.categoryLabel || '') + '</span></td>';
      html += '<td style="font-weight:700;color:var(--green)">' + (b.effectiveWeight || 0).toFixed(2) + '</td></tr>';
    }
    html += '</table></div></div>';
  }

  // Bearish signals
  const bears = sig.bearishSignals || [];
  if (bears.length) {
    html += '<div class="card"><h3>🔴 利空信号</h3><div class="table-scroll"><table>';
    html += '<tr><th>标题</th><th>时间</th><th>来源</th><th>可信度</th><th>类别</th><th>权重</th></tr>';
    for (const b of bears) {
      html += '<tr><td style="white-space:normal;max-width:280px">' + newsTitle(b.title, b.link) + '</td>';
      html += '<td>' + fmtTime(b.time) + '</td>';
      html += '<td>' + esc(b.source) + '</td>';
      html += '<td>' + credTierBadge(b.credibility?.tier || '') + '</td>';
      html += '<td><span class="badge badge-red">' + esc(b.categoryLabel || '') + '</span></td>';
      html += '<td style="font-weight:700;color:var(--red)">' + (b.effectiveWeight || 0).toFixed(2) + '</td></tr>';
    }
    html += '</table></div></div>';
  }

  // Scored news
  const sn = d.scoredNews || [];
  if (sn.length) {
    html += '<div class="card"><h3>📋 新闻可信度排序</h3><div class="table-scroll"><table>';
    html += '<tr><th>标题</th><th>时间</th><th>来源</th><th>可信度</th><th>等级</th><th>实质</th></tr>';
    for (const n of sn) {
      html += '<tr><td style="white-space:normal;max-width:320px">' + newsTitle(n.title, n.link) + '</td>';
      html += '<td>' + fmtTime(n.time) + '</td>';
      html += '<td>' + esc(n.source) + '</td>';
      html += '<td style="font-weight:700">' + (n.credibility?.score || 0).toFixed(2) + '</td>';
      html += '<td>' + credTierBadge(n.credibility?.tier || '') + '</td>';
      html += '<td>' + substanceBadge(n.substance?.label || '') + '</td></tr>';
    }
    html += '</table></div></div>';
  }

  el.innerHTML = html;
}

// ═══ Event Calendar ═══
function toggleCalSymbolInput() {
  var scope = document.getElementById("calScope").value;
  document.getElementById("calSymbolWrap").style.display = scope === "stock" ? "" : "none";
}

async function calApiCall(action, extra) {
  var body = Object.assign({ action: action }, extra || {});
  var start = Date.now();
  var ctrl = new AbortController();
  var timer = setTimeout(function() { ctrl.abort(); }, 60000);
  try {
    var r = await fetch(CAL_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    var data = await r.json();
    var elapsed = Date.now() - start;
    if (elapsed < 600) await new Promise(function(ok) { setTimeout(ok, 600 - elapsed); });
    return data;
  } catch (e) {
    if (e.name === "AbortError") throw new Error("请求超时，请稍后重试");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

async function loadCalendar() {
  showLoading("cal");
  try {
    var scope = document.getElementById("calScope").value;
    var days = parseInt(document.getElementById("calDays").value) || 30;
    var typeSel = document.getElementById("calTypes");
    var selectedTypes = [];
    for (var i = 0; i < typeSel.options.length; i++) {
      if (typeSel.options[i].selected && typeSel.options[i].value !== "all") {
        selectedTypes.push(typeSel.options[i].value);
      }
    }

    var action = "upcoming_events";
    var extra = { days: days };
    if (selectedTypes.length > 0) extra.event_types = selectedTypes;

    if (scope === "watchlist") {
      action = "watchlist_events";
    } else if (scope === "portfolio") {
      action = "portfolio_events";
    } else if (scope === "stock") {
      var syms = document.getElementById("calSymbols").value.trim();
      if (!syms) { showError("cal", "请输入股票代码"); return; }
      action = "stock_events";
      extra.symbols = syms.split(/[,，\\s]+/).filter(function(s) { return s; });
    }

    var data = await calApiCall(action, extra);
    if (data.error) { showError("cal", data.error); return; }
    renderCalendar(data, scope);
    showContent("cal");
  } catch (e) { showError("cal", "请求失败: " + e.message); }
}

function eventTypeBadge(type) {
  var m = {
    "unlock": { cls: "badge-red", icon: "🔓" },
    "dividend": { cls: "badge-green", icon: "💰" },
    "shareholder_meeting": { cls: "badge-blue", icon: "🏛️" },
    "insider_increase": { cls: "badge-green", icon: "📈" },
    "insider_decrease": { cls: "badge-red", icon: "📉" },
    "earnings": { cls: "badge-yellow", icon: "📋" },
    "repurchase": { cls: "badge-green", icon: "🔄" },
    "allotment": { cls: "badge-yellow", icon: "📑" },
    "placement": { cls: "badge-blue", icon: "💼" },
  };
  var info = m[type] || { cls: "badge-yellow", icon: "📌" };
  return info;
}

function impactBadge(impact) {
  if (impact === "positive") return '<span class="badge badge-green">利好</span>';
  if (impact === "negative") return '<span class="badge badge-red">利空</span>';
  return '<span class="badge badge-yellow">中性</span>';
}

function renderCalendar(d, scope) {
  var el = document.getElementById("cal-content");
  var scopeLabel = { upcoming: "全市场", watchlist: "自选股", portfolio: "持仓股", stock: "指定股票" };
  var html = completionBanner("事件日历已加载 — " + (d.totalEvents || 0) + " 个事件 (" + (scopeLabel[scope] || scope) + ")");

  // KPI row
  html += '<div class="kpi-row">';
  html += '<div class="kpi"><div class="label">事件总数</div><div class="value" style="color:var(--accent)">' + (d.totalEvents || 0) + '</div></div>';
  if (d.alertCount > 0) {
    html += '<div class="kpi"><div class="label">预警</div><div class="value" style="color:var(--red)">' + d.alertCount + '</div></div>';
  }
  if (d.watchlistCount) {
    html += '<div class="kpi"><div class="label">自选股数</div><div class="value">' + d.watchlistCount + '</div></div>';
  }
  if (d.holdingCount) {
    html += '<div class="kpi"><div class="label">持仓股数</div><div class="value">' + d.holdingCount + '</div></div>';
  }
  // Type counts
  var tc = d.typeCounts || {};
  for (var tkey in tc) {
    var ti = eventTypeBadge(tkey);
    html += '<div class="kpi"><div class="label">' + ti.icon + ' ' + esc(tkey) + '</div><div class="value">' + tc[tkey] + '</div></div>';
  }
  html += '</div>';

  // Alerts section
  var alerts = d.alerts || [];
  if (alerts.length) {
    html += '<div class="card" style="border-color:var(--red)">';
    html += '<h3>⚠️ 事件预警 (' + alerts.length + ')</h3>';
    for (var ai = 0; ai < alerts.length; ai++) {
      var a = alerts[ai];
      var alertColor = a.level === "danger" ? "var(--red)" : a.level === "warning" ? "var(--yellow)" : a.level === "positive" ? "var(--green)" : "var(--text-dim)";
      html += '<div style="padding:8px 12px;margin-bottom:6px;background:var(--bg3);border-radius:6px;border-left:3px solid ' + alertColor + ';font-size:13px">';
      html += '<a href="' + B + '/quant/admin/stock?symbol=' + encodeURIComponent(a.symbol) + '" target="_blank" class="stock-link" style="font-weight:700">' + esc(a.symbol) + '</a> ';
      html += esc(a.message);
      html += '</div>';
    }
    html += '</div>';
  }

  // Timeline view
  var events = d.events || [];
  if (events.length) {
    html += '<div class="card"><h3>📅 事件时间线</h3>';
    html += '<div class="timeline-container">';

    var currentDate = "";
    var today = (function(){var d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')})();
    for (var ei = 0; ei < events.length; ei++) {
      var ev = events[ei];
      if (ev.date !== currentDate) {
        if (currentDate) html += '</div>'; // close previous day
        currentDate = ev.date;
        var isToday = ev.date === today;
        var dayLabel = isToday ? "今天" : ev.date;
        var isPast = ev.date < today;
        html += '<div class="timeline-day" style="' + (isToday ? 'border-left-color:var(--accent)' : isPast ? 'border-left-color:var(--text-dim)' : '') + '">';
        html += '<div class="timeline-date' + (isToday ? ' today' : '') + '">' + esc(dayLabel) + '</div>';
      }

      var typeInfo = eventTypeBadge(ev.type);
      html += '<div class="timeline-event">';
      html += '<span class="badge ' + typeInfo.cls + '">' + typeInfo.icon + ' ' + esc(ev.typeLabel || ev.type) + '</span> ';
      html += '<a href="' + B + '/quant/admin/stock?symbol=' + encodeURIComponent(ev.symbol) + '" target="_blank" class="stock-link">' + esc(ev.symbol) + '</a> ';
      html += '<strong>' + esc(ev.name) + '</strong> ';
      html += '<span style="color:var(--text-dim);font-size:12px">' + esc(ev.summary) + '</span> ';
      html += impactBadge(ev.impact);
      html += '</div>';
    }
    if (currentDate) html += '</div>'; // close last day
    html += '</div></div>';
  }

  // Table view
  if (events.length) {
    html += '<div class="card"><h3>📋 事件列表</h3><div class="table-scroll"><table>';
    html += '<tr><th>日期</th><th>类型</th><th>代码</th><th>名称</th><th>ժҪ</th><th>影响</th></tr>';
    for (var ti2 = 0; ti2 < events.length; ti2++) {
      var e = events[ti2];
      var typeI = eventTypeBadge(e.type);
      html += '<tr>';
      html += '<td>' + esc(e.date) + '</td>';
      html += '<td><span class="badge ' + typeI.cls + '">' + typeI.icon + ' ' + esc(e.typeLabel || e.type) + '</span></td>';
      html += '<td><a href="' + B + '/quant/admin/stock?symbol=' + encodeURIComponent(e.symbol) + '" target="_blank" class="stock-link">' + esc(e.symbol) + '</a></td>';
      html += '<td>' + esc(e.name) + '</td>';
      html += '<td style="white-space:normal;max-width:300px;font-size:12px">' + esc(e.summary) + '</td>';
      html += '<td>' + impactBadge(e.impact) + '</td>';
      html += '</tr>';
    }
    html += '</table></div></div>';
  }

  if (!events.length && !alerts.length) {
    html += '<div class="empty-state" style="border:1px solid var(--border);border-radius:8px;padding:24px"><div class="icon">😴</div><h3>查询范围内无事件</h3></div>';
  }

  el.innerHTML = html;
}

// ═══ Tech Frontier ═══
async function loadFrontier() {
  showLoading("frontier");
  try {
    const data = await sentApiCall("tech_frontier", {});
    if (data.error) { showError("frontier", data.error); return; }
    renderFrontier(data);
    showContent("frontier");
  } catch (e) { showError("frontier", "请求失败: " + e.message); }
}

function renderFrontier(d) {
  const el = document.getElementById("frontier-content");
  let html = completionBanner("前沿数据已加载 — " + (d.frontierCount || 0) + " 个方向");
  html += '<div class="kpi-row">';
  html += '<div class="kpi"><div class="label">前沿方向</div><div class="value" style="color:var(--purple)">' + (d.frontierCount || 0) + '</div></div>';
  if (d.detectedCount > 0) {
    html += '<div class="kpi"><div class="label">新闻命中</div><div class="value" style="color:var(--red)">' + d.detectedCount + '</div></div>';
  }
  html += '</div>';

  // Detected from news
  if (d.detected && d.detected.length) {
    html += '<div class="card" style="border-color:var(--red)"><h3>🔥 新闻命中前沿</h3><div class="table-scroll"><table>';
    html += '<tr><th>方向</th><th>新闻</th><th>来源</th><th>可信度</th><th>实质</th><th>A股标的</th></tr>';
    for (const f of d.detected) {
      html += '<tr><td><span class="badge badge-purple">' + esc(f.frontier) + '</span></td>';
      html += '<td style="white-space:normal;max-width:250px">' + esc(f.newsTitle) + '</td>';
      html += '<td>' + esc(f.source) + '</td>';
      html += '<td>' + credTierBadge(f.credibility?.tier || '') + '</td>';
      html += '<td>' + substanceBadge(f.substance?.label || '') + '</td>';
      html += '<td>' + (f.aShares || []).map(c => '<a href="' + B + '/quant/admin/stock?symbol=' + c + '" target="_blank" class="stock-link">' + c + '</a>').join(' ') + '</td></tr>';
    }
    html += '</table></div></div>';
  }

  // Full frontier database
  html += '<div class="cards-row">';
  for (const f of (d.database || [])) {
    html += '<div class="card"><h3>🔬 ' + esc(f.name) + '</h3>';
    html += '<div style="margin-bottom:8px">' + (f.keywords || []).map(k => '<span class="badge badge-purple" style="margin:2px">' + esc(k) + '</span>').join('') + '</div>';
    html += '<div style="font-size:12px;color:var(--text-dim);margin-bottom:8px">催化剂: ' + (f.catalysts || []).map(c => esc(c)).join(' · ') + '</div>';
    html += '<div>A股标的: ' + (f.aShares || []).map(c => '<a href="' + B + '/quant/admin/stock?symbol=' + c + '" target="_blank" class="stock-link" style="margin-right:8px">' + c + '</a>').join('') + '</div>';
    html += '</div>';
  }
  html += '</div>';

  el.innerHTML = html;
}
`;
}

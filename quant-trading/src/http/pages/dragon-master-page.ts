/**
 * Dragon Master Page — self-contained SPA for 真龙战法 (True Dragon Strategy) analysis.
 *
 * Tabs: 主线总览 (theme overview), 龙头排名 (dragon ranking), 打板信号 (board signals)
 * Data fetched from `/quant/admin/api/dragon-master?action=full|themes|dragons|timing`.
 */

/** Return the full HTML for the dragon master analysis page. */
export function getDragonMasterPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>真龙战法 — OpenClaw 量化</title>
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
.cards-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;margin-bottom:16px}
.card .table-scroll{overflow-x:auto;margin:0 -16px;padding:0 16px}
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;text-align:center;min-width:110px}
.kpi .label{font-size:11px;color:var(--text-dim);margin-bottom:4px}
.kpi .value{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .sub{font-size:11px;color:var(--text-dim);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--text-dim);font-weight:600;font-size:12px;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover{background:var(--bg3)}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}
.badge-accent{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-orange{background:rgba(219,109,40,.15);color:var(--orange)}
.dragon-1{color:var(--red);font-weight:700}
.dragon-2{color:var(--orange);font-weight:600}
.dragon-3{color:var(--yellow);font-weight:600}
.best-timing{background:rgba(63,185,80,.12);border:1px solid rgba(63,185,80,.3);border-radius:4px;padding:2px 6px}
.loading{text-align:center;padding:40px;color:var(--text-dim)}
.error{color:var(--red);padding:16px;background:rgba(248,81,73,.1);border-radius:8px;margin-bottom:16px}
.summary-box{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:14px;line-height:1.6}
.rule-box{background:var(--bg2);border-left:3px solid var(--accent);padding:10px 14px;margin-bottom:8px;font-size:12px;color:var(--text-dim)}
.rule-box strong{color:var(--text)}
.btn{padding:6px 16px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:rgba(88,166,255,.15);border-color:var(--accent);color:var(--accent)}
</style>
</head>
<body>
<header>
  <h1>🐉 真龙战法</h1>
  <div class="header-right">
    <button class="btn btn-primary" onclick="loadData()">刷新</button>
    <a href="${b}/quant/admin/lianban">连板分析</a>
    <a href="${b}/quant/admin/planner">投资规划</a>
  </div>
</header>
<div class="breadcrumb"><a href="${b}/quant/admin">首页</a> / 真龙战法</div>

<div class="tabs">
  <div class="tab active" data-tab="overview">主线总览</div>
  <div class="tab" data-tab="dragons">龙头排名</div>
  <div class="tab" data-tab="signals">打板信号</div>
  <div class="tab" data-tab="rules">战法口诀</div>
</div>

<main>
  <!-- Tab 1: 主线总览 -->
  <div class="tab-content active" id="tab-overview">
    <div id="summary-area"></div>
    <div id="kpi-area"></div>
    <div id="themes-area"><div class="loading">加载中...</div></div>
  </div>

  <!-- Tab 2: 龙头排名 -->
  <div class="tab-content" id="tab-dragons">
    <div id="dragons-area"><div class="loading">加载中...</div></div>
  </div>

  <!-- Tab 3: 打板信号 -->
  <div class="tab-content" id="tab-signals">
    <div id="signals-area"><div class="loading">加载中...</div></div>
  </div>

  <!-- Tab 4: 战法口诀 -->
  <div class="tab-content" id="tab-rules">
    <div class="card">
      <h3>📖 真龙战法 · 六步口诀</h3>
      <div class="rule-box"><strong>第一步：确认大主线</strong> — 板块涨停≥10只，连续2天，有2连板+高度 = 超级大主线</div>
      <div class="rule-box"><strong>第二步：确认龙一</strong> — 板块内最早涨停、封单最大、最稳不开板 = 龙一。第二天最早2连板封住 = 100%龙一</div>
      <div class="rule-box"><strong>第三步：何时买？(最关键)</strong> — 1板不确定龙一→不买；2板确认但秒板/一字→99%买不进；<span class="best-timing">3板打板 = 最佳买点！</span></div>
      <div class="rule-box"><strong>第四步：怎么买？(唯一正确)</strong> — 只做涨停打板买！等冲到涨停价→等封单稳→挂涨停价买入。不涨停=不确定强→不买</div>
      <div class="rule-box"><strong>第五步：买不到怎么办？</strong> — 龙一买不到→做同主线龙二；龙二买不到→做龙三；<span style="color:var(--red)">龙四以后全部不看</span></div>
      <div class="rule-box"><strong>第六步：万能口诀</strong> — 主线看两天，涨停要十只；龙一看最先，二板定真龙；一板不买，二板难买；<strong style="color:var(--green)">三板打板，最稳最安全</strong></div>
    </div>
  </div>
</main>

<script>
const API = '${b}/quant/admin/api/dragon-master';
let DATA = null;

// Tab switching
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('tab-' + t.dataset.tab)?.classList.add('active');
  });
});

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function fmt(n, d=1) { return typeof n === 'number' ? n.toFixed(d) : String(n ?? '-'); }

function dragonBadge(rank) {
  if (rank === 1) return '<span class="badge badge-red">龙一</span>';
  if (rank === 2) return '<span class="badge badge-orange">龙二</span>';
  if (rank === 3) return '<span class="badge badge-yellow">龙三</span>';
  return '<span class="badge">龙' + rank + '</span>';
}

function riskBadge(level) {
  if (level === 'low') return '<span class="badge badge-green">低风险</span>';
  if (level === 'medium') return '<span class="badge badge-yellow">中风险</span>';
  return '<span class="badge badge-red">高风险</span>';
}

function actionBadge(action) {
  if (action === 'buy') return '<span class="badge badge-green">买入</span>';
  if (action === 'wait') return '<span class="badge badge-yellow">等待</span>';
  return '<span class="badge badge-red">跳过</span>';
}

async function loadData() {
  try {
    document.getElementById('themes-area').innerHTML = '<div class="loading">加载中...</div>';
    const resp = await fetch(API + '?action=full&top=5');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    DATA = await resp.json();
    if (DATA.error) throw new Error(DATA.error);
    renderAll();
  } catch (e) {
    document.getElementById('themes-area').innerHTML = '<div class="error">加载失败: ' + esc(e.message) + '</div>';
  }
}

function renderAll() {
  if (!DATA) return;
  renderSummary();
  renderKPIs();
  renderThemes();
  renderDragons();
  renderSignals();
}

function renderSummary() {
  const el = document.getElementById('summary-area');
  el.innerHTML = '<div class="summary-box">' + esc(DATA.summary || '') + '</div>';
}

function renderKPIs() {
  const m = DATA.market_summary || {};
  const themes = DATA.themes || [];
  const picks = DATA.top_picks || [];
  const superCount = m.super_theme_count || 0;

  document.getElementById('kpi-area').innerHTML = \`
    <div class="kpi-row">
      <div class="kpi"><div class="label">今日涨停</div><div class="value">\${m.total_zt_today || 0}</div></div>
      <div class="kpi"><div class="label">昨日涨停</div><div class="value">\${m.total_zt_yesterday || 0}</div></div>
      <div class="kpi"><div class="label">超级主线</div><div class="value" style="color:\${superCount > 0 ? 'var(--green)' : 'var(--text-dim)'}">\${superCount}</div></div>
      <div class="kpi"><div class="label">分析主线</div><div class="value">\${themes.length}</div></div>
      <div class="kpi"><div class="label">打板信号</div><div class="value" style="color:\${picks.length > 0 ? 'var(--green)' : 'var(--text-dim)'}">\${picks.length}</div></div>
    </div>
  \`;
}

function renderThemes() {
  const themes = DATA.themes || [];
  if (!themes.length) {
    document.getElementById('themes-area').innerHTML = '<div class="card"><p style="color:var(--text-dim)">暂无明确主线，市场分散</p></div>';
    return;
  }

  let html = '';
  for (const t of themes) {
    const superTag = t.is_super_theme ? '<span class="badge badge-green">超级主线</span>' : '<span class="badge badge-accent">潜力主线</span>';
    const dragons = (t.dragons || []).slice(0, 3);

    html += '<div class="card">';
    html += '<h3>' + superTag + ' ' + esc(t.sector) + ' <span style="color:var(--text-dim);font-size:12px;font-weight:400">评分 ' + fmt(t.theme_score) + ' | 涨停 ' + t.today_zt_count + 'ֻ | 最高' + t.max_board_height + '连板</span></h3>';

    if (dragons.length) {
      html += '<div class="table-scroll"><table><tr><th>排名</th><th>代码</th><th>名称</th><th>龙头评分</th><th>连板</th><th>首封时间</th><th>封单(亿)</th><th>开板次数</th><th>信号</th></tr>';
      for (const d of dragons) {
        const f = d.factors || {};
        html += '<tr>';
        html += '<td>' + dragonBadge(d.dragon_rank) + '</td>';
        html += '<td>' + esc(d.code) + '</td>';
        html += '<td>' + esc(d.name) + '</td>';
        html += '<td style="font-weight:600">' + fmt(d.dragon_score) + '</td>';
        html += '<td>' + (f.lian_ban_count || '-') + '板</td>';
        html += '<td>' + esc(f.first_board_time || '-') + '</td>';
        html += '<td>' + fmt(f.seal_amount_yi, 2) + '</td>';
        html += '<td>' + (f.open_count || 0) + '</td>';
        html += '<td style="font-size:11px">' + (d.signals || []).map(s => esc(s)).join(', ') + '</td>';
        html += '</tr>';
      }
      html += '</table></div>';
    }

    // Fallback recommendation
    const fb = t.fallback || {};
    if (fb.recommendation) {
      html += '<div style="margin-top:8px;padding:8px 12px;background:var(--bg3);border-radius:6px;font-size:12px">';
      html += '<strong>推荐:</strong> ' + esc(fb.recommendation);
      html += '</div>';
    }
    html += '</div>';
  }
  document.getElementById('themes-area').innerHTML = html;
}

function renderDragons() {
  const themes = DATA.themes || [];
  let allDragons = [];
  for (const t of themes) {
    for (const d of (t.dragons || [])) {
      allDragons.push({...d, sector: t.sector, theme_score: t.theme_score});
    }
  }
  allDragons.sort((a, b) => b.dragon_score - a.dragon_score);

  if (!allDragons.length) {
    document.getElementById('dragons-area').innerHTML = '<div class="card"><p style="color:var(--text-dim)">无龙头数据</p></div>';
    return;
  }

  let html = '<div class="card"><h3>🐉 全市场龙头排名</h3><div class="table-scroll"><table>';
  html += '<tr><th>#</th><th>龙头</th><th>代码</th><th>名称</th><th>板块</th><th>评分</th><th>连板</th><th>首封</th><th>封单(亿)</th><th>稳定性</th><th>Day2确认</th></tr>';

  allDragons.forEach((d, i) => {
    const f = d.factors || {};
    html += '<tr>';
    html += '<td>' + (i + 1) + '</td>';
    html += '<td>' + dragonBadge(d.dragon_rank) + '</td>';
    html += '<td>' + esc(d.code) + '</td>';
    html += '<td>' + esc(d.name) + '</td>';
    html += '<td><span class="badge badge-accent">' + esc(d.sector) + '</span></td>';
    html += '<td style="font-weight:700">' + fmt(d.dragon_score) + '</td>';
    html += '<td>' + (f.lian_ban_count || '-') + '板</td>';
    html += '<td>' + esc(f.first_board_time || '-') + '</td>';
    html += '<td>' + fmt(f.seal_amount_yi, 2) + '</td>';
    html += '<td>' + (f.open_count === 0 ? '<span class="badge badge-green">全天未开板</span>' : f.open_count + '次开板') + '</td>';
    html += '<td>' + (f.day2_confirmed ? '<span class="badge badge-green">已确认</span>' : '-') + '</td>';
    html += '</tr>';
  });
  html += '</table></div></div>';
  document.getElementById('dragons-area').innerHTML = html;
}

function renderSignals() {
  const themes = DATA.themes || [];
  let allSignals = [];
  for (const t of themes) {
    for (const s of (t.buy_signals || [])) {
      allSignals.push({...s, sector: t.sector});
    }
  }

  // Board signals
  let allBoard = [];
  for (const t of themes) {
    for (const bs of (t.board_signals || [])) {
      allBoard.push({...bs, sector: t.sector});
    }
  }

  if (!allSignals.length && !allBoard.length) {
    document.getElementById('signals-area').innerHTML = '<div class="card"><p style="color:var(--text-dim)">暂无打板信号</p></div>';
    return;
  }

  let html = '';

  // Buy timing signals
  if (allSignals.length) {
    html += '<div class="card"><h3>⏰ 买入时机分析</h3><div class="table-scroll"><table>';
    html += '<tr><th>龙头</th><th>代码</th><th>名称</th><th>板块</th><th>板数</th><th>动作</th><th>最佳时机</th><th>置信度</th><th>量能</th><th>风险</th><th>理由</th></tr>';
    for (const s of allSignals) {
      const isBest = s.is_best_timing;
      html += '<tr' + (isBest ? ' style="background:rgba(63,185,80,.06)"' : '') + '>';
      html += '<td>' + dragonBadge(s.dragon_rank) + '</td>';
      html += '<td>' + esc(s.code) + '</td>';
      html += '<td>' + esc(s.name) + '</td>';
      html += '<td>' + esc(s.sector) + '</td>';
      html += '<td>' + s.board_number + '板</td>';
      html += '<td>' + actionBadge(s.action) + '</td>';
      html += '<td>' + (isBest ? '<span class="best-timing">★ 最佳</span>' : '-') + '</td>';
      html += '<td style="font-weight:600">' + fmt(s.confidence, 0) + '%</td>';
      html += '<td>' + esc(s.volume_signal || '-') + '</td>';
      html += '<td>' + riskBadge(s.risk_level) + '</td>';
      html += '<td style="font-size:11px;max-width:200px;white-space:normal">' + esc(s.reason || '') + '</td>';
      html += '</tr>';
    }
    html += '</table></div></div>';
  }

  // Board buy signals (封单分析)
  if (allBoard.length) {
    html += '<div class="card"><h3>🔒 封单打板信号</h3><div class="table-scroll"><table>';
    html += '<tr><th>代码</th><th>名称</th><th>板块</th><th>状态</th><th>封单强度</th><th>封单(亿)</th><th>建议</th></tr>';
    for (const bs of allBoard) {
      html += '<tr>';
      html += '<td>' + esc(bs.code) + '</td>';
      html += '<td>' + esc(bs.name) + '</td>';
      html += '<td>' + esc(bs.sector) + '</td>';
      html += '<td>' + (bs.ready ? '<span class="badge badge-green">可打板</span>' : '<span class="badge badge-yellow">' + esc(bs.signal) + '</span>') + '</td>';
      html += '<td>' + esc(bs.seal_strength || '-') + '</td>';
      html += '<td>' + fmt(bs.seal_amount_yi, 2) + '</td>';
      html += '<td style="font-size:11px;max-width:250px;white-space:normal">' + esc(bs.advice || '') + '</td>';
      html += '</tr>';
    }
    html += '</table></div></div>';
  }

  // Top picks summary
  const picks = DATA.top_picks || [];
  if (picks.length) {
    html += '<div class="card"><h3>🎯 首选标的</h3>';
    for (const p of picks) {
      const isBest = p.is_best_timing;
      html += '<div style="padding:8px 12px;background:var(--bg3);border-radius:6px;margin-bottom:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">';
      html += dragonBadge(p.dragon_rank);
      html += '<strong>' + esc(p.code) + ' ' + esc(p.name) + '</strong>';
      html += '<span class="badge badge-accent">' + esc(p.sector) + '</span>';
      html += '<span>' + p.board_number + '板</span>';
      if (isBest) html += '<span class="best-timing">★ 最佳时机</span>';
      html += '<span>置信度 ' + fmt(p.confidence, 0) + '%</span>';
      html += riskBadge(p.risk_level);
      html += '</div>';
    }
    html += '</div>';
  }

  document.getElementById('signals-area').innerHTML = html;
}

// Init
loadData();
</script>
</body>
</html>`;
}

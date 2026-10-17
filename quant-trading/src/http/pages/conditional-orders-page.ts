/**
 * Conditional Orders Monitoring Page — 条件单监控看板
 *
 * Displays all conditional orders grouped by stock symbol with:
 *  - Per-stock collapsible cards showing all orders
 *  - Status badges (active / triggered)
 *  - Delete individual orders
 *  - Manual trigger check
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

export function getConditionalOrdersPageHtml(): string {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quant Trading 条件单监控</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📡</text></svg>">
<style>
${THEME_CSS_VARS}
${BASE_CSS_RESET}
${HEADER_CSS}
${TOAST_CSS}
${KPI_CSS}
.container{max-width:1400px;margin:0 auto;padding:20px 24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:12px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:6px;font-size:13px;cursor:pointer;border:1px solid var(--border);background:var(--bg3);color:var(--text);transition:all .15s}
.btn:hover{background:var(--border)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-primary:hover{opacity:.85}
.btn-danger{background:var(--red);color:#fff;border-color:var(--red)}
.btn-sm{padding:4px 10px;font-size:12px}
.btn-xs{padding:2px 8px;font-size:11px}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--border)}
th{font-weight:600;color:var(--text-dim);font-size:11px;text-transform:uppercase}
.stock-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;margin-bottom:8px;overflow:hidden}
.stock-header{display:flex;align-items:center;gap:12px;padding:12px 16px;cursor:pointer;user-select:none}
.stock-header:hover{background:var(--bg3)}
.stock-header .symbol{font-family:monospace;color:var(--accent);font-weight:600;font-size:14px}
.stock-header .name{color:var(--text);font-size:13px}
.stock-header .count{margin-left:auto;display:flex;gap:6px;align-items:center}
.badge{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500}
.badge-active{background:rgba(34,197,94,0.15);color:#22c55e}
.badge-triggered{background:rgba(239,68,68,0.15);color:#ef4444}
.badge-type{background:var(--bg3);color:var(--text-dim)}
.stock-body{padding:0 16px 12px;display:none}
.stock-body.open{display:block}
.order-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);font-size:12px}
.order-row:last-child{border-bottom:none}
.order-type{width:90px;flex-shrink:0}
.order-desc{flex:1;color:var(--text-dim)}
.order-status{width:60px;text-align:center;flex-shrink:0}
.order-actions{width:60px;text-align:right;flex-shrink:0}
.type-stop_loss{color:#ef4444}
.type-take_profit{color:#22c55e}
.type-staged_tp{color:#22c55e}
.type-trailing_stop{color:#f59e0b}
.type-trailing_atr{color:#f59e0b}
.type-trailing_tp{color:#f59e0b}
.type-indicator_stop{color:#a78bfa}
.type-swing_grid{color:#60a5fa}
.triggered-row{opacity:0.5}
.filter-bar{display:flex;gap:8px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
.filter-bar select{padding:4px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:12px}
.filter-bar input[type=text]{padding:4px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:12px;width:160px}
.expand-icon{transition:transform .2s;font-size:12px;color:var(--text-dim)}
.expand-icon.open{transform:rotate(90deg)}
.empty-state{text-align:center;padding:48px 20px;color:var(--text-dim)}
.empty-state .icon{font-size:48px;margin-bottom:12px}
</style>
</head>
<body>
<div class="container">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap">
    <h2 style="margin:0">📡 条件单监控</h2>
    <div style="margin-left:auto;display:flex;gap:8px">
      <button class="btn btn-sm" onclick="checkNow()">⚡ 立即检查</button>
      <button class="btn btn-sm" onclick="toggleHistory()">📋 历史记录</button>
      <button class="btn btn-sm" onclick="loadOrders()">🔄 刷新</button>
    </div>
  </div>

  <div id="kpi-area" style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px"></div>

  <div class="filter-bar">
    <input type="text" id="filter-symbol" placeholder="搜索代码/名称..." oninput="applyFilter()">
    <select id="filter-type" onchange="applyFilter()">
      <option value="">全部类型</option>
      <option value="stop_loss">止损</option>
      <option value="take_profit">止盈</option>
      <option value="staged_tp">分批止盈</option>
      <option value="trailing_stop">跟踪止损</option>
      <option value="trailing_atr">ATR跟踪</option>
      <option value="swing_grid">波段网格</option>
      <option value="indicator_stop">指标止损</option>
    </select>
    <select id="filter-status" onchange="applyFilter()">
      <option value="">全部状态</option>
      <option value="active">活跃</option>
      <option value="triggered">已触发</option>
    </select>
    <select id="filter-source" onchange="applyFilter()">
      <option value="">全部来源</option>
      <option value="combo">组合优化</option>
      <option value="manual">手动创建</option>
      <option value="swing">波段网格</option>
    </select>
    <button class="btn btn-sm" onclick="expandAll(true)">展开全部</button>
    <button class="btn btn-sm" onclick="expandAll(false)">收起全部</button>
  </div>

  <div id="orders-area"></div>
  <div id="history-area" style="display:none"></div>
</div>

<script>
${SHARED_JS_HELPERS}

function showToast(msg, type) {
  var existing = document.querySelector('.toast');
  if (existing) existing.remove();
  var t = document.createElement('div');
  t.className = 'toast toast-' + (type||'info');
  t.textContent = msg;
  t.style.cssText = 'position:fixed;top:24px;right:24px;padding:10px 20px;border-radius:8px;color:#fff;font-size:13px;z-index:9999;animation:fadeIn .2s';
  t.style.background = type==='error'?'#dc2626':type==='warn'?'#d97706':type==='ok'?'#16a34a':'#2563eb';
  document.body.appendChild(t);
  setTimeout(function(){ t.remove(); }, 4000);
}

const API_BASE = '/quant/admin/api/conditional-orders';
let allOrders = [];
let allHistory = [];
let showingHistory = false;

const TYPE_LABELS = {
  stop_loss: '🔴 止损',
  take_profit: '🟢 止盈',
  staged_tp: '🟢 分批止盈',
  trailing_stop: '🟡 跟踪止损',
  trailing_atr: '🟡 ATR跟踪',
  trailing_tp: '🟡 跟踪止盈',
  indicator_stop: '🟣 指标止损',
  swing_grid: '🔵 波段网格',
};

async function loadOrders() {
  try {
    const resp = await fetch(API_BASE + '?active=false');
    const data = await resp.json();
    allOrders = data.orders || [];
    renderKPI();
    applyFilter();
  } catch (err) {
    showToast('加载失败: ' + err.message, 'error');
  }
}

function renderKPI() {
  const active = allOrders.filter(function(o) { return !o.triggered; });
  const triggered = allOrders.filter(function(o) { return o.triggered; });
  const symbols = new Set(allOrders.map(function(o) { return o.symbol; }));
  const activeSymbols = new Set(active.map(function(o) { return o.symbol; }));

  // Count by source
  var comboCount = allOrders.filter(function(o) { return (o.strategyId || '').indexOf('combo') >= 0; }).length;

  document.getElementById('kpi-area').innerHTML =
    '<div class="kpi"><div class="kpi-value">' + allOrders.length + '</div><div class="kpi-label">总条件单</div></div>'
    + '<div class="kpi"><div class="kpi-value metric-good">' + active.length + '</div><div class="kpi-label">活跃监控</div></div>'
    + '<div class="kpi"><div class="kpi-value metric-bad">' + triggered.length + '</div><div class="kpi-label">已触发</div></div>'
    + '<div class="kpi"><div class="kpi-value">' + symbols.size + '</div><div class="kpi-label">监控股票</div></div>'
    + '<div class="kpi"><div class="kpi-value">' + comboCount + '</div><div class="kpi-label">组合优化单</div></div>';
}

function applyFilter() {
  var keyword = (document.getElementById('filter-symbol').value || '').toLowerCase();
  var typeFilter = document.getElementById('filter-type').value;
  var statusFilter = document.getElementById('filter-status').value;
  var sourceFilter = document.getElementById('filter-source').value;

  var filtered = allOrders.filter(function(o) {
    if (keyword && o.symbol.toLowerCase().indexOf(keyword) < 0 && (o.name || '').toLowerCase().indexOf(keyword) < 0) return false;
    if (typeFilter && o.type !== typeFilter) return false;
    if (statusFilter === 'active' && o.triggered) return false;
    if (statusFilter === 'triggered' && !o.triggered) return false;
    if (sourceFilter === 'combo' && (o.strategyId || '').indexOf('combo') < 0) return false;
    if (sourceFilter === 'manual' && (o.strategyId || '').indexOf('manual') < 0) return false;
    if (sourceFilter === 'swing' && o.type !== 'swing_grid') return false;
    return true;
  });

  renderGroupedOrders(filtered);
}

function renderGroupedOrders(orders) {
  var groups = {};
  var symbolOrder = [];
  for (var i = 0; i < orders.length; i++) {
    var o = orders[i];
    if (!groups[o.symbol]) {
      groups[o.symbol] = { symbol: o.symbol, name: o.name || '', orders: [] };
      symbolOrder.push(o.symbol);
    }
    groups[o.symbol].orders.push(o);
  }

  if (!symbolOrder.length) {
    document.getElementById('orders-area').innerHTML = '<div class="empty-state"><div class="icon">📡</div><div>暂无条件单</div></div>';
    return;
  }

  var html = '';
  for (var si = 0; si < symbolOrder.length; si++) {
    var sym = symbolOrder[si];
    var g = groups[sym];
    var activeCount = g.orders.filter(function(o) { return !o.triggered; }).length;
    var triggeredCount = g.orders.length - activeCount;

    html += '<div class="stock-card">';
    html += '<div class="stock-header" onclick="toggleStock(this)">';
    html += '<span class="expand-icon">▶</span>';
    html += '<span class="symbol">' + g.symbol + '</span>';
    html += '<span class="name">' + g.name + '</span>';
    html += '<div class="count">';
    if (activeCount > 0) html += '<span class="badge badge-active">活跃 ' + activeCount + '</span>';
    if (triggeredCount > 0) html += '<span class="badge badge-triggered">已触发 ' + triggeredCount + '</span>';
    html += '<button class="btn btn-danger btn-xs" onclick="event.stopPropagation();deleteSymbolOrders(\\'' + g.symbol + '\\')" title="删除该股票全部条件单">🗑</button>';
    html += '</div></div>';

    html += '<div class="stock-body">';
    // Group orders by category
    var cats = categorizeOrders(g.orders);

    if (cats.stopLoss.length) {
      html += '<div style="margin-bottom:8px"><div style="font-size:11px;color:var(--text-dim);margin-bottom:4px;font-weight:600">🛡️ 止损保护</div>';
      for (var j = 0; j < cats.stopLoss.length; j++) html += renderOrderRow(cats.stopLoss[j]);
      html += '</div>';
    }
    if (cats.takeProfit.length) {
      html += '<div style="margin-bottom:8px"><div style="font-size:11px;color:var(--text-dim);margin-bottom:4px;font-weight:600">🎯 止盈目标</div>';
      for (var j = 0; j < cats.takeProfit.length; j++) html += renderOrderRow(cats.takeProfit[j]);
      html += '</div>';
    }
    if (cats.buyAlerts.length) {
      html += '<div style="margin-bottom:8px"><div style="font-size:11px;color:var(--text-dim);margin-bottom:4px;font-weight:600">📗 买入提醒</div>';
      for (var j = 0; j < cats.buyAlerts.length; j++) html += renderOrderRow(cats.buyAlerts[j]);
      html += '</div>';
    }
    if (cats.sellAlerts.length) {
      html += '<div style="margin-bottom:8px"><div style="font-size:11px;color:var(--text-dim);margin-bottom:4px;font-weight:600">📕 卖出提醒</div>';
      for (var j = 0; j < cats.sellAlerts.length; j++) html += renderOrderRow(cats.sellAlerts[j]);
      html += '</div>';
    }
    if (cats.grid.length) {
      html += '<div style="margin-bottom:8px"><div style="font-size:11px;color:var(--text-dim);margin-bottom:4px;font-weight:600">🔵 波段网格</div>';
      for (var j = 0; j < cats.grid.length; j++) html += renderOrderRow(cats.grid[j]);
      html += '</div>';
    }
    if (cats.other.length) {
      html += '<div style="margin-bottom:8px"><div style="font-size:11px;color:var(--text-dim);margin-bottom:4px;font-weight:600">📋 其他</div>';
      for (var j = 0; j < cats.other.length; j++) html += renderOrderRow(cats.other[j]);
      html += '</div>';
    }

    html += '</div></div>';
  }

  document.getElementById('orders-area').innerHTML = html;
}

function categorizeOrders(orders) {
  var cats = { stopLoss: [], takeProfit: [], buyAlerts: [], sellAlerts: [], grid: [], other: [] };
  for (var i = 0; i < orders.length; i++) {
    var o = orders[i];
    var desc = (o.description || '').toLowerCase();
    if (o.type === 'swing_grid') {
      cats.grid.push(o);
    } else if (desc.indexOf('买入提醒') >= 0 || desc.indexOf('加仓提醒') >= 0) {
      cats.buyAlerts.push(o);
    } else if (desc.indexOf('卖出提醒') >= 0 || desc.indexOf('减仓') >= 0) {
      cats.sellAlerts.push(o);
    } else if (o.type === 'stop_loss' || o.type === 'trailing_stop' || o.type === 'trailing_atr' || o.type === 'indicator_stop') {
      cats.stopLoss.push(o);
    } else if (o.type === 'take_profit' || o.type === 'staged_tp' || o.type === 'trailing_tp') {
      cats.takeProfit.push(o);
    } else {
      cats.other.push(o);
    }
  }
  return cats;
}

function renderOrderRow(o) {
  var cls = o.triggered ? ' triggered-row' : '';
  var statusBadge = o.triggered
    ? '<span class="badge badge-triggered">已触发</span>'
    : '<span class="badge badge-active">监控中</span>';
  var typeLabel = TYPE_LABELS[o.type] || o.type;

  // Extract key price from description
  var priceMatch = (o.description || '').match(/[¥￥](\\d+\\.?\\d*)/);
  var priceText = priceMatch ? '¥' + priceMatch[1] : '';

  var html = '<div class="order-row' + cls + '">';
  html += '<div class="order-type type-' + o.type + '">' + typeLabel + '</div>';
  html += '<div class="order-desc">' + (o.description || o.id) + '</div>';
  if (priceText) html += '<div style="width:70px;font-family:monospace;text-align:right;flex-shrink:0;color:var(--text)">' + priceText + '</div>';
  html += '<div class="order-status">' + statusBadge + '</div>';
  html += '<div class="order-actions">';
  if (!o.triggered) {
    html += '<button class="btn btn-danger btn-xs" onclick="deleteOrder(\\'' + o.id + '\\')" title="删除">✕</button>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function toggleStock(header) {
  var body = header.nextElementSibling;
  var icon = header.querySelector('.expand-icon');
  body.classList.toggle('open');
  icon.classList.toggle('open');
}

function expandAll(open) {
  document.querySelectorAll('.stock-body').forEach(function(b) {
    if (open) b.classList.add('open'); else b.classList.remove('open');
  });
  document.querySelectorAll('.expand-icon').forEach(function(i) {
    if (open) i.classList.add('open'); else i.classList.remove('open');
  });
}

async function deleteOrder(id) {
  if (!confirm('确认删除条件单 ' + id + '？')) return;
  try {
    var resp = await fetch(API_BASE, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id }),
    });
    var data = await resp.json();
    if (data.ok) {
      showToast('已删除 ' + id, 'ok');
      loadOrders();
    } else {
      showToast('删除失败: ' + (data.error || ''), 'error');
    }
  } catch (err) {
    showToast('删除失败: ' + err.message, 'error');
  }
}

async function deleteSymbolOrders(symbol) {
  var orders = allOrders.filter(function(o) { return o.symbol === symbol; });
  if (!orders.length) { showToast('该股票无条件单', 'warn'); return; }
  if (!confirm('确认删除 ' + symbol + ' 的全部 ' + orders.length + ' 个条件单（含已触发）？')) return;

  var deleted = 0;
  for (var i = 0; i < orders.length; i++) {
    try {
      var resp = await fetch(API_BASE, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: orders[i].id }),
      });
      var data = await resp.json();
      if (data.ok) deleted++;
    } catch (e) {}
  }
  showToast('已删除 ' + deleted + '/' + orders.length + ' 个条件单', 'ok');
  loadOrders();
}

async function checkNow() {
  try {
    showToast('正在检查...', 'info');
    var resp = await fetch(API_BASE, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'check_now' }),
    });
    var data = await resp.json();
    if (data.triggered && data.triggered.length) {
      showToast('触发了 ' + data.triggered.length + ' 个条件单！', 'warn');
    } else {
      showToast('检查完毕，无触发', 'ok');
    }
    loadOrders();
  } catch (err) {
    showToast('检查失败: ' + err.message, 'error');
  }
}

async function toggleHistory() {
  var area = document.getElementById('history-area');
  if (showingHistory) {
    area.style.display = 'none';
    showingHistory = false;
    return;
  }

  try {
    var resp = await fetch(API_BASE + '-history?limit=200');
    var data = await resp.json();
    allHistory = data.history || [];

    if (!allHistory.length) {
      area.innerHTML = '<div class="card"><h3>📋 历史记录</h3><p style="color:var(--text-dim)">暂无已触发历史</p></div>';
      area.style.display = 'block';
      showingHistory = true;
      return;
    }

    var html = '<div class="card"><h3>📋 历史记录 (' + allHistory.length + ')</h3>';
    html += '<table><thead><tr><th>ID</th><th>股票</th><th>类型</th><th>说明</th><th>触发时间</th></tr></thead><tbody>';
    for (var i = 0; i < allHistory.length; i++) {
      var o = allHistory[i];
      html += '<tr><td style="font-family:monospace;font-size:11px">' + o.id + '</td>';
      html += '<td><span style="font-family:monospace;color:var(--accent)">' + o.symbol + '</span> ' + (o.name || '') + '</td>';
      html += '<td class="type-' + o.type + '">' + (TYPE_LABELS[o.type] || o.type) + '</td>';
      html += '<td style="color:var(--text-dim);max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (o.description || '') + '</td>';
      html += '<td style="font-size:11px">' + (o.triggeredAt ? new Date(o.triggeredAt).toLocaleString('zh-CN') : '-') + '</td>';
      html += '</tr>';
    }
    html += '</tbody></table></div>';
    area.innerHTML = html;
    area.style.display = 'block';
    showingHistory = true;
  } catch (err) {
    showToast('加载历史失败: ' + err.message, 'error');
  }
}

// Auto-load on page ready
loadOrders();
</script>
</body>
</html>`;
}

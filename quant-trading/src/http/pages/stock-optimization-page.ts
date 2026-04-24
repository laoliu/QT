/**
 * Stock Combo Optimization Page — 个股多策略组合优化仪表盘
 *
 * Self-contained HTML SPA for:
 *  - Running per-stock strategy combo optimization
 *  - Viewing optimization results and trading plans
 *  - Applying monitoring to stocks
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

export function getStockOptimizationPageHtml(): string {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 个股组合优化</title>
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
.metric-warn{color:var(--yellow)}
.progress-bar{width:100%;height:8px;background:var(--bg3);border-radius:4px;overflow:hidden;margin:8px 0}
.progress-bar-fill{height:100%;background:var(--accent);border-radius:4px;transition:width .3s}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500}
.tag-voting{background:#1a3a2a;color:var(--green)}
.tag-weighted{background:#2a2a1a;color:var(--yellow)}
.tag-gated{background:#1a2a3a;color:var(--accent)}
.confidence-bar{display:inline-block;width:60px;height:6px;background:var(--bg3);border-radius:3px;vertical-align:middle;margin-left:6px}
.confidence-fill{height:100%;border-radius:3px}
.plan-section{margin:12px 0;padding:12px;background:var(--bg3);border-radius:6px}
.plan-section h4{font-size:13px;margin-bottom:8px;color:var(--accent)}
.zone-list{display:flex;flex-wrap:wrap;gap:8px}
.zone-item{padding:6px 12px;border-radius:6px;font-size:12px;border:1px solid var(--border)}
.zone-buy{background:#0d2818;border-color:var(--green);color:var(--green)}
.zone-sell{background:#2d1018;border-color:var(--red);color:var(--red)}
.tier-row{display:flex;align-items:center;gap:12px;padding:4px 0;font-size:12px}
.tabs{display:flex;gap:0;border-bottom:1px solid var(--border);margin-bottom:16px}
.tab{padding:8px 16px;cursor:pointer;font-size:13px;color:var(--text-dim);border-bottom:2px solid transparent;transition:all .15s}
.tab:hover{color:var(--text)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-content{display:none}
.tab-content.active{display:block}
.search-box{display:flex;gap:8px;margin-bottom:16px}
.search-box input{flex:1}
.empty-state{text-align:center;padding:40px 20px;color:var(--text-dim)}
.empty-state .icon{font-size:48px;margin-bottom:12px}
@media(max-width:768px){.grid-2{grid-template-columns:1fr}.container{padding:12px}}
</style>
</head>
<body>
<header class="header">
  <div class="header-inner">
    <a href="/quant/admin/ui" class="header-back">← 返回</a>
    <h1>🎯 个股组合优化</h1>
    <span style="color:var(--text-dim);font-size:12px">对每只股票找到最佳策略组合，输出交易计划</span>
  </div>
</header>

<div class="container">
  <!-- Tabs -->
  <div class="tabs">
    <div class="tab active" data-tab="run" onclick="switchTab('run')">🚀 运行优化</div>
    <div class="tab" data-tab="results" onclick="switchTab('results')">📊 优化结果</div>
    <div class="tab" data-tab="history" onclick="switchTab('history')">📋 历史记录</div>
    <div class="tab" data-tab="perf" onclick="switchTab('perf')">📈 绩效跟踪</div>
  </div>

  <!-- Tab: Run Optimization -->
  <div id="tab-run" class="tab-content active">
    <div class="grid grid-2">
      <div class="card">
        <h3>⚙️ 优化参数</h3>
        <div style="display:grid;gap:12px">
          <div>
            <label>目标股票 (留空则从适配分析取)</label>
            <input type="text" id="inp-symbols" placeholder="600519,000858,000001 或留空">
          </div>
          <div class="grid grid-2">
            <div>
              <label>回测年数</label>
              <select id="inp-years">
                <option value="3">3年</option>
                <option value="5" selected>5年</option>
                <option value="8">8年</option>
              </select>
            </div>
            <div>
              <label>每股取Top-K策略</label>
              <select id="inp-topk">
                <option value="3">3</option>
                <option value="5" selected>5</option>
                <option value="8">8</option>
              </select>
            </div>
          </div>
          <div class="grid grid-2">
            <div>
              <label>最大组合大小</label>
              <select id="inp-combo-size">
                <option value="2">2策略</option>
                <option value="3" selected>3策略</option>
              </select>
            </div>
            <div>
              <label>初始资金</label>
              <select id="inp-capital">
                <option value="500000">50万</option>
                <option value="1000000" selected>100万</option>
                <option value="2000000">200万</option>
              </select>
            </div>
          </div>
          <div>
            <label>组合方法</label>
            <div style="display:flex;gap:12px;margin-top:4px">
              <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
                <input type="checkbox" id="chk-voting" checked> 投票法
              </label>
              <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
                <input type="checkbox" id="chk-weighted" checked> 加权法
              </label>
              <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
                <input type="checkbox" id="chk-gated" checked> 门控法
              </label>
            </div>
          </div>
          <div style="display:flex;gap:8px">
            <button class="btn btn-primary" id="btn-run" onclick="runOptimization()">🚀 开始优化</button>
            <button class="btn btn-danger" id="btn-stop" onclick="stopOptimization()" style="display:none">⏹ 停止</button>
          </div>
        </div>
      </div>
      <div class="card">
        <h3>📊 运行进度</h3>
        <div id="progress-area">
          <div class="empty-state" style="padding:20px">
            <div style="font-size:24px;margin-bottom:8px">⏳</div>
            <div>等待运行...</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Tab: Results -->
  <div id="tab-results" class="tab-content">
    <div class="search-box">
      <input type="text" id="inp-search" placeholder="输入股票代码搜索，如 600519" onkeydown="if(event.key==='Enter')searchStock()">
      <button class="btn btn-primary" onclick="searchStock()">🔍 查询</button>
      <button class="btn" onclick="loadAllResults()">📋 加载全部</button>
    </div>
    <div id="results-area">
      <div class="empty-state">
        <div class="icon">📊</div>
        <div>运行优化后查看结果，或输入股票代码搜索</div>
      </div>
    </div>
  </div>

  <!-- Tab: History -->
  <div id="tab-history" class="tab-content">
    <div id="history-area">
      <div class="empty-state">
        <div class="icon">📋</div>
        <div>加载中...</div>
      </div>
    </div>
  </div>

  <!-- Tab: Performance -->
  <div id="tab-perf" class="tab-content">
    <div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap">
      <input type="text" id="inp-perf-symbol" placeholder="股票代码筛选 (留空=全部)" style="width:180px">
      <button class="btn btn-primary" onclick="runPerfSnapshot()">📸 生成快照</button>
      <button class="btn" onclick="loadPerfHistory()">📊 查看历史</button>
    </div>
    <div id="perf-area">
      <div class="empty-state">
        <div class="icon">📈</div>
        <div>点击"生成快照"对比实际价格与优化预测</div>
      </div>
    </div>
  </div>
</div>

<script>
${SHARED_JS_HELPERS}
${SHARED_JS_API}

const API_BASE = '/quant/admin/api/stock-optimization';

// ── Tab switching ────────────────────────────────────────────
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelector('[data-tab="'+tab+'"]').classList.add('active');
  document.getElementById('tab-'+tab).classList.add('active');
  if (tab === 'history') loadHistory();
  if (tab === 'results') loadAllResults();
  if (tab === 'perf') loadPerfHistory();
}

// ── Run optimization ─────────────────────────────────────────
let progressTimer = null;

async function runOptimization() {
  const symStr = document.getElementById('inp-symbols').value.trim();
  const symbols = symStr ? symStr.split(/[,\\s，]+/).filter(Boolean) : null;
  const methods = [];
  if (document.getElementById('chk-voting').checked) methods.push('voting');
  if (document.getElementById('chk-weighted').checked) methods.push('weighted');
  if (document.getElementById('chk-gated').checked) methods.push('gated');

  if (!methods.length) { showToast('请至少选择一种组合方法', 'warn'); return; }

  document.getElementById('btn-run').disabled = true;
  document.getElementById('btn-stop').style.display = '';

  try {
    // Start polling progress
    progressTimer = setInterval(checkProgress, 2000);
    checkProgress();

    const resp = await fetch(API_BASE + '/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        symbols,
        backtest_years: +document.getElementById('inp-years').value,
        top_k_strategies: +document.getElementById('inp-topk').value,
        max_combo_size: +document.getElementById('inp-combo-size').value,
        initial_capital: +document.getElementById('inp-capital').value,
        combo_methods: methods,
      }),
    });
    const data = await resp.json();

    if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }

    if (data.ok) {
      showToast('优化完成: ' + (data.optimized_count||0) + ' 只股票', 'ok');
      renderRunSummary(data);
    } else {
      showToast('优化失败: ' + (data.error||'未知错误'), 'error');
    }
  } catch (err) {
    showToast('请求失败: ' + err.message, 'error');
  } finally {
    document.getElementById('btn-run').disabled = false;
    document.getElementById('btn-stop').style.display = 'none';
    if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
  }
}

async function stopOptimization() {
  try {
    await fetch(API_BASE + '/stop', { method: 'POST' });
    showToast('已发送停止请求', 'ok');
  } catch {}
}

async function checkProgress() {
  try {
    const resp = await fetch(API_BASE + '/status');
    const data = await resp.json();
    renderProgress(data);
  } catch {}
}

function renderProgress(data) {
  const area = document.getElementById('progress-area');
  if (!data || data.status === 'idle') {
    area.innerHTML = '<div class="empty-state" style="padding:20px"><div style="font-size:24px;margin-bottom:8px">⏳</div><div>等待运行...</div></div>';
    return;
  }

  const pct = data.total > 0 ? Math.round(data.completed / data.total * 100) : 0;
  const statusIcon = data.status === 'done' ? '✅' : data.status === 'cancelled' ? '🚫' : '🔄';
  const elapsed = data.elapsed_seconds ? formatDuration(data.elapsed_seconds) : '-';

  area.innerHTML = \`
    <div style="margin-bottom:12px">
      <span style="font-size:16px">\${statusIcon}</span>
      <strong>\${data.phase || data.status}</strong>
    </div>
    <div class="progress-bar">
      <div class="progress-bar-fill" style="width:\${pct}%"></div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-dim);margin-top:4px">
      <span>\${data.completed || 0} / \${data.total || 0} 股票</span>
      <span>\${pct}%</span>
      <span>耗时: \${elapsed}</span>
    </div>
    \${data.current_symbol ? '<div style="font-size:12px;margin-top:8px;color:var(--text-dim)">当前: '+data.current_symbol+'</div>' : ''}
    \${data.result_count ? '<div style="font-size:12px;margin-top:4px;color:var(--green)">已优化: '+data.result_count+' 只</div>' : ''}
  \`;
}

function renderRunSummary(data) {
  const area = document.getElementById('progress-area');
  area.innerHTML = \`
    <div style="text-align:center;padding:20px">
      <div style="font-size:32px;margin-bottom:8px">✅</div>
      <div style="font-size:15px;font-weight:600">优化完成</div>
      <div style="margin-top:12px;display:grid;grid-template-columns:repeat(3,1fr);gap:12px;text-align:center">
        <div><div style="font-size:20px;font-weight:bold;color:var(--accent)">\${data.total_stocks||0}</div><div style="font-size:11px;color:var(--text-dim)">总股票数</div></div>
        <div><div style="font-size:20px;font-weight:bold;color:var(--green)">\${data.optimized_count||0}</div><div style="font-size:11px;color:var(--text-dim)">优化成功</div></div>
        <div><div style="font-size:20px;font-weight:bold">\${formatDuration(data.elapsed_seconds||0)}</div><div style="font-size:11px;color:var(--text-dim)">耗时</div></div>
      </div>
      <button class="btn btn-primary" style="margin-top:16px" onclick="switchTab('results')">查看结果 →</button>
    </div>
  \`;
}

function formatDuration(sec) {
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec/60) + 'm ' + (sec%60) + 's';
  return Math.floor(sec/3600) + 'h ' + Math.floor((sec%3600)/60) + 'm';
}

// ── Search stock ─────────────────────────────────────────────
async function searchStock() {
  const symbol = document.getElementById('inp-search').value.trim();
  if (!symbol) { showToast('请输入股票代码', 'warn'); return; }

  try {
    const resp = await fetch(API_BASE + '/result?symbol=' + encodeURIComponent(symbol));
    const data = await resp.json();
    if (data.ok && data.results && data.results.length > 0) {
      renderStockResults(data.results, data.symbol);
    } else {
      document.getElementById('results-area').innerHTML =
        '<div class="empty-state"><div class="icon">🔍</div><div>未找到 ' + symbol + ' 的优化数据</div><div style="font-size:12px;margin-top:4px;color:var(--text-dim)">请先运行优化</div></div>';
    }
  } catch (err) {
    showToast('查询失败: ' + err.message, 'error');
  }
}

async function loadAllResults() {
  try {
    const resp = await fetch(API_BASE + '/list');
    const data = await resp.json();
    if (!data.ok || !data.stocks || !data.stocks.length) {
      document.getElementById('results-area').innerHTML =
        '<div class="empty-state"><div class="icon">📊</div><div>暂无优化数据</div></div>';
      return;
    }
    renderStockList(data.stocks, data.run_id);
  } catch (err) {
    showToast('加载失败: ' + err.message, 'error');
  }
}

function renderStockList(stocks, runId) {
  const area = document.getElementById('results-area');
  let html = '<div class="card"><div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap">';
  html += '<h3 style="margin:0">📊 优化结果 — ' + stocks.length + ' 只股票 <span style="font-size:11px;color:var(--text-dim);font-weight:normal">(' + runId + ')</span></h3>';
  html += '<div style="margin-left:auto;display:flex;gap:8px;align-items:center">';
  html += '<select id="batch-category" style="padding:4px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg2);color:var(--text);font-size:12px">';
  html += '<option value="watchlist">观察池</option><option value="shortlist">精选池</option><option value="portfolio">持仓池</option><option value="ideas">灵感池</option>';
  html += '</select>';
  html += '<button class="btn btn-primary btn-sm" id="batch-add-btn" onclick="batchAddToPool()" disabled style="white-space:nowrap">⭐ 批量加入股票池 (0)</button>';
  html += '<button class="btn btn-sm" id="batch-monitor-btn" onclick="showBatchMonitorDialog()" disabled style="white-space:nowrap;background:#1e3a5f;color:#60a5fa">📡 批量监控 (0)</button>';
  html += '</div></div>';
  html += '<table><thead><tr><th style="width:32px"><input type="checkbox" id="select-all" onchange="toggleSelectAll(this.checked)"></th><th>股票代码</th><th>名称</th><th>现价</th><th>评分</th><th>收益</th><th>夏普</th><th>回撤</th><th>胜率</th><th>组合数</th><th>操作</th></tr></thead><tbody>';
  for (const s of stocks) {
    html += '<tr>'
      + '<td><input type="checkbox" class="stock-check" data-code="' + s.symbol + '" data-name="' + (s.stock_name||'') + '" data-score="' + (s.best_score||0).toFixed(1) + '" data-price="' + (s.current_price||0) + '" onchange="updateBatchCount()"></td>'
      + '<td style="font-family:monospace"><a href="/quant/admin/stock?symbol=' + s.symbol + '" target="_blank" style="color:var(--accent);text-decoration:none">' + s.symbol + '</a></td>'
      + '<td>' + (s.stock_name||'-') + '</td>'
      + '<td style="font-family:monospace">' + (s.current_price ? '¥' + s.current_price.toFixed(2) : '-') + '</td>'
      + '<td style="font-weight:600">' + (s.best_score||0).toFixed(1) + '</td>'
      + '<td class="' + ((s.best_return||0)>=0?'metric-good':'metric-bad') + '">' + (s.best_return||0).toFixed(1) + '%</td>'
      + '<td>' + (s.best_sharpe||0).toFixed(2) + '</td>'
      + '<td class="metric-bad">-' + (s.best_drawdown||0).toFixed(1) + '%</td>'
      + '<td>' + (s.best_win_rate||0).toFixed(1) + '%</td>'
      + '<td>' + (s.combo_count||0) + '</td>'
      + '<td><button class="btn btn-sm" onclick="viewStock(\\'' + s.symbol + '\\')">📋 详情</button></td>'
      + '</tr>';
  }
  html += '</tbody></table></div>';
  area.innerHTML = html;
}

function toggleSelectAll(checked) {
  document.querySelectorAll('.stock-check').forEach(function(cb) { cb.checked = checked; });
  updateBatchCount();
}

function updateBatchCount() {
  const checked = document.querySelectorAll('.stock-check:checked');
  const btn = document.getElementById('batch-add-btn');
  if (btn) {
    btn.disabled = checked.length === 0;
    btn.textContent = '⭐ 批量加入股票池 (' + checked.length + ')';
  }
  const monBtn = document.getElementById('batch-monitor-btn');
  if (monBtn) {
    monBtn.disabled = checked.length === 0;
    monBtn.textContent = '📡 批量监控 (' + checked.length + ')';
  }
  // Sync select-all checkbox state
  const all = document.querySelectorAll('.stock-check');
  const selectAll = document.getElementById('select-all');
  if (selectAll) {
    selectAll.checked = all.length > 0 && checked.length === all.length;
    selectAll.indeterminate = checked.length > 0 && checked.length < all.length;
  }
}

async function batchAddToPool() {
  const checked = document.querySelectorAll('.stock-check:checked');
  if (!checked.length) return;
  const category = document.getElementById('batch-category')?.value || 'watchlist';
  const stocks = [];
  checked.forEach(function(cb) {
    stocks.push({
      code: cb.dataset.code,
      name: cb.dataset.name || undefined,
      priority: Math.round(parseFloat(cb.dataset.score || '0') / 20), // 0-100 score -> 1-5 priority
    });
  });

  const btn = document.getElementById('batch-add-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 添加中...'; }

  try {
    const resp = await fetch(API_BASE.replace('/stock-optimization', '') + '/favorites/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        stocks: stocks,
        category: category,
        tags: ['combo-optimization'],
      }),
    });
    const data = await resp.json();
    if (data.ok) {
      showToast('已添加 ' + data.succeeded + '/' + data.total + ' 只股票到' + (category === 'watchlist' ? '观察池' : category === 'shortlist' ? '精选池' : category === 'portfolio' ? '持仓池' : '灵感池'), 'ok');
      // Uncheck all
      document.querySelectorAll('.stock-check').forEach(function(cb) { cb.checked = false; });
      const selectAll = document.getElementById('select-all');
      if (selectAll) { selectAll.checked = false; selectAll.indeterminate = false; }
      updateBatchCount();
    } else {
      showToast('添加失败: ' + (data.error || ''), 'error');
    }
  } catch (err) {
    showToast('添加失败: ' + err.message, 'error');
  }
  if (btn) { btn.disabled = false; updateBatchCount(); }
}

function showBatchMonitorDialog() {
  const checked = document.querySelectorAll('.stock-check:checked');
  if (!checked.length) return;

  const existing = document.getElementById('batch-monitor-dialog');
  if (existing) existing.remove();

  let stockRows = '';
  checked.forEach(function(cb) {
    const price = parseFloat(cb.dataset.price) || 0;
    stockRows += '<tr>'
      + '<td style="font-family:monospace">' + cb.dataset.code + '</td>'
      + '<td>' + (cb.dataset.name || '-') + '</td>'
      + '<td style="font-family:monospace">' + (price > 0 ? '¥' + price.toFixed(2) : '-') + '</td>'
      + '<td><input type="number" class="bm-qty" data-code="' + cb.dataset.code + '" data-price="' + price + '" data-score="' + (cb.dataset.score || '50') + '" step="100" value="1000" style="width:80px;padding:2px 4px;border-radius:4px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:12px"></td>'
      + '<td class="bm-amount" data-code="' + cb.dataset.code + '" style="font-size:11px;color:var(--text-dim)">-</td>'
      + '</tr>';
  });

  const dialog = document.createElement('div');
  dialog.id = 'batch-monitor-dialog';
  dialog.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:1000';
  dialog.innerHTML = '<div style="background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:24px;width:600px;max-width:90vw;max-height:80vh;overflow-y:auto">'
    + '<h3 style="margin-bottom:8px">📡 批量应用监控 — ' + checked.length + ' 只股票</h3>'
    + '<p style="font-size:12px;color:var(--text-dim);margin-bottom:12px">将为每只股票自动创建条件单：止损、分批止盈、买卖区间提醒。</p>'
    + '<div style="display:flex;gap:12px;margin-bottom:12px;align-items:end;flex-wrap:wrap;padding:12px;background:var(--bg3);border-radius:8px">'
    + '<div><label style="font-size:12px;display:block;margin-bottom:4px">💰 总投入金额 (¥)</label>'
    + '<input type="number" id="bm-total-amount" step="10000" placeholder="如 500000" style="width:140px;padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--bg2);color:var(--text);font-size:13px"></div>'
    + '<div><label style="font-size:12px;display:block;margin-bottom:4px">分配方式</label>'
    + '<select id="bm-alloc-method" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--bg2);color:var(--text);font-size:12px">'
    + '<option value="score">按评分加权</option><option value="equal">等额分配</option></select></div>'
    + '<button class="btn btn-sm btn-primary" onclick="calcBatchShares()" style="white-space:nowrap">📊 自动计算股数</button>'
    + '</div>'
    + '<table style="width:100%;font-size:12px;margin-bottom:12px"><thead><tr><th>代码</th><th>名称</th><th>现价</th><th>持仓股数</th><th>预估金额</th></tr></thead><tbody>' + stockRows + '</tbody></table>'
    + '<div id="bm-summary" style="font-size:12px;color:var(--text-dim);margin-bottom:12px"></div>'
    + '<div style="display:flex;gap:8px;justify-content:flex-end">'
    + '<button class="btn" onclick="document.getElementById(\\\'batch-monitor-dialog\\\').remove()">取消</button>'
    + '<button class="btn btn-primary" id="bm-confirm-btn">确认创建条件单</button>'
    + '</div></div>';
  document.body.appendChild(dialog);
  document.getElementById('bm-confirm-btn').addEventListener('click', doBatchMonitor);
  dialog.addEventListener('click', function(e) { if (e.target === dialog) dialog.remove(); });

  // Auto-update amounts when qty changes
  document.querySelectorAll('.bm-qty').forEach(function(inp) {
    inp.addEventListener('input', updateBmSummary);
  });
}

function calcBatchShares() {
  const totalAmount = parseFloat(document.getElementById('bm-total-amount').value) || 0;
  if (totalAmount <= 0) { showToast('请输入总投入金额', 'warn'); return; }

  const method = document.getElementById('bm-alloc-method').value;
  const inputs = document.querySelectorAll('.bm-qty');
  const items = [];
  let totalScore = 0;

  inputs.forEach(function(inp) {
    const price = parseFloat(inp.dataset.price) || 0;
    const score = parseFloat(inp.dataset.score) || 50;
    items.push({ inp: inp, price: price, score: score });
    totalScore += score;
  });

  if (!items.length) return;

  items.forEach(function(item) {
    if (item.price <= 0) { item.inp.value = 0; return; }

    let allocAmount;
    if (method === 'score') {
      // Score-weighted: higher score gets more capital
      allocAmount = totalAmount * (item.score / totalScore);
    } else {
      // Equal allocation
      allocAmount = totalAmount / items.length;
    }

    // A-share lot size is 100 shares, round down to nearest 100
    const rawShares = allocAmount / item.price;
    const lots = Math.floor(rawShares / 100);
    item.inp.value = lots * 100;
  });

  updateBmSummary();
}

function updateBmSummary() {
  const inputs = document.querySelectorAll('.bm-qty');
  let totalUsed = 0;
  inputs.forEach(function(inp) {
    const qty = parseInt(inp.value) || 0;
    const price = parseFloat(inp.dataset.price) || 0;
    const amount = qty * price;
    totalUsed += amount;
    // Update per-row amount display
    const amountCell = document.querySelector('.bm-amount[data-code="' + inp.dataset.code + '"]');
    if (amountCell) {
      amountCell.textContent = amount > 0 ? '¥' + amount.toLocaleString('zh-CN', {maximumFractionDigits: 0}) : '-';
    }
  });

  const summary = document.getElementById('bm-summary');
  if (summary) {
    const totalTarget = parseFloat((document.getElementById('bm-total-amount') || {}).value) || 0;
    let text = '预估总投入: ¥' + totalUsed.toLocaleString('zh-CN', {maximumFractionDigits: 0});
    if (totalTarget > 0) {
      const usage = (totalUsed / totalTarget * 100).toFixed(1);
      text += ' / ¥' + totalTarget.toLocaleString('zh-CN', {maximumFractionDigits: 0}) + ' (使用 ' + usage + '%)';
    }
    summary.textContent = text;
  }
}

async function doBatchMonitor() {
  const qtyInputs = document.querySelectorAll('.bm-qty');
  const stocks = [];
  qtyInputs.forEach(function(inp) {
    stocks.push({ symbol: inp.dataset.code, quantity: parseInt(inp.value) || 1000 });
  });

  if (!stocks.length) return;

  const btn = document.getElementById('bm-confirm-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 创建中...'; }

  try {
    const resp = await fetch(API_BASE + '/apply-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stocks: stocks }),
    });
    const data = await resp.json();
    const dialog = document.getElementById('batch-monitor-dialog');
    if (dialog) dialog.remove();

    if (data.ok) {
      showToast(data.message || ('已创建 ' + data.total_orders + ' 个条件单'), 'ok');
      if (data.failed > 0) {
        const failedList = data.results.filter(function(r) { return !r.ok; }).map(function(r) { return r.symbol + ': ' + r.error; }).join(', ');
        showToast('部分失败: ' + failedList, 'warn');
      }
    } else {
      showToast('创建失败: ' + (data.error || ''), 'error');
    }
  } catch (err) {
    showToast('请求失败: ' + err.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '确认创建条件单'; }
  }
}

function viewStock(symbol) {
  document.getElementById('inp-search').value = symbol;
  searchStock();
}

function renderStockResults(results, symbol) {
  const area = document.getElementById('results-area');
  if (!results.length) {
    area.innerHTML = '<div class="empty-state"><div class="icon">📊</div><div>暂无数据</div></div>';
    return;
  }

  const best = results[0]; // highest composite_score
  const plan = best.trading_plan || {};
  const metrics = plan.backtestMetrics || {};
  window._currentPlan = { best, plan, symbol }; // store for apply action

  let html = \`
    <div class="card">
      <h3>🏆 \${symbol} \${best.stock_name ? '(' + best.stock_name + ')' : ''} — 最佳组合</h3>
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center">
        <span class="tag tag-\${best.combo_method}">\${best.combo_method}</span>
  \`;

  // Strategy combo
  const strategyIds = Array.isArray(best.strategy_ids) ? best.strategy_ids : [];
  const strategyNames = Array.isArray(best.strategy_names) ? best.strategy_names : strategyIds;
  for (let i = 0; i < strategyIds.length; i++) {
    html += '<span class="tag" style="background:var(--bg3)">' + (strategyNames[i]||strategyIds[i]) + '</span>';
  }

  // Optimized params badge
  if (best.optimized_params && typeof best.optimized_params === 'object' && Object.keys(best.optimized_params).length > 0) {
    html += '<span class="tag" style="background:#2a1a3a;color:#c084fc">⚡ 参数已优化</span>';
  }

  // Apply monitoring button
  html += '<button class="btn btn-primary btn-sm" style="margin-left:auto" onclick="showApplyDialog(this.dataset.sym)" data-sym="' + symbol + '" title="一键应用到条件单监控">📡 应用监控</button>';
  html += '</div>';

  // KPI metrics
  html += \`
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px">
      <div class="kpi"><div class="kpi-value \${(best.total_return||0)>=0?'metric-good':'metric-bad'}">\${(best.total_return||0).toFixed(1)}%</div><div class="kpi-label">总收益</div></div>
      <div class="kpi"><div class="kpi-value \${(best.annualized_return||0)>=0?'metric-good':'metric-bad'}">\${(best.annualized_return||0).toFixed(1)}%</div><div class="kpi-label">年化收益</div></div>
      <div class="kpi"><div class="kpi-value \${(best.sharpe_ratio||0)>1?'metric-good':(best.sharpe_ratio||0)>0.5?'metric-warn':'metric-bad'}">\${(best.sharpe_ratio||0).toFixed(2)}</div><div class="kpi-label">夏普</div></div>
      <div class="kpi"><div class="kpi-value metric-bad">-\${(best.max_drawdown||0).toFixed(1)}%</div><div class="kpi-label">最大回撤</div></div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px">
      <div class="kpi"><div class="kpi-value">\${(best.win_rate||0).toFixed(1)}%</div><div class="kpi-label">胜率</div></div>
      <div class="kpi"><div class="kpi-value">\${(best.profit_factor||0).toFixed(2)}</div><div class="kpi-label">盈亏比</div></div>
      <div class="kpi"><div class="kpi-value">\${best.total_trades||0}</div><div class="kpi-label">总交易</div></div>
      <div class="kpi">
        <div class="kpi-value">\${((best.confidence||0)*100).toFixed(0)}%</div>
        <div class="kpi-label">置信度</div>
      </div>
    </div>
  \`;

  // Equity curve chart
  if (plan.equityCurve && plan.equityCurve.length > 2) {
    html += '<div class="plan-section"><h4>📈 资金曲线</h4><canvas id="equityCanvas" width="1100" height="220" style="width:100%;max-height:220px"></canvas></div>';
  }

  // Buy/sell zone price chart
  if ((plan.buyZones && plan.buyZones.length) || (plan.sellZones && plan.sellZones.length)) {
    html += '<div class="plan-section"><h4>📊 价格区间图</h4><canvas id="zoneCanvas" width="600" height="180" style="width:100%;max-height:180px"></canvas></div>';
  }

  // Trading Plan
  if (plan.buyZones || plan.sellZones) {
    html += '<div class="plan-section"><h4>📍 买入区间</h4><div class="zone-list">';
    for (const z of (plan.buyZones||[])) {
      html += '<div class="zone-item zone-buy">¥' + z.priceLevel + ' <span style="font-size:10px">(' + (z.description||z.type) + ')</span></div>';
    }
    html += '</div></div>';

    html += '<div class="plan-section"><h4>📍 卖出区间</h4><div class="zone-list">';
    for (const z of (plan.sellZones||[])) {
      html += '<div class="zone-item zone-sell">¥' + z.priceLevel + ' <span style="font-size:10px">(' + (z.description||z.type) + ')</span></div>';
    }
    html += '</div></div>';
  }

  // Stop loss & Take profit
  if (plan.stopLoss) {
    html += \`<div class="plan-section"><h4>🛡️ 止损止盈</h4>
      <div style="font-size:13px">
        <div>止损: <strong class="metric-bad">¥\${plan.stopLoss.price}</strong> (距入场价 -\${plan.stopLoss.pctFromEntry}%)</div>
    \`;
    if (plan.takeProfitTiers) {
      for (const tier of plan.takeProfitTiers) {
        html += '<div class="tier-row">止盈 +' + tier.targetPct + '% → 卖出 ' + (tier.sellPortion*100).toFixed(0) + '% 仓位</div>';
      }
    }
    html += '</div></div>';
  }

  // Position sizing
  if (plan.positionSizing) {
    const ps = plan.positionSizing;
    html += \`<div class="plan-section"><h4>💰 仓位管理</h4>
      <div style="font-size:13px">
        <div>方法: \${ps.method} | 初始仓位: \${ps.initialPct}% | 最大仓位: \${ps.maxPct}% | 最多加仓: \${ps.maxScaleCount}次</div>
      </div>
    </div>\`;
  }

  // Add/reduce rules
  if (plan.addPositionRules && plan.addPositionRules.length) {
    html += '<div class="plan-section"><h4>➕ 加仓规则</h4>';
    for (const r of plan.addPositionRules) {
      html += '<div style="font-size:12px;padding:4px 0">• ' + r.description + ' (' + r.pct + '% 仓位)</div>';
    }
    html += '</div>';
  }
  if (plan.reducePositionRules && plan.reducePositionRules.length) {
    html += '<div class="plan-section"><h4>➖ 减仓规则</h4>';
    for (const r of plan.reducePositionRules) {
      html += '<div style="font-size:12px;padding:4px 0">• ' + r.description + ' (' + r.pct + '% 仓位)</div>';
    }
    html += '</div>';
  }

  // Holding period
  if (plan.holdingPeriod) {
    const hp = plan.holdingPeriod;
    html += '<div class="plan-section"><h4>⏱️ 持仓周期</h4><div style="font-size:13px">最优: ' + hp.optimalDays + '天 | 范围: ' + hp.minDays + '-' + hp.maxDays + '天</div></div>';
  }

  // Timing hints
  if (plan.timingHints) {
    const th = plan.timingHints;
    html += '<div class="plan-section"><h4>🕐 交易时机</h4><div style="font-size:13px">';
    if (th.bestEntryDays && th.bestEntryDays.length) {
      html += '<div style="margin-bottom:6px"><strong>最佳入场日:</strong> ';
      html += th.bestEntryDays.map(d => '<span class="tag" style="background:#0d2818;color:var(--green)">' + d.day + ' (均盈 ' + d.avgPnl + '%)</span>').join(' ');
      html += '</div>';
    }
    if (th.avoidDays && th.avoidDays.length) {
      html += '<div style="margin-bottom:6px"><strong>回避日:</strong> ';
      html += th.avoidDays.map(d => '<span class="tag" style="background:#2d1018;color:var(--red)">' + d.day + ' (均亏 ' + d.avgPnl + '%)</span>').join(' ');
      html += '</div>';
    }
    if (th.buyDayDistribution) {
      html += '<div style="margin-top:4px;color:var(--text-dim)">买入分布: ' + Object.entries(th.buyDayDistribution).map(([k,v]) => k + ':' + v + '次').join(' | ') + '</div>';
    }
    html += '</div></div>';
  }

  // Optimized params
  if (best.optimized_params && typeof best.optimized_params === 'object' && Object.keys(best.optimized_params).length > 0) {
    html += '<div class="plan-section"><h4>⚡ 优化后参数</h4><div style="font-size:12px;font-family:monospace">';
    for (const [sid, params] of Object.entries(best.optimized_params)) {
      html += '<div style="margin-bottom:4px"><strong>' + sid + ':</strong> ' + JSON.stringify(params) + '</div>';
    }
    html += '</div></div>';
  }

  html += '</div>';

  // Other combos table
  if (results.length > 1) {
    html += '<div class="card"><h3>📋 其他组合对比</h3><table><thead><tr><th>组合方法</th><th>策略</th><th>评分</th><th>收益</th><th>夏普</th><th>回撤</th><th>胜率</th><th>置信度</th></tr></thead><tbody>';
    for (const r of results.slice(1)) {
      const names = (Array.isArray(r.strategy_names) ? r.strategy_names : r.strategy_ids || []).join('+');
      html += '<tr>'
        + '<td><span class="tag tag-' + r.combo_method + '">' + r.combo_method + '</span></td>'
        + '<td style="font-size:11px">' + names + '</td>'
        + '<td>' + (r.composite_score||0).toFixed(1) + '</td>'
        + '<td class="' + ((r.total_return||0)>=0?'metric-good':'metric-bad') + '">' + (r.total_return||0).toFixed(1) + '%</td>'
        + '<td>' + (r.sharpe_ratio||0).toFixed(2) + '</td>'
        + '<td class="metric-bad">-' + (r.max_drawdown||0).toFixed(1) + '%</td>'
        + '<td>' + (r.win_rate||0).toFixed(1) + '%</td>'
        + '<td>' + ((r.confidence||0)*100).toFixed(0) + '%</td>'
        + '</tr>';
    }
    html += '</tbody></table></div>';
  }

  area.innerHTML = html;

  // Render charts after DOM update
  if (plan.equityCurve && plan.equityCurve.length > 2) {
    drawEquityCurve(plan.equityCurve);
  }
  if ((plan.buyZones && plan.buyZones.length) || (plan.sellZones && plan.sellZones.length)) {
    drawZoneChart(plan.buyZones || [], plan.sellZones || [], plan.stopLoss);
  }
}

// ── History ──────────────────────────────────────────────────
async function loadHistory() {
  try {
    const resp = await fetch(API_BASE + '/history');
    const data = await resp.json();
    renderHistory(data.runs || []);
  } catch (err) {
    showToast('加载历史失败: ' + err.message, 'error');
  }
}

function renderHistory(runs) {
  const area = document.getElementById('history-area');
  if (!runs.length) {
    area.innerHTML = '<div class="empty-state"><div class="icon">📋</div><div>暂无历史记录</div></div>';
    return;
  }

  let html = '<table><thead><tr><th>运行ID</th><th>时间</th><th>股票数</th><th>组合数</th><th>平均评分</th><th>操作</th></tr></thead><tbody>';
  for (const r of runs) {
    html += '<tr>'
      + '<td style="font-family:monospace;font-size:12px">' + r.run_id + '</td>'
      + '<td>' + (r.created_at||'') + '</td>'
      + '<td>' + r.stock_count + '</td>'
      + '<td>' + r.combo_count + '</td>'
      + '<td>' + (r.avg_score?.toFixed(1)||'-') + '</td>'
      + '<td><button class="btn btn-danger btn-sm" onclick="deleteRun(\\'' + r.run_id + '\\')">删除</button></td>'
      + '</tr>';
  }
  html += '</tbody></table>';
  area.innerHTML = html;
}

async function deleteRun(runId) {
  if (!confirm('确认删除运行 ' + runId + ' ?')) return;
  try {
    const resp = await fetch(API_BASE + '/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ run_id: runId }),
    });
    const data = await resp.json();
    if (data.ok) {
      showToast('已删除', 'ok');
      loadHistory();
    } else {
      showToast('删除失败: ' + (data.error||''), 'error');
    }
  } catch (err) {
    showToast('删除失败: ' + err.message, 'error');
  }
}

// ── Charts ───────────────────────────────────────────────────
function drawEquityCurve(curve) {
  const canvas = document.getElementById('equityCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const values = curve.map(p => p.value);
  const minV = Math.min(...values) * 0.98;
  const maxV = Math.max(...values) * 1.02;
  const range = maxV - minV || 1;

  // Grid lines
  ctx.strokeStyle = 'rgba(255,255,255,0.05)';
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const y = H * i / 4;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
  }

  // Draw line
  ctx.strokeStyle = '#3b82f6';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = 0; i < values.length; i++) {
    const x = (i / (values.length - 1)) * W;
    const y = H - ((values[i] - minV) / range) * H;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Fill area
  ctx.lineTo(W, H);
  ctx.lineTo(0, H);
  ctx.closePath();
  ctx.fillStyle = 'rgba(59,130,246,0.1)';
  ctx.fill();

  // Baseline (initial capital)
  if (values[0]) {
    const baseY = H - ((values[0] - minV) / range) * H;
    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(0, baseY); ctx.lineTo(W, baseY); ctx.stroke();
    ctx.setLineDash([]);
  }

  // Labels
  ctx.font = '11px sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,0.5)';
  ctx.fillText('¥' + maxV.toFixed(0), 4, 12);
  ctx.fillText('¥' + minV.toFixed(0), 4, H - 4);
  if (curve[0] && curve[0].date) ctx.fillText(curve[0].date, 4, H - 16);
  if (curve.length > 1 && curve[curve.length-1].date) {
    const lastDate = curve[curve.length-1].date;
    ctx.fillText(lastDate, W - ctx.measureText(lastDate).width - 4, H - 16);
  }
  // Final value
  const finalV = values[values.length - 1];
  const pct = ((finalV / values[0]) - 1) * 100;
  const color = pct >= 0 ? '#22c55e' : '#ef4444';
  ctx.fillStyle = color;
  ctx.fillText('¥' + finalV.toFixed(0) + ' (' + (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%)', W - 150, 14);
}

function drawZoneChart(buyZones, sellZones, stopLoss) {
  const canvas = document.getElementById('zoneCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  // Collect all zone items with their category for unified layout
  const items = [];
  for (const z of buyZones) items.push({ price: z.priceLevel, type: z.type || 'buy', cat: 'buy' });
  for (const z of sellZones) items.push({ price: z.priceLevel, type: z.type || 'sell', cat: 'sell' });
  if (stopLoss && stopLoss.price) items.push({ price: stopLoss.price, type: '止损', cat: 'stop' });
  if (!items.length) return;

  // Sort by price descending (highest at top)
  items.sort((a, b) => b.price - a.price);

  const minP = Math.min(...items.map(i => i.price)) * 0.95;
  const maxP = Math.max(...items.map(i => i.price)) * 1.05;
  const range = maxP - minP || 1;
  const pad = 80; // left padding for price labels
  const rowH = 18; // minimum row height in pixels

  function yForPrice(p) { return H - ((p - minP) / range) * (H - 20) - 10; }

  // Compute raw Y positions then push apart overlapping labels
  const slots = items.map(item => ({ ...item, rawY: yForPrice(item.price), y: yForPrice(item.price) }));
  // Push apart: iterate top-to-bottom, ensure minimum gap
  for (let i = 1; i < slots.length; i++) {
    const gap = slots[i].y - slots[i - 1].y;
    if (gap < rowH) {
      slots[i].y = slots[i - 1].y + rowH;
    }
  }
  // If bottom overflows, shift everything up proportionally
  const maxY = H - 10;
  if (slots.length && slots[slots.length - 1].y > maxY) {
    const overflow = slots[slots.length - 1].y - maxY;
    for (const s of slots) s.y -= overflow * 0.5;
  }

  ctx.font = '11px sans-serif';

  for (const s of slots) {
    const y = s.y;
    const lineY = s.rawY; // horizontal line at true price position

    if (s.cat === 'buy') {
      ctx.strokeStyle = 'rgba(34,197,94,0.5)';
      ctx.fillStyle = 'rgba(34,197,94,0.08)';
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.fillRect(pad, lineY - 1, W - pad, 4);
      ctx.beginPath(); ctx.moveTo(pad, lineY); ctx.lineTo(W, lineY); ctx.stroke();
      ctx.fillStyle = '#22c55e';
      ctx.fillText('¥' + s.price.toFixed(2), 2, y + 4);
      ctx.fillText(s.type, pad + 8, y + 4);
    } else if (s.cat === 'sell') {
      ctx.strokeStyle = 'rgba(239,68,68,0.5)';
      ctx.fillStyle = 'rgba(239,68,68,0.08)';
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.fillRect(pad, lineY - 1, W - pad, 4);
      ctx.beginPath(); ctx.moveTo(pad, lineY); ctx.lineTo(W, lineY); ctx.stroke();
      ctx.fillStyle = '#ef4444';
      ctx.fillText('¥' + s.price.toFixed(2), 2, y + 4);
      ctx.fillText(s.type, pad + 8, y + 4);
    } else {
      // stop loss — dashed
      ctx.strokeStyle = '#ef4444';
      ctx.setLineDash([6, 3]);
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(pad, lineY); ctx.lineTo(W, lineY); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#ef4444';
      ctx.fillText('止损 ¥' + s.price.toFixed(2), 2, y + 4);
    }

    // Draw thin connector from label to actual price line when displaced
    if (Math.abs(y - lineY) > 4) {
      ctx.strokeStyle = 'rgba(255,255,255,0.15)';
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 2]);
      ctx.beginPath(); ctx.moveTo(pad - 4, y + 1); ctx.lineTo(pad - 4, lineY); ctx.stroke();
      ctx.setLineDash([]);
    }
  }
}

// ── Apply monitoring dialog ──────────────────────────────────
function showApplyDialog(symbol) {
  const existing = document.getElementById('apply-dialog');
  if (existing) existing.remove();

  const dialog = document.createElement('div');
  dialog.id = 'apply-dialog';
  dialog.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:1000';
  dialog.innerHTML = \`
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:24px;width:400px;max-width:90vw">
      <h3 style="margin-bottom:16px">📡 应用监控 — \${symbol}</h3>
      <p style="font-size:12px;color:var(--text-dim);margin-bottom:12px">将交易计划中的止损、止盈、买卖区间提醒应用到条件单系统</p>
      <div style="display:grid;gap:12px">
        <div>
          <label>入场价格 (¥)</label>
          <input type="number" id="apply-entry" step="0.01" placeholder="当前/计划入场价">
        </div>
        <div>
          <label>持仓股数</label>
          <input type="number" id="apply-qty" step="100" value="1000" placeholder="如 1000">
        </div>
      </div>
      <div style="display:flex;gap:8px;margin-top:16px;justify-content:flex-end">
        <button class="btn" onclick="document.getElementById('apply-dialog').remove()">取消</button>
        <button class="btn btn-primary" id="apply-confirm-btn">确认应用</button>
      </div>
    </div>
  \`;
  document.body.appendChild(dialog);
  document.getElementById('apply-confirm-btn').addEventListener('click', () => doApply(symbol));
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.remove(); });
}

async function doApply(symbol) {
  const entryPrice = parseFloat(document.getElementById('apply-entry').value);
  const quantity = parseInt(document.getElementById('apply-qty').value);

  if (!entryPrice || entryPrice <= 0) { showToast('请输入有效入场价格', 'warn'); return; }
  if (!quantity || quantity <= 0) { showToast('请输入有效股数', 'warn'); return; }

  try {
    const resp = await fetch(API_BASE + '/apply', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ symbol, entry_price: entryPrice, quantity }),
    });
    const data = await resp.json();
    const dialog = document.getElementById('apply-dialog');
    if (dialog) dialog.remove();

    if (data.ok) {
      showToast(data.message || '已应用监控', 'ok');
    } else {
      showToast('应用失败: ' + (data.error || ''), 'error');
    }
  } catch (err) {
    showToast('请求失败: ' + err.message, 'error');
  }
}

// ── Performance tracking ─────────────────────────────────────
async function runPerfSnapshot() {
  try {
    showToast('正在生成绩效快照...', 'info');
    const resp = await fetch(API_BASE + '/track', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
    });
    const data = await resp.json();
    if (data.ok) {
      showToast('快照完成: 追踪 ' + (data.tracked||0) + ' 只股票', 'ok');
      renderPerfResults(data);
    } else {
      showToast('快照失败: ' + (data.error||''), 'error');
    }
  } catch (err) {
    showToast('请求失败: ' + err.message, 'error');
  }
}

async function loadPerfHistory() {
  try {
    const sym = (document.getElementById('inp-perf-symbol').value || '').trim();
    let url = API_BASE + '/perf-history?limit=50';
    if (sym) url += '&symbol=' + encodeURIComponent(sym);
    const resp = await fetch(url);
    const data = await resp.json();
    if (data.ok && data.history && data.history.length) {
      renderPerfHistory(data.history);
    } else {
      document.getElementById('perf-area').innerHTML =
        '<div class="empty-state"><div class="icon">📈</div><div>暂无绩效数据，请先生成快照</div></div>';
    }
  } catch (err) {
    showToast('加载失败: ' + err.message, 'error');
  }
}

function renderPerfResults(data) {
  const area = document.getElementById('perf-area');
  if (!data.results || !data.results.length) {
    area.innerHTML = '<div class="empty-state"><div class="icon">📈</div><div>无追踪数据</div></div>';
    return;
  }

  let html = '<div class="card"><h3>📸 快照: ' + (data.snapshot_date||'') + '</h3>';
  html += '<table><thead><tr><th>股票</th><th>当前价</th><th>预期收益</th><th>实际收益</th><th>买入区</th><th>卖出区</th><th>止损</th><th>置信度调整</th></tr></thead><tbody>';
  for (const r of data.results) {
    const actualCls = (r.actual_return||0) >= 0 ? 'metric-good' : 'metric-bad';
    html += '<tr>'
      + '<td>' + r.symbol + '</td>'
      + '<td>¥' + (r.price||0).toFixed(2) + '</td>'
      + '<td>' + (r.expected_return||0).toFixed(1) + '%</td>'
      + '<td class="' + actualCls + '">' + (r.actual_return||0).toFixed(1) + '%</td>'
      + '<td>' + (r.hit_buy_zone ? '✅' : '-') + '</td>'
      + '<td>' + (r.hit_sell_zone ? '✅' : '-') + '</td>'
      + '<td>' + (r.hit_stop_loss ? '🚨' : '-') + '</td>'
      + '<td>' + (r.confidence_adj > 0 ? '+' : '') + (r.confidence_adj||0).toFixed(2) + '</td>'
      + '</tr>';
  }
  html += '</tbody></table></div>';
  area.innerHTML = html;
}

function renderPerfHistory(history) {
  const area = document.getElementById('perf-area');
  let html = '<div class="card"><h3>📊 绩效追踪历史</h3>';
  html += '<table><thead><tr><th>日期</th><th>股票</th><th>当前价</th><th>预期</th><th>实际</th><th>买入区</th><th>卖出区</th><th>止损</th></tr></thead><tbody>';
  for (const r of history) {
    const actualCls = (r.actual_return||0) >= 0 ? 'metric-good' : 'metric-bad';
    html += '<tr>'
      + '<td style="font-size:11px">' + (r.snapshot_date||'') + '</td>'
      + '<td>' + r.symbol + '</td>'
      + '<td>¥' + (r.current_price||0).toFixed(2) + '</td>'
      + '<td>' + (r.expected_return||0).toFixed(1) + '%</td>'
      + '<td class="' + actualCls + '">' + (r.actual_return||0).toFixed(1) + '%</td>'
      + '<td>' + (r.hit_buy_zone ? '✅' : '-') + '</td>'
      + '<td>' + (r.hit_sell_zone ? '✅' : '-') + '</td>'
      + '<td>' + (r.hit_stop_loss ? '🚨' : '-') + '</td>'
      + '</tr>';
  }
  html += '</tbody></table></div>';
  area.innerHTML = html;
}

// ── Toast ────────────────────────────────────────────────────
function showToast(msg, type) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  const t = document.createElement('div');
  t.className = 'toast toast-' + (type||'info');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

// ── Init ─────────────────────────────────────────────────────
checkProgress();
</script>
</body>
</html>`;
}

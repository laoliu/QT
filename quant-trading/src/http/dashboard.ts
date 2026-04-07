/**
 * Dashboard HTML page — serves a self-contained single-page trading dashboard.
 *
 * Features:
 *  - Portfolio overview with positions table
 *  - SVG equity curve & daily P&L chart (lightweight, no external deps)
 *  - Alerts panel (unacknowledged alerts, summary)
 *  - Connection health & data staleness status
 *  - Engine controls (start/stop/pause/resume)
 *  - Orders and signals tables
 *  - Auto-refresh polling (30s)
 */

/** Return the full HTML page for the trading dashboard. */
export function getDashboardHtml(baseUrl: string): string {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenClaw 量化交易仪表盘</title>
  <style>
    :root {
      --bg: #0d1117;
      --bg2: #161b22;
      --bg3: #21262d;
      --border: #30363d;
      --text: #c9d1d9;
      --text-dim: #8b949e;
      --accent: #58a6ff;
      --green: #3fb950;
      --red: #f85149;
      --yellow: #d29922;
      --orange: #db6d28;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }
    header {
      background: var(--bg2);
      border-bottom: 1px solid var(--border);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 8px;
    }
    header h1 { font-size: 18px; font-weight: 600; }
    .header-right { display: flex; align-items: center; gap: 12px; }
    header .status { font-size: 13px; color: var(--text-dim); }
    header .status .dot {
      display: inline-block;
      width: 8px; height: 8px;
      border-radius: 50%;
      margin-right: 4px;
      vertical-align: middle;
    }
    .dot-green { background: var(--green); }
    .dot-red { background: var(--red); }
    .dot-yellow { background: var(--yellow); }
    .dot-orange { background: var(--orange); }
    /* Grid layout */
    .grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 16px;
      padding: 16px 24px;
    }
    @media (max-width: 1200px) {
      .grid { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 700px) {
      .grid { grid-template-columns: 1fr; }
    }
    .card {
      background: var(--bg2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
    }
    .card h2 {
      font-size: 14px;
      color: var(--text-dim);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .card.span-2 { grid-column: span 2; }
    .card.span-3 { grid-column: 1 / -1; }
    @media (max-width: 700px) {
      .card.span-2, .card.span-3 { grid-column: 1 / -1; }
    }
    .stats { display: flex; gap: 24px; flex-wrap: wrap; }
    .stat { display: flex; flex-direction: column; }
    .stat .value { font-size: 24px; font-weight: 600; }
    .stat .label { font-size: 12px; color: var(--text-dim); margin-top: 2px; }
    .positive { color: var(--green); }
    .negative { color: var(--red); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th {
      text-align: left; padding: 6px 8px;
      border-bottom: 1px solid var(--border);
      color: var(--text-dim); font-weight: 500;
    }
    td { padding: 6px 8px; border-bottom: 1px solid var(--border); }
    tr:hover td { background: rgba(88,166,255,0.04); }
    .badge {
      display: inline-block; padding: 2px 8px;
      border-radius: 12px; font-size: 11px; font-weight: 500;
    }
    .badge-buy { background: rgba(63,185,80,0.15); color: var(--green); }
    .badge-sell { background: rgba(248,81,73,0.15); color: var(--red); }
    .badge-pending { background: rgba(210,153,34,0.15); color: var(--yellow); }
    .badge-filled { background: rgba(63,185,80,0.15); color: var(--green); }
    .badge-info { background: rgba(88,166,255,0.15); color: var(--accent); }
    .badge-warning { background: rgba(210,153,34,0.15); color: var(--yellow); }
    .badge-critical { background: rgba(248,81,73,0.15); color: var(--red); }
    .badge-connected { background: rgba(63,185,80,0.15); color: var(--green); }
    .badge-degraded { background: rgba(210,153,34,0.15); color: var(--yellow); }
    .badge-disconnected { background: rgba(248,81,73,0.15); color: var(--red); }
    .badge-fresh { background: rgba(63,185,80,0.15); color: var(--green); }
    .empty { color: var(--text-dim); text-align: center; padding: 24px; }
    /* Buttons */
    .btn {
      background: none; border: 1px solid var(--border);
      color: var(--text); padding: 4px 12px;
      border-radius: 6px; cursor: pointer; font-size: 12px;
    }
    .btn:hover { border-color: var(--accent); color: var(--accent); }
    .btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .btn-primary:hover { opacity: 0.9; }
    .btn-danger { border-color: var(--red); color: var(--red); }
    .btn-danger:hover { background: rgba(248,81,73,0.15); }
    .btn:disabled { opacity: 0.4; cursor: not-allowed; pointer-events: none; }
    .btn-group { display: flex; gap: 6px; }
    /* Mode banner */
    .mode-banner {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 4px 14px; border-radius: 6px;
      font-size: 13px; font-weight: 600; letter-spacing: 0.5px;
    }
    .mode-paper {
      background: rgba(210,153,34,0.15); color: var(--yellow);
      border: 1px solid rgba(210,153,34,0.3);
    }
    .mode-live {
      background: rgba(63,185,80,0.15); color: var(--green);
      border: 1px solid rgba(63,185,80,0.3);
    }
    .mode-banner .mode-dot {
      width: 8px; height: 8px; border-radius: 50%;
      animation: pulse 2s infinite;
    }
    .mode-paper .mode-dot { background: var(--yellow); }
    .mode-live .mode-dot { background: var(--green); }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    /* Chart */
    .chart-container { position: relative; }
    .chart-container canvas { width: 100%; height: 100%; display: block; }
    .chart-toolbar { display:flex; gap:6px; align-items:center; margin-bottom:8px; flex-wrap:wrap; }
    .chart-toolbar .btn { font-size:11px; padding:2px 8px; }
    .chart-toolbar .range-btn { font-size:11px; padding:2px 8px; background:none; border:1px solid var(--border); color:var(--text-dim); border-radius:4px; cursor:pointer; }
    .chart-toolbar .range-btn:hover, .chart-toolbar .range-btn.active { border-color:var(--accent); color:var(--accent); }
    .chart-tooltip { position:absolute; display:none; background:var(--bg2); border:1px solid var(--border); border-radius:6px; padding:8px 12px; font-size:12px; pointer-events:none; z-index:10; white-space:nowrap; box-shadow:0 4px 12px rgba(0,0,0,0.4); }
    .chart-tooltip .tt-date { color:var(--text); font-weight:600; margin-bottom:4px; }
    .chart-tooltip .tt-row { display:flex; justify-content:space-between; gap:16px; }
    .chart-tooltip .tt-label { color:var(--text-dim); }
    .chart-tooltip .tt-val { font-family:monospace; }
    .chart-legend { display:flex; gap:12px; flex-wrap:wrap; font-size:11px; margin-top:4px; }
    .chart-legend-item { display:flex; align-items:center; gap:4px; cursor:pointer; opacity:0.9; }
    .chart-legend-item:hover { opacity:1; }
    .chart-legend-dot { width:10px; height:3px; border-radius:1px; }
    /* Alerts */
    .alert-item {
      padding: 8px 12px;
      border-left: 3px solid var(--border);
      margin-bottom: 6px;
      font-size: 13px;
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 8px;
    }
    .alert-item.sev-critical { border-left-color: var(--red); }
    .alert-item.sev-warning { border-left-color: var(--yellow); }
    .alert-item.sev-info { border-left-color: var(--accent); }
    .alert-time { color: var(--text-dim); font-size: 11px; white-space: nowrap; }
    .alert-msg { flex: 1; }
    .alert-actions { display: flex; gap: 4px; margin-top: 4px; flex-wrap: wrap; }
    .alert-actions .btn { font-size: 11px; padding: 2px 8px; }
    .alert-actions .btn-buy { border-color: var(--green); color: var(--green); }
    .alert-actions .btn-buy:hover { background: rgba(63,185,80,0.15); }
    .alert-actions .btn-sell { border-color: var(--red); color: var(--red); }
    .alert-actions .btn-sell:hover { background: rgba(248,81,73,0.15); }
    .alert-content { display: flex; flex-direction: column; flex: 1; gap: 4px; }
    /* Quick order modal */
    .modal-overlay { display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.6); z-index:1000; justify-content:center; align-items:center; }
    .modal-overlay.active { display:flex; }
    .modal { background:var(--bg2); border:1px solid var(--border); border-radius:12px; padding:20px; width:360px; max-width:90vw; }
    .modal h3 { margin-bottom: 12px; font-size: 16px; }
    .modal-field { margin-bottom: 10px; }
    .modal-field label { display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 4px; }
    .modal-field input, .modal-field select { width: 100%; padding: 6px 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 13px; }
    .modal-field input:focus, .modal-field select:focus { outline: none; border-color: var(--accent); }
    .modal-row { display: flex; gap: 8px; }
    .modal-row .modal-field { flex: 1; }
    .modal-footer { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
    .modal-info { font-size: 12px; color: var(--text-dim); padding: 6px 8px; background: rgba(88,166,255,0.06); border-radius: 6px; margin-bottom: 10px; }
    /* Health strip */
    .health-strip { display: flex; gap: 16px; flex-wrap: wrap; }
    .health-item {
      display: flex; align-items: center; gap: 6px;
      font-size: 13px;
    }
    .toast{position:fixed;bottom:20px;right:20px;background:var(--bg2);border:1px solid var(--border);padding:10px 18px;border-radius:8px;font-size:13px;opacity:0;transition:opacity .3s;z-index:999;pointer-events:none}
    .toast.show{opacity:1}
    .breadcrumb{background:var(--bg2);padding:6px 24px;font-size:12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:0;flex-wrap:wrap}
    .breadcrumb a{color:var(--accent);text-decoration:none}
    .breadcrumb a:hover{text-decoration:underline}
    .breadcrumb .sep{color:var(--text-dim);margin:0 8px;user-select:none}
    .breadcrumb .current{color:var(--text)}
  </style>
</head>
<body>
  <div class="breadcrumb"><a href="${baseUrl}/quant/">🏠 首页</a><span class="sep">/</span><span class="current">📊 交易仪表盘</span></div>
  <header>
    <h1><a href="${baseUrl}/quant/" style="color:inherit;text-decoration:none">OpenClaw 量化交易</a></h1>
    <div class="header-right">
      <select id="modeSwitch" class="mode-banner mode-paper" onchange="switchViewMode(this.value)" style="cursor:pointer;appearance:none;-webkit-appearance:none;padding:4px 28px 4px 14px;background-image:url('data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 viewBox=%220 0 12 12%22><path d=%22M3 5l3 3 3-3%22 fill=%22%238b949e%22/></svg>');background-repeat:no-repeat;background-position:right 8px center;">
        <option value="">当前模式</option>
        <option value="paper">🟡 模拟盘</option>
        <option value="live">🟢 实盘</option>
      </select>
      <span id="modeBanner" class="mode-banner mode-paper" style="display:none"><span class="mode-dot"></span>加载中...</span>
      <div class="btn-group" id="engineControls">
        <button class="btn btn-primary" onclick="engineAction('start')" id="btnStart" disabled>启动</button>
        <button class="btn" onclick="engineAction('pause')" id="btnPause" disabled>暂停</button>
        <button class="btn" onclick="engineAction('resume')" id="btnResume" disabled>恢复</button>
        <button class="btn btn-danger" onclick="engineAction('stop')" id="btnStop" disabled>停止</button>
      </div>
      <button class="btn" onclick="refreshAll()">刷新</button>
      <span class="status" id="sseStatus" title="SSE 连接状态">
        <span class="dot dot-orange"></span>轮询
      </span>
      <span class="status" id="engineStatus">
        <span class="dot dot-red"></span>加载中...
      </span>
    </div>
  </header>

  <!-- Health strip -->
  <div style="padding: 8px 24px; display: flex; gap: 24px; flex-wrap: wrap; border-bottom: 1px solid var(--border); background: var(--bg2);">
    <div class="health-item" id="brokerHealth"><span class="dot dot-red"></span>Broker: --</div>
    <div class="health-item" id="dataHealth"><span class="dot dot-red"></span>数据: --</div>
    <div class="health-item" id="alertSummary">告警: --</div>
  </div>

  <div class="grid">
    <!-- Portfolio -->
    <div class="card span-3" id="portfolioCard">
      <h2>投资组合</h2>
      <div class="stats" id="portfolioStats">
        <div class="stat"><span class="value">--</span><span class="label">总资产</span></div>
      </div>
    </div>

    <!-- Equity chart -->
    <div class="card span-2">
      <h2>收益曲线
        <span id="equitySummary" style="font-size:12px;font-weight:400;color:var(--text-dim)"></span>
        <select id="benchmarkSelect" style="font-size:11px;padding:2px 6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);border-radius:4px;margin-left:8px;" onchange="loadBenchmarkOverlay()">
          <option value="">无基准</option>
          <option value="csi300" selected>沪深300</option>
          <option value="csi500">中证500</option>
          <option value="sse">上证综指</option>
          <option value="spy">S&P 500</option>
        </select>
      </h2>
      <div class="chart-toolbar" id="chartToolbar" style="display:none;">
        <button class="range-btn active" onclick="setChartRange(0)" data-range="0">全部</button>
        <button class="range-btn" onclick="setChartRange(30)" data-range="30">30天</button>
        <button class="range-btn" onclick="setChartRange(60)" data-range="60">60天</button>
        <button class="range-btn" onclick="setChartRange(90)" data-range="90">90天</button>
        <span style="flex:1"></span>
        <label style="font-size:11px;color:var(--text-dim);display:flex;align-items:center;gap:4px;cursor:pointer;">
          <input type="checkbox" id="chkDrawdown" onchange="toggleDrawdown()" checked style="accent-color:var(--red)"> 回撤区间
        </label>
        <label style="font-size:11px;color:var(--text-dim);display:flex;align-items:center;gap:4px;cursor:pointer;">
          <input type="checkbox" id="chkEvents" onchange="toggleEvents()" checked style="accent-color:var(--accent)"> 事件标注
        </label>
        <button class="btn" onclick="resetChartZoom()" title="重置缩放">重置</button>
      </div>
      <div class="chart-container" id="equityChart" style="height:220px;"><div class="empty">暂无数据</div></div>
      <div class="chart-tooltip" id="chartTooltip"></div>
      <div class="chart-legend" id="chartLegend"></div>
      <div id="benchmarkStats" style="font-size:12px;color:var(--text-dim);margin-top:6px;"></div>
    </div>

    <!-- Daily P&L -->
    <div class="card">
      <h2>今日盈亏</h2>
      <div id="pnlCard"><div class="empty">--</div></div>
    </div>

    <!-- Positions -->
    <div class="card">
      <h2>持仓</h2>
      <div id="positionsTable"><div class="empty">暂无持仓</div></div>
    </div>

    <!-- Orders -->
    <div class="card">
      <h2>今日委托</h2>
      <div id="ordersTable"><div class="empty">暂无委托</div></div>
    </div>

    <!-- Alerts -->
    <div class="card">
      <h2>最近告警 <button class="btn" style="font-size:11px;padding:2px 8px;" onclick="ackAll()">全部已读</button></h2>
      <div id="alertsList"><div class="empty">暂无告警</div></div>
    </div>

    <!-- Signals -->
    <div class="card">
      <h2>最近信号</h2>
      <div id="signalsTable"><div class="empty">暂无信号</div></div>
    </div>

    <!-- Staleness -->
    <div class="card">
      <h2>数据源状态</h2>
      <div id="stalenessTable"><div class="empty">暂无数据</div></div>
    </div>

    <!-- Price Alerts -->
    <div class="card">
      <h2>条件预警</h2>
      <div id="priceAlertsPanel"><div class="empty">暂无预警</div></div>
    </div>

    <!-- Scheduled Tasks -->
    <div class="card">
      <h2>定时任务</h2>
      <div id="scheduledTasksPanel"><div class="empty">暂无定时任务</div></div>
    </div>

    <!-- Backtest History -->
    <div class="card">
      <h2>回测记录</h2>
      <div id="backtestHistoryPanel"><div class="empty">暂无回测记录</div></div>
    </div>
  </div>

  <div class="toast" id="toast"></div>

  <!-- Quick Order Modal -->
  <div class="modal-overlay" id="orderModal" onclick="if(event.target===this)closeOrderModal()">
    <div class="modal">
      <h3 id="orderModalTitle">快速下单</h3>
      <div class="modal-info" id="orderModalInfo"></div>
      <div class="modal-row">
        <div class="modal-field"><label>标的代码</label><input id="omSymbol" readonly></div>
        <div class="modal-field"><label>方向</label><select id="omSide"><option value="buy">买入</option><option value="sell">卖出</option></select></div>
      </div>
      <div class="modal-row">
        <div class="modal-field"><label>类型</label><select id="omType" onchange="toggleLimitPrice()"><option value="market">市价</option><option value="limit">限价</option></select></div>
        <div class="modal-field" id="omPriceField"><label>价格</label><input id="omPrice" type="number" step="0.01" placeholder="限价价格"></div>
      </div>
      <div class="modal-field"><label>数量（股）</label><input id="omQty" type="number" step="100" min="100" value="100"></div>
      <div class="modal-footer">
        <button class="btn" onclick="closeOrderModal()">取消</button>
        <button class="btn btn-primary" id="omSubmit" onclick="submitQuickOrder()">确认下单</button>
      </div>
    </div>
  </div>

  <script>
    const BASE = ${JSON.stringify(baseUrl)};
    // ── View mode: paper / live / '' (auto) ─────────────────
    let viewMode = ''; // '' = auto (follow active engine)
    function switchViewMode(mode) {
      viewMode = mode;
      const sel = document.getElementById('modeSwitch');
      sel.className = 'mode-banner ' + (mode === 'live' ? 'mode-live' : 'mode-paper');
      refreshAll();
    }
    function modeParam(prefix) {
      if (!viewMode) return '';
      return (prefix ? '&' : '?') + 'mode=' + viewMode;
    }
    async function api(path, opts) {
      try { const r = await fetch(BASE + path, opts); return await r.json(); }
      catch(e) { console.error('API error:', path, e); return null; }
    }
    function fmt(n) {
      if (n == null) return '--';
      return Number(n).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2});
    }
    function pctClass(n) { return n > 0 ? 'positive' : n < 0 ? 'negative' : ''; }
    /** Escape HTML special characters to prevent XSS */
    function esc(s) {
      if (s == null) return '';
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;');
    }
    function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}

    // ── SSE Manager — real-time event subscription ──────────
    const SSE = {
      source: null,
      reconnectMs: 1000,
      maxReconnectMs: 30000,
      reconnectTimer: null,
      connected: false,

      connect() {
        if (this.source) this.source.close();
        const url = BASE + '/quant/api/events?filter=signal,order,engine,alert,pnl,position,equity';
        this.source = new EventSource(url);

        this.source.onopen = () => {
          this.connected = true;
          this.reconnectMs = 1000; // reset backoff
          this.updateIndicator('connected');
          console.log('[SSE] connected');
        };

        this.source.onmessage = (e) => {
          try {
            const evt = JSON.parse(e.data);
            this.handleEvent(evt);
          } catch(err) { console.warn('[SSE] parse error:', err); }
        };

        this.source.onerror = () => {
          this.connected = false;
          this.updateIndicator('disconnected');
          this.source.close();
          this.source = null;
          // Exponential backoff reconnect
          this.reconnectTimer = setTimeout(() => this.connect(), this.reconnectMs);
          this.reconnectMs = Math.min(this.reconnectMs * 2, this.maxReconnectMs);
          console.log('[SSE] reconnecting in ' + this.reconnectMs/1000 + 's');
        };
      },

      handleEvent(evt) {
        const cat = evt.category;
        const data = evt.data;

        if (cat === 'engine') {
          // Engine status changed — refresh status panel
          loadStatus();
        } else if (cat === 'signal') {
          // New signal — prepend to signals table + refresh
          loadSignals();
        } else if (cat === 'order') {
          // Order update — refresh orders
          loadOrders();
          loadPortfolio(); // positions may have changed
        } else if (cat === 'alert') {
          // New alert — refresh alerts panel
          loadAlerts();
          // Flash header indicator
          this.flashBadge('alertSummary');
        } else if (cat === 'pnl') {
          loadPnl();
        } else if (cat === 'position') {
          loadPortfolio();
        } else if (cat === 'equity') {
          loadEquityChart();
        }
      },

      updateIndicator(state) {
        const el = document.getElementById('sseStatus');
        if (!el) return;
        if (state === 'connected') {
          el.innerHTML = '<span class="dot dot-green"></span>实时';
          el.title = 'SSE 实时连接正常';
        } else {
          el.innerHTML = '<span class="dot dot-orange"></span>轮询';
          el.title = 'SSE 断开，回退到轮询模式';
        }
      },

      flashBadge(id) {
        const el = document.getElementById(id);
        if (!el) return;
        el.style.transition = 'background 0.3s';
        el.style.background = 'rgba(248,81,73,0.2)';
        setTimeout(() => { el.style.background = ''; }, 1500);
      },

      close() {
        if (this.source) { this.source.close(); this.source = null; }
        if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); }
        this.connected = false;
      }
    };

    // ── Engine controls ─────────────────────────────────────
    async function engineAction(action) {
      var labels={start:'启动',stop:'停止',pause:'暂停',resume:'恢复'};
      var el=document.getElementById('btn'+action.charAt(0).toUpperCase()+action.slice(1));
      if(el)el.disabled=true;
      try{
        var r=await api('/quant/api/engine/' + action, { method: 'POST' });
        toast('✅ '+(r&&r.message?r.message:(labels[action]||action)+'成功'));
      }catch(e){toast('❌ '+(labels[action]||action)+'失败');}
      setTimeout(refreshAll, 500);
    }

    // ── Portfolio ───────────────────────────────────────────
    async function loadPortfolio() {
      const r = await api('/quant/api/portfolio' + modeParam(false)); if (!r?.data) return;
      const d = r.data, cash = d.cash??0, tv = d.totalValue??cash;
      const pnl = d.profit!=null ? d.profit : tv-(d.initialCash??100000);
      const pct = d.profitPct!=null ? d.profitPct*100 : (d.initialCash ? pnl/(d.initialCash)*100 : 0);
      const isPaper = r.source==='paper';
      const isLive = r.source==='qmt'||r.source==='remote-qmt'||r.source==='live';
      const srcLabel = isLive ? ' (QMT实盘)' : isPaper ? ' (模拟)' : '';
      // Auto-select the mode dropdown when in auto mode
      const sel = document.getElementById('modeSwitch');
      if (!viewMode && sel) {
        sel.value = isPaper ? 'paper' : isLive ? 'live' : '';
        sel.className = 'mode-banner ' + (isLive ? 'mode-live' : 'mode-paper');
      }
      document.getElementById('portfolioStats').innerHTML =
        '<div class="stat"><span class="value">¥'+fmt(tv)+'</span><span class="label">总资产'+srcLabel+'</span></div>'+
        '<div class="stat"><span class="value">¥'+fmt(cash)+'</span><span class="label">可用资金</span></div>'+
        '<div class="stat"><span class="value '+pctClass(pnl)+'">¥'+fmt(pnl)+'</span><span class="label">浮动盈亏</span></div>'+
        '<div class="stat"><span class="value '+pctClass(pct)+'">'+fmt(pct)+'%</span><span class="label">收益率</span></div>';
      const positions = d.positions??[];
      if (!positions.length) { document.getElementById('positionsTable').innerHTML='<div class="empty">暂无持仓</div>'; return; }
      let h='<table><tr><th>代码</th><th>持仓</th><th>成本</th><th>现价</th><th>盈亏</th></tr>';
      for(const p of positions){const hasPrice=p.currentPrice!=null&&p.avgCost!=null;const u=hasPrice?(p.currentPrice-p.avgCost)*(p.quantity??0):null;
        h+='<tr><td>'+esc(p.symbol)+'</td><td>'+(p.quantity??0)+'</td><td>¥'+fmt(p.avgCost)+'</td><td>'+(p.currentPrice!=null?'¥'+fmt(p.currentPrice):'¥--')+'</td><td class="'+(u!=null?pctClass(u):'')+'">'+( u!=null?'¥'+fmt(u):'¥--')+'</td></tr>';}
      document.getElementById('positionsTable').innerHTML=h+'</table>';
    }

    // ── Orders ──────────────────────────────────────────────
    async function loadOrders() {
      const r = await api('/quant/api/orders' + modeParam(false)); const orders=r?.data??[];
      if(!orders.length){document.getElementById('ordersTable').innerHTML='<div class="empty">暂无委托</div>';return;}
      let h='<table><tr><th>时间</th><th>代码</th><th>方向</th><th>数量</th><th>价格</th><th>状态</th></tr>';
      for(const o of orders.slice(-20)){
        const side=o.side==='buy'?'<span class="badge badge-buy">买入</span>':'<span class="badge badge-sell">卖出</span>';
        const st=o.status==='filled'?'<span class="badge badge-filled">已成</span>':'<span class="badge badge-pending">'+esc(o.status??'待处理')+'</span>';
        h+='<tr><td>'+esc(o.time)+'</td><td>'+esc(o.symbol)+'</td><td>'+side+'</td><td>'+(o.quantity??'')+'</td><td>¥'+fmt(o.price)+'</td><td>'+st+'</td></tr>';}
      document.getElementById('ordersTable').innerHTML=h+'</table>';
    }

    // ── Signals ─────────────────────────────────────────────
    async function loadSignals() {
      const r = await api('/quant/api/signals?limit=20' + modeParam(true)); const sigs=r?.data??[];
      if(!sigs.length){document.getElementById('signalsTable').innerHTML='<div class="empty">暂无信号</div>';return;}
      let h='<table><tr><th>日期</th><th>策略</th><th>代码</th><th>方向</th><th>价格</th></tr>';
      for(const s of sigs.slice(-15)){
        const dir=s.direction==='buy'?'<span class="badge badge-buy">买</span>':'<span class="badge badge-sell">卖</span>';
        const dt=s.date??(s.timestamp?new Date(s.timestamp).toLocaleDateString():'');
        h+='<tr><td>'+esc(dt)+'</td><td>'+esc(s.strategyId)+'</td><td>'+esc(s.symbol)+'</td><td>'+dir+'</td><td>¥'+fmt(s.price)+'</td></tr>';}
      document.getElementById('signalsTable').innerHTML=h+'</table>';
    }

    // ── Engine status ───────────────────────────────────────
    async function loadStatus() {
      const r = await api('/quant/api/live/status'); const d=r?.data;
      const s=(typeof d==='string'?d:(d&&d.engineStatus))||'stopped';
      const dc=s==='running'?'dot-green':s==='paused'?'dot-yellow':'dot-red';
      const lb={running:'运行中',paused:'已暂停',stopped:'已停止',starting:'启动中',error:'错误'}[s]??s;
      document.getElementById('engineStatus').innerHTML='<span class="dot '+dc+'"></span>'+lb;
      // Enable/disable control buttons
      document.getElementById('btnStart').disabled = (s==='running'||s==='starting');
      document.getElementById('btnPause').disabled = (s!=='running');
      document.getElementById('btnResume').disabled = (s!=='paused');
      document.getElementById('btnStop').disabled = (s==='stopped');
    }

    // ── Daily P&L ───────────────────────────────────────────
    async function loadPnl() {
      const r = await api('/quant/api/pnl' + modeParam(false)); const d=r?.data;
      if(!d||d.netPnl==null){document.getElementById('pnlCard').innerHTML='<div class="empty">暂无数据</div>';return;}
      document.getElementById('pnlCard').innerHTML=
        '<div class="stats"><div class="stat"><span class="value '+pctClass(d.netPnl)+'">¥'+fmt(d.netPnl)+'</span><span class="label">'+(d.date??'今日')+' 净盈亏</span></div></div>';
    }

    // ── Equity chart (Interactive Canvas) ─────────────────────
    // Chart state
    var EC = {
      canvas: null, ctx: null,
      fullData: [],        // full equity history [{x,y,cash,marketValue,dailyPnl,positionCount}]
      overlayData: [],     // benchmark overlay [{x,y}]
      eventsData: [],      // key events [{date,type,label}]
      drawdownRanges: [],  // [{startIdx,endIdx,depth}]
      rangeStart: 0, rangeEnd: 0,  // visible window indices into fullData
      isDragging: false, dragStartX: 0, dragRangeStart: 0, dragRangeEnd: 0,
      isSelecting: false, selectStartX: 0, selectEndX: 0,
      showDrawdown: true, showEvents: true,
      hoverIdx: -1,
      dpr: 1
    };

    function initEquityCanvas() {
      var container = document.getElementById('equityChart');
      container.innerHTML = '<canvas id="equityCanvas"></canvas>';
      EC.canvas = document.getElementById('equityCanvas');
      EC.dpr = window.devicePixelRatio || 1;
      var rect = container.getBoundingClientRect();
      EC.canvas.width = rect.width * EC.dpr;
      EC.canvas.height = rect.height * EC.dpr;
      EC.canvas.style.width = rect.width + 'px';
      EC.canvas.style.height = rect.height + 'px';
      EC.ctx = EC.canvas.getContext('2d');
      EC.ctx.scale(EC.dpr, EC.dpr);

      // Mouse events for pan, zoom, hover, range-select
      EC.canvas.addEventListener('wheel', onChartWheel, {passive: false});
      EC.canvas.addEventListener('mousedown', onChartMouseDown);
      EC.canvas.addEventListener('mousemove', onChartMouseMove);
      EC.canvas.addEventListener('mouseup', onChartMouseUp);
      EC.canvas.addEventListener('mouseleave', onChartMouseLeave);
      // Touch events for mobile
      EC.canvas.addEventListener('touchstart', onChartTouchStart, {passive:false});
      EC.canvas.addEventListener('touchmove', onChartTouchMove, {passive:false});
      EC.canvas.addEventListener('touchend', onChartTouchEnd);
    }

    function computeDrawdownRanges(data) {
      var ranges = [];
      var peak = -Infinity, peakIdx = 0;
      var inDD = false, ddStart = 0, ddPeak = 0;
      for (var i = 0; i < data.length; i++) {
        if (data[i].y > peak) { peak = data[i].y; peakIdx = i; }
        var dd = (peak - data[i].y) / peak;
        if (dd > 0.02 && !inDD) { inDD = true; ddStart = peakIdx; ddPeak = peak; }
        if (inDD && (data[i].y >= ddPeak || i === data.length - 1)) {
          var maxDepth = 0;
          for (var j = ddStart; j <= i; j++) { var d2 = (ddPeak - data[j].y) / ddPeak; if (d2 > maxDepth) maxDepth = d2; }
          if (maxDepth > 0.03) { ranges.push({startIdx: ddStart, endIdx: i, depth: maxDepth}); }
          inDD = false;
        }
      }
      EC.drawdownRanges = ranges;
    }

    function collectEvents(data) {
      var events = [];
      if (!data.length) return events;
      // Max drawdown point
      var peak = -Infinity, maxDDVal = 0, maxDDIdx = 0;
      for (var i = 0; i < data.length; i++) {
        if (data[i].y > peak) peak = data[i].y;
        var dd = peak - data[i].y;
        if (dd > maxDDVal) { maxDDVal = dd; maxDDIdx = i; }
      }
      if (maxDDVal > 0) events.push({idx: maxDDIdx, type: 'maxdd', label: '最大回撤', color: '#f85149'});
      // Max value point
      var maxVal = -Infinity, maxIdx = 0;
      for (var i = 0; i < data.length; i++) { if (data[i].y > maxVal) { maxVal = data[i].y; maxIdx = i; } }
      events.push({idx: maxIdx, type: 'peak', label: '最高点', color: '#3fb950'});
      // Min value point
      var minVal = Infinity, minIdx = 0;
      for (var i = 0; i < data.length; i++) { if (data[i].y < minVal) { minVal = data[i].y; minIdx = i; } }
      if (minIdx !== maxDDIdx) events.push({idx: minIdx, type: 'trough', label: '最低点', color: '#d29922'});
      // Large daily changes (>3%)
      for (var i = 1; i < data.length; i++) {
        var chg = (data[i].y - data[i-1].y) / data[i-1].y;
        if (chg > 0.03) events.push({idx: i, type: 'surge', label: '+' + (chg*100).toFixed(1) + '%', color: '#3fb950'});
        else if (chg < -0.03) events.push({idx: i, type: 'drop', label: (chg*100).toFixed(1) + '%', color: '#f85149'});
      }
      EC.eventsData = events;
      return events;
    }

    function drawChart() {
      if (!EC.canvas || !EC.ctx || !EC.fullData.length) return;
      var canvas = EC.canvas;
      var ctx = EC.ctx;
      var W = canvas.width / EC.dpr, H = canvas.height / EC.dpr;
      var padL = 62, padR = 14, padT = 14, padB = 28;
      var cw = W - padL - padR, ch = H - padT - padB;

      // Clear
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = '#0d1117';
      ctx.fillRect(0, 0, W, H);

      var data = EC.fullData;
      var s = EC.rangeStart, e = EC.rangeEnd;
      if (s < 0) s = 0;
      if (e >= data.length) e = data.length - 1;
      if (e <= s) return;
      var visData = data.slice(s, e + 1);
      var visOverlay = [];
      if (EC.overlayData.length > 0) {
        // Align overlay to visible range by date
        var dateMap = {};
        EC.overlayData.forEach(function(d) { dateMap[d.x] = d.y; });
        visData.forEach(function(d) { if (dateMap[d.x] != null) visOverlay.push({x: d.x, y: dateMap[d.x]}); });
      }

      // Compute Y range
      var allY = visData.map(function(d){return d.y});
      if (visOverlay.length) visOverlay.forEach(function(d){allY.push(d.y)});
      var yMin = Math.min.apply(null, allY), yMax = Math.max.apply(null, allY);
      var yMargin = (yMax - yMin) * 0.06 || 1;
      yMin -= yMargin; yMax += yMargin;
      var yRange = yMax - yMin;

      function xPos(i) { return padL + cw * (i / Math.max(visData.length - 1, 1)); }
      function yPos(v) { return padT + ch * (1 - (v - yMin) / yRange); }

      // Grid lines
      ctx.strokeStyle = '#21262d'; ctx.lineWidth = 0.5;
      ctx.fillStyle = '#8b949e'; ctx.font = '10px sans-serif'; ctx.textAlign = 'right';
      for (var gi = 0; gi <= 4; gi++) {
        var gy = padT + ch * gi / 4;
        ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(W - padR, gy); ctx.stroke();
        var gv = yMax - yRange * gi / 4;
        ctx.fillText('¥' + fmt(gv), padL - 4, gy + 3);
      }

      // Drawdown highlighting
      if (EC.showDrawdown && EC.drawdownRanges.length) {
        EC.drawdownRanges.forEach(function(r) {
          var rs = Math.max(r.startIdx - s, 0);
          var re = Math.min(r.endIdx - s, visData.length - 1);
          if (rs >= visData.length || re < 0) return;
          var x1 = xPos(rs), x2 = xPos(re);
          var alpha = Math.min(0.25, r.depth * 2);
          ctx.fillStyle = 'rgba(248,81,73,' + alpha.toFixed(2) + ')';
          ctx.fillRect(x1, padT, x2 - x1, ch);
        });
      }

      // Range selection highlight
      if (EC.isSelecting) {
        var selX1 = Math.min(EC.selectStartX, EC.selectEndX);
        var selX2 = Math.max(EC.selectStartX, EC.selectEndX);
        ctx.fillStyle = 'rgba(88,166,255,0.15)';
        ctx.fillRect(selX1, padT, selX2 - selX1, ch);
        ctx.strokeStyle = 'rgba(88,166,255,0.5)'; ctx.lineWidth = 1;
        ctx.strokeRect(selX1, padT, selX2 - selX1, ch);
      }

      // Draw main equity line + fill
      var color = visData[visData.length - 1].y >= visData[0].y ? '#3fb950' : '#f85149';
      ctx.strokeStyle = color; ctx.lineWidth = 1.8;
      ctx.beginPath();
      for (var i = 0; i < visData.length; i++) {
        var x = xPos(i), y = yPos(visData[i].y);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
      // Area fill
      ctx.lineTo(xPos(visData.length - 1), padT + ch);
      ctx.lineTo(xPos(0), padT + ch);
      ctx.closePath();
      var hexR = parseInt(color.slice(1, 3), 16), hexG = parseInt(color.slice(3, 5), 16), hexB = parseInt(color.slice(5, 7), 16);
      var grad = ctx.createLinearGradient(0, padT, 0, padT + ch);
      grad.addColorStop(0, 'rgba(' + hexR + ',' + hexG + ',' + hexB + ',0.15)');
      grad.addColorStop(1, 'rgba(' + hexR + ',' + hexG + ',' + hexB + ',0.01)');
      ctx.fillStyle = grad;
      ctx.fill();

      // Draw overlay (benchmark)
      if (visOverlay.length >= 2) {
        ctx.strokeStyle = '#58a6ff'; ctx.lineWidth = 1.5; ctx.setLineDash([5, 3]);
        ctx.beginPath();
        for (var i = 0; i < visOverlay.length; i++) {
          // Find x position by matching date in visData
          var oi = -1;
          for (var vi = 0; vi < visData.length; vi++) { if (visData[vi].x === visOverlay[i].x) { oi = vi; break; } }
          if (oi < 0) continue;
          var x = xPos(oi), y = yPos(visOverlay[i].y);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Event annotations
      if (EC.showEvents && EC.eventsData.length) {
        EC.eventsData.forEach(function(ev) {
          var evIdx = ev.idx - s;
          if (evIdx < 0 || evIdx >= visData.length) return;
          var x = xPos(evIdx), y = yPos(visData[evIdx].y);
          // Marker
          ctx.fillStyle = ev.color;
          if (ev.type === 'maxdd' || ev.type === 'trough' || ev.type === 'drop') {
            // Downward triangle
            ctx.beginPath(); ctx.moveTo(x, y + 10); ctx.lineTo(x - 5, y + 3); ctx.lineTo(x + 5, y + 3); ctx.closePath(); ctx.fill();
          } else {
            // Upward triangle
            ctx.beginPath(); ctx.moveTo(x, y - 10); ctx.lineTo(x - 5, y - 3); ctx.lineTo(x + 5, y - 3); ctx.closePath(); ctx.fill();
          }
          // Label
          ctx.font = '9px sans-serif'; ctx.textAlign = 'center';
          ctx.fillText(ev.label, x, ev.type === 'maxdd' || ev.type === 'trough' || ev.type === 'drop' ? y + 20 : y - 14);
        });
      }

      // X-axis labels
      ctx.fillStyle = '#8b949e'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
      var labelCount = Math.min(6, visData.length);
      for (var li = 0; li < labelCount; li++) {
        var idx = Math.floor(li * (visData.length - 1) / Math.max(labelCount - 1, 1));
        ctx.fillText(visData[idx].x, xPos(idx), H - padB + 14);
      }

      // Crosshair & tooltip
      if (EC.hoverIdx >= 0 && EC.hoverIdx < visData.length) {
        var hi = EC.hoverIdx;
        var hx = xPos(hi), hy = yPos(visData[hi].y);
        // Vertical line
        ctx.strokeStyle = 'rgba(139,148,158,0.4)'; ctx.lineWidth = 0.5; ctx.setLineDash([3, 3]);
        ctx.beginPath(); ctx.moveTo(hx, padT); ctx.lineTo(hx, padT + ch); ctx.stroke();
        // Horizontal line
        ctx.beginPath(); ctx.moveTo(padL, hy); ctx.lineTo(W - padR, hy); ctx.stroke();
        ctx.setLineDash([]);
        // Dot
        ctx.beginPath(); ctx.arc(hx, hy, 4, 0, Math.PI * 2);
        ctx.fillStyle = color; ctx.fill();
        ctx.strokeStyle = '#0d1117'; ctx.lineWidth = 2; ctx.stroke();
        // Overlay dot
        if (visOverlay.length) {
          for (var vi = 0; vi < visOverlay.length; vi++) {
            if (visData[hi] && visOverlay[vi] && visData[hi].x === visOverlay[vi].x) {
              var oy = yPos(visOverlay[vi].y);
              ctx.beginPath(); ctx.arc(hx, oy, 4, 0, Math.PI * 2);
              ctx.fillStyle = '#58a6ff'; ctx.fill();
              ctx.strokeStyle = '#0d1117'; ctx.lineWidth = 2; ctx.stroke();
              break;
            }
          }
        }

        // Show tooltip
        var tip = document.getElementById('chartTooltip');
        var d = visData[hi];
        var html = '<div class="tt-date">' + esc(d.x) + '</div>';
        html += '<div class="tt-row"><span class="tt-label">净值</span><span class="tt-val">¥' + fmt(d.y) + '</span></div>';
        if (d.cash != null) html += '<div class="tt-row"><span class="tt-label">现金</span><span class="tt-val">¥' + fmt(d.cash) + '</span></div>';
        if (d.marketValue != null) html += '<div class="tt-row"><span class="tt-label">市值</span><span class="tt-val">¥' + fmt(d.marketValue) + '</span></div>';
        if (d.dailyPnl != null) html += '<div class="tt-row"><span class="tt-label">日盈亏</span><span class="tt-val ' + pctClass(d.dailyPnl) + '">¥' + fmt(d.dailyPnl) + '</span></div>';
        if (d.positionCount != null) html += '<div class="tt-row"><span class="tt-label">持仓数</span><span class="tt-val">' + d.positionCount + '</span></div>';
        // Overlay value
        if (visOverlay.length) {
          for (var vi = 0; vi < visOverlay.length; vi++) {
            if (visOverlay[vi].x === d.x) {
              html += '<div class="tt-row"><span class="tt-label" style="color:#58a6ff">基准</span><span class="tt-val" style="color:#58a6ff">¥' + fmt(visOverlay[vi].y) + '</span></div>';
              break;
            }
          }
        }
        tip.innerHTML = html;
        tip.style.display = 'block';
        // Position tooltip
        var containerRect = document.getElementById('equityChart').getBoundingClientRect();
        var canvasRect = EC.canvas.getBoundingClientRect();
        var tipX = hx + canvasRect.left - containerRect.left + 12;
        var tipY = hy + canvasRect.top - containerRect.top - 20;
        if (tipX + 180 > containerRect.width) tipX = hx + canvasRect.left - containerRect.left - 180;
        if (tipY < 0) tipY = 10;
        tip.style.left = tipX + 'px';
        tip.style.top = tipY + 'px';
      } else {
        document.getElementById('chartTooltip').style.display = 'none';
      }
    }

    // ── Chart interaction handlers ──────────────────────────────
    function getChartPad() { return {l:62,r:14,t:14,b:28}; }
    function canvasMouseIdx(e) {
      var rect = EC.canvas.getBoundingClientRect();
      var mx = e.clientX - rect.left;
      var pad = getChartPad();
      var cw = rect.width - pad.l - pad.r;
      var n = EC.rangeEnd - EC.rangeStart + 1;
      var idx = Math.round((mx - pad.l) / cw * (n - 1));
      return Math.max(0, Math.min(n - 1, idx));
    }

    function onChartWheel(e) {
      e.preventDefault();
      if (!EC.fullData.length) return;
      var rect = EC.canvas.getBoundingClientRect();
      var mx = e.clientX - rect.left;
      var pad = getChartPad();
      var cw = rect.width - pad.l - pad.r;
      var ratio = (mx - pad.l) / cw; // 0..1 position of mouse
      if (ratio < 0) ratio = 0; if (ratio > 1) ratio = 1;

      var range = EC.rangeEnd - EC.rangeStart;
      var delta = Math.sign(e.deltaY) * Math.max(1, Math.floor(range * 0.1));
      var newRange = range + delta;
      if (newRange < 5) newRange = 5;
      if (newRange > EC.fullData.length - 1) newRange = EC.fullData.length - 1;

      var newStart = Math.round(EC.rangeStart - (newRange - range) * ratio);
      var newEnd = newStart + newRange;
      if (newStart < 0) { newStart = 0; newEnd = newRange; }
      if (newEnd >= EC.fullData.length) { newEnd = EC.fullData.length - 1; newStart = newEnd - newRange; }
      if (newStart < 0) newStart = 0;

      EC.rangeStart = newStart;
      EC.rangeEnd = newEnd;
      drawChart();
    }

    function onChartMouseDown(e) {
      if (e.button === 2 || e.shiftKey) {
        // Right-click or shift: range selection
        EC.isSelecting = true;
        EC.selectStartX = e.clientX - EC.canvas.getBoundingClientRect().left;
        EC.selectEndX = EC.selectStartX;
      } else {
        EC.isDragging = true;
        EC.dragStartX = e.clientX;
        EC.dragRangeStart = EC.rangeStart;
        EC.dragRangeEnd = EC.rangeEnd;
      }
      EC.canvas.style.cursor = EC.isSelecting ? 'crosshair' : 'grabbing';
    }

    function onChartMouseMove(e) {
      if (!EC.fullData.length) return;
      if (EC.isDragging) {
        var dx = e.clientX - EC.dragStartX;
        var rect = EC.canvas.getBoundingClientRect();
        var pad = getChartPad();
        var cw = rect.width - pad.l - pad.r;
        var range = EC.dragRangeEnd - EC.dragRangeStart;
        var shift = Math.round(-dx / cw * range);
        var ns = EC.dragRangeStart + shift;
        var ne = EC.dragRangeEnd + shift;
        if (ns < 0) { ns = 0; ne = range; }
        if (ne >= EC.fullData.length) { ne = EC.fullData.length - 1; ns = ne - range; }
        if (ns < 0) ns = 0;
        EC.rangeStart = ns; EC.rangeEnd = ne;
        EC.hoverIdx = -1;
        drawChart();
      } else if (EC.isSelecting) {
        EC.selectEndX = e.clientX - EC.canvas.getBoundingClientRect().left;
        EC.hoverIdx = -1;
        drawChart();
      } else {
        EC.hoverIdx = canvasMouseIdx(e);
        EC.canvas.style.cursor = 'crosshair';
        drawChart();
      }
    }

    function onChartMouseUp(e) {
      if (EC.isSelecting) {
        EC.isSelecting = false;
        // Zoom to selected range
        var rect = EC.canvas.getBoundingClientRect();
        var pad = getChartPad();
        var cw = rect.width - pad.l - pad.r;
        var n = EC.rangeEnd - EC.rangeStart + 1;
        var x1 = Math.min(EC.selectStartX, EC.selectEndX) - pad.l;
        var x2 = Math.max(EC.selectStartX, EC.selectEndX) - pad.l;
        if (x2 - x1 > 10) {
          var newStart = EC.rangeStart + Math.floor(x1 / cw * (n - 1));
          var newEnd = EC.rangeStart + Math.ceil(x2 / cw * (n - 1));
          if (newStart < 0) newStart = 0;
          if (newEnd >= EC.fullData.length) newEnd = EC.fullData.length - 1;
          if (newEnd - newStart >= 3) { EC.rangeStart = newStart; EC.rangeEnd = newEnd; }
        }
        drawChart();
      }
      EC.isDragging = false;
      EC.isSelecting = false;
      EC.canvas.style.cursor = 'crosshair';
    }

    function onChartMouseLeave() {
      EC.isDragging = false;
      EC.isSelecting = false;
      EC.hoverIdx = -1;
      EC.canvas.style.cursor = 'default';
      document.getElementById('chartTooltip').style.display = 'none';
      drawChart();
    }

    var _touchLastDist = 0, _touchLastX = 0;
    function onChartTouchStart(e) {
      if (e.touches.length === 1) {
        EC.isDragging = true;
        EC.dragStartX = e.touches[0].clientX;
        EC.dragRangeStart = EC.rangeStart;
        EC.dragRangeEnd = EC.rangeEnd;
        e.preventDefault();
      } else if (e.touches.length === 2) {
        _touchLastDist = Math.abs(e.touches[0].clientX - e.touches[1].clientX);
        _touchLastX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        e.preventDefault();
      }
    }
    function onChartTouchMove(e) {
      if (e.touches.length === 1 && EC.isDragging) {
        var dx = e.touches[0].clientX - EC.dragStartX;
        var rect = EC.canvas.getBoundingClientRect();
        var pad = getChartPad();
        var cw = rect.width - pad.l - pad.r;
        var range = EC.dragRangeEnd - EC.dragRangeStart;
        var shift = Math.round(-dx / cw * range);
        var ns = EC.dragRangeStart + shift;
        var ne = EC.dragRangeEnd + shift;
        if (ns < 0) { ns = 0; ne = range; }
        if (ne >= EC.fullData.length) { ne = EC.fullData.length - 1; ns = ne - range; }
        if (ns < 0) ns = 0;
        EC.rangeStart = ns; EC.rangeEnd = ne;
        drawChart();
        e.preventDefault();
      } else if (e.touches.length === 2) {
        var dist = Math.abs(e.touches[0].clientX - e.touches[1].clientX);
        var scale = dist / (_touchLastDist || dist);
        var range = EC.rangeEnd - EC.rangeStart;
        var newRange = Math.round(range / scale);
        if (newRange < 5) newRange = 5;
        if (newRange > EC.fullData.length - 1) newRange = EC.fullData.length - 1;
        var mid = Math.floor((EC.rangeStart + EC.rangeEnd) / 2);
        EC.rangeStart = Math.max(0, mid - Math.floor(newRange / 2));
        EC.rangeEnd = Math.min(EC.fullData.length - 1, EC.rangeStart + newRange);
        _touchLastDist = dist;
        drawChart();
        e.preventDefault();
      }
    }
    function onChartTouchEnd() { EC.isDragging = false; }

    // ── Chart controls ─────────────────────────────────────────
    function setChartRange(days) {
      if (!EC.fullData.length) return;
      // Update active button
      document.querySelectorAll('.range-btn').forEach(function(b) { b.classList.remove('active'); });
      document.querySelector('.range-btn[data-range="' + days + '"]').classList.add('active');
      if (days === 0) {
        EC.rangeStart = 0;
        EC.rangeEnd = EC.fullData.length - 1;
      } else {
        EC.rangeEnd = EC.fullData.length - 1;
        EC.rangeStart = Math.max(0, EC.fullData.length - days);
      }
      drawChart();
    }
    function resetChartZoom() {
      setChartRange(0);
    }
    function toggleDrawdown() {
      EC.showDrawdown = document.getElementById('chkDrawdown').checked;
      drawChart();
    }
    function toggleEvents() {
      EC.showEvents = document.getElementById('chkEvents').checked;
      drawChart();
    }

    function updateChartLegend() {
      var html = '<div class="chart-legend-item"><div class="chart-legend-dot" style="background:' +
        (EC.fullData.length > 1 && EC.fullData[EC.fullData.length-1].y >= EC.fullData[0].y ? '#3fb950' : '#f85149') +
        '"></div>策略</div>';
      if (EC.overlayData.length) html += '<div class="chart-legend-item"><div class="chart-legend-dot" style="background:#58a6ff;border-style:dashed;border-width:1px;border-color:#58a6ff;height:0;width:10px;"></div>基准</div>';
      if (EC.showDrawdown) html += '<div class="chart-legend-item"><div class="chart-legend-dot" style="background:rgba(248,81,73,0.3);width:10px;height:8px;"></div>回撤区间</div>';
      document.getElementById('chartLegend').innerHTML = html;
    }

    async function loadEquityChart() {
      var r = await api('/quant/api/equity-history?limit=120' + modeParam(true));
      var history = r && r.data;

      if (history && Array.isArray(history) && history.length >= 2) {
        document.getElementById('chartToolbar').style.display = 'flex';
        EC.fullData = history.map(function(h) {
          return {x: h.date, y: h.equity, cash: h.cash, marketValue: h.marketValue, dailyPnl: h.dailyPnl, positionCount: h.positionCount};
        });
        EC.rangeStart = 0;
        EC.rangeEnd = EC.fullData.length - 1;
        EC.overlayData = [];
        computeDrawdownRanges(EC.fullData);
        collectEvents(EC.fullData);
        initEquityCanvas();
        drawChart();
        updateChartLegend();

        // Load summary stats
        var sr = await api('/quant/api/equity-history/summary' + modeParam(false));
        var s = sr && sr.data;
        if (s && s.totalDays > 0) {
          var retCls = pctClass(s.totalReturnPct);
          document.getElementById('equitySummary').innerHTML =
            '<span class="' + retCls + '">' + fmt(s.totalReturnPct) + '%</span> · ' +
            '最大回撤 ' + fmt(s.maxDrawdownPct) + '% · ' +
            '胜率 ' + fmt(s.winRate) + '% · ' +
            s.totalDays + '天';
        }
        // Auto-load benchmark
        await loadBenchmarkOverlay();
        return;
      }

      // Single data point
      if (history && Array.isArray(history) && history.length === 1) {
        var h = history[0];
        document.getElementById('equityChart').innerHTML =
          '<div class="stats"><div class="stat"><span class="value">¥' + fmt(h.equity) + '</span><span class="label">' + h.date + ' 净值（首日，暂无趋势）</span></div></div>';
        document.getElementById('chartToolbar').style.display = 'none';
        return;
      }

      // Fallback: try pnl endpoint for legacy data
      var pr = await api('/quant/api/pnl' + modeParam(false)); var d = pr && pr.data;
      var legacyHistory = d && (d.equityHistory || d.history);
      if (legacyHistory && Array.isArray(legacyHistory) && legacyHistory.length >= 2) {
        document.getElementById('chartToolbar').style.display = 'flex';
        EC.fullData = legacyHistory.map(function(h) { return {x: h.date || h.timestamp, y: h.equity || h.value}; });
        EC.rangeStart = 0;
        EC.rangeEnd = EC.fullData.length - 1;
        EC.overlayData = [];
        computeDrawdownRanges(EC.fullData);
        collectEvents(EC.fullData);
        initEquityCanvas();
        drawChart();
        updateChartLegend();
        return;
      }

      // Last fallback: show current equity value
      var tv = d && (d.totalValue || d.equity);
      if (tv != null) {
        document.getElementById('equityChart').innerHTML =
          '<div class="stats"><div class="stat"><span class="value">¥' + fmt(tv) + '</span><span class="label">当前净值</span></div></div>';
      }
      document.getElementById('chartToolbar').style.display = 'none';
    }

    // ── Benchmark overlay ───────────────────────────────────
    async function loadBenchmarkOverlay() {
      var sel = document.getElementById('benchmarkSelect');
      var benchId = sel ? sel.value : '';
      if (!benchId || !EC.fullData.length) {
        EC.overlayData = [];
        updateChartLegend();
        drawChart();
        document.getElementById('benchmarkStats').innerHTML = '';
        return;
      }
      var r = await api('/quant/api/benchmark/compare?benchmark=' + benchId + modeParam(true));
      if (!r || r.status !== 'ok' || !r.data) {
        document.getElementById('benchmarkStats').innerHTML = '<span style="color:var(--text-dim)">基准数据不足</span>';
        EC.overlayData = [];
        updateChartLegend();
        drawChart();
        return;
      }
      var cmp = r.data;
      // Scale benchmark to same starting equity for visual comparison
      var first = EC.fullData[0].y;
      EC.overlayData = cmp.data.map(function(d) { return {x: d.date, y: first * d.normalizedReturn}; });
      updateChartLegend();
      drawChart();
      // Show stats
      var s = cmp.stats;
      var exCls = pctClass(s.excessReturn);
      document.getElementById('benchmarkStats').innerHTML =
        '策略: <span class="' + pctClass(s.strategyReturnPct) + '">' + fmt(s.strategyReturnPct) + '%</span> · ' +
        cmp.benchmark.name + ': <span class="' + pctClass(s.benchmarkReturnPct) + '">' + fmt(s.benchmarkReturnPct) + '%</span> · ' +
        '超额: <span class="' + exCls + '">' + fmt(s.excessReturn) + '%</span> · ' +
        'IR: ' + s.informationRatio.toFixed(2);
    }

    // ── Health / Staleness ──────────────────────────────────
    async function loadHealth() {
      const r = await api('/quant/api/health'); const d=r?.data;
      if (!d) { document.getElementById('brokerHealth').innerHTML='<span class="dot dot-red"></span>Broker: N/A'; return; }
      const dc = d.status==='connected'?'dot-green':d.status==='degraded'?'dot-yellow':'dot-red';
      const lb = {connected:'正常',degraded:'降级',disconnected:'断开'}[d.status]??d.status;
      let extra = '';
      if (d.consecutiveFailures > 0) extra = ' (失败'+d.consecutiveFailures+'次)';
      document.getElementById('brokerHealth').innerHTML='<span class="dot '+dc+'"></span>Broker: '+lb+extra;
    }

    async function loadStaleness() {
      const r = await api('/quant/api/staleness'); const d=r?.data;
      if (!d) { document.getElementById('dataHealth').innerHTML='<span class="dot dot-red"></span>数据: N/A'; return; }
      const dc = d.overallStatus==='fresh'?'dot-green':d.overallStatus==='warning'?'dot-yellow':d.overallStatus==='critical'?'dot-red':'dot-orange';
      const lb = {fresh:'正常',warning:'延迟',critical:'中断',no_data:'无数据'}[d.overallStatus]??d.overallStatus;
      document.getElementById('dataHealth').innerHTML='<span class="dot '+dc+'"></span>数据: '+lb+' ('+d.totalSymbols+'只)';

      // Staleness table
      if (!d.symbols || !d.symbols.length) {
        document.getElementById('stalenessTable').innerHTML='<div class="empty">暂无订阅</div>';
        return;
      }
      let h='<table><tr><th>标的</th><th>状态</th><th>延迟</th></tr>';
      for (const s of d.symbols) {
        const cls = 'badge-'+s.status;
        const lb2 = {fresh:'正常',warning:'延迟',critical:'中断'}[s.status]??s.status;
        const age = s.ageMs > 0 ? (s.ageMs/1000).toFixed(0)+'s' : 'N/A';
        h += '<tr><td>'+s.symbol+'</td><td><span class="badge '+cls+'">'+lb2+'</span></td><td>'+age+'</td></tr>';
      }
      document.getElementById('stalenessTable').innerHTML=h+'</table>';
    }

    // ── Alerts ──────────────────────────────────────────────
    async function loadAlerts() {
      const [alertsR, summaryR] = await Promise.all([
        api('/quant/api/alerts?limit=10&acknowledged=false'),
        api('/quant/api/alerts/summary'),
      ]);
      // Summary
      const sum = summaryR?.data;
      if (sum) {
        const parts = [];
        if (sum.bySeverity.critical > 0) parts.push('<span class="badge badge-critical">'+sum.bySeverity.critical+' 严重</span>');
        if (sum.bySeverity.warning > 0) parts.push('<span class="badge badge-warning">'+sum.bySeverity.warning+' 警告</span>');
        if (sum.unacknowledged > 0) parts.push(sum.unacknowledged + ' 未读');
        document.getElementById('alertSummary').innerHTML = '告警: ' + (parts.length ? parts.join(' ') : '无');
      }
      // List
      const alerts = alertsR?.data ?? [];
      if (!alerts.length) { document.getElementById('alertsList').innerHTML='<div class="empty">暂无未读告警</div>'; return; }
      let h = '';
      for (const a of alerts) {
        const t = new Date(a.timestamp).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'});
        const sym = a.metadata?.symbol ?? '';
        const price = a.metadata?.price ?? '';
        const changePct = a.metadata?.changePct ?? '';
        const hasSymbol = !!sym && sym !== '*';
        const actions = hasSymbol ? (
          '<div class="alert-actions">' +
          '<button class="btn" onclick="viewStock(\\''+esc(sym)+'\\')" title="查看 K线/行情">📈 行情</button>' +
          '<button class="btn btn-buy" onclick="openQuickOrder(\\''+esc(sym)+'\\',\\'buy\\',\\''+price+'\\',\\''+changePct+'\\')" title="快速买入">买入</button>' +
          '<button class="btn btn-sell" onclick="openQuickOrder(\\''+esc(sym)+'\\',\\'sell\\',\\''+price+'\\',\\''+changePct+'\\')" title="快速卖出">卖出</button>' +
          (a.metadata?.alertId ? '<button class="btn" onclick="dismissAlert(\\''+esc(a.metadata.alertId)+'\\')" title="停止该条件监控">停止监控</button>' : '') +
          '</div>'
        ) : '';
        h += '<div class="alert-item sev-'+a.severity+'">' +
          '<div class="alert-content"><span class="alert-msg">'+a.message+'</span>'+actions+'</div>' +
          '<span class="alert-time">'+t+'</span></div>';
      }
      document.getElementById('alertsList').innerHTML = h;
    }

    async function ackAll() {
      await api('/quant/api/alerts/acknowledge', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({all:true}) });
      loadAlerts();
    }

    // ── Alert Action Handlers ───────────────────────────────
    function viewStock(symbol) {
      window.open(BASE + '/quant/admin/stock?symbol=' + encodeURIComponent(symbol), '_blank');
    }

    function openQuickOrder(symbol, side, price, changePct) {
      document.getElementById('omSymbol').value = symbol;
      document.getElementById('omSide').value = side;
      document.getElementById('omType').value = 'limit';
      document.getElementById('omPrice').value = price || '';
      document.getElementById('omQty').value = '100';
      document.getElementById('omSubmit').disabled = false;
      const sideLabel = side === 'buy' ? '买入' : '卖出';
      document.getElementById('orderModalTitle').textContent = '快速' + sideLabel + ' ' + symbol;
      const pctStr = changePct != null ? (changePct >= 0 ? '+' : '') + Number(changePct).toFixed(2) + '%' : '';
      document.getElementById('orderModalInfo').innerHTML = '当前价: ¥' + (price ? Number(price).toFixed(2) : '--') + (pctStr ? '  涨跌: ' + pctStr : '');
      toggleLimitPrice();
      document.getElementById('orderModal').classList.add('active');
    }

    function closeOrderModal() {
      document.getElementById('orderModal').classList.remove('active');
    }

    function toggleLimitPrice() {
      const isLimit = document.getElementById('omType').value === 'limit';
      document.getElementById('omPriceField').style.display = isLimit ? '' : 'none';
    }

    async function submitQuickOrder() {
      const symbol = document.getElementById('omSymbol').value;
      const side = document.getElementById('omSide').value;
      const orderType = document.getElementById('omType').value;
      const quantity = parseInt(document.getElementById('omQty').value, 10);
      const price = orderType === 'limit' ? parseFloat(document.getElementById('omPrice').value) : undefined;
      if (!symbol || !quantity || quantity <= 0) { toast('请填写有效数量'); return; }
      if (orderType === 'limit' && (!price || price <= 0)) { toast('请填写有效价格'); return; }
      document.getElementById('omSubmit').disabled = true;
      const body = { symbol, side, orderType, quantity, mode: viewMode || undefined };
      if (price != null) body.price = price;
      // Warn if placing order in a mode different from active engine
      if (viewMode && viewMode !== '') {
        const confirmMsg = viewMode === 'live' ? '⚠️ 将通过实盘下单，确认？' : '将通过模拟盘下单，确认？';
        if (!confirm(confirmMsg)) { document.getElementById('omSubmit').disabled = false; return; }
      }
      const r = await api('/quant/api/orders' + modeParam(false), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (r?.status === 'ok') {
        toast(side === 'buy' ? '买入委托已提交' : '卖出委托已提交');
        closeOrderModal();
        loadOrders();
        loadPortfolio();
      } else {
        toast('下单失败: ' + (r?.message ?? '未知错误'));
        document.getElementById('omSubmit').disabled = false;
      }
    }

    async function dismissAlert(alertId) {
      const r = await api('/quant/api/price-alerts?id=' + encodeURIComponent(alertId), { method: 'DELETE' });
      if (r?.status === 'ok') {
        toast('已停止监控');
        loadAlerts();
        loadPriceAlerts();
      } else {
        toast('操作失败: ' + (r?.message ?? '未知错误'));
      }
    }

    // ── Price Alerts ────────────────────────────────────────
    async function loadPriceAlerts() {
      const r = await api('/quant/api/price-alerts');
      const alerts = r?.data ?? [];
      if (!alerts.length) { document.getElementById('priceAlertsPanel').innerHTML='<div class="empty">暂无预警</div>'; return; }
      const active = alerts.filter(a => !a.triggered || a.repeating);
      let h='<table><tr><th>标的</th><th>条件</th><th>状态</th></tr>';
      for (const a of alerts.slice(0, 20)) {
        const st = a.triggered && !a.repeating ? '<span class="badge badge-filled">已触发</span>' : '<span class="badge badge-pending">监控中</span>';
        h += '<tr><td>'+(a.symbol??'')+'</td><td>'+(a.description??a.type+' '+a.threshold)+'</td><td>'+st+'</td></tr>';
      }
      h += '</table>';
      if (active.length > 0) h = '<div style="font-size:12px;color:var(--text-dim);margin-bottom:6px;">活跃: '+active.length+' / '+alerts.length+'</div>' + h;
      document.getElementById('priceAlertsPanel').innerHTML = h;
    }

    // ── Scheduled Tasks ─────────────────────────────────────
    async function loadScheduledTasks() {
      const r = await api('/quant/api/scheduled-tasks');
      const tasks = r?.data ?? [];
      if (!tasks.length) { document.getElementById('scheduledTasksPanel').innerHTML='<div class="empty">暂无定时任务</div>'; return; }
      const icons = {pending:'⏳',completed:'✅',failed:'❌',cancelled:'🚫'};
      let h='<table><tr><th>状态</th><th>描述</th><th>计划时间</th></tr>';
      for (const t of tasks.slice(0, 20)) {
        const icon = icons[t.status] ?? '•';
        const sched = t.scheduledAt ? new Date(t.scheduledAt).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '--';
        h += '<tr><td>'+icon+'</td><td>'+(t.description??t.id)+'</td><td>'+sched+'</td></tr>';
      }
      document.getElementById('scheduledTasksPanel').innerHTML = h + '</table>';
    }

    // ── Backtest History ────────────────────────────────────
    async function loadBacktestHistory() {
      const r = await api('/quant/api/backtests?limit=10');
      const runs = r?.data ?? [];
      if (!runs.length) { document.getElementById('backtestHistoryPanel').innerHTML='<div class="empty">暂无回测记录</div>'; return; }
      let h='<table><tr><th>策略</th><th>标的</th><th>收益</th><th>日期</th></tr>';
      for (const b of runs) {
        const ret = b.returnPct != null ? (b.returnPct*100).toFixed(1)+'%' : '--';
        const cls = pctClass(b.returnPct??0);
        const dt = b.createdAt ? new Date(b.createdAt).toLocaleDateString('zh-CN') : '--';
        h += '<tr><td>'+(b.strategyId??'')+'</td><td>'+(b.symbols?b.symbols.join(','):'')+'</td><td class="'+cls+'">'+ret+'</td><td>'+dt+'</td></tr>';
      }
      document.getElementById('backtestHistoryPanel').innerHTML = h + '</table>';
    }

    // ── Refresh all ─────────────────────────────────────────
    async function refreshAll() {
      await Promise.all([
        loadPortfolio(), loadOrders(), loadSignals(), loadStatus(),
        loadPnl(), loadEquityChart(), loadHealth(), loadStaleness(), loadAlerts(),
        loadPriceAlerts(), loadScheduledTasks(), loadBacktestHistory(),
      ]);
    }
    refreshAll();

    // Start SSE connection for real-time updates
    SSE.connect();
    // Fallback polling: 60s when SSE connected, 15s when disconnected
    setInterval(() => {
      const interval = SSE.connected ? 60000 : 15000;
      if (!SSE._lastPoll || Date.now() - SSE._lastPoll >= interval) {
        SSE._lastPoll = Date.now();
        refreshAll();
      }
    }, 5000); // check every 5s, actual refresh governed by interval
  </script>
</body>
</html>`;
}

/** Return the endpoint index as JSON (backward-compat). */
export function handleDashboardRequest(): Record<string, unknown> {
  return {
    status: "ok",
    name: "Quant Trading Plugin",
    endpoints: [
      "GET  /quant/dashboard",
      "GET  /quant/dashboard/ui",
      "GET  /quant/api/portfolio",
      "GET  /quant/api/live/status",
      "POST /quant/api/engine/start",
      "POST /quant/api/engine/stop",
      "POST /quant/api/engine/pause",
      "POST /quant/api/engine/resume",
      "GET    /quant/api/orders[?status=&page=&pageSize=]",
      "POST   /quant/api/orders",
      "DELETE /quant/api/orders[?id=]",
      "GET  /quant/api/trades",
      "GET  /quant/api/pnl",
      "GET  /quant/api/strategies",
      "POST /quant/api/strategies",
      "GET  /quant/api/signals[?strategyId=&symbol=&limit=&page=&pageSize=]",
      "GET  /quant/api/confirmations",
      "GET  /quant/api/health",
      "GET  /quant/api/staleness",
      "GET    /quant/api/alerts[?severity=&source=&after=&before=&acknowledged=&limit=&offset=]",
      "DELETE /quant/api/alerts",
      "GET  /quant/api/alerts/summary",
      "POST /quant/api/alerts/acknowledge",
      "GET  /quant/api/events (SSE stream, ?filter=signal,order,...)",
      "GET  /quant/api/events/recent[?category=&limit=]",
    ],
  };
}

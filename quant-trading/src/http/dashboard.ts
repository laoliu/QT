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
  <title>Quant Trading 量化交易仪表盘</title>
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
    .stock-link { color: var(--accent); text-decoration: none; white-space: nowrap; }
    .stock-link:hover, .stock-link:focus-visible { text-decoration: underline; }
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
    .chart-container svg { width: 100%; height: 160px; }
    .chart-label { font-size: 11px; fill: var(--text-dim); }
    .chart-line { fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .chart-area { opacity: 0.1; }
    .chart-grid { stroke: var(--border); stroke-width: 0.5; }
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
    <h1><a href="${baseUrl}/quant/" style="color:inherit;text-decoration:none">Quant Trading 量化交易</a></h1>
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
      <h2>投资组合 <button class="btn" style="font-size:11px;padding:2px 8px;" onclick="exportCSV('portfolio')" title="导出持仓明细CSV">导出CSV</button></h2>
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
      <div class="chart-container" id="equityChart"><div class="empty">暂无数据</div></div>
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
      <h2>回测记录 <button class="btn" style="font-size:11px;padding:2px 8px;" onclick="exportCSV('journal')" title="导出交易日志CSV">导出日志</button></h2>
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

    // ── Stock name resolution cache ─────────────────────────
    const _nameCache = {};
    async function resolveStockNames(symbols) {
      const missing = symbols.filter(s => s && !_nameCache[s]);
      if (missing.length) {
        try {
          const r = await api('/quant/api/stock-names?symbols=' + missing.join(','));
          const data = r?.data ?? {};
          for (const [k,v] of Object.entries(data)) _nameCache[k] = v;
        } catch(e) { console.warn('stock-names error', e); }
      }
    }
    function nameLabel(symbol, market) {
      if (!symbol) return '';
      const n = _nameCache[symbol];
      return stockLink(symbol,undefined,market) + (n ? ' <span style="color:var(--text-dim)">' + esc(n) + '</span>' : '');
    }
    function stockDetailUrl(symbol, market) {
      if (market && market !== 'cn_stock') return '';
      const code = String(symbol ?? '').trim()
        .replace(/^(?:SH|SZ|BJ)([0-9]{6})$/i, '$1')
        .replace(/^([0-9]{6})[.](?:SH|SZ|BJ)$/i, '$1');
      if (!/^[0-9]{6}$/.test(code)) return '';
      return BASE + '/quant/admin/stock?symbol=' + encodeURIComponent(code);
    }
    function stockLink(symbol, label, market) {
      const text = esc(label ?? symbol);
      const url = stockDetailUrl(symbol, market);
      return url ? '<a class="stock-link" href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+text+'</a>' : text;
    }
    function stockText(text, symbol, market) {
      text = String(text ?? '');
      // Legacy portfolio alerts have a leading code but no symbol metadata.
      const leading = text.match(/^([0-9]{6})(?=$|[^0-9A-Za-z_.])/);
      const code = symbol || (leading ? leading[1] : '');
      const index = code ? text.indexOf(code) : -1;
      if (index < 0 || !stockDetailUrl(code, market)) return esc(text);
      return esc(text.slice(0, index)) + stockLink(code, undefined, market) + esc(text.slice(index + code.length));
    }

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
        const detectedMode = isPaper ? 'paper' : isLive ? 'live' : '';
        sel.value = detectedMode;
        sel.className = 'mode-banner ' + (isLive ? 'mode-live' : 'mode-paper');
        // Also set viewMode so equity chart and other APIs use the correct mode
        if (detectedMode) viewMode = detectedMode;
      }
      document.getElementById('portfolioStats').innerHTML =
        '<div class="stat"><span class="value">¥'+fmt(tv)+'</span><span class="label">总资产'+srcLabel+'</span></div>'+
        '<div class="stat"><span class="value">¥'+fmt(cash)+'</span><span class="label">可用资金</span></div>'+
        '<div class="stat"><span class="value '+pctClass(pnl)+'">¥'+fmt(pnl)+'</span><span class="label">浮动盈亏</span></div>'+
        '<div class="stat"><span class="value '+pctClass(pct)+'">'+fmt(pct)+'%</span><span class="label">收益率</span></div>';
      const positions = d.positions??[];
      if (!positions.length) { document.getElementById('positionsTable').innerHTML='<div class="empty">暂无持仓</div>'; return; }
      let h='<table><tr><th>代码</th><th>名称</th><th>持仓</th><th>成本</th><th>现价</th><th>盈亏</th></tr>';
      for(const p of positions){const hasPrice=p.currentPrice!=null&&p.avgCost!=null;const u=hasPrice?(p.currentPrice-p.avgCost)*(p.quantity??0):null;
        h+='<tr><td>'+stockLink(p.symbol,undefined,p.market)+'</td><td>'+(p.name?stockLink(p.symbol,p.name,p.market):'')+'</td><td>'+(p.quantity??0)+'</td><td>¥'+fmt(p.avgCost)+'</td><td>'+(p.currentPrice!=null?'¥'+fmt(p.currentPrice):'¥--')+'</td><td class="'+(u!=null?pctClass(u):'')+'">'+( u!=null?'¥'+fmt(u):'¥--')+'</td></tr>';}
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
        const t=o.createdAt?new Date(o.createdAt).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',timeZone:'Asia/Shanghai'}):(o.time??'');
        h+='<tr><td>'+esc(t)+'</td><td>'+stockLink(o.symbol,undefined,o.market)+'</td><td>'+side+'</td><td>'+(o.quantity??'')+'</td><td>¥'+fmt(o.price??o.avgFillPrice??o.filledPrice)+'</td><td>'+st+'</td></tr>';}
      document.getElementById('ordersTable').innerHTML=h+'</table>';
    }

    // ── Signals ─────────────────────────────────────────────
    async function loadSignals() {
      const r = await api('/quant/api/signals?limit=20' + modeParam(true)); const sigs=r?.data??[];
      if(!sigs.length){document.getElementById('signalsTable').innerHTML='<div class="empty">暂无信号</div>';return;}
      const slice = sigs.slice(-15);
      await resolveStockNames(slice.map(s => s.symbol).filter(Boolean));
      let h='<table><tr><th>日期</th><th>策略</th><th>代码</th><th>方向</th><th>价格</th></tr>';
      for(const s of slice){
        const dir=s.direction==='buy'?'<span class="badge badge-buy">买</span>':'<span class="badge badge-sell">卖</span>';
        const dt=s.date??(s.timestamp?new Date(s.timestamp).toLocaleDateString():'');
        h+='<tr><td>'+esc(dt)+'</td><td>'+esc(s.strategyId)+'</td><td>'+nameLabel(s.symbol,s.market)+'</td><td>'+dir+'</td><td>¥'+fmt(s.price)+'</td></tr>';}
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

    // ── Equity chart (SVG) — powered by equity-history API ───
    async function loadEquityChart() {
      // Try dedicated equity-history endpoint first
      const r = await api('/quant/api/equity-history?limit=120' + modeParam(true));
      const history = r?.data;

      if (history && Array.isArray(history) && history.length >= 2) {
        renderSvgChart('equityChart', history.map(h => ({x: h.date, y: h.equity})));
        // Load summary stats
        const sr = await api('/quant/api/equity-history/summary' + modeParam(false));
        const s = sr?.data;
        if (s && s.totalDays > 0) {
          const retCls = pctClass(s.totalReturnPct);
          document.getElementById('equitySummary').innerHTML =
            '<span class="'+retCls+'">'+fmt(s.totalReturnPct)+'%</span> · ' +
            '最大回撤 '+fmt(s.maxDrawdownPct)+'% · ' +
            '胜率 '+fmt(s.winRate)+'% · ' +
            s.totalDays+'天';
        }
        return;
      }

      // Single data point — show current value instead of empty chart
      if (history && Array.isArray(history) && history.length === 1) {
        const h = history[0];
        document.getElementById('equityChart').innerHTML =
          '<div class="stats"><div class="stat"><span class="value">¥'+fmt(h.equity)+'</span><span class="label">'+h.date+' 净值（首日，暂无趋势）</span></div></div>';
        return;
      }

      // Fallback: try pnl endpoint for legacy data
      const pr = await api('/quant/api/pnl' + modeParam(false)); const d=pr?.data;
      const legacyHistory = d?.equityHistory ?? d?.history;
      if (legacyHistory && Array.isArray(legacyHistory) && legacyHistory.length >= 2) {
        renderSvgChart('equityChart', legacyHistory.map(h => ({x: h.date||h.timestamp, y: h.equity||h.value})));
        return;
      }

      // Last fallback: show current equity value
      const tv = d?.totalValue ?? d?.equity;
      if (tv != null) {
        document.getElementById('equityChart').innerHTML =
          '<div class="stats"><div class="stat"><span class="value">¥'+fmt(tv)+'</span><span class="label">当前净值</span></div></div>';
      }
    }

    function renderSvgChart(containerId, data, overlay) {
      if (!data.length) return;
      const W = 600, H = 150, pad = {t:10,r:10,b:25,l:60};
      const cw = W-pad.l-pad.r, ch = H-pad.t-pad.b;

      // Merge data ranges for proper scaling
      const allY = [...data.map(d=>d.y), ...(overlay||[]).map(d=>d.y)];
      const yMin = Math.min(...allY), yMax = Math.max(...allY);
      const yRange = yMax - yMin || 1;
      const xStep = cw / Math.max(data.length - 1, 1);

      function buildPath(points) {
        let p = '', a = '';
        for (let i = 0; i < points.length; i++) {
          const x = pad.l + i * xStep;
          const y = pad.t + ch - ((points[i].y - yMin) / yRange) * ch;
          p += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
          a += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
        }
        a += 'L'+(pad.l + (points.length-1)*xStep)+','+(pad.t+ch)+'L'+pad.l+','+(pad.t+ch)+'Z';
        return {path:p, area:a};
      }

      // Determine line color
      const color = data[data.length-1].y >= data[0].y ? 'var(--green)' : 'var(--red)';
      const {path, area: areaPath} = buildPath(data);

      // Grid lines
      let grid = '';
      for (let i = 0; i <= 3; i++) {
        const gy = pad.t + (ch / 3) * i;
        const val = yMax - (yRange / 3) * i;
        grid += '<line x1="'+pad.l+'" y1="'+gy+'" x2="'+(W-pad.r)+'" y2="'+gy+'" class="chart-grid"/>';
        grid += '<text x="'+(pad.l-4)+'" y="'+(gy+4)+'" text-anchor="end" class="chart-label">¥'+fmt(val)+'</text>';
      }

      // X labels (first, mid, last)
      const xl = [0, Math.floor(data.length/2), data.length-1];
      for (const i of xl) {
        if (data[i]) {
          const x = pad.l + i * xStep;
          grid += '<text x="'+x+'" y="'+(H-4)+'" text-anchor="middle" class="chart-label">'+data[i].x+'</text>';
        }
      }

      let overlayPaths = '';
      if (overlay && overlay.length >= 2) {
        const oStep = cw / Math.max(overlay.length - 1, 1);
        let op = '';
        for (let i = 0; i < overlay.length; i++) {
          const x = pad.l + i * oStep;
          const y = pad.t + ch - ((overlay[i].y - yMin) / yRange) * ch;
          op += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
        }
        overlayPaths = '<path d="'+op+'" stroke="var(--accent)" class="chart-line" stroke-dasharray="4,3" opacity="0.7"/>';
      }

      // Legend
      let legend = '<text x="'+(W-pad.r)+'" y="'+(pad.t+10)+'" text-anchor="end" class="chart-label" fill="'+color+'">策略</text>';
      if (overlay && overlay.length) {
        legend += '<text x="'+(W-pad.r)+'" y="'+(pad.t+22)+'" text-anchor="end" class="chart-label" fill="var(--accent)">基准</text>';
      }

      document.getElementById(containerId).innerHTML =
        '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="xMidYMid meet">' +
        grid +
        '<path d="'+areaPath+'" fill="'+color+'" class="chart-area"/>' +
        '<path d="'+path+'" stroke="'+color+'" class="chart-line"/>' +
        overlayPaths +
        legend +
        '</svg>';
    }

    // ── Benchmark overlay ───────────────────────────────────
    async function loadBenchmarkOverlay() {
      const sel = document.getElementById('benchmarkSelect');
      const benchId = sel ? sel.value : '';
      if (!benchId) {
        // Reload chart without overlay
        await loadEquityChart();
        document.getElementById('benchmarkStats').innerHTML = '';
        return;
      }
      const r = await api('/quant/api/benchmark/compare?benchmark=' + benchId + modeParam(true));
      if (!r || r.status !== 'ok' || !r.data) {
        document.getElementById('benchmarkStats').innerHTML = '<span style="color:var(--text-dim)">基准数据不足</span>';
        return;
      }
      const cmp = r.data;
      // Re-render chart with both curves using normalized equity values
      const eqR = await api('/quant/api/equity-history?limit=120' + modeParam(true));
      const history = eqR?.data;
      if (history && history.length >= 2) {
        const first = history[0].equity;
        const stratData = history.map(h => ({x: h.date, y: h.equity}));
        // Scale benchmark to same starting equity for visual comparison
        const benchData = cmp.data.map(d => ({x: d.date, y: first * d.normalizedReturn}));
        renderSvgChart('equityChart', stratData, benchData);
      }
      // Show stats
      const s = cmp.stats;
      const exCls = pctClass(s.excessReturn);
      document.getElementById('benchmarkStats').innerHTML =
        '策略: <span class="'+pctClass(s.strategyReturnPct)+'">'+fmt(s.strategyReturnPct)+'%</span> · ' +
        cmp.benchmark.name+': <span class="'+pctClass(s.benchmarkReturnPct)+'">'+fmt(s.benchmarkReturnPct)+'%</span> · ' +
        '超额: <span class="'+exCls+'">'+fmt(s.excessReturn)+'%</span> · ' +
        'IR: '+s.informationRatio.toFixed(2);
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
      document.getElementById('dataHealth').innerHTML='<span class="dot '+dc+'"></span>数据: '+lb+' ('+d.totalSymbols+')';

      // Staleness table
      if (!d.symbols || !d.symbols.length) {
        document.getElementById('stalenessTable').innerHTML='<div class="empty">暂无订阅</div>';
        return;
      }
      await resolveStockNames(d.symbols.map(s => s.symbol).filter(Boolean));
      let h='<table><tr><th>标的</th><th>状态</th><th>延迟</th></tr>';
      for (const s of d.symbols) {
        const cls = 'badge-'+s.status;
        const lb2 = {fresh:'正常',warning:'延迟',critical:'中断'}[s.status]??s.status;
        const age = s.ageMs > 0 ? (s.ageMs/1000).toFixed(0)+'s' : 'N/A';
        h += '<tr><td>'+nameLabel(s.symbol,s.market)+'</td><td><span class="badge '+cls+'">'+lb2+'</span></td><td>'+age+'</td></tr>';
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
        const _d = new Date(a.timestamp);
        const t = _d.toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit',timeZone:'Asia/Shanghai'})+' '+_d.toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'});
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
          '<div class="alert-content"><span class="alert-msg">'+stockText(a.message,sym,a.metadata?.market)+'</span>'+actions+'</div>' +
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
      const url = stockDetailUrl(symbol);
      if (url) window.open(url, '_blank', 'noopener,noreferrer');
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
      const slice = alerts.slice(0, 20);
      await resolveStockNames(slice.map(a => a.symbol).filter(Boolean));
      let h='<table><tr><th>标的</th><th>条件</th><th>状态</th></tr>';
      for (const a of slice) {
        const st = a.triggered && !a.repeating ? '<span class="badge badge-filled">已触发</span>' : '<span class="badge badge-pending">监控中</span>';
        h += '<tr><td>'+nameLabel(a.symbol,a.market)+'</td><td>'+stockText(a.description??a.type+' '+a.threshold,a.symbol,a.market)+'</td><td>'+st+'</td></tr>';
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
        const sched = t.scheduledAt ? new Date(t.scheduledAt).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'}) : '--';
        h += '<tr><td>'+icon+'</td><td>'+(t.description??t.id)+'</td><td>'+sched+'</td></tr>';
      }
      document.getElementById('scheduledTasksPanel').innerHTML = h + '</table>';
    }

    // ── Backtest History ────────────────────────────────────
    async function loadBacktestHistory() {
      const r = await api('/quant/api/backtests?limit=10');
      const runs = r?.data ?? [];
      if (!runs.length) { document.getElementById('backtestHistoryPanel').innerHTML='<div class="empty">暂无回测记录</div>'; return; }
      let h='<table><tr><th>策略</th><th>标的</th><th>收益</th><th>日期</th><th></th></tr>';
      for (const b of runs) {
        const ret = b.returnPct != null ? (b.returnPct*100).toFixed(1)+'%' : '--';
        const cls = pctClass(b.returnPct??0);
        const dt = b.createdAt ? new Date(b.createdAt).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'}) : '--';
        h += '<tr><td>'+(b.strategyId??'')+'</td><td>'+(b.symbols?b.symbols.map(s=>stockLink(s)).join(', '):'')+'</td><td class="'+cls+'">'+ret+'</td><td>'+dt+'</td><td><button class="btn" style="font-size:10px;padding:1px 6px;" onclick="exportCSV(\\'backtest\\',\\''+esc(b.id)+'\\')">CSV</button></td></tr>';
      }
      document.getElementById('backtestHistoryPanel').innerHTML = h + '</table>';
    }

    // ── CSV Export ───────────────────────────────────────────
    function exportCSV(type, backtestId) {
      let url = BASE + '/quant/export/' + type;
      if (type === 'backtest' && backtestId) url += '?id=' + encodeURIComponent(backtestId);
      const a = document.createElement('a');
      a.href = url;
      a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      toast('正在下载 ' + type + ' CSV...');
    }

    // ── Refresh all ─────────────────────────────────────────
    async function refreshAll() {
      // Load portfolio first to auto-detect broker mode (paper/live),
      // so equity chart and other APIs use the correct mode parameter.
      await loadPortfolio();
      await Promise.all([
        loadOrders(), loadSignals(), loadStatus(),
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
      "GET  /quant/export/portfolio",
      "GET  /quant/export/journal",
      "GET  /quant/export/backtest[?id=]",
    ],
  };
}

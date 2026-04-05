/**
 * Performance Report Page — HTML body.
 */
export function getPerformanceReportBodyHtml(baseUrl: string): string {
  return `
<div class="breadcrumb">
  <a href="${baseUrl}/quant/">🏠 首页</a><span class="sep">/</span>
  <a href="${baseUrl}/quant/dashboard/ui">📊 仪表盘</a><span class="sep">/</span>
  <span class="current">📈 交易绩效报告</span>
</div>
<header>
  <h1><a href="${baseUrl}/quant/" style="color:inherit;text-decoration:none">交易绩效报告</a></h1>
  <div class="header-right">
    <a href="${baseUrl}/quant/dashboard/ui">仪表盘</a>
    <a href="${baseUrl}/quant/admin/planner">投资规划</a>
  </div>
</header>
<div class="container">
  <!-- Controls -->
  <div class="controls">
    <label>模式:</label>
    <select id="modeSelect" onchange="reload()">
      <option value="paper">模拟盘</option>
      <option value="live">实盘</option>
    </select>
    <label>区间:</label>
    <select id="rangeSelect" onchange="reload()">
      <option value="7">近7天</option>
      <option value="30" selected>近30天</option>
      <option value="90">近90天</option>
      <option value="180">近半年</option>
      <option value="365">近一年</option>
      <option value="all">全部</option>
    </select>
    <label>从:</label>
    <input type="date" id="dateFrom">
    <label>至:</label>
    <input type="date" id="dateTo">
    <button class="btn btn-primary" onclick="reload()">查询</button>
  </div>

  <!-- KPI Summary -->
  <div class="section" id="summarySection">
    <h2>总体表现</h2>
    <div class="kpi-row" id="kpiRow"><div class="empty">加载中...</div></div>
  </div>

  <!-- Equity Curve -->
  <div class="section">
    <h2>收益曲线
      <select id="benchmarkSelect" style="font-size:11px;padding:2px 6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);border-radius:4px;" onchange="loadEquityCurve()">
        <option value="">无基准</option>
        <option value="csi300" selected>沪深300</option>
        <option value="csi500">中证500</option>
        <option value="sse">上证综指</option>
      </select>
    </h2>
    <div class="chart-container" id="equityCurveChart"><div class="empty">加载中...</div></div>
    <div id="equityBenchmarkStats" style="font-size:12px;color:var(--text-dim);margin-top:6px;"></div>
  </div>

  <!-- Period P&L Charts -->
  <div class="grid-2">
    <div class="section">
      <h2>每日盈亏</h2>
      <div class="chart-container" id="dailyPnlChart"><div class="empty">加载中...</div></div>
    </div>
    <div class="section">
      <h2>每月盈亏</h2>
      <div class="chart-container" id="monthlyPnlChart"><div class="empty">加载中...</div></div>
    </div>
  </div>

  <!-- Tabs: Strategy / Symbol / Trades -->
  <div class="section">
    <div class="tab-bar">
      <button class="tab-btn active" onclick="switchTab('strategy')">策略分析</button>
      <button class="tab-btn" onclick="switchTab('symbol')">标的分析</button>
      <button class="tab-btn" onclick="switchTab('trades')">交易明细</button>
      <button class="tab-btn" onclick="switchTab('hitrate')">信号命中率</button>
    </div>
    <div class="tab-content active" id="tab-strategy">
      <div id="strategyTable"><div class="empty">加载中...</div></div>
    </div>
    <div class="tab-content" id="tab-symbol">
      <div id="symbolTable"><div class="empty">加载中...</div></div>
    </div>
    <div class="tab-content" id="tab-trades">
      <div id="tradesTable"><div class="empty">加载中...</div></div>
    </div>
    <div class="tab-content" id="tab-hitrate">
      <div id="hitrateTable"><div class="empty">加载中...</div></div>
    </div>
  </div>

  <!-- Equity History Summary -->
  <div class="section">
    <h2>权益统计</h2>
    <div id="equitySummarySection"><div class="empty">加载中...</div></div>
  </div>
</div>
<div class="toast" id="toast"></div>
`;
}

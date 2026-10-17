/**
 * Investment Planner — HTML body template.
 * Contains the page skeleton: header, tabs, tab content panels, forms, etc.
 * Uses \`\${b}\` for base URL interpolation (3 occurrences).
 */
export function getPlannerBodyHtml(b: string): string {
  return `
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">🏠 首页</a><span class="sep">/</span><a href="${b}/quant/admin/ui">🔧 管理面板</a><span class="sep">/</span><span class="current">📈 投资规划</span></div>
<header>
  <h1>📈 投资规划系统</h1>
  <div class="header-right">
    <select id="global-mode-select" class="mode-select">
      <option value="paper">🟢 模拟盘</option>
      <option value="live">🔴 实盘</option>
    </select>
    <a href="${b}/quant/dashboard/ui">📊 交易仪表盘</a>
    <span style="color:var(--text-dim)" id="clock"></span>
  </div>
</header>
<div class="tabs">
  <div class="tab-group" data-mode="shared">
    <span class="tab-group-label">规划</span>
    <div class="tab active" data-tab="plan">📋 制定计划</div>
  </div>
  <div class="tab-group" data-mode="paper">
    <span class="tab-group-label">研究 · 模拟</span>
    <div class="tab tab-sim" data-tab="screen">🔍 选股筛选</div>
    <div class="tab tab-sim" data-tab="institution">🏦 机构行为</div>
    <div class="tab tab-sim" data-tab="accumulation">🧲 主力吸筹</div>
    <div class="tab tab-sim" data-tab="backtest">🧪 策略回测</div>
    <div class="tab tab-sim" data-tab="combo-backtest">🔬 组合回测</div>
    <div class="tab tab-sim" data-tab="pf-backtest">📊 持仓回测</div>
    <div class="tab tab-sim" data-tab="portfolio">💼 模拟持仓</div>
    <div class="tab tab-sim" data-tab="swing-overlay">💹 持仓波段</div>
    <div class="tab tab-sim" data-tab="signals">🚨 交易信号</div>
    <div class="tab tab-sim" data-tab="milestones">📊 目标追踪</div>
  </div>
  <div class="tab-group" data-mode="live">
    <span class="tab-group-label">实盘 · 操作</span>
    <div class="tab tab-live" data-tab="recommend">🎯 建仓推荐</div>
    <div class="tab tab-live" data-tab="add-position">📈 加仓推荐</div>
    <div class="tab tab-live" data-tab="rebalance">⚖️ 调仓建议</div>
    <div class="tab tab-live" data-tab="swing-overlay">💹 持仓波段</div>
    <div class="tab tab-live" data-tab="signals">🚨 交易信号</div>
    <div class="tab tab-live" data-tab="milestones">📊 目标追踪</div>
  </div>
</div>

<!-- Quick-nav guide (shared across all tabs) -->
<div class="nav-guide" id="nav-guide">
  <div class="nav-guide-paper">
    <span class="nav-guide-title">🟢 模拟盘功能导航</span>
    <div class="nav-guide-btns">
      <button class="nav-btn nav-btn-sim" onclick="activateTab('screen');history.pushState(null,null,'#screen')">🔍 选股筛选</button>
      <button class="nav-btn nav-btn-sim" onclick="activateTab('institution');history.pushState(null,null,'#institution')">🏦 机构行为</button>
      <button class="nav-btn nav-btn-sim" onclick="activateTab('accumulation');history.pushState(null,null,'#accumulation')">🧲 主力吸筹</button>
      <button class="nav-btn nav-btn-sim" onclick="activateTab('backtest');history.pushState(null,null,'#backtest')">🧪 策略回测</button>
      <button class="nav-btn nav-btn-sim" onclick="activateTab('combo-backtest');history.pushState(null,null,'#combo-backtest')">🔬 组合回测</button>
      <button class="nav-btn nav-btn-sim" onclick="activateTab('pf-backtest');history.pushState(null,null,'#pf-backtest')">📊 持仓回测</button>
      <button class="nav-btn nav-btn-sim" onclick="activateTab('portfolio');history.pushState(null,null,'#portfolio')">💼 模拟持仓</button>
      <button class="nav-btn nav-btn-sim" onclick="activateTab('swing-overlay');history.pushState(null,null,'#swing-overlay')">💹 持仓波段</button>
      <button class="nav-btn nav-btn-sim" onclick="activateTab('signals');history.pushState(null,null,'#signals')">🚨 交易信号</button>
    </div>
  </div>
  <div class="nav-guide-live">
    <span class="nav-guide-title">🔴 实盘功能导航</span>
    <div class="nav-guide-btns">
      <button class="nav-btn nav-btn-live" onclick="activateTab('recommend');history.pushState(null,null,'#recommend')">🎯 建仓推荐</button>
      <button class="nav-btn nav-btn-live" onclick="activateTab('add-position');history.pushState(null,null,'#add-position')">📈 加仓推荐</button>
      <button class="nav-btn nav-btn-live" onclick="activateTab('rebalance');history.pushState(null,null,'#rebalance')">⚖️ 调仓建议</button>
      <button class="nav-btn nav-btn-live" onclick="activateTab('swing-overlay');history.pushState(null,null,'#swing-overlay')">💹 持仓波段</button>
      <button class="nav-btn nav-btn-live" onclick="activateTab('signals');history.pushState(null,null,'#signals')">🚨 交易信号</button>
      <button class="nav-btn nav-btn-live" onclick="activateTab('milestones');history.pushState(null,null,'#milestones')">📊 目标追踪</button>
    </div>
  </div>
</div>

<!-- ═══ Plan Tab ═══ -->
<div class="tab-content active" id="tab-plan">
  <!-- 今日交易计划面板 -->
  <div class="card" id="today-plan-card" style="margin-bottom:16px;border-left:3px solid #f0b429">
    <h3>📋 今日交易计划 <span style="flex:1"></span>
      <button class="btn btn-sm" onclick="loadTodayPlan()">🔄 刷新</button>
      <button class="btn btn-sm btn-primary" onclick="showCreatePlanModal()">➕ 新建计划</button>
    </h3>
    <div id="today-plan-content">
      <p style="color:var(--text-dim)">加载中...</p>
    </div>
  </div>

  <!-- 新建/编辑计划弹窗 -->
  <div id="trade-plan-modal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:1000;overflow-y:auto">
    <div style="max-width:800px;margin:40px auto;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:24px;position:relative">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h3 id="trade-plan-modal-title">📋 新建交易计划</h3>
        <button class="btn btn-sm" onclick="closeTradePlanModal()">✕ 关闭</button>
      </div>
      <div class="form-row">
        <label>日期</label>
        <input type="date" id="tp-date" style="width:160px">
      </div>
      <div class="form-row">
        <label>仓位预算(Ԫ)</label>
        <input type="number" id="tp-budget" value="100000" style="width:160px">
      </div>
      <div class="form-row">
        <label>备注</label>
        <input type="text" id="tp-notes" style="width:100%" placeholder="今日交易思路...">
      </div>
      <h4 style="margin:12px 0 8px">目标股票</h4>
      <div id="tp-stocks-list"></div>
      <button class="btn btn-sm" onclick="addTpStockRow()" style="margin-top:8px">➕ 添加股票</button>
      <div class="btn-group" style="margin-top:16px">
        <button class="btn btn-primary" onclick="submitTradePlan()">✅ 保存计划</button>
      </div>
    </div>
  </div>

  <!-- 偏差报告弹窗 -->
  <div id="deviation-report-modal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:1000;overflow-y:auto">
    <div style="max-width:700px;margin:40px auto;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:24px;position:relative">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h3>📊 执行偏差报告</h3>
        <button class="btn btn-sm" onclick="closeDeviationModal()">✕ 关闭</button>
      </div>
      <div id="deviation-report-content"></div>
    </div>
  </div>

  <div class="cards">
    <div class="card">
      <h3>💰 投资目标设定</h3>
      <div class="form-row">
        <label>初始资金(Ԫ)</label>
        <input type="number" id="initial-capital" value="500000" style="width:140px">
      </div>
      <div class="form-row">
        <label>目标资金(Ԫ)</label>
        <input type="number" id="target-capital" value="1000000" style="width:140px">
      </div>
      <div class="form-row">
        <label>投资期限(月)</label>
        <input type="number" id="timeframe" value="12" style="width:80px" min="1" max="60">
      </div>
      <div class="form-row">
        <label>风险偏好</label>
        <select id="risk-tolerance">
          <option value="low">稳健型 (低风险)</option>
          <option value="medium" selected>均衡型 (中等风险)</option>
          <option value="high">进取型 (高风险)</option>
          <option value="aggressive">激进型 (极高风险)</option>
        </select>
      </div>
      <div class="form-row">
        <label>策略偏好</label>
        <select id="strategy-pref">
          <option value="auto" selected>自动推荐</option>
          <option value="value">价值投资</option>
          <option value="growth">成长投资</option>
          <option value="momentum">趋势动量</option>
          <option value="swing">波段交易</option>
          <option value="mixed">混合策略</option>
          <option value="manipulation">跟庄策略</option>
        </select>
      </div>
      <div class="btn-group" style="align-items:center">
        <button class="btn btn-primary" onclick="generatePlan()" id="btn-gen-plan">🎯 生成投资计划</button>
        <span id="plan-mode-indicator" style="font-size:12px;margin-left:8px"></span>
      </div>
    </div>
    <div class="card" id="plan-risk-card" style="display:none">
      <h3>⚠️ 风险评估</h3>
      <div id="risk-assessment"></div>
    </div>
  </div>
  <div id="plan-result" style="display:none">
    <div class="kpi-row" id="plan-kpis"></div>
    <div class="card" style="margin-bottom:14px">
      <h3>📊 资金分配方案 <span id="plan-version-badge" style="font-size:12px;margin-left:8px"></span></h3>
      <div style="overflow-x:auto"><table id="allocation-table">
        <thead><tr><th>代码</th><th>名称</th><th>评分</th><th style="min-width:320px">选入理由</th><th>现价</th><th>股数</th><th>金额</th><th>仓位%</th><th>分批入场</th><th>止损</th><th>止盈</th></tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
    <div class="cards">
      <div class="card">
        <h3>📐 交易规则</h3>
        <ul class="rules-list" id="rules-list"></ul>
      </div>
      <div class="card">
        <h3>⏰ 每日监控计划</h3>
        <ul class="rules-list" id="monitoring-schedule"></ul>
      </div>
    </div>
    <div class="card">
      <h3>🏊 候选池 (替补股票)</h3>
      <div style="overflow-x:auto"><table id="candidate-pool-table">
        <thead><tr><th>代码</th><th style="min-width:60px">名称</th><th>现价</th><th>PE</th><th>评分</th><th style="min-width:260px">理由</th><th>操作</th></tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
    <div class="btn-group" style="margin-top:12px">
      <button class="btn btn-primary" onclick="startMonitoring()">🚀 开始监控</button>
      <button class="btn" onclick="planStocksToRecommend()">📋 导入到建仓推荐 →</button>
    </div>
  </div>

  <!-- Plan History -->
  <div class="card" style="margin-top:16px">
    <h3>📜 历史版本管理 <span style="flex:1"></span><span id="plan-history-status" style="font-size:12px;color:var(--text-dim)"></span></h3>
    <div id="plan-history-panel" style="display:none">
      <div id="plan-history-top-stocks"></div>
      <div style="overflow-x:auto"><table id="plan-history-table">
        <thead><tr><th>版本</th><th>生成日期</th><th>备注</th><th>策略</th><th>初始资金</th><th>目标资金</th><th>持仓数</th><th>风险级别</th><th>可行性</th><th>入选股票</th><th>操作</th></tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
  </div>
  <!-- Plan Detail Modal -->
  <div id="plan-detail-modal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:1000;overflow-y:auto">
    <div style="max-width:900px;margin:40px auto;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:24px;position:relative">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h3 id="plan-detail-title" style="font-size:16px"></h3>
        <button class="btn btn-sm" onclick="closePlanDetail()">✕ 关闭</button>
      </div>
      <div id="plan-detail-meta" style="margin-bottom:12px"></div>
      <div id="plan-detail-kpis" class="kpi-row" style="margin-bottom:12px"></div>
      <div style="overflow-x:auto"><table id="plan-detail-table">
        <thead><tr><th>代码</th><th>名称</th><th>评分</th><th>理由</th><th>现价</th><th>股数</th><th>金额</th><th>仓位%</th><th>止损</th><th>止盈</th></tr></thead>
        <tbody></tbody>
      </table></div>
      <div class="btn-group" style="margin-top:14px">
        <button class="btn btn-primary" id="btn-restore-plan" onclick="restoreHistoryPlan()">🔄 恢复此计划</button>
        <button class="btn" onclick="importHistoryPlanToRecommend()">📋 导入到建仓推荐</button>
        <button class="btn" onclick="monitorHistoryPlan()">🚀 开始监控</button>
        <button class="btn btn-danger" id="btn-delete-plan" onclick="deleteHistoryPlan()">🗑️ 删除</button>
      </div>
    </div>
  </div>
</div>

<!-- ═══ Portfolio Tab ═══ -->
<div class="tab-content" id="tab-portfolio">
  <div class="kpi-row" id="portfolio-kpis"></div>
  <div class="card">
    <h3>💼 当前持仓 <span style="flex:1"></span>
      <button class="btn btn-sm" onclick="refreshPortfolio()">🔄 刷新行情</button>
    </h3>
    <div style="overflow-x:auto"><table id="portfolio-table">
      <thead><tr><th>代码</th><th>名称</th><th>持仓</th><th>成本</th><th>现价</th><th>市值</th><th>盈亏</th><th>盈亏%</th><th>今日%</th><th>止损</th><th>止盈</th><th>状态</th></tr></thead>
      <tbody></tbody>
    </table></div>
  </div>

  <!-- Conditional order tracking for portfolio positions -->
  <div class="card" style="margin-top:14px">
    <h3 style="cursor:pointer;user-select:none" onclick="togglePortfolioConds()">
      🛡️ 持仓条件单状态
      <span id="pf-cond-toggle" style="float:right;font-size:12px;color:var(--text-dim)">▶ 展开</span>
    </h3>
    <div id="pf-cond-panel" style="display:none;margin-top:8px">
      <div class="btn-group" style="margin-bottom:8px;gap:6px">
        <button class="btn btn-sm" onclick="loadPortfolioConditionals()">🔄 刷新</button>
        <button class="btn btn-sm" onclick="checkConditionalNow()">⚡ 立即检查触发</button>
      </div>
      <div id="pf-cond-status" style="font-size:12px;color:var(--text-dim);margin-bottom:8px"></div>
      <div style="overflow-x:auto"><table id="pf-cond-table" style="font-size:12px">
        <thead><tr><th>代码</th><th>名称</th><th>类型</th><th>入场价</th><th>触发价/比例</th><th>当前止损价</th><th>峰值价</th><th>数量</th><th>创建时间</th><th>状态</th><th>触发结果</th></tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
  </div>

  <!-- Equity curve chart -->
  <div class="card" style="margin-top:14px">
    <h3>📈 收益曲线 <span style="flex:1"></span>
      <button class="btn btn-sm" onclick="loadEquityCurve()">🔄 刷新</button>
    </h3>
    <div id="equity-curve-kpis" class="kpi-row" style="margin-bottom:8px"></div>
    <div id="equity-curve-chart" style="height:300px;width:100%;position:relative">
      <canvas id="equity-canvas" style="width:100%;height:100%"></canvas>
    </div>
    <div id="equity-curve-status" style="font-size:12px;color:var(--text-dim);margin-top:4px"></div>
  </div>
</div>

<!-- ═══ Screen Tab ═══ -->
<div class="tab-content" id="tab-screen">
  <div class="card">
    <h3>🔍 智能选股</h3>
    <div class="form-row">
      <label>策略</label>
      <select id="screen-strategy">
        <option value="auto">综合评分</option>
        <option value="momentum">趋势动量</option>
        <option value="value">价值低估</option>
        <option value="growth">高成长</option>
        <option value="swing">波段机会</option>
        <option value="manipulation">跟庄策略</option>
        <option value="overnight">🌙 一夜持股法</option>
        <option value="sector_hotspot">🔥 板块热点竞价候选</option>
      </select>
      <label>数量</label>
      <input type="number" id="screen-top-n" value="20" min="5" max="50" style="width:60px">
      <button class="btn btn-primary" onclick="runScreening()">🔎 开始筛选</button>
    </div>
    <div id="screen-status" style="font-size:12px;color:var(--text-dim);margin-top:4px"></div>
  </div>
  <div id="screen-results" style="display:none">
    <div class="kpi-row" id="screen-enrichment-kpis" style="margin-top:12px"></div>
    <div class="card" style="margin-top:12px">
      <h3>📋 筛选结果 (<span id="screen-count">0</span> 只)</h3>
      <div style="overflow-x:auto"><table id="screen-table">
        <thead><tr><th>#</th><th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th><th>PE</th><th>PB</th><th>市值(亿)</th><th>换手率</th><th>量比</th><th>评分</th><th>标签</th><th>理由</th><th>操作</th></tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
  </div>
  <!-- Screener History Comparison -->
  <div class="card" style="margin-top:12px">
    <h3>📅 选股历史对比 <span style="flex:1"></span>
      <button class="btn btn-sm" onclick="loadScreenerHistory()">🔄 刷新历史</button>
    </h3>
    <div id="screener-history-status" style="font-size:12px;color:var(--text-dim);margin-bottom:8px"></div>
    <div id="screener-history-kpis" class="kpi-row"></div>
    <div id="screener-history-panel" style="display:none">
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">🆕 新入选</strong>
        <span style="font-size:11px;color:var(--text-dim)">（前次无，本次有）</span>
        <div id="sh-new" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">🔁 连续入选</strong>
        <span style="font-size:11px;color:var(--text-dim)">（两次均有）</span>
        <div id="sh-kept" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">📤 已退出</strong>
        <span style="font-size:11px;color:var(--text-dim)">（前次有，本次无）</span>
        <div id="sh-dropped" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <details style="margin-top:8px">
        <summary style="cursor:pointer;font-size:12px;color:var(--accent)">📊 近期历史明细</summary>
        <div style="overflow-x:auto;margin-top:6px"><table id="sh-history-table" style="font-size:12px">
          <thead><tr><th>日期</th><th>策略</th><th>数量</th><th>股票池</th></tr></thead>
          <tbody></tbody>
        </table></div>
      </details>
    </div>
  </div>
</div>

<!-- ═══ Institution Activity Tab ═══ -->
<div class="tab-content" id="tab-institution">
  <div class="card">
    <h3>🏦 代理机构行为扫描</h3>
    <div class="form-row">
      <label>观察日期</label>
      <input type="text" id="inst-date" placeholder="YYYY-MM-DD" style="width:130px">
      <label>观察窗口</label>
      <input type="number" id="inst-lookback" value="10" min="3" max="30" style="width:70px">
      <label>最低置信度</label>
      <input type="number" id="inst-min-confidence" value="35" min="0" max="100" style="width:70px">
    </div>
    <div class="form-row">
      <label>吸筹阈值</label>
      <span style="font-size:12px;color:var(--text-dim)">近</span>
      <input type="number" id="inst-pre-accum-days" value="10" min="5" max="30" style="width:64px">
      <span style="font-size:12px;color:var(--text-dim)">天放量≥</span>
      <input type="number" id="inst-pre-accum-count" value="2" min="1" max="10" style="width:64px">
      <span style="font-size:12px;color:var(--text-dim)">次，放量倍数≥</span>
      <input type="number" id="inst-pre-accum-vol-ratio" value="1.5" min="1" max="5" step="0.1" style="width:72px">
    </div>
    <div class="form-row">
      <label>抢筹阈值</label>
      <span style="font-size:12px;color:var(--text-dim)">涨幅≥</span>
      <input type="number" id="inst-rush-change-pct" value="5" min="1" max="20" step="0.5" style="width:64px">
      <span style="font-size:12px;color:var(--text-dim)">%，量比≥</span>
      <input type="number" id="inst-rush-vol-ratio" value="2" min="1" max="10" step="0.1" style="width:64px">
      <span style="font-size:12px;color:var(--text-dim)">，换手≥</span>
      <input type="number" id="inst-rush-turnover" value="3" min="0" max="50" step="0.1" style="width:64px">
      <span style="font-size:12px;color:var(--text-dim)">%</span>
    </div>
    <div class="form-row">
      <label>预设档位</label>
      <button class="btn btn-sm" onclick="applyInstitutionThresholdPreset('conservative')">🛡️ 保守</button>
      <button class="btn btn-sm" onclick="applyInstitutionThresholdPreset('balanced')">⚖️ 平衡</button>
      <button class="btn btn-sm" onclick="applyInstitutionThresholdPreset('aggressive')">🚀 激进</button>
      <button class="btn btn-sm" onclick="saveInstitutionCustomPreset()">💾 另存为预设</button>
      <select id="inst-custom-preset" style="min-width:130px">
        <option value="">选择自定义预设</option>
      </select>
      <button class="btn btn-sm" onclick="applySelectedInstitutionCustomPreset()">📥 应用</button>
      <button class="btn btn-sm" onclick="deleteSelectedInstitutionCustomPreset()">🗑️ 删除</button>
      <span id="inst-preset-label" style="font-size:12px;color:var(--text-dim)">当前: 自定义</span>
    </div>
    <div class="form-row">
      <label>股票池</label>
      <input type="text" id="inst-symbols" placeholder="留空=自动扫描; 多个用逗号分隔，如 600519,000858" style="width:360px">
      <button class="btn" onclick="usePlanForInstitution()">📋 使用当前计划</button>
      <button class="btn btn-primary" id="btn-inst-scan" onclick="scanInstitutionActivity()">🔎 扫描机构行为</button>
    </div>
    <div id="inst-status" style="font-size:12px;color:var(--text-dim);margin-top:6px">利用龙虎榜、主力资金、北向资金、大宗交易和价量结构做公开代理机构推断。实名量化机构日频交易不可见；无 Level2/逐笔数据时，盘口骗单只做弱风险提示。</div>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>📜 扫描历史 <span style="flex:1"></span><span id="inst-history-status" style="font-size:12px;color:var(--text-dim)"></span></h3>
    <div id="inst-history-kpis" class="kpi-row"></div>
    <div id="inst-history-panel" style="display:none">
      <div id="inst-history-top" style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:12px"></div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">🆕 新上榜</strong>
        <span style="font-size:11px;color:var(--text-dim)">（相对上一次扫描）</span>
        <div id="inst-history-new" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">🔁 连续上榜</strong>
        <span style="font-size:11px;color:var(--text-dim)">（连续多次进入 leaders）</span>
        <div id="inst-history-kept" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">📤 掉出榜单</strong>
        <span style="font-size:11px;color:var(--text-dim)">（上一版在，本版不在）</span>
        <div id="inst-history-dropped" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">🧱 历史高壁垒榜</strong>
        <span style="font-size:11px;color:var(--text-dim)">（按单票历史峰值壁垒分）</span>
        <div id="inst-history-moat-peaks" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">🖐️ 历史高人工确认榜</strong>
        <span style="font-size:11px;color:var(--text-dim)">（按单票历史峰值人工确认分）</span>
        <div id="inst-history-manual-peaks" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">🎯 历史高决策榜</strong>
        <span style="font-size:11px;color:var(--text-dim)">（按单票历史峰值决策分）</span>
        <div id="inst-history-decision-peaks" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">📦 历史高订单验证榜</strong>
        <button class="btn btn-xs" style="margin-left:6px;font-size:11px" onclick="toggleInstitutionPeakLeaderSort('order')">📊 按<span id="inst-peak-order-sort-label">次数</span></button>
        <span style="font-size:11px;color:var(--text-dim)">（按历史进入 Top 榜且完成订单验证次数）</span>
        <div id="inst-history-order-peaks" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">🤝 历史高客户验证榜</strong>
        <button class="btn btn-xs" style="margin-left:6px;font-size:11px" onclick="toggleInstitutionPeakLeaderSort('customer')">📊 按<span id="inst-peak-customer-sort-label">次数</span></button>
        <span style="font-size:11px;color:var(--text-dim)">（按历史进入 Top 榜且完成客户验证次数）</span>
        <div id="inst-history-customer-peaks" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">🧪 历史高专利验证榜</strong>
        <button class="btn btn-xs" style="margin-left:6px;font-size:11px" onclick="toggleInstitutionPeakLeaderSort('patent')">📊 按<span id="inst-peak-patent-sort-label">次数</span></button>
        <span style="font-size:11px;color:var(--text-dim)">（按历史进入 Top 榜且完成专利验证次数）</span>
        <div id="inst-history-patent-peaks" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px">
        <strong style="font-size:13px">🏗️ 历史高重组推进榜</strong>
        <button class="btn btn-xs" style="margin-left:6px;font-size:11px" onclick="toggleInstitutionPeakLeaderSort('restructure')">📊 按<span id="inst-peak-restructure-sort-label">出现次数</span></button>
        <span style="font-size:11px;color:var(--text-dim)">（按历史最高重组级别，其次看出现次数）</span>
        <div id="inst-history-restructure-peaks" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px"></div>
      </div>
      <div style="margin-bottom:12px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:8px;min-height:100px">
        <strong style="font-size:12px;display:block;margin-bottom:6px">📈 结构化因子趋势（最近12次）</strong>
        <div id="inst-history-trend-chart" style="width:100%;height:80px;display:flex;align-items:flex-end;gap:1px;font-size:9px;color:var(--text-dim)"></div>
      </div>
      <details style="margin-top:8px">
        <summary style="cursor:pointer;font-size:12px;color:var(--accent)">🗂️ 历史快照明细</summary>
        <div style="overflow-x:auto;margin-top:6px"><table id="inst-history-table" style="font-size:12px">
          <thead><tr><th>版本</th><th>观察日</th><th>生成时间</th><th>候选</th><th>leaders</th><th>高风险</th><th>Top3壁垒</th><th>Top3人工确认</th><th>Top3决策</th><th>Top3订单</th><th>Top3客户</th><th>Top3专利</th><th>Top3重组</th><th>市场基调</th><th>筛选参数</th><th>操作</th></tr></thead>
          <tbody></tbody>
        </table></div>
      </details>
    </div>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>🥇 机构前三管理 <span style="flex:1"></span><span id="inst-top3-status" style="font-size:12px;color:var(--text-dim)">等待扫描或载入历史</span></h3>
    <div class="btn-group" style="margin-bottom:10px">
      <button class="btn btn-sm" onclick="useLatestInstitutionHistoryTop3()">📜 使用最新历史</button>
      <button class="btn btn-sm" onclick="saveInstitutionTop3ToFavorites()">⭐ 前三加入收藏</button>
      <a class="btn btn-sm" href="${b}/quant/admin/favorites" target="_blank" style="text-decoration:none">📌 打开收藏管理</a>
    </div>
    <div style="overflow-x:auto"><table id="inst-top3-table" style="font-size:12px">
      <thead><tr><th>排名</th><th>代码</th><th>名称</th><th>动作</th><th>置信度</th><th>净判断</th><th>建仓分</th><th>壁垒分</th><th>人工确认</th><th>决策分</th><th>来源</th><th>操作</th></tr></thead>
      <tbody><tr><td colspan="12" style="text-align:center;color:var(--text-dim)">暂无机构前三，请先扫描机构行为</td></tr></tbody>
    </table></div>
    <div class="card" style="margin-top:12px;border:1px solid rgba(88,166,255,.25)">
      <h3>✍️ 人工确认项</h3>
      <div id="inst-manual-target" style="font-size:12px;color:var(--text-dim);margin-bottom:10px">请选择机构前三中的一只股票进行人工确认。</div>
      <div class="form-row">
        <label style="min-width:auto"><input type="checkbox" id="inst-manual-moat" style="vertical-align:middle;margin-right:4px">壁垒成立</label>
        <label style="min-width:auto"><input type="checkbox" id="inst-manual-supply" style="vertical-align:middle;margin-right:4px">供应链验证</label>
        <label style="min-width:auto"><input type="checkbox" id="inst-manual-order" style="vertical-align:middle;margin-right:4px">订单验证</label>
        <label style="min-width:auto"><input type="checkbox" id="inst-manual-customer" style="vertical-align:middle;margin-right:4px">客户验证</label>
        <label style="min-width:auto"><input type="checkbox" id="inst-manual-patent" style="vertical-align:middle;margin-right:4px">专利验证</label>
      </div>
      <div class="form-row">
        <label>重组级别</label>
        <select id="inst-manual-restructure-level" style="min-width:140px">
          <option value="none">无</option>
          <option value="rumor">传闻/筹划</option>
          <option value="announcement">正式公告</option>
          <option value="approved">获批/落地</option>
        </select>
      </div>
      <div class="form-row">
        <label>确认备注</label>
        <input type="text" id="inst-manual-note" placeholder="例如：已核对公告、订单、客户认证或产业链采访" style="width:min(100%,520px)">
      </div>
      <div class="btn-group" style="margin-top:10px">
        <button class="btn btn-primary" onclick="saveInstitutionManualConfirmation()">💾 保存人工确认</button>
        <button class="btn" onclick="clearInstitutionManualConfirmation()">🧹 清空当前确认</button>
      </div>
      <div id="inst-manual-status" style="font-size:12px;color:var(--text-dim);margin-top:8px">保存后会重算当前决策分；若当前结果对应历史快照，也会同步写回快照。</div>
    </div>
    <details style="margin-top:10px">
      <summary style="cursor:pointer;font-size:12px;color:var(--accent)">🗂️ 历史前三轨迹</summary>
      <div style="overflow-x:auto;margin-top:6px"><table id="inst-top3-history-table" style="font-size:12px">
        <thead><tr><th>版本</th><th>观察日</th><th>前三股票</th><th>leaders 数</th><th>候选数</th><th>Top3壁垒</th><th>Top3人工确认</th><th>Top3决策</th><th>Top3订单</th><th>Top3客户</th><th>Top3专利</th><th>Top3重组</th><th>市场基调</th></tr></thead>
        <tbody><tr><td colspan="9" style="text-align:center;color:var(--text-dim)">暂无历史轨迹</td></tr></tbody>
      </table></div>
    </details>
  </div>

  <div id="inst-results" style="display:none">
    <div class="kpi-row" id="inst-kpis"></div>

    <div class="cards">
      <div class="card">
        <h3>🧭 市场背景</h3>
        <div id="inst-market-summary" style="font-size:12px;color:var(--text-dim);line-height:1.7"></div>
      </div>
      <div class="card">
        <h3>⚠️ 能力边界</h3>
        <ul class="rules-list" id="inst-warnings"></ul>
      </div>
    </div>

    <div class="card" style="margin-top:12px">
      <h3>🏆 今日可观察候选 (<span id="inst-candidate-count">0</span> 只)</h3>
      <div style="overflow-x:auto"><table id="inst-table">
        <thead><tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th><th>建仓分</th><th>钓鱼分</th><th>派发分</th><th>置信度</th><th>净判断</th><th>跟随窗口</th><th>动作</th><th style="min-width:260px">正证据</th><th style="min-width:220px">反证据</th><th>操作</th></tr></thead>
        <tbody></tbody>
      </table></div>
    </div>

    <div class="cards" style="margin-top:12px">
      <div class="card">
        <h3>🚫 高风险钓鱼名单</h3>
        <div id="inst-bait-list"></div>
      </div>
      <div class="card">
        <h3>🔍 单票证据面板</h3>
        <div id="inst-detail-empty" style="font-size:12px;color:var(--text-dim);line-height:1.7">选择左侧股票查看证据拆解、正反信号和原始事件时间线。</div>
        <div id="inst-detail-panel" style="display:none">
          <div class="kpi-row" id="inst-detail-kpis"></div>
          <div id="inst-detail-summary" style="font-size:12px;color:var(--text-dim);line-height:1.7"></div>
          <div class="cards" style="margin-top:12px">
            <div class="card">
              <h3>✅ 正证据</h3>
              <ul class="rules-list" id="inst-positive-list"></ul>
            </div>
            <div class="card">
              <h3>⚠️ 反证据</h3>
              <ul class="rules-list" id="inst-negative-list"></ul>
            </div>
          </div>
          <div style="margin-top:12px;overflow-x:auto"><table id="inst-timeline-table">
            <thead><tr><th>日期</th><th>来源</th><th>类型</th><th>事件</th><th>分值</th></tr></thead>
            <tbody></tbody>
          </table></div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ Add Position (加仓推荐) Tab ═══ -->
<div class="tab-content" id="tab-add-position">
  <div class="card" style="margin-bottom:14px;border-left:3px solid var(--green)">
    <h3>📈 加仓推荐</h3>
    <p style="font-size:12px;color:var(--text-dim);margin-bottom:12px">自动扫描当前持仓，结合技术面 + 主力吸筹 + 资金面 + 风控检查，生成加仓推荐。一键执行复用建仓下单链路。</p>
    <div class="form-row">
      <label>加仓预算(¥)</label>
      <input type="number" id="ap-budget" value="100000" style="width:140px">
      <label>最大加仓比例</label>
      <select id="ap-max-add-pct" style="width:100px">
        <option value="25">25%</option>
        <option value="50" selected>50%</option>
        <option value="75">75%</option>
        <option value="100">100%</option>
      </select>
      <label>最低置信度</label>
      <input type="number" id="ap-min-confidence" value="60" min="30" max="95" style="width:70px">%
    </div>
    <div class="form-row">
      <label>数据源</label>
      <select id="ap-source">
        <option value="auto" selected>自动（优先实盘持仓）</option>
        <option value="live">仅实盘持仓</option>
        <option value="plan">仅投资计划</option>
      </select>
      <label style="margin-left:16px;cursor:pointer"><input type="checkbox" id="ap-check-accumulation" checked> 参考主力吸筹信号</label>
      <label style="margin-left:16px;cursor:pointer"><input type="checkbox" id="ap-check-risk" checked> 启用风控检查</label>
    </div>
    <div class="btn-group" style="gap:8px">
      <button class="btn btn-primary" id="btn-ap-run" onclick="runAddPositionRecommend()">📈 生成加仓推荐</button>
      <button class="btn" id="btn-ap-refresh" onclick="loadAddPositionPortfolio()">🔄 刷新持仓</button>
    </div>
    <div id="ap-status" style="font-size:12px;color:var(--text-dim);margin-top:8px"></div>
  </div>

  <!-- Current Holdings Summary -->
  <div class="kpi-row" id="ap-portfolio-kpis"></div>

  <!-- Recommendation Results -->
  <div id="ap-results" style="display:none">
    <div class="kpi-row" id="ap-rec-kpis"></div>

    <!-- Recommendation cards -->
    <div id="ap-rec-cards"></div>

    <!-- Execution panel -->
    <div class="card" style="margin-top:14px;border:1px solid rgba(13,148,136,.35)">
      <h3>⚡ 加仓执行</h3>
      <div class="form-row" style="align-items:center;gap:10px;flex-wrap:wrap">
        <label>执行比例</label>
        <input type="number" id="ap-exec-pct" min="10" max="100" value="100" style="width:80px">%
        <label>跟踪止损</label>
        <input type="number" id="ap-exec-trailing" min="2" max="20" value="8" style="width:70px">%
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" id="ap-exec-conds" checked>
          自动创建止损/止盈条件单
        </label>
      </div>
      <div class="btn-group" style="margin-top:8px;flex-wrap:wrap;gap:6px">
        <button class="btn" id="btn-ap-dry-run" onclick="executeAddPosition(true)">🧪 试运行（不下单）</button>
        <button class="btn btn-primary" id="btn-ap-exec" onclick="executeAddPosition(false)">🚀 执行加仓（真实下单）</button>
        <button class="btn" id="btn-ap-premarket" onclick="executeAddPosition(false,true)" title="跳过交易时段检查，直接提交限价委托到QMT">📋 预埋委托（盘前下单）</button>
      </div>
      <div id="ap-exec-mode-hint" style="font-size:12px;margin-top:6px;padding:4px 8px;border-radius:4px;display:none"></div>
      <div id="ap-exec-status" style="font-size:12px;color:var(--text-dim);margin-top:4px"></div>
      <div id="ap-exec-result" style="margin-top:8px"></div>
    </div>

    <!-- Detail table -->
    <div class="card" style="margin-top:14px">
      <h3>📋 加仓推荐明细 <span id="ap-sel-hint" style="font-size:12px;font-weight:normal;color:var(--text-dim);margin-left:8px"></span></h3>
      <div style="overflow-x:auto">
        <table id="ap-table">
          <thead><tr><th style="width:32px"><input type="checkbox" id="ap-select-all" checked onchange="toggleApSelectAll(this.checked)"></th><th>#</th><th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th><th>持仓</th><th>盈亏%</th><th>建议加仓</th><th>金额</th><th>加仓入场</th><th>止盈/止损</th><th>置信度</th><th>评分</th><th style="min-width:240px">推荐理由</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <!-- Skip list -->
    <div id="ap-skip-panel" style="display:none;margin-top:14px" class="card">
      <h3 style="color:var(--text-dim)">⏭️ 不建议加仓</h3>
      <div id="ap-skip-list"></div>
    </div>
  </div>
</div>

<!-- ═══ Rebalance Tab ═══ -->
<div class="tab-content" id="tab-rebalance">
  <div id="rebalance-mode-hint" style="font-size:12px;margin-bottom:8px;padding:4px 8px;border-radius:4px;display:none"></div>

  <!-- KPI summary row -->
  <div class="kpi-row" id="rebalance-portfolio-kpis"></div>

  <!-- Current Holdings with interactive action controls -->
  <div class="card" style="margin-top:8px">
    <h3>⚖️ 当前持仓 · 调仓操作 <span style="flex:1"></span>
      <button class="btn btn-sm" onclick="loadRebalancePortfolio()">🔄 刷新持仓</button>
      <button class="btn btn-sm btn-primary" onclick="showAddNewPositionRow()">➕ 新购股票</button>
    </h3>
    <p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">选择每个仓位的操作：持有、加仓、减仓或卖出，并设置具体参数后提交</p>
    <div id="rebalance-holdings-status" style="font-size:12px;color:var(--text-dim);margin-bottom:8px"></div>
    <div style="overflow-x:auto"><table id="rebalance-holdings-table" style="font-size:13px">
      <thead><tr>
        <th>代码</th><th>名称</th><th>持仓</th><th>成本</th><th>现价</th><th>盈亏%</th><th>仓位%</th>
        <th style="min-width:100px">操作</th>
        <th style="min-width:140px">参数</th>
        <th style="min-width:100px">策略</th>
        <th>备注</th>
      </tr></thead>
      <tbody id="rebalance-holdings-body"></tbody>
      <tbody id="rebalance-new-rows"></tbody>
    </table></div>
  </div>

  <!-- Action buttons -->
  <div class="card" style="margin-top:10px">
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <button class="btn btn-primary" id="btn-submit-rebalance" onclick="submitManualRebalance()">📋 提交调仓计划</button>
      <button class="btn" id="btn-ai-rebalance" onclick="runRebalance()">🤖 AI 自动建议</button>
      <button class="btn" id="btn-smart-strategy" onclick="runSmartStrategy()" style="background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;border:none">🧠 智能策略诊断</button>
      <button class="btn" onclick="resetRebalanceActions()">↩️ 重置操作</button>
      <span id="rebalance-status" style="font-size:12px;color:var(--text-dim)"></span>
    </div>
  </div>

  <!-- Execution panel (shown after plan submission) -->
  <div id="rebalance-exec-panel" style="display:none;margin-top:12px">
    <div class="card" style="border-left:3px solid var(--green)">
      <h3>🚀 调仓执行 <span style="flex:1"></span>
        <button class="btn btn-sm" onclick="document.getElementById('rebalance-exec-panel').style.display='none'">✕ 关闭</button>
      </h3>
      <div id="rebalance-exec-summary" style="margin-bottom:10px"></div>
      <div class="kpi-row" id="rebalance-exec-kpis" style="margin-bottom:10px"></div>
      <div style="overflow-x:auto"><table id="rebalance-exec-table" style="font-size:12px">
        <thead><tr><th>操作</th><th>代码</th><th>名称</th><th>价格</th><th>数量</th><th>金额</th><th>策略</th><th>状态</th></tr></thead>
        <tbody></tbody>
      </table></div>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px">
        <label style="font-size:12px">条件单 <input type="checkbox" id="rb-exec-conds" checked></label>
        <label style="font-size:12px">移动止损%
          <input type="number" id="rb-exec-trailing" value="8" min="2" max="20" step="0.5" style="width:60px;font-size:12px">
        </label>
        <span style="flex:1"></span>
        <button class="btn" id="btn-rb-dry-run" onclick="executeRebalancePlan(true)">🧪 试运行</button>
        <button class="btn btn-primary" id="btn-rb-exec" onclick="executeRebalancePlan(false)" style="background:var(--red);border-color:var(--red)">⚡ 实盘执行</button>
      </div>
      <div id="rebalance-exec-status" style="font-size:12px;color:var(--text-dim);margin-top:8px"></div>
      <div id="rebalance-exec-result" style="margin-top:8px"></div>
    </div>
  </div>

  <!-- Smart strategy diagnosis results -->
  <div id="smart-strategy-results" style="display:none;margin-top:12px">
    <div class="card" style="border-left:3px solid #8b5cf6">
      <h3>🧠 智能策略诊断报告 <span style="flex:1"></span>
        <button class="btn btn-sm" onclick="applySmartStrategyToTable()">📥 应用建议到持仓表</button>
        <button class="btn btn-sm" onclick="document.getElementById('smart-strategy-results').style.display='none'">✕ 关闭</button>
      </h3>
      <div id="smart-strategy-macro" style="margin-bottom:10px"></div>
      <div class="kpi-row" id="smart-strategy-kpis"></div>
      <div id="smart-strategy-cards" style="margin-top:10px"></div>
    </div>
  </div>

  <!-- AI auto-generated results (shown after AI suggest) -->
  <div id="rebalance-results" style="display:none;margin-top:12px">
    <div class="kpi-row" id="rebalance-kpis"></div>
    <div id="rebalance-actions"></div>
    <div class="card" style="margin-top:12px">
      <h3>📋 AI 调仓明细 <span style="flex:1"></span>
        <button class="btn btn-sm" onclick="applyAiRebalanceToTable()">📥 应用到持仓表</button>
      </h3>
      <div style="overflow-x:auto"><table id="rebalance-table">
        <thead><tr><th>操作</th><th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th><th>目标仓位</th><th>当前仓位</th><th>建议数量</th><th>理由</th></tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
  </div>
</div>

<!-- ═══ Swing Overlay Tab (持仓波段) ═══ -->
<div class="tab-content" id="tab-swing-overlay">
  <div class="card" style="margin-bottom:16px">
    <h3>💹 持仓波段套利
      <span style="flex:1"></span>
      <button class="btn btn-sm" onclick="scanSwingSuitability()">🔍 扫描适合度</button>
      <button class="btn btn-sm" onclick="refreshSwingDashboard()" style="margin-left:8px">🔄 刷新状态</button>
    </h3>
    <div style="font-size:13px;color:var(--text-dim);margin-bottom:12px">
      在整体上涨的持仓股票上，保留核心仓位不动，用波段仓位在动态网格内高抛低吸，钻取振荡利润。<br>
      <strong>操作流程：</strong>① 扫描持仓适合度 → ② 选择高分股票配置参数 → ③ 激活波段监控 → ④ 系统自动创建网格条件单
    </div>
    <div id="swing-scan-results">
      <div style="text-align:center;padding:30px;color:var(--text-dim)">点击"🔍 扫描适合度"开始分析持仓股票的波段特征</div>
    </div>
  </div>

  <div class="card">
    <h3>📊 活跃波段监控</h3>
    <div id="swing-active-dashboard">
      <div style="text-align:center;padding:30px;color:var(--text-dim)">暂无激活的波段监控</div>
    </div>
  </div>

  <!-- Swing Chart Modal -->
  <div id="swing-chart-modal" style="display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.75);align-items:center;justify-content:center">
    <div style="background:var(--bg2);border-radius:12px;width:92vw;max-width:1200px;max-height:90vh;overflow:auto;padding:20px;position:relative">
      <div style="display:flex;align-items:center;margin-bottom:12px">
        <h3 id="swing-chart-title" style="margin:0;flex:1">📈 K线图</h3>
        <div style="display:flex;gap:4px;margin-right:12px">
          <button id="swing-chart-mode-kline" class="btn btn-sm btn-primary" onclick="switchSwingChartMode('kline')">日K</button>
          <button id="swing-chart-mode-tick" class="btn btn-sm" onclick="switchSwingChartMode('tick')">分笔</button>
        </div>
        <select id="swing-chart-period" onchange="loadSwingChart(window._swingChartSymbol)" style="margin-right:12px;padding:4px 8px;background:var(--bg3);color:var(--text);border:1px solid var(--border);border-radius:4px">
          <option value="30">30天</option>
          <option value="60" selected>60天</option>
          <option value="120">120天</option>
          <option value="250">一年</option>
        </select>
        <button onclick="closeSwingChart()" style="background:none;border:none;color:var(--text-dim);font-size:24px;cursor:pointer;padding:0 8px">✕</button>
      </div>
      <div id="swing-chart-container" style="width:100%;height:420px;background:#161b22;border-radius:8px"></div>
      <div id="swing-chart-legend" style="display:flex;gap:16px;margin-top:8px;font-size:12px;color:var(--text-dim)">
        <span>🟢 买入点</span><span>🔴 卖出点</span><span style="color:#f39c12">━ 网格线</span><span style="color:#58a6ff">━ MA中心</span>
      </div>
    </div>
  </div>
</div>

<!-- ═══ Signals Tab ═══ -->
<div class="tab-content" id="tab-signals">
  <div class="kpi-row" id="signal-kpis"></div>
  <div id="signal-list"></div>
  <div class="card" style="margin-top:12px">
    <h3>📜 信号历史 <span id="signal-history-mode-badge" style="font-size:12px;margin-left:8px"></span></h3>
    <div style="overflow-x:auto"><table id="signal-history-table">
      <thead><tr><th>时间</th><th>类型</th><th>股票</th><th>操作</th><th>价格</th><th>盈亏%</th><th>消息</th></tr></thead>
      <tbody></tbody>
    </table></div>
  </div>
</div>

<!-- ═══ Milestones Tab ═══ -->
<div class="tab-content" id="tab-milestones">
  <div class="kpi-row" id="milestone-kpis"></div>
  <div class="card">
    <h3>📊 月度目标进度</h3>
    <div id="milestone-chart"></div>
  </div>
  <div class="card" style="margin-top:12px">
    <h3>📅 里程碑明细</h3>
    <div style="overflow-x:auto"><table id="milestone-table">
      <thead><tr><th>月份</th><th>目标资金</th><th>目标收益率</th><th>实际资金</th><th>实际收益率</th><th>状态</th></tr></thead>
      <tbody></tbody>
    </table></div>
  </div>
</div>

<!-- ═══ Accumulation Tab (主力吸筹) ═══ -->
<div class="tab-content" id="tab-accumulation">
  <div class="card">
    <h3>🧲 主力吸筹检测</h3>
    <p style="font-size:12px;color:var(--text-dim);margin-bottom:10px">
      通过资金流向、筹码分布、量价结构、换手率和大单净量五个维度综合判断个股是否有主力吸筹迹象。
    </p>
    <div class="form-row">
      <label>股票代码</label>
      <input type="text" id="acc-symbol" placeholder="如 600519" style="width:120px">
      <label>回看天数</label>
      <input type="number" id="acc-lookback" value="30" min="10" max="120" style="width:70px">
      <label>观察日</label>
      <input type="text" id="acc-date" placeholder="YYYY-MM-DD" style="width:130px">
      <button class="btn btn-primary" id="btn-acc-detect" onclick="detectAccumulation()">🔍 检测吸筹</button>
      <button class="btn" onclick="detailAccumulation()">📊 详细分析</button>
    </div>
    <div class="form-row" style="margin-top:8px">
      <label>批量扫描</label>
      <input type="text" id="acc-symbols" placeholder="留空=扫描活跃股; 多只用逗号分隔" style="width:300px">
      <label>最低分</label>
      <input type="number" id="acc-min-score" value="40" min="0" max="100" style="width:60px">
      <button class="btn" id="btn-acc-scan" onclick="scanAccumulation()">🔎 批量扫描</button>
      <button class="btn" id="btn-acc-top3" onclick="scanAccumulationTop3()">🏆 复合前三</button>
    </div>
    <div class="form-row" style="margin-top:8px;flex-wrap:wrap;gap:8px 12px">
      <label>阈值预设</label>
      <select id="acc-top3-preset" onchange="onAccumulationTop3PresetChange()" style="width:130px">
        <option value="stable">稳健</option>
        <option value="swing" selected>波段</option>
        <option value="shortline">短线</option>
        <option value="custom">自定义</option>
      </select>
      <button class="btn btn-sm" style="padding:2px 8px;font-size:11px" onclick="resetAccumulationTop3Preset()">重置波段</button>
      <span id="acc-top3-preset-hint" style="font-size:12px;color:var(--text-dim)">当前: 波段预设</span>
      <span style="font-size:12px;color:var(--text-dim)">稳健=更严格，波段=均衡，短线=更敏感</span>
      <span class="acc-top3-help-wrap" id="acc-top3-preset-help-wrap">
        <button type="button" class="btn btn-sm" id="acc-top3-preset-help" style="padding:2px 8px;font-size:11px" onclick="toggleAccumulationTop3PresetHelp(event)" aria-haspopup="dialog" aria-expanded="false" aria-controls="acc-top3-preset-help-panel">档位对比 ⓘ</button>
        <div id="acc-top3-preset-help-panel" class="acc-top3-help-panel" style="display:none" role="dialog" aria-label="建议参数档位对比" aria-hidden="true" tabindex="-1"></div>
      </span>
      <label>前期吸筹</label>
      <input type="number" id="acc-top3-pre-days" value="10" min="5" max="30" style="width:58px">
      <span style="font-size:12px;color:var(--text-dim)">天内放量≥</span>
      <input type="number" id="acc-top3-pre-count" value="2" min="1" max="6" style="width:54px">
      <span style="font-size:12px;color:var(--text-dim)">次，放量≥</span>
      <input type="number" id="acc-top3-pre-vol" value="1.5" min="1" max="5" step="0.1" style="width:58px">
      <span style="font-size:12px;color:var(--text-dim)">x</span>
      <label style="margin-left:8px">机构抢筹</label>
      <span style="font-size:12px;color:var(--text-dim)">涨幅≥</span>
      <input type="number" id="acc-top3-rush-change" value="5" min="1" max="20" step="0.5" style="width:58px">
      <span style="font-size:12px;color:var(--text-dim)">% 量比≥</span>
      <input type="number" id="acc-top3-rush-vol" value="2" min="1" max="6" step="0.1" style="width:58px">
      <span style="font-size:12px;color:var(--text-dim)">x 换手≥</span>
      <input type="number" id="acc-top3-rush-turnover" value="3" min="0" max="30" step="0.5" style="width:58px">
      <span style="font-size:12px;color:var(--text-dim)">%</span>
      <label style="margin-left:8px">启动前异动</label>
      <span style="font-size:12px;color:var(--text-dim)">筹码≥</span>
      <input type="number" id="acc-top3-anomaly-chip" value="8" min="0" max="20" step="0.5" style="width:58px">
      <span style="font-size:12px;color:var(--text-dim)">量价≥</span>
      <input type="number" id="acc-top3-anomaly-vp" value="6" min="0" max="20" step="0.5" style="width:58px">
      <span style="font-size:12px;color:var(--text-dim)">换手≥</span>
      <input type="number" id="acc-top3-anomaly-turnover" value="6" min="0" max="20" step="0.5" style="width:58px">
    </div>
    <div style="font-size:12px;color:var(--text-dim);margin-top:6px">
      复合前三按当前阈值判断：前期吸筹、机构抢筹，以及技术壁垒或重组信息共振，并不等同于纯吸筹总分前三。
    </div>
    <div id="acc-status" style="font-size:12px;color:var(--text-dim);margin-top:6px"></div>
  </div>

  <!-- Single stock result -->
  <div id="acc-result" style="display:none;margin-top:12px">
    <div class="kpi-row" id="acc-kpis"></div>
    <div class="cards" style="margin-top:12px">
      <div class="card">
        <h3>📊 维度得分</h3>
        <div id="acc-dimensions"></div>
      </div>
      <div class="card">
        <h3>🔍 信号明细</h3>
        <ul class="rules-list" id="acc-signals"></ul>
      </div>
    </div>
  </div>

  <!-- Scan results table -->
  <div id="acc-scan-results" style="display:none;margin-top:12px">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
        <h3 style="margin:0">🧲 吸筹候选 (<span id="acc-scan-count">0</span> 只)</h3>
        <div style="display:flex;align-items:center;gap:10px;font-size:12px;flex-wrap:wrap">
          <label style="min-width:auto">总分≥</label>
          <input type="number" id="acc-scan-filter-total" value="0" min="0" max="100" step="5" style="width:58px" onchange="onAccumulationScanFilterChange()">
          <label style="min-width:auto">筹码分≥</label>
          <input type="number" id="acc-scan-filter-chip" value="0" min="0" max="20" step="1" style="width:58px" onchange="onAccumulationScanFilterChange()">
        </div>
      </div>
      <div style="overflow-x:auto"><table id="acc-scan-table">
        <thead><tr>
          <th>代码</th><th>名称</th><th>现价</th><th>总分</th><th>等级</th>
          <th>资金流</th><th>筹码</th><th>量价</th><th>换手</th><th>大单</th>
          <th>结论</th><th>操作</th>
        </tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
  </div>

  <div id="acc-top3-results" style="display:none;margin-top:12px">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
        <h3 style="margin:0">🏆 复合条件前三 (<span id="acc-top3-count">0</span> 只)</h3>
        <div style="display:flex;align-items:center;gap:10px;font-size:12px;flex-wrap:wrap">
          <label style="min-width:auto">入选主因</label>
          <select id="acc-top3-reason-filter" onchange="onAccumulationTop3ReasonFilterChange()" style="width:170px">
            <option value="all">全部</option>
            <option value="resonance">吸筹与抢筹共振</option>
            <option value="pre">前期吸筹主导</option>
            <option value="rush">机构抢筹主导</option>
            <option value="chip">纯吸筹评分达标</option>
          </select>
          <label style="display:inline-flex;align-items:center;gap:4px;min-width:auto">
            <input type="checkbox" id="acc-top3-backbone-only" onchange="onAccumulationTop3ReasonFilterChange()">
            只看纯吸筹骨架
          </label>
        </div>
      </div>
      <div id="acc-top3-summary" style="font-size:12px;color:var(--text-dim);margin:6px 0 10px"></div>
      <div style="overflow-x:auto"><table id="acc-top3-table">
        <thead><tr>
          <th>排名</th><th>代码</th><th>名称</th><th>命中条件</th><th>入选原因</th><th>综合判断</th>
          <th>吸筹分</th><th>建仓/置信</th><th>壁垒/重组</th><th>说明</th><th>操作</th>
        </tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
  </div>

  <!-- Detail with chart data -->
  <div id="acc-detail" style="display:none;margin-top:12px">
    <div class="card">
      <h3>📈 资金流向趋势</h3>
      <div id="acc-flow-chart" style="overflow-x:auto">
        <table id="acc-flow-table" style="font-size:12px">
          <thead><tr><th>日期</th><th>主力净流入</th><th>超大单</th><th>大单</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- ═══ Backtest Tab ═══ -->
<div class="tab-content" id="tab-backtest">
  <div class="cards">
    <div class="card">
      <h3>🧪 回测参数</h3>
      <div class="form-row">
        <label>起始日期</label>
        <input type="text" id="bt-start" value="" style="width:120px" placeholder="YYYY-MM-DD">
        <label>结束日期</label>
        <input type="text" id="bt-end" value="" style="width:120px" placeholder="YYYY-MM-DD">
        <span style="margin-left:8px;font-size:12px">
          <button class="btn btn-sm" onclick="setBtPeriod(3)" style="padding:2px 8px;font-size:11px">近3月</button>
          <button class="btn btn-sm" onclick="setBtPeriod(6)" style="padding:2px 8px;font-size:11px">近半年</button>
          <button class="btn btn-sm btn-primary" onclick="setBtPeriod(12)" style="padding:2px 8px;font-size:11px">近1年</button>
        </span>
      </div>
      <div class="form-row">
        <label>初始资金</label>
        <input type="number" id="bt-capital" value="500000" style="width:140px">
        <label>调仓周期(天)</label>
        <input type="number" id="bt-rebalance" value="14" min="1" max="90" style="width:60px">
      </div>
      <div class="form-row">
        <label>风险偏好</label>
        <select id="bt-risk">
          <option value="low">稳健型</option>
          <option value="medium" selected>均衡型</option>
          <option value="high">进取型</option>
          <option value="aggressive">激进型</option>
        </select>
        <label>佣金费率</label>
        <input type="text" id="bt-commission" value="0.0003" style="width:80px">
        <label>印花税</label>
        <input type="text" id="bt-tax" value="0.001" style="width:80px">
      </div>
      <div class="form-row">
        <label>股票代码</label>
        <input type="text" id="bt-symbols" placeholder="留空=自动选股; 多个用逗号分隔: 600519,000858" style="width:320px">
      </div>
      <div class="btn-group">
        <button class="btn btn-primary" id="btn-bt-single" onclick="runSingleBacktest()">▶️ 单策略回测</button>
        <button class="btn btn-warning" id="btn-bt-compare" onclick="runCompareBacktest()">⚡ 多策略对比</button>
        <button class="btn" id="btn-bt-multi-period" onclick="runMultiPeriodBacktest()" style="background:var(--purple,#bc8cff);color:#fff">📊 多周期全策略对比</button>
      </div>
      <div class="form-row" style="margin-top:6px">
        <label>单策略</label>
        <select id="bt-strategy">
          <option value="momentum">趋势动量</option>
          <option value="value">价值投资</option>
          <option value="growth">成长投资</option>
          <option value="swing">波段交易</option>
          <option value="mixed">混合策略</option>
          <option value="manipulation">跟庄策略</option>
        </select>
      </div>
      <div id="bt-status" style="font-size:12px;color:var(--text-dim);margin-top:8px"></div>
    </div>
  </div>

  <!-- Single Backtest Results -->
  <div id="bt-single-results" style="display:none">
    <div class="kpi-row" id="bt-kpis"></div>
    <div class="cards">
      <div class="card">
        <h3>📈 净值曲线</h3>
        <div class="bt-chart"><canvas id="bt-equity-canvas"></canvas></div>
      </div>
      <div class="card">
        <h3>📅 月度收益</h3>
        <div id="bt-monthly-chart" style="height:120px;display:flex;align-items:flex-end;gap:3px;padding:8px 4px"></div>
      </div>
    </div>
    <div class="cards">
      <div class="card">
        <h3>📋 交易记录 (<span id="bt-trade-count">0</span> 笔)</h3>
        <div style="overflow-x:auto;max-height:400px;overflow-y:auto">
          <table id="bt-trades-table">
            <thead><tr><th>日期</th><th>股票</th><th>方向</th><th>数量</th><th>价格</th><th>盈亏</th><th>盈亏%</th><th>佣金</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <h3>💼 期末持仓</h3>
        <div style="overflow-x:auto">
          <table id="bt-final-pos-table">
            <thead><tr><th>代码</th><th>名称</th><th>数量</th><th>成本</th><th>现价</th><th>盈亏</th><th>盈亏%</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- Compare Results -->
  <div id="bt-compare-results" style="display:none">
    <div class="card" style="margin-bottom:16px">
      <h3>🏆 策略对比总览</h3>
      <div id="bt-compare-summary"></div>
    </div>
    <div class="card" style="margin-bottom:16px">
      <h3>📈 净值曲线对比</h3>
      <div class="bt-chart" style="height:320px"><canvas id="bt-compare-canvas"></canvas></div>
      <div class="bt-legend" id="bt-compare-legend"></div>
    </div>
    <div class="bt-compare-grid" id="bt-compare-cards"></div>
  </div>

  <!-- Multi-Period Results -->
  <div id="bt-multi-period-results" style="display:none">
    <div class="card" style="margin-bottom:16px">
      <h3>📊 多周期全策略对比</h3>
      <p style="color:var(--text-dim);font-size:12px;margin:4px 0 8px">同时对比所有策略在近3个月、半年、1年三个周期的表现</p>
      <div id="bt-multi-period-summary"></div>
    </div>
    <div id="bt-multi-period-charts"></div>
  </div>
</div>

<!-- ═══ Recommend Tab ═══ -->
<div class="tab-content" id="tab-recommend">
  <!-- Pool Optimizer -->
  <div class="card" style="margin-bottom:14px;border:1px solid var(--border)">
    <h3 style="cursor:pointer;user-select:none" onclick="toggleOptimizer()">
      🔬 股票池智能寻优
      <span id="opt-toggle" style="float:right;font-size:12px;color:var(--text-dim)">▶ 展开</span>
    </h3>
    <p style="color:var(--text-dim);font-size:12px;margin:4px 0 0">自动测试多种 股票池×策略×风险×调仓 组合，找到最优方案并回填到下方建仓参数</p>
    <div id="opt-panel" style="display:none;margin-top:12px">
      <div class="form-row">
        <label>资金</label>
        <input type="number" id="opt-capital" value="500000" style="width:140px">
        <label>回测起始</label>
        <input type="text" id="opt-start" value="2025-06-01" style="width:120px">
        <label>回测结束</label>
        <input type="text" id="opt-end" value="" placeholder="留空=今天" style="width:120px">
      </div>
      <div class="form-row">
        <label>最大轮次</label>
        <input type="number" id="opt-rounds" value="16" min="3" max="30" style="width:60px">
        <label style="margin-left:18px">
          <input type="checkbox" id="opt-auto-screen" checked style="vertical-align:middle;margin-right:4px">
          <strong>🌐 全市场自动筛选</strong>
          <span style="font-size:11px;color:var(--text-dim)">(从~2000+只 A股中自动构建股票池)</span>
        </label>
      </div>
      <div id="opt-screen-hint" style="font-size:11px;color:var(--accent);margin:4px 0 8px 0">
        ℹ️ 开启全市场筛选后，系统将通过 Sina Finance API 抓取全市场股票，按市值/PE/动量/行业分组生成约8个智能股票池，再进行多轮回测对比。关闭则使用预设股票池。
      </div>
      <div class="btn-group">
        <button class="btn btn-warning" id="btn-opt-run" onclick="runOptimizer()">🔬 开始寻优（约3-8分钟）</button>
      </div>
      <div id="opt-status" style="font-size:12px;color:var(--text-dim);margin-top:8px"></div>

      <div id="opt-results" style="display:none;margin-top:14px">
        <!-- Screening summary (only shown in auto mode) -->
        <div id="opt-screening-card" class="card" style="margin-bottom:14px;border:1px solid var(--accent);display:none">
          <h3 style="color:var(--accent)">\ud83c\udf10 \u5168\u5e02\u573a\u7b5b\u9009\u7ed3\u679c</h3>
          <div class="kpi-row" id="opt-screen-kpis"></div>
          <div id="opt-screen-pools" style="margin-top:8px"></div>
        </div>

        <!-- Best recommendation -->
        <div class="card" style="background:rgba(63,185,80,0.08);border:1px solid var(--green);margin-bottom:14px">
          <h3 style="color:var(--green)">🏆 最优方案</h3>
          <div id="opt-best-text" style="font-size:14px;line-height:1.8"></div>
          <div class="kpi-row" id="opt-best-kpis" style="margin-top:10px"></div>
          <div class="btn-group" style="margin-top:10px">
            <button class="btn btn-primary" onclick="applyOptimalPool()">✅ 采用此方案（回填到下方参数）</button>
          </div>
        </div>

        <!-- All rounds table -->
        <div class="card">
          <h3>📊 全部测试轮次 (<span id="opt-round-count">0</span> 轮)</h3>
          <div style="overflow-x:auto">
            <table id="opt-table">
              <thead><tr><th>排名</th><th>股票池</th><th>只数</th><th>策略</th><th>风险</th><th>调仓</th><th>收益%</th><th>夏普</th><th>回撤%</th><th>胜率%</th><th>利润因子</th><th>综合分</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Optimization History -->
  <div class="card" style="margin-bottom:14px;border:1px solid var(--border)">
    <h3 style="display:flex;align-items:center;gap:8px">🕘 寻优历史记录 <span style="flex:1"></span><button class="btn btn-sm" onclick="loadOptHistory()">🔄 刷新</button></h3>
    <div id="opt-history-status" style="font-size:12px;color:var(--text-dim);margin-bottom:8px">暂无历史</div>
    <div id="opt-history-panel" style="display:none">
      <div style="overflow-x:auto"><table id="opt-history-table" style="font-size:12px">
        <thead><tr><th>#</th><th>时间</th><th>资金</th><th>起始日</th><th>轮次</th><th>筛选模式</th><th>最优股票池</th><th>最优策略</th><th>收益%</th><th>夏普</th><th>回撤%</th><th>股票数</th><th>操作</th></tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
  </div>

  <!-- Optimization History Detail Modal -->
  <div id="opt-history-detail-modal" style="display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.6);z-index:1000;overflow-y:auto">
    <div style="max-width:1200px;margin:40px auto;background:var(--bg);border-radius:12px;padding:24px;position:relative">
      <button onclick="closeOptHistoryDetail()" style="position:absolute;top:12px;right:16px;background:none;border:none;color:var(--text);font-size:20px;cursor:pointer">✕</button>
      <h3 id="opt-detail-title" style="margin-bottom:12px">寻优详情</h3>
      <div id="opt-detail-content"></div>
    </div>
  </div>

  <!-- Strategy Backtest Detail Modal -->
  <div id="strategy-detail-modal" style="display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.65);z-index:1000;overflow-y:auto">
    <div style="max-width:1100px;margin:30px auto;background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:24px;position:relative">
      <button onclick="closeStrategyDetail()" style="position:absolute;top:12px;right:16px;background:none;border:none;color:var(--text);font-size:22px;cursor:pointer;z-index:1">✕</button>
      <div id="strategy-detail-header" style="margin-bottom:16px"></div>
      <div id="strategy-detail-kpis" class="kpi-row" style="margin-bottom:16px"></div>

      <div class="card" style="margin-bottom:14px">
        <h3 style="font-size:14px;margin-bottom:8px">📊 综合分计算过程</h3>
        <div id="strategy-detail-score-breakdown"></div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h3 style="font-size:14px;margin-bottom:8px">📈 净值曲线</h3>
        <div class="bt-chart" style="height:260px"><canvas id="sd-equity-canvas"></canvas></div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h3 style="font-size:14px;margin-bottom:8px">📅 月度收益</h3>
        <div id="sd-monthly-chart" style="height:140px;display:flex;align-items:flex-end;gap:3px;padding:8px 4px;overflow-x:auto"></div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h3 style="font-size:14px;margin-bottom:8px">🎯 策略候选股票</h3>
        <div style="overflow-x:auto"><table id="sd-candidates-table" style="font-size:12px">
          <thead><tr><th>排名</th><th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th><th>筛选评分</th><th>入选理由</th></tr></thead>
          <tbody></tbody>
        </table></div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h3 style="font-size:14px;margin-bottom:8px">📝 交易记录（近100笔）</h3>
        <div style="overflow-x:auto;max-height:360px;overflow-y:auto"><table id="sd-trades-table" style="font-size:11px">
          <thead><tr><th>日期</th><th>代码</th><th>名称</th><th>方向</th><th>价格</th><th>股数</th><th>金额</th><th>盈亏</th><th>盈亏%</th><th>佣金</th></tr></thead>
          <tbody></tbody>
        </table></div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h3 style="font-size:14px;margin-bottom:8px">💼 期末持仓</h3>
        <div style="overflow-x:auto"><table id="sd-positions-table" style="font-size:12px">
          <thead><tr><th>代码</th><th>名称</th><th>持仓</th><th>成本</th><th>现价</th><th>盈亏</th><th>盈亏%</th></tr></thead>
          <tbody></tbody>
        </table></div>
      </div>

      <div class="card">
        <h3 style="font-size:14px;margin-bottom:8px">📋 完整回测指标</h3>
        <div id="sd-full-summary" style="font-size:12px"></div>
      </div>
    </div>
  </div>

  <!-- Stock Candidate Detail Modal -->
  <div id="stock-detail-modal" style="display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.65);z-index:1000;overflow-y:auto">
    <div style="max-width:1000px;margin:30px auto;background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:24px;position:relative">
      <button onclick="closeStockDetail()" style="position:absolute;top:12px;right:16px;background:none;border:none;color:var(--text);font-size:22px;cursor:pointer;z-index:1">✕</button>
      <div id="stock-detail-header" style="margin-bottom:16px"></div>
      <div id="stock-detail-kpis" class="kpi-row" style="margin-bottom:16px"></div>

      <div class="card" style="margin-bottom:14px">
        <h3 style="font-size:14px;margin-bottom:8px">� 选股时间轴</h3>
        <div id="stock-detail-timeline"></div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h3 style="font-size:14px;margin-bottom:8px">�📊 综合分计算过程</h3>
        <div id="stock-detail-score-breakdown"></div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h3 style="font-size:14px;margin-bottom:8px">🔍 各策略贡献明细</h3>
        <div id="stock-detail-strategy-contributions"></div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h3 style="font-size:14px;margin-bottom:8px">📝 各策略选股理由</h3>
        <div id="stock-detail-reasons"></div>
      </div>

      <div class="card" style="margin-bottom:14px">
        <h3 style="font-size:14px;margin-bottom:8px">🧪 单股回测结果</h3>
        <div id="stock-detail-backtest"></div>
        <div id="stock-bt-equity-section" style="display:none;margin-top:12px">
          <h4 style="font-size:13px;margin-bottom:6px;color:var(--text-dim)">📈 净值曲线</h4>
          <div class="bt-chart" style="height:280px"><canvas id="stock-bt-equity-canvas"></canvas></div>
        </div>
        <div id="stock-bt-trades-section" style="display:none;margin-top:12px">
          <h4 style="font-size:13px;margin-bottom:6px;color:var(--text-dim)">📋 交易记录 <span id="stock-bt-trades-count" style="font-size:11px"></span></h4>
          <div style="overflow-x:auto;max-height:300px;overflow-y:auto"><table id="stock-bt-trades-table" style="font-size:11px">
            <thead><tr><th>日期</th><th>类型</th><th>代码</th><th>价格</th><th>数量</th><th>金额</th><th>收益%</th></tr></thead>
            <tbody></tbody>
          </table></div>
        </div>
        <div id="stock-bt-monthly-section" style="display:none;margin-top:12px">
          <h4 style="font-size:13px;margin-bottom:6px;color:var(--text-dim)">📅 月度收益</h4>
          <div id="stock-bt-monthly-chart" style="height:120px;display:flex;align-items:flex-end;gap:3px;padding:8px 4px;overflow-x:auto"></div>
        </div>
      </div>

      <div class="card">
        <h3 style="font-size:14px;margin-bottom:8px">🛣️ 自适应路由</h3>
        <div id="stock-detail-routing"></div>
      </div>
    </div>
  </div>

    <!-- Daily Composite Picker -->
    <div class="card" style="margin-bottom:14px;border:1px solid var(--accent)">
      <h3>🧭 每日综合选股</h3>
      <p style="color:var(--text-dim);font-size:12px;margin:4px 0 10px">自动运行多策略，选出前 2-3 个强势策略，合并候选池并筛出不超过 8 只股票，再进行收益/回撤/胜率排行，作为每日总和选股清单。</p>
      <div id="dp-regime-banner" style="display:none;margin-bottom:10px;padding:10px 14px;background:rgba(210,153,34,.12);border:1px solid #d29922;border-radius:8px;font-size:13px;color:#d29922"></div>
      <div class="form-row">
        <label>策略池</label>
        <button class="btn btn-sm" type="button" onclick="document.querySelectorAll('.dp-strategy').forEach(c=>c.checked=true)" style="padding:2px 8px;font-size:11px">全选</button>
        <button class="btn btn-sm" type="button" onclick="document.querySelectorAll('.dp-strategy').forEach(c=>c.checked=false)" style="padding:2px 8px;font-size:11px">全不选</button>
        <button class="btn btn-sm" type="button" onclick="selectDefensiveOnly()" style="padding:2px 8px;font-size:11px;background:#1a7f37;color:#fff;border-color:#1a7f37" title="仅选择均值回归/价值/防御类策略（适合下行市场）">🛡️ 防御模式</button>
        <button class="btn btn-sm" type="button" onclick="selectFitnessGlobalTop()" style="padding:2px 8px;font-size:11px;background:var(--purple);color:#fff;border-color:var(--purple)" title="从策略适配分析中加载全局排名前10的策略">🏆 全局最优</button>
        <button class="btn btn-sm" type="button" onclick="selectFitnessStockOptimal()" style="padding:2px 8px;font-size:11px;background:#1f6feb;color:#fff;border-color:#1f6feb" title="使用fitness_optimal策略（根据每只股票的历史最优策略选股）">🎯 个股最优</button>
        <button class="btn btn-sm" type="button" onclick="showFitnessRankingPanel()" style="padding:2px 8px;font-size:11px;border-color:var(--purple);color:var(--purple)" title="查看全局策略排名详情并自选">📊 排名选择</button>
        <span id="dp-fitness-badge" style="font-size:11px;color:var(--text-dim)"></span>
        <label style="display:none"><input type="checkbox" class="dp-strategy" value="fitness_optimal" style="vertical-align:middle;margin-right:4px">个股最优</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="momentum" checked style="vertical-align:middle;margin-right:4px">趋势动量</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="value" checked style="vertical-align:middle;margin-right:4px">价值投资</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="growth" checked style="vertical-align:middle;margin-right:4px">成长投资</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="swing" checked style="vertical-align:middle;margin-right:4px">波段交易</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="mixed" checked style="vertical-align:middle;margin-right:4px">混合策略</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="manipulation" checked style="vertical-align:middle;margin-right:4px">跟庄策略</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="spread-arbitrage" checked style="vertical-align:middle;margin-right:4px">价差套利</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="rsi-reversal" checked style="vertical-align:middle;margin-right:4px">RSI反转</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="vwap-reversion" checked style="vertical-align:middle;margin-right:4px">VWAP回归</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="mean-reversion-zscore" checked style="vertical-align:middle;margin-right:4px">均值回归Z-Score</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="williams-r" checked style="vertical-align:middle;margin-right:4px">威廉指标反转</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="momentum-breakout" checked style="vertical-align:middle;margin-right:4px">动量突破</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="range-breakout" checked style="vertical-align:middle;margin-right:4px">区间突破</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="dual-ma-crossover" checked style="vertical-align:middle;margin-right:4px">双均线交叉</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="trend-combo" checked style="vertical-align:middle;margin-right:4px">趋势合并</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="double-bottom" checked style="vertical-align:middle;margin-right:4px">双底双顶</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="macd-divergence" checked style="vertical-align:middle;margin-right:4px">MACD背离</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="volume-confirmation" checked style="vertical-align:middle;margin-right:4px">成交量确认</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="gated" checked style="vertical-align:middle;margin-right:4px">条件门控</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="scored" checked style="vertical-align:middle;margin-right:4px">加权仓位</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="mean-reversion" checked style="vertical-align:middle;margin-right:4px">均值回归</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="macd-confirmation" checked style="vertical-align:middle;margin-right:4px">MACD确认</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-kdj" checked style="vertical-align:middle;margin-right:4px">KDJ随机</label>
      </div>
      <div class="form-row" style="flex-wrap:wrap;gap:4px 8px">
        <label style="color:var(--accent);font-weight:600;font-size:11px;min-width:auto">组合/增强</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="composite" checked style="vertical-align:middle;margin-right:4px">综合投票</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="enhanced-composite" checked style="vertical-align:middle;margin-right:4px">增强综合</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="rsi-filter" checked style="vertical-align:middle;margin-right:4px">RSI动量过滤</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="rsi-enhanced" checked style="vertical-align:middle;margin-right:4px">RSI增强选股</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="rsi-macd-combined" checked style="vertical-align:middle;margin-right:4px">RSI+MACD组合</label>
      </div>
      <div class="form-row" style="flex-wrap:wrap;gap:4px 8px">
        <label style="color:var(--accent);font-weight:600;font-size:11px;min-width:auto">扩展技术指标</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="trix-momentum" checked style="vertical-align:middle;margin-right:4px">TRIX动量</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="mfi-oscillator" checked style="vertical-align:middle;margin-right:4px">MFI资金流</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="aroon-trend" checked style="vertical-align:middle;margin-right:4px">阿隆趋势</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cmf-divergence" checked style="vertical-align:middle;margin-right:4px">蔡金资金流背离</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="elder-ray" checked style="vertical-align:middle;margin-right:4px">Elder Ray力量</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="supertrend" checked style="vertical-align:middle;margin-right:4px">超级趋势</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="hull-ma-crossover" checked style="vertical-align:middle;margin-right:4px">Hull MA交叉</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="dema-crossover" checked style="vertical-align:middle;margin-right:4px">DEMA交叉</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="triple-ma-crossover" checked style="vertical-align:middle;margin-right:4px">三重均线交叉</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="ma-envelope" checked style="vertical-align:middle;margin-right:4px">MA包络</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="connors-rsi" checked style="vertical-align:middle;margin-right:4px">Connors RSI</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="kama-trend" checked style="vertical-align:middle;margin-right:4px">KAMA自适应趋势</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="demarker" checked style="vertical-align:middle;margin-right:4px">DeMARK指标</label>
      </div>
      <div class="form-row" style="flex-wrap:wrap;gap:4px 8px">
        <label style="color:var(--accent);font-weight:600;font-size:11px;min-width:auto">CTA经典</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-trend" checked style="vertical-align:middle;margin-right:4px">CTA趋势跟踪</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="dual-thrust" checked style="vertical-align:middle;margin-right:4px">Dual Thrust双推力</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="turtle-breakout" checked style="vertical-align:middle;margin-right:4px">海龟突破</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="macd-trend" checked style="vertical-align:middle;margin-right:4px">MACD趋势</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="bollinger-breakout" checked style="vertical-align:middle;margin-right:4px">布林带突破</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="keltner-channel" checked style="vertical-align:middle;margin-right:4px">肯特纳通道</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="r-breaker" checked style="vertical-align:middle;margin-right:4px">R-Breaker反转</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="aberration" checked style="vertical-align:middle;margin-right:4px">标准差偏离</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="ttm-squeeze" checked style="vertical-align:middle;margin-right:4px">TTM挤压</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="dmi-adx" checked style="vertical-align:middle;margin-right:4px">DMI+ADX</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="parabolic-sar" checked style="vertical-align:middle;margin-right:4px">抛物线SAR</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="volatility-target" checked style="vertical-align:middle;margin-right:4px">波动率目标</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="momentum-rotation" checked style="vertical-align:middle;margin-right:4px">动量轮动</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="obv-trend" checked style="vertical-align:middle;margin-right:4px">OBV趋势</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="ichimoku" checked style="vertical-align:middle;margin-right:4px">一目均衡表</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="grid-trading" checked style="vertical-align:middle;margin-right:4px">网格交易</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="multi-ma-convergence" checked style="vertical-align:middle;margin-right:4px">多均线收敛</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="donchian-mid-reversion" checked style="vertical-align:middle;margin-right:4px">唐奇安中线回归</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="pivot-point" checked style="vertical-align:middle;margin-right:4px">枢轴点</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="chandelier-exit" checked style="vertical-align:middle;margin-right:4px">烛台止损</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="roc-momentum" checked style="vertical-align:middle;margin-right:4px">ROC动量</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cci-divergence" checked style="vertical-align:middle;margin-right:4px">CCI背离</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="ema-crossover" checked style="vertical-align:middle;margin-right:4px">EMA均线交叉</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="atr-trailing" checked style="vertical-align:middle;margin-right:4px">ATR追踪止损</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="stochastic-rsi" checked style="vertical-align:middle;margin-right:4px">随机RSI</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="macd-histogram-reversal" checked style="vertical-align:middle;margin-right:4px">MACD柱状反转</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="bollinger-bandwidth" checked style="vertical-align:middle;margin-right:4px">布林带带宽</label>
      </div>
      <div class="form-row" style="flex-wrap:wrap;gap:4px 8px">
        <label style="color:var(--accent);font-weight:600;font-size:11px;min-width:auto">CTA扩展</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-awesome-oscillator" checked style="vertical-align:middle;margin-right:4px">AO振荡器</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-elder-force" checked style="vertical-align:middle;margin-right:4px">Elder力量指数</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-demarker" checked style="vertical-align:middle;margin-right:4px">CTA DeMARK</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-ultimate-oscillator" checked style="vertical-align:middle;margin-right:4px">终极振荡器</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-adx-breakout" checked style="vertical-align:middle;margin-right:4px">ADX突破</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-trix-trend" checked style="vertical-align:middle;margin-right:4px">TRIX趋势</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-hull-ma-trend" checked style="vertical-align:middle;margin-right:4px">Hull MA趋势</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-mass-index" checked style="vertical-align:middle;margin-right:4px">质量指数</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-chaikin-oscillator" checked style="vertical-align:middle;margin-right:4px">蔡金振荡器</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-coppock" checked style="vertical-align:middle;margin-right:4px">Coppock曲线</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-rsi-divergence" checked style="vertical-align:middle;margin-right:4px">RSI背离</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-volume-breakout" checked style="vertical-align:middle;margin-right:4px">成交量突破</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-adaptive-ma" checked style="vertical-align:middle;margin-right:4px">自适应均线</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-ma-envelope" checked style="vertical-align:middle;margin-right:4px">CTA MA包络</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-connors-rsi" checked style="vertical-align:middle;margin-right:4px">CTA Connors RSI</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-aroon-oscillator" checked style="vertical-align:middle;margin-right:4px">阿隆振荡器</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-mfi-trend" checked style="vertical-align:middle;margin-right:4px">MFI趋势</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-dema-crossover" checked style="vertical-align:middle;margin-right:4px">CTA DEMA交叉</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-triple-ma" checked style="vertical-align:middle;margin-right:4px">CTA三重均线</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cta-supertrend" checked style="vertical-align:middle;margin-right:4px">CTA超级趋势</label>
      </div>
      <div class="form-row" style="flex-wrap:wrap;gap:4px 8px">
        <label style="color:var(--accent);font-weight:600;font-size:11px;min-width:auto;opacity:0.5">T0日内 (A股暂不支持)</label>
        <label style="min-width:auto;opacity:0.5"><input type="checkbox" class="dp-strategy" value="t0-day-trading" disabled style="vertical-align:middle;margin-right:4px">T0日内交易</label>
        <label style="min-width:auto;opacity:0.5"><input type="checkbox" class="dp-strategy" value="t0-enhanced" disabled style="vertical-align:middle;margin-right:4px">T0增强</label>
      </div>
      <div class="form-row" style="flex-wrap:wrap;gap:4px 8px">
        <label style="color:var(--accent);font-weight:600;font-size:11px;min-width:auto">Python趋势</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="ema-cross" checked style="vertical-align:middle;margin-right:4px">EMA均线交叉</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="adx-trend" checked style="vertical-align:middle;margin-right:4px">ADX趋势强度</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="aroon" checked style="vertical-align:middle;margin-right:4px">阿隆指标</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="vortex" checked style="vertical-align:middle;margin-right:4px">漩涡指标</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cci" checked style="vertical-align:middle;margin-right:4px">CCI顺势</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="trix" checked style="vertical-align:middle;margin-right:4px">TRIX平滑</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="dmi" checked style="vertical-align:middle;margin-right:4px">趋向指标DMI</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="dema-cross" checked style="vertical-align:middle;margin-right:4px">DEMA交叉</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="tema-cross" checked style="vertical-align:middle;margin-right:4px">TEMA交叉</label>
      </div>
      <div class="form-row" style="flex-wrap:wrap;gap:4px 8px">
        <label style="color:var(--accent);font-weight:600;font-size:11px;min-width:auto">Python通道</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="donchian-breakout" checked style="vertical-align:middle;margin-right:4px">唐奇安突破</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="atr-channel" checked style="vertical-align:middle;margin-right:4px">ATR通道</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="envelope" checked style="vertical-align:middle;margin-right:4px">包络线</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="price-channel-breakout" checked style="vertical-align:middle;margin-right:4px">价格通道突破</label>
      </div>
      <div class="form-row" style="flex-wrap:wrap;gap:4px 8px">
        <label style="color:var(--accent);font-weight:600;font-size:11px;min-width:auto">Python指标</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="stochastic" checked style="vertical-align:middle;margin-right:4px">随机振荡器</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="mfi" checked style="vertical-align:middle;margin-right:4px">MFI资金流</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="cmf" checked style="vertical-align:middle;margin-right:4px">蔡金资金流</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="adl" checked style="vertical-align:middle;margin-right:4px">聚散指标ADL</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="roc" checked style="vertical-align:middle;margin-right:4px">变化率ROC</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="vwap" checked style="vertical-align:middle;margin-right:4px">VWAP均价</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="adaptive-ma" checked style="vertical-align:middle;margin-right:4px">自适应均线</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="obv" checked style="vertical-align:middle;margin-right:4px">能量潮OBV</label>
      </div>
      <div class="form-row" style="flex-wrap:wrap;gap:4px 8px">
        <label style="color:var(--accent);font-weight:600;font-size:11px;min-width:auto">Python形态</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="engulfing" checked style="vertical-align:middle;margin-right:4px">吞没形态</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="hammer" checked style="vertical-align:middle;margin-right:4px">锤子线</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="doji" checked style="vertical-align:middle;margin-right:4px">十字星</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="morning-star" checked style="vertical-align:middle;margin-right:4px">启明星</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="three-soldiers" checked style="vertical-align:middle;margin-right:4px">三兵三鸦</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="harami" checked style="vertical-align:middle;margin-right:4px">孕线形态</label>
      </div>
      <div class="form-row" style="flex-wrap:wrap;gap:4px 8px">
        <label style="color:var(--accent);font-weight:600;font-size:11px;min-width:auto">Python组合</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="macd-rsi-combo" checked style="vertical-align:middle;margin-right:4px">MACD+RSI组合</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="bollinger-rsi-combo" checked style="vertical-align:middle;margin-right:4px">布林+RSI组合</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="ma-volume-combo" checked style="vertical-align:middle;margin-right:4px">均线+量能组合</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="kdj-macd-combo" checked style="vertical-align:middle;margin-right:4px">KDJ+MACD组合</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="ichimoku-adx-combo" checked style="vertical-align:middle;margin-right:4px">一目+ADX组合</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="multi-tf-ma" checked style="vertical-align:middle;margin-right:4px">多周期均线</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="volatility-breakout" checked style="vertical-align:middle;margin-right:4px">波动率突破</label>
        <label style="min-width:auto"><input type="checkbox" class="dp-strategy" value="regime-switch" checked style="vertical-align:middle;margin-right:4px">趋势/震荡切换</label>
        <span id="dp-composed-strategies"></span>
      </div>
      <div id="dp-aipha-strategies-wrap" class="form-row" style="flex-wrap:wrap;gap:4px 8px;display:none">
        <label style="color:var(--purple);font-weight:600;font-size:11px;min-width:auto">AIPHA多因子</label>
        <button class="btn btn-sm" type="button" onclick="toggleAiphaStrategies()" style="padding:2px 6px;font-size:10px;border-color:var(--purple);color:var(--purple)" id="dp-aipha-toggle">展开</button>
        <span id="dp-aipha-count" style="font-size:11px;color:var(--text-dim)"></span>
        <div id="dp-aipha-strategies" style="display:none;width:100%;flex-wrap:wrap;gap:4px 8px;margin-top:4px"></div>
      </div>
      <!-- Fitness Ranking Panel (hidden by default) -->
      <div id="dp-fitness-panel" style="display:none;margin:8px 0;padding:12px;background:var(--bg);border:1px solid var(--purple);border-radius:8px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span style="font-size:14px;font-weight:600;color:var(--purple)">🏆 全局策略排名</span>
          <span style="flex:1"></span>
          <button class="btn btn-sm" onclick="applyFitnessSelection()" style="border-color:var(--purple);color:var(--purple)">✅ 应用勾选</button>
          <button class="btn btn-sm" onclick="document.getElementById('dp-fitness-panel').style.display='none'">✖ 关闭</button>
        </div>
        <div id="dp-fitness-ranking-body" style="font-size:13px"><span style="color:var(--text-dim)">加载中...</span></div>
      </div>
      <div class="form-row">
        <label>每策略前N</label>
        <input type="number" id="dp-top-n" value="8" min="5" max="20" style="width:60px">
        <label>入围策略</label>
        <input type="number" id="dp-top-strategy-count" value="3" min="2" max="5" style="width:60px">
        <label>最终股票</label>
        <input type="number" id="dp-final-limit" value="8" min="3" max="8" style="width:60px">
        <span style="font-size:11px;color:var(--text-dim)">回测资金/周期/风险将复用下方「建仓推荐参数」</span>
      </div>
      <div class="btn-group" style="flex-wrap:wrap;gap:6px">
        <button class="btn btn-primary" id="btn-dp-run" onclick="runDailyCompositePick()">🚀 运行每日综合选股</button>
        <button class="btn" onclick="applyDailyPickToRecommend()">📥 应用到建仓推荐</button>
      </div>
      <div id="dp-status" style="font-size:12px;color:var(--text-dim);margin-top:8px"></div>
    </div>

    <!-- Auto-Trade Scheduler Panel -->
    <div class="card" style="margin-bottom:12px;border:1px solid rgba(251,191,36,.35)">
      <h3 style="cursor:pointer;user-select:none" onclick="toggleAutoTradePanel()">
        🤖 自动交易定时任务
        <span id="at-toggle" style="float:right;font-size:12px;color:var(--text-dim)">▶ 展开</span>
      </h3>
      <div id="at-panel" style="display:none;margin-top:12px">
        <div id="at-status-bar" style="font-size:12px;color:var(--text-dim);margin-bottom:10px">加载中…</div>
        <div class="form-row" style="align-items:center;gap:10px;flex-wrap:wrap">
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-weight:600">
            <input type="checkbox" id="at-enabled"> 启用自动交易
          </label>
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--orange)">
            <input type="checkbox" id="at-dry-run" checked> 试运行模式（不实际下单）
          </label>
        </div>
        <div class="form-row" style="align-items:center;gap:10px;flex-wrap:wrap;margin-top:8px">
          <label>筛选时间 (cron)</label>
          <input type="text" id="at-screen-cron" value="30 7 * * 1-5" style="width:160px" placeholder="30 7 * * 1-5">
          <label>下单时间 (cron)</label>
          <input type="text" id="at-execute-cron" value="31 9 * * 1-5" style="width:160px" placeholder="31 9 * * 1-5">
          <span style="font-size:11px;color:var(--text-dim)">格式: 分 时 日 月 周(1-5=周一至周五) | A股开盘9:30，9:30前自动走预埋单</span>
        </div>
        <div class="form-row" style="align-items:center;gap:10px;flex-wrap:wrap;margin-top:8px">
          <label>执行比例</label>
          <input type="number" id="at-exec-pct" min="10" max="100" value="60" style="width:70px">%
          <label>跟踪止损</label>
          <input type="number" id="at-trailing" min="2" max="20" value="8" style="width:60px">%
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
            <input type="checkbox" id="at-cond-orders" checked> 创建条件单
          </label>
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
            <input type="checkbox" id="at-force-pre"> 盘前预埋
          </label>
        </div>
        <div class="form-row" style="align-items:center;gap:10px;flex-wrap:wrap;margin-top:8px">
          <label>投资资金</label>
          <input type="number" id="at-capital" value="500000" style="width:130px">
          <label>风险</label>
          <select id="at-risk" style="width:80px">
            <option value="low">稳健</option>
            <option value="medium">均衡</option>
            <option value="high" selected>进取</option>
            <option value="aggressive">激进</option>
          </select>
          <label>推荐策略</label>
          <select id="at-rec-strategy" style="width:100px">
            <option value="mixed" selected>混合策略</option>
            <option value="momentum">趋势动量</option>
            <option value="value">价值投资</option>
            <option value="growth">成长投资</option>
            <option value="swing">波段交易</option>
          </select>
        </div>
        <div class="form-row" style="align-items:center;gap:10px;flex-wrap:wrap;margin-top:8px">
          <label>每策略前N</label>
          <input type="number" id="at-top-n" value="8" min="5" max="20" style="width:55px">
          <label>入围策略</label>
          <input type="number" id="at-top-strat" value="3" min="2" max="5" style="width:55px">
          <label>最终股票</label>
          <input type="number" id="at-final-limit" value="8" min="3" max="8" style="width:55px">
          <label>调仓周期</label>
          <input type="number" id="at-rebalance" value="5" min="1" max="30" style="width:55px">天
        </div>
        <div class="form-row" style="align-items:center;gap:10px;flex-wrap:wrap;margin-top:8px">
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
            <input type="checkbox" id="at-use-fitness" checked> 使用适配度优选策略
          </label>
          <label>优选策略数</label>
          <input type="number" id="at-fitness-top-n" value="6" min="3" max="15" style="width:55px">
          <span style="font-size:11px;color:var(--text-dim)">勾选后仅用策略适配度分析筛出的Top策略，跳过全量策略遍历（大幅提速）</span>
        </div>
        <div class="btn-group" style="margin-top:10px;flex-wrap:wrap;gap:6px">
          <button class="btn btn-primary" id="btn-at-save" onclick="saveAutoTradeConfig()">💾 保存配置</button>
          <button class="btn" id="btn-at-screen" onclick="triggerAutoTradeScreen()">🔍 立即筛选</button>
          <button class="btn" id="btn-at-exec" onclick="triggerAutoTradeExec()">🚀 立即执行</button>
          <button class="btn" id="btn-at-refresh" onclick="loadAutoTradeStatus()">🔄 刷新状态</button>
        </div>
        <div id="at-result" style="margin-top:10px;font-size:12px"></div>
      </div>
    </div>

    <div class="card" style="margin-bottom:12px">
      <h3 style="display:flex;align-items:center;gap:8px">🕘 每日综合选股历史 <span style="flex:1"></span><button class="btn btn-sm" onclick="loadDailyPickHistory()">🔄 刷新</button></h3>
      <div id="dp-history-status" style="font-size:12px;color:var(--text-dim);margin-bottom:8px">暂无历史</div>
      <div id="dp-history-panel" style="display:none">
        <div style="overflow-x:auto"><table id="dp-history-table" style="font-size:12px">
          <thead><tr><th>#</th><th>时间</th><th>策略池</th><th>最佳策略</th><th>综合分</th><th>入围数</th><th>最终股票</th><th>操作</th></tr></thead>
          <tbody></tbody>
        </table></div>
      </div>
    </div>

    <div id="dp-results" style="display:none;margin-bottom:14px">
      <div id="dp-regime-result-warn" style="display:none"></div>
      <div class="kpi-row" id="dp-kpis"></div>

      <div class="card" style="margin-bottom:12px">
        <h3>🏆 策略回测排行 <span style="font-size:11px;font-weight:normal;color:var(--text-dim);margin-left:8px">💡 点击策略行查看详细计算过程</span></h3>
        <div style="overflow-x:auto"><table id="dp-strategy-table">
          <thead><tr><th>排名</th><th>策略</th><th>候选数</th><th>收益%</th><th>回撤%</th><th>胜率%</th><th>夏普</th><th>利润因子</th><th>综合分</th><th>状态</th></tr></thead>
          <tbody></tbody>
        </table></div>
      </div>

      <div class="card" style="margin-bottom:12px">
        <h3>📋 综合候选列表（含策略来源）<span style="font-size:11px;font-weight:normal;color:var(--text-dim);margin-left:8px">💡 点击行查看该股票详细评分过程</span></h3>
        <div style="overflow-x:auto"><table id="dp-combined-table">
          <thead><tr><th>排名</th><th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th><th>综合分</th><th>收益%</th><th>夏普</th><th>回撤%</th><th>胜率%</th><th>入选次数</th><th>来源策略</th><th>自适应路由</th><th style="min-width:260px">来源明细</th><th>状态</th></tr></thead>
          <tbody></tbody>
        </table></div>
      </div>

      <div class="card" style="margin-bottom:12px">
        <h3>🧪 入围股票单股回测排行</h3>
        <div style="overflow-x:auto"><table id="dp-stock-bt-table">
          <thead><tr><th>排名</th><th>代码</th><th>名称</th><th>回测策略</th><th>来源策略</th><th>收益%</th><th>回撤%</th><th>胜率%</th><th>夏普</th><th>综合分</th><th>状态</th></tr></thead>
          <tbody></tbody>
        </table></div>
      </div>

      <div class="card">
        <h3>📈 入围股票池策略排行</h3>
        <div style="overflow-x:auto"><table id="dp-pool-bt-table">
          <thead><tr><th>排名</th><th>策略</th><th>收益%</th><th>回撤%</th><th>胜率%</th><th>夏普</th><th>利润因子</th><th>综合分</th><th>状态</th></tr></thead>
          <tbody></tbody>
        </table></div>
      </div>
    </div>

  <div class="cards">
    <div class="card">
      <h3>🎯 建仓推荐参数</h3>
      <div class="form-row">
        <label>投资资金</label>
        <input type="number" id="rec-capital" value="500000" style="width:140px">
        <label>目标资金</label>
        <input type="number" id="rec-target-capital" value="600000" style="width:140px">
        <label>策略</label>
        <select id="rec-strategy">
          <option value="mixed" selected>混合策略</option>
          <option value="momentum">趋势动量</option>
          <option value="value">价值投资</option>
          <option value="growth">成长投资</option>
          <option value="swing">波段交易</option>
          <option value="manipulation">跟庄策略</option>
        </select>
      </div>
      <div class="form-row">
        <label>风险偏好</label>
        <select id="rec-risk">
          <option value="low">稳健型</option>
          <option value="medium">均衡型</option>
          <option value="high" selected>进取型</option>
          <option value="aggressive">激进型</option>
        </select>
        <label>调仓周期</label>
        <input type="number" id="rec-rebalance" value="5" min="1" max="30" style="width:60px"> 天
        <label style="margin-left:16px;cursor:pointer"><input type="checkbox" id="rec-exclude-star" checked> 排除科创板(688)</label>
      </div>
      <div class="form-row">
        <label>回测起始</label>
        <input type="text" id="rec-start" value="2025-06-01" style="width:120px">
        <label>回测结束</label>
        <input type="text" id="rec-end" value="" placeholder="留空=今天" style="width:120px">
      </div>
      <div class="form-row">
        <label>股票池</label>
        <input type="text" id="rec-symbols" value="" style="width:100%;max-width:600px" placeholder="代码逗号分隔，可点击下方按钮导入">
      </div>
      <div class="btn-group" style="flex-wrap:wrap;gap:6px">
        <button class="btn btn-primary" id="btn-rec-run" onclick="runRecommend()">🎯 生成建仓推荐</button>
        <button class="btn" onclick="importFromScreener()" title="从 /选股 命令结果导入">📥 从选股导入</button>
        <button class="btn" onclick="planStocksToRecommend()" title="从投资计划导入选中的股票">📋 从计划导入</button>
      </div>
      <div id="rec-status" style="font-size:12px;color:var(--text-dim);margin-top:8px"></div>
    </div>
  </div>

  <div id="rec-pool-stale-banner" style="display:none;margin-bottom:10px;padding:8px 12px;background:rgba(210,153,34,.15);border:1px solid rgba(210,153,34,.5);border-radius:6px;font-size:12px;color:#d29922">
    ⚠️ 股票池已修改，当前结果是基于旧股票池生成的。请点击 <strong>生成建仓推荐</strong> 重新分析。
  </div>

  <div id="rec-results" style="display:none">
    <!-- Backtest basis -->
    <div class="kpi-row" id="rec-bt-kpis"></div>

    <!-- Allocation summary -->
    <div class="kpi-row" id="rec-alloc-kpis"></div>

    <!-- Execute recommendation -->
    <div class="card" style="margin-bottom:14px;border:1px solid rgba(13,148,136,.35)">
      <h3>⚡ 推荐执行（单账号）</h3>
      <div class="form-row" style="align-items:center;gap:10px;flex-wrap:wrap">
        <label>执行比例</label>
        <input type="number" id="rec-exec-pct" min="10" max="100" value="60" style="width:80px">%
        <label>跟踪止损</label>
        <input type="number" id="rec-exec-trailing" min="2" max="20" value="8" style="width:70px">%
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" id="rec-exec-conds" checked>
          自动创建止损/止盈条件单
        </label>
      </div>
      <div class="btn-group" style="margin-top:8px;flex-wrap:wrap;gap:6px">
        <button class="btn" id="btn-rec-dry-run" onclick="executeRecommend(true)">🧪 试运行（不下单）</button>
        <button class="btn btn-primary" id="btn-rec-exec" onclick="executeRecommend(false)">🚀 执行建仓（真实下单）</button>
        <button class="btn" id="btn-rec-premarket" onclick="executeRecommend(false,true)" title="跳过交易时段检查，直接提交限价委托到QMT（盘前预埋单）">📋 预埋委托（盘前下单）</button>
        <button class="btn" id="btn-rec-schedule" onclick="scheduleStrategy()" title="生成 MiniQMT 预埋策略文件，开盘时自动下单">📄 生成QMT策略文件</button>
      </div>
      <div id="rec-exec-mode-hint" style="font-size:12px;margin-top:6px;padding:4px 8px;border-radius:4px;display:none"></div>
      <div id="rec-exec-hint" style="font-size:12px;color:var(--text-dim);margin-top:8px">执行会按分批入场第1笔价格提交限价买单；预埋委托可在非交易时段提交，开盘后按限价撮合。</div>
      <div id="rec-exec-status" style="font-size:12px;color:var(--text-dim);margin-top:4px"></div>
      <div id="rec-exec-result" style="margin-top:8px"></div>
    </div>

    <!-- Fill confirmation panel (hidden until real orders submitted) -->
    <div id="rec-fill-confirm" class="card" style="display:none;margin-bottom:14px;border:1px solid rgba(251,191,36,.35)">
      <h3>⏳ 成交确认</h3>
      <div id="fill-confirm-status" style="font-size:12px;color:var(--text-dim);margin-bottom:8px">正在等待订单成交…</div>
      <div id="fill-confirm-table-wrap" style="overflow-x:auto"><table id="fill-confirm-table" style="font-size:12px">
        <thead><tr><th>代码</th><th>名称</th><th>委托数量</th><th>已成交</th><th>均价</th><th>持仓确认</th><th>状态</th></tr></thead>
        <tbody></tbody>
      </table></div>
      <div class="btn-group" style="margin-top:10px;gap:8px">
        <button class="btn btn-primary" id="btn-fill-monitor" disabled onclick="startMonitoringAfterFill()">🚀 进入实盘监控</button>
        <button class="btn" id="btn-fill-next-batch" style="display:none" onclick="executeNextBatch()">📦 执行下一批次</button>
        <button class="btn" id="btn-fill-skip" onclick="skipFillConfirm()">⏭️ 跳过确认，直接监控</button>
        <button class="btn" id="btn-fill-refresh" onclick="pollFillStatusOnce()">🔄 手动刷新</button>
      </div>
      <div id="fill-batch-info" style="font-size:12px;color:var(--accent);margin-top:6px;display:none"></div>
      <div id="fill-confirm-hint" style="font-size:11px;color:var(--text-dim);margin-top:6px">自动每 5 秒轮询订单状态，全部成交或有持仓后可进入实盘监控。</div>
    </div>

    <!-- Position table -->
    <div class="card" style="margin-bottom:14px">
      <h3>🎯 建仓推荐明细 <span id="rec-sel-hint" style="font-size:12px;font-weight:normal;color:var(--text-dim);margin-left:8px"></span></h3>
      <div style="overflow-x:auto">
        <table id="rec-table">
          <thead><tr><th style="width:32px"><input type="checkbox" id="rec-select-all" checked onchange="toggleRecSelectAll(this.checked)" title="全选/取消全选"></th><th>#</th><th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th><th>建议股数</th><th>建议金额</th><th>权重%</th><th>分批入场</th><th>止盈/止损</th><th>持有天数</th><th>预期收益</th><th>目标贡献</th><th>评分</th><th>类别</th><th style="min-width:280px">推荐理由</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <!-- Categories -->
    <div class="cards" id="rec-categories"></div>

    <!-- Tips -->
    <div class="card" style="margin-top:14px">
      <h3>📝 操作建议</h3>
      <ul class="rules-list" id="rec-tips"></ul>
    </div>

    <!-- All stocks in pool -->
    <div class="card" style="margin-top:14px">
      <h3>📊 股池实时行情 (<span id="rec-pool-count">0</span> 只)</h3>
      <div style="overflow-x:auto">
        <table id="rec-pool-table">
          <thead><tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th><th>PE</th><th>PB</th><th>市值(亿)</th><th>换手率%</th><th>评分</th><th>技术面</th><th>回测持仓</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ── Engine Status + Conditional Orders ── -->
  <div class="card" style="margin-top:14px;border:1px solid rgba(13,148,136,.25)">
    <h3 style="cursor:pointer;user-select:none" onclick="toggleCondPanel()">
      🛡️ 实盘引擎 & 条件单管理
      <span id="cond-toggle" style="float:right;font-size:12px;color:var(--text-dim)">▶ 展开</span>
    </h3>
    <div id="cond-panel" style="display:none;margin-top:12px">
      <!-- Engine status KPIs -->
      <div class="kpi-row" id="engine-status-kpis"></div>
      <!-- Positions detail (toggled by clicking 持仓数) -->
      <div id="engine-positions-detail" style="display:none;margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <h4 style="font-size:13px;margin:0">📦 持仓明细</h4>
          <button class="btn btn-sm" onclick="document.getElementById('engine-positions-detail').style.display='none'">✕ 收起</button>
        </div>
        <div style="overflow-x:auto">
          <table id="engine-positions-table">
            <thead><tr><th>代码</th><th>名称</th><th>持仓</th><th>可用</th><th>冻结</th><th>成本</th><th>现价</th><th>市值</th><th>盈亏</th><th>盈亏%</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <!-- Pending orders detail (toggled by clicking 挂单数) -->
      <div id="engine-pending-detail" style="display:none;margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <h4 style="font-size:13px;margin:0">📋 挂单明细</h4>
          <button class="btn btn-sm" onclick="document.getElementById('engine-pending-detail').style.display='none'">✕ 收起</button>
        </div>
        <div style="overflow-x:auto">
          <table id="engine-pending-table">
            <thead><tr><th>订单号</th><th>代码</th><th>名称</th><th>方向</th><th>类型</th><th>价格</th><th>数量</th><th>已成</th><th>状态</th><th>券商</th><th>策略</th><th>时间</th><th>操作</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>

      <!-- Conditional order actions -->
      <div class="btn-group" style="margin:8px 0;gap:6px;flex-wrap:wrap">
        <button class="btn btn-primary" onclick="loadConditionalOrders()">🔄 刷新条件单</button>
        <button class="btn" onclick="checkConditionalNow()">⚡ 立即检查触发</button>
        <button class="btn" onclick="toggleCondHistory()">📜 触发历史</button>
        <button class="btn btn-danger" onclick="showClearPositionsModal()" style="margin-left:auto">🗑️ 一键清仓</button>
      </div>

      <!-- Clear Positions Modal -->
      <div id="clear-pos-modal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:1000;overflow-y:auto">
        <div style="max-width:600px;margin:60px auto;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:24px;position:relative">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
            <h3 style="margin:0">🗑️ 一键清仓 — 选择卖出持仓</h3>
            <button class="btn btn-sm" onclick="closeClearPosModal()">✕ 关闭</button>
          </div>
          <p style="font-size:12px;color:var(--text-dim);margin-bottom:12px">勾选要卖出的持仓，取消勾选可保留该股票。将以市价卖出可用数量。</p>
          <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
            <label style="font-size:12px;cursor:pointer"><input type="checkbox" id="clear-pos-select-all" checked onchange="toggleClearPosAll(this.checked)"> 全选/取消</label>
            <span id="clear-pos-hint" style="font-size:12px;color:var(--text-dim)"></span>
          </div>
          <div style="overflow-x:auto;max-height:400px;overflow-y:auto">
            <table id="clear-pos-table" style="font-size:12px">
              <thead><tr><th style="width:30px"></th><th>代码</th><th>名称</th><th>可卖</th><th>成本</th><th>现价</th><th>盈亏%</th><th>市值</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
          <div style="margin-top:16px;display:flex;gap:8px;justify-content:flex-end">
            <button class="btn" onclick="closeClearPosModal()">取消</button>
            <button class="btn btn-danger" id="clear-pos-confirm-btn" onclick="executeClearPositions()">确认清仓</button>
          </div>
        </div>
      </div>
      <div id="cond-status" style="font-size:12px;color:var(--text-dim);margin-bottom:8px"></div>

      <!-- Active conditional orders -->
      <div style="overflow-x:auto">
        <table id="cond-order-table">
          <thead><tr><th>ID</th><th>代码</th><th>名称</th><th>类型</th><th>入场价</th><th>触发值</th><th>数量</th><th>策略</th><th>描述</th><th>创建时间</th><th>触发时间</th><th>状态</th><th>保护</th><th>操作</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>

      <!-- Conditional order history -->
      <div id="cond-history-panel" style="display:none;margin-top:14px;border-top:1px solid var(--border);padding-top:12px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <h4 style="font-size:13px;margin:0">📜 条件单触发历史</h4>
          <button class="btn btn-sm" onclick="document.getElementById('cond-history-panel').style.display='none'">✕ 收起</button>
        </div>
        <div id="cond-history-status" style="font-size:12px;color:var(--text-dim);margin-bottom:6px"></div>
        <div style="overflow-x:auto">
          <table id="cond-history-table">
            <thead><tr><th>ID</th><th>代码</th><th>名称</th><th>类型</th><th>入场价</th><th>触发值</th><th>数量</th><th>策略</th><th>描述</th><th>创建时间</th><th>触发时间</th><th>结果</th><th>归档时间</th><th>归档原因</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>

      <!-- Add manual conditional order -->
      <div style="margin-top:14px;border-top:1px solid var(--border);padding-top:12px">
        <h3 style="font-size:14px">➕ 手动添加条件单</h3>
        <div class="form-row" style="align-items:center;gap:8px;flex-wrap:wrap">
          <label>代码</label><input type="text" id="cond-add-symbol" style="width:100px" placeholder="600519">
          <label>类型</label>
          <select id="cond-add-type" onchange="updateCondAddFields()">
            <option value="stop_loss">止损</option>
            <option value="take_profit">止盈</option>
            <option value="trailing_stop">跟踪止损</option>
          </select>
          <label>入场价</label><input type="number" id="cond-add-entry" style="width:100px" placeholder="100.00">
          <label id="cond-add-trigger-label">止损价</label><input type="number" id="cond-add-trigger" style="width:100px" placeholder="95.00">
          <label>数量</label><input type="number" id="cond-add-qty" style="width:80px" value="100">
          <button class="btn btn-primary" onclick="addManualConditional()">添加</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Breakout Monitor (突破监控) ── -->
  <div class="card" style="margin-top:14px;border:1px solid rgba(234,179,8,.25)">
    <h3 style="cursor:pointer;user-select:none" onclick="toggleBreakoutPanel()">
      🎯 突破监控 & EOD 规则
      <span id="bo-toggle" style="float:right;font-size:12px;color:var(--text-dim)">▶ 展开</span>
    </h3>
    <div id="bo-panel" style="display:none;margin-top:12px">
      <div class="btn-group" style="margin:8px 0;gap:6px;flex-wrap:wrap">
        <button class="btn btn-primary" onclick="loadBreakoutOrders()">🔄 刷新</button>
      </div>
      <div id="bo-status" style="font-size:12px;color:var(--text-dim);margin-bottom:8px"></div>
      <div style="overflow-x:auto">
        <table id="bo-table">
          <thead><tr><th>ID</th><th>代码</th><th>触发价</th><th>缓冲%</th><th>数量</th><th>状态</th><th>描述</th><th>创建时间</th><th>操作</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
      <div id="eod-section" style="margin-top:14px;border-top:1px solid var(--border);padding-top:12px;display:none">
        <h4 style="font-size:13px;margin:0 0 8px">📉 EOD 退出规则</h4>
        <div style="overflow-x:auto">
          <table id="eod-table">
            <thead><tr><th>ID</th><th>代码</th><th>均线</th><th>类型</th><th>退出动作</th><th>描述</th><th>状态</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ Portfolio Backtest Tab ═══ -->
<div class="tab-content" id="tab-pf-backtest">
  <div class="card" style="margin-bottom:14px">
    <h3>📊 持仓策略回测</h3>
    <p style="font-size:12px;color:var(--text-dim);margin-bottom:14px">输入已持有的股票，用多种策略组合回测，对比不同策略的盈利表现</p>

    <!-- 参数设置区 -->
    <div class="pbt-form-section">
      <h4>⚙️ 回测参数</h4>
      <div class="pbt-input-group">
        <div class="pbt-field" style="grid-column:span 2">
          <label>股票代码 <span style="font-size:10px;opacity:.7">逗号分隔</span>
            <button class="btn btn-sm" onclick="pbtLoadHoldings()" style="margin-left:8px;font-size:10px;padding:1px 8px">📂 从持仓导入</button>
          </label>
          <input type="text" id="pbt-symbols" placeholder="600519,000858,300750" title="逗号分隔多只股票代码">
          <div id="pbt-holding-chips" style="display:none;margin-top:6px;display:flex;flex-wrap:wrap;gap:4px"></div>
        </div>
        <div class="pbt-field">
          <label>起始日期</label>
          <input type="text" id="pbt-start" placeholder="YYYY-MM-DD">
        </div>
        <div class="pbt-field">
          <label>结束日期</label>
          <input type="text" id="pbt-end" placeholder="YYYY-MM-DD">
        </div>
        <div class="pbt-field">
          <label>初始资金</label>
          <input type="number" id="pbt-capital" value="100000">
        </div>
        <div class="pbt-period-btns">
          <button class="btn btn-sm" onclick="setPbtPeriod(3)">近3月</button>
          <button class="btn btn-sm" onclick="setPbtPeriod(6)">近半年</button>
          <button class="btn btn-sm active" onclick="setPbtPeriod(12)">近1年</button>
        </div>
      </div>
    </div>

    <!-- 策略选择区 -->
    <div class="pbt-form-section">
      <h4>📋 选择策略 <span style="font-size:11px;color:var(--text-dim);font-weight:normal;margin-left:4px">(点击选中/取消，至少选1个)</span>
        <span style="flex:1"></span>
        <input type="text" class="pbt-search" id="pbt-search" placeholder="🔍 搜索策略..." oninput="pbtFilterStrategies(this.value)">
      </h4>
      <div id="pbt-strategy-chips"></div>
    </div>

    <div class="pbt-actions">
      <button class="btn btn-primary" id="btn-pbt-run" onclick="runPortfolioBacktest()">🚀 开始回测</button>
      <button class="btn btn-sm" onclick="pbtSelectAll()">全选</button>
      <button class="btn btn-sm" onclick="pbtClearAll()">清除</button>
      <button class="btn btn-sm" onclick="pbtSelectRecommended()">⭐ 推荐组合</button>
      <span id="pbt-selected-count" style="font-size:11px;color:var(--text-dim)"></span>
      <span style="flex:1"></span>
      <span id="pbt-status" style="font-size:12px;color:var(--text-dim)"></span>
    </div>
    <div class="pbt-hint">
      💡 回测基于<strong>日K线数据</strong>，包含真实A股费率：佣金万3、印花税万5、过户费万0.2、滑点千1。多只股票时资金等权分配。
    </div>
  </div>

  <div id="pbt-results" style="display:none">
    <div class="kpi-row" id="pbt-kpis"></div>
    <div class="card" style="margin-bottom:14px">
      <h3>📊 策略对比排名</h3>
      <div style="font-size:11px;color:var(--text-dim);margin-bottom:8px;line-height:1.6">
        📐 <strong>综合评分公式</strong>：(年化收益×0.5 + 夏普比率×10 + 胜率×0.3 + min(盈亏比,4)×4 − 最大回撤×1.0) × 活跃度系数　　活跃度系数 = min(1, max(0.05, 交易次数/15))
      </div>
      <div style="overflow-x:auto">
        <table id="pbt-comparison-table">
          <thead><tr><th>排名</th><th>策略</th><th title="综合评分 = (年化收益×0.5 + 夏普比率×10 + 胜率×0.3 + min(盈亏比,4)×4 − 最大回撤×1.0) × 活跃度系数">综合评分 ⓘ</th><th>年化收益</th><th>最终市值</th><th>盈亏</th><th>最大回撤</th><th>夏普比率</th><th>胜率</th><th>交易次数</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
    <div class="card" id="pbt-best-card" style="display:none;margin-bottom:14px">
      <h3>🏆 最优策略推荐</h3>
      <div id="pbt-best-content"></div>
    </div>
    <div class="card" id="pbt-failed-card" style="display:none">
      <h3>⚠️ 失败的策略</h3>
      <div id="pbt-failed-content"></div>
    </div>
  </div>

  <!-- Strategy Frequency Analysis -->
  <div class="card" style="margin-bottom:14px">
    <h3 style="display:flex;align-items:center;gap:8px">
      📊 策略表现频率分析
      <button class="btn btn-sm" onclick="loadPbtFrequency()" style="font-size:11px">🔄 刷新</button>
    </h3>
    <p style="font-size:11px;color:var(--text-dim);margin-bottom:10px">统计所有历史回测中，各策略的综合评分、排名和出现频率，帮助识别持续表现优秀的策略</p>
    <div id="pbt-frequency-content"><span style="color:var(--text-dim);font-size:12px">点击刷新加载数据，或完成首次回测后自动显示</span></div>
  </div>

  <!-- History -->
  <div class="card">
    <h3 style="display:flex;align-items:center;gap:8px">
      📜 回测历史记录
      <button class="btn btn-sm" onclick="loadPbtHistory()" style="font-size:11px">🔄 刷新</button>
    </h3>
    <div id="pbt-history-content"><span style="color:var(--text-dim);font-size:12px">完成首次回测后自动显示</span></div>
  </div>
</div>

<!-- ═══ Combo Backtest Tab ═══ -->
<div class="tab-content" id="tab-combo-backtest">
  <div class="card">
    <h3>🔬 Top-N 策略组合回测</h3>
    <p style="font-size:12px;color:var(--text-dim);margin-bottom:12px">从 Fitness 分析选出的 Top-N 策略出发，进行综合组合回测。支持多种配权模式对比，验证策略组合的收益率、胜率和夏普比率。</p>
    <div id="combo-top-strategies" style="margin-bottom:12px"><span style="color:var(--text-dim)">加载中...</span></div>
    <div class="form-row">
      <label>Top N 策略</label>
      <input type="number" id="combo-top-n" value="10" min="2" max="20" style="width:60px">
      <label>配权模式</label>
      <select id="combo-alloc-mode">
        <option value="equal" selected>等权分配</option>
        <option value="risk_parity">风险平价</option>
        <option value="max_sharpe">最大夏普</option>
        <option value="min_variance">最小方差</option>
        <option value="inverse_vol">波动率倒数</option>
        <option value="target_vol">目标波动率</option>
        <option value="max_diversification">最大分散化</option>
        <option value="custom">自定义权重</option>
      </select>
    </div>
    <!-- Custom weight editor (hidden by default) -->
    <div id="combo-custom-weights-panel" style="display:none;margin:8px 0;padding:10px;background:var(--bg3);border:1px solid var(--border);border-radius:8px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <strong style="font-size:12px">\u2699\ufe0f 自定义策略权重</strong>
        <button class="btn btn-sm" onclick="resetComboCustomWeights()" style="font-size:11px">\u21ba 重置为等权</button>
      </div>
      <div id="combo-weight-sliders" style="font-size:12px"></div>
      <div style="margin-top:6px;font-size:11px;color:var(--text-dim)">总权重: <span id="combo-weight-total">0%</span></div>
    </div>
    <div class="form-row">
      <label>再平衡频率</label>
      <select id="combo-rebalance">
        <option value="monthly" selected>每月</option>
        <option value="quarterly">每季</option>
        <option value="weekly">每周</option>
      </select>
      <label>回测天数</label>
      <input type="number" id="combo-period" value="252" min="20" max="1260" style="width:80px">
      <label>初始资金</label>
      <input type="number" id="combo-capital" value="1000000" style="width:140px">
    </div>
    <div class="btn-group">
      <button class="btn btn-primary" id="btn-combo-run" onclick="runComboBacktest()">▶️ 运行组合回测</button>
      <button class="btn btn-warning" id="btn-combo-compare" onclick="runComboCompare()">⚡ 对比配权模式</button>
    </div>
    <div id="combo-status" style="font-size:12px;color:var(--text-dim);margin-top:8px"></div>
  </div>

  <!-- Combo Backtest Results -->
  <div id="combo-results" style="display:none">
    <div class="kpi-row" id="combo-kpis"></div>
    <div class="cards">
      <div class="card" style="flex:2">
        <h3>📈 组合净值曲线</h3>
        <div class="bt-chart" style="height:300px"><canvas id="combo-equity-canvas"></canvas></div>
        <div id="combo-legend" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;font-size:11px"></div>
      </div>
      <div class="card">
        <h3>📅 月度收益</h3>
        <div id="combo-monthly" style="height:120px;padding:8px 4px"></div>
      </div>
    </div>
    <div class="cards">
      <div class="card">
        <h3>🎯 策略贡献度</h3>
        <div id="combo-contributions"></div>
      </div>
      <div class="card">
        <h3>📉 回撤曲线</h3>
        <div style="height:150px;position:relative"><canvas id="combo-dd-canvas" style="width:100%;height:100%"></canvas></div>
      </div>
    </div>
    <div class="cards">
      <div class="card">
        <h3>📋 子策略详情</h3>
        <div style="overflow-x:auto"><table id="combo-sub-table">
          <thead><tr><th>策略</th><th>权重</th><th>年化收益</th><th>最大回撤</th><th>夏普</th><th>交易数</th><th>手续费</th></tr></thead>
          <tbody id="combo-sub-tbody"></tbody>
        </table></div>
      </div>
      <div class="card">
        <h3>🔗 相关性矩阵</h3>
        <div id="combo-correlation" style="overflow-x:auto;font-size:11px"></div>
      </div>
    </div>
    <div id="combo-errors"></div>
    <!-- Paper trading apply -->
    <div class="card" style="margin-top:14px;border-left:3px solid var(--green)">
      <h3>\ud83d\udcb0 应用到模拟盘</h3>
      <p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">将组合回测的最优方案按策略权重分配资金，生成模拟盘批量订单。</p>
      <div class="form-row">
        <label>执行比例</label>
        <input type="number" id="combo-paper-pct" value="100" min="10" max="100" step="10" style="width:60px">
        <span style="font-size:12px;color:var(--text-dim)">%</span>
        <label style="margin-left:12px"><input type="checkbox" id="combo-paper-cond" checked style="vertical-align:middle;margin-right:4px">创建条件单</label>
        <label style="margin-left:12px"><input type="checkbox" id="combo-paper-dry" checked style="vertical-align:middle;margin-right:4px">先试运行</label>
      </div>
      <div class="btn-group">
        <button class="btn btn-primary" id="btn-combo-paper" onclick="applyComboToPaper()">\ud83d\udcc4 生成模拟盘订单</button>
      </div>
      <div id="combo-paper-status" style="font-size:12px;color:var(--text-dim);margin-top:8px"></div>
      <div id="combo-paper-result" style="display:none;margin-top:10px"></div>
    </div>
  </div>

  <!-- Compare Results -->
  <div id="combo-compare-results" style="display:none">
    <div class="card" style="margin-bottom:14px">
      <h3>⚡ 配权模式对比</h3>
      <div style="overflow-x:auto"><table id="combo-compare-table"></table></div>
      <div id="combo-compare-recommendation"></div>
    </div>
    <div class="card">
      <h3>📈 各模式净值对比</h3>
      <div class="bt-chart" style="height:300px"><canvas id="combo-compare-canvas"></canvas></div>
      <div id="combo-compare-legend" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;font-size:11px"></div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

`;
}

/**
 * Auto-Trade Scheduler Page — 自动交易调度中心
 *
 * Provides a management UI for the AutoTradeScheduler:
 *  - Enable/disable scheduler with toggle
 *  - View current status, last screen/execute results
 *  - Manually trigger screen or execute phases
 *  - Configure all scheduler parameters
 *  - Stop (disable) the scheduler at any time
 */
import {
  THEME_CSS_VARS,
  BASE_CSS_RESET,
  HEADER_CSS,
  KPI_CSS,
  SHARED_JS_HELPERS,
  SHARED_JS_API,
} from "./_shared.js";

export function getAutoTradePageHtml(): string {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 自动交易调度</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>🤖</text></svg>">
<style>
${THEME_CSS_VARS}
${BASE_CSS_RESET}
${HEADER_CSS}
${KPI_CSS}
.container{max-width:1200px;margin:0 auto;padding:16px 20px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:20px;margin-bottom:16px}
.card h3{font-size:15px;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.kpi-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.btn{padding:8px 16px;border:1px solid var(--border);border-radius:6px;background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px;transition:background .15s}
.btn:hover{background:var(--border)}
.btn:disabled{opacity:0.5;cursor:not-allowed}
.btn-primary{background:#1e3a5f;color:#60a5fa;border-color:#2563eb}
.btn-primary:hover{background:#1e40af}
.btn-danger{background:#450a0a;color:#f87171;border-color:#dc2626}
.btn-danger:hover{background:#7f1d1d}
.btn-success{background:#052e16;color:#4ade80;border-color:#16a34a}
.btn-success:hover{background:#14532d}
.btn-warn{background:#451a03;color:#fbbf24;border-color:#d97706}
.btn-warn:hover{background:#78350f}
.toggle{display:flex;align-items:center;gap:12px;font-size:15px;font-weight:600}
.toggle-switch{position:relative;width:52px;height:28px;cursor:pointer}
.toggle-switch input{opacity:0;width:0;height:0}
.toggle-slider{position:absolute;top:0;left:0;right:0;bottom:0;background:var(--bg3);border:1px solid var(--border);border-radius:14px;transition:.3s}
.toggle-slider:before{content:'';position:absolute;width:22px;height:22px;border-radius:50%;background:var(--text-dim);left:2px;top:2px;transition:.3s}
.toggle-switch input:checked+.toggle-slider{background:#16a34a;border-color:#16a34a}
.toggle-switch input:checked+.toggle-slider:before{transform:translateX(24px);background:#fff}
.status-badge{display:inline-block;padding:3px 10px;border-radius:10px;font-size:11px;font-weight:600}
.status-on{background:#052e16;color:#4ade80;border:1px solid #16a34a}
.status-off{background:#1c1917;color:#a8a29e;border:1px solid #57534e}
.status-running{background:#172554;color:#60a5fa;border:1px solid #2563eb;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.6}}
.log-box{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px;font-family:'Cascadia Code','Fira Code',monospace;font-size:12px;color:var(--text-dim);max-height:200px;overflow-y:auto;white-space:pre-wrap;line-height:1.6}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px 20px}
.form-group{display:flex;flex-direction:column;gap:4px}
.form-group label{font-size:12px;color:var(--text-dim)}
.form-group input,.form-group select{padding:6px 10px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px}
.form-group input:focus,.form-group select:focus{outline:none;border-color:var(--accent)}
.result-card{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:12px;margin-top:8px}
.alloc-table{width:100%;font-size:12px;border-collapse:collapse;margin-top:8px}
.alloc-table th,.alloc-table td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--border)}
.alloc-table th{color:var(--text-dim);font-weight:500}
.toast{position:fixed;top:24px;right:24px;padding:10px 20px;border-radius:8px;color:#fff;font-size:13px;z-index:9999;animation:fadeIn .2s}
@keyframes fadeIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}
.step-card{background:var(--bg3);border-radius:8px;padding:12px;border-left:3px solid var(--border)}
.step-card .step-title{font-size:13px;font-weight:600;margin-bottom:4px}
.step-card .step-desc{font-size:11px;color:var(--text-dim);line-height:1.5}
a{color:var(--accent)}
</style>
</head>
<body>
<header>
  <h1>🤖 自动交易调度中心</h1>
  <div class="header-right">
    <a href="/quant/admin/ui">← 返回管理台</a>
    <span id="refresh-timer" style="color:var(--text-dim);font-size:11px"></span>
  </div>
</header>

<div class="container">
  <!-- KPI Row -->
  <div class="kpi-row" id="kpi-row">
    <div class="kpi"><div class="kpi-val" id="kpi-status">-</div><div class="kpi-label">调度状态</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-mode">-</div><div class="kpi-label">运行模式</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-pending">-</div><div class="kpi-label">待执行</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-screen">-</div><div class="kpi-label">上次筛选</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-execute">-</div><div class="kpi-label">上次执行</div></div>
  </div>

  <!-- Master Toggle + Actions -->
  <div class="card">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
      <div class="toggle">
        <label class="toggle-switch">
          <input type="checkbox" id="master-toggle" onchange="toggleEnabled(this.checked)">
          <span class="toggle-slider"></span>
        </label>
        <span id="toggle-label">加载中…</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-primary" id="btn-screen" onclick="triggerScreen()">🔍 手动筛选</button>
        <button class="btn btn-success" id="btn-execute" onclick="triggerExecute()">🚀 手动执行</button>
        <button class="btn btn-danger" id="btn-stop" onclick="emergencyStop()">⏹️ 紧急停止</button>
        <button class="btn" onclick="loadStatus()">🔄 刷新</button>
      </div>
    </div>
  </div>

  <!-- Status & Results -->
  <div class="card">
    <h3>📊 运行状态</h3>
    <div id="status-detail" class="log-box">加载中…</div>
  </div>

  <!-- Workflow Guide (always visible) -->
  <div class="card" id="guide-card">
    <h3 style="cursor:pointer" onclick="toggleSection('guide-body')">
      📖 工作流程说明 <span style="font-size:11px;color:var(--text-dim)">(点击展开/收起)</span>
    </h3>
    <div id="guide-body">
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:12px">
        <div style="background:var(--bg3);border-radius:8px;padding:12px;border-left:3px solid #60a5fa">
          <div style="font-size:13px;font-weight:600;margin-bottom:4px">① 每日筛选 <span style="font-size:11px;color:var(--text-dim)">(默认 07:30)</span></div>
          <div style="font-size:11px;color:var(--text-dim);line-height:1.5">运行多策略综合选股 → 策略回测排名 → 选出 Top 策略 → 合并候选股 → 个股回测验证 → 生成最终入围名单</div>
        </div>
        <div style="background:var(--bg3);border-radius:8px;padding:12px;border-left:3px solid #fbbf24">
          <div style="font-size:13px;font-weight:600;margin-bottom:4px">② 推荐生成</div>
          <div style="font-size:11px;color:var(--text-dim);line-height:1.5">根据最终入围股、资金量、风险偏好 → 分配仓位和股数 → 生成推荐方案（待执行列表）</div>
        </div>
        <div style="background:var(--bg3);border-radius:8px;padding:12px;border-left:3px solid #4ade80">
          <div style="font-size:13px;font-weight:600;margin-bottom:4px">③ 执行下单 <span style="font-size:11px;color:var(--text-dim)">(默认 09:31)</span></div>
          <div style="font-size:11px;color:var(--text-dim);line-height:1.5">市场环境二次校验 → 过滤高风险标的 → 提交限价委托 → 自动创建止损止盈条件单</div>
        </div>
      </div>
      <div style="font-size:11px;color:var(--text-dim);line-height:1.6">
        <b>快速开始：</b>点击「🔍 手动筛选」运行一次完整流程，筛选完成后下方会显示策略排名、选股明细等详情。<br>
        <b>自动模式：</b>打开总开关 → 系统按 cron 时间自动执行筛选和下单。试运行模式下不会真实下单。<br>
        <b>策略来源：</b>勾选「使用适配度优选策略」会自动从 <a href="/quant/admin/strategy-fitness" target="_blank" style="color:var(--accent)">策略适配度分析</a> 中选取表现最好的策略。
      </div>
    </div>
  </div>

  <!-- Fitness Strategies (live fetch) -->
  <div class="card" id="fitness-card">
    <h3 style="cursor:pointer" onclick="toggleSection('fitness-body')">
      🏅 当前优选策略 <span id="fitness-count" style="font-size:12px;color:var(--text-dim);font-weight:normal"></span>
      <span style="font-size:11px;color:var(--text-dim)">(点击展开/收起)</span>
    </h3>
    <div id="fitness-body"><div style="color:var(--text-dim);font-size:12px">加载中…</div></div>
  </div>

  <!-- Pick History (live fetch) -->
  <div class="card" id="history-card">
    <h3 style="cursor:pointer" onclick="toggleSection('history-body')">
      📅 历史筛选记录 <span id="history-count" style="font-size:12px;color:var(--text-dim);font-weight:normal"></span>
      <span style="font-size:11px;color:var(--text-dim)">(点击展开/收起)</span>
    </h3>
    <div id="history-body"><div style="color:var(--text-dim);font-size:12px">加载中…</div></div>
  </div>

  <!-- Pending Allocations -->
  <div class="card" id="alloc-card" style="display:none">
    <h3>📋 待执行推荐 <span id="alloc-count" style="font-size:12px;color:var(--text-dim);font-weight:normal"></span></h3>
    <div id="alloc-body"></div>
    <div id="alloc-actions" style="margin-top:16px;padding:16px;background:var(--bg3);border-radius:8px;display:none">
      <p style="font-size:13px;margin-bottom:10px">👇 <b>下一步操作：</b></p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <button class="btn btn-success" onclick="triggerExecute()">🚀 执行下单 — 提交推荐方案</button>
        <button class="btn btn-primary" onclick="applyBatchMonitor()">📡 创建条件单 — 设置止损止盈买卖提醒</button>
        <button class="btn" onclick="viewInOptimization()">📊 查看优化详情</button>
      </div>
      <p style="font-size:11px;color:var(--text-dim)">• "执行下单" 会按推荐的股数和价格提交订单（试运行模式不会真实下单）<br>• "创建条件单" 跳转到组合优化页面，可批量创建止损/止盈/买卖区间监控<br>• 0 股的标的表示资金不足以买入 1 手（100股），可增加初始资金或减少选股数量</p>
    </div>
  </div>

  <!-- Market Context -->
  <div class="card" id="market-card" style="display:none">
    <h3>🌍 市场环境</h3>
    <div id="market-body"></div>
  </div>

  <!-- Strategy Ranking -->
  <div class="card" id="strategy-card" style="display:none">
    <h3 style="cursor:pointer" onclick="toggleSection('strategy-body')">
      🏆 策略排名 <span id="strategy-count" style="font-size:12px;color:var(--text-dim);font-weight:normal"></span>
      <span style="font-size:11px;color:var(--text-dim)">(点击展开/收起)</span>
    </h3>
    <div id="strategy-body"></div>
  </div>

  <!-- Combined Stock List -->
  <div class="card" id="combined-card" style="display:none">
    <h3 style="cursor:pointer" onclick="toggleSection('combined-body')">
      📊 综合选股明细 <span id="combined-count" style="font-size:12px;color:var(--text-dim);font-weight:normal"></span>
      <span style="font-size:11px;color:var(--text-dim)">(点击展开/收起)</span>
    </h3>
    <div id="combined-body"></div>
  </div>

  <!-- Routing Enrichment -->
  <div class="card" id="routing-card" style="display:none">
    <h3 style="cursor:pointer" onclick="toggleSection('routing-body')">
      🔀 策略路由分配 <span id="routing-count" style="font-size:12px;color:var(--text-dim);font-weight:normal"></span>
      <span style="font-size:11px;color:var(--text-dim)">(点击展开/收起)</span>
    </h3>
    <div id="routing-body"></div>
  </div>

  <!-- Stock Backtest Ranking -->
  <div class="card" id="stock-bt-card" style="display:none">
    <h3 style="cursor:pointer" onclick="toggleSection('stock-bt-body')">
      📈 个股回测排名 <span id="stock-bt-count" style="font-size:12px;color:var(--text-dim);font-weight:normal"></span>
      <span style="font-size:11px;color:var(--text-dim)">(点击展开/收起)</span>
    </h3>
    <div id="stock-bt-body"></div>
  </div>

  <!-- Configuration -->
  <div class="card">
    <h3 style="cursor:pointer" onclick="document.getElementById('config-body').style.display=document.getElementById('config-body').style.display==='none'?'block':'none'">
      ⚙️ 配置参数 <span style="font-size:11px;color:var(--text-dim)">(点击展开/收起)</span>
    </h3>
    <div id="config-body" style="display:none">
      <div class="form-grid">
        <div class="form-group"><label>筛选定时 (cron)</label><input id="cfg-screenCron" placeholder="30 7 * * 1-5"></div>
        <div class="form-group"><label>执行定时 (cron)</label><input id="cfg-executeCron" placeholder="31 9 * * 1-5"></div>
        <div class="form-group"><label>💰 初始资金 (¥)</label><input type="number" id="cfg-initialCapital" step="10000"></div>
        <div class="form-group"><label>执行比例 (%)</label><input type="number" id="cfg-executePct" min="10" max="100"></div>
        <div class="form-group"><label>风险偏好</label>
          <select id="cfg-riskTolerance"><option value="low">保守</option><option value="medium">中等</option><option value="high">积极</option><option value="aggressive">激进</option></select>
        </div>
        <div class="form-group"><label>最终股票数</label><input type="number" id="cfg-finalStockLimit" min="3" max="8"></div>
        <div class="form-group"><label>每策略TopN</label><input type="number" id="cfg-topNPerStrategy" min="5" max="20"></div>
        <div class="form-group"><label>Top策略数</label><input type="number" id="cfg-topStrategyCount" min="2" max="5"></div>
        <div class="form-group"><label>调仓周期(天)</label><input type="number" id="cfg-rebalanceDays" min="1" max="30"></div>
        <div class="form-group"><label>跟踪止损 (%)</label><input type="number" id="cfg-trailingStopPct" min="2" max="20" step="0.5"></div>
        <div class="form-group"><label>回测起始日</label><input type="date" id="cfg-startDate"></div>
        <div class="form-group"><label>推荐策略</label>
          <select id="cfg-recommendStrategy"><option value="mixed">混合(mixed)</option><option value="momentum">动量(momentum)</option><option value="mean_reversion">均值回归</option><option value="breakout">突破(breakout)</option></select>
        </div>
      </div>
      <div style="display:flex;gap:12px;margin-top:12px;flex-wrap:wrap;align-items:center">
        <label style="font-size:13px;display:flex;align-items:center;gap:6px"><input type="checkbox" id="cfg-dryRun"> 🧪 试运行（不真实下单）</label>
        <label style="font-size:13px;display:flex;align-items:center;gap:6px"><input type="checkbox" id="cfg-createConditionalOrders"> 📡 自动创建条件单</label>
        <label style="font-size:13px;display:flex;align-items:center;gap:6px"><input type="checkbox" id="cfg-useFitnessStrategies"> 🏆 使用适配度优选策略</label>
        <label style="font-size:13px;display:flex;align-items:center;gap:6px"><input type="checkbox" id="cfg-comboReoptEnabled"> 🎯 周末组合重优化</label>
      </div>
      <div style="margin-top:16px;display:flex;gap:8px">
        <button class="btn btn-primary" onclick="saveConfig()">💾 保存配置</button>
        <button class="btn" onclick="loadStatus()">↩️ 撤销修改</button>
      </div>
    </div>
  </div>
</div>

<script>
${SHARED_JS_HELPERS}
${SHARED_JS_API}

var API = '/quant/admin/api/auto-trade';
var refreshInterval = null;
var currentStatus = null;

function showToast(msg, type) {
  var existing = document.querySelector('.toast');
  if (existing) existing.remove();
  var t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  t.style.background = type==='error'?'#dc2626':type==='warn'?'#d97706':type==='ok'?'#16a34a':'#2563eb';
  document.body.appendChild(t);
  setTimeout(function(){ t.remove(); }, 4000);
}

async function loadStatus() {
  try {
    var resp = await fetch(API + '/status');
    var data = await resp.json();
    if (data.error) { showToast(data.error, 'error'); return; }
    currentStatus = data;
    renderStatus(data);
  } catch (err) {
    showToast('加载失败: ' + err.message, 'error');
  }
}

function renderStatus(s) {
  // KPI
  var isOn = s.enabled;
  document.getElementById('kpi-status').innerHTML = isOn
    ? '<span class="status-badge status-on">运行中</span>'
    : '<span class="status-badge status-off">已停止</span>';
  document.getElementById('kpi-mode').textContent = s.config.dryRun ? '🧪 试运行' : '⚡ 实盘';
  document.getElementById('kpi-pending').textContent = (s.pendingAllocations || []).length + ' 只';
  document.getElementById('kpi-screen').textContent = s.lastScreenAt ? formatTime(s.lastScreenAt) : '—';
  document.getElementById('kpi-execute').textContent = s.lastExecuteAt ? formatTime(s.lastExecuteAt) : '—';

  // Toggle
  document.getElementById('master-toggle').checked = isOn;
  document.getElementById('toggle-label').textContent = isOn ? '自动交易已启用' : '自动交易已停止';
  document.getElementById('toggle-label').style.color = isOn ? 'var(--green)' : 'var(--text-dim)';

  // Status detail
  var lines = [];
  lines.push('状态: ' + (isOn ? '✅ 运行中' : '⏸️ 已停止'));
  lines.push('模式: ' + (s.config.dryRun ? '🧪 试运行（不实际下单）' : '⚡ 实盘模式'));
  lines.push('筛选定时: ' + s.config.screenCron + '  |  执行定时: ' + s.config.executeCron);
  if (s.config.useFitnessStrategies) {
    lines.push('策略来源: 🏆 适配度优选 (Top ' + (s.config.fitnessTopN || 6) + ')');
  } else if (s.config.strategies && s.config.strategies.length) {
    lines.push('策略来源: 📌 手动指定 (' + s.config.strategies.length + ' 个)');
  } else {
    lines.push('策略来源: 🔄 全量策略');
  }
  lines.push('资金: ¥' + (s.config.initialCapital || 0).toLocaleString() + '  |  风险: ' + (s.config.riskTolerance || '—') + '  |  选股: ' + (s.config.finalStockLimit || 8) + ' 只');
  lines.push('');
  if (s.lastScreenAt) {
    lines.push('📊 上次筛选: ' + s.lastScreenAt);
    lines.push('   结果: ' + (s.lastScreenResult || '—'));
  } else {
    lines.push('📊 尚未执行过筛选 — 点击「🔍 手动筛选」开始');
  }
  if (s.lastExecuteAt) {
    lines.push('🚀 上次执行: ' + s.lastExecuteAt);
    lines.push('   结果: ' + (s.lastExecuteResult || '—'));
  }
  if (s.pendingSymbols && s.pendingSymbols.length) {
    lines.push('');
    lines.push('📋 待执行股票: ' + s.pendingSymbols.join(', '));
  }
  if (s.lastError) {
    lines.push('');
    lines.push('❌ 最后错误: ' + s.lastError);
  }
  document.getElementById('status-detail').textContent = lines.join('\\n');

  // Pending allocations
  var allocs = s.pendingAllocations || [];
  var allocCard = document.getElementById('alloc-card');
  if (allocs.length > 0) {
    allocCard.style.display = '';
    document.getElementById('alloc-count').textContent = '(' + allocs.length + ' 只)';
    var html = '<table class="alloc-table"><thead><tr><th>代码</th><th>名称</th><th>股数</th><th>价格</th><th>金额</th><th>状态</th></tr></thead><tbody>';
    var totalAmt = 0;
    var zeroCount = 0;
    allocs.forEach(function(a) {
      var code = a.code || a.symbol || '';
      var name = a.name || a.stock_name || '';
      var shares = a.shares || a.quantity || 0;
      var price = a.price || a.entry_price || 0;
      var amount = a.amount || (shares * price);
      totalAmt += amount;
      var isZero = shares <= 0;
      if (isZero) zeroCount++;
      var rowStyle = isZero ? 'opacity:0.5' : '';
      var statusHtml = isZero ? '<span style="color:var(--yellow)">⚠️ 资金不足</span>' : '<span style="color:var(--green)">✅ 就绪</span>';
      html += '<tr style="' + rowStyle + '"><td style="font-family:monospace"><a href="/quant/admin/stock?symbol=' + code + '" target="_blank" style="color:var(--accent);text-decoration:none">' + code + '</a></td><td>' + name + '</td><td>' + shares + '</td><td>¥' + (price > 0 ? price.toFixed(2) : '-') + '</td><td>¥' + amount.toLocaleString('zh-CN', {maximumFractionDigits:0}) + '</td><td>' + statusHtml + '</td></tr>';
    });
    if (zeroCount > 0) {
      html += '<tr><td colspan="6" style="color:var(--yellow);font-size:11px;padding-top:8px">⚠️ ' + zeroCount + ' 只股票因资金不足未分配股数，可在配置中增加初始资金或减少选股数量</td></tr>';
    }
    html += '<tr style="font-weight:600;border-top:2px solid var(--border)"><td colspan="4">合计</td><td>¥' + totalAmt.toLocaleString('zh-CN', {maximumFractionDigits:0}) + '</td><td></td></tr>';
    html += '</tbody></table>';
    document.getElementById('alloc-body').innerHTML = html;
    document.getElementById('alloc-actions').style.display = '';
  } else {
    allocCard.style.display = 'none';
  }

  // Config form
  fillConfig(s.config);

  // Rich screening snapshot sections
  renderScreenSnapshot(s.lastScreenSnapshot);
}

function fillConfig(c) {
  document.getElementById('cfg-screenCron').value = c.screenCron || '';
  document.getElementById('cfg-executeCron').value = c.executeCron || '';
  document.getElementById('cfg-initialCapital').value = c.initialCapital || 500000;
  document.getElementById('cfg-executePct').value = c.executePct || 60;
  document.getElementById('cfg-riskTolerance').value = c.riskTolerance || 'high';
  document.getElementById('cfg-finalStockLimit').value = c.finalStockLimit || 8;
  document.getElementById('cfg-topNPerStrategy').value = c.topNPerStrategy || 8;
  document.getElementById('cfg-topStrategyCount').value = c.topStrategyCount || 3;
  document.getElementById('cfg-rebalanceDays').value = c.rebalanceDays || 5;
  document.getElementById('cfg-trailingStopPct').value = c.trailingStopPct || 8;
  document.getElementById('cfg-startDate').value = c.startDate || '';
  document.getElementById('cfg-recommendStrategy').value = c.recommendStrategy || 'mixed';
  document.getElementById('cfg-dryRun').checked = c.dryRun !== false;
  document.getElementById('cfg-createConditionalOrders').checked = c.createConditionalOrders !== false;
  document.getElementById('cfg-useFitnessStrategies').checked = c.useFitnessStrategies !== false;
  document.getElementById('cfg-comboReoptEnabled').checked = !!c.comboReoptEnabled;
}

async function toggleEnabled(on) {
  try {
    var resp = await fetch(API + '/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: on }),
    });
    var data = await resp.json();
    if (data.ok) {
      showToast(on ? '✅ 自动交易已启用' : '⏸️ 自动交易已停止', 'ok');
      loadStatus();
    } else {
      showToast(data.error || '操作失败', 'error');
    }
  } catch (err) {
    showToast('操作失败: ' + err.message, 'error');
  }
}

async function saveConfig() {
  var cfg = {
    screenCron: document.getElementById('cfg-screenCron').value,
    executeCron: document.getElementById('cfg-executeCron').value,
    initialCapital: parseInt(document.getElementById('cfg-initialCapital').value) || 500000,
    executePct: parseInt(document.getElementById('cfg-executePct').value) || 60,
    riskTolerance: document.getElementById('cfg-riskTolerance').value,
    finalStockLimit: parseInt(document.getElementById('cfg-finalStockLimit').value) || 8,
    topNPerStrategy: parseInt(document.getElementById('cfg-topNPerStrategy').value) || 8,
    topStrategyCount: parseInt(document.getElementById('cfg-topStrategyCount').value) || 3,
    rebalanceDays: parseInt(document.getElementById('cfg-rebalanceDays').value) || 5,
    trailingStopPct: parseFloat(document.getElementById('cfg-trailingStopPct').value) || 8,
    startDate: document.getElementById('cfg-startDate').value || '',
    recommendStrategy: document.getElementById('cfg-recommendStrategy').value,
    dryRun: document.getElementById('cfg-dryRun').checked,
    createConditionalOrders: document.getElementById('cfg-createConditionalOrders').checked,
    useFitnessStrategies: document.getElementById('cfg-useFitnessStrategies').checked,
    comboReoptEnabled: document.getElementById('cfg-comboReoptEnabled').checked,
  };

  try {
    var resp = await fetch(API + '/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    var data = await resp.json();
    if (data.ok) {
      showToast('✅ 配置已保存', 'ok');
      loadStatus();
    } else {
      showToast(data.error || '保存失败', 'error');
    }
  } catch (err) {
    showToast('保存失败: ' + err.message, 'error');
  }
}

async function triggerScreen() {
  var btn = document.getElementById('btn-screen');
  btn.disabled = true;
  btn.textContent = '⏳ 筛选中…';
  showToast('正在执行筛选，请稍候…', 'info');

  try {
    var resp = await fetch(API + '/trigger-screen', { method: 'POST' });
    var data = await resp.json();
    if (data.ok) {
      var msg = '✅ 筛选完成: ' + (data.symbols || []).length + ' 只入围';
      if (data.allocations && data.allocations.length) {
        msg += ', ' + data.allocations.length + ' 只已生成推荐';
      }
      showToast(msg, 'ok');
    } else {
      showToast('筛选失败: ' + (data.error || ''), 'error');
    }
    loadStatus();
  } catch (err) {
    showToast('筛选失败: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 手动筛选';
  }
}

async function triggerExecute() {
  if (!currentStatus || !(currentStatus.pendingAllocations || []).length) {
    showToast('没有待执行的推荐，请先运行筛选', 'warn');
    return;
  }
  var mode = currentStatus.config.dryRun ? '试运行' : '实盘下单';
  if (!confirm('确认' + mode + '？\\n将执行 ' + currentStatus.pendingAllocations.length + ' 只股票的推荐方案。')) return;

  var btn = document.getElementById('btn-execute');
  btn.disabled = true;
  btn.textContent = '⏳ 执行中…';

  try {
    var resp = await fetch(API + '/trigger-execute', { method: 'POST' });
    var data = await resp.json();
    if (data.ok) {
      showToast('✅ 执行完成', 'ok');
    } else {
      showToast('执行失败: ' + (data.error || ''), 'error');
    }
    loadStatus();
  } catch (err) {
    showToast('执行失败: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '🚀 手动执行';
  }
}

async function emergencyStop() {
  if (!confirm('⚠️ 确认紧急停止？\\n将立即禁用自动交易调度，已提交的订单不会被撤销。')) return;

  try {
    var resp = await fetch(API + '/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: false }),
    });
    var data = await resp.json();
    if (data.ok) {
      showToast('⏹️ 自动交易已紧急停止', 'warn');
      loadStatus();
    } else {
      showToast(data.error || '停止失败', 'error');
    }
  } catch (err) {
    showToast('停止失败: ' + err.message, 'error');
  }
}

function formatTime(iso) {
  if (!iso) return '—';
  try {
    var d = new Date(iso);
    return (d.getMonth()+1) + '/' + d.getDate() + ' ' + d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0');
  } catch(e) { return iso; }
}

function applyBatchMonitor() {
  // Navigate to optimization page where batch monitor functionality exists
  window.open('/quant/admin/stock-optimization', '_blank');
  showToast('已打开组合优化页面，请勾选股票后点击"📡 批量监控"', 'info');
}

function viewInOptimization() {
  window.open('/quant/admin/stock-optimization', '_blank');
}

function toggleSection(id) {
  var el = document.getElementById(id);
  if (el) el.style.display = el.style.display === 'none' ? '' : 'none';
}

function pct(v) { return v != null ? (v * 100).toFixed(1) + '%' : '—'; }
function num2(v) { return v != null ? Number(v).toFixed(2) : '—'; }
function scoreColor(v) { return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text-dim)'; }
function strategyCategory(id) {
  if (!id) return '';
  if (id.startsWith('cta-')) return 'CTA';
  if (id.indexOf('grid') >= 0) return '网格';
  if (id.indexOf('mean-reversion') >= 0 || id.indexOf('reversion') >= 0) return '均值回归';
  if (id.indexOf('momentum') >= 0 || id.indexOf('breakout') >= 0) return '动量/突破';
  if (id.indexOf('rsi') >= 0 || id.indexOf('stochastic') >= 0 || id.indexOf('oscillator') >= 0) return '振荡器';
  if (id.indexOf('macd') >= 0) return '趋势';
  if (id.indexOf('ma-') >= 0 || id.indexOf('-ma') >= 0 || id.indexOf('crossover') >= 0) return '均线';
  if (id.indexOf('volume') >= 0 || id.indexOf('obv') >= 0 || id.indexOf('mfi') >= 0) return '量价';
  if (id.indexOf('bollinger') >= 0 || id.indexOf('keltner') >= 0 || id.indexOf('envelope') >= 0) return '通道';
  if (id.indexOf('regime') >= 0 || id.indexOf('adaptive') >= 0) return '自适应';
  return '综合';
}

function renderScreenSnapshot(snap) {
  // Hide all snapshot sections if no data
  var ids = ['market-card','strategy-card','combined-card','routing-card','stock-bt-card'];
  if (!snap) {
    ids.forEach(function(id){ document.getElementById(id).style.display = 'none'; });
    return;
  }

  // ── Market Context ──
  var mCard = document.getElementById('market-card');
  if (snap.marketEnrichment && snap.marketEnrichment.macroRegime) {
    mCard.style.display = '';
    var me = snap.marketEnrichment;
    var pg = me.positionGuidance || {};
    var regimeColors = { BULL: '#4ade80', NEUTRAL: '#fbbf24', BEAR: '#f87171', RECOVERY: '#60a5fa' };
    var regimeColor = regimeColors[me.macroRegime] || 'var(--text-dim)';
    var html = '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center">';
    html += '<div style="font-size:20px;font-weight:700;color:' + regimeColor + '">' + (me.macroRegime || '—') + '</div>';
    html += '<div style="color:var(--text-dim);font-size:13px;flex:1;min-width:200px">' + (me.macroDescription || '') + '</div>';
    html += '</div>';
    if (pg.positionScale != null) {
      html += '<div style="display:flex;gap:20px;margin-top:12px;flex-wrap:wrap;font-size:12px">';
      html += '<div>📏 仓位系数: <b>' + (pg.positionScale != null ? (pg.positionScale * 100).toFixed(0) + '%' : '—') + '</b></div>';
      html += '<div>🔒 单只上限: <b>' + (pg.maxSinglePositionPct != null ? pg.maxSinglePositionPct + '%' : '—') + '</b></div>';
      html += '<div>📊 最大持仓: <b>' + (pg.maxPositions || '—') + ' 只</b></div>';
      html += '<div>🎯 调整方向: <b>' + (pg.regimeAdjustment || '—') + '</b></div>';
      html += '</div>';
    }
    if (snap.resolvedStrategies && snap.resolvedStrategies.length) {
      html += '<div style="margin-top:10px;font-size:12px;color:var(--text-dim)">🎯 使用策略: <span style="color:var(--accent)">' + snap.resolvedStrategies.join(', ') + '</span></div>';
    }
    html += '<div style="margin-top:6px;font-size:11px;color:var(--text-dim)">生成时间: ' + snap.generatedAt + '</div>';
    document.getElementById('market-body').innerHTML = html;
  } else {
    mCard.style.display = 'none';
  }

  // ── Strategy Ranking ──
  var sCard = document.getElementById('strategy-card');
  var ranking = snap.strategyRanking || [];
  if (ranking.length > 0) {
    sCard.style.display = '';
    var selSet = {};
    (snap.selectedStrategies || []).forEach(function(s){ selSet[s.strategy] = true; });
    document.getElementById('strategy-count').textContent = '(' + ranking.length + ' 个策略)';
    var html = '<table class="alloc-table"><thead><tr><th>#</th><th>策略</th><th>综合分</th><th>总收益</th><th>年化</th><th>夏普</th><th>最大回撤</th><th>胜率</th><th>利润因子</th><th>交易次数</th><th>候选股</th></tr></thead><tbody>';
    ranking.forEach(function(r) {
      var isSel = selSet[r.strategy];
      var rowStyle = isSel ? 'background:rgba(34,197,94,0.08)' : r.error ? 'opacity:0.5' : '';
      var badge = isSel ? '<span style="color:#4ade80;font-size:10px">✓ 入选</span> ' : '';
      html += '<tr style="' + rowStyle + '">';
      html += '<td>' + (r.rank || '—') + '</td>';
      html += '<td>' + badge + '<b>' + (r.strategyLabel || r.strategy) + '</b></td>';
      html += '<td style="color:' + scoreColor(r.compositeScore) + '">' + num2(r.compositeScore) + '</td>';
      html += '<td style="color:' + scoreColor(r.totalReturn) + '">' + pct(r.totalReturn) + '</td>';
      html += '<td style="color:' + scoreColor(r.annualizedReturn) + '">' + pct(r.annualizedReturn) + '</td>';
      html += '<td>' + num2(r.sharpeRatio) + '</td>';
      html += '<td style="color:var(--red)">' + pct(r.maxDrawdown) + '</td>';
      html += '<td>' + pct(r.winRate) + '</td>';
      html += '<td>' + num2(r.profitFactor) + '</td>';
      html += '<td>' + (r.totalTrades || '—') + '</td>';
      html += '<td style="font-size:11px;color:var(--text-dim)">' + (r.topSymbols || []).slice(0,4).join(', ') + '</td>';
      html += '</tr>';
      if (r.error) {
        html += '<tr style="opacity:0.5"><td></td><td colspan="10" style="font-size:11px;color:var(--red)">❌ ' + r.error + '</td></tr>';
      }
    });
    html += '</tbody></table>';
    document.getElementById('strategy-body').innerHTML = html;
  } else {
    sCard.style.display = 'none';
  }

  // ── Combined Stock List ──
  var cCard = document.getElementById('combined-card');
  var combined = snap.combinedList || [];
  if (combined.length > 0) {
    cCard.style.display = '';
    document.getElementById('combined-count').textContent = '(' + combined.length + ' 只)';
    var html = '<table class="alloc-table"><thead><tr><th>#</th><th>代码</th><th>名称</th><th>综合分</th><th>出现次数</th><th>回测收益</th><th>夏普</th><th>回撤</th><th>入选策略</th><th>选股理由</th></tr></thead><tbody>';
    combined.forEach(function(c) {
      var isSel = c.selected;
      var rowStyle = isSel ? 'background:rgba(34,197,94,0.08)' : 'opacity:0.6';
      var badge = isSel ? '<span style="color:#4ade80;font-size:10px">✓</span> ' : '';
      // Collect top reasons from all source strategies
      var allReasons = [];
      (c.sourceDetails || []).forEach(function(sd) {
        (sd.reasons || []).forEach(function(r) {
          if (allReasons.indexOf(r) === -1 && allReasons.length < 3) allReasons.push(r);
        });
      });
      html += '<tr style="' + rowStyle + '">';
      html += '<td>' + c.rank + '</td>';
      html += '<td style="font-family:monospace"><a href="/quant/admin/stock?symbol=' + c.symbol + '" target="_blank" style="color:var(--accent);text-decoration:none">' + badge + c.symbol + '</a></td>';
      html += '<td>' + c.name + '</td>';
      html += '<td style="color:' + scoreColor(c.combinedScore) + '">' + num2(c.combinedScore) + '</td>';
      html += '<td><b>' + c.appearanceCount + '</b> 个策略</td>';
      html += '<td style="color:' + scoreColor(c.backtestReturn) + '">' + pct(c.backtestReturn) + '</td>';
      html += '<td>' + num2(c.backtestSharpe) + '</td>';
      html += '<td style="color:var(--red)">' + pct(c.backtestDrawdown) + '</td>';
      html += '<td style="font-size:11px">' + (c.selectedBy || []).join(', ') + '</td>';
      html += '<td style="font-size:11px;color:var(--text-dim)">' + allReasons.join('; ') + '</td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    document.getElementById('combined-body').innerHTML = html;
  } else {
    cCard.style.display = 'none';
  }

  // ── Routing Enrichment ──
  var rCard = document.getElementById('routing-card');
  var routing = snap.routingEnrichment || [];
  if (routing.length > 0) {
    rCard.style.display = '';
    document.getElementById('routing-count').textContent = '(' + routing.length + ' 只)';
    var html = '<table class="alloc-table"><thead><tr><th>代码</th><th>名称</th><th>组合策略</th><th>策略列表</th><th>波动性</th><th>趋势</th><th>分配方式</th><th>理由</th></tr></thead><tbody>';
    routing.forEach(function(r) {
      html += '<tr>';
      html += '<td style="font-family:monospace"><a href="/quant/admin/stock?symbol=' + r.symbol + '" target="_blank" style="color:var(--accent);text-decoration:none">' + r.symbol + '</a></td>';
      html += '<td>' + (r.name || '') + '</td>';
      html += '<td><b>' + (r.combo || '—') + '</b></td>';
      html += '<td style="font-size:11px">' + (r.strategies || []).join(', ') + '</td>';
      html += '<td>' + (r.volatility || '—') + '</td>';
      html += '<td>' + (r.trend || '—') + '</td>';
      html += '<td style="font-size:11px">' + (r.comboMethod || r.source || '—') + '</td>';
      html += '<td style="font-size:11px;color:var(--text-dim)">' + (r.reason || '—') + '</td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    document.getElementById('routing-body').innerHTML = html;
  } else {
    rCard.style.display = 'none';
  }

  // ── Stock Backtest Ranking ──
  var btCard = document.getElementById('stock-bt-card');
  var stockBt = snap.stockBacktestRanking || [];
  if (stockBt.length > 0) {
    btCard.style.display = '';
    document.getElementById('stock-bt-count').textContent = '(' + stockBt.length + ' 只)';
    var html = '<table class="alloc-table"><thead><tr><th>#</th><th>代码</th><th>名称</th><th>回测策略</th><th>总收益</th><th>夏普比率</th><th>最大回撤</th><th>胜率</th></tr></thead><tbody>';
    stockBt.forEach(function(r) {
      html += '<tr>';
      html += '<td>' + (r.rank || '—') + '</td>';
      html += '<td style="font-family:monospace"><a href="/quant/admin/stock?symbol=' + r.symbol + '" target="_blank" style="color:var(--accent);text-decoration:none">' + r.symbol + '</a></td>';
      html += '<td>' + (r.name || '') + '</td>';
      html += '<td>' + (r.strategyLabel || r.strategy || '—') + '</td>';
      html += '<td style="color:' + scoreColor(r.totalReturn) + '">' + pct(r.totalReturn) + '</td>';
      html += '<td>' + num2(r.sharpeRatio) + '</td>';
      html += '<td style="color:var(--red)">' + pct(r.maxDrawdown) + '</td>';
      html += '<td>' + pct(r.winRate) + '</td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    document.getElementById('stock-bt-body').innerHTML = html;
  } else {
    btCard.style.display = 'none';
  }
}

// ── Fitness Strategies (live fetch) ──
async function loadFitnessStrategies() {
  try {
    var resp = await fetch('/quant/admin/api/strategy-fitness/global');
    var data = await resp.json();
    var strats = data.strategies || [];
    var el = document.getElementById('fitness-body');
    var countEl = document.getElementById('fitness-count');
    if (strats.length === 0) {
      el.innerHTML = '<div style="color:var(--text-dim);font-size:12px">暂无适配度数据。请先运行 <a href="/quant/admin/strategy-fitness" target="_blank" style="color:var(--accent)">策略适配度分析</a>。</div>';
      countEl.textContent = '';
      return;
    }
    countEl.textContent = '(' + strats.length + ' 个)';
    var html = '<table class="alloc-table"><thead><tr><th>#</th><th>策略</th><th>综合评分</th><th>年化收益</th><th>夏普比率</th><th>最大回撤</th><th>胜率</th><th>标签</th></tr></thead><tbody>';
    strats.forEach(function(s, i) {
      var score = s.composite_score || s.avg_score || s.final_score || s.score || 0;
      var ann = s.annualized_return || s.annual_return;
      var sharpe = s.sharpe_ratio || s.sharpe;
      var dd = s.max_drawdown;
      var wr = s.win_rate;
      var label = s.strategy_label || s.strategy_name || s.strategy_id || '';
      var tags = s.tags || s.category || strategyCategory(s.strategy_id);
      html += '<tr>';
      html += '<td>' + (s.rank || (i+1)) + '</td>';
      html += '<td><b>' + label + '</b><div style="font-size:10px;color:var(--text-dim)">' + (s.strategy_id || '') + '</div></td>';
      html += '<td style="color:' + scoreColor(score) + ';font-weight:600">' + num2(score) + '</td>';
      html += '<td style="color:' + scoreColor(ann) + '">' + pct(ann) + '</td>';
      html += '<td>' + num2(sharpe) + '</td>';
      html += '<td style="color:var(--red)">' + pct(dd) + '</td>';
      html += '<td>' + pct(wr) + '</td>';
      html += '<td style="font-size:11px;color:var(--text-dim)">' + tags + '</td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    html += '<div style="margin-top:8px;font-size:11px;color:var(--text-dim)">数据来源: <a href="/quant/admin/strategy-fitness" target="_blank" style="color:var(--accent)">策略适配度分析</a>  |  batch: ' + (data.batch_id || '—') + '</div>';
    el.innerHTML = html;
  } catch(e) {
    document.getElementById('fitness-body').innerHTML = '<div style="color:var(--text-dim);font-size:12px">加载失败: ' + e.message + '</div>';
  }
}

// ── Pick History (live fetch) ──
async function loadPickHistory() {
  try {
    var resp = await fetch('/quant/admin/api/daily-pick-history');
    var data = await resp.json();
    var items = Array.isArray(data) ? data : [];
    var el = document.getElementById('history-body');
    var countEl = document.getElementById('history-count');
    if (items.length === 0) {
      el.innerHTML = '<div style="color:var(--text-dim);font-size:12px">暂无历史记录。运行「手动筛选」后可在 <a href="/quant/admin/daily-picks" target="_blank" style="color:var(--accent)">每日综合选股</a> 页面保存结果。</div>';
      countEl.textContent = '';
      return;
    }
    // Show most recent first, limit 10
    items.sort(function(a,b){ return (b.createdAt||'').localeCompare(a.createdAt||''); });
    items = items.slice(0, 10);
    countEl.textContent = '(最近 ' + items.length + ' 条)';
    var html = '<table class="alloc-table"><thead><tr><th>时间</th><th>入围股票</th><th>策略数</th><th>备注</th><th>操作</th></tr></thead><tbody>';
    items.forEach(function(item) {
      var summary = item.summary || {};
      var params = item.params || {};
      var symbols = summary.finalSymbols || summary.symbols || [];
      var stratCount = summary.strategyCount || params.topStrategyCount || params.top_strategy_count || '—';
      var note = summary.note || '';
      if (typeof symbols === 'object' && Array.isArray(symbols)) {
        note = symbols.join(', ');
      }
      html += '<tr>';
      html += '<td style="white-space:nowrap">' + formatTime(item.createdAt) + '</td>';
      html += '<td><b>' + (Array.isArray(symbols) ? symbols.length : '—') + '</b> 只</td>';
      html += '<td>' + stratCount + '</td>';
      html += '<td style="font-size:11px;color:var(--text-dim);max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + note + '</td>';
      html += '<td><a href="/quant/admin/daily-picks?history=' + (item.id||'') + '" target="_blank" style="color:var(--accent);font-size:11px;text-decoration:none">查看详情 →</a></td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
  } catch(e) {
    document.getElementById('history-body').innerHTML = '<div style="color:var(--text-dim);font-size:12px">加载失败: ' + e.message + '</div>';
  }
}

// Auto-refresh every 10 seconds
loadStatus();
loadFitnessStrategies();
loadPickHistory();
refreshInterval = setInterval(loadStatus, 10000);

</script>
</body>
</html>`;
}

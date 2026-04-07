/** Investment Planner — Trade Plan panel (today plan, create, mark, close) */
export function getTradePlanJs(): string {
  return `
// ── Trade Plan state ────────────────────────────────────────
var _tradePlanEditing = null; // plan ID being edited

function loadTodayPlan() {
  var el = document.getElementById('today-plan-content');
  el.innerHTML = '<p style="color:var(--text-dim)">加载中...</p>';
  api('/quant/trade-plans/today').then(function(r) {
    if (!r.plan) {
      el.innerHTML = '<p style="color:var(--text-dim)">今日暂无交易计划，点击右上角 ➕ 新建</p>';
      return;
    }
    renderTodayPlan(r.plan);
  }).catch(function() {
    el.innerHTML = '<p style="color:var(--red)">加载失败</p>';
  });
}

function renderTodayPlan(plan) {
  var el = document.getElementById('today-plan-content');
  var statusBadge = plan.status === 'open'
    ? '<span style="background:#22c55e;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">进行中</span>'
    : '<span style="background:#6b7280;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">已关闭</span>';

  var kpis = '<div class="kpi-row" style="margin:8px 0">'
    + kpiHtml(plan.stocks.length, '目标股票数')
    + kpiHtml(num(plan.budget), '仓位预算(元)')
    + kpiHtml(plan.stocks.filter(function(s){return s.executed}).length + '/' + plan.stocks.length, '已执行')
    + '</div>';

  var rows = plan.stocks.map(function(s) {
    var execStatus = s.executed
      ? '<span style="color:var(--green)">✅ 已执行</span>'
      : '<span style="color:var(--text-dim)">⏳ 待执行</span>';
    var devInfo = '';
    if (s.executed && s.actualEntryPrice != null) {
      var dev = ((s.actualEntryPrice - s.entryPrice) / s.entryPrice * 100).toFixed(2);
      devInfo = '<br><span style="font-size:11px;color:var(--text-dim)">实际入场: ' + num(s.actualEntryPrice) + ' (偏差' + dev + '%)</span>';
    }
    return '<tr>'
      + '<td>' + esc(s.code) + '</td>'
      + '<td>' + esc(s.name || '-') + '</td>'
      + '<td>' + num(s.entryPrice) + '</td>'
      + '<td>' + num(s.stopPrice) + '</td>'
      + '<td>' + num(s.takeProfitPrice) + '</td>'
      + '<td>' + (s.positionRatio * 100).toFixed(0) + '%</td>'
      + '<td style="max-width:200px;white-space:normal;font-size:12px">' + esc(s.reason) + '</td>'
      + '<td>' + execStatus + devInfo + '</td>'
      + '<td>'
      + (plan.status === 'open' && !s.executed ? '<button class="btn btn-sm" onclick="markStockExecution(\\'' + esc(plan.id) + '\\',\\'' + esc(s.code) + '\\')">标记执行</button>' : '')
      + '</td>'
      + '</tr>';
  }).join('');

  var table = '<div style="overflow-x:auto"><table>'
    + '<thead><tr><th>代码</th><th>名称</th><th>入场价</th><th>止损</th><th>止盈</th><th>仓位</th><th>理由</th><th>状态</th><th>操作</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table></div>';

  var notes = plan.notes ? '<p style="font-size:13px;color:var(--text-dim);margin:8px 0">📝 ' + esc(plan.notes) + '</p>' : '';

  var actions = '';
  if (plan.status === 'open') {
    actions = '<div class="btn-group" style="margin-top:12px">'
      + '<button class="btn btn-primary" onclick="closeTradePlanWithReport(\\'' + esc(plan.id) + '\\')">📊 关闭并生成偏差报告</button>'
      + '</div>';
  } else if (plan.deviationReport) {
    actions = '<div class="btn-group" style="margin-top:12px">'
      + '<button class="btn" onclick="showDeviationReport(\\'' + esc(plan.id) + '\\')">📊 查看偏差报告</button>'
      + '</div>';
  }

  el.innerHTML = '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
    + '<strong>' + esc(plan.date) + '</strong> ' + statusBadge
    + ' <span style="font-size:12px;color:var(--text-dim)">ID: ' + esc(plan.id) + '</span>'
    + '</div>'
    + notes + kpis + table + actions;
}

function showCreatePlanModal() {
  _tradePlanEditing = null;
  document.getElementById('trade-plan-modal-title').textContent = '📋 新建交易计划';
  document.getElementById('tp-date').value = new Date().toISOString().slice(0,10);
  document.getElementById('tp-budget').value = '100000';
  document.getElementById('tp-notes').value = '';
  document.getElementById('tp-stocks-list').innerHTML = '';
  addTpStockRow();
  addTpStockRow();
  document.getElementById('trade-plan-modal').style.display = '';
}

function closeTradePlanModal() {
  document.getElementById('trade-plan-modal').style.display = 'none';
}

function closeDeviationModal() {
  document.getElementById('deviation-report-modal').style.display = 'none';
}

function addTpStockRow() {
  var container = document.getElementById('tp-stocks-list');
  var idx = container.children.length;
  var row = document.createElement('div');
  row.className = 'tp-stock-row';
  row.style.cssText = 'display:grid;grid-template-columns:100px 80px 80px 80px 60px 1fr 30px;gap:6px;margin-bottom:6px;align-items:center';
  row.innerHTML = ''
    + '<input type="text" placeholder="代码" class="tp-code" style="width:100%">'
    + '<input type="number" placeholder="入场价" class="tp-entry" style="width:100%">'
    + '<input type="number" placeholder="止损价" class="tp-stop" style="width:100%">'
    + '<input type="number" placeholder="止盈价" class="tp-tp" style="width:100%">'
    + '<input type="number" placeholder="%" class="tp-ratio" style="width:100%" min="1" max="100" value="20">'
    + '<input type="text" placeholder="买入理由" class="tp-reason" style="width:100%">'
    + '<button class="btn btn-sm btn-danger" onclick="this.parentElement.remove()" style="padding:2px 6px">✕</button>';
  container.appendChild(row);
}

function submitTradePlan() {
  var date = document.getElementById('tp-date').value;
  var budget = Number(document.getElementById('tp-budget').value);
  var notes = document.getElementById('tp-notes').value;
  if (!date || !budget) { toast('请填写日期和预算'); return; }

  var rows = document.querySelectorAll('.tp-stock-row');
  var stocks = [];
  for (var i = 0; i < rows.length; i++) {
    var code = rows[i].querySelector('.tp-code').value.trim();
    var entry = Number(rows[i].querySelector('.tp-entry').value);
    var stop = Number(rows[i].querySelector('.tp-stop').value);
    var tp = Number(rows[i].querySelector('.tp-tp').value);
    var ratio = Number(rows[i].querySelector('.tp-ratio').value) / 100;
    var reason = rows[i].querySelector('.tp-reason').value.trim();
    if (!code || !entry) continue;
    stocks.push({ code: code, entryPrice: entry, stopPrice: stop, takeProfitPrice: tp, positionRatio: ratio, reason: reason || '未填写' });
  }
  if (stocks.length === 0) { toast('请至少添加一只目标股票'); return; }

  var body = JSON.stringify({ date: date, budget: budget, notes: notes, stocks: stocks });
  api('/quant/trade-plans', { method: 'POST', body: body }).then(function(r) {
    if (r.status === 'ok') {
      toast('✅ 交易计划已创建: ' + r.id);
      closeTradePlanModal();
      loadTodayPlan();
    } else {
      toast('❌ ' + (r.message || '创建失败'));
    }
  });
}

function markStockExecution(planId, code) {
  var price = prompt('请输入实际入场价 (留空表示未执行):');
  if (price === null) return;
  var executed = price.trim() !== '';
  var actualEntry = executed ? Number(price) : null;
  var note = executed ? prompt('执行备注 (可留空):') || '' : '';

  var body = JSON.stringify({
    code: code,
    executed: executed,
    actual_entry_price: actualEntry,
    execution_note: note
  });
  api('/quant/trade-plans/detail?id=' + encodeURIComponent(planId) + '&action=mark', {
    method: 'POST', body: body
  }).then(function(r) {
    if (r.status === 'ok') {
      toast('✅ 已更新');
      loadTodayPlan();
    } else {
      toast('❌ ' + (r.message || '更新失败'));
    }
  });
}

function closeTradePlanWithReport(planId) {
  if (!confirm('确认关闭此计划并生成偏差报告？')) return;
  api('/quant/trade-plans/detail?id=' + encodeURIComponent(planId) + '&action=close', {
    method: 'POST', body: '{}'
  }).then(function(r) {
    if (r.status === 'ok' && r.report) {
      toast('✅ 计划已关闭');
      loadTodayPlan();
      renderDeviationReport(r.report);
      document.getElementById('deviation-report-modal').style.display = '';
    } else {
      toast('❌ ' + (r.message || '关闭失败'));
    }
  });
}

function showDeviationReport(planId) {
  api('/quant/trade-plans/detail?id=' + encodeURIComponent(planId)).then(function(r) {
    if (r.status === 'ok' && r.plan && r.plan.deviationReport) {
      renderDeviationReport(r.plan.deviationReport);
      document.getElementById('deviation-report-modal').style.display = '';
    } else {
      toast('未找到偏差报告');
    }
  });
}

function renderDeviationReport(report) {
  var el = document.getElementById('deviation-report-content');
  var kpis = '<div class="kpi-row">'
    + kpiHtml(report.totalPlanned, '计划股票数')
    + kpiHtml(report.totalExecuted, '实际执行数')
    + kpiHtml((report.executionRate * 100).toFixed(0) + '%', '执行率')
    + '</div>';

  var rows = report.items.map(function(item) {
    var devStr = item.entryDeviation != null ? item.entryDeviation.toFixed(2) + '%' : '-';
    var devColor = item.entryDeviation != null
      ? (Math.abs(item.entryDeviation) > 2 ? 'var(--red)' : 'var(--green)')
      : 'var(--text-dim)';
    return '<tr>'
      + '<td>' + esc(item.code) + '</td>'
      + '<td>' + esc(item.name || '-') + '</td>'
      + '<td>' + num(item.planned.entryPrice) + '</td>'
      + '<td>' + (item.actual.entryPrice != null ? num(item.actual.entryPrice) : '-') + '</td>'
      + '<td style="color:' + devColor + '">' + devStr + '</td>'
      + '<td>' + (item.executionRate ? '✅' : '❌') + '</td>'
      + '</tr>';
  }).join('');

  var table = '<div style="overflow-x:auto;margin-top:12px"><table>'
    + '<thead><tr><th>代码</th><th>名称</th><th>计划入场</th><th>实际入场</th><th>入场偏差</th><th>已执行</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table></div>';

  el.innerHTML = '<p style="font-size:12px;color:var(--text-dim)">生成时间: ' + esc(report.generatedAt) + '</p>'
    + kpis + table;
}

// Auto-load today plan on page init
setTimeout(loadTodayPlan, 300);
`;
}

/**
 * Performance Report Page — main browser-side script.
 */
import { SHARED_JS_HELPERS, SHARED_JS_API } from "../_shared.js";

export function getPerformanceReportMainScript(baseUrlJson: string): string {
  return `
var B=${baseUrlJson};
${SHARED_JS_HELPERS}
${SHARED_JS_API}

// ── State ───────────────────────────────────────────────────────

var attrData = null;   // AttributionResult
var equityData = null;  // EquitySnapshot[]
var equitySummary = null;
var tradesRaw = [];

// ── Helpers ─────────────────────────────────────────────────────

function getMode() { return document.getElementById('modeSelect').value; }

function getDateRange() {
  var fromEl = document.getElementById('dateFrom');
  var toEl = document.getElementById('dateTo');
  if (fromEl.value && toEl.value) {
    return { from: fromEl.value, to: toEl.value };
  }
  var range = document.getElementById('rangeSelect').value;
  if (range === 'all') return { from: '', to: '' };
  var now = new Date();
  var to = now.toISOString().slice(0, 10);
  var from = new Date(now.getTime() - parseInt(range) * 86400000).toISOString().slice(0, 10);
  return { from: from, to: to };
}

function colorVal(v, decimals) {
  decimals = decimals || 2;
  if (v == null) return '-';
  var cls = v > 0 ? 'positive' : v < 0 ? 'negative' : '';
  var prefix = v > 0 ? '+' : '';
  return '<span class="' + cls + '">' + prefix + Number(v).toFixed(decimals) + '</span>';
}

function pctBadge(rate) {
  if (rate == null) return '-';
  var v = (rate * 100).toFixed(1);
  var cls = rate >= 0.5 ? 'badge-green' : rate >= 0.3 ? 'badge-yellow' : 'badge-red';
  return '<span class="badge ' + cls + '">' + v + '%</span>';
}

function contributionBar(pct) {
  var absPct = Math.min(Math.abs(pct), 100);
  var color = pct >= 0 ? 'var(--green)' : 'var(--red)';
  return '<div class="progress-bar" style="width:80px;display:inline-block;vertical-align:middle;margin-left:6px"><div class="progress-bar-fill" style="width:' + absPct + '%;background:' + color + '"></div></div>';
}

// ── Tab switching ───────────────────────────────────────────────

function switchTab(name) {
  var btns = document.querySelectorAll('.tab-btn');
  var tabs = document.querySelectorAll('.tab-content');
  for (var i = 0; i < btns.length; i++) btns[i].classList.remove('active');
  for (var i = 0; i < tabs.length; i++) tabs[i].classList.remove('active');
  event.target.classList.add('active');
  var el = document.getElementById('tab-' + name);
  if (el) el.classList.add('active');
}

// ── Data loading ────────────────────────────────────────────────

async function reload() {
  await Promise.all([loadAttribution(), loadEquityCurve(), loadEquitySummary(), loadHitRate()]);
}

async function loadAttribution() {
  var mode = getMode();
  var r = getDateRange();

  // Load trades
  try {
    var resp = await api('/quant/api/trades?mode=' + mode);
    tradesRaw = resp && resp.data ? resp.data : [];
  } catch(e) { tradesRaw = []; }

  // Filter trades by date range
  var filtered = tradesRaw.filter(function(t) {
    if (!t.exitTime) return false;
    var d = t.exitTime.slice(0, 10);
    if (r.from && d < r.from) return false;
    if (r.to && d > r.to) return false;
    return true;
  });

  // Compute attribution client-side
  attrData = computeAttribution(filtered);
  renderKPIs();
  renderStrategyTable();
  renderSymbolTable();
  renderTradesTable(filtered);
  renderPeriodCharts();
}

function computeAttribution(trades) {
  trades.sort(function(a, b) { return (a.exitTime || '').localeCompare(b.exitTime || ''); });

  var totalNet = 0, totalGross = 0, totalComm = 0;
  var wins = [], losses = [];
  for (var i = 0; i < trades.length; i++) {
    var t = trades[i];
    totalNet += t.netPnl || 0;
    totalGross += t.grossPnl || 0;
    totalComm += t.commission || 0;
    if ((t.netPnl || 0) > 0) wins.push(t);
    else losses.push(t);
  }

  var winAmt = wins.reduce(function(s, t) { return s + t.netPnl; }, 0);
  var lossAmt = Math.abs(losses.reduce(function(s, t) { return s + t.netPnl; }, 0));

  // By strategy
  var stratMap = {};
  for (var i = 0; i < trades.length; i++) {
    var t = trades[i];
    var sid = t.strategyId || 'unknown';
    if (!stratMap[sid]) stratMap[sid] = { trades: [], wins: 0, losses: 0, grossPnl: 0, netPnl: 0 };
    var s = stratMap[sid];
    s.trades.push(t);
    if ((t.netPnl || 0) > 0) s.wins++;
    else s.losses++;
    s.grossPnl += t.grossPnl || 0;
    s.netPnl += t.netPnl || 0;
  }

  var byStrategy = [];
  for (var sid in stratMap) {
    var s = stratMap[sid];
    var sWins = s.trades.filter(function(t) { return t.netPnl > 0; });
    var sLosses = s.trades.filter(function(t) { return t.netPnl <= 0; });
    var sWinTotal = sWins.reduce(function(sum, t) { return sum + t.netPnl; }, 0);
    var sLossTotal = Math.abs(sLosses.reduce(function(sum, t) { return sum + t.netPnl; }, 0));
    byStrategy.push({
      strategyId: sid,
      trades: s.trades.length,
      wins: s.wins,
      losses: s.losses,
      winRate: s.trades.length > 0 ? s.wins / s.trades.length : 0,
      grossPnl: s.grossPnl,
      netPnl: s.netPnl,
      avgWin: sWins.length > 0 ? sWinTotal / sWins.length : 0,
      avgLoss: sLosses.length > 0 ? sLossTotal / sLosses.length : 0,
      largestWin: sWins.length > 0 ? Math.max.apply(null, sWins.map(function(t) { return t.netPnl; })) : 0,
      largestLoss: sLosses.length > 0 ? Math.min.apply(null, sLosses.map(function(t) { return t.netPnl; })) : 0,
      profitFactor: sLossTotal > 0 ? sWinTotal / sLossTotal : (sWinTotal > 0 ? Infinity : 0),
      contributionPct: totalNet !== 0 ? (s.netPnl / Math.abs(totalNet)) * 100 : 0
    });
  }
  byStrategy.sort(function(a, b) { return Math.abs(b.netPnl) - Math.abs(a.netPnl); });

  // By symbol
  var symMap = {};
  for (var i = 0; i < trades.length; i++) {
    var t = trades[i];
    if (!symMap[t.symbol]) symMap[t.symbol] = { trades: [], netPnl: 0, wins: 0, holdingDays: 0 };
    var s = symMap[t.symbol];
    s.trades.push(t);
    s.netPnl += t.netPnl || 0;
    if ((t.netPnl || 0) > 0) s.wins++;
    s.holdingDays += t.holdingDays || 0;
  }
  var bySymbol = [];
  for (var sym in symMap) {
    var s = symMap[sym];
    bySymbol.push({
      symbol: sym,
      name: s.trades[0] && s.trades[0].name || '',
      trades: s.trades.length,
      netPnl: s.netPnl,
      avgPnl: s.trades.length > 0 ? s.netPnl / s.trades.length : 0,
      winRate: s.trades.length > 0 ? s.wins / s.trades.length : 0,
      holdingDaysAvg: s.trades.length > 0 ? s.holdingDays / s.trades.length : 0,
      contributionPct: totalNet !== 0 ? (s.netPnl / Math.abs(totalNet)) * 100 : 0
    });
  }
  bySymbol.sort(function(a, b) { return b.netPnl - a.netPnl; });

  // By period (day/month)
  function buildPeriod(keyFn) {
    var map = {};
    for (var i = 0; i < trades.length; i++) {
      var t = trades[i];
      var key = keyFn(t);
      if (!map[key]) map[key] = { trades: 0, netPnl: 0, wins: 0, losses: 0 };
      map[key].trades++;
      map[key].netPnl += t.netPnl || 0;
      if ((t.netPnl || 0) > 0) map[key].wins++;
      else map[key].losses++;
    }
    var keys = Object.keys(map).sort();
    var cum = 0;
    return keys.map(function(k) {
      cum += map[k].netPnl;
      return { period: k, trades: map[k].trades, netPnl: map[k].netPnl, cumPnl: cum, wins: map[k].wins, losses: map[k].losses };
    });
  }

  var byDay = buildPeriod(function(t) { return (t.exitTime || '').slice(0, 10); });
  var byMonth = buildPeriod(function(t) { return (t.exitTime || '').slice(0, 7); });

  return {
    totalTrades: trades.length,
    totalNetPnl: totalNet,
    totalGrossPnl: totalGross,
    totalCommission: totalComm,
    overallWinRate: trades.length > 0 ? wins.length / trades.length : 0,
    avgWin: wins.length > 0 ? winAmt / wins.length : 0,
    avgLoss: losses.length > 0 ? lossAmt / losses.length : 0,
    profitFactor: lossAmt > 0 ? winAmt / lossAmt : (winAmt > 0 ? Infinity : 0),
    byStrategy: byStrategy,
    bySymbol: bySymbol,
    byDay: byDay,
    byMonth: byMonth
  };
}

// ── Rendering ───────────────────────────────────────────────────

function renderKPIs() {
  if (!attrData || attrData.totalTrades === 0) {
    document.getElementById('kpiRow').innerHTML = '<div class="empty">所选区间内无已了结交易</div>';
    return;
  }
  var a = attrData;
  var pf = a.profitFactor === Infinity ? '∞' : a.profitFactor.toFixed(2);
  document.getElementById('kpiRow').innerHTML =
    kpiHtml(a.totalTrades, '总交易') +
    kpiHtml(colorVal(a.totalNetPnl), '净盈亏 (¥)') +
    kpiHtml(colorVal(a.totalGrossPnl), '毛盈亏 (¥)') +
    kpiHtml('¥' + num(a.totalCommission), '手续费') +
    kpiHtml(pctBadge(a.overallWinRate), '胜率') +
    kpiHtml('¥' + num(a.avgWin), '平均盈利') +
    kpiHtml('¥' + num(a.avgLoss), '平均亏损') +
    kpiHtml(pf, '盈亏比');
}

function renderStrategyTable() {
  if (!attrData || attrData.byStrategy.length === 0) {
    document.getElementById('strategyTable').innerHTML = '<div class="empty">暂无策略数据</div>';
    return;
  }
  var h = '<table><tr><th>策略</th><th>交易数</th><th>胜率</th><th>净盈亏</th><th>平均盈利</th><th>平均亏损</th><th>盈亏比</th><th>最大单笔盈利</th><th>最大单笔亏损</th><th>贡献度</th></tr>';
  for (var i = 0; i < attrData.byStrategy.length; i++) {
    var s = attrData.byStrategy[i];
    var pf = s.profitFactor === Infinity ? '∞' : s.profitFactor.toFixed(2);
    h += '<tr><td><strong>' + esc(s.strategyId) + '</strong></td>' +
      '<td>' + s.trades + ' (' + s.wins + '胜/' + s.losses + '负)</td>' +
      '<td>' + pctBadge(s.winRate) + '</td>' +
      '<td>' + colorVal(s.netPnl) + '</td>' +
      '<td>¥' + num(s.avgWin) + '</td>' +
      '<td>¥' + num(s.avgLoss) + '</td>' +
      '<td>' + pf + '</td>' +
      '<td>' + colorVal(s.largestWin) + '</td>' +
      '<td>' + colorVal(s.largestLoss) + '</td>' +
      '<td>' + s.contributionPct.toFixed(1) + '%' + contributionBar(s.contributionPct) + '</td></tr>';
  }
  document.getElementById('strategyTable').innerHTML = h + '</table>';
}

function renderSymbolTable() {
  if (!attrData || attrData.bySymbol.length === 0) {
    document.getElementById('symbolTable').innerHTML = '<div class="empty">暂无标的数据</div>';
    return;
  }
  var h = '<table><tr><th>代码</th><th>名称</th><th>交易数</th><th>胜率</th><th>净盈亏</th><th>平均盈亏</th><th>平均持仓天数</th><th>贡献度</th></tr>';
  for (var i = 0; i < attrData.bySymbol.length; i++) {
    var s = attrData.bySymbol[i];
    h += '<tr><td><strong>' + esc(s.symbol) + '</strong></td>' +
      '<td>' + esc(s.name) + '</td>' +
      '<td>' + s.trades + '</td>' +
      '<td>' + pctBadge(s.winRate) + '</td>' +
      '<td>' + colorVal(s.netPnl) + '</td>' +
      '<td>' + colorVal(s.avgPnl) + '</td>' +
      '<td>' + s.holdingDaysAvg.toFixed(1) + '</td>' +
      '<td>' + s.contributionPct.toFixed(1) + '%' + contributionBar(s.contributionPct) + '</td></tr>';
  }
  document.getElementById('symbolTable').innerHTML = h + '</table>';
}

function renderTradesTable(trades) {
  if (!trades || trades.length === 0) {
    document.getElementById('tradesTable').innerHTML = '<div class="empty">暂无交易记录</div>';
    return;
  }
  // Show newest first, limit to 100
  var sorted = trades.slice().reverse().slice(0, 100);
  var h = '<div style="font-size:12px;color:var(--text-dim);margin-bottom:8px">显示最近 ' + Math.min(trades.length, 100) + ' 笔 (共 ' + trades.length + ' 笔)</div>';
  h += '<table><tr><th>平仓时间</th><th>代码</th><th>方向</th><th>买入价</th><th>卖出价</th><th>数量</th><th>持仓天数</th><th>毛盈亏</th><th>手续费</th><th>净盈亏</th><th>策略</th></tr>';
  for (var i = 0; i < sorted.length; i++) {
    var t = sorted[i];
    var sideLabel = t.side === 'buy' ? '<span class="badge badge-green">买</span>' : '<span class="badge badge-red">卖</span>';
    h += '<tr><td>' + esc((t.exitTime || '').slice(0, 10)) + '</td>' +
      '<td>' + esc(t.symbol) + '</td>' +
      '<td>' + sideLabel + '</td>' +
      '<td>¥' + num(t.entryPrice) + '</td>' +
      '<td>¥' + num(t.exitPrice) + '</td>' +
      '<td>' + (t.quantity || 0) + '</td>' +
      '<td>' + (t.holdingDays || '-') + '</td>' +
      '<td>' + colorVal(t.grossPnl) + '</td>' +
      '<td>¥' + num(t.commission) + '</td>' +
      '<td>' + colorVal(t.netPnl) + '</td>' +
      '<td>' + esc(t.strategyId || '-') + '</td></tr>';
  }
  document.getElementById('tradesTable').innerHTML = h + '</table>';
}

// ── Charts ──────────────────────────────────────────────────────

function renderPeriodCharts() {
  if (!attrData) return;
  renderBarChart('dailyPnlChart', attrData.byDay, '日');
  renderBarChart('monthlyPnlChart', attrData.byMonth, '月');
}

function renderBarChart(containerId, data, periodLabel) {
  if (!data || data.length === 0) {
    document.getElementById(containerId).innerHTML = '<div class="empty">暂无' + periodLabel + '度数据</div>';
    return;
  }

  var W = 600, H = 170, pad = {t:10,r:10,b:30,l:60};
  var cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;

  var maxAbs = 0;
  for (var i = 0; i < data.length; i++) {
    var abs = Math.abs(data[i].netPnl);
    if (abs > maxAbs) maxAbs = abs;
  }
  if (maxAbs === 0) maxAbs = 1;

  var barW = Math.max(2, Math.min(20, (cw / data.length) * 0.7));
  var gap = (cw - barW * data.length) / Math.max(data.length - 1, 1);
  var midY = pad.t + ch / 2;

  // Grid
  var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">';
  svg += '<line x1="' + pad.l + '" y1="' + midY + '" x2="' + (W - pad.r) + '" y2="' + midY + '" stroke="var(--text-dim)" stroke-width="0.5" stroke-dasharray="3,3"/>';

  // Y axis labels
  svg += '<text x="' + (pad.l - 4) + '" y="' + (pad.t + 10) + '" text-anchor="end" class="chart-label">+¥' + numInt(maxAbs) + '</text>';
  svg += '<text x="' + (pad.l - 4) + '" y="' + (midY + 4) + '" text-anchor="end" class="chart-label">0</text>';
  svg += '<text x="' + (pad.l - 4) + '" y="' + (H - pad.b - 2) + '" text-anchor="end" class="chart-label">-¥' + numInt(maxAbs) + '</text>';

  // Bars
  for (var i = 0; i < data.length; i++) {
    var d = data[i];
    var x = pad.l + i * (barW + gap);
    var barH = (Math.abs(d.netPnl) / maxAbs) * (ch / 2);
    var y = d.netPnl >= 0 ? midY - barH : midY;
    var cls = d.netPnl >= 0 ? 'bar-pos' : 'bar-neg';
    svg += '<rect class="pnl-bar ' + cls + '" x="' + x + '" y="' + y + '" width="' + barW + '" height="' + Math.max(barH, 1) + '"><title>' + esc(d.period) + ': ¥' + Number(d.netPnl).toFixed(2) + ' (' + d.trades + '笔)</title></rect>';

    // X labels (show only some to avoid overlap)
    if (data.length <= 15 || i === 0 || i === data.length - 1 || i === Math.floor(data.length / 2)) {
      var label = d.period.length > 7 ? d.period.slice(5) : d.period;
      svg += '<text x="' + (x + barW / 2) + '" y="' + (H - 4) + '" text-anchor="middle" class="chart-label">' + label + '</text>';
    }
  }

  // Cumulative line
  var maxCum = 0;
  for (var i = 0; i < data.length; i++) {
    var abs = Math.abs(data[i].cumPnl);
    if (abs > maxCum) maxCum = abs;
  }
  if (maxCum > 0) {
    var cumPath = '';
    for (var i = 0; i < data.length; i++) {
      var cx = pad.l + i * (barW + gap) + barW / 2;
      var cy = midY - (data[i].cumPnl / maxCum) * (ch / 2) * 0.9;
      cumPath += (i === 0 ? 'M' : 'L') + cx.toFixed(1) + ',' + cy.toFixed(1);
    }
    var cumColor = data[data.length - 1].cumPnl >= 0 ? 'var(--green)' : 'var(--red)';
    svg += '<path d="' + cumPath + '" stroke="' + cumColor + '" fill="none" stroke-width="1.5" stroke-dasharray="4,2" opacity="0.6"/>';
  }

  svg += '</svg>';
  document.getElementById(containerId).innerHTML = svg;
}

// ── Equity Curve ────────────────────────────────────────────────

async function loadEquityCurve() {
  var mode = getMode();
  var r = getDateRange();
  var url = '/quant/api/equity-history?mode=' + mode;
  if (r.from && r.to) url += '&start=' + r.from + '&end=' + r.to;
  else url += '&limit=365';

  try {
    var resp = await api(url);
    equityData = resp && resp.data ? resp.data : [];
  } catch(e) { equityData = []; }

  if (equityData.length < 2) {
    document.getElementById('equityCurveChart').innerHTML = '<div class="empty">权益数据不足 (需至少2个交易日)</div>';
    document.getElementById('equityBenchmarkStats').innerHTML = '';
    return;
  }

  // Render equity curve
  var chartData = equityData.map(function(h) { return { x: h.date, y: h.equity }; });
  renderLineChart('equityCurveChart', chartData, null);

  // Load benchmark if selected
  var benchId = document.getElementById('benchmarkSelect').value;
  if (benchId) {
    try {
      var cmp = await api('/quant/api/benchmark/compare?benchmark=' + benchId + '&mode=' + mode);
      if (cmp && cmp.status === 'ok' && cmp.data) {
        var firstEq = equityData[0].equity;
        var benchData = cmp.data.data.map(function(d) { return { x: d.date, y: firstEq * d.normalizedReturn }; });
        renderLineChart('equityCurveChart', chartData, benchData);
        var s = cmp.data.stats;
        var exCls = s.excessReturn > 0 ? 'positive' : s.excessReturn < 0 ? 'negative' : '';
        document.getElementById('equityBenchmarkStats').innerHTML =
          '策略: <span class="' + (s.strategyReturnPct >= 0 ? 'positive' : 'negative') + '">' + num(s.strategyReturnPct) + '%</span> · ' +
          cmp.data.benchmark.name + ': <span class="' + (s.benchmarkReturnPct >= 0 ? 'positive' : 'negative') + '">' + num(s.benchmarkReturnPct) + '%</span> · ' +
          '超额: <span class="' + exCls + '">' + num(s.excessReturn) + '%</span> · ' +
          'IR: ' + s.informationRatio.toFixed(2);
      }
    } catch(e) {
      document.getElementById('equityBenchmarkStats').innerHTML = '<span style="color:var(--text-dim)">基准数据不可用</span>';
    }
  } else {
    document.getElementById('equityBenchmarkStats').innerHTML = '';
  }
}

function renderLineChart(containerId, data, overlay) {
  if (!data || data.length < 2) return;
  var W = 600, H = 180, pad = {t:10,r:10,b:25,l:60};
  var cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;

  var allY = data.map(function(d) { return d.y; });
  if (overlay) for (var i = 0; i < overlay.length; i++) allY.push(overlay[i].y);
  var yMin = Math.min.apply(null, allY), yMax = Math.max.apply(null, allY);
  var yRange = yMax - yMin || 1;
  var xStep = cw / Math.max(data.length - 1, 1);

  function buildPath(points, step) {
    var p = '', a = '';
    for (var i = 0; i < points.length; i++) {
      var x = pad.l + i * step;
      var y = pad.t + ch - ((points[i].y - yMin) / yRange) * ch;
      p += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
      a += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
    }
    a += 'L' + (pad.l + (points.length - 1) * step) + ',' + (pad.t + ch) + 'L' + pad.l + ',' + (pad.t + ch) + 'Z';
    return {path: p, area: a};
  }

  var color = data[data.length - 1].y >= data[0].y ? 'var(--green)' : 'var(--red)';
  var built = buildPath(data, xStep);

  // Grid
  var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">';
  for (var i = 0; i <= 3; i++) {
    var gy = pad.t + (ch / 3) * i;
    var val = yMax - (yRange / 3) * i;
    svg += '<line x1="' + pad.l + '" y1="' + gy + '" x2="' + (W - pad.r) + '" y2="' + gy + '" class="chart-grid"/>';
    svg += '<text x="' + (pad.l - 4) + '" y="' + (gy + 4) + '" text-anchor="end" class="chart-label">¥' + num(val) + '</text>';
  }

  // X labels
  var xl = [0, Math.floor(data.length / 2), data.length - 1];
  for (var j = 0; j < xl.length; j++) {
    var idx = xl[j];
    if (data[idx]) {
      var xpos = pad.l + idx * xStep;
      svg += '<text x="' + xpos + '" y="' + (H - 4) + '" text-anchor="middle" class="chart-label">' + data[idx].x + '</text>';
    }
  }

  svg += '<path d="' + built.area + '" fill="' + color + '" class="chart-area"/>';
  svg += '<path d="' + built.path + '" stroke="' + color + '" class="chart-line"/>';

  // Overlay (benchmark)
  if (overlay && overlay.length >= 2) {
    var oStep = cw / Math.max(overlay.length - 1, 1);
    var op = '';
    for (var i = 0; i < overlay.length; i++) {
      var x = pad.l + i * oStep;
      var y = pad.t + ch - ((overlay[i].y - yMin) / yRange) * ch;
      op += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
    }
    svg += '<path d="' + op + '" stroke="var(--accent)" fill="none" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.7"/>';
  }

  // Legend
  svg += '<text x="' + (W - pad.r) + '" y="' + (pad.t + 10) + '" text-anchor="end" class="chart-label" fill="' + color + '">策略</text>';
  if (overlay && overlay.length) {
    svg += '<text x="' + (W - pad.r) + '" y="' + (pad.t + 22) + '" text-anchor="end" class="chart-label" fill="var(--accent)">基准</text>';
  }

  svg += '</svg>';
  document.getElementById(containerId).innerHTML = svg;
}

// ── Equity Summary ──────────────────────────────────────────────

async function loadEquitySummary() {
  var mode = getMode();
  try {
    var resp = await api('/quant/api/equity-history/summary?mode=' + mode);
    equitySummary = resp && resp.data ? resp.data : null;
  } catch(e) { equitySummary = null; }

  var el = document.getElementById('equitySummarySection');
  if (!equitySummary || equitySummary.totalDays === 0) {
    el.innerHTML = '<div class="empty">暂无权益历史数据</div>';
    return;
  }
  var s = equitySummary;
  el.innerHTML =
    '<div class="kpi-row">' +
    kpiHtml(s.totalDays, '交易天数') +
    kpiHtml('¥' + num(s.startEquity), '初始权益') +
    kpiHtml('¥' + num(s.endEquity), '最终权益') +
    kpiHtml(colorVal(s.totalReturnPct) + '%', '总收益率') +
    kpiHtml(colorVal(-s.maxDrawdownPct) + '%', '最大回撤') +
    kpiHtml(s.winDays + '/' + s.lossDays, '盈利天/亏损天') +
    kpiHtml(num(s.winRate) + '%', '日胜率') +
    kpiHtml('¥' + num(s.avgDailyPnl), '日均盈亏') +
    '</div>' +
    (s.bestDay ? '<div style="font-size:12px;color:var(--text-dim);margin-top:8px">最佳单日: ' + s.bestDay.date + ' <span class="positive">+¥' + num(s.bestDay.pnl) + '</span> · 最差单日: ' + (s.worstDay ? s.worstDay.date + ' <span class="negative">¥' + num(s.worstDay.pnl) + '</span>' : '-') + '</div>' : '');
}

// ── Signal Hit Rate ─────────────────────────────────────────────

async function loadHitRate() {
  try {
    // Signal hit rate is not mode-specific; query global
    var resp = await api('/quant/api/signals?limit=200');
    var signals = resp && resp.data ? resp.data : [];
    // Show basic stats if available
    if (signals.length === 0) {
      document.getElementById('hitrateTable').innerHTML = '<div class="empty">暂无信号数据</div>';
      return;
    }

    // Group by strategy
    var stratMap = {};
    for (var i = 0; i < signals.length; i++) {
      var s = signals[i];
      var sid = s.strategyId || s.strategy || 'unknown';
      if (!stratMap[sid]) stratMap[sid] = { total: 0, buy: 0, sell: 0 };
      stratMap[sid].total++;
      if (s.direction === 'buy') stratMap[sid].buy++;
      else if (s.direction === 'sell') stratMap[sid].sell++;
    }

    var h = '<table><tr><th>策略</th><th>总信号</th><th>买入</th><th>卖出</th></tr>';
    for (var sid in stratMap) {
      var st = stratMap[sid];
      h += '<tr><td>' + esc(sid) + '</td><td>' + st.total + '</td><td>' + st.buy + '</td><td>' + st.sell + '</td></tr>';
    }
    document.getElementById('hitrateTable').innerHTML = h + '</table>';
  } catch(e) {
    document.getElementById('hitrateTable').innerHTML = '<div class="empty">信号数据加载失败</div>';
  }
}

// ── Init ────────────────────────────────────────────────────────

// Set default date range
(function() {
  var now = new Date();
  var to = now.toISOString().slice(0, 10);
  document.getElementById('dateTo').value = to;
  var from = new Date(now.getTime() - 30 * 86400000);
  document.getElementById('dateFrom').value = from.toISOString().slice(0, 10);
})();

reload();
`;
}

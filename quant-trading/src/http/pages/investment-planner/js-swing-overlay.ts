/** Investment Planner — Swing Overlay (持仓波段套利) UI module */
export function getSwingOverlayJs(): string {
  return `
/* ═══ Swing Overlay (持仓波段套利) ═══ */

var _swingScanResults = [];
var _swingActiveOverlays = {};  // symbol → {params, gridOrders, stats}

function scanSwingSuitability() {
  var el = document.getElementById('swing-scan-results');
  if (!el) return;
  el.innerHTML = '<div style="text-align:center;padding:30px"><div class="spinner"></div><br>正在分析持仓波段适合度…</div>';

  // Get portfolio symbols
  var symbols = [];
  if (currentPlan && currentPlan.portfolio) {
    currentPlan.portfolio.forEach(function(p) { if (p.symbol) symbols.push(p.symbol); });
  }
  if (!symbols.length) {
    el.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-dim)">⚠️ 暂无持仓数据。请先在"制定计划"中生成投资计划。</div>';
    return;
  }

  var mode = window._currentViewMode || 'paper';
  api('/quant/admin/api/swing-overlay/scan', {
    method: 'POST',
    body: JSON.stringify({ symbols: symbols, lookback_days: 60, mode: mode }),
    _timeout: 60000
  }).then(function(d) {
    if (!d || !d.results) {
      el.innerHTML = '<div style="padding:20px;color:var(--text-dim)">扫描完成，无适合波段操作的股票。</div>';
      return;
    }
    _swingScanResults = d.results;
    renderSwingScanResults(d.results);
  }).catch(function(e) {
    el.innerHTML = '<div style="padding:20px;color:#e74c3c">⚠️ 扫描失败: ' + esc(String(e)) + '</div>';
  });
}

function renderSwingScanResults(results) {
  var el = document.getElementById('swing-scan-results');
  if (!el) return;
  if (!results || !results.length) {
    el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-dim)">暂无适合波段操作的持仓股票</div>';
    return;
  }

  var html = '<table class="data-table"><thead><tr>' +
    '<th>排名</th><th>代码</th><th>名称</th><th>综合得分</th>' +
    '<th>趋势</th><th>振荡</th><th>均值回归</th><th>振幅</th><th>流动性</th>' +
    '<th>建议网格%</th><th>建议核心比</th><th>操作</th></tr></thead><tbody>';

  results.forEach(function(r, idx) {
    var f = r.factors || {};
    var isActive = !!_swingActiveOverlays[r.stock_id];
    var scoreColor = r.score >= 60 ? '#27ae60' : r.score >= 40 ? '#f39c12' : '#95a5a6';
    html += '<tr>' +
      '<td>' + (idx + 1) + '</td>' +
      '<td style="font-family:monospace">' + esc(r.stock_id) + '</td>' +
      '<td>' + esc(r.name || '') + '</td>' +
      '<td style="font-weight:bold;color:' + scoreColor + '">' + (r.score || 0).toFixed(1) + '</td>' +
      '<td>' + (f.trend_score || 0).toFixed(1) + '</td>' +
      '<td>' + (f.oscillation_score || 0).toFixed(1) + '</td>' +
      '<td>' + (f.mean_reversion_score || 0).toFixed(1) + '</td>' +
      '<td>' + (f.amplitude_score || 0).toFixed(1) + '</td>' +
      '<td>' + (f.liquidity_score || 0).toFixed(1) + '</td>' +
      '<td>' + (f.recommended_grid_pct || '-') + '%</td>' +
      '<td>' + ((f.recommended_core_ratio || 0) * 100).toFixed(0) + '%</td>' +
      '<td>' + (isActive
        ? '<button class="btn btn-sm btn-danger" onclick="deactivateSwingOverlay(\\'' + esc(r.stock_id) + '\\')">⏹ 停止</button>'
        : '<button class="btn btn-sm btn-primary" onclick="showSwingConfig(\\'' + esc(r.stock_id) + '\\',' + idx + ')" ' + (r.score < 30 ? 'disabled title="得分过低不建议波段"' : '') + '>💹 激活</button>'
      ) + '</td></tr>';
  });
  html += '</tbody></table>';

  // Signals summary
  var signalSummary = '';
  results.forEach(function(r) {
    if (r.signals && r.signals.length) {
      signalSummary += '<div style="margin:4px 0"><strong>' + esc(r.stock_id) + '</strong>: ' + r.signals.map(function(s){return esc(s)}).join(' | ') + '</div>';
    }
  });
  if (signalSummary) {
    html += '<details style="margin-top:12px"><summary style="cursor:pointer;color:var(--text-dim)">📊 详细信号</summary><div style="padding:8px;font-size:12px;line-height:1.8">' + signalSummary + '</div></details>';
  }
  el.innerHTML = html;
}

function showSwingConfig(symbol, idx) {
  var r = _swingScanResults[idx];
  if (!r) return;
  var f = r.factors || {};

  var gridPct = f.recommended_grid_pct || 3.0;
  var coreRatio = f.recommended_core_ratio || 0.6;
  var biasBuy = f.recommended_bias_buy || -5.0;
  var biasSell = f.recommended_bias_sell || 3.5;

  var configHtml = '<div class="card" style="margin-top:12px;padding:16px" id="swing-config-panel">' +
    '<h4>💹 波段配置 — ' + esc(symbol) + ' ' + esc(r.name || '') + '</h4>' +
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px">' +
    '<label>核心仓位比例 (%)<br><input type="number" id="swing-core-ratio" value="' + (coreRatio * 100).toFixed(0) + '" min="30" max="90" step="5" style="width:100%"></label>' +
    '<label>网格间距 (%)<br><input type="number" id="swing-grid-pct" value="' + gridPct.toFixed(1) + '" min="1.5" max="8" step="0.5" style="width:100%"></label>' +
    '<label>单侧网格数<br><input type="number" id="swing-grid-count" value="3" min="1" max="6" step="1" style="width:100%"></label>' +
    '<label>买入BIAS阈值 (%)<br><input type="number" id="swing-bias-buy" value="' + biasBuy.toFixed(1) + '" min="-12" max="-2" step="0.5" style="width:100%"></label>' +
    '<label>卖出BIAS阈值 (%)<br><input type="number" id="swing-bias-sell" value="' + biasSell.toFixed(1) + '" min="1" max="8" step="0.5" style="width:100%"></label>' +
    '<label>放量确认倍数<br><input type="number" id="swing-vol-mult" value="1.5" min="1.0" max="4.0" step="0.1" style="width:100%"></label>' +
    '</div>' +
    '<div style="margin-top:8px;font-size:12px;color:var(--text-dim)">' +
    '⚠️ A股手续费约0.1%/往返，网格间距建议 ≥ 1.5% 以确保扣费后有利润。' +
    '</div>' +
    '<div style="margin-top:12px;display:flex;gap:8px">' +
    '<button class="btn btn-primary" onclick="activateSwingOverlay(\\'' + esc(symbol) + '\\')">✅ 激活波段监控</button>' +
    '<button class="btn" onclick="document.getElementById(\\'swing-config-panel\\').remove()">取消</button>' +
    '</div></div>';

  var existing = document.getElementById('swing-config-panel');
  if (existing) existing.remove();
  var container = document.getElementById('swing-scan-results');
  if (container) container.insertAdjacentHTML('afterend', configHtml);
}

function activateSwingOverlay(symbol) {
  var coreRatio = parseFloat(document.getElementById('swing-core-ratio').value) / 100;
  var gridPct = parseFloat(document.getElementById('swing-grid-pct').value);
  var gridCount = parseInt(document.getElementById('swing-grid-count').value, 10);
  var biasBuy = parseFloat(document.getElementById('swing-bias-buy').value);
  var biasSell = parseFloat(document.getElementById('swing-bias-sell').value);
  var volMult = parseFloat(document.getElementById('swing-vol-mult').value);
  var mode = window._currentViewMode || 'paper';

  if (gridPct < 1.5) {
    toast('⚠️ 网格间距 < 1.5%，扣除手续费后可能无利润！', 'warning');
  }

  var btn = event.target;
  btn.disabled = true;
  btn.textContent = '激活中…';

  api('/quant/admin/api/swing-overlay/activate', {
    method: 'POST',
    body: JSON.stringify({
      symbol: symbol,
      mode: mode,
      params: {
        core_ratio: coreRatio,
        grid_pct: gridPct,
        grid_count: gridCount,
        bias_buy: biasBuy,
        bias_sell: biasSell,
        vol_mult: volMult
      }
    }),
    _timeout: 15000
  }).then(function(d) {
    if (d && d.ok) {
      _swingActiveOverlays[symbol] = {
        params: { coreRatio: coreRatio, gridPct: gridPct, gridCount: gridCount, biasBuy: biasBuy, biasSell: biasSell },
        gridOrders: d.grid_orders || [],
        activatedAt: new Date().toISOString()
      };
      toast('✅ ' + symbol + ' 波段监控已激活，创建 ' + (d.grid_orders ? d.grid_orders.length : 0) + ' 个网格条件单', 'success');
      var configPanel = document.getElementById('swing-config-panel');
      if (configPanel) configPanel.remove();
      refreshSwingDashboard();
      if (_swingScanResults.length) renderSwingScanResults(_swingScanResults);
    } else {
      toast('⚠️ 激活失败: ' + (d && d.error ? d.error : '未知错误'), 'error');
      btn.disabled = false;
      btn.textContent = '💹 激活';
    }
  }).catch(function(e) {
    toast('⚠️ 激活失败: ' + String(e), 'error');
    btn.disabled = false;
    btn.textContent = '💹 激活';
  });
}

function deactivateSwingOverlay(symbol) {
  if (!confirm('确认停止 ' + symbol + ' 的波段监控？\\n将取消所有未触发的网格条件单。')) return;

  api('/quant/admin/api/swing-overlay/deactivate', {
    method: 'POST',
    body: JSON.stringify({ symbol: symbol, mode: window._currentViewMode || 'paper' }),
    _timeout: 10000
  }).then(function(d) {
    if (d && d.ok) {
      delete _swingActiveOverlays[symbol];
      toast('⏹ ' + symbol + ' 波段监控已停止，取消 ' + (d.removed_count || 0) + ' 个条件单', 'info');
      refreshSwingDashboard();
      if (_swingScanResults.length) renderSwingScanResults(_swingScanResults);
    }
  }).catch(function(e) {
    toast('停止失败: ' + String(e), 'error');
  });
}

function refreshSwingDashboard() {
  var el = document.getElementById('swing-active-dashboard');
  if (!el) return;

  var mode = window._currentViewMode || 'paper';
  api('/quant/admin/api/swing-overlay/status?mode=' + mode, { _timeout: 10000 }).then(function(d) {
    if (!d || !d.overlays || !d.overlays.length) {
      el.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-dim)">暂无激活的波段监控</div>';
      return;
    }

    // Update local state
    d.overlays.forEach(function(o) {
      _swingActiveOverlays[o.symbol] = o;
    });

    var totalSwingPnl = 0;
    var totalRoundTrips = 0;

    var html = '<table class="data-table"><thead><tr>' +
      '<th>代码</th><th>名称</th><th>当前价</th><th>MA中心</th><th>BIAS</th>' +
      '<th>核心仓</th><th>波段仓</th><th>网格单</th><th>往返次数</th><th>波段损益</th><th>操作</th>' +
      '</tr></thead><tbody>';

    d.overlays.forEach(function(o) {
      var pnlColor = (o.swing_pnl || 0) >= 0 ? '#e74c3c' : '#27ae60';
      totalSwingPnl += (o.swing_pnl || 0);
      totalRoundTrips += (o.round_trips || 0);

      html += '<tr>' +
        '<td style="font-family:monospace">' + esc(o.symbol) + '</td>' +
        '<td>' + esc(o.name || '') + '</td>' +
        '<td>¥' + (o.current_price || 0).toFixed(2) + '</td>' +
        '<td>¥' + (o.ma_center || 0).toFixed(2) + '</td>' +
        '<td style="color:' + ((o.bias || 0) < 0 ? '#27ae60' : '#e74c3c') + '">' + (o.bias || 0).toFixed(2) + '%</td>' +
        '<td>' + (o.core_shares || 0) + '股</td>' +
        '<td>' + (o.swing_shares || 0) + '/' + (o.swing_total || 0) + '股</td>' +
        '<td>' + (o.active_grid_orders || 0) + '个</td>' +
        '<td>' + (o.round_trips || 0) + '次</td>' +
        '<td style="color:' + pnlColor + '">' + ((o.swing_pnl || 0) >= 0 ? '+' : '') + (o.swing_pnl || 0).toFixed(2) + '%</td>' +
        '<td><button class="btn btn-sm btn-danger" onclick="deactivateSwingOverlay(\\'' + esc(o.symbol) + '\\')">⏹ 停止</button></td>' +
        '</tr>';

      // Grid visualization
      if (o.grid_levels && o.grid_levels.length) {
        html += '<tr><td colspan="11" style="padding:4px 12px;background:var(--bg-card)">' +
          renderGridVisualization(o) + '</td></tr>';
      }
    });

    html += '</tbody></table>';

    // Summary KPIs
    var kpi = '<div class="kpi-row" style="margin-bottom:12px">' +
      '<div class="kpi"><div class="kpi-value">' + d.overlays.length + '</div><div class="kpi-label">监控股票</div></div>' +
      '<div class="kpi"><div class="kpi-value">' + totalRoundTrips + '</div><div class="kpi-label">累计往返</div></div>' +
      '<div class="kpi"><div class="kpi-value" style="color:' + (totalSwingPnl >= 0 ? '#e74c3c' : '#27ae60') + '">' +
        (totalSwingPnl >= 0 ? '+' : '') + totalSwingPnl.toFixed(2) + '%</div><div class="kpi-label">总波段损益</div></div>' +
      '</div>';

    el.innerHTML = kpi + html;
  }).catch(function(e) {
    el.innerHTML = '<div style="padding:20px;color:#e74c3c">⚠️ 加载失败: ' + esc(String(e)) + '</div>';
  });
}

function renderGridVisualization(overlay) {
  if (!overlay.grid_levels || !overlay.grid_levels.length) return '';
  var levels = overlay.grid_levels;
  var price = overlay.current_price || 0;
  var center = overlay.ma_center || levels[Math.floor(levels.length / 2)];
  var minP = levels[0] * 0.995;
  var maxP = levels[levels.length - 1] * 1.005;
  var range = maxP - minP;
  if (range <= 0) return '';

  var filledGrids = overlay.filled_grids || {};
  var html = '<div style="position:relative;height:32px;background:linear-gradient(90deg,#1a472a,#2d2d2d 40%,#2d2d2d 60%,#4a1a1a);border-radius:4px;margin:4px 0;overflow:hidden">';

  // Grid level markers
  levels.forEach(function(l, i) {
    var pct = ((l - minP) / range * 100).toFixed(1);
    var centerIdx = Math.floor(levels.length / 2);
    var color = i < centerIdx ? '#27ae60' : i > centerIdx ? '#e74c3c' : '#f39c12';
    var filled = filledGrids[i];
    html += '<div style="position:absolute;left:' + pct + '%;top:0;bottom:0;width:2px;background:' + color + ';opacity:' + (filled ? '1' : '0.4') + '" title="第' + (i - centerIdx) + '层 ¥' + l.toFixed(2) + (filled ? ' (已触发)' : '') + '"></div>';
  });

  // Current price marker
  var pricePct = ((price - minP) / range * 100).toFixed(1);
  html += '<div style="position:absolute;left:' + pricePct + '%;top:2px;bottom:2px;width:3px;background:#fff;border-radius:2px;z-index:2" title="当前价 ¥' + price.toFixed(2) + '"></div>';
  html += '<div style="position:absolute;left:' + pricePct + '%;top:50%;transform:translate(-50%,-50%);font-size:10px;color:#fff;text-shadow:0 1px 3px rgba(0,0,0,0.8);z-index:3;white-space:nowrap">¥' + price.toFixed(2) + '</div>';

  // Center MA marker
  var maPct = ((center - minP) / range * 100).toFixed(1);
  html += '<div style="position:absolute;left:' + maPct + '%;top:0;bottom:0;width:1px;background:#f39c12;opacity:0.6" title="MA中心 ¥' + center.toFixed(2) + '"></div>';

  html += '</div>';
  html += '<div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-dim)"><span>¥' + levels[0].toFixed(2) + '</span><span>MA ¥' + center.toFixed(2) + '</span><span>¥' + levels[levels.length - 1].toFixed(2) + '</span></div>';
  return html;
}

/* Auto-refresh swing dashboard when tab activates */
if (typeof window._onTabActivated === 'function') {
  var _origTabCb = window._onTabActivated;
  window._onTabActivated = function(name) {
    _origTabCb(name);
    if (name === 'swing-overlay') refreshSwingDashboard();
  };
} else {
  window._onTabActivated = function(name) {
    if (name === 'swing-overlay') refreshSwingDashboard();
  };
}
`;
}

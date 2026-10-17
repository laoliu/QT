/**
 * Risk Dashboard Page — 风险管理面板
 *
 * Displays portfolio risk metrics, position concentration, drawdown analysis.
 * Data fetched from existing portfolio/dashboard APIs.
 */

export function getRiskDashboardHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>风险管理 — Quant Trading 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:18px;font-weight:600}
.container{max-width:1200px;margin:0 auto;padding:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-top:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px}
.card h2{font-size:15px;margin-bottom:12px;color:var(--accent)}
.loading{color:var(--text-dim);font-style:italic}
.kpi-row{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.kpi-box{background:var(--bg3);border-radius:6px;padding:12px 16px;flex:1;min-width:140px}
.kpi-label{font-size:12px;color:var(--text-dim)}
.kpi-val{font-size:22px;font-weight:700;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500}
.error{color:var(--red);font-size:13px}
</style>
</head>
<body>
<header>
  <h1>🛡️ 风险管理</h1>
  <a href="${b}/quant/">← 返回首页</a>
</header>
<div class="container">
  <div id="kpi" class="kpi-row"><div class="loading">加载中…</div></div>
  <div class="grid">
    <div class="card">
      <h2>📊 持仓集中度</h2>
      <div id="concentration" class="loading">加载中…</div>
    </div>
    <div class="card">
      <h2>📉 回撤分析</h2>
      <div id="drawdown" class="loading">加载中…</div>
    </div>
  </div>
  <div class="card" style="margin-top:16px">
    <h2>🛡️ 实时风控覆盖 (MDD梯度 + 波动率缩放)</h2>
    <div id="risk-overlay" class="loading">加载中…</div>
  </div>
</div>
<script>
var B="${b}";
// Fetch portfolio data and equity summary in parallel
Promise.all([
  fetch(B+"/quant/api/portfolio").then(function(r){return r.json()}).catch(function(){return {}}),
  fetch(B+"/quant/api/equity-history/summary").then(function(r){return r.json()}).catch(function(){return {}})
]).then(function(results){
  var portRes=results[0];
  var eqRes=results[1];
  var el=document.getElementById("kpi");
  var pf=(portRes&&portRes.data)||portRes||{};
  var positions=pf.positions||[];
  var summary=(eqRes&&eqRes.data)||{};

  // Calculate totals from positions
  var totalVal=0;var todayPnl=0;
  if(pf.totalAssets!=null){totalVal=pf.totalAssets}
  else if(pf.totalValue!=null){totalVal=pf.totalValue}
  else if(pf.total_value!=null){totalVal=pf.total_value}
  else{
    var cash=Number(pf.cash||pf.available||0);
    totalVal=positions.reduce(function(s,p){
      return s+(p.marketValue||p.market_value||(p.currentPrice||p.lastPrice||0)*(p.quantity||p.shares||0));
    },cash);
  }
  if(pf.todayPnl!=null){todayPnl=pf.todayPnl}
  else if(pf.today_pnl!=null){todayPnl=pf.today_pnl}
  else if(pf.profit!=null){todayPnl=pf.profit}
  else{
    todayPnl=positions.reduce(function(s,p){return s+(p.todayPnl||p.today_pnl||0)},0);
  }

  // Max drawdown from equity history summary
  var maxDD=0;
  if(summary.maxDrawdownPct!=null){maxDD=summary.maxDrawdownPct/100}
  else if(summary.maxDrawdown!=null){maxDD=summary.maxDrawdown}
  else if(pf.maxDrawdown!=null){maxDD=pf.maxDrawdown}

  el.innerHTML='<div class="kpi-box"><div class="kpi-label">总资产</div><div class="kpi-val">¥'+Number(totalVal).toLocaleString()+'</div></div>'+
    '<div class="kpi-box"><div class="kpi-label">今日盈亏</div><div class="kpi-val" style="color:'+(todayPnl>=0?"var(--green)":"var(--red)")+'">'+(todayPnl>=0?"+":"")+Number(todayPnl).toFixed(2)+'</div></div>'+
    '<div class="kpi-box"><div class="kpi-label">最大回撤</div><div class="kpi-val" style="color:var(--red)">'+Number(maxDD*100).toFixed(2)+'%</div></div>';

  // Concentration
  var concEl=document.getElementById("concentration");
  if(!positions.length){concEl.innerHTML='暂无持仓';return;}
  var totalMV=positions.reduce(function(s,p){
    return s+(p.marketValue||p.market_value||(p.currentPrice||p.lastPrice||0)*(p.quantity||p.shares||0));
  },0);
  positions.sort(function(a,b){
    var mvA=a.marketValue||a.market_value||(a.currentPrice||a.lastPrice||0)*(a.quantity||a.shares||0);
    var mvB=b.marketValue||b.market_value||(b.currentPrice||b.lastPrice||0)*(b.quantity||b.shares||0);
    return mvB-mvA;
  });
  // Resolve stock names then render
  var syms=positions.map(function(p){return p.symbol||p.code}).filter(Boolean);
  fetch(B+"/quant/api/stock-names?symbols="+syms.join(",")).then(function(r){return r.json()}).catch(function(){return {}}).then(function(nr){
    var nameMap=(nr&&nr.data)||{};
    var html='<table><thead><tr><th>标的</th><th>市值</th><th>占比</th></tr></thead><tbody>';
    positions.forEach(function(p){
      var code=p.symbol||p.code;
      var label=code;
      if(nameMap[code]){label=code+' <span style="color:var(--text-dim)">'+nameMap[code]+'</span>'}
      var mv=p.marketValue||p.market_value||(p.currentPrice||p.lastPrice||0)*(p.quantity||p.shares||0);
      var pct=totalMV>0?(mv/totalMV*100):0;
      html+='<tr><td>'+label+'</td><td>¥'+Number(mv).toLocaleString()+'</td><td>'+pct.toFixed(1)+'%</td></tr>';
    });
    html+='</tbody></table>';
    concEl.innerHTML=html;
  });

  // Drawdown analysis from equity history
  var ddEl=document.getElementById("drawdown");
  if(summary.maxDrawdownPct!=null&&summary.totalDays>0){
    ddEl.innerHTML='<table><thead><tr><th>指标</th><th>数值</th></tr></thead><tbody>'+
      '<tr><td>最大回撤</td><td style="color:var(--red)">-'+Number(summary.maxDrawdownPct).toFixed(2)+'%</td></tr>'+
      (summary.totalReturnPct!=null?'<tr><td>总收益率</td><td style="color:'+(summary.totalReturnPct>=0?'var(--green)':'var(--red)')+'">'+Number(summary.totalReturnPct).toFixed(2)+'%</td></tr>':'')+
      (summary.winRate!=null?'<tr><td>日胜率</td><td>'+Number(summary.winRate).toFixed(1)+'%</td></tr>':'')+
      '<tr><td>记录天数</td><td>'+summary.totalDays+'天</td></tr>'+
      (summary.startEquity!=null?'<tr><td>起始权益</td><td>¥'+Number(summary.startEquity).toLocaleString()+'</td></tr>':'')+
      (summary.endEquity!=null?'<tr><td>当前权益</td><td>¥'+Number(summary.endEquity).toLocaleString()+'</td></tr>':'')+
      '</tbody></table>';
  }else{
    ddEl.innerHTML='<p style="color:var(--text-dim)">回撤曲线需要权益历史数据，请在 Dashboard 中查看详细图表。</p>';
  }
}).catch(function(e){
  document.getElementById("kpi").innerHTML='<div class="error">'+e+'</div>';
});

// ── Risk Overlay Status ──────────────────────────────────────────
fetch(B+"/quant/admin/api/risk-overlay/status").then(function(r){return r.json()}).then(function(res){
  var el=document.getElementById("risk-overlay");
  if(!res||!res.ok||!res.data){el.innerHTML='<p style="color:var(--text-dim)">风控覆盖服务未就绪</p>';return}
  var d=res.data;
  var stateColors={normal:'var(--green)',l1_reduced:'var(--yellow)',l2_liquidated:'var(--red)',recovering:'var(--accent)'};
  var stateLabels={normal:'正常',l1_reduced:'L1 减仓',l2_liquidated:'L2 清仓',recovering:'恢复中'};
  var modeLabels={both:'MDD+Vol',mdd_only:'仅MDD',vol_only:'仅Vol',disabled:'已禁用'};
  var scaleColor=d.combinedScale>=0.9?'var(--green)':d.combinedScale>=0.5?'var(--yellow)':'var(--red)';

  var html='<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px">';
  html+='<div class="kpi-box"><div class="kpi-label">启用状态</div><div class="kpi-val" style="font-size:16px;color:'+(d.enabled?'var(--green)':'var(--text-dim)')+'">'+(d.enabled?'✅ 已启用':'⏸️ 未启用')+'</div></div>';
  html+='<div class="kpi-box"><div class="kpi-label">模式</div><div class="kpi-val" style="font-size:16px">'+(modeLabels[d.mode]||d.mode)+'</div></div>';
  html+='<div class="kpi-box"><div class="kpi-label">MDD状态</div><div class="kpi-val" style="font-size:16px;color:'+(stateColors[d.mddState]||'var(--text)')+'">'+(stateLabels[d.mddState]||d.mddState)+'</div></div>';
  html+='<div class="kpi-box"><div class="kpi-label">当前回撤</div><div class="kpi-val" style="font-size:16px;color:var(--red)">'+(d.currentDrawdown*100).toFixed(2)+'%</div></div>';
  html+='<div class="kpi-box"><div class="kpi-label">综合缩放因子</div><div class="kpi-val" style="font-size:16px;color:'+scaleColor+'">'+(d.combinedScale*100).toFixed(1)+'%</div></div>';
  html+='<div class="kpi-box"><div class="kpi-label">实际波动率</div><div class="kpi-val" style="font-size:16px">'+(d.realizedVol*100).toFixed(2)+'%</div></div>';
  html+='<div class="kpi-box"><div class="kpi-label">峰值NAV</div><div class="kpi-val" style="font-size:16px">¥'+Number(d.peakNav).toLocaleString()+'</div></div>';
  html+='<div class="kpi-box"><div class="kpi-label">数据点</div><div class="kpi-val" style="font-size:16px">'+d.dataPoints+'</div></div>';
  html+='</div>';

  // Enable/disable toggle + config link
  html+='<div style="display:flex;gap:8px;align-items:center;margin-bottom:12px">';
  html+='<button onclick="toggleRiskOverlay()" style="padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:'+(d.enabled?'rgba(248,81,73,.15)':'rgba(63,185,80,.15)')+';color:'+(d.enabled?'var(--red)':'var(--green)')+';cursor:pointer;font-size:13px">'+(d.enabled?'⏸️ 停用风控覆盖':'▶️ 启用风控覆盖')+'</button>';
  html+='<a href="'+B+'/quant/admin/risk-simulator" style="color:var(--accent);font-size:13px">⚙️ 打开模拟器配置</a>';
  if(d.lastUpdateTime){html+='<span style="color:var(--text-dim);font-size:11px;margin-left:auto">最后更新: '+d.lastUpdateTime+'</span>'}
  html+='</div>';

  // Recent transitions
  if(d.recentTransitions&&d.recentTransitions.length>0){
    html+='<details><summary style="cursor:pointer;color:var(--accent);font-size:13px;margin-bottom:8px">📋 最近状态转换 ('+d.recentTransitions.length+')</summary>';
    html+='<table><thead><tr><th>日期</th><th>从</th><th>到</th><th>回撤</th><th>NAV</th><th>缩放</th></tr></thead><tbody>';
    d.recentTransitions.slice().reverse().forEach(function(t){
      html+='<tr><td>'+t.date+'</td>';
      html+='<td style="color:'+(stateColors[t.fromState]||'var(--text)')+'">'+(stateLabels[t.fromState]||t.fromState)+'</td>';
      html+='<td style="color:'+(stateColors[t.toState]||'var(--text)')+'">'+(stateLabels[t.toState]||t.toState)+'</td>';
      html+='<td>'+(t.drawdown*100).toFixed(2)+'%</td>';
      html+='<td>¥'+Number(t.nav).toLocaleString()+'</td>';
      html+='<td>'+(t.scale*100).toFixed(1)+'%</td></tr>';
    });
    html+='</tbody></table></details>';
  }

  el.innerHTML=html;
}).catch(function(){
  document.getElementById("risk-overlay").innerHTML='<p style="color:var(--text-dim)">无法加载风控覆盖状态</p>';
});
</script>
<script>
function toggleRiskOverlay(){
  fetch(B+"/quant/admin/api/risk-overlay/status").then(function(r){return r.json()}).then(function(res){
    var current=res&&res.data&&res.data.enabled;
    return fetch(B+"/quant/admin/api/risk-overlay/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:!current})});
  }).then(function(){location.reload()});
}
</script>
</body>
</html>`;
}

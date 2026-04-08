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
<title>风险管理 — OpenClaw 量化</title>
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
</div>
<script>
var B="${b}";
fetch(B+"/quant/dashboard").then(function(r){return r.json()}).then(function(d){
  var el=document.getElementById("kpi");
  if(!d||d.error){el.innerHTML='<div class="error">'+(d?.error||"暂无数据")+'</div>';return;}
  var port=d.portfolio||{};
  var maxDD=d.maxDrawdown!=null?d.maxDrawdown:(port.maxDrawdown||0);
  var totalVal=port.totalValue||port.total_value||0;
  var todayPnl=port.todayPnl||port.today_pnl||0;
  el.innerHTML='<div class="kpi-box"><div class="kpi-label">总资产</div><div class="kpi-val">¥'+Number(totalVal).toLocaleString()+'</div></div>'+
    '<div class="kpi-box"><div class="kpi-label">今日盈亏</div><div class="kpi-val" style="color:'+(todayPnl>=0?"var(--green)":"var(--red)")+'">'+(todayPnl>=0?"+":"")+Number(todayPnl).toFixed(2)+'</div></div>'+
    '<div class="kpi-box"><div class="kpi-label">最大回撤</div><div class="kpi-val" style="color:var(--red)">'+Number(maxDD*100).toFixed(2)+'%</div></div>';
  // Concentration
  var positions=d.positions||port.positions||[];
  var concEl=document.getElementById("concentration");
  if(!positions.length){concEl.innerHTML='暂无持仓';return;}
  var total=positions.reduce(function(s,p){return s+(p.marketValue||p.market_value||0)},0);
  var html='<table><thead><tr><th>标的</th><th>市值</th><th>占比</th></tr></thead><tbody>';
  positions.sort(function(a,b){return(b.marketValue||b.market_value||0)-(a.marketValue||a.market_value||0)});
  positions.forEach(function(p){
    var mv=p.marketValue||p.market_value||0;
    var pct=total>0?(mv/total*100):0;
    html+='<tr><td>'+(p.symbol||p.code)+'</td><td>¥'+Number(mv).toLocaleString()+'</td><td>'+pct.toFixed(1)+'%</td></tr>';
  });
  html+='</tbody></table>';
  concEl.innerHTML=html;
}).catch(function(e){
  document.getElementById("kpi").innerHTML='<div class="error">'+e+'</div>';
});
// Drawdown placeholder
document.getElementById("drawdown").innerHTML='<p style="color:var(--text-dim)">回撤曲线需要权益历史数据，请在 Dashboard 中查看详细图表。</p>';
</script>
</body>
</html>`;
}

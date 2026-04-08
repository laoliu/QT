/**
 * Trade History Page — 交易记录
 *
 * Filterable table of executed trades.
 * Data fetched from `/quant/admin/api/trade-history`
 */

export function getTradeHistoryHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>交易记录 — OpenClaw 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:18px;font-weight:600}
.container{max-width:1200px;margin:0 auto;padding:24px}
.filters{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.filters input,.filters select{background:var(--bg3);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:6px 10px;font-size:13px}
.filters button{background:var(--accent);color:#fff;border:none;border-radius:4px;padding:6px 16px;cursor:pointer;font-size:13px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500;position:sticky;top:0;background:var(--bg2)}
.loading{color:var(--text-dim);font-style:italic}
.error{color:var(--red)}
.buy{color:var(--red)}.sell{color:var(--green)}
.count{color:var(--text-dim);font-size:13px;margin-bottom:8px}
</style>
</head>
<body>
<header>
  <h1>📋 交易记录</h1>
  <a href="${b}/quant/">← 返回首页</a>
</header>
<div class="container">
  <div class="filters">
    <input type="date" id="f-from" placeholder="开始日期">
    <input type="date" id="f-to" placeholder="结束日期">
    <input type="text" id="f-symbol" placeholder="代码">
    <select id="f-side"><option value="">全部</option><option value="buy">买入</option><option value="sell">卖出</option></select>
    <button onclick="loadTrades()">查询</button>
  </div>
  <div class="count" id="count"></div>
  <div id="trades-content" class="loading">加载中…</div>
</div>
<script>
var B="${b}";
function loadTrades(){
  var from=document.getElementById("f-from").value;
  var to=document.getElementById("f-to").value;
  var sym=document.getElementById("f-symbol").value;
  var side=document.getElementById("f-side").value;
  var q=[];
  if(from)q.push("from="+from);
  if(to)q.push("to="+to);
  if(sym)q.push("symbol="+encodeURIComponent(sym));
  if(side)q.push("side="+side);
  var url=B+"/quant/admin/api/trade-history"+(q.length?"?"+q.join("&"):"");
  fetch(url).then(function(r){return r.json()}).then(function(d){
    var el=document.getElementById("trades-content");
    document.getElementById("count").textContent="共 "+(d.count||0)+" 条记录";
    var trades=d.trades||[];
    if(!trades.length){el.innerHTML='<p class="loading">暂无交易记录</p>';return;}
    var html='<table><thead><tr><th>时间</th><th>代码</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th></tr></thead><tbody>';
    trades.forEach(function(t){
      var side=t.side||t.direction||"";
      var cls=side==="buy"?"buy":"sell";
      var time=t.time||t.timestamp||t.date||"";
      html+='<tr><td>'+time+'</td><td>'+(t.symbol||t.code||"")+'</td><td class="'+cls+'">'+(side==="buy"?"买入":"卖出")+'</td><td>'+(t.price||"-")+'</td><td>'+(t.quantity||t.volume||"-")+'</td><td>'+(t.amount||"-")+'</td></tr>';
    });
    html+='</tbody></table>';
    el.innerHTML=html;
  }).catch(function(e){document.getElementById("trades-content").innerHTML='<div class="error">'+e+'</div>'});
}
loadTrades();
</script>
</body>
</html>`;
}

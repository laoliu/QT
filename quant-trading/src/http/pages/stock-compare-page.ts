/**
 * Stock Compare Page — 股票对比
 *
 * Compare multiple stocks side-by-side with quotes and basic metrics.
 * Data fetched from `/quant/admin/api/stock-compare?action=...`
 */

export function getStockCompareHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>股票对比 — OpenClaw 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:18px;font-weight:600}
.container{max-width:1200px;margin:0 auto;padding:24px}
.input-row{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.input-row input{background:var(--bg3);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:8px 12px;font-size:14px;width:200px}
.input-row button{background:var(--accent);color:#fff;border:none;border-radius:4px;padding:8px 20px;cursor:pointer;font-size:14px}
.compare-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
.stock-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px}
.stock-card h3{font-size:16px;margin-bottom:8px}
.price{font-size:24px;font-weight:700;margin:8px 0}
.up{color:var(--red)}.down{color:var(--green)}
.metric{display:flex;justify-content:space-between;padding:4px 0;font-size:13px;border-bottom:1px solid var(--border)}
.metric-label{color:var(--text-dim)}
.error{color:var(--red);font-size:13px}
</style>
</head>
<body>
<header>
  <h1>⚖️ 股票对比</h1>
  <a href="${b}/quant/">← 返回首页</a>
</header>
<div class="container">
  <div class="input-row">
    <input id="symbols-input" type="text" placeholder="输入股票代码，逗号分隔 (如 600519,000858)">
    <button onclick="doCompare()">对比</button>
  </div>
  <div class="compare-grid" id="compare-result"></div>
</div>
<script>
var B="${b}";
function doCompare(){
  var raw=document.getElementById("symbols-input").value.trim();
  if(!raw)return;
  var symbols=raw.split(/[,，\\s]+/).filter(Boolean);
  var el=document.getElementById("compare-result");
  el.innerHTML='<p style="color:var(--text-dim)">加载中…</p>';
  Promise.all(symbols.map(function(s){
    return fetch(B+"/quant/admin/api/stock-compare?action=quote&symbol="+encodeURIComponent(s))
      .then(function(r){return r.json()})
      .then(function(d){d._symbol=s;return d})
      .catch(function(){return{_symbol:s,error:"请求失败"}});
  })).then(function(results){
    var html="";
    results.forEach(function(d){
      if(d.error){html+='<div class="stock-card"><h3>'+d._symbol+'</h3><div class="error">'+d.error+'</div></div>';return;}
      var price=d.price||d.close||0;
      var chg=d.changePercent||d.change_pct||0;
      var cls=chg>=0?"up":"down";
      html+='<div class="stock-card">';
      html+='<h3>'+(d.name||d._symbol)+' <span style="color:var(--text-dim);font-size:13px">'+d._symbol+'</span></h3>';
      html+='<div class="price '+cls+'">'+Number(price).toFixed(2)+' <span style="font-size:14px">'+(chg>=0?"+":"")+Number(chg).toFixed(2)+'%</span></div>';
      var metrics=[["成交量",d.volume],["成交额",d.amount||d.turnover],["换手率",d.turnoverRate||d.turnover_rate],["市盈率",d.pe||d.pe_ttm],["市值",d.marketCap||d.market_cap]];
      metrics.forEach(function(m){
        if(m[1]!=null)html+='<div class="metric"><span class="metric-label">'+m[0]+'</span><span>'+m[1]+'</span></div>';
      });
      html+='</div>';
    });
    el.innerHTML=html||'<p class="error">无结果</p>';
  });
}
</script>
</body>
</html>`;
}

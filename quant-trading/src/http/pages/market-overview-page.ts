/**
 * Market Overview Page — 大盘分析
 *
 * Sections: 指数行情, 板块排行
 * Data fetched from `/quant/admin/api/market-overview?action=...`
 */

export function getMarketOverviewHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>大盘分析 — OpenClaw 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:18px;font-weight:600}
.container{max-width:1200px;margin:0 auto;padding:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:16px;margin-top:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px}
.card h2{font-size:15px;margin-bottom:12px;color:var(--accent)}
.loading{color:var(--text-dim);font-style:italic}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500}
.error{color:var(--red);font-size:13px}
.index-list{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px}
.index-item{background:var(--bg3);border-radius:6px;padding:12px 16px;min-width:160px}
.index-name{font-size:13px;color:var(--text-dim)}
.index-val{font-size:20px;font-weight:700;margin:4px 0}
.up{color:var(--red)}.down{color:var(--green)}
</style>
</head>
<body>
<header>
  <h1>🏛️ 大盘分析</h1>
  <a href="${b}/quant/">← 返回首页</a>
</header>
<div class="container">
  <div class="grid">
    <div class="card">
      <h2>📊 主要指数</h2>
      <div class="loading" id="index-content">加载中…</div>
    </div>
    <div class="card">
      <h2>🏷️ 板块排行</h2>
      <div class="loading" id="sector-content">加载中…</div>
    </div>
  </div>
</div>
<script>
var B="${b}";
var INDICES=["000001","399001","399006","000300","000016","000905"];
var NAMES={"000001":"上证指数","399001":"深证成指","399006":"创业板指","000300":"沪深300","000016":"上证50","000905":"中证500"};
Promise.all(INDICES.map(function(s){
  return fetch(B+"/quant/admin/api/market-overview?action=index&symbol="+s).then(function(r){return r.json()}).catch(function(){return{error:true}});
})).then(function(results){
  var el=document.getElementById("index-content");
  var html='<div class="index-list">';
  results.forEach(function(d,i){
    if(d.error){html+='<div class="index-item"><div class="index-name">'+NAMES[INDICES[i]]+'</div><div class="error">暂无数据</div></div>';return;}
    var price=d.price||d.close||0;
    var chg=d.changePercent||d.change_pct||0;
    var cls=chg>=0?"up":"down";
    html+='<div class="index-item"><div class="index-name">'+(NAMES[INDICES[i]]||INDICES[i])+'</div><div class="index-val '+cls+'">'+Number(price).toFixed(2)+'</div><div class="'+cls+'">'+(chg>=0?"+":"")+Number(chg).toFixed(2)+'%</div></div>';
  });
  html+='</div>';
  el.innerHTML=html;
});
fetch(B+"/quant/admin/api/market-overview?action=sectors").then(function(r){return r.json()}).then(function(d){
  var el=document.getElementById("sector-content");
  if(d.error){el.innerHTML='<div class="error">'+d.error+'</div>';return;}
  var rows=Array.isArray(d)?d:(d.sectors||d.data||[]);
  if(!rows.length){el.innerHTML='<div class="error">暂无板块数据</div>';return;}
  var html='<table><thead><tr><th>板块</th><th>涨跌幅</th><th>领涨股</th></tr></thead><tbody>';
  rows.slice(0,20).forEach(function(r){
    var chg=r.change_pct||r.changePct||r.pct||0;
    html+='<tr><td>'+(r.name||r.sector||"")+'</td><td style="color:'+(chg>=0?"var(--red)":"var(--green)")+'">'+Number(chg).toFixed(2)+'%</td><td>'+(r.leader||r.top_stock||"")+'</td></tr>';
  });
  html+='</tbody></table>';
  el.innerHTML=html;
}).catch(function(e){document.getElementById("sector-content").innerHTML='<div class="error">'+e+'</div>'});
</script>
</body>
</html>`;
}

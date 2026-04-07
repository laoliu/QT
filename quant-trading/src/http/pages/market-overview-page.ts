/**
 * Market Overview Page — 大盘分析
 *
 * Consolidated view of:
 * - Major index charts (上证/深证/创业板)
 * - Sector heatmap (板块热力图)
 * - Market breadth indicators
 *
 * Served at `/quant/admin/market-overview`
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
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{font-size:18px;font-weight:600}
.header-right{display:flex;align-items:center;gap:12px;font-size:13px}
.breadcrumb{background:var(--bg);padding:6px 24px;font-size:12px;border-bottom:1px solid var(--border)}
.breadcrumb a{color:var(--accent)}
main{max-width:1200px;margin:0 auto;padding:20px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}
.card h3{font-size:14px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.index-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:16px}
@media(max-width:768px){.index-grid{grid-template-columns:1fr}}
.index-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;text-align:center}
.index-card .name{font-size:14px;font-weight:600;margin-bottom:4px}
.index-card .price{font-size:24px;font-weight:700;margin:8px 0;font-variant-numeric:tabular-nums}
.index-card .change{font-size:14px;font-weight:600}
.index-card svg{display:block;margin:8px auto 0}
.sector-heatmap{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.sector-cell{border-radius:6px;padding:8px 12px;font-size:12px;text-align:center;min-width:80px;flex:1 1 auto;color:#fff;cursor:pointer;transition:transform .15s}
.sector-cell:hover{transform:scale(1.05)}
.sector-cell .sname{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sector-cell .spct{font-size:11px;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--text-dim);font-weight:600;font-size:12px;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover{background:var(--bg3)}
.loading{text-align:center;padding:40px;color:var(--text-dim);font-size:14px}
.refresh-btn{background:var(--bg3);border:1px solid var(--border);color:var(--accent);padding:5px 14px;border-radius:6px;cursor:pointer;font-size:12px}
.refresh-btn:hover{border-color:var(--accent)}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
</style>
</head>
<body>
<header>
  <h1>📊 大盘分析</h1>
  <div class="header-right">
    <span id="updateTime" style="color:var(--text-dim)">--</span>
    <button class="refresh-btn" onclick="loadAll()">🔄 刷新</button>
    <a href="${b}/quant/">← 返回首页</a>
  </div>
</header>
<div class="breadcrumb"><a href="${b}/quant/">首页</a> / 大盘分析</div>

<main>
<!-- Index cards -->
<div class="index-grid" id="indexGrid">
  <div class="index-card" id="idx_000001"><div class="name">上证指数</div><div class="price">--</div><div class="change">--</div></div>
  <div class="index-card" id="idx_399001"><div class="name">深证成指</div><div class="price">--</div><div class="change">--</div></div>
  <div class="index-card" id="idx_399006"><div class="name">创业板指</div><div class="price">--</div><div class="change">--</div></div>
</div>

<!-- Sector Heatmap -->
<div class="card">
  <h3>🗺️ 板块热力图</h3>
  <div id="sectorHeatmap" class="loading">加载中...</div>
</div>

<!-- Sector Top Gainers/Losers -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
  <div class="card">
    <h3 style="color:var(--green)">📈 领涨板块 Top 10</h3>
    <div id="topGainers" class="loading">加载中...</div>
  </div>
  <div class="card">
    <h3 style="color:var(--red)">📉 领跌板块 Top 10</h3>
    <div id="topLosers" class="loading">加载中...</div>
  </div>
</div>

<!-- Volume comparison -->
<div class="card">
  <h3>📊 成交额统计</h3>
  <div id="volumeInfo" class="loading">加载中...</div>
</div>
</main>

<div class="toast" id="toast"></div>

<script>
var B='${b}';
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');if(!el)return;el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}
function api(path){
  return fetch(B+path).then(function(r){
    if(!r.ok) return r.json().catch(function(){return{}}).then(function(b){throw new Error(b.error||r.status)});
    return r.json();
  }).catch(function(e){toast('❌ '+e.message);throw e});
}

function loadAll(){
  document.getElementById('updateTime').textContent='加载中...';
  Promise.allSettled([loadIndices(), loadSectors()]).then(function(){
    document.getElementById('updateTime').textContent='更新: '+new Date().toLocaleTimeString('zh-CN');
  });
}

function loadIndices(){
  var indices=[
    {code:'000001',name:'上证指数'},
    {code:'399001',name:'深证成指'},
    {code:'399006',name:'创业板指'}
  ];
  var promises=indices.map(function(idx){
    return api('/quant/admin/api/market-overview?action=index&symbol='+idx.code).then(function(d){
      var el=document.getElementById('idx_'+idx.code);
      if(!el)return;
      if(d.price!=null){
        var chg=d.changePct||0;
        var color=chg>=0?'var(--green)':'var(--red)';
        el.innerHTML=
          '<div class="name">'+idx.name+'</div>'+
          '<div class="price" style="color:'+color+'">'+num(d.price)+'</div>'+
          '<div class="change" style="color:'+color+'">'+(chg>=0?'+':'')+num(chg)+'% &nbsp;'+(d.change>=0?'+':'')+num(d.change||0)+'</div>'+
          '<div style="font-size:11px;color:var(--text-dim);margin-top:6px">成交额: '+num((d.amount||0)/1e8)+' 亿</div>';
      }
    }).catch(function(){});
  });
  return Promise.allSettled(promises);
}

function loadSectors(){
  return api('/quant/admin/api/market-overview?action=sectors').then(function(d){
    var sectors=d.sectors||[];
    if(!sectors.length){
      document.getElementById('sectorHeatmap').innerHTML='<div style="color:var(--text-dim)">暂无数据</div>';
      return;
    }

    // Heatmap
    var hm='<div class="sector-heatmap">';
    sectors.forEach(function(s){
      var pct=s.changePct||0;
      var r,g,bl;
      if(pct>0){r=Math.min(255,80+pct*20);g=60;bl=60}
      else if(pct<0){r=60;g=Math.min(255,80+Math.abs(pct)*20);bl=60}
      else{r=100;g=100;bl=100}
      var bg='rgb('+Math.round(r)+','+Math.round(g)+','+Math.round(bl)+')';
      if(pct>=0){bg='rgba(63,185,80,'+(0.2+Math.min(pct,10)/10*0.8)+')'}
      else{bg='rgba(248,81,73,'+(0.2+Math.min(Math.abs(pct),10)/10*0.8)+')'}
      hm+='<div class="sector-cell" style="background:'+bg+'"><div class="sname">'+esc(s.name)+'</div><div class="spct">'+(pct>=0?'+':'')+num(pct)+'%</div></div>';
    });
    hm+='</div>';
    document.getElementById('sectorHeatmap').innerHTML=hm;

    // Top gainers/losers
    var sorted=sectors.slice().sort(function(a,b){return(b.changePct||0)-(a.changePct||0)});
    var gainers=sorted.slice(0,10);
    var losers=sorted.slice(-10).reverse();

    function makeTable(list){
      var h='<table><tr><th>板块</th><th>涨跌幅</th><th>领涨股</th></tr>';
      list.forEach(function(s){
        var pct=s.changePct||0;
        var color=pct>=0?'var(--green)':'var(--red)';
        h+='<tr><td>'+esc(s.name)+'</td><td style="color:'+color+'">'+(pct>=0?'+':'')+num(pct)+'%</td><td>'+esc(s.leader||'-')+'</td></tr>';
      });
      return h+'</table>';
    }
    document.getElementById('topGainers').innerHTML=makeTable(gainers);
    document.getElementById('topLosers').innerHTML=makeTable(losers);

    // Volume stats
    var totalVol=sectors.reduce(function(s,x){return s+(x.volume||0)},0);
    document.getElementById('volumeInfo').innerHTML=
      '<div style="font-size:16px;font-weight:700;margin-bottom:8px">全市场成交额: '+num(totalVol/1e8)+' 亿</div>'+
      '<div style="font-size:12px;color:var(--text-dim)">共 '+sectors.length+' 个板块</div>';
  }).catch(function(){});
}

loadAll();
setInterval(loadAll, 60000);
</script>
</body>
</html>`;
}

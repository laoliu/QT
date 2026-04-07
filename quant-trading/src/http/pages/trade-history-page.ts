/**
 * Trade History Page — 交易记录
 *
 * Browse historical trades with filtering and CSV export:
 * - Full order history
 * - Filter by date range, symbol, side (buy/sell)
 * - Export to CSV
 *
 * Served at `/quant/admin/trade-history`
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
.filter-bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
.filter-bar input,.filter-bar select{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-size:13px}
.filter-bar input:focus,.filter-bar select:focus{outline:none;border-color:var(--accent)}
.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;text-align:center;min-width:120px;flex:1}
.kpi .label{font-size:11px;color:var(--text-dim);margin-bottom:4px}
.kpi .value{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--text-dim);font-weight:600;font-size:12px;white-space:nowrap;cursor:pointer;user-select:none}
th:hover{color:var(--accent)}
td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover{background:var(--bg3)}
.badge-buy{background:rgba(63,185,80,.15);color:var(--green);display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge-sell{background:rgba(248,81,73,.15);color:var(--red);display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.loading{text-align:center;padding:40px;color:var(--text-dim);font-size:14px}
.empty{text-align:center;padding:40px;color:var(--text-dim);font-size:14px}
.pagination{display:flex;justify-content:center;gap:8px;margin-top:16px}
.pagination button{background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:5px 12px;border-radius:4px;cursor:pointer;font-size:12px}
.pagination button:hover{border-color:var(--accent)}
.pagination button.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
</style>
</head>
<body>
<header>
  <h1>📋 交易记录</h1>
  <div class="header-right">
    <a href="${b}/quant/">← 返回首页</a>
  </div>
</header>
<div class="breadcrumb"><a href="${b}/quant/">首页</a> / 交易记录</div>

<main>
<!-- Filter bar -->
<div class="card">
  <div class="filter-bar">
    <label style="font-size:12px;color:var(--text-dim)">起始日期</label>
    <input type="date" id="dateFrom" style="color-scheme:dark">
    <label style="font-size:12px;color:var(--text-dim)">结束日期</label>
    <input type="date" id="dateTo" style="color-scheme:dark">
    <label style="font-size:12px;color:var(--text-dim)">代码</label>
    <input type="text" id="filterSymbol" placeholder="如 600519" style="width:100px">
    <label style="font-size:12px;color:var(--text-dim)">方向</label>
    <select id="filterSide">
      <option value="">全部</option>
      <option value="buy">买入</option>
      <option value="sell">卖出</option>
    </select>
    <button class="btn btn-primary" onclick="loadTrades()">查询</button>
    <button class="btn" onclick="exportCSV()">📥 导出CSV</button>
  </div>
</div>

<!-- KPI summary -->
<div class="kpi-row">
  <div class="kpi"><div class="label">总交易数</div><div class="value" id="kpiCount">--</div></div>
  <div class="kpi"><div class="label">买入次数</div><div class="value" id="kpiBuy" style="color:var(--green)">--</div></div>
  <div class="kpi"><div class="label">卖出次数</div><div class="value" id="kpiSell" style="color:var(--red)">--</div></div>
  <div class="kpi"><div class="label">总成交额</div><div class="value" id="kpiAmount">--</div></div>
  <div class="kpi"><div class="label">总手续费</div><div class="value" id="kpiFee">--</div></div>
</div>

<!-- Trade table -->
<div class="card">
  <h3>📝 交易明细</h3>
  <div style="overflow-x:auto">
    <div id="tradeTable" class="loading">加载中...</div>
  </div>
  <div class="pagination" id="pagination"></div>
</div>
</main>

<div class="toast" id="toast"></div>

<script>
var B='${b}';
var allTrades=[];
var currentPage=1;
var pageSize=50;
var sortCol='time';
var sortDir=-1;

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');if(!el)return;el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}

function loadTrades(){
  document.getElementById('tradeTable').innerHTML='<div class="loading">加载中...</div>';
  var url='/quant/admin/api/trade-history?limit=2000';
  var from=document.getElementById('dateFrom').value;
  var to=document.getElementById('dateTo').value;
  var sym=document.getElementById('filterSymbol').value.trim();
  var side=document.getElementById('filterSide').value;
  if(from)url+='&from='+from;
  if(to)url+='&to='+to;
  if(sym)url+='&symbol='+sym;
  if(side)url+='&side='+side;

  fetch(B+url).then(function(r){return r.json()}).then(function(d){
    allTrades=d.trades||d.orders||d||[];
    currentPage=1;

    // KPI
    var buyCount=0,sellCount=0,totalAmount=0,totalFee=0;
    allTrades.forEach(function(t){
      if((t.side||t.direction)==='buy')buyCount++;else sellCount++;
      totalAmount+=(t.amount||t.quantity*(t.price||0))||0;
      totalFee+=(t.fee||t.commission||0);
    });
    document.getElementById('kpiCount').textContent=allTrades.length;
    document.getElementById('kpiBuy').textContent=buyCount;
    document.getElementById('kpiSell').textContent=sellCount;
    document.getElementById('kpiAmount').textContent=num(totalAmount);
    document.getElementById('kpiFee').textContent=num(totalFee);

    renderTable();
  }).catch(function(e){
    document.getElementById('tradeTable').innerHTML='<div class="empty">无法加载交易记录: '+esc(e.message)+'</div>';
  });
}

function renderTable(){
  if(!allTrades.length){
    document.getElementById('tradeTable').innerHTML='<div class="empty">暂无交易记录</div>';
    document.getElementById('pagination').innerHTML='';
    return;
  }

  // Sort
  var sorted=allTrades.slice().sort(function(a,b){
    var av=a[sortCol]||'';var bv=b[sortCol]||'';
    if(typeof av==='number')return(av-bv)*sortDir;
    return String(av).localeCompare(String(bv))*sortDir;
  });

  // Paginate
  var totalPages=Math.ceil(sorted.length/pageSize);
  var start=(currentPage-1)*pageSize;
  var page=sorted.slice(start,start+pageSize);

  var h='<table><tr>'+
    '<th onclick="sortBy(\\'time\\')">时间 '+sortArrow('time')+'</th>'+
    '<th onclick="sortBy(\\'symbol\\')">代码 '+sortArrow('symbol')+'</th>'+
    '<th>名称</th>'+
    '<th onclick="sortBy(\\'side\\')">方向 '+sortArrow('side')+'</th>'+
    '<th onclick="sortBy(\\'price\\')">价格 '+sortArrow('price')+'</th>'+
    '<th onclick="sortBy(\\'quantity\\')">数量 '+sortArrow('quantity')+'</th>'+
    '<th>成交额</th>'+
    '<th>手续费</th>'+
    '<th>策略</th>'+
    '<th>状态</th>'+
    '</tr>';
  page.forEach(function(t){
    var side=t.side||t.direction||'';
    var isBuy=side==='buy';
    var amt=(t.amount||t.quantity*(t.price||0))||0;
    h+='<tr>'+
      '<td>'+esc((t.time||t.timestamp||t.date||'-').substring(0,19))+'</td>'+
      '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(t.symbol||t.code||'')+'">'+esc(t.symbol||t.code||'-')+'</a></td>'+
      '<td>'+esc(t.name||'-')+'</td>'+
      '<td><span class="badge-'+(isBuy?'buy':'sell')+'">'+(isBuy?'买入':'卖出')+'</span></td>'+
      '<td>'+num(t.price)+'</td>'+
      '<td>'+Number(t.quantity||0).toLocaleString()+'</td>'+
      '<td>'+num(amt)+'</td>'+
      '<td>'+num(t.fee||t.commission||0)+'</td>'+
      '<td>'+esc(t.strategy||'-')+'</td>'+
      '<td>'+esc(t.status||'filled')+'</td>'+
      '</tr>';
  });
  h+='</table>';
  document.getElementById('tradeTable').innerHTML=h;

  // Pagination
  var pg='';
  if(totalPages>1){
    if(currentPage>1)pg+='<button onclick="goPage('+(currentPage-1)+')">上一页</button>';
    for(var i=1;i<=totalPages;i++){
      if(i===currentPage)pg+='<button class="active">'+i+'</button>';
      else if(Math.abs(i-currentPage)<4||i===1||i===totalPages)pg+='<button onclick="goPage('+i+')">'+i+'</button>';
      else if(Math.abs(i-currentPage)===4)pg+='<button disabled>...</button>';
    }
    if(currentPage<totalPages)pg+='<button onclick="goPage('+(currentPage+1)+')">下一页</button>';
  }
  document.getElementById('pagination').innerHTML=pg;
}

function sortBy(col){
  if(sortCol===col)sortDir*=-1;else{sortCol=col;sortDir=-1}
  renderTable();
}
function sortArrow(col){return sortCol===col?(sortDir>0?'▲':'▼'):''}
function goPage(p){currentPage=p;renderTable();window.scrollTo(0,0)}

function exportCSV(){
  if(!allTrades.length){toast('没有数据可导出');return}
  var header='时间,代码,名称,方向,价格,数量,成交额,手续费,策略,状态\\n';
  var rows=allTrades.map(function(t){
    var side=(t.side||t.direction||'')==='buy'?'买入':'卖出';
    var amt=(t.amount||t.quantity*(t.price||0))||0;
    return [
      (t.time||t.timestamp||t.date||''),
      (t.symbol||t.code||''),
      (t.name||''),
      side,
      (t.price||0),
      (t.quantity||0),
      amt.toFixed(2),
      (t.fee||t.commission||0).toFixed(2),
      (t.strategy||''),
      (t.status||'filled')
    ].map(function(v){return '"'+String(v).replace(/"/g,'""')+'"'}).join(',');
  }).join('\\n');
  var csv='\\uFEFF'+header+rows;
  var blob=new Blob([csv],{type:'text/csv;charset=utf-8'});
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  a.href=url;
  a.download='trades_'+(new Date().toISOString().slice(0,10))+'.csv';
  a.click();
  URL.revokeObjectURL(url);
  toast('CSV 导出成功');
}

// Default date range: last 30 days
var now=new Date();
document.getElementById('dateTo').value=now.toISOString().slice(0,10);
var ago=new Date(now-30*86400000);
document.getElementById('dateFrom').value=ago.toISOString().slice(0,10);

loadTrades();
</script>
</body>
</html>`;
}

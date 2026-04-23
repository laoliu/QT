/**
 * Historical Trades Page (UI-3) — 080009
 *
 * 05050800040105053107CSV导出
 */
export function getHistoricalTradesPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 0800</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>97</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.breadcrumb{background:var(--bg);padding:8px 24px;font-size:12px;border-bottom:1px solid var(--border)}
header{display:flex;justify-content:space-between;align-items:center;padding:16px 24px;background:var(--bg2);border-bottom:1px solid var(--border)}
header h1{margin:0;font-size:20px}
.actions{display:flex;gap:8px;align-items:center}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:var(--accent);color:#000;border-color:var(--accent)}.btn-primary:hover{opacity:.9;color:#000}
main{padding:20px 24px}
.filters{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px;align-items:center}
.filters label{font-size:12px;color:var(--text-dim)}
.filters input,.filters select{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-size:12px}
.filters input:focus,.filters select:focus{outline:none;border-color:var(--accent)}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.card h2{margin:0 0 12px;font-size:15px}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:10px 8px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
th{color:var(--text-dim);font-size:11px;cursor:pointer;user-select:none}th:hover{color:var(--accent)}
th.sorted-asc::after{content:' ▲';font-size:8px}th.sorted-desc::after{content:' ▼';font-size:8px}
tr:hover{background:rgba(255,255,255,.02)}
.up{color:var(--green)}.down{color:var(--red)}
.tag{display:inline-block;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:600}
.tag-buy{background:rgba(248,81,73,0.2);color:var(--red)}.tag-sell{background:rgba(63,185,80,0.2);color:var(--green)}
.empty{padding:40px;text-align:center;color:var(--text-dim)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:16px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}
.kpi-val{font-size:18px;font-weight:700}.kpi-label{font-size:11px;color:var(--text-dim);margin-top:4px}
.pagination{display:flex;justify-content:center;align-items:center;gap:8px;padding:12px 0}
.pagination button{background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:6px;cursor:pointer;font-size:12px}
.pagination button:disabled{opacity:0.4;cursor:default}
.pagination button:hover:not(:disabled){border-color:var(--accent)}
.pagination span{font-size:12px;color:var(--text-dim)}
@media(max-width:640px){.kpis{grid-template-columns:repeat(2,1fr)}.filters{flex-direction:column}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">92 07</a> / <a href="${b}/quant/admin/ui">99 管理面板</a> / <span>97 0800</span></div>
<header>
  <h1>97 080009</h1>
  <div class="actions">
    <button class="btn" onclick="exportCsv()">93 导出CSV</button>
    <button class="btn" onclick="reload()">94 06</button>
    <a class="btn" href="${b}/quant/admin/ui">返回管理面板</a>
  </div>
</header>
<main>
  <div class="kpis" id="kpis">
    <div class="kpi"><div class="kpi-val" id="kpi-total">-</div><div class="kpi-label">1705</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-net-pnl">-</div><div class="kpi-label">07</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-win-rate">-</div><div class="kpi-label">09</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-avg-hold">-</div><div class="kpi-label">090202</div></div>
  </div>

  <div class="filters">
    <label>13</label>
    <input type="date" id="f-from">
    <span style="color:var(--text-dim)">~</span>
    <input type="date" id="f-to">
    <label>股票代码</label>
    <input type="text" id="f-symbol" placeholder="000001" style="width:80px">
    <label>方向</label>
    <select id="f-side"><option value="">00</option><option value="buy">买入</option><option value="sell">卖出</option></select>
    <label>策略</label>
    <input type="text" id="f-strategy" placeholder="策略名" style="width:90px">
    <button class="btn-primary btn" onclick="applyFilter()">0505</button>
    <button class="btn" onclick="clearFilter()">重置</button>
  </div>

  <div class="card">
    <table id="trades-table">
      <thead><tr>
        <th data-col="time" onclick="sortBy('time')">02</th>
        <th data-col="symbol" onclick="sortBy('symbol')">代码</th>
        <th>名称</th>
        <th data-col="side" onclick="sortBy('side')">方向</th>
        <th data-col="price" onclick="sortBy('price')">00</th>
        <th data-col="qty" onclick="sortBy('qty')">数量</th>
        <th data-col="amount" onclick="sortBy('amount')">金额</th>
        <th data-col="pnl" onclick="sortBy('pnl')">07</th>
        <th>策略</th>
      </tr></thead>
      <tbody id="trades-body"></tbody>
    </table>
    <div class="pagination">
      <button onclick="changePage(-1)" id="prev-btn" disabled>72 0507</button>
      <span id="page-info">-</span>
      <button onclick="changePage(1)" id="next-btn" disabled>0507 74</button>
    </div>
  </div>
</main>

<script>
const B='${b}';
let allTrades=[];
let filteredTrades=[];
let currentPage=1;
const PAGE_SIZE=50;
let sortCol='time',sortDir='desc';

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v,d=2){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d}):'-'}

function sortBy(col){
  if(sortCol===col){sortDir=sortDir==='asc'?'desc':'asc'}else{sortCol=col;sortDir='desc'}
  document.querySelectorAll('th').forEach(el=>el.className='');
  const th=document.querySelector('th[data-col="'+col+'"]');
  if(th)th.className='sorted-'+sortDir;
  applySort();
  renderPage();
}

function applySort(){
  filteredTrades.sort((a,b)=>{
    let va=a[sortCol],vb=b[sortCol];
    if(typeof va==='string')va=va.toLowerCase();
    if(typeof vb==='string')vb=vb.toLowerCase();
    if(va<vb)return sortDir==='asc'?-1:1;
    if(va>vb)return sortDir==='asc'?1:-1;
    return 0;
  });
}

function applyFilter(){
  const from=document.getElementById('f-from').value;
  const to=document.getElementById('f-to').value;
  const sym=document.getElementById('f-symbol').value.trim().toLowerCase();
  const side=document.getElementById('f-side').value;
  const strat=document.getElementById('f-strategy').value.trim().toLowerCase();

  filteredTrades=allTrades.filter(t=>{
    if(from&&t.time<from)return false;
    if(to&&t.time>to+'T23:59:59')return false;
    if(sym&&!(t.symbol||'').toLowerCase().includes(sym))return false;
    if(side&&t.side!==side)return false;
    if(strat&&!(t.strategy||'').toLowerCase().includes(strat))return false;
    return true;
  });
  applySort();
  currentPage=1;
  renderPage();
  updateKpis();
}

function clearFilter(){
  document.getElementById('f-from').value='';
  document.getElementById('f-to').value='';
  document.getElementById('f-symbol').value='';
  document.getElementById('f-side').value='';
  document.getElementById('f-strategy').value='';
  filteredTrades=[...allTrades];
  applySort();
  currentPage=1;
  renderPage();
  updateKpis();
}

function changePage(delta){
  currentPage+=delta;
  renderPage();
}

function renderPage(){
  const start=(currentPage-1)*PAGE_SIZE;
  const page=filteredTrades.slice(start,start+PAGE_SIZE);
  const totalPages=Math.max(1,Math.ceil(filteredTrades.length/PAGE_SIZE));

  document.getElementById('page-info').textContent='第'+currentPage+'/'+totalPages+'07 (共'+filteredTrades.length+'条)';
  document.getElementById('prev-btn').disabled=currentPage<=1;
  document.getElementById('next-btn').disabled=currentPage>=totalPages;

  if(page.length===0){
    document.getElementById('trades-body').innerHTML='<tr><td colspan="9" class="empty">150004</td></tr>';
    return;
  }

  document.getElementById('trades-body').innerHTML=page.map(t=>{
    const pnl=Number(t.pnl||0);
    const cls=pnl>0?'up':pnl<0?'down':'';
    const sideTag=t.side==='buy'?'<span class="tag tag-buy">买入</span>':'<span class="tag tag-sell">卖出</span>';
    return '<tr>'+
      '<td>'+esc(t.time)+'</td>'+
      '<td>'+esc(t.symbol)+'</td>'+
      '<td>'+esc(t.name||'-')+'</td>'+
      '<td>'+sideTag+'</td>'+
      '<td>'+num(t.price)+'</td>'+
      '<td>'+num(t.qty,0)+'</td>'+
      '<td>'+num(t.amount)+'</td>'+
      '<td class="'+cls+'">'+(pnl>=0?'+':'')+num(pnl)+'</td>'+
      '<td>'+esc(t.strategy||'-')+'</td>'+
    '</tr>';
  }).join('');
}

function updateKpis(){
  document.getElementById('kpi-total').textContent=filteredTrades.length;
  const netPnl=filteredTrades.reduce((s,t)=>s+(Number(t.pnl)||0),0);
  const nel=document.getElementById('kpi-net-pnl');
  nel.textContent=(netPnl>=0?'+':'')+num(netPnl);
  nel.style.color=netPnl>=0?'var(--green)':'var(--red)';
  const sells=filteredTrades.filter(t=>t.side==='sell'&&t.pnl!=null);
  const wins=sells.filter(t=>Number(t.pnl)>0).length;
  document.getElementById('kpi-win-rate').textContent=sells.length>0?(wins/sells.length*100).toFixed(1)+'%':'-';
  document.getElementById('kpi-avg-hold').textContent='-'; // Placeholder - derived from hold duration if available
}

function exportCsv(){
  if(!filteredTrades.length)return;
  const headers=['02','代码','名称','方向','00','数量','金额','07','策略'];
  let csv='\\uFEFF'+headers.join(',')+String.fromCharCode(10);
  for(const t of filteredTrades){
    csv+=[t.time,t.symbol,t.name||'',t.side,t.price,t.qty,t.amount,t.pnl||0,t.strategy||''].join(',')+String.fromCharCode(10);
  }
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='trades_'+(function(){var d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')})()+'.csv';
  a.click();
}

async function reload(){
  try{
    const res=await fetch(B+'/quant/api/trade-log');
    if(!res.ok)throw new Error('fetch fail');
    const data=await res.json();
    allTrades=(data.trades||data||[]).map(t=>({
      time:t.time||t.timestamp||t.filledAt||'',
      symbol:t.symbol||t.code||'',
      name:t.name||t.stockName||'',
      side:t.side||t.direction||'',
      price:Number(t.price||t.filledPrice||0),
      qty:Number(t.qty||t.quantity||t.amount||0),
      amount:Number(t.totalAmount||t.amount||(t.price*t.qty)||0),
      pnl:t.pnl!=null?Number(t.pnl):null,
      strategy:t.strategy||t.strategyName||'',
    }));
    filteredTrades=[...allTrades];
    applySort();
    currentPage=1;
    renderPage();
    updateKpis();
  }catch(e){
    document.getElementById('trades-body').innerHTML='<tr><td colspan="9" class="empty">加载失败: '+esc(e.message)+'</td></tr>';
  }
}
reload();
</script>
</body>
</html>`;
}

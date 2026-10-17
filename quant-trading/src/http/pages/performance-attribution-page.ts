/**
 * Performance Attribution Page (UI-4) — 绩效归因分析
 *
 * 0505/个股/0202070300
 */
export function getPerformanceAttributionPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quant Trading 绩效归因</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>96</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--purple:#bc8cff;--orange:#db6d28;--cyan:#39d353}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.breadcrumb{background:var(--bg);padding:8px 24px;font-size:12px;border-bottom:1px solid var(--border)}
header{display:flex;justify-content:space-between;align-items:center;padding:16px 24px;background:var(--bg2);border-bottom:1px solid var(--border)}
header h1{margin:0;font-size:20px}
.actions{display:flex;gap:8px;align-items:center}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--accent);color:var(--accent)}
main{padding:20px 24px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}
.kpi-val{font-size:18px;font-weight:700}.kpi-label{font-size:11px;color:var(--text-dim);margin-top:4px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.card h2{margin:0 0 12px;font-size:15px}
.tabs{display:flex;gap:4px;margin-bottom:16px}
.tab{padding:8px 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text-dim);cursor:pointer;font-size:12px}
.tab.active{background:var(--accent);color:#000;border-color:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:10px 8px;border-bottom:1px solid var(--border);text-align:left}
th{color:var(--text-dim);font-size:11px}tr:hover{background:rgba(255,255,255,.02)}
.up{color:var(--green)}.down{color:var(--red)}
.bar{height:18px;border-radius:3px;display:inline-block;vertical-align:middle;min-width:2px}
.bar-pos{background:var(--green)}.bar-neg{background:var(--red)}
.chart-container{height:300px;position:relative;margin:12px 0}
canvas{width:100%!important;height:100%!important}
.empty{padding:40px;text-align:center;color:var(--text-dim)}
@media(max-width:768px){.grid2{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">92 07</a> / <a href="${b}/quant/admin/ui">99 管理面板</a> / <span>96 绩效归因</span></div>
<header>
  <h1>96 绩效归因分析</h1>
  <div class="actions">
    <select id="period" class="btn" onchange="reload()">
      <option value="1m">近1月</option>
      <option value="3m" selected>近3月</option>
      <option value="6m">近6月</option>
      <option value="1y">近1年</option>
      <option value="all">00</option>
    </select>
    <a class="btn" href="${b}/quant/admin/ui">返回管理面板</a>
  </div>
</header>
<main>
  <div class="kpis">
    <div class="kpi"><div class="kpi-val" id="kpi-net">-</div><div class="kpi-label">07</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-trades">-</div><div class="kpi-label">总交易数</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-win">-</div><div class="kpi-label">09</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-pf">-</div><div class="kpi-label">07</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-sharpe">-</div><div class="kpi-label">夏普比率</div></div>
  </div>

  <div class="tabs">
    <div class="tab active" onclick="switchTab('strategy')">按策略</div>
    <div class="tab" onclick="switchTab('symbol')">按个股</div>
    <div class="tab" onclick="switchTab('period')">02</div>
  </div>

  <div class="grid2">
    <div class="card">
      <h2 id="chart-title">0702</h2>
      <div class="chart-container"><canvas id="bar-chart"></canvas></div>
    </div>
    <div class="card">
      <h2 id="pie-title">0108</h2>
      <div class="chart-container"><canvas id="pie-chart"></canvas></div>
    </div>
  </div>

  <div class="card">
    <h2 id="table-title">策略明细</h2>
    <table>
      <thead id="table-head"></thead>
      <tbody id="table-body"></tbody>
    </table>
  </div>
</main>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"><\/script>
<script>
const B='${b}';
let barChart=null,pieChart=null;
let currentTab='strategy';
let attrData={};

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v,d=2){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d}):'-'}

function getDateRange(){
  const p=document.getElementById('period').value;
  const now=new Date();
  let from;
  if(p==='1m')from=new Date(now.getTime()-30*86400000);
  else if(p==='3m')from=new Date(now.getTime()-90*86400000);
  else if(p==='6m')from=new Date(now.getTime()-180*86400000);
  else if(p==='1y')from=new Date(now.getTime()-365*86400000);
  else from=new Date('2020-01-01');
  return {from:from.toISOString().slice(0,10),to:now.toISOString().slice(0,10)};
}

function switchTab(tab){
  currentTab=tab;
  document.querySelectorAll('.tab').forEach(el=>el.classList.remove('active'));
  document.querySelector('.tab[onclick*="'+tab+'"]').classList.add('active');
  renderView();
}

const COLORS=['#58a6ff','#3fb950','#f85149','#d29922','#bc8cff','#db6d28','#39d353','#f778ba','#79c0ff','#ffa657'];

function renderView(){
  const data=attrData[currentTab]||[];
  const labels={'strategy':'策略','symbol':'个股','period':'02'};
  const label=labels[currentTab]||'07';
  document.getElementById('chart-title').textContent=label+'0702';
  document.getElementById('pie-title').textContent=label+'贡献占比';
  document.getElementById('table-title').textContent=label+'06';

  const names=data.map(d=>d.name||d.label||d.key||'-');
  const pnls=data.map(d=>Number(d.netPnl||d.pnl||0));
  const counts=data.map(d=>Number(d.trades||d.count||0));

  // Bar chart
  if(barChart)barChart.destroy();
  const ctx1=document.getElementById('bar-chart').getContext('2d');
  barChart=new Chart(ctx1,{
    type:'bar',
    data:{labels:names,datasets:[{label:'07',data:pnls,backgroundColor:pnls.map(v=>v>=0?'rgba(63,185,80,.6)':'rgba(248,81,73,.6)'),borderRadius:4}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8b949e',font:{size:10}},grid:{display:false}},y:{ticks:{color:'#8b949e'},grid:{color:'rgba(48,54,61,.5)'}}}}
  });

  // Pie chart
  if(pieChart)pieChart.destroy();
  const absPnls=pnls.map(Math.abs);
  const ctx2=document.getElementById('pie-chart').getContext('2d');
  pieChart=new Chart(ctx2,{
    type:'doughnut',
    data:{labels:names,datasets:[{data:absPnls,backgroundColor:COLORS.slice(0,names.length)}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'right',labels:{color:'#c9d1d9',font:{size:10},boxWidth:12}}}}
  });

  // Table
  document.getElementById('table-head').innerHTML='<tr><th>'+label+'</th><th>交易数</th><th>07</th><th>09</th><th>07</th><th>00</th></tr>';
  if(!data.length){
    document.getElementById('table-body').innerHTML='<tr><td colspan="6" class="empty">暂无数据</td></tr>';
    return;
  }
  const maxAbs=Math.max(...pnls.map(Math.abs),1);
  document.getElementById('table-body').innerHTML=data.map((d,i)=>{
    const p=Number(d.netPnl||d.pnl||0);
    const cls=p>=0?'up':'down';
    const barW=Math.round(Math.abs(p)/maxAbs*80);
    const barCls=p>=0?'bar-pos':'bar-neg';
    const wr=d.winRate!=null?(Number(d.winRate)*100).toFixed(1)+'%':'-';
    const pf=d.profitFactor!=null?Number(d.profitFactor).toFixed(2):'-';
    return '<tr><td>'+esc(d.name||d.label||d.key)+'</td><td>'+num(d.trades||d.count||0,0)+'</td>'+
      '<td class="'+cls+'">'+(p>=0?'+':'')+num(p)+'</td><td>'+wr+'</td><td>'+pf+'</td>'+
      '<td><span class="bar '+barCls+'" style="width:'+barW+'px"></span></td></tr>';
  }).join('');
}

function updateOverallKpis(raw){
  const nel=document.getElementById('kpi-net');
  const net=Number(raw.totalNetPnl||0);
  nel.textContent=(net>=0?'+':'')+num(net);nel.style.color=net>=0?'var(--green)':'var(--red)';
  document.getElementById('kpi-trades').textContent=raw.totalTrades||0;
  document.getElementById('kpi-win').textContent=raw.overallWinRate!=null?(Number(raw.overallWinRate)*100).toFixed(1)+'%':'-';
  document.getElementById('kpi-pf').textContent=raw.profitFactor!=null&&raw.profitFactor!==Infinity?Number(raw.profitFactor).toFixed(2):'-';
  document.getElementById('kpi-sharpe').textContent=raw.sharpeRatio!=null?Number(raw.sharpeRatio).toFixed(2):'-';
}

async function reload(){
  try{
    const {from,to}=getDateRange();
    const [r1,r2,r3]=await Promise.all([
      fetch(B+'/quant/api/tools/performance_attribution',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'by_strategy',from,to})}),
      fetch(B+'/quant/api/tools/performance_attribution',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'by_symbol',from,to})}),
      fetch(B+'/quant/api/tools/performance_attribution',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'by_period',from,to,period_type:'monthly'})}),
    ]);
    const d1=await r1.json(),d2=await r2.json(),d3=await r3.json();
    attrData={strategy:d1.items||d1.strategies||[],symbol:d2.items||d2.symbols||[],period:d3.items||d3.periods||[]};
    updateOverallKpis(d1);
    renderView();
  }catch(e){
    document.getElementById('table-body').innerHTML='<tr><td colspan="6" class="empty">加载失败: '+esc(e.message)+'</td></tr>';
  }
}
reload();
</script>
</body>
</html>`;
}

/**
 * Equity Curve Page (UI-6) — 交互式资金曲线
 *
 * 00 lightweight-charts (CDN) 绘制资金曲线
 */
export function getEquityCurvePageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quant Trading 资金曲线</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>94</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.breadcrumb{background:var(--bg);padding:8px 24px;font-size:12px;border-bottom:1px solid var(--border)}
header{display:flex;justify-content:space-between;align-items:center;padding:16px 24px;background:var(--bg2);border-bottom:1px solid var(--border)}
header h1{margin:0;font-size:20px}
.actions{display:flex;gap:8px;align-items:center}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--accent);color:var(--accent)}.btn.active{background:rgba(88,166,255,.14);border-color:var(--accent);color:var(--accent)}
main{padding:20px 24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.card h2{margin:0 0 12px;font-size:15px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}
.kpi-val{font-size:18px;font-weight:700}.kpi-label{font-size:11px;color:var(--text-dim);margin-top:4px}
#chart{width:100%;height:400px}
.period-btns{display:flex;gap:6px;margin-bottom:12px}
.empty{padding:24px;text-align:center;color:var(--text-dim)}
@media(max-width:640px){.kpis{grid-template-columns:repeat(2,1fr)}#chart{height:280px}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">92 07</a> / <a href="${b}/quant/admin/ui">99 管理面板</a> / <span>94 资金曲线</span></div>
<header>
  <h1>94 资金曲线</h1>
  <div class="actions">
    <select id="mode" class="btn" onchange="reload()">
      <option value="paper">00</option>
      <option value="live">06</option>
    </select>
    <a class="btn" href="${b}/quant/admin/ui">返回管理面板</a>
  </div>
</header>
<main>
  <div class="kpis">
    <div class="kpi"><div class="kpi-val" id="kpi-latest">-</div><div class="kpi-label">0605</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-total-return">-</div><div class="kpi-label">总收益率</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-max-dd">-</div><div class="kpi-label">最大回撤</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-days">-</div><div class="kpi-label">交易天数</div></div>
  </div>

  <div class="card">
    <h2>净值曲线</h2>
    <div class="period-btns">
      <button class="btn active" onclick="setPeriod(30,this)">近30天</button>
      <button class="btn" onclick="setPeriod(90,this)">近3月</button>
      <button class="btn" onclick="setPeriod(180,this)">近半年</button>
      <button class="btn" onclick="setPeriod(0,this)">00</button>
    </div>
    <div id="chart"></div>
  </div>

  <div class="card">
    <h2>0706</h2>
    <div id="table-area"><div class="empty">加载中...</div></div>
  </div>
</main>

<script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
<script>
const B='${b}';
let allData=[];
let chart=null,lineSeries=null;
let currentPeriod=30;

function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function createChart(){
  const container=document.getElementById('chart');
  container.innerHTML='';
  chart=LightweightCharts.createChart(container,{
    width:container.clientWidth,
    height:400,
    layout:{background:{type:'solid',color:'#161b22'},textColor:'#c9d1d9'},
    grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},
    timeScale:{borderColor:'#30363d'},
    rightPriceScale:{borderColor:'#30363d'},
    crosshair:{mode:0},
  });
  lineSeries=chart.addAreaSeries({
    topColor:'rgba(88,166,255,0.3)',
    bottomColor:'rgba(88,166,255,0.02)',
    lineColor:'#58a6ff',
    lineWidth:2,
  });
  window.addEventListener('resize',()=>{
    chart.applyOptions({width:container.clientWidth});
  });
}

function setPeriod(days,btn){
  currentPeriod=days;
  document.querySelectorAll('.period-btns .btn').forEach(b=>b.classList.remove('active'));
  if(btn)btn.classList.add('active');
  updateChart();
}

function updateChart(){
  if(!lineSeries)return;
  let data=allData;
  if(currentPeriod>0){
    data=data.slice(-currentPeriod);
  }
  lineSeries.setData(data.map(d=>({time:d.date,value:d.equity})));
  chart.timeScale().fitContent();
}

function updateKpis(){
  if(allData.length===0)return;
  const first=allData[0];
  const last=allData[allData.length-1];
  document.getElementById('kpi-latest').textContent='06'+num(last.equity);
  const ret=first.equity>0?((last.equity-first.equity)/first.equity*100):0;
  const retEl=document.getElementById('kpi-total-return');
  retEl.textContent=(ret>=0?'+':'')+ret.toFixed(2)+'%';
  retEl.style.color=ret>=0?'var(--green)':'var(--red)';

  // Max drawdown
  let peak=0,maxDD=0;
  for(const d of allData){
    if(d.equity>peak)peak=d.equity;
    const dd=peak>0?(peak-d.equity)/peak:0;
    if(dd>maxDD)maxDD=dd;
  }
  document.getElementById('kpi-max-dd').textContent='-'+(maxDD*100).toFixed(2)+'%';
  document.getElementById('kpi-days').textContent=allData.length;
}

function renderTable(){
  const recent=allData.slice(-30).reverse();
  if(recent.length===0){
    document.getElementById('table-area').innerHTML='<div class="empty">无数据</div>';
    return;
  }
  let h='<table><tr><th>日期</th><th>05</th><th>当日盈亏</th><th>02</th></tr>';
  for(const d of recent){
    const pnl=d.dailyPnl||0;
    const color=pnl>0?'var(--green)':pnl<0?'var(--red)':'var(--text)';
    h+='<tr><td>'+esc(d.date)+'</td><td>'+num(d.equity)+'</td>';
    h+='<td style="color:'+color+'">'+(pnl>=0?'+':'')+num(pnl)+'</td>';
    h+='<td>'+(d.positionCount||0)+'</td></tr>';
  }
  h+='</table>';
  document.getElementById('table-area').innerHTML=h;
}

async function reload(){
  try{
    const mode=document.getElementById('mode').value;
    const res=await fetch(B+'/quant/api/equity-history?mode='+mode);
    if(!res.ok)throw new Error('fetch failed');
    const result=await res.json();
    allData=(result.snapshots||result||[]).map(d=>({
      date:d.date||d.timestamp,
      equity:d.equity||d.totalAssets||0,
      dailyPnl:d.dailyPnl||0,
      positionCount:d.positionCount||0,
    }));
    if(!chart)createChart();
    updateChart();
    updateKpis();
    renderTable();
  }catch(e){
    document.getElementById('table-area').innerHTML='<div class="empty">加载失败: '+esc(e.message)+'</div>';
  }
}
reload();
</script>
</body>
</html>`;
}

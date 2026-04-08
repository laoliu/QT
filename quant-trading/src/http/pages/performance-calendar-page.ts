/**
 * Performance Calendar Page (RE-6) — 绩效日历热力图
 *
 * 04050507020507
 */
export function getPerformanceCalendarPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 绩效日历</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>91</text></svg>">
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
main{padding:20px 24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.card h2{margin:0 0 12px;font-size:15px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}
.kpi-val{font-size:18px;font-weight:700}.kpi-label{font-size:11px;color:var(--text-dim);margin-top:4px}
.calendar-nav{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.calendar-nav button{background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:6px;cursor:pointer;font-size:13px}
.calendar-nav button:hover{border-color:var(--accent)}
.calendar-nav span{font-size:16px;font-weight:600;min-width:120px;text-align:center}
.calendar{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}
.cal-header{text-align:center;font-size:11px;color:var(--text-dim);padding:6px}
.cal-day{aspect-ratio:1;border-radius:6px;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:12px;cursor:default;position:relative;min-height:50px}
.cal-day .day-num{font-size:11px;color:var(--text-dim)}.cal-day .day-val{font-size:10px;font-weight:600;margin-top:2px}
.cal-day.empty{background:transparent}
.cal-day.no-data{background:var(--bg3);opacity:0.4}
.tooltip{position:absolute;bottom:110%;left:50%;transform:translateX(-50%);background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:8px;font-size:11px;white-space:nowrap;display:none;z-index:10}
.cal-day:hover .tooltip{display:block}
.legend{display:flex;align-items:center;gap:6px;margin-top:12px;font-size:11px;color:var(--text-dim)}
.legend-box{width:16px;height:16px;border-radius:3px}
.month-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:16px}
.month-stat{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:10px 14px}
.month-stat-label{font-size:11px;color:var(--text-dim)}.month-stat-val{font-size:16px;font-weight:700;margin-top:4px}
@media(max-width:640px){.calendar{gap:2px}.cal-day{min-height:40px;font-size:10px}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">92 07</a> / <a href="${b}/quant/admin/ui">99 管理面板</a> / <span>91 绩效日历</span></div>
<header>
  <h1>91 绩效日历热力图</h1>
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
    <div class="kpi"><div class="kpi-val" id="kpi-month-pnl">-</div><div class="kpi-label">本月盈亏</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-win-days">-</div><div class="kpi-label">盈利天数</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-lose-days">-</div><div class="kpi-label">亏损天数</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-win-rate">-</div><div class="kpi-label">09</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-best-day">-</div><div class="kpi-label">最佳单日</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-worst-day">-</div><div class="kpi-label">最差单日</div></div>
  </div>

  <div class="card">
    <div class="calendar-nav">
      <button onclick="changeMonth(-1)">72</button>
      <span id="month-label">-</span>
      <button onclick="changeMonth(1)">74</button>
      <button onclick="goToday()" style="margin-left:auto">今天</button>
    </div>
    <div class="calendar" id="calendar"></div>
    <div class="legend">
      <span>亏损</span>
      <div class="legend-box" style="background:rgba(248,81,73,0.8)"></div>
      <div class="legend-box" style="background:rgba(248,81,73,0.4)"></div>
      <div class="legend-box" style="background:var(--bg3)"></div>
      <div class="legend-box" style="background:rgba(63,185,80,0.4)"></div>
      <div class="legend-box" style="background:rgba(63,185,80,0.8)"></div>
      <span>07</span>
    </div>
  </div>
</main>

<script>
const B='${b}';
let allSnapshots=[];
let currentYear=new Date().getFullYear();
let currentMonth=new Date().getMonth();

function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function pnlColor(v,alpha){
  if(v>0)return 'rgba(63,185,80,'+Math.min(0.9,0.2+Math.abs(v)/3000*0.7)+')';
  if(v<0)return 'rgba(248,81,73,'+Math.min(0.9,0.2+Math.abs(v)/3000*0.7)+')';
  return 'var(--bg3)';
}

function changeMonth(delta){
  currentMonth+=delta;
  if(currentMonth>11){currentMonth=0;currentYear++}
  if(currentMonth<0){currentMonth=11;currentYear--}
  renderCalendar();
}
function goToday(){
  currentYear=new Date().getFullYear();
  currentMonth=new Date().getMonth();
  renderCalendar();
}

function renderCalendar(){
  document.getElementById('month-label').textContent=currentYear+'年'+(currentMonth+1)+'月';
  const cal=document.getElementById('calendar');

  // Day of week headers
  const headers=['05','二','三','四','五','六','日'];
  let html=headers.map(h=>'<div class="cal-header">'+h+'</div>').join('');

  const firstDay=new Date(currentYear,currentMonth,1);
  let startDow=(firstDay.getDay()+6)%7; // Mon=0
  const daysInMonth=new Date(currentYear,currentMonth+1,0).getDate();

  // Build PnL map for this month
  const pnlMap={};
  for(const snap of allSnapshots){
    const d=snap.date;
    if(d&&d.startsWith(currentYear+'-'+String(currentMonth+1).padStart(2,'0'))){
      pnlMap[parseInt(d.slice(8,10))]=snap.dailyPnl||0;
    }
  }

  // Empty cells before first day
  for(let i=0;i<startDow;i++)html+='<div class="cal-day empty"></div>';

  // Day cells
  let monthPnl=0,winDays=0,loseDays=0,bestDay=0,worstDay=0;
  for(let d=1;d<=daysInMonth;d++){
    const pnl=pnlMap[d];
    if(pnl!==undefined){
      monthPnl+=pnl;
      if(pnl>0)winDays++;
      if(pnl<0)loseDays++;
      if(pnl>bestDay)bestDay=pnl;
      if(pnl<worstDay)worstDay=pnl;
      html+='<div class="cal-day" style="background:'+pnlColor(pnl)+'">';
      html+='<div class="day-num">'+d+'</div>';
      html+='<div class="day-val" style="color:'+(pnl>=0?'var(--green)':'var(--red)')+'">'+(pnl>=0?'+':'')+num(pnl)+'</div>';
      html+='<div class="tooltip">'+currentYear+'-'+String(currentMonth+1).padStart(2,'0')+'-'+String(d).padStart(2,'0')+'<br>07: '+(pnl>=0?'+':'')+num(pnl)+'</div>';
      html+='</div>';
    }else{
      html+='<div class="cal-day no-data"><div class="day-num">'+d+'</div></div>';
    }
  }
  cal.innerHTML=html;

  // KPIs
  const mpEl=document.getElementById('kpi-month-pnl');
  mpEl.textContent=(monthPnl>=0?'+':'')+num(monthPnl);
  mpEl.style.color=monthPnl>=0?'var(--green)':'var(--red)';
  document.getElementById('kpi-win-days').textContent=winDays;
  document.getElementById('kpi-lose-days').textContent=loseDays;
  const total=winDays+loseDays;
  document.getElementById('kpi-win-rate').textContent=total>0?(winDays/total*100).toFixed(1)+'%':'-';
  const bdEl=document.getElementById('kpi-best-day');
  bdEl.textContent='+'+num(bestDay);bdEl.style.color='var(--green)';
  const wdEl=document.getElementById('kpi-worst-day');
  wdEl.textContent=num(worstDay);wdEl.style.color='var(--red)';
}

async function reload(){
  try{
    const mode=document.getElementById('mode').value;
    const res=await fetch(B+'/quant/api/equity-history?mode='+mode);
    if(!res.ok)throw new Error('fetch failed');
    const result=await res.json();
    allSnapshots=(result.snapshots||result||[]).map(d=>({
      date:d.date||d.timestamp,
      dailyPnl:d.dailyPnl||0,
    }));
    renderCalendar();
  }catch(e){
    document.getElementById('calendar').innerHTML='<div style="grid-column:1/-1;text-align:center;padding:24px;color:var(--text-dim)">加载失败: '+esc(e.message)+'</div>';
  }
}
reload();
</script>
</body>
</html>`;
}

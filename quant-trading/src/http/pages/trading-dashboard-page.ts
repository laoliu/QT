/**
 * Trading Dashboard Page (UI-1) — 02070602
 *
 * 0505: 账户概览 KPI、持仓列表、当日盈亏、浮动盈亏等
 */
export function getTradingDashboardPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quant Trading 0207</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>96</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.breadcrumb{background:var(--bg);padding:8px 24px;font-size:12px;border-bottom:1px solid var(--border)}
header{display:flex;justify-content:space-between;align-items:center;padding:16px 24px;background:var(--bg2);border-bottom:1px solid var(--border)}
header h1{margin:0;font-size:20px}
.actions{display:flex;gap:8px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--accent);color:var(--accent)}
main{padding:20px 24px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}
.kpi-val{font-size:20px;font-weight:700}.kpi-label{font-size:11px;color:var(--text-dim);margin-top:4px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.card h2{margin:0 0 12px;font-size:15px}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:10px 8px;border-bottom:1px solid var(--border);text-align:left}th{color:var(--text-dim);font-size:11px}
tr:hover{background:rgba(255,255,255,.02)}
.up{color:var(--green)}.down{color:var(--red)}
.empty{padding:24px;text-align:center;color:var(--text-dim)}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.status-running{background:var(--green)}.status-stopped{background:var(--red)}.status-paused{background:var(--yellow)}
@media(max-width:640px){.kpis{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">92 07</a> / <a href="${b}/quant/admin/ui">99 管理面板</a> / <span>96 0207</span></div>
<header>
  <h1>96 0207</h1>
  <div class="actions">
    <button class="btn" onclick="refresh()">94 06</button>
    <a class="btn" href="${b}/quant/admin/ui">返回管理面板</a>
  </div>
</header>
<main>
  <div class="kpis" id="kpis">
    <div class="kpi"><div class="kpi-val" id="kpi-total">-</div><div class="kpi-label">03</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-cash">-</div><div class="kpi-label">可用资金</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-market-value">-</div><div class="kpi-label">05</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-daily-pnl">-</div><div class="kpi-label">当日盈亏</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-float-pnl">-</div><div class="kpi-label">浮动盈亏</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-positions">-</div><div class="kpi-label">02</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-engine">-</div><div class="kpi-label">0800</div></div>
  </div>

  <div class="card">
    <h2>0206</h2>
    <div id="positions-table"><div class="empty">加载中...</div></div>
  </div>

  <div class="card">
    <h2>今日交易</h2>
    <div id="trades-table"><div class="empty">加载中...</div></div>
  </div>
</main>

<script>
const B='${b}';
function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function pnlColor(v){return v>0?'var(--green)':v<0?'var(--red)':'var(--text)'}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

async function refresh(){
  try{
    // Fetch portfolio
    const res=await fetch(B+'/quant/api/portfolio');
    if(!res.ok)throw new Error('fetch failed');
    const data=await res.json();

    const acct=data.account||{};
    const pos=data.positions||[];

    document.getElementById('kpi-total').textContent='06'+num(acct.totalAssets||0);
    document.getElementById('kpi-cash').textContent='06'+num(acct.cash||0);
    const mv=pos.reduce((s,p)=>s+(p.marketValue||0),0);
    document.getElementById('kpi-market-value').textContent='06'+num(mv);
    const dailyPnl=pos.reduce((s,p)=>s+(p.dailyPnl||0),0);
    const el=document.getElementById('kpi-daily-pnl');
    el.textContent=(dailyPnl>=0?'+':'')+num(dailyPnl);
    el.style.color=pnlColor(dailyPnl);
    const floatPnl=pos.reduce((s,p)=>s+(p.unrealizedPnl||0),0);
    const el2=document.getElementById('kpi-float-pnl');
    el2.textContent=(floatPnl>=0?'+':'')+num(floatPnl);
    el2.style.color=pnlColor(floatPnl);
    document.getElementById('kpi-positions').textContent=pos.length;

    // Positions table
    if(pos.length===0){
      document.getElementById('positions-table').innerHTML='<div class="empty">1502</div>';
    }else{
      let h='<table><tr><th>代码</th><th>名称</th><th>数量</th><th>08</th><th>02</th><th>05</th><th>浮动盈亏</th><th>07%</th></tr>';
      for(const p of pos){
        const pnl=p.unrealizedPnl||0;
        const pnlPct=p.costPrice>0?((p.currentPrice-p.costPrice)/p.costPrice*100):0;
        h+='<tr><td>'+esc(p.symbol)+'</td><td>'+esc(p.name||'-')+'</td>';
        h+='<td>'+p.quantity+'</td><td>'+num(p.costPrice)+'</td><td>'+num(p.currentPrice)+'</td>';
        h+='<td>'+num(p.marketValue)+'</td>';
        h+='<td style="color:'+pnlColor(pnl)+'">'+(pnl>=0?'+':'')+num(pnl)+'</td>';
        h+='<td style="color:'+pnlColor(pnlPct)+'">'+(pnlPct>=0?'+':'')+pnlPct.toFixed(2)+'%</td></tr>';
      }
      h+='</table>';
      document.getElementById('positions-table').innerHTML=h;
    }

    // Engine status
    const engRes=await fetch(B+'/quant/api/engine/status').catch(()=>null);
    if(engRes&&engRes.ok){
      const eng=await engRes.json();
      const statusEl=document.getElementById('kpi-engine');
      const s=eng.status||'stopped';
      const labels={running:'运行中',stopped:'0509',paused:'05',starting:'启动中'};
      statusEl.innerHTML='<span class="status-dot status-'+s+'"></span>'+(labels[s]||s);
    }

    // Today's trades
    const trRes=await fetch(B+'/quant/api/orders?status=filled&limit=20').catch(()=>null);
    if(trRes&&trRes.ok){
      const trades=await trRes.json();
      const list=Array.isArray(trades)?trades:(trades.orders||[]);
      if(list.length===0){
        document.getElementById('trades-table').innerHTML='<div class="empty">1500</div>';
      }else{
        let h='<table><tr><th>02</th><th>代码</th><th>方向</th><th>18</th><th>数量</th><th>0800</th></tr>';
        for(const t of list.slice(0,20)){
          const time=t.filledTime||t.createTime||'';
          h+='<tr><td>'+esc(time.slice(11,19)||time)+'</td><td>'+esc(t.symbol)+'</td>';
          h+='<td>'+esc(t.side)+'</td><td>'+num(t.filledPrice||t.price)+'</td>';
          h+='<td>'+t.quantity+'</td><td>'+esc(t.status)+'</td></tr>';
        }
        h+='</table>';
        document.getElementById('trades-table').innerHTML=h;
      }
    }
  }catch(e){
    console.error(e);
    document.getElementById('positions-table').innerHTML='<div class="empty">加载失败: '+esc(e.message)+'</div>';
  }
}
refresh();
setInterval(refresh,30000);
</script>
</body>
</html>`;
}

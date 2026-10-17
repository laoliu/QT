/**
 * Order Approval Page (UI-8) — 订单审批页面
 *
 * 050502040702/18
 */
export function getOrderApprovalPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quant Trading 订单审批</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>73</text></svg>">
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
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}
.btn-danger{background:#da3633;border-color:#f85149;color:#fff}
.btn-danger:hover{background:#f85149}
main{padding:20px 24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.card h2{margin:0 0 12px;font-size:15px}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:10px 8px;border-bottom:1px solid var(--border);text-align:left}th{color:var(--text-dim);font-size:11px}
tr:hover{background:rgba(255,255,255,.02)}
.empty{padding:24px;text-align:center;color:var(--text-dim)}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.badge{padding:2px 8px;border-radius:999px;font-size:11px}
.badge-pending{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-confirmed{background:rgba(63,185,80,.15);color:var(--green)}
.badge-rejected{background:rgba(248,81,73,.15);color:var(--red)}
.select-all{margin-right:6px}
.toolbar{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap}
@media(max-width:640px){.toolbar{flex-direction:column;align-items:stretch}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">92 07</a> / <a href="${b}/quant/admin/ui">99 管理面板</a> / <span>73 订单审批</span></div>
<header>
  <h1>73 订单审批</h1>
  <div class="actions">
    <button class="btn" onclick="refresh()">94 06</button>
    <a class="btn" href="${b}/quant/admin/ui">返回管理面板</a>
  </div>
</header>
<main>
  <div class="card">
    <div class="toolbar">
      <h2>待审批订单 <span id="pending-count" style="color:var(--yellow)">(0)</span></h2>
      <div class="actions">
        <button class="btn btn-primary" onclick="batchAction('confirm')">批量确认</button>
        <button class="btn btn-danger" onclick="batchAction('reject')">批量拒绝</button>
      </div>
    </div>
    <div id="pending-table"><div class="empty">加载中...</div></div>
  </div>

  <div class="card">
    <h2>历史订单</h2>
    <div id="history-table"><div class="empty">加载中...</div></div>
  </div>
</main>
<div class="toast" id="toast"></div>

<script>
const B='${b}';
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function toast(msg,ms){ms=ms||2500;const el=document.getElementById('toast');el.textContent=msg;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),ms)}

async function refresh(){
  try{
    const res=await fetch(B+'/quant/api/orders?limit=100');
    if(!res.ok)throw new Error('0002');
    const data=await res.json();
    const orders=Array.isArray(data)?data:(data.orders||[]);

    const pending=orders.filter(o=>o.status==='pending_confirmation'||o.status==='pending');
    const history=orders.filter(o=>o.status!=='pending_confirmation'&&o.status!=='pending');

    document.getElementById('pending-count').textContent='('+pending.length+')';

    // Pending table
    if(pending.length===0){
      document.getElementById('pending-table').innerHTML='<div class="empty">无待审批订单</div>';
    }else{
      let h='<table><tr><th><input type="checkbox" class="select-all" onchange="toggleAll(this)"></th><th>02</th><th>代码</th><th>方向</th><th>18</th><th>数量</th><th>策略</th><th>操作</th></tr>';
      for(const o of pending){
        h+='<tr><td><input type="checkbox" class="order-cb" value="'+esc(o.orderId)+'"></td>';
        h+='<td>'+esc((o.createTime||'').slice(11,19))+'</td>';
        h+='<td>'+esc(o.symbol)+'</td>';
        h+='<td>'+esc(o.side)+'</td>';
        h+='<td>'+num(o.price)+'</td>';
        h+='<td>'+o.quantity+'</td>';
        h+='<td>'+esc(o.strategyId||'-')+'</td>';
        h+='<td>';
        h+='<button class="btn btn-primary" style="padding:4px 8px;font-size:11px" onclick="action(\\''+esc(o.orderId)+'\\',\\'confirm\\')">02</button> ';
        h+='<button class="btn btn-danger" style="padding:4px 8px;font-size:11px" onclick="action(\\''+esc(o.orderId)+'\\',\\'reject\\')">18</button>';
        h+='</td></tr>';
      }
      h+='</table>';
      document.getElementById('pending-table').innerHTML=h;
    }

    // History table
    if(history.length===0){
      document.getElementById('history-table').innerHTML='<div class="empty">无历史订单</div>';
    }else{
      let h='<table><tr><th>02</th><th>代码</th><th>方向</th><th>18</th><th>数量</th><th>0800</th><th>策略</th></tr>';
      for(const o of history.slice(0,50)){
        const statusClass=o.status==='filled'?'badge-confirmed':o.status==='rejected'?'badge-rejected':'badge-pending';
        h+='<tr><td>'+esc((o.createTime||'').slice(0,19))+'</td>';
        h+='<td>'+esc(o.symbol)+'</td><td>'+esc(o.side)+'</td>';
        h+='<td>'+num(o.filledPrice||o.price)+'</td><td>'+o.quantity+'</td>';
        h+='<td><span class="badge '+statusClass+'">'+esc(o.status)+'</span></td>';
        h+='<td>'+esc(o.strategyId||'-')+'</td></tr>';
      }
      h+='</table>';
      document.getElementById('history-table').innerHTML=h;
    }
  }catch(e){
    document.getElementById('pending-table').innerHTML='<div class="empty">'+esc(e.message)+'</div>';
  }
}

function toggleAll(el){
  document.querySelectorAll('.order-cb').forEach(cb=>cb.checked=el.checked);
}

async function action(orderId,act){
  try{
    const url=B+'/quant/api/orders/'+orderId+'/'+act;
    const res=await fetch(url,{method:'POST'});
    if(!res.ok)throw new Error(act+' 02');
    toast(act==='confirm'?'02':'0418');
    refresh();
  }catch(e){toast(e.message)}
}

async function batchAction(act){
  const ids=[...document.querySelectorAll('.order-cb:checked')].map(cb=>cb.value);
  if(ids.length===0){toast('0539');return}
  for(const id of ids){await action(id,act)}
}

refresh();
setInterval(refresh,15000);
</script>
</body>
</html>`;
}

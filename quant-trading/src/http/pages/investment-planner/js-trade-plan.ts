/**
 * Investment Planner — trade plan tab client-side JS.
 *
 * Provides plan creation, editing, and execution tracking UI logic.
 */
export function getTradePlanJs(): string {
  return `
/* ══════ TRADE PLAN ══════ */
function loadTradePlans(){
  fetch(B+'/quant/trade-plans?limit=20')
    .then(function(r){return r.json()})
    .then(function(d){
      if(d.status==='ok'&&d.plans) renderTradePlans(d.plans);
    })
    .catch(function(e){console.warn('trade-plans load error',e)});
}

function renderTradePlans(plans){
  var el=document.getElementById('trade-plan-list');
  if(!el)return;
  if(!plans.length){el.innerHTML='<p class="muted">暂无交易计划</p>';return;}
  var html=plans.map(function(p){
    var status=p.status==='open'?'🟢 进行中':'⬜ 已结束';
    var count=p.stocks?p.stocks.length:0;
    return '<div class="card" onclick="loadTradePlanDetail(\\''+p.id+'\\')">'+
      '<div><strong>'+p.date+'</strong> '+status+'</div>'+
      '<div>'+count+'只标的 | 预算¥'+p.budget+'</div>'+
      (p.notes?'<div class="muted">'+p.notes+'</div>':'')+
      '</div>';
  }).join('');
  el.innerHTML=html;
}

function loadTradePlanDetail(id){
  fetch(B+'/quant/trade-plans/detail?id='+encodeURIComponent(id))
    .then(function(r){return r.json()})
    .then(function(d){
      if(d.status==='ok'&&d.plan) renderTradePlanDetail(d.plan);
    })
    .catch(function(e){console.warn('trade-plan detail error',e)});
}

function renderTradePlanDetail(plan){
  var el=document.getElementById('trade-plan-detail');
  if(!el)return;
  var rows=plan.stocks.map(function(s){
    var exec=s.executed?'✅':'⬜';
    return '<tr>'+
      '<td>'+exec+'</td>'+
      '<td>'+s.code+'</td>'+
      '<td>'+(s.name||'')+'</td>'+
      '<td>'+s.direction+'</td>'+
      '<td>'+(s.targetPrice||'-')+'</td>'+
      '<td>'+(s.quantity||'-')+'</td>'+
      '<td>'+(s.reason||'')+'</td>'+
      '</tr>';
  }).join('');
  el.innerHTML='<h3>'+plan.date+' 交易计划</h3>'+
    '<table><thead><tr><th>执行</th><th>代码</th><th>名称</th><th>方向</th><th>目标价</th><th>数量</th><th>理由</th></tr></thead>'+
    '<tbody>'+rows+'</tbody></table>';
}
`;
}

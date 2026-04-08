/**
 * Investment Planner — trade plan tab client-side JS.
 *
 * Provides plan creation, editing, and execution tracking UI logic.
 */
export function getTradePlanJs(): string {
  return `
/* ══════ TRADE PLAN ══════ */
function loadTodayPlan(){
  var el=document.getElementById('today-plan-content');
  if(!el)return;
  fetch(B+'/quant/trade-plans/today')
    .then(function(r){return r.json()})
    .then(function(d){
      if(d.status==='ok'&&d.plan){
        var p=d.plan;
        var dirLabel={buy:'买入',sell:'卖出'};
        var rows=(p.stocks||[]).map(function(s){
          var exec=s.executed?'<span style="color:#48bb78">✅ 已执行</span>':'<span style="color:var(--text-dim)">⬜ 待执行</span>';
          return '<tr>'+
            '<td>'+exec+'</td>'+
            '<td><strong>'+s.code+'</strong></td>'+
            '<td>'+(s.name||'')+'</td>'+
            '<td>'+(dirLabel[s.direction]||s.direction)+'</td>'+
            '<td>'+(s.targetPrice?'¥'+s.targetPrice:'-')+'</td>'+
            '<td>'+(s.quantity||'-')+'</td>'+
            '<td style="color:var(--text-dim);font-size:12px">'+(s.reason||'')+'</td>'+
            '</tr>';
        }).join('');
        var execCount=(p.stocks||[]).filter(function(s){return s.executed}).length;
        var total=(p.stocks||[]).length;
        el.innerHTML=
          '<div style="display:flex;gap:16px;margin-bottom:10px;font-size:13px;color:var(--text-dim)">'+
            '<span>📅 '+p.date+'</span>'+
            '<span>💰 预算 ¥'+p.budget+'</span>'+
            '<span>📊 '+execCount+'/'+total+' 已执行</span>'+
            (p.notes?'<span>📝 '+esc(p.notes)+'</span>':'')+
          '</div>'+
          '<table><thead><tr><th>状态</th><th>代码</th><th>名称</th><th>方向</th><th>目标价</th><th>数量</th><th>理由</th></tr></thead>'+
          '<tbody>'+rows+'</tbody></table>'+
          '<div style="margin-top:8px">'+
            '<button class="btn btn-sm" onclick="loadTradePlanDetail(\\''+p.id+'\\')">📋 查看详情</button>'+
            (p.status==='open'?'<button class="btn btn-sm" onclick="showEditPlanModal(\\''+p.id+'\\')">✏️ 编辑</button>':'')+
          '</div>';
      }else{
        el.innerHTML='<p style="color:var(--text-dim)">今日暂无交易计划，点击右上角「新建计划」创建。</p>';
      }
    })
    .catch(function(e){
      console.warn('today-plan load error',e);
      el.innerHTML='<p style="color:#e53e3e">加载失败: '+esc(String(e))+'</p>';
    });
}

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

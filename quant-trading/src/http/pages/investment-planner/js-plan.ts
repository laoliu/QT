/** Investment Planner — investment plan generation, rendering, and history management */
export function getPlanJs(): string {
  return `
function restoreCachedPlan(){
  try{
    var raw=sessionStorage.getItem('openclaw-plan-cache');
    if(raw&&!currentPlan){
      currentPlan=JSON.parse(raw);
      renderPlan(currentPlan);
    }
  }catch(e){}
}

/* ══════ PLAN ══════ */
function generatePlan(){
  var btn=document.getElementById('btn-gen-plan');
  btn.disabled=true;btn.textContent='⏳ 正在分析市场…';
  toast('正在分析全市场股票，请稍候…');

  var prefSel=document.getElementById('strategy-pref');
  var prefVal=prefSel.value;
  var stratPref=prefVal;
  var composedId=null;
  // For composed strategies, send the display name as preference and the ID separately
  if(prefVal.indexOf('composed:')===0){
    composedId=prefVal.slice(9);
    var selOpt=prefSel.options[prefSel.selectedIndex];
    stratPref=selOpt?selOpt.textContent:'mixed';
  }

  var body={
    action:'plan',
    initial_capital:Number(document.getElementById('initial-capital').value)||500000,
    target_capital:Number(document.getElementById('target-capital').value)||1000000,
    timeframe_months:Number(document.getElementById('timeframe').value)||12,
    risk_tolerance:document.getElementById('risk-tolerance').value,
    strategy_preference:stratPref,
    composed_strategy_id:composedId
  };

  api('/quant/admin/api/investment-plan',{method:'POST',body:JSON.stringify(body),_timeout:60000}).then(function(d){
    btn.disabled=false;btn.textContent='🎯 生成投资计划';
    currentPlan=d;
    currentPlanVersion=null;
    renderPlan(d);
    // Cache for browser back
    try{sessionStorage.setItem('openclaw-plan-cache',JSON.stringify(d))}catch(e){}
    // Auto-save to server history
    api('/quant/admin/api/plan-history',{method:'POST',body:JSON.stringify({plan:d,params:body})}).then(function(r){
      loadPlanHistory();
      // Set version from latest entry
      if(r&&r.status==='ok'){
        api('/quant/admin/api/plan-history').then(function(h){
          if(h.entries&&h.entries.length>0){
            currentPlanVersion=h.entries[0].version||null;
            updatePlanVersionBadge();
          }
        }).catch(function(){});
      }
    }).catch(function(){});
    toast('✅ 投资计划已生成');
  }).catch(function(){btn.disabled=false;btn.textContent='🎯 生成投资计划'});
}

function renderPlan(d){
  var p=d.plan;var s=d.summary;

  // Risk card
  var rc=document.getElementById('plan-risk-card');rc.style.display='block';
  var riskColor=p.riskLevel==='极高'?'red':p.riskLevel==='高'?'orange':p.riskLevel==='中'?'yellow':'green';
  document.getElementById('risk-assessment').innerHTML=
    '<div style="margin-bottom:8px"><span class="badge badge-'+riskColor+'" style="font-size:14px;padding:4px 12px">风险等级: '+esc(p.riskLevel)+'</span> '+
    '<span class="badge badge-blue" style="font-size:14px;padding:4px 12px">可行性: '+esc(p.feasibility)+'</span></div>'+
    '<p style="font-size:12px;color:var(--text-dim);line-height:1.6">'+esc(p.riskWarning)+'</p>';

  // KPIs
  document.getElementById('plan-kpis').innerHTML=[
    kpiHtml('¥'+numInt(p.initialCapital),'初始资金'),
    kpiHtml('¥'+numInt(p.targetCapital),'目标资金'),
    kpiHtml(p.requiredReturn+'%','总收益目标'),
    kpiHtml(p.monthlyReturnNeeded.toFixed(2)+'%','月均收益'),
    kpiHtml('<span class="badge badge-blue">'+esc(p.strategyLabel)+'</span>','推荐策略'),
    kpiHtml(esc(p.tradeFrequency),'交易频率'),
    kpiHtml(s.totalPositions+'ֻ','持仓数'),
    kpiHtml(s.cashReservePct+'%','现金比例'),
  ].join('');

  // Allocation table
  var tbody=document.querySelector('#allocation-table tbody');
  tbody.innerHTML=d.portfolio.map(function(pos){
    // Compute batch entry prices from price & stopLoss
    var mid=(pos.price+pos.stopLoss)/2;
    var entryHtml='<div style="font-size:11px;line-height:1.5">'+
      '<div>①'+num(pos.price)+' <span style="color:var(--text-dim)">(40%)</span></div>'+
      '<div>②'+num(mid)+' <span style="color:var(--text-dim)">(30%)</span></div>'+
      '<div>③'+num(pos.stopLoss*1.02)+' <span style="color:var(--text-dim)">(30%)</span></div></div>';
    return '<tr>'+
      '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(pos.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(pos.symbol)+'</a></td>'+
      '<td>'+esc(pos.name)+'</td>'+
      '<td><span class="badge badge-purple">'+pos.score+'</span></td>'+
      '<td style="font-size:11px"><div style="display:flex;flex-wrap:wrap;gap:2px">'+(pos.reasons||[]).map(function(r){return '<span class="badge badge-blue">'+esc(r)+'</span>'}).join('')+'</div></td>'+
      '<td>'+num(pos.price)+'</td>'+
      '<td>'+numInt(pos.shares)+'</td>'+
      '<td>¥'+numInt(pos.cost)+'</td>'+
      '<td>'+pos.weight+'%</td>'+
      '<td>'+entryHtml+'</td>'+
      '<td style="color:var(--red)">'+num(pos.stopLoss)+'</td>'+
      '<td style="color:var(--green)">'+num(pos.takeProfit)+'</td>'+
      '</tr>';
  }).join('')||'<tr><td colspan="11" style="text-align:center;color:var(--text-dim)">暂无推荐</td></tr>';

  // Rules
  var rulesIcons={'stopLoss':'🛑','takeProfit':'💰','positionSize':'📏','rebalance':'🔄','cashReserve':'💵','entryRule':'📈','exitRule':'📉'};
  var rulesLabels={'stopLoss':'止损规则','takeProfit':'止盈规则','positionSize':'仓位控制','rebalance':'定期调仓','cashReserve':'现金储备','entryRule':'入场条件','exitRule':'出场条件'};
  var rl=document.getElementById('rules-list');
  rl.innerHTML=Object.keys(d.rules).map(function(k){
    return '<li><span class="rules-icon">'+(rulesIcons[k]||'📌')+'</span><div><strong>'+esc(rulesLabels[k]||k)+'</strong><br><span style="color:var(--text-dim)">'+esc(d.rules[k])+'</span></div></li>';
  }).join('');

  // Monitoring schedule
  var ml=document.getElementById('monitoring-schedule');
  var monIcons={'preMarket':'🌅','openWatch':'🔔','midDay':'☀️','afternoon':'🌤️','close':'🌆','evening':'🌙'};
  ml.innerHTML=Object.keys(d.monitoring).map(function(k){
    return '<li><span class="rules-icon">'+(monIcons[k]||'⏰')+'</span><div>'+esc(d.monitoring[k])+'</div></li>';
  }).join('');

  // Candidate pool
  var cpt=document.querySelector('#candidate-pool-table tbody');
  cpt.innerHTML=(d.candidatePool||[]).map(function(c){
    return '<tr>'+
      '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(c.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(c.symbol)+'</a></td>'+
      '<td>'+esc(c.name)+'</td>'+
      '<td>'+num(c.price)+'</td>'+
      '<td>'+num(c.pe)+'</td>'+
      '<td><span class="badge badge-purple">'+c.score+'</span></td>'+
      '<td style="font-size:11px">'+(c.reasons||[]).slice(0,3).map(function(r){return esc(r)}).join(', ')+'</td>'+
      '<td><button class="btn btn-sm" onclick="viewStock(\\''+esc(c.symbol)+'\\')">查看</button></td>'+
      '</tr>';
  }).join('')||'<tr><td colspan="7" style="text-align:center;color:var(--text-dim)">暂无候选</td></tr>';

  updatePlanVersionBadge();
  document.getElementById('plan-result').style.display='block';
}

function updatePlanVersionBadge(){
  var el=document.getElementById('plan-version-badge');
  if(!el)return;
  if(currentPlanVersion){
    el.innerHTML='<span class="badge badge-green" style="font-size:11px;font-family:monospace">'+esc(currentPlanVersion)+'</span>';
  }else if(currentPlan){
    el.innerHTML='<span class="badge badge-yellow" style="font-size:11px">未保存</span>';
  }else{
    el.innerHTML='';
  }
}

function planStocksToRecommend(){
  if(!currentPlan||!currentPlan.portfolio||!currentPlan.portfolio.length){toast('⚠️ 请先生成投资计划');_activateTab('plan');history.pushState(null,null,'#plan');return}
  var codes=currentPlan.portfolio.map(function(p){return p.symbol}).join(',');
  document.getElementById('rec-symbols').value=codes;
  localStorage.setItem('openclaw-rec-symbols',codes);
  // Switch to recommend tab
  activateTab('recommend');
  history.pushState(null,null,'#recommend');
  toast('✅ 已导入 '+currentPlan.portfolio.length+' 只股票到建仓推荐');
}

function importFromScreener(){
  toast('🔄 正在加载选股结果…');
  api('/quant/api/stocklist').then(function(r){
    var data=r.data||r;
    if(!data||!data.length){toast('⚠️ 暂无选股结果，请先运行 /选股 命令');return}
    var codes=data.map(function(s){return s.replace(/\.[A-Z]{2}$/,'')}).join(',');
    document.getElementById('rec-symbols').value=codes;
    localStorage.setItem('openclaw-rec-symbols',codes);
    toast('✅ 已导入 '+data.length+' 只选股结果');
  }).catch(function(){toast('❌ 加载选股结果失败')});
}

function loadPlanHistory(){
  var panel=document.getElementById('plan-history-panel');
  var statusEl=document.getElementById('plan-history-status');
  if(!panel||!statusEl)return;
  statusEl.textContent='加载历史…';
  api('/quant/admin/api/plan-history').then(function(d){
    if(!d.entries||d.entries.length===0){
      statusEl.textContent='暂无历史记录，请先生成投资计划';
      panel.style.display='none';
      return;
    }
    statusEl.textContent='共 '+d.count+' 个版本';
    panel.style.display='block';

    // Top stocks KPIs
    var topHtml='';
    if(d.topStocks&&d.topStocks.length>0){
      topHtml='<div style="margin-bottom:12px"><h4 style="margin:0 0 6px">🏆 高频入选股票</h4><div style="display:flex;flex-wrap:wrap;gap:4px">';
      d.topStocks.slice(0,10).forEach(function(s){
        var badge=s.count>=5?'badge-green':s.count>=3?'badge-blue':'badge-yellow';
        topHtml+='<a href="'+B+'/quant/admin/stock?symbol='+esc(s.code)+'" target="_blank" class="badge '+badge+'" style="text-decoration:none;padding:4px 8px;cursor:pointer">'+esc(s.code)+' <sup>'+s.count+'次('+s.pct+'%)</sup></a>';
      });
      topHtml+='</div></div>';
    }
    document.getElementById('plan-history-top-stocks').innerHTML=topHtml;

    // History table with version management
    var tbody=document.querySelector('#plan-history-table tbody');
    var rows='';
    d.entries.forEach(function(e){
      var dt=e.timestamp?new Date(e.timestamp).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'-';
      var ver=e.version||e.id.substring(0,10);
      var sm=e.summary||{};
      var riskBadge=sm.riskLevel==='极高'||sm.riskLevel==='高'?'badge-red':sm.riskLevel==='中'?'badge-yellow':'badge-green';
      var labelHtml=e.label?'<span style="color:var(--accent);font-size:11px">'+esc(e.label)+'</span>':'<span style="color:var(--text-dim);font-size:11px;cursor:pointer" onclick="editPlanLabel(\\''+esc(e.id)+'\\',this)">点击添加备注</span>';
      rows+='<tr data-plan-id="'+esc(e.id)+'">'+
        '<td><span class="badge badge-purple" style="font-size:11px;font-family:monospace">'+esc(ver)+'</span></td>'+
        '<td style="white-space:nowrap">'+esc(dt)+'</td>'+
        '<td>'+labelHtml+'</td>'+
        '<td>'+esc(e.strategy||'-')+'</td>'+
        '<td>¥'+numInt(e.initialCapital)+'</td>'+
        '<td>¥'+numInt(e.targetCapital)+'</td>'+
        '<td>'+e.stockCount+'ֻ</td>'+
        '<td><span class="badge '+riskBadge+'">'+esc(String(sm.riskLevel||'-'))+'</span></td>'+
        '<td>'+esc(String(sm.feasibility||'-'))+'</td>'+
        '<td style="font-size:11px;max-width:200px"><div style="display:flex;flex-wrap:wrap;gap:2px">'+
          e.stocks.slice(0,6).map(function(c){return '<span class="badge badge-blue" style="font-size:10px">'+esc(c)+'</span>'}).join('')+
          (e.stocks.length>6?'<span class="badge" style="font-size:10px">+'+( e.stocks.length-6)+'</span>':'')+
        '</div></td>'+
        '<td><div class="btn-group" style="margin:0;flex-wrap:nowrap">'+
          '<button class="btn btn-sm" onclick="viewPlanDetail(\\''+esc(e.id)+'\\')">📋 查看</button>'+
          '<button class="btn btn-sm" onclick="importPlanToRecommendById(\\''+esc(e.id)+'\\')">📥 导入</button>'+
          '<button class="btn btn-sm" onclick="monitorPlanById(\\''+esc(e.id)+'\\')">🚀 监控</button>'+
        '</div></td>'+
        '</tr>';
    });
    tbody.innerHTML=rows;
    // Auto-load latest plan's full data if no plan is currently loaded
    if(!currentPlan&&d.entries.length>0){
      var latest=d.entries[0];
      api('/quant/admin/api/plan-history?id='+encodeURIComponent(latest.id)).then(function(r){
        if(r.status==='ok'&&r.entry&&r.entry.fullPlan&&!currentPlan){
          currentPlan=r.entry.fullPlan;
          currentPlanVersion=r.entry.version||null;
          renderPlan(currentPlan);
        }
      }).catch(function(){});
    }
  }).catch(function(err){statusEl.textContent='加载失败'});
}

var viewingPlanId=null;

function viewPlanDetail(planId){
  toast('加载计划详情…');
  api('/quant/admin/api/plan-history?id='+encodeURIComponent(planId)).then(function(d){
    if(d.status!=='ok'||!d.entry){toast('❌ '+(d.message||'加载失败'));return}
    var e=d.entry;
    viewingPlanId=planId;
    var ver=e.version||e.id.substring(0,10);
    var dt=e.timestamp?new Date(e.timestamp).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'-';
    document.getElementById('plan-detail-title').textContent='📋 投资计划 '+ver;
    var labelText=e.label?'<strong>备注:</strong> '+esc(e.label)+' | ':'';
    document.getElementById('plan-detail-meta').innerHTML=
      '<span style="font-size:12px;color:var(--text-dim)">'+labelText+'生成时间: '+esc(dt)+' | 策略: '+esc(e.strategy||'-')+'</span>';

    // Render plan if full data available
    var fp=e.fullPlan;
    if(fp&&fp.plan){
      var p=fp.plan;var s=fp.summary||{};
      document.getElementById('plan-detail-kpis').innerHTML=[
        kpiHtml('¥'+numInt(p.initialCapital),'初始资金'),
        kpiHtml('¥'+numInt(p.targetCapital),'目标资金'),
        kpiHtml((p.requiredReturn||0)+'%','收益目标'),
        kpiHtml(s.totalPositions||e.stockCount,'持仓数'),
      ].join('');
      var dtb=document.querySelector('#plan-detail-table tbody');
      dtb.innerHTML=(fp.portfolio||[]).map(function(pos){
        return '<tr>'+
          '<td><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(String(pos.symbol||''))+'" target="_blank" style="color:var(--accent)">'+esc(pos.symbol)+'</a></td>'+
          '<td>'+esc(pos.name)+'</td>'+
          '<td><span class="badge badge-purple">'+(pos.score||'-')+'</span></td>'+
          '<td style="font-size:11px">'+(pos.reasons||[]).slice(0,3).map(function(r){return '<span class="badge badge-blue" style="font-size:10px">'+esc(r)+'</span>'}).join(' ')+'</td>'+
          '<td>'+num(pos.price)+'</td>'+
          '<td>'+numInt(pos.shares)+'</td>'+
          '<td>¥'+numInt(pos.cost)+'</td>'+
          '<td>'+(pos.weight||0)+'%</td>'+
          '<td style="color:var(--red)">'+num(pos.stopLoss)+'</td>'+
          '<td style="color:var(--green)">'+num(pos.takeProfit)+'</td>'+
          '</tr>';
      }).join('')||'<tr><td colspan="10" style="text-align:center;color:var(--text-dim)">无持仓数据</td></tr>';
    }else{
      document.getElementById('plan-detail-kpis').innerHTML=
        kpiHtml(e.stockCount,'持仓数')+kpiHtml('¥'+numInt(e.initialCapital),'初始资金')+kpiHtml('¥'+numInt(e.targetCapital),'目标资金');
      document.querySelector('#plan-detail-table tbody').innerHTML=
        e.stocks.map(function(c){return '<tr><td><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(String(c||''))+'" target="_blank" style="color:var(--accent)">'+esc(c)+'</a></td><td colspan="9" style="color:var(--text-dim);font-size:11px">（旧版本，无详细数据）</td></tr>'}).join('');
    }
    document.getElementById('plan-detail-modal').style.display='block';
  }).catch(function(){toast('❌ 加载失败')});
}

function closePlanDetail(){
  document.getElementById('plan-detail-modal').style.display='none';
  viewingPlanId=null;
}

function restoreHistoryPlan(){
  if(!viewingPlanId){toast('⚠️ 无选中计划');return}
  api('/quant/admin/api/plan-history?id='+encodeURIComponent(viewingPlanId)).then(function(d){
    if(d.status!=='ok'||!d.entry||!d.entry.fullPlan){toast('⚠️ 此版本无完整数据，无法恢复');return}
    currentPlan=d.entry.fullPlan;
    currentPlanVersion=d.entry.version||null;
    renderPlan(currentPlan);
    try{sessionStorage.setItem('openclaw-plan-cache',JSON.stringify(currentPlan))}catch(e){}
    closePlanDetail();
    toast('✅ 已恢复计划 '+(d.entry.version||''));
  }).catch(function(){toast('❌ 恢复失败')});
}

function quickRestorePlan(planId){
  api('/quant/admin/api/plan-history?id='+encodeURIComponent(planId)).then(function(d){
    if(d.status!=='ok'||!d.entry||!d.entry.fullPlan){toast('⚠️ 此版本无完整数据，无法恢复');return}
    currentPlan=d.entry.fullPlan;
    currentPlanVersion=d.entry.version||null;
    renderPlan(currentPlan);
    try{sessionStorage.setItem('openclaw-plan-cache',JSON.stringify(currentPlan))}catch(e){}
    toast('✅ 已恢复计划 '+(d.entry.version||''));
  }).catch(function(){toast('❌ 恢复失败')});
}

function importPlanToRecommendById(planId){
  if(!planId){toast('⚠️ 无选中计划');return}
  api('/quant/admin/api/plan-history?id='+encodeURIComponent(planId)).then(function(d){
    if(d.status!=='ok'||!d.entry||!d.entry.fullPlan){toast('⚠️ 此版本无完整数据');return}
    var fp=d.entry.fullPlan;
    if(!fp.portfolio||!fp.portfolio.length){toast('⚠️ 此版本无持仓数据');return}
    var codes=fp.portfolio.map(function(p){return p.symbol}).join(',');
    var recEl=document.getElementById('rec-symbols');
    if(recEl)recEl.value=codes;
    localStorage.setItem('openclaw-rec-symbols',codes);
    closePlanDetail();
    activateTab('recommend');
    history.pushState(null,null,'#recommend');
    toast('✅ 已从 '+(d.entry.version||'历史版本')+' 导入 '+fp.portfolio.length+' 只股票到建仓推荐');
  }).catch(function(e){console.error('导入失败',e);toast('❌ 导入失败: '+(e&&e.message||'未知错误'))});
}
function importHistoryPlanToRecommend(){importPlanToRecommendById(viewingPlanId)}

function monitorPlanById(planId){
  api('/quant/admin/api/plan-history?id='+encodeURIComponent(planId)).then(function(d){
    if(d.status!=='ok'||!d.entry||!d.entry.fullPlan){toast('⚠️ 此版本无完整数据，无法监控');return}
    currentPlan=d.entry.fullPlan;
    currentPlanVersion=d.entry.version||null;
    renderPlan(currentPlan);
    try{sessionStorage.setItem('openclaw-plan-cache',JSON.stringify(currentPlan))}catch(e){}
    closePlanDetail();
    startMonitoring();
    toast('✅ 已加载 '+(d.entry.version||'')+' 并开始监控');
  }).catch(function(){toast('❌ 操作失败')});
}
function monitorHistoryPlan(){monitorPlanById(viewingPlanId)}

function deleteHistoryPlan(){
  if(!viewingPlanId)return;
  if(!confirm('确定要删除此投资计划版本？此操作不可撤销。'))return;
  api('/quant/admin/api/plan-history?id='+encodeURIComponent(viewingPlanId),{method:'DELETE'}).then(function(d){
    if(d.status==='ok'){
      closePlanDetail();
      loadPlanHistory();
      toast('✅ 已删除');
    }else{toast('❌ '+(d.message||'删除失败'))}
  }).catch(function(){toast('❌ 删除失败')});
}

function editPlanLabel(planId,el){
  var label=prompt('为此计划添加备注/标签:','');
  if(label===null)return;
  api('/quant/admin/api/plan-history?id='+encodeURIComponent(planId),{method:'PATCH',body:JSON.stringify({label:label})}).then(function(d){
    if(d.status==='ok'){
      el.textContent=label||'点击添加备注';
      el.style.color=label?'var(--accent)':'var(--text-dim)';
      toast('✅ 备注已更新');
    }else{toast('❌ '+(d.message||'更新失败'))}
  }).catch(function(){toast('❌ 更新失败')});
}

`;
}

/** Investment Planner — institution activity scanning, Top3, manual confirmation, and history */
export function getInstitutionJs(): string {
  return `
function parseSymbolList(raw){
  if(!raw)return [];
  return raw.replace(/，/g,',').split(/[\s,]+/).map(function(s){return s.trim()}).filter(Boolean);
}

function institutionActionBadge(tag){
  if(tag==='可试跟')return '<span class="badge badge-green">可试跟</span>';
  if(tag==='可观察')return '<span class="badge badge-blue">可观察</span>';
  if(tag==='避免追入')return '<span class="badge badge-yellow">避免追入</span>';
  if(tag==='高风险钓鱼')return '<span class="badge badge-red">高风险钓鱼</span>';
  return '<span class="badge badge-orange">中性观察</span>';
}

function evidenceTypeBadge(tp){
  if(tp==='build')return '<span class="badge badge-green">建仓</span>';
  if(tp==='bait')return '<span class="badge badge-red">钓鱼</span>';
  if(tp==='distribution')return '<span class="badge badge-orange">派发</span>';
  return '<span class="badge badge-blue">背景</span>';
}

function usePlanForInstitution(){
  if(!currentPlan||!currentPlan.portfolio||!currentPlan.portfolio.length){toast('⚠️ 当前没有可导入的投资计划');return}
  var codes=currentPlan.portfolio.map(function(p){return p.symbol}).join(',');
  document.getElementById('inst-symbols').value=codes;
  localStorage.setItem('openclaw-inst-symbols',codes);
  toast('✅ 已导入 '+currentPlan.portfolio.length+' 只计划股票');
}

function renderInstitutionHistoryBadges(targetId,items,emptyText,badgeClass,transform){
  var el=document.getElementById(targetId);
  items=items||[];
  if(!items.length){
    el.innerHTML='<span style="font-size:12px;color:var(--text-dim)">'+esc(emptyText||'暂无')+'</span>';
    return;
  }
  el.innerHTML=items.slice(0,16).map(function(item){
    var label=transform?transform(item):String(item);
    var code=typeof item==='object'&&item.code?item.code:String(item);
    return '<a href="'+B+'/quant/admin/stock?symbol='+esc(code)+'" target="_blank" class="badge '+(badgeClass||'badge-blue')+'" style="text-decoration:none;padding:4px 8px;cursor:pointer">'+esc(label)+'</a>';
  }).join('');
}

function getInstitutionMetricValues(rows,key){
  rows=Array.isArray(rows)?rows:[];
  return rows.map(function(row){
    var value=row&&row[key];
    return value==null?null:Number(value);
  }).filter(function(value){
    return value!=null && Number.isFinite(value);
  });
}

function averageInstitutionMetric(rows,key){
  var values=getInstitutionMetricValues(rows,key);
  if(!values.length)return null;
  var total=values.reduce(function(sum,value){return sum+value;},0);
  return total/values.length;
}

function countInstitutionManualFlag(rows,flag){
  rows=Array.isArray(rows)?rows:[];
  return rows.filter(function(row){
    var confirmation=normalizeInstitutionManualConfirmation(row&&row.manualConfirmation);
    if(!confirmation)return false;
    if(flag==='restructure')return confirmation.restructureLevel&&confirmation.restructureLevel!=='none';
    return Boolean(confirmation[flag]);
  }).length;
}

function toggleInstitutionPeakLeaderSort(peakType){
  if(!institutionHistoryCachedMetrics)return;
  var currentSort=institutionHistoryPeakSortBy[peakType]||'count';
  var nextSort=currentSort==='count'?'score':'count';
  institutionHistoryPeakSortBy[peakType]=nextSort;
  var metricsData=institutionHistoryCachedMetrics;
  var leaderKey='top'+peakType.charAt(0).toUpperCase()+peakType.slice(1)+'Leaders';
  if(peakType==='customer')leaderKey='topCustomerLeaders';
  if(peakType==='patent')leaderKey='topPatentLeaders';
  if(peakType==='order')leaderKey='topOrderLeaders';
  if(peakType==='restructure')leaderKey='topRestructureLeaders';
  var leaderList=Array.isArray(metricsData[leaderKey])?metricsData[leaderKey]:[];
  var sorted=leaderList.slice().sort(function(a,b){
    if(nextSort==='score'){
      var diff=Number(b.score||0)-Number(a.score||0);
      if(diff!==0)return diff;
      return Number(b.count||0)-Number(a.count||0);
    }else{
      var diff=Number(b.count||0)-Number(a.count||0);
      if(diff!==0)return diff;
      return Number(b.score||0)-Number(a.score||0);
    }
  });
  var elemId='inst-history-'+peakType+'-peaks';
  var labelId='inst-peak-'+peakType+'-sort-label';
  var labelEl=document.getElementById(labelId);
  if(labelEl)labelEl.textContent=nextSort==='score'?'分数':'次数';
  var badgeClass='badge-orange';
  if(peakType==='customer')badgeClass='badge-blue';
  if(peakType==='patent')badgeClass='badge-purple';
  if(peakType==='restructure')badgeClass='badge-green';
  if(peakType==='order')badgeClass='badge-orange';
  if(peakType==='restructure'){
    renderInstitutionHistoryBadges(elemId,sorted,peakType+': 暂无重组推进峰值记录',badgeClass,function(item){
      return item.symbol+' · '+String(item.label||'重组推进')+' · '+numInt(Number(item.count||0))+'次';
    });
  }else{
    renderInstitutionHistoryBadges(elemId,sorted,peakType+': 暂无峰值记录',badgeClass,function(item){
      return item.symbol+' · '+numInt(Number(item.count||0))+'次 · '+num(Number(item.score||0));
    });
  }
}

function renderInstitutionTrendChart(trendData){
  var chartEl=document.getElementById('inst-history-trend-chart');
  if(!chartEl)return;
  if(!trendData||!trendData.length){
    chartEl.innerHTML='<span style="color:var(--text-dim);font-size:11px">暂无趋势数据</span>';
    return;
  }
  var maxCount=Math.max(...trendData.map(function(point){
    return (point.orderVerified||0)+(point.customerVerified||0)+(point.patentVerified||0)+(point.restructure||0);
  }));
  if(maxCount===0)maxCount=1;
  chartEl.innerHTML=trendData.map(function(point,idx){
    var order=Number(point.orderVerified||0);
    var customer=Number(point.customerVerified||0);
    var patent=Number(point.patentVerified||0);
    var restructure=Number(point.restructure||0);
    var total=order+customer+patent+restructure;
    var heightPct=(total/maxCount)*100;
    var barHtml='<div style="flex:1;display:flex;flex-direction:column-reverse;gap:0;height:100%">';
    if(order>0)barHtml+='<div style="background:var(--orange);flex:'+order+';min-height:2px" title="订单:'+order+'"></div>';
    if(customer>0)barHtml+='<div style="background:var(--accent);flex:'+customer+';min-height:2px" title="客户:'+customer+'"></div>';
    if(patent>0)barHtml+='<div style="background:var(--purple);flex:'+patent+';min-height:2px" title="专利:'+patent+'"></div>';
    if(restructure>0)barHtml+='<div style="background:var(--green);flex:'+restructure+';min-height:2px" title="重组:'+restructure+'"></div>';
    barHtml+='</div>';
    var tooltip='v'+idx+'\\n订单:'+order+' 客户:'+customer+' 专利:'+patent+' 重组:'+restructure;
    return '<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;min-width:1px" title="'+esc(tooltip)+'" data-version="'+esc(String(point.version||''))+'" data-date="'+esc(String(point.asOfDate||''))+'">'+barHtml+'<div style="font-size:9px;color:var(--text-dim);width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">v'+numInt(idx+1)+'</div></div>';
  }).join('');
}

function normalizeInstitutionManualConfirmation(confirmation){
  if(!confirmation||typeof confirmation!=='object')return null;
  var restructureLevelRaw=String(confirmation.restructureLevel||'').trim().toLowerCase();
  var restructureLevel=['none','rumor','announcement','approved'].indexOf(restructureLevelRaw)>=0
    ? restructureLevelRaw
    : (confirmation.restructureConfirmed?'announcement':'none');
  return {
    moatConfirmed:Boolean(confirmation.moatConfirmed),
    restructureConfirmed:Boolean(confirmation.restructureConfirmed)||restructureLevel!=='none',
    restructureLevel:restructureLevel,
    supplyChainVerified:Boolean(confirmation.supplyChainVerified),
    orderVerified:Boolean(confirmation.orderVerified),
    customerVerified:Boolean(confirmation.customerVerified),
    patentVerified:Boolean(confirmation.patentVerified),
    notes:String(confirmation.notes||''),
    score:Number(confirmation.score||0),
    updatedAt:String(confirmation.updatedAt||'')
  };
}

function computeInstitutionManualScore(confirmation){
  confirmation=normalizeInstitutionManualConfirmation(confirmation);
  if(!confirmation)return 0;
  var score=0;
  if(confirmation.moatConfirmed)score+=2;
  if(confirmation.supplyChainVerified)score+=2;
  if(confirmation.orderVerified)score+=2;
  if(confirmation.customerVerified)score+=2;
  if(confirmation.patentVerified)score+=1;
  if(confirmation.restructureLevel==='rumor')score+=1;
  else if(confirmation.restructureLevel==='announcement')score+=2;
  else if(confirmation.restructureLevel==='approved')score+=3;
  return score;
}

function institutionRestructureLevelLabel(level){
  if(level==='approved')return '重组落地';
  if(level==='announcement')return '重组公告';
  if(level==='rumor')return '重组筹划';
  return '';
}

function institutionManualConfirmationSummary(item){
  var confirmation=normalizeInstitutionManualConfirmation(item&&item.manualConfirmation);
  var manualScore=Number(item&&item.manualScore!=null?item.manualScore:computeInstitutionManualScore(confirmation));
  var tags=[];
  if(confirmation&&confirmation.moatConfirmed)tags.push('<span class="badge badge-yellow">壁垒</span>');
  if(confirmation&&confirmation.supplyChainVerified)tags.push('<span class="badge badge-green">供应链</span>');
  if(confirmation&&confirmation.orderVerified)tags.push('<span class="badge badge-orange">订单</span>');
  if(confirmation&&confirmation.customerVerified)tags.push('<span class="badge badge-blue">客户</span>');
  if(confirmation&&confirmation.patentVerified)tags.push('<span class="badge badge-purple">专利</span>');
  if(confirmation&&confirmation.restructureLevel&&confirmation.restructureLevel!=='none')tags.push('<span class="badge badge-blue">'+esc(institutionRestructureLevelLabel(confirmation.restructureLevel))+'</span>');
  if(!tags.length)return '<span style="color:var(--text-dim)">未确认</span>';
  return '<div style="display:flex;flex-wrap:wrap;gap:4px;align-items:center"><span class="badge badge-purple">'+num(manualScore)+'</span>'+tags.join('')+'</div>';
}

function applyInstitutionManualConfirmation(row,confirmation){
  var next=Object.assign({},row);
  var normalized=normalizeInstitutionManualConfirmation(confirmation);
  var manualScore=computeInstitutionManualScore(normalized);
  next.manualConfirmation=normalized?Object.assign({},normalized,{score:manualScore,updatedAt:new Date().toISOString()}):null;
  next.manualScore=manualScore;
  next.decisionScore=computeInstitutionDecisionScore(next);
  return next;
}

function syncInstitutionTop3Rows(rows){
  currentInstitutionTop3Rows=(Array.isArray(rows)?rows:[]).map(function(row){
    var next=Object.assign({},row);
    next.manualConfirmation=normalizeInstitutionManualConfirmation(row&&row.manualConfirmation);
    next.manualScore=Number(row&&row.manualScore||0);
    return next;
  });
  if(!currentInstitutionTop3Rows.length){
    selectedInstitutionTop3Symbol='';
    renderInstitutionManualConfirmationPanel();
    return;
  }
  var exists=currentInstitutionTop3Rows.some(function(row){return row.symbol===selectedInstitutionTop3Symbol;});
  if(!exists)selectedInstitutionTop3Symbol=String(currentInstitutionTop3Rows[0].symbol||'');
  renderInstitutionManualConfirmationPanel();
}

function getSelectedInstitutionTop3Row(){
  return currentInstitutionTop3Rows.find(function(row){return row.symbol===selectedInstitutionTop3Symbol;})||null;
}

function renderInstitutionManualConfirmationPanel(){
  var targetEl=document.getElementById('inst-manual-target');
  var statusEl=document.getElementById('inst-manual-status');
  var moatEl=document.getElementById('inst-manual-moat');
  var supplyEl=document.getElementById('inst-manual-supply');
  var orderEl=document.getElementById('inst-manual-order');
  var customerEl=document.getElementById('inst-manual-customer');
  var patentEl=document.getElementById('inst-manual-patent');
  var restructureLevelEl=document.getElementById('inst-manual-restructure-level');
  var noteEl=document.getElementById('inst-manual-note');
  if(!targetEl||!statusEl||!moatEl||!supplyEl||!orderEl||!customerEl||!patentEl||!restructureLevelEl||!noteEl)return;
  var row=getSelectedInstitutionTop3Row();
  if(!row){
    targetEl.textContent='请选择机构前三中的一只股票进行人工确认。';
    moatEl.checked=false;
    supplyEl.checked=false;
    orderEl.checked=false;
    customerEl.checked=false;
    patentEl.checked=false;
    restructureLevelEl.value='none';
    noteEl.value='';
    statusEl.textContent='保存后会重算当前决策分；若当前结果对应历史快照，也会同步写回快照。';
    return;
  }
  var confirmation=normalizeInstitutionManualConfirmation(row.manualConfirmation)||{};
  moatEl.checked=Boolean(confirmation.moatConfirmed);
  supplyEl.checked=Boolean(confirmation.supplyChainVerified);
  orderEl.checked=Boolean(confirmation.orderVerified);
  customerEl.checked=Boolean(confirmation.customerVerified);
  patentEl.checked=Boolean(confirmation.patentVerified);
  restructureLevelEl.value=String(confirmation.restructureLevel||'none');
  noteEl.value=String(confirmation.notes||'');
  targetEl.textContent='当前股票：'+String(row.symbol||'')+' '+String(row.name||'')+'，当前人工确认分 '+num(Number(row.manualScore||0))+'。';
  statusEl.textContent=currentInstitutionTop3HistoryId
    ? '当前确认会写回历史快照，并刷新历史摘要。'
    : '当前结果尚未绑定历史快照；保存后会先更新表格中的决策分。';
}

function selectInstitutionTop3ForManual(symbol){
  selectedInstitutionTop3Symbol=String(symbol||'').trim();
  // Highlight the selected row in the table
  var allRows=document.querySelectorAll('#inst-top3-table tbody tr[data-inst-symbol]');
  for(var i=0;i<allRows.length;i++){
    if(allRows[i].getAttribute('data-inst-symbol')===selectedInstitutionTop3Symbol){
      allRows[i].classList.add('inst-row-selected');
    }else{
      allRows[i].classList.remove('inst-row-selected');
    }
  }
  renderInstitutionManualConfirmationPanel();
  // Scroll the confirmation panel into view
  var panel=document.getElementById('inst-manual-target');
  if(panel)panel.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function refreshInstitutionHistoryAfterManualPatch(historyId){
  return loadInstitutionHistory().then(function(){
    if(viewingInstitutionHistoryId===historyId)return viewInstitutionHistory(historyId);
  }).catch(function(){});
}

function saveInstitutionManualConfirmation(){
  var row=getSelectedInstitutionTop3Row();
  if(!row){toast('⚠️ 请先选择一只机构前三股票');return}
  var confirmation={
    moatConfirmed:Boolean(document.getElementById('inst-manual-moat').checked),
    supplyChainVerified:Boolean(document.getElementById('inst-manual-supply').checked),
    orderVerified:Boolean(document.getElementById('inst-manual-order').checked),
    customerVerified:Boolean(document.getElementById('inst-manual-customer').checked),
    patentVerified:Boolean(document.getElementById('inst-manual-patent').checked),
    restructureLevel:String(document.getElementById('inst-manual-restructure-level').value||'none'),
    notes:String(document.getElementById('inst-manual-note').value||'').trim()
  };
  confirmation.restructureConfirmed=confirmation.restructureLevel!=='none';
  var updatedRow=applyInstitutionManualConfirmation(row,confirmation);
  currentInstitutionTop3Rows=currentInstitutionTop3Rows.map(function(item){
    return item.symbol===updatedRow.symbol?updatedRow:item;
  });
  currentInstitutionTop3Rows.sort(function(a,b){
    return Number(b.decisionScore||0)-Number(a.decisionScore||0);
  });
  currentInstitutionTop3Rows=currentInstitutionTop3Rows.map(function(item,idx){
    var next=Object.assign({},item);
    next.rank=idx+1;
    return next;
  });
  renderInstitutionTop3(currentInstitutionTop3Rows,currentInstitutionTop3Rows[0]&&currentInstitutionTop3Rows[0].source||'当前结果',true);
  selectInstitutionTop3ForManual(updatedRow.symbol);
  if(!currentInstitutionTop3HistoryId){
    toast('✅ 已更新人工确认，当前决策分已重算');
    return;
  }
  api('/quant/admin/api/institution-activity/history?id='+encodeURIComponent(currentInstitutionTop3HistoryId),{
    method:'PATCH',
    body:JSON.stringify({
      symbol:updatedRow.symbol,
      manualConfirmation:updatedRow.manualConfirmation,
      manualScore:updatedRow.manualScore,
      decisionScore:updatedRow.decisionScore
    })
  }).then(function(d){
    if(d&&d.status==='ok'){
      refreshInstitutionHistoryAfterManualPatch(currentInstitutionTop3HistoryId);
      toast('✅ 人工确认已保存到历史快照');
      return;
    }
    toast('❌ '+String(d&&d.message||'人工确认保存失败'));
  }).catch(function(){toast('❌ 人工确认保存失败')});
}

function clearInstitutionManualConfirmation(){
  var row=getSelectedInstitutionTop3Row();
  if(!row){toast('⚠️ 请先选择一只机构前三股票');return}
  document.getElementById('inst-manual-moat').checked=false;
  document.getElementById('inst-manual-supply').checked=false;
  document.getElementById('inst-manual-order').checked=false;
  document.getElementById('inst-manual-customer').checked=false;
  document.getElementById('inst-manual-patent').checked=false;
  document.getElementById('inst-manual-restructure-level').value='none';
  document.getElementById('inst-manual-note').value='';
  saveInstitutionManualConfirmation();
}

function computeInstitutionDecisionScore(item){
  var manualScore=Number(item&&item.manualScore!=null?item.manualScore:computeInstitutionManualScore(item&&item.manualConfirmation));
  return Number(item.confidence||0)*0.4 + Number(item.netConviction||0)*0.3 + Number(item.buildScore||0)*0.2 + Number(item.techMoatScore||0)*4 + manualScore*1.5;
}

function fetchInstitutionMoat(symbol,name){
  symbol=String(symbol||'').trim();
  if(!symbol)return Promise.resolve({score:null,grade:'',confidenceLevel:''});
  if(institutionMoatCache[symbol])return Promise.resolve(institutionMoatCache[symbol]);
  return api('/quant/admin/api/tech-moat',{method:'POST',body:JSON.stringify({symbol:symbol,company_name:name||''}),_timeout:60000}).then(function(d){
    var value={
      score:d&&d.moatScore!=null?Number(d.moatScore):null,
      grade:String(d&&d.moatGrade||''),
      confidenceLevel:String(d&&d.confidenceLevel||'')
    };
    institutionMoatCache[symbol]=value;
    return value;
  }).catch(function(){
    return {score:null,grade:'',confidenceLevel:''};
  });
}

function enrichInstitutionTop3Rows(rows,sourceLabel,seq,onDone){
  return Promise.all((Array.isArray(rows)?rows:[]).map(function(item){
    return fetchInstitutionMoat(item.symbol,item.name).then(function(moat){
      var next=Object.assign({},item);
      next.techMoatScore=moat.score;
      next.techMoatGrade=moat.grade;
      next.techMoatConfidenceLevel=moat.confidenceLevel;
      next.decisionScore=computeInstitutionDecisionScore(next);
      return next;
    });
  })).then(function(enriched){
    if(seq!==_instTop3Seq)return;
    enriched.sort(function(a,b){
      var ds=Number(b.decisionScore||0)-Number(a.decisionScore||0);
      if(ds)return ds;
      return Number(b.confidence||0)-Number(a.confidence||0);
    });
    enriched=enriched.map(function(item,idx){
      var next=Object.assign({},item);
      next.rank=idx+1;
      return next;
    });
    enriched=enriched.slice(0,3);
    renderInstitutionTop3(enriched,sourceLabel,true);
    if(typeof onDone==='function')onDone(enriched);
  });
}

function pickInstitutionTop3(scanData){
  if(!scanData)return [];
  var leaders=Array.isArray(scanData.leaders)?scanData.leaders:[];
  var candidates=Array.isArray(scanData.candidates)?scanData.candidates:[];
  var base=leaders.length?leaders:candidates;
  return base.slice(0,3).map(function(item,idx){
    return {
      rank:idx+1,
      symbol:String(item.symbol||''),
      name:String(item.name||''),
      actionTag:String(item.actionTag||'中性观察'),
      confidence:Number(item.confidence||0),
      netConviction:Number(item.netConviction||0),
      buildScore:Number(item.buildScore||0),
      manualConfirmation:normalizeInstitutionManualConfirmation(item.manualConfirmation),
      manualScore:Number(item.manualScore||0),
      techMoatScore:null,
      decisionScore:computeInstitutionDecisionScore(item),
      source:leaders.length?'本次 leaders':'本次候选'
    };
  }).filter(function(item){return item.symbol;});
}

function renderInstitutionTop3(rows,sourceLabel,skipEnrich,onEnriched){
  rows=Array.isArray(rows)?rows:[];
  var tbody=document.querySelector('#inst-top3-table tbody');
  var statusEl=document.getElementById('inst-top3-status');
  if(!tbody||!statusEl)return;
  syncInstitutionTop3Rows(rows);
  if(!rows.length){
    tbody.innerHTML='<tr><td colspan="12" style="text-align:center;color:var(--text-dim)">暂无机构前三，请先扫描机构行为</td></tr>';
    statusEl.textContent='暂无机构前三';
    return;
  }
  if(!skipEnrich){
    _instTop3Seq+=1;
    statusEl.textContent=(sourceLabel||'当前结果')+' · 正在补充技术壁垒';
    void enrichInstitutionTop3Rows(rows,sourceLabel,_instTop3Seq,onEnriched);
  }
  tbody.innerHTML=rows.map(function(item,idx){
    var rank=(item.rank!=null?item.rank:(idx+1));
    var symbol=String(item.symbol||'');
    var src=String(item.source||sourceLabel||'当前结果');
    var symbolJs=escAttrJsSingleQuoted(symbol);
    var nameJs=escAttrJsSingleQuoted(item.name||'');
    var moatScore=item.techMoatScore==null?'-':num(Number(item.techMoatScore));
    var decisionScore=num(Number(item.decisionScore!=null?item.decisionScore:computeInstitutionDecisionScore(item)));
    var manualSummary=institutionManualConfirmationSummary(item);
    var isSelected=selectedInstitutionTop3Symbol===symbol;
    return '<tr data-inst-symbol="'+esc(symbol)+'"'+(isSelected?' class="inst-row-selected"':'')+'>'+
      '<td><span class="badge badge-purple">#'+numInt(rank)+'</span></td>'+
      '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(symbol)+'" target="_blank" style="color:var(--accent)">'+esc(symbol)+'</a></td>'+
      '<td>'+esc(item.name||'')+'</td>'+
      '<td>'+institutionActionBadge(item.actionTag||'中性观察')+'</td>'+
      '<td><span class="badge badge-blue">'+num(item.confidence)+'</span></td>'+
      '<td style="color:'+(Number(item.netConviction)>=0?'var(--green)':'var(--red)')+'">'+num(item.netConviction)+'</td>'+
      '<td><span class="badge badge-green">'+num(item.buildScore)+'</span></td>'+
      '<td><span class="badge badge-yellow">'+moatScore+'</span></td>'+
      '<td>'+manualSummary+'</td>'+
      '<td><span class="badge badge-purple">'+decisionScore+'</span></td>'+
      '<td>'+esc(src)+'</td>'+
      '<td><button class="btn btn-sm" onclick="selectInstitutionTop3ForManual(\\''+symbolJs+'\\')">确认</button> <button class="btn btn-sm" onclick="loadInstitutionDetail(\\''+symbolJs+'\\')">详情</button> <button class="btn btn-sm" onclick="addInstitutionTop3ToFavorites(\\''+symbolJs+'\\',\\''+nameJs+'\\')">收藏</button></td>'+
    '</tr>';
  }).join('');
  if(skipEnrich)statusEl.textContent=(sourceLabel||'当前结果')+' · 共 '+rows.length+' 只';
}

function renderInstitutionTop3History(entries){
  entries=Array.isArray(entries)?entries:[];
  var tbody=document.querySelector('#inst-top3-history-table tbody');
  if(!tbody)return;
  if(!entries.length){
    tbody.innerHTML='<tr><td colspan="13" style="text-align:center;color:var(--text-dim)">暂无历史轨迹</td></tr>';
    return;
  }
  tbody.innerHTML=entries.slice(0,12).map(function(item){
    var summary=item.summary||{};
    var leaders=Array.isArray(item.topLeaders)?item.topLeaders:[];
    var top3Leaders=leaders.slice(0,3);
    var avgMoat=averageInstitutionMetric(leaders.slice(0,3),'techMoatScore');
    var avgManual=averageInstitutionMetric(leaders.slice(0,3),'manualScore');
    var avgDecision=averageInstitutionMetric(leaders.slice(0,3),'decisionScore');
    var orderCount=countInstitutionManualFlag(top3Leaders,'orderVerified');
    var customerCount=countInstitutionManualFlag(top3Leaders,'customerVerified');
    var patentCount=countInstitutionManualFlag(top3Leaders,'patentVerified');
    var restructureCount=countInstitutionManualFlag(top3Leaders,'restructure');
    var top3=top3Leaders.map(function(row){
      return '<a href="'+B+'/quant/admin/stock?symbol='+esc(row.symbol)+'" target="_blank" class="badge badge-blue" style="text-decoration:none;margin:1px">'+esc(row.symbol)+'</a>';
    }).join('');
    return '<tr>'+
      '<td><span class="badge badge-blue">'+esc(item.version||'-')+'</span></td>'+
      '<td>'+esc(summary.asOfDate||'-')+'</td>'+
      '<td>'+(top3||'<span style="color:var(--text-dim)">无</span>')+'</td>'+
      '<td>'+numInt(summary.leaderCount||0)+'</td>'+
      '<td>'+numInt(summary.candidateCount||0)+'</td>'+
      '<td>'+(avgMoat==null?'<span style="color:var(--text-dim)">-</span>':'<span class="badge badge-yellow">'+num(avgMoat)+'</span>')+'</td>'+
      '<td>'+(avgManual==null?'<span style="color:var(--text-dim)">-</span>':'<span class="badge badge-blue">'+num(avgManual)+'</span>')+'</td>'+
      '<td>'+(avgDecision==null?'<span style="color:var(--text-dim)">-</span>':'<span class="badge badge-purple">'+num(avgDecision)+'</span>')+'</td>'+
      '<td><span class="badge badge-orange">'+numInt(orderCount)+'</span></td>'+
      '<td><span class="badge badge-blue">'+numInt(customerCount)+'</span></td>'+
      '<td><span class="badge badge-purple">'+numInt(patentCount)+'</span></td>'+
      '<td><span class="badge badge-green">'+numInt(restructureCount)+'</span></td>'+
      '<td>'+esc(summary.marketTone||'δ֪')+'</td>'+
    '</tr>';
  }).join('');
}

function useLatestInstitutionHistoryTop3(){
  var entries=(institutionHistoryCache&&institutionHistoryCache.entries)||[];
  if(!entries.length){
    toast('⚠️ 暂无机构历史，请先扫描一次');
    return;
  }
  var latest=entries[0]||{};
  currentInstitutionTop3HistoryId=latest.id||null;
  var rows=(latest.topLeaders||[]).slice(0,3).map(function(item,idx){
    return {
      rank:idx+1,
      symbol:String(item.symbol||''),
      name:String(item.name||''),
      actionTag:String(item.actionTag||'可观察'),
      confidence:Number(item.confidence||0),
      netConviction:Number(item.netConviction||0),
      buildScore:Number(item.buildScore||item.netConviction||0),
      manualConfirmation:normalizeInstitutionManualConfirmation(item.manualConfirmation),
      manualScore:Number(item.manualScore||0),
      techMoatScore:item.techMoatScore==null?null:Number(item.techMoatScore),
      techMoatGrade:String(item.techMoatGrade||''),
      decisionScore:item.decisionScore==null?null:Number(item.decisionScore),
      source:'历史 '+String(latest.version||'')
    };
  });
  renderInstitutionTop3(rows,'历史 '+String(latest.version||''));
  toast('✅ 已加载最近一次机构前三');
}

function addInstitutionTop3ToFavorites(symbol,name){
  if(!symbol){toast('⚠️ 缺少股票代码');return}
  api('/quant/admin/api/favorites',{
    method:'POST',
    body:JSON.stringify({
      code:symbol,
      name:name||symbol,
      category:'institution-top3',
      tags:['institution','top3'],
      priority:5,
      notes:'来自机构前三管理面板'
    })
  }).then(function(d){
    if(d&&d.ok){toast('✅ 已加入收藏: '+symbol);return}
    toast('❌ 加入收藏失败');
  }).catch(function(){toast('❌ 加入收藏失败')});
}

function saveInstitutionTop3ToFavorites(){
  var rows=currentInstitutionTop3Rows&&currentInstitutionTop3Rows.length?currentInstitutionTop3Rows:pickInstitutionTop3(institutionScanCache);
  if(!rows.length){
    toast('⚠️ 当前没有可保存的机构前三');
    return;
  }
  var tasks=rows.map(function(item){
    return api('/quant/admin/api/favorites',{
      method:'POST',
      body:JSON.stringify({
        code:item.symbol,
        name:item.name||item.symbol,
        category:'institution-top3',
        tags:['institution','top3'],
        priority:5,
        notes:'来自机构前三管理面板'
      })
    });
  });
  Promise.allSettled(tasks).then(function(){
    toast('✅ 机构前三已同步到收藏');
  });
}

function saveInstitutionHistory(scanData,params){
  api('/quant/admin/api/institution-activity/history',{
    method:'POST',
    body:JSON.stringify({scan:scanData,params:params})
  }).then(function(){
    loadInstitutionHistory();
  }).catch(function(){});
}

function loadInstitutionHistory(){
  var statusEl=document.getElementById('inst-history-status');
  var panel=document.getElementById('inst-history-panel');
  statusEl.textContent='正在加载…';
  return api('/quant/admin/api/institution-activity/history').then(function(d){
    institutionHistoryCache=d;
    institutionHistoryCachedMetrics=d.metrics||null;
    institutionHistoryPeakSortBy={order:'count',customer:'count',patent:'count',restructure:'count'};
    var entries=d.entries||[];
    if(!entries.length){
      panel.style.display='none';
      currentInstitutionTop3HistoryId=null;
      document.getElementById('inst-history-kpis').innerHTML='';
      document.getElementById('inst-history-top').innerHTML='<span style="font-size:12px;color:var(--text-dim)">暂无历史快照，执行一次扫描后会自动保存。</span>';
      renderInstitutionTop3([], '暂无历史');
      renderInstitutionTop3History([]);
      statusEl.textContent='暂无历史';
      return;
    }
    panel.style.display='block';
    var latest=entries[0]||{};
    var comparison=d.comparison||{};
    var metrics=d.metrics||{};
    var latestSummary=latest.summary||{};
    document.getElementById('inst-history-kpis').innerHTML=[
      kpiHtml(String(d.count||entries.length),'快照数'),
      kpiHtml(String(latestSummary.candidateCount||0),'最新候选'),
      kpiHtml(String(latestSummary.leaderCount||0),'最新 leaders'),
      kpiHtml(String(latestSummary.baitCount||0),'最新高风险'),
      kpiHtml(metrics.latestAvgMoatScore==null?'-':num(Number(metrics.latestAvgMoatScore)),'最新Top3壁垒'),
      kpiHtml(metrics.latestAvgManualScore==null?'-':num(Number(metrics.latestAvgManualScore)),'最新Top3人工确认'),
      kpiHtml(metrics.latestAvgDecisionScore==null?'-':num(Number(metrics.latestAvgDecisionScore)),'最新Top3决策'),
      kpiHtml(numInt(Number(metrics.latestOrderVerifiedCount||0)),'最新订单验证'),
      kpiHtml(numInt(Number(metrics.latestCustomerVerifiedCount||0)),'最新客户验证'),
      kpiHtml(numInt(Number(metrics.latestPatentVerifiedCount||0)),'最新专利验证'),
      kpiHtml(numInt(Number(metrics.latestRestructureCount||0)),'最新重组推进')
    ].join('');
    renderInstitutionHistoryBadges('inst-history-top',d.topLeaderStocks||[],'暂无高频 leaders','badge-purple',function(item){
      return item.code+' · '+numInt(item.count)+'次';
    });
    renderInstitutionHistoryBadges('inst-history-new',comparison.newLeaders||[],'暂无新上榜 leader','badge-green');
    renderInstitutionHistoryBadges('inst-history-kept',comparison.keptLeaders||[],'暂无连续上榜 leader','badge-blue',function(code){
      var streak=(comparison.leaderStreaks&&comparison.leaderStreaks[code])||1;
      return code+' · '+numInt(streak)+'连';
    });
    renderInstitutionHistoryBadges('inst-history-dropped',comparison.droppedLeaders||[],'暂无掉出 leader','badge-orange');
    renderInstitutionHistoryBadges('inst-history-moat-peaks',metrics.topMoatLeaders||[],'暂无峰值壁垒记录','badge-yellow',function(item){
      var parts=[item.symbol,String(num(Number(item.score||0)))];
      if(item.grade)parts.push(String(item.grade));
      return parts.join(' · ');
    });
    renderInstitutionHistoryBadges('inst-history-manual-peaks',metrics.topManualLeaders||[],'暂无峰值人工确认记录','badge-blue',function(item){
      return item.symbol+' · '+num(Number(item.score||0));
    });
    renderInstitutionHistoryBadges('inst-history-decision-peaks',metrics.topDecisionLeaders||[],'暂无峰值决策记录','badge-purple',function(item){
      return item.symbol+' · '+num(Number(item.score||0));
    });
    renderInstitutionHistoryBadges('inst-history-order-peaks',metrics.topOrderLeaders||[],'暂无订单验证峰值记录','badge-orange',function(item){
      return item.symbol+' · '+numInt(Number(item.count||0))+'次 · '+num(Number(item.score||0));
    });
    renderInstitutionHistoryBadges('inst-history-customer-peaks',metrics.topCustomerLeaders||[],'暂无客户验证峰值记录','badge-blue',function(item){
      return item.symbol+' · '+numInt(Number(item.count||0))+'次 · '+num(Number(item.score||0));
    });
    renderInstitutionHistoryBadges('inst-history-patent-peaks',metrics.topPatentLeaders||[],'暂无专利验证峰值记录','badge-purple',function(item){
      return item.symbol+' · '+numInt(Number(item.count||0))+'次 · '+num(Number(item.score||0));
    });
    renderInstitutionHistoryBadges('inst-history-restructure-peaks',metrics.topRestructureLeaders||[],'暂无重组推进峰值记录','badge-green',function(item){
      return item.symbol+' · '+String(item.label||'重组推进')+' · '+numInt(Number(item.count||0))+'次';
    });
    renderInstitutionTrendChart(metrics.trendData||[]);
    document.querySelector('#inst-history-table tbody').innerHTML=entries.map(function(item){
      var summary=item.summary||{};
      var params=item.params||{};
      var topLeaders=Array.isArray(item.topLeaders)?item.topLeaders:[];
      var top3Leaders=topLeaders.slice(0,3);
      var avgMoat=averageInstitutionMetric(top3Leaders,'techMoatScore');
      var avgManual=averageInstitutionMetric(top3Leaders,'manualScore');
      var avgDecision=averageInstitutionMetric(top3Leaders,'decisionScore');
      var orderCount=countInstitutionManualFlag(top3Leaders,'orderVerified');
      var customerCount=countInstitutionManualFlag(top3Leaders,'customerVerified');
      var patentCount=countInstitutionManualFlag(top3Leaders,'patentVerified');
      var restructureCount=countInstitutionManualFlag(top3Leaders,'restructure');
      var filters=[];
      if(params.lookback_days!=null)filters.push('窗口'+numInt(params.lookback_days)+'天');
      if(params.min_confidence!=null)filters.push('置信度≥'+numInt(params.min_confidence));
      if(params.pre_accum_count_min!=null&&params.pre_accum_days!=null)filters.push('吸筹:'+numInt(params.pre_accum_days)+'天≥'+numInt(params.pre_accum_count_min)+'次');
      if(params.pre_accum_volume_ratio!=null)filters.push('吸筹放量≥'+num(params.pre_accum_volume_ratio)+'x');
      if(params.rush_change_pct!=null&&params.rush_volume_ratio!=null)filters.push('抢筹:涨≥'+num(params.rush_change_pct)+'% 量≥'+num(params.rush_volume_ratio)+'x');
      if(params.rush_turnover_pct!=null)filters.push('抢筹换手≥'+num(params.rush_turnover_pct)+'%');
      if(params.symbols&&params.symbols.length)filters.push('自选'+numInt(params.symbols.length)+'只');
      return '<tr>'+
        '<td><span class="badge badge-blue">'+esc(item.version||'-')+'</span></td>'+
        '<td>'+esc(summary.asOfDate||'-')+'</td>'+
        '<td>'+esc((item.timestamp||'').replace('T',' ').slice(0,19)||'-')+'</td>'+
        '<td>'+numInt(summary.candidateCount||0)+'</td>'+
        '<td>'+numInt(summary.leaderCount||0)+'</td>'+
        '<td>'+numInt(summary.baitCount||0)+'</td>'+
        '<td>'+(avgMoat==null?'<span style="color:var(--text-dim)">-</span>':'<span class="badge badge-yellow">'+num(avgMoat)+'</span>')+'</td>'+
        '<td>'+(avgManual==null?'<span style="color:var(--text-dim)">-</span>':'<span class="badge badge-blue">'+num(avgManual)+'</span>')+'</td>'+
        '<td>'+(avgDecision==null?'<span style="color:var(--text-dim)">-</span>':'<span class="badge badge-purple">'+num(avgDecision)+'</span>')+'</td>'+
        '<td><span class="badge badge-orange">'+numInt(orderCount)+'</span></td>'+
        '<td><span class="badge badge-blue">'+numInt(customerCount)+'</span></td>'+
        '<td><span class="badge badge-purple">'+numInt(patentCount)+'</span></td>'+
        '<td><span class="badge badge-green">'+numInt(restructureCount)+'</span></td>'+
        '<td>'+esc(summary.marketTone||'δ֪')+'</td>'+
        '<td>'+(filters.length?filters.map(function(x){return '<span class="badge badge-sim" style="margin:1px">'+esc(x)+'</span>'}).join(''):'<span style="color:var(--text-dim)">默认</span>')+'</td>'+
        '<td><button class="btn btn-sm" onclick="viewInstitutionHistory(\\''+esc(item.id)+'\\')">查看</button> <button class="btn btn-sm btn-danger" onclick="deleteInstitutionHistory(\\''+esc(item.id)+'\\')">删除</button></td>'+
        '</tr>';
    }).join('');
    var latestTop3=(latest.topLeaders||[]).slice(0,3).map(function(row,idx){
      return {
        rank:idx+1,
        symbol:String(row.symbol||''),
        name:String(row.name||''),
        actionTag:String(row.actionTag||'可观察'),
        confidence:Number(row.confidence||0),
        netConviction:Number(row.netConviction||0),
        buildScore:Number(row.buildScore||row.netConviction||0),
        manualConfirmation:normalizeInstitutionManualConfirmation(row.manualConfirmation),
        manualScore:Number(row.manualScore||0),
        techMoatScore:row.techMoatScore==null?null:Number(row.techMoatScore),
        techMoatGrade:String(row.techMoatGrade||''),
        decisionScore:row.decisionScore==null?null:Number(row.decisionScore),
        source:'历史 '+String(latest.version||'')
      };
    });
    currentInstitutionTop3HistoryId=latest.id||null;
    renderInstitutionTop3(latestTop3,'历史 '+String(latest.version||''));
    renderInstitutionTop3History(entries);
    var bestMoat=metrics.bestMoatLeader||null;
    var bestDecision=metrics.bestDecisionLeader||null;
    var insightParts=['最近快照 '+esc(latest.version||'')+'，观察日 '+esc(latestSummary.asOfDate||'-')];
    if(bestMoat&&bestMoat.symbol&&bestMoat.score!=null)insightParts.push('历史最高壁垒 '+esc(bestMoat.symbol)+' '+num(Number(bestMoat.score)));
    if(bestDecision&&bestDecision.symbol&&bestDecision.score!=null)insightParts.push('历史最高决策 '+esc(bestDecision.symbol)+' '+num(Number(bestDecision.score)));
    statusEl.textContent=insightParts.join(' ｜ ');
  }).catch(function(){
    statusEl.textContent='历史加载失败';
  });
}

function viewInstitutionHistory(historyId){
  return api('/quant/admin/api/institution-activity/history?id='+encodeURIComponent(historyId)).then(function(d){
    if(d.status==='error'||!d.entry||!d.entry.fullScan){toast('⚠️ 历史快照不存在');return}
    var entry=d.entry;
    var params=entry.params||{};
    viewingInstitutionHistoryId=historyId;
    currentInstitutionTop3HistoryId=historyId;
    document.getElementById('inst-date').value=params.as_of_date||entry.summary&&entry.summary.asOfDate||'';
    document.getElementById('inst-lookback').value=String(params.lookback_days||10);
    document.getElementById('inst-min-confidence').value=String(params.min_confidence||0);
    document.getElementById('inst-pre-accum-days').value=String(params.pre_accum_days||10);
    document.getElementById('inst-pre-accum-count').value=String(params.pre_accum_count_min||2);
    document.getElementById('inst-pre-accum-vol-ratio').value=String(params.pre_accum_volume_ratio||1.5);
    document.getElementById('inst-rush-change-pct').value=String(params.rush_change_pct||5);
    document.getElementById('inst-rush-vol-ratio').value=String(params.rush_volume_ratio||2);
    document.getElementById('inst-rush-turnover').value=String(params.rush_turnover_pct||3);
    setInstitutionPresetLabel(detectInstitutionPresetByThresholds({
      pre_accum_days:params.pre_accum_days||10,
      pre_accum_count_min:params.pre_accum_count_min||2,
      pre_accum_volume_ratio:params.pre_accum_volume_ratio||1.5,
      rush_change_pct:params.rush_change_pct||5,
      rush_volume_ratio:params.rush_volume_ratio||2,
      rush_turnover_pct:params.rush_turnover_pct||3
    }));
    document.getElementById('inst-symbols').value=(params.symbols||[]).join(',');
    institutionScanCache=entry.fullScan;
    renderInstitutionScan(entry.fullScan);
    var top3=(entry.topLeaders||[]).slice(0,3).map(function(row,idx){
      return {
        rank:idx+1,
        symbol:String(row.symbol||''),
        name:String(row.name||''),
        actionTag:String(row.actionTag||'可观察'),
        confidence:Number(row.confidence||0),
        netConviction:Number(row.netConviction||0),
        buildScore:Number(row.buildScore||row.netConviction||0),
        manualConfirmation:normalizeInstitutionManualConfirmation(row.manualConfirmation),
        manualScore:Number(row.manualScore||0),
        techMoatScore:row.techMoatScore==null?null:Number(row.techMoatScore),
        techMoatGrade:String(row.techMoatGrade||''),
        decisionScore:row.decisionScore==null?null:Number(row.decisionScore),
        source:'历史 '+String(entry.version||'')
      };
    });
    renderInstitutionTop3(top3,'历史 '+String(entry.version||''));
    document.getElementById('inst-status').textContent='已载入历史快照 '+(entry.version||'')+'，可继续查看当时榜单和单票证据';
    var leader=((entry.fullScan.leaders&&entry.fullScan.leaders[0])||(entry.fullScan.candidates&&entry.fullScan.candidates[0]));
    if(leader&&leader.symbol){loadInstitutionDetail(leader.symbol);}
    toast('✅ 已载入历史快照');
  });
}

function deleteInstitutionHistory(historyId){
  if(!confirm('确认删除这个机构行为快照？'))return;
  api('/quant/admin/api/institution-activity/history?id='+encodeURIComponent(historyId),{method:'DELETE'}).then(function(d){
    if(d.status==='ok'){
      if(viewingInstitutionHistoryId===historyId)viewingInstitutionHistoryId=null;
      if(currentInstitutionTop3HistoryId===historyId)currentInstitutionTop3HistoryId=null;
      loadInstitutionHistory();
      toast('✅ 历史快照已删除');
      return;
    }
    toast('❌ '+(d.message||'删除失败'));
  });
}

function scanInstitutionActivity(){
  ensureInstitutionDefaults();
  var btn=document.getElementById('btn-inst-scan');
  var symbols=parseSymbolList(document.getElementById('inst-symbols').value);
  if(symbols.length){localStorage.setItem('openclaw-inst-symbols',symbols.join(','));}
  btn.disabled=true;btn.textContent='⏳ 正在聚合…';
  document.getElementById('inst-results').style.display='none';
  document.getElementById('inst-status').textContent='正在聚合龙虎榜、主力资金、北向资金、大宗交易和价量结构…';
  var body={
    action:'scan',
    as_of_date:document.getElementById('inst-date').value||'',
    lookback_days:Number(document.getElementById('inst-lookback').value)||10,
    min_confidence:Number(document.getElementById('inst-min-confidence').value)||0,
    pre_accum_days:Number(document.getElementById('inst-pre-accum-days').value)||10,
    pre_accum_count_min:Number(document.getElementById('inst-pre-accum-count').value)||2,
    pre_accum_volume_ratio:Number(document.getElementById('inst-pre-accum-vol-ratio').value)||1.5,
    rush_change_pct:Number(document.getElementById('inst-rush-change-pct').value)||5,
    rush_volume_ratio:Number(document.getElementById('inst-rush-vol-ratio').value)||2,
    rush_turnover_pct:Number(document.getElementById('inst-rush-turnover').value)||3
  };
  var presetKey=detectInstitutionPresetByThresholds(body);
  persistInstitutionThresholds(body,presetKey);
  setInstitutionPresetLabel(presetKey);
  if(symbols.length)body.symbols=symbols;
  api('/quant/admin/api/institution-activity/scan',{method:'POST',body:JSON.stringify(body),_timeout:360000}).then(function(d){
    btn.disabled=false;btn.textContent='🔎 扫描机构行为';
    institutionScanCache=d;
    currentInstitutionTop3HistoryId=null;
    renderInstitutionScan(d);
    var top3Rows=pickInstitutionTop3(d);
    if(top3Rows.length){
      renderInstitutionTop3(top3Rows,'本次扫描',false,function(enrichedTop3){
        var scanForHistory=Object.assign({},d,{topLeaders:enrichedTop3});
        saveInstitutionHistory(scanForHistory,body);
      });
    }else{
      renderInstitutionTop3([], '本次扫描');
      saveInstitutionHistory(d,body);
    }
    var leader=((d.leaders&&d.leaders[0])||(d.candidates&&d.candidates[0]));
    if(leader&&leader.symbol){loadInstitutionDetail(leader.symbol);}
    document.getElementById('inst-status').textContent='扫描完成：已生成候选榜单与风险名单';
    toast('✅ 机构行为扫描完成');
  }).catch(function(){
    btn.disabled=false;btn.textContent='🔎 扫描机构行为';
    document.getElementById('inst-status').textContent='扫描失败，请稍后重试';
  });
}

function renderInstitutionScan(d){
  var candidates=d.candidates||[];
  var leaders=d.leaders||[];
  var baits=d.highRiskBaits||[];
  var ms=d.marketSummary||{};
  var northVal=ms.northboundNetIn;
  var northHtml='-';
  if(northVal!=null){
    northHtml=(northVal>=0?'+':'-')+Math.abs(northVal/100000000).toFixed(2)+'亿';
    northHtml='<span style="color:'+(northVal>=0?'var(--green)':'var(--red)')+'">'+northHtml+'</span>';
  }
  document.getElementById('inst-results').style.display='block';
  document.getElementById('inst-kpis').innerHTML=[
    kpiHtml(String((d.stats&&d.stats.candidateCount)||candidates.length),'候选数'),
    kpiHtml(String(leaders.length),'可观察'),
    kpiHtml(String(baits.length),'高风险'),
    kpiHtml(northHtml,'北向背景')
  ].join('');

  document.getElementById('inst-market-summary').innerHTML=
    '<p>'+esc(ms.summary||'暂无北向/市场背景摘要')+'</p>'+
    '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:8px">'+
      '<span class="badge badge-blue">龙虎榜 '+numInt(ms.dragonTigerCount||0)+'</span>'+
      '<span class="badge badge-purple">主力资金 '+numInt(ms.fundFlowCount||0)+'</span>'+
      '<span class="badge badge-orange">大宗交易 '+numInt(ms.blockTradeCount||0)+'</span>'+
      '<span class="badge badge-sim">高管增减持 '+numInt(ms.managementChangeCount||0)+'</span>'+
      '<span class="badge badge-green">机构持仓 '+numInt(ms.institutionalHoldCount||0)+'</span>'+
      '<span class="badge '+((ms.marketTone||'').indexOf('空')>=0?'badge-red':'badge-green')+'">市场基调 '+esc(ms.marketTone||'δ֪')+'</span>'+
    '</div>';

  var warnings=(d.warnings||[]).map(function(w){
    return '<li><span class="rules-icon">⚠️</span><div>'+esc(w)+'</div></li>';
  }).join('')||'<li><span class="rules-icon">ℹ️</span><div>无额外提示</div></li>';
  document.getElementById('inst-warnings').innerHTML=warnings;

  document.getElementById('inst-candidate-count').textContent=String(candidates.length);
  document.querySelector('#inst-table tbody').innerHTML=candidates.map(function(item){
    var pos=(item.topEvidence||[]).map(function(ev){return '<span class="badge badge-blue" style="margin:1px">'+esc(ev)+'</span>'}).join('')||'<span style="color:var(--text-dim)">-</span>';
    var neg=(item.topCounterEvidence||[]).map(function(ev){return '<span class="badge badge-yellow" style="margin:1px">'+esc(ev)+'</span>'}).join('')||'<span style="color:var(--text-dim)">-</span>';
    return '<tr>'+
      '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(item.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(item.symbol)+'</a></td>'+
      '<td>'+esc(item.name)+'</td>'+
      '<td>'+num(item.price)+'</td>'+
      '<td style="color:'+pctColor(item.changePct)+'">'+pct(item.changePct)+'</td>'+
      '<td><span class="badge badge-green">'+num(item.buildScore)+'</span></td>'+
      '<td><span class="badge badge-red">'+num(item.baitRiskScore)+'</span></td>'+
      '<td><span class="badge badge-orange">'+num(item.distributionScore)+'</span></td>'+
      '<td><span class="badge badge-purple">'+num(item.confidence)+'</span></td>'+
      '<td style="color:'+(item.netConviction>=0?'var(--green)':'var(--red)')+'">'+num(item.netConviction)+'</td>'+
      '<td>'+esc(item.followWindow||'观望')+'</td>'+
      '<td>'+institutionActionBadge(item.actionTag)+'</td>'+
      '<td>'+pos+'</td>'+
      '<td>'+neg+'</td>'+
      '<td><button class="btn btn-sm" onclick="loadInstitutionDetail(\\''+esc(item.symbol)+'\\')">详情</button></td>'+
      '</tr>';
  }).join('')||'<tr><td colspan="14" style="text-align:center;color:var(--text-dim)">暂无候选</td></tr>';

  var baitHtml='';
  if(baits.length){
    baitHtml='<ul class="rules-list">'+baits.map(function(item){
      var reason=((item.topCounterEvidence||[])[0]||'风险信号较多');
      return '<li><span class="rules-icon">🚫</span><div><strong>'+esc(item.symbol)+' '+esc(item.name)+'</strong> '+institutionActionBadge(item.actionTag)+'<br><span style="color:var(--text-dim)">钓鱼分'+num(item.baitRiskScore)+'，派发分'+num(item.distributionScore)+'，'+esc(reason)+'</span><br><button class="btn btn-sm" style="margin-top:6px" onclick="loadInstitutionDetail(\\''+esc(item.symbol)+'\\')">查看证据</button></div></li>';
    }).join('')+'</ul>';
  }else{
    baitHtml='<div style="font-size:12px;color:var(--text-dim)">当前未识别到明显的高风险钓鱼候选。</div>';
  }
  document.getElementById('inst-bait-list').innerHTML=baitHtml;
}

function loadInstitutionDetail(symbol){
  ensureInstitutionDefaults();
  var resultsEl=document.getElementById('inst-results');
  if(resultsEl)resultsEl.style.display='block';
  var dateVal=document.getElementById('inst-date').value||'';
  var lookback=Number(document.getElementById('inst-lookback').value)||10;
  var cacheKey=symbol+'|'+dateVal+'|'+lookback;
  if(institutionDetailCache[cacheKey]){
    renderInstitutionDetail(institutionDetailCache[cacheKey]);
    return;
  }
  document.getElementById('inst-detail-empty').style.display='block';
  document.getElementById('inst-detail-empty').textContent='正在加载 '+symbol+' 的证据详情…';
  document.getElementById('inst-detail-panel').style.display='none';
  document.getElementById('inst-detail-empty').scrollIntoView({behavior:'smooth',block:'start'});
  api('/quant/admin/api/institution-activity/detail',{
    method:'POST',
    _timeout:60000,
    body:JSON.stringify({
      symbol:symbol,
      as_of_date:dateVal,
      lookback_days:lookback,
      pre_accum_days:Number(document.getElementById('inst-pre-accum-days').value)||10,
      pre_accum_count_min:Number(document.getElementById('inst-pre-accum-count').value)||2,
      pre_accum_volume_ratio:Number(document.getElementById('inst-pre-accum-vol-ratio').value)||1.5,
      rush_change_pct:Number(document.getElementById('inst-rush-change-pct').value)||5,
      rush_volume_ratio:Number(document.getElementById('inst-rush-vol-ratio').value)||2,
      rush_turnover_pct:Number(document.getElementById('inst-rush-turnover').value)||3
    })
  }).then(function(d){
    institutionDetailCache[cacheKey]=d;
    renderInstitutionDetail(d);
  }).catch(function(){
    document.getElementById('inst-detail-empty').style.display='block';
    document.getElementById('inst-detail-empty').textContent='详情加载失败，请稍后重试';
  });
}

function renderInstitutionDetail(d){
  var s=d.summary||{};
  var mc=s.marketContext||{};
  document.getElementById('inst-detail-empty').style.display='none';
  document.getElementById('inst-detail-panel').style.display='block';
  document.getElementById('inst-detail-panel').scrollIntoView({behavior:'smooth',block:'start'});
  document.getElementById('inst-detail-kpis').innerHTML=[
    kpiHtml('<span style="color:var(--green)">'+num(s.buildScore)+'</span>','建仓分'),
    kpiHtml('<span style="color:var(--red)">'+num(s.baitRiskScore)+'</span>','钓鱼分'),
    kpiHtml('<span style="color:var(--orange)">'+num(s.distributionScore)+'</span>','派发分'),
    kpiHtml(num(s.confidence),'置信度'),
    kpiHtml((s.netConviction>=0?'+':'')+num(s.netConviction),'净判断'),
    kpiHtml(esc(s.followWindow||'观望'),'跟随窗口')
  ].join('');
  document.getElementById('inst-detail-summary').innerHTML=
    '<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px">'+
      institutionActionBadge(s.actionTag)+
      '<span class="badge badge-purple">'+esc(s.symbol||'')+' '+esc(s.name||'')+'</span>'+
      (s.sourceTags||[]).map(function(tag){return '<span class="badge badge-blue">'+esc(tag)+'</span>'}).join('')+
    '</div>'+
    '<p>市场背景: '+esc(mc.summary||'暂无')+'</p>'+
    '<p>'+(s.price!=null?'现价 '+num(s.price)+'，涨跌 '+pct(s.changePct)+'。 ':'')+'第一版跟随窗口为规则推断，后续会继续用历史事件研究校准。</p>';

  var cc=d.controllerContext;
  if(cc&&cc.controller&&cc.controller!=='无'){
    document.getElementById('inst-detail-summary').innerHTML+=
      '<div style="margin-top:8px;padding:8px 12px;background:rgba(218,165,32,.08);border-radius:6px;font-size:12px;line-height:1.6">'+
        '<strong style="color:#daa520">实控人</strong> '+esc(cc.controller)+
        (cc.ratio?' · 控股'+num(cc.ratio)+'%':'')+
        (cc.controlType?' · '+esc(cc.controlType):'')+
        (cc.asOfDate?' · 截至'+esc(cc.asOfDate):'')+
      '</div>';
  }

  var posList=d.positiveEvidence||[];
  var negList=d.negativeEvidence||[];
  document.getElementById('inst-positive-list').innerHTML=posList.map(function(item){
    return '<li><span class="rules-icon">✅</span><div><strong>'+esc(item.title)+'</strong> <span class="badge badge-green">+'+num(item.score)+'</span><br><span style="color:var(--text-dim)">'+esc(item.source||'')+' · '+esc(item.detail||'')+'</span></div></li>';
  }).join('')||'<li><span class="rules-icon">—</span><div>暂无强正证据</div></li>';
  document.getElementById('inst-negative-list').innerHTML=negList.map(function(item){
    return '<li><span class="rules-icon">⚠️</span><div><strong>'+esc(item.title)+'</strong> <span class="badge badge-red">'+num(item.score)+'</span><br><span style="color:var(--text-dim)">'+esc(item.source||'')+' · '+esc(item.detail||'')+'</span></div></li>';
  }).join('')||'<li><span class="rules-icon">—</span><div>暂无明显反证</div></li>';

  document.querySelector('#inst-timeline-table tbody').innerHTML=(d.timeline||[]).map(function(item){
    return '<tr>'+
      '<td>'+esc(item.tradeDate||'-')+'</td>'+
      '<td>'+esc(item.source||'-')+'</td>'+
      '<td>'+evidenceTypeBadge(item.evidenceType)+'</td>'+
      '<td><strong>'+esc(item.title||'-')+'</strong><br><span style="font-size:11px;color:var(--text-dim)">'+esc(item.detail||'')+'</span></td>'+
      '<td>'+(item.scoreImpact>=0?'+':'')+num(item.scoreImpact)+'</td>'+
      '</tr>';
  }).join('')||'<tr><td colspan="5" style="text-align:center;color:var(--text-dim)">暂无事件时间线</td></tr>';
}

`;
}

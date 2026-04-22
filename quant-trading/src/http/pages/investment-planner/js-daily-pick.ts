/** Investment Planner — daily composite picks, recommendations, and order execution */
export function getDailyPickJs(): string {
  return `
function getDailyPickStrategies(){
  return Array.from(document.querySelectorAll('.dp-strategy:checked')).map(function(el){
    return el.value;
  });
}

function setDailyPickLoading(loading,msg){
  var btn=document.getElementById('btn-dp-run');
  if(btn){
    btn.disabled=loading;
    btn.textContent=loading?'⏳ 正在运行综合选股…':'🚀 运行每日综合选股';
  }
  var st=document.getElementById('dp-status');
  if(st&&msg!=null)st.textContent=msg;
  // Disable/enable history action buttons while running
  document.querySelectorAll('#dp-history-table .btn').forEach(function(b){b.disabled=loading;});
}

function getDailyPickHistoryRaw(){
  return _dailyPickHistory||[];
}

function syncDailyPickHistory(){
  return _dailyPickHistory||[];
}

function fetchDailyPickHistory(){
  return api('/quant/admin/api/daily-pick-history',{_timeout:60000}).then(function(list){
    _dailyPickHistory=Array.isArray(list)?list:[];
    return _dailyPickHistory;
  }).catch(function(){
    _dailyPickHistory=[];
    return _dailyPickHistory;
  });
}

function captureDailyPickParams(){
  return {
    strategies:getDailyPickStrategies(),
    topN:Math.max(5,Math.min(20,parseInt(document.getElementById('dp-top-n').value)||8)),
    topStrategyCount:Math.max(2,Math.min(5,parseInt(document.getElementById('dp-top-strategy-count').value)||3)),
    finalLimit:Math.max(3,Math.min(8,parseInt(document.getElementById('dp-final-limit').value)||8)),
    initialCapital:Number(document.getElementById('rec-capital').value)||500000,
    riskTolerance:document.getElementById('rec-risk').value||'medium',
    rebalanceDays:Number(document.getElementById('rec-rebalance').value)||5,
    startDate:document.getElementById('rec-start').value||'2025-06-01',
    endDate:(document.getElementById('rec-end').value||'').trim()
  };
}

function summarizeDailyPickResult(d){
  var ranking=Array.isArray(d&&d.strategyRanking)?d.strategyRanking:[];
  var best=null;
  for(var i=0;i<ranking.length;i++){
    if(ranking[i]&&ranking[i].rank===1){best=ranking[i];break;}
  }
  var finalSymbols=Array.isArray(d&&d.finalSymbols)?d.finalSymbols:[];
  return {
    bestStrategy:(best&&(best.strategyLabel||best.strategy))||'-',
    bestScore:(best&&best.compositeScore!=null)?Number(best.compositeScore).toFixed(1):'-',
    finalCount:finalSymbols.length,
    finalSymbols:finalSymbols
  };
}

function formatDailyPickTime(ts){
  if(!ts)return '-';
  try{return new Date(ts).toLocaleString('zh-CN');}catch(_e){return String(ts);}
}

function saveDailyPickHistoryEntry(d){
  var entry={
    id:Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8),
    createdAt:new Date().toISOString(),
    params:captureDailyPickParams(),
    summary:summarizeDailyPickResult(d),
    result:d
  };
  api('/quant/admin/api/daily-pick-history/save',{
    method:'POST',
    body:JSON.stringify(entry)
  }).then(function(){
    return fetchDailyPickHistory();
  }).then(function(){
    renderDailyPickHistory();
  }).catch(function(e){
    console.warn('saveDailyPickHistoryEntry failed',e);
    toast('⚠️ 历史记录保存失败: '+e.message);
  });
}

function renderDailyPickHistory(){
  var statusEl=document.getElementById('dp-history-status');
  var panelEl=document.getElementById('dp-history-panel');
  var tbody=document.querySelector('#dp-history-table tbody');
  if(!statusEl||!panelEl||!tbody)return;

  var list=syncDailyPickHistory();
  if(!list.length){
    statusEl.textContent='暂无历史';
    panelEl.style.display='none';
    tbody.innerHTML='';
    return;
  }

  statusEl.textContent='共 '+list.length+' 条历史记录';
  panelEl.style.display='block';

  var rows='';
  list.forEach(function(entry,idx){
    var p=entry.params||{};
    var sum=entry.summary||{};
    var strategies=Array.isArray(p.strategies)?p.strategies.join(', '):'-';
    var symbols=Array.isArray(sum.finalSymbols)?sum.finalSymbols:[];
    var symbolText=symbols.length?symbols.slice(0,8).join(', '):'-';
    rows+='<tr>';
    rows+='<td>'+(idx+1)+'</td>';
    rows+='<td>'+esc(formatDailyPickTime(entry.createdAt))+'</td>';
    rows+='<td>'+esc(strategies)+'</td>';
    rows+='<td>'+esc(sum.bestStrategy||'-')+'</td>';
    rows+='<td>'+esc(sum.bestScore||'-')+'</td>';
    rows+='<td>'+(sum.finalCount||0)+'</td>';
    rows+='<td style="max-width:320px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+esc(symbolText)+'</td>';
    rows+='<td>'+
      '<button class="btn btn-sm" onclick="restoreDailyPickHistory(\\''+entry.id+'\\')">查看</button> '+
      '<button class="btn btn-sm" onclick="applyDailyPickHistoryToRecommend(\\''+entry.id+'\\')">应用</button> '+
      '<button class="btn btn-sm btn-danger" onclick="deleteDailyPickHistory(\\''+entry.id+'\\')">删除</button>'+
      '</td>';
    rows+='</tr>';
  });
  tbody.innerHTML=rows;
}

function loadDailyPickHistory(){
  fetchDailyPickHistory().then(function(){
    renderDailyPickHistory();
  });
}

function restoreDailyPickHistory(id,callback){
  if(_dailyPickRunning){
    toast('⏳ 综合选股正在运行中，请等待完成后再操作');
    return;
  }
  var list=syncDailyPickHistory();
  var found=null;
  for(var i=0;i<list.length;i++){
    if(list[i]&&list[i].id===id){found=list[i];break;}
  }
  if(!found){
    toast('未找到历史记录');
    return;
  }

  toast('正在加载历史记录…');
  api('/quant/admin/api/daily-pick-history/detail?id='+encodeURIComponent(id),{_timeout:60000})
    .then(function(result){
      if(!result||result.error){
        toast('❌ 加载失败: '+(result&&result.error||'未知错误'));
        return;
      }
      var p=found.params||{};
      if(Array.isArray(p.strategies)&&p.strategies.length){
        document.querySelectorAll('.dp-strategy').forEach(function(el){
          el.checked=p.strategies.indexOf(el.value)>=0;
        });
      }
      if(p.topN!=null)document.getElementById('dp-top-n').value=p.topN;
      if(p.topStrategyCount!=null)document.getElementById('dp-top-strategy-count').value=p.topStrategyCount;
      if(p.finalLimit!=null)document.getElementById('dp-final-limit').value=p.finalLimit;
      if(p.startDate)document.getElementById('rec-start').value=p.startDate;
      document.getElementById('rec-end').value=p.endDate||'';
      if(p.initialCapital!=null)document.getElementById('rec-capital').value=p.initialCapital;
      if(p.rebalanceDays!=null)document.getElementById('rec-rebalance').value=p.rebalanceDays;
      if(p.riskTolerance){
        var riskSel=document.getElementById('rec-risk');
        for(var j=0;j<riskSel.options.length;j++){
          if(riskSel.options[j].value===p.riskTolerance){riskSel.selectedIndex=j;break;}
        }
      }
      renderDailyCompositePick(result,{skipHistory:true});
      setDailyPickLoading(false,'已加载历史记录：'+formatDailyPickTime(found.createdAt));
      toast('✅ 已恢复历史综合选股结果');
      if(typeof callback==='function')callback();
    })
    .catch(function(e){
      toast('❌ 加载历史记录失败: '+e.message);
    });
}

function applyDailyPickHistoryToRecommend(id){
  restoreDailyPickHistory(id,function(){
    applyDailyPickToRecommend();
  });
}

function deleteDailyPickHistory(id){
  if(_dailyPickRunning){
    toast('⏳ 综合选股正在运行中，请等待完成后再操作');
    return;
  }
  if(!confirm('确认删除这条每日综合选股历史记录吗？'))return;
  api('/quant/admin/api/daily-pick-history/delete',{
    method:'POST',
    body:JSON.stringify({id:id}),
    _timeout:30000
  }).then(function(){
    return fetchDailyPickHistory();
  }).then(function(){
    renderDailyPickHistory();
    toast('🗑️ 历史记录已删除');
  }).catch(function(e){
    toast('❌ 删除失败: '+e.message);
  });
}

function runDailyCompositePick(){
  if(_dailyPickRunning){
    toast('⏳ 综合选股正在运行中，请等待完成后再操作');
    return;
  }
  var strategies=getDailyPickStrategies();
  if(!strategies.length){
    toast('请至少选择一个策略');
    return;
  }

  var topN=Math.max(5,Math.min(20,parseInt(document.getElementById('dp-top-n').value)||8));
  var topStrategyCount=Math.max(2,Math.min(5,parseInt(document.getElementById('dp-top-strategy-count').value)||3));
  var finalLimit=Math.max(3,Math.min(8,parseInt(document.getElementById('dp-final-limit').value)||8));
  var endVal=document.getElementById('rec-end').value.trim();

  var body={
    strategies:strategies,
    top_n_per_strategy:topN,
    top_strategy_count:topStrategyCount,
    final_stock_limit:finalLimit,
    initial_capital:Number(document.getElementById('rec-capital').value)||500000,
    risk_tolerance:document.getElementById('rec-risk').value||'medium',
    rebalance_days:Number(document.getElementById('rec-rebalance').value)||5,
    start_date:document.getElementById('rec-start').value||'2025-06-01'
  };
  if(endVal)body.end_date=endVal;
  if(_selectedAiphaStrategies.length>0)body.aipha_strategies=_selectedAiphaStrategies;

  document.getElementById('dp-results').style.display='none';
  _dailyPickRunning=true;
  setDailyPickLoading(true,'正在执行：策略筛选 → 策略回测排行 → 合并候选 → 8股内回测排行（约1-4分钟）…');
  toast('正在运行每日综合选股…');

  api('/quant/admin/api/daily-picks',{method:'POST',body:JSON.stringify(body),_timeout:1800000})
    .then(function(d){
      _dailyPickRunning=false;
      if(d.error){
        setDailyPickLoading(false,'❌ 每日综合选股失败: '+d.error);
        toast('❌ '+d.error);
        return;
      }
      setDailyPickLoading(false,'每日综合选股完成');
      renderDailyCompositePick(d);
    })
    .catch(function(e){
      _dailyPickRunning=false;
      setDailyPickLoading(false,'❌ 每日综合选股失败: '+e.message);
    });
}

function renderDailyCompositePick(d){
  var opts=arguments[1]||{};
  _dailyPickState=d;
  if(!opts.skipHistory)saveDailyPickHistoryEntry(d);
  document.getElementById('dp-results').style.display='block';

  function isNum(v){return typeof v==='number'&&isFinite(v)}
  function f2(v){return isNum(v)?Number(v).toFixed(2):'-'}
  function fPct(v){return isNum(v)?pct(Number(v)):'-'}
  function fDD(v){return isNum(v)?('-'+Math.abs(Number(v)).toFixed(2)+'%'):'-'}
  function colorPct(v){return isNum(v)?pctColor(Number(v)):'var(--text)'}

  var ranking=Array.isArray(d.strategyRanking)?d.strategyRanking:[];
  var selected=Array.isArray(d.selectedStrategies)?d.selectedStrategies:[];
  var combined=Array.isArray(d.combinedList)?d.combinedList:[];
  var finalSymbols=Array.isArray(d.finalSymbols)?d.finalSymbols:[];
  var stockBt=Array.isArray(d.stockBacktestRanking)?d.stockBacktestRanking:[];
  var poolBt=Array.isArray(d.poolBacktestRanking)?d.poolBacktestRanking:[];

  var bestLabel='-';
  var bestScore='-';
  for(var i=0;i<ranking.length;i++){
    if(ranking[i].rank===1){
      bestLabel=ranking[i].strategyLabel||ranking[i].strategy||'-';
      bestScore=isNum(ranking[i].compositeScore)?Number(ranking[i].compositeScore).toFixed(1):'-';
      break;
    }
  }

  var okStockBt=0;
  for(var si=0;si<stockBt.length;si++){
    if(stockBt[si]&&stockBt[si].error==null&&stockBt[si].rank!=null)okStockBt++;
  }

  document.getElementById('dp-kpis').innerHTML=
    kpiHtml(esc(bestLabel),'最佳策略')+
    kpiHtml(bestScore,'策略综合分')+
    kpiHtml(finalSymbols.length+'ֻ','最终入围')+
    kpiHtml(selected.length+'个','入围策略')+
    kpiHtml(okStockBt+'ֻ','单股可回测');

  // Strategy ranking table
  var stBody=document.querySelector('#dp-strategy-table tbody');
  var stRows='';
  ranking.forEach(function(r,rIdx){
    var isTop=r.rank===1;
    var style=isTop?'background:rgba(63,185,80,.08)':'';
    var hasDetail=!r.error&&r.backtestDetail;
    stRows+='<tr style="'+style+';'+(hasDetail?'cursor:pointer':'') +'" '+(hasDetail?'onclick="showStrategyDetail('+rIdx+')" title="点击查看详细计算过程"':'')+'>';
    stRows+='<td>'+(r.rank==null?'-':(isTop?'🏆 ':'')+r.rank)+'</td>';
    stRows+='<td>'+(hasDetail?'<span style="color:var(--accent);text-decoration:underline">':'') +esc(r.strategyLabel||r.strategy||'-')+(hasDetail?'</span>':'')+'</td>';
    stRows+='<td>'+(r.screenCount!=null?r.screenCount:'-')+'</td>';
    stRows+='<td style="color:'+colorPct(r.totalReturn)+'">'+fPct(r.totalReturn)+'</td>';
    stRows+='<td style="color:var(--red)">'+fDD(r.maxDrawdown)+'</td>';
    stRows+='<td>'+(isNum(r.winRate)?Number(r.winRate).toFixed(1)+'%':'-')+'</td>';
    stRows+='<td>'+f2(r.sharpeRatio)+'</td>';
    stRows+='<td>'+f2(r.profitFactor)+'</td>';
    stRows+='<td>'+(isNum(r.compositeScore)?Number(r.compositeScore).toFixed(1):'-')+'</td>';
    if(r.error){
      stRows+='<td><span class="badge badge-red">'+esc(r.error)+'</span></td>';
    }else if(r.rank!=null&&r.rank<=3){
      stRows+='<td><span class="badge badge-green">入围</span></td>';
    }else{
      stRows+='<td><span class="badge badge-yellow">观察</span></td>';
    }
    stRows+='</tr>';
  });
  stBody.innerHTML=stRows||'<tr><td colspan="10" style="color:var(--text-dim)">暂无数据</td></tr>';

  // Combined list table
  var cbBody=document.querySelector('#dp-combined-table tbody');
  var cbRows='';
  var routingMap={};
  var routingArr=Array.isArray(d.routingEnrichment)?d.routingEnrichment:[];
  routingArr.forEach(function(re){if(re&&re.symbol&&re.routing)routingMap[re.symbol]=re.routing;});
  combined.forEach(function(item,itemIdx){
    var selectedBy=(item.selectedBy||[]).map(function(s){
      return '<span class="badge badge-blue" style="margin:1px">'+esc(s)+'</span>';
    }).join(' ');
    var details=(item.sourceDetails||[]).map(function(src){
      return esc(src.strategyLabel||src.strategy||'-')+'#'+(src.candidateRank||'-')+'('+f2(src.screenScore)+')';
    }).join(' | ');
    var itemSym=String(item.symbol||'');
    var itemSymJs=itemSym.replace(/\\\\/g,'\\\\\\\\').replace(/'/g,"\\\\'" );
    var rt=routingMap[itemSym];
    var routingCell='-';
    if(rt){
      var rtBadge=rt.source==='data-driven'?'badge-green':'badge-yellow';
      routingCell='<span class="badge '+rtBadge+'" style="margin:1px" title="'+esc(rt.reason||'')+'">'+esc(rt.combo||'-')+'</span>';
      routingCell+='<br><span style="font-size:10px;color:var(--text-dim)">'+esc(rt.profileKey||'')+'</span>';
    }
    cbRows+='<tr style="cursor:pointer;'+(item.selected?'background:rgba(88,166,255,.07)':'')+'" onclick="showStockDetail('+itemIdx+')" title="点击查看评分详情">';
    cbRows+='<td>'+(item.rank||'-')+'</td>';
    cbRows+='<td><strong><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(itemSym)+'" target="_blank" style="color:var(--accent);text-decoration:underline;cursor:pointer" onclick="event.stopPropagation()">'+esc(itemSym||'-')+'</a></strong></td>';
    cbRows+='<td>'+esc(item.name||item.symbol||'-')+'</td>';
    cbRows+='<td>'+num(item.price)+'</td>';
    cbRows+='<td style="color:'+colorPct(item.changePct)+'">'+fPct(item.changePct)+'</td>';
    cbRows+='<td><strong>'+f2(item.combinedScore)+'</strong></td>';
    if(item.backtestError){
      cbRows+='<td colspan="4" style="color:var(--text-dim);font-size:11px" title="'+esc(item.backtestError)+'"><span class="badge badge-red">'+esc(item.backtestError)+'</span></td>';
    }else{
      cbRows+='<td style="color:'+colorPct(item.backtestReturn)+'">'+fPct(item.backtestReturn)+'</td>';
      cbRows+='<td>'+f2(item.backtestSharpe)+'</td>';
      cbRows+='<td style="color:var(--red)">'+fDD(item.backtestDrawdown)+'</td>';
      cbRows+='<td>'+(isNum(item.backtestWinRate)?Number(item.backtestWinRate).toFixed(1)+'%':'-')+'</td>';
    }
    cbRows+='<td>'+(item.appearanceCount||0)+'</td>';
    cbRows+='<td>'+selectedBy+'</td>';
    cbRows+='<td>'+routingCell+'</td>';
    cbRows+='<td style="font-size:11px">'+(details||'-')+'</td>';
    cbRows+='<td>'+(item.selected?'<span class="badge badge-green">入围8股</span>':'<span class="badge badge-yellow">候补</span>')+'</td>';
    cbRows+='</tr>';
  });
  cbBody.innerHTML=cbRows||'<tr><td colspan="15" style="color:var(--text-dim)">暂无数据</td></tr>';

  // Single-stock backtest ranking
  var sbBody=document.querySelector('#dp-stock-bt-table tbody');
  var sbRows='';
  stockBt.forEach(function(r){
    var src=(r.selectedBy||[]).join('、');
    var stockSym=String(r.symbol||'');
    var stockSymJs=stockSym.replace(/\\\\/g,'\\\\\\\\').replace(/'/g,"\\\\'" );
    sbRows+='<tr'+(r.rank===1?' style="background:rgba(63,185,80,.08)"':'')+'>';
    sbRows+='<td>'+(r.rank==null?'-':(r.rank===1?'🏆 ':'')+r.rank)+'</td>';
    sbRows+='<td style="cursor:pointer" onclick="viewStock(\\''+stockSymJs+'\\')"><strong><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(stockSym)+'" target="_blank" style="color:var(--accent);text-decoration:underline;cursor:pointer">'+esc(stockSym||'-')+'</a></strong></td>';
    sbRows+='<td>'+esc(r.name||r.symbol||'-')+'</td>';
    sbRows+='<td>'+esc(r.strategyLabel||r.strategy||'-')+'</td>';
    sbRows+='<td style="font-size:11px">'+esc(src||'-')+'</td>';
    sbRows+='<td style="color:'+colorPct(r.totalReturn)+'">'+fPct(r.totalReturn)+'</td>';
    sbRows+='<td style="color:var(--red)">'+fDD(r.maxDrawdown)+'</td>';
    sbRows+='<td>'+(isNum(r.winRate)?Number(r.winRate).toFixed(1)+'%':'-')+'</td>';
    sbRows+='<td>'+f2(r.sharpeRatio)+'</td>';
    sbRows+='<td>'+(isNum(r.compositeScore)?Number(r.compositeScore).toFixed(1):'-')+'</td>';
    if(r.error)sbRows+='<td><span class="badge badge-red">'+esc(r.error)+'</span></td>';
    else sbRows+='<td><span class="badge badge-green">有效</span></td>';
    sbRows+='</tr>';
  });
  sbBody.innerHTML=sbRows||'<tr><td colspan="11" style="color:var(--text-dim)">暂无数据</td></tr>';

  // Pool strategy ranking on final symbols
  var pbBody=document.querySelector('#dp-pool-bt-table tbody');
  var pbRows='';
  poolBt.forEach(function(r){
    pbRows+='<tr'+(r.rank===1?' style="background:rgba(63,185,80,.08)"':'')+'>';
    pbRows+='<td>'+(r.rank==null?'-':(r.rank===1?'🏆 ':'')+r.rank)+'</td>';
    pbRows+='<td>'+esc(r.strategyLabel||r.strategy||'-')+'</td>';
    pbRows+='<td style="color:'+colorPct(r.totalReturn)+'">'+fPct(r.totalReturn)+'</td>';
    pbRows+='<td style="color:var(--red)">'+fDD(r.maxDrawdown)+'</td>';
    pbRows+='<td>'+(isNum(r.winRate)?Number(r.winRate).toFixed(1)+'%':'-')+'</td>';
    pbRows+='<td>'+f2(r.sharpeRatio)+'</td>';
    pbRows+='<td>'+f2(r.profitFactor)+'</td>';
    pbRows+='<td>'+(isNum(r.compositeScore)?Number(r.compositeScore).toFixed(1):'-')+'</td>';
    if(r.error)pbRows+='<td><span class="badge badge-red">'+esc(r.error)+'</span></td>';
    else if(r.rank!=null&&r.rank<=3)pbRows+='<td><span class="badge badge-green">优先</span></td>';
    else pbRows+='<td><span class="badge badge-yellow">参考</span></td>';
    pbRows+='</tr>';
  });
  pbBody.innerHTML=pbRows||'<tr><td colspan="9" style="color:var(--text-dim)">暂无数据</td></tr>';

  toast('✅ 每日综合选股完成，可一键应用到建仓推荐');
}

function applyDailyPickToRecommend(){
  if(!_dailyPickState||!Array.isArray(_dailyPickState.finalSymbols)||!_dailyPickState.finalSymbols.length){
    toast('请先运行每日综合选股');
    return;
  }
  document.getElementById('rec-symbols').value=_dailyPickState.finalSymbols.join(',');
  var best=Array.isArray(_dailyPickState.selectedStrategies)&&_dailyPickState.selectedStrategies.length?_dailyPickState.selectedStrategies[0]:null;
  if(best&&best.strategy){
    var stratSel=document.getElementById('rec-strategy');
    for(var i=0;i<stratSel.options.length;i++){
      if(stratSel.options[i].value===best.strategy){stratSel.selectedIndex=i;break;}
    }
  }
  localStorage.setItem('openclaw-rec-symbols',document.getElementById('rec-symbols').value||'');
  toast('✅ 已应用每日综合选股结果到建仓推荐');
}

/* ══════ RECOMMEND ══════ */
function runRecommend(){
  var btn=document.getElementById('btn-rec-run');
  btn.disabled=true;btn.textContent='⏳ 正在分析…';
  _lastRecommendData=null;
  _recExecuted=false;
  document.getElementById('rec-status').textContent='正在运行回测+实时数据分析，请稍候（可能需要30-60秒）…';
  document.getElementById('rec-results').style.display='none';
  var execStatusEl=document.getElementById('rec-exec-status');
  if(execStatusEl){execStatusEl.textContent='';}
  var execResultEl=document.getElementById('rec-exec-result');
  if(execResultEl){execResultEl.innerHTML='';}
  toast('正在生成建仓推荐…');

  var endVal=document.getElementById('rec-end').value.trim();
  var symVal=document.getElementById('rec-symbols').value.trim();
  if(symVal)localStorage.setItem('openclaw-rec-symbols',symVal);
  var body={
    capital:Number(document.getElementById('rec-capital').value)||500000,
    target_capital:Number(document.getElementById('rec-target-capital').value)||0,
    strategy:document.getElementById('rec-strategy').value,
    risk_tolerance:document.getElementById('rec-risk').value,
    rebalance_days:Number(document.getElementById('rec-rebalance').value)||5,
    start_date:document.getElementById('rec-start').value||'2025-06-01',
    symbols:symVal,
    exclude_star:document.getElementById('rec-exclude-star').checked
  };
  if(endVal)body.end_date=endVal;

  api('/quant/admin/api/recommend',{method:'POST',body:JSON.stringify(body),_timeout:600000})
    .then(function(d){
      btn.disabled=false;btn.textContent='🎯 生成建仓推荐';
      document.getElementById('rec-status').textContent='分析完成';
      if(d.error){toast('❌ '+d.error);return;}
      renderRecommend(d);
    })
    .catch(function(e){
      btn.disabled=false;btn.textContent='🎯 生成建仓推荐';
      document.getElementById('rec-status').textContent='分析失败: '+e.message;
    });
}

function executeRecommend(dryRun,forcePremarket){
  if(_recExecRunning){
    return;
  }
  if(_recExecuted&&!dryRun){
    toast('已执行过建仓，请重新生成推荐后再下单');
    return;
  }
  var data=_lastRecommendData;
  if(!data||!Array.isArray(data.allocations)||data.allocations.length===0){
    toast('请先生成建仓推荐');
    return;
  }

  var selectedAllocs=getSelectedAllocations();
  if(selectedAllocs.length===0){
    toast('请至少勾选一只股票');
    return;
  }

  if(!dryRun&&!forcePremarket){
    var modeHintEl=document.getElementById('rec-exec-mode-hint');
    var modeText=modeHintEl&&modeHintEl.style.color==='var(--red)'?'【实盘模式】将提交真实买入委托':'【模拟盘】将记录模拟买入委托';
    var ok=confirm(modeText+'（已选 '+selectedAllocs.length+' 只）并创建条件单，是否继续？');
    if(!ok){
      return;
    }
  }
  if(forcePremarket){
    var ok2=confirm('将跳过交易时段检查，直接提交限价预埋委托到QMT。\\n委托将在开盘后按限价撮合。是否继续？');
    if(!ok2){
      return;
    }
  }

  var pctVal=Number(document.getElementById('rec-exec-pct').value);
  if(!isFinite(pctVal))pctVal=60;
  pctVal=Math.max(10,Math.min(100,Math.round(pctVal)));

  var trailingVal=Number(document.getElementById('rec-exec-trailing').value);
  if(!isFinite(trailingVal))trailingVal=8;
  trailingVal=Math.max(2,Math.min(20,trailingVal));

  var createCond=document.getElementById('rec-exec-conds').checked;
  var statusEl=document.getElementById('rec-exec-status');
  var resultEl=document.getElementById('rec-exec-result');
  var dryBtn=document.getElementById('btn-rec-dry-run');
  var liveBtn=document.getElementById('btn-rec-exec');

  _recExecRunning=true;
  dryBtn.disabled=true;
  liveBtn.disabled=true;
  statusEl.style.color='var(--text-dim)';
  statusEl.textContent=forcePremarket?'正在提交预埋委托…':(dryRun?'正在试运行执行计划…':'正在提交真实委托，请稍候…');
  resultEl.innerHTML='';

  api('/quant/admin/api/recommend-execute',{
    method:'POST',
    body:JSON.stringify({
      dry_run:dryRun,
      force_premarket:!!forcePremarket,
      execute_pct:pctVal,
      create_conditional_orders:createCond,
      trailing_stop_pct:trailingVal,
      allocations:selectedAllocs
    })
  })
    .then(function(resp){
      if(resp.error){
        statusEl.style.color='var(--red)';
        statusEl.textContent='执行失败: '+resp.error;
        if(resp.hint){
          resultEl.innerHTML='<div class="card" style="border:1px solid var(--red);background:rgba(248,81,73,.08)">'+esc(resp.hint)+'</div>';
        }
        toast('❌ '+resp.error);
        return;
      }

      var plan=resp.plan||{};
      var items=Array.isArray(plan.items)?plan.items:[];
      var skipped=Array.isArray(plan.skipped)?plan.skipped:[];

      if(resp.dryRun){
        statusEl.style.color='var(--green)';
        statusEl.textContent='试运行完成：计划下单 '+(plan.totalOrders||0)+' 笔，预计金额 ¥'+numInt(plan.estimatedAmount||0)+'。';
        var html='<div class="card" style="padding:10px">';
        html+='<div style="font-size:12px;color:var(--text-dim);margin-bottom:6px">按第1笔入场比例 '+(plan.executePct||pctVal)+'% 生成执行计划（未下单）</div>';
        if(items.length){
          html+='<div style="overflow-x:auto"><table><thead><tr><th>代码</th><th>数量</th><th>限价</th><th>金额</th></tr></thead><tbody>';
          items.forEach(function(it){
            html+='<tr><td>'+esc(it.code)+'</td><td>'+numInt(it.quantity)+'</td><td>'+num(it.limitPrice)+'</td><td>¥'+numInt(it.notional)+'</td></tr>';
          });
          html+='</tbody></table></div>';
        }
        if(skipped.length){
          html+='<div style="margin-top:8px;font-size:12px;color:var(--orange)">跳过 '+skipped.length+' 只：'+skipped.map(function(s){return esc(s.code+':'+s.reason)}).join('；')+'</div>';
        }
        html+='</div>';
        resultEl.innerHTML=html;
        toast('✅ 试运行完成');
        return;
      }

      var exec=resp.execution||{};
      var submitted=Number(exec.submitted)||0;
      var rejected=Number(exec.rejected)||0;
      var conds=Number(exec.conditionalOrders)||0;
      statusEl.style.color=rejected>0?'var(--orange)':'var(--green)';
      statusEl.textContent='执行完成：已提交 '+submitted+' 笔，失败 '+rejected+' 笔，创建条件单 '+conds+' 个。';

      var rows='';
      (exec.orders||[]).forEach(function(o){
        rows+='<tr><td>'+esc(o.code)+'</td><td>'+numInt(o.quantity)+'</td><td>'+num(o.price)+'</td><td>'+esc(o.orderId)+'</td><td>'+esc(o.status)+'</td><td>'+numInt((o.conditionals||[]).length)+'</td></tr>';
      });
      var failHtml='';
      if(Array.isArray(exec.failures)&&exec.failures.length){
        failHtml='<div style="margin-top:8px;font-size:12px;color:var(--orange)">失败明细：'+exec.failures.map(function(f){return esc(f.code+':'+f.reason)}).join('；')+'</div>';
      }
      resultEl.innerHTML='<div class="card" style="padding:10px">'+
        (rows?('<div style="overflow-x:auto"><table><thead><tr><th>代码</th><th>数量</th><th>价格</th><th>订单ID</th><th>状态</th><th>条件单</th></tr></thead><tbody>'+rows+'</tbody></table></div>'):'<div style="font-size:12px;color:var(--text-dim)">本次没有成功提交委托。</div>')+
        failHtml+
        '</div>';
      toast(rejected>0?'⚠️ 已执行，存在部分失败':'✅ 建仓委托已提交，正在等待成交确认…');
      // Track batch execution
      _recCurrentBatch=1;
      _recBatchHistory=[{batch:1,pct:pctVal,submitted:submitted}];
      // Start fill confirmation polling
      if(submitted>0){
        startFillConfirmPolling(exec.orders||[]);
      }
      // Prevent accidental re-execution with the same data
      _recExecuted=true;
    })
    .catch(function(e){
      statusEl.style.color='var(--red)';
      statusEl.textContent='执行失败: '+e.message;
    })
    .finally(function(){
      _recExecRunning=false;
      dryBtn.disabled=false;
      // Keep live button disabled after successful real execution
      if(_recExecuted){
        liveBtn.disabled=true;
        liveBtn.title='已执行，请重新生成推荐后再下单';
      } else {
        liveBtn.disabled=false;
        liveBtn.title='';
      }
    });
}

/* ══════ SCHEDULE QMT STRATEGY (PRE-MARKET) ══════ */
function scheduleStrategy(){
  var data=_lastRecommendData;
  if(!data||!Array.isArray(data.allocations)||data.allocations.length===0){
    toast('请先生成建仓推荐');
    return;
  }

  var selectedAllocs=getSelectedAllocations();
  if(selectedAllocs.length===0){
    toast('请至少勾选一只股票');
    return;
  }

  var ok=confirm('将在 Windows MiniQMT 上生成预埋策略文件，开盘时自动提交限价买单（已选 '+selectedAllocs.length+' 只）。是否继续？');
  if(!ok) return;

  var pctVal=Number(document.getElementById('rec-exec-pct').value);
  if(!isFinite(pctVal))pctVal=60;
  pctVal=Math.max(10,Math.min(100,Math.round(pctVal)));

  var statusEl=document.getElementById('rec-exec-status');
  var resultEl=document.getElementById('rec-exec-result');
  var schedBtn=document.getElementById('btn-rec-schedule');

  schedBtn.disabled=true;
  statusEl.style.color='var(--text-dim)';
  statusEl.textContent='正在生成 MiniQMT 预埋策略…';
  resultEl.innerHTML='';

  api('/quant/admin/api/recommend-schedule-strategy',{
    method:'POST',
    body:JSON.stringify({
      execute_pct:pctVal,
      allocations:selectedAllocs
    })
  })
    .then(function(resp){
      if(resp.error){
        statusEl.style.color='var(--red)';
        statusEl.textContent='生成失败: '+resp.error;
        toast('❌ '+resp.error);
        return;
      }

      var strat=resp.strategy||{};
      var orders=Array.isArray(resp.orders)?resp.orders:[];
      var skipped=Array.isArray(resp.skipped)?resp.skipped:[];

      statusEl.style.color='var(--green)';
      statusEl.textContent='✅ 预埋策略已生成，共 '+orders.length+' 笔委托';

      var html='<div class="card" style="padding:10px;border:1px solid rgba(13,148,136,.35)">';
      html+='<div style="font-size:13px;font-weight:600;margin-bottom:8px">📋 MiniQMT 预埋策略</div>';
      html+='<div style="font-size:12px;color:var(--text-dim);margin-bottom:6px">';
      html+='策略文件: <code style="color:var(--accent)">'+esc(strat.filepath||strat.filename||'')+'</code></div>';
      html+='<div style="font-size:12px;color:var(--text-dim);margin-bottom:8px">';
      html+='请在 MiniQMT 中加载此策略文件并运行，开盘时将自动提交以下委托：</div>';

      if(orders.length){
        html+='<div style="overflow-x:auto"><table><thead><tr><th>代码</th><th>名称</th><th>数量</th><th>限价</th><th>金额</th></tr></thead><tbody>';
        orders.forEach(function(o){
          html+='<tr><td>'+esc(o.code)+'</td><td>'+esc(o.name)+'</td><td>'+numInt(o.quantity)+'</td><td>'+num(o.price)+'</td><td>¥'+numInt(o.quantity*o.price)+'</td></tr>';
        });
        html+='</tbody></table></div>';
      }
      if(skipped.length){
        html+='<div style="margin-top:8px;font-size:12px;color:var(--orange)">跳过 '+skipped.length+' 只：'+skipped.map(function(s){return esc(s.code+':'+s.reason)}).join('；')+'</div>';
      }
      html+='<div style="margin-top:10px;font-size:12px;padding:8px;background:rgba(13,148,136,.08);border-radius:6px">';
      html+='<strong>使用说明：</strong><br>';
      html+='1. 在 Windows 上打开 MiniQMT<br>';
      html+='2. 新建策略 → 加载文件: <code>'+esc(strat.filename||'')+'</code><br>';
      html+='3. 运行策略，等待开盘自动下单<br>';
      html+='4. 策略会在集合竞价/开盘时自动提交限价委托</div>';
      html+='</div>';
      resultEl.innerHTML=html;
      toast('✅ 预埋策略已生成');
    })
    .catch(function(e){
      statusEl.style.color='var(--red)';
      statusEl.textContent='生成失败: '+e.message;
    })
    .finally(function(){
      schedBtn.disabled=false;
    });
}

/* ══════ FILL CONFIRMATION POLLING ══════ */
var _fillPollTimer=null;
var _fillPollOrderIds=[];
var _fillPollOrders=[];
var _fillConfirmed=false;
var _fillPollCount=0;
var FILL_POLL_INTERVAL=5000;
var FILL_POLL_MAX=120; // stop after 10 minutes

function startFillConfirmPolling(executedOrders){
  // Show panel
  _fillConfirmed=false;
  _fillPollCount=0;
  _fillPollOrders=executedOrders;
  _fillPollOrderIds=executedOrders.map(function(o){return o.orderId});
  document.getElementById('rec-fill-confirm').style.display='block';
  document.getElementById('btn-fill-monitor').disabled=true;
  document.getElementById('fill-confirm-status').textContent='正在等待订单成交… (0/'+_fillPollOrderIds.length+')';
  document.getElementById('fill-confirm-status').style.color='var(--yellow)';
  // Initial render
  renderFillTable(_fillPollOrders.map(function(o){
    return {orderId:o.orderId,symbol:o.code,status:o.status,quantity:o.quantity,filledQty:0,avgFillPrice:null,hasPosition:false,positionQty:0};
  }));
  // Start polling
  if(_fillPollTimer){clearInterval(_fillPollTimer)}
  _fillPollTimer=setInterval(pollFillStatusOnce,FILL_POLL_INTERVAL);
  // First poll immediately
  pollFillStatusOnce();
}

function pollFillStatusOnce(){
  if(!_fillPollOrderIds.length)return;
  _fillPollCount++;
  api('/quant/admin/api/order-fill-status',{
    method:'POST',
    body:JSON.stringify({orderIds:_fillPollOrderIds})
  }).then(function(d){
    var orders=d.orders||[];
    renderFillTable(orders);
    var filledCount=orders.filter(function(o){return o.status==='filled'||o.hasPosition}).length;
    var partialCount=orders.filter(function(o){return o.status==='partial'}).length;
    var pendingCount=orders.filter(function(o){return o.status==='pending'||o.status==='submitted'}).length;
    var cancelledCount=orders.filter(function(o){return o.status==='cancelled'||o.status==='rejected'||o.status==='expired'}).length;
    var statusEl=document.getElementById('fill-confirm-status');

    if(d.allFilled||filledCount===_fillPollOrderIds.length){
      // All confirmed
      _fillConfirmed=true;
      stopFillPolling();
      statusEl.style.color='var(--green)';
      statusEl.textContent='✅ 全部成交确认完成 ('+filledCount+'/'+_fillPollOrderIds.length+')，可以进入实盘监控。';
      document.getElementById('btn-fill-monitor').disabled=false;
      document.getElementById('rec-fill-confirm').style.borderColor='rgba(63,185,80,.5)';
      toast('✅ 所有订单已成交，可进入实盘监控');
      // Auto-refresh portfolio tab data so user sees updated positions immediately
      try{refreshPortfolio();}catch(_e){}
      try{loadConditionalOrders();}catch(_e){}
      // Show next batch button if more batches remain
      updateBatchUI();
    }else if(d.anyFilled||filledCount>0){
      statusEl.style.color='var(--accent)';
      statusEl.textContent='部分成交 ('+filledCount+'/'+_fillPollOrderIds.length+')'+
        (partialCount>0?' · 部分成交 '+partialCount:'')+(pendingCount>0?' · 等待中 '+pendingCount:'')+
        ' · 轮询 #'+_fillPollCount;
      // Allow monitoring if at least some filled
      document.getElementById('btn-fill-monitor').disabled=false;
    }else{
      statusEl.style.color='var(--yellow)';
      statusEl.textContent='等待成交中… ('+filledCount+'/'+_fillPollOrderIds.length+')'+
        (cancelledCount>0?' · 已撤/拒绝 '+cancelledCount:'')+' · 轮询 #'+_fillPollCount;
    }

    // Auto-stop after max polls
    if(_fillPollCount>=FILL_POLL_MAX){
      stopFillPolling();
      if(!_fillConfirmed){
        statusEl.textContent+=' · 已达最大轮询次数，请手动检查订单状态。';
        document.getElementById('btn-fill-monitor').disabled=false;
      }
    }
  }).catch(function(e){
    document.getElementById('fill-confirm-status').textContent='查询失败: '+e.message+' · 轮询 #'+_fillPollCount;
  });
}

function stopFillPolling(){
  if(_fillPollTimer){clearInterval(_fillPollTimer);_fillPollTimer=null}
}

function renderFillTable(orders){
  var tbody=document.querySelector('#fill-confirm-table tbody');
  var statusLabels={pending:'⏳ 待提交',submitted:'📤 已提交',partial:'🔄 部分成交',filled:'✅ 已成交',cancelled:'❌ 已撤单',rejected:'🚫 被拒绝',expired:'⌛ 已过期',unknown:'❓ δ֪'};
  tbody.innerHTML=orders.map(function(o){
    var filled=o.status==='filled'||o.hasPosition;
    var rowStyle=filled?'background:rgba(63,185,80,.06)':'';
    return '<tr style="'+rowStyle+'">'+
      '<td>'+esc(o.symbol||o.orderId||'')+'</td>'+
      '<td>'+numInt(o.quantity||0)+'</td>'+
      '<td>'+numInt(o.filledQty||0)+'</td>'+
      '<td>'+(o.avgFillPrice?num(o.avgFillPrice):'-')+'</td>'+
      '<td>'+(o.hasPosition?'<span style="color:var(--green)">✅ 有持仓('+numInt(o.positionQty)+')</span>':'<span style="color:var(--text-dim)">—</span>')+'</td>'+
      '<td>'+(statusLabels[o.status]||o.status)+'</td>'+
      '</tr>';
  }).join('')||'<tr><td colspan="6" style="color:var(--text-dim)">无订单</td></tr>';
}

function startMonitoringAfterFill(){
  stopFillPolling();
  toast('🚀 成交确认完毕，进入实盘监控');
  startMonitoring();
}

function skipFillConfirm(){
  stopFillPolling();
  var ok=confirm('订单可能尚未完全成交，确定跳过确认直接进入监控？');
  if(!ok)return;
  toast('⏭️ 已跳过成交确认，进入实盘监控');
  startMonitoring();
}

/* ══════ SEQUENTIAL BATCH EXECUTION ══════ */
function executeNextBatch(){
  var data=_lastRecommendData;
  if(!data||!Array.isArray(data.allocations)||data.allocations.length===0){
    toast('推荐数据已丢失，请重新生成');
    return;
  }
  var nextBatch=_recCurrentBatch+1;
  if(nextBatch>3){
    toast('已完成所有 3 个批次建仓');
    return;
  }
  // Determine the batch percentage from allocation entryPrices (batch2=25%, batch3=15%)
  var batchKey='batch'+nextBatch;
  var sampleAlloc=data.allocations[0];
  var entryPrices=sampleAlloc&&sampleAlloc.entryPrices||{};
  var batchInfo=entryPrices[batchKey];
  var batchPct=batchInfo&&batchInfo.pct?batchInfo.pct:(nextBatch===2?25:15);
  var ok=confirm('当前已完成第 '+_recCurrentBatch+' 批次建仓。\\n即将执行第 '+nextBatch+' 批次（约 '+batchPct+'% 仓位），是否继续？');
  if(!ok)return;

  var trailingVal=Number(document.getElementById('rec-exec-trailing').value);
  if(!isFinite(trailingVal))trailingVal=8;
  trailingVal=Math.max(2,Math.min(20,trailingVal));
  var createCond=document.getElementById('rec-exec-conds').checked;

  var statusEl=document.getElementById('rec-exec-status');
  var resultEl=document.getElementById('rec-exec-result');
  statusEl.style.color='var(--text-dim)';
  statusEl.textContent='正在提交第 '+nextBatch+' 批次委托…';

  // Override execute_pct with the batch-specific percentage
  api('/quant/admin/api/recommend-execute',{
    method:'POST',
    body:JSON.stringify({
      dry_run:false,
      execute_pct:batchPct,
      batch_number:nextBatch,
      create_conditional_orders:createCond,
      trailing_stop_pct:trailingVal,
      allocations:data.allocations
    })
  }).then(function(resp){
    if(resp.error){
      statusEl.style.color='var(--red)';
      statusEl.textContent='第 '+nextBatch+' 批次执行失败: '+resp.error;
      toast('❌ '+resp.error);
      return;
    }
    var exec=resp.execution||{};
    _recCurrentBatch=nextBatch;
    _recBatchHistory.push({batch:nextBatch,pct:batchPct,submitted:exec.submitted||0});
    statusEl.style.color='var(--green)';
    statusEl.textContent='第 '+nextBatch+' 批次已提交 '+(exec.submitted||0)+' 笔委托';
    toast('✅ 第 '+nextBatch+' 批次已提交');
    // Restart fill polling for the new batch
    if(exec.submitted>0){
      startFillConfirmPolling(exec.orders||[]);
    }
    updateBatchUI();
  }).catch(function(e){
    statusEl.style.color='var(--red)';
    statusEl.textContent='第 '+nextBatch+' 批次执行失败: '+e.message;
  });
}

function updateBatchUI(){
  var nextBtn=document.getElementById('btn-fill-next-batch');
  var batchInfo=document.getElementById('fill-batch-info');
  if(!nextBtn||!batchInfo)return;
  if(_recCurrentBatch<3&&_fillConfirmed){
    nextBtn.style.display='';
    nextBtn.textContent='📦 执行第 '+(_recCurrentBatch+1)+' 批次';
    batchInfo.style.display='';
    batchInfo.textContent='已完成: '+_recBatchHistory.map(function(b){return '第'+b.batch+'批('+b.pct+'%, '+b.submitted+'笔)'}).join(' → ')+' | 下一批次: 第'+(_recCurrentBatch+1)+'批';
  }else if(_recCurrentBatch>=3){
    nextBtn.style.display='none';
    batchInfo.style.display='';
    batchInfo.textContent='✅ 全部 3 个批次建仓已完成';
    batchInfo.style.color='var(--green)';
  }else{
    nextBtn.style.display='none';
    batchInfo.style.display='none';
  }
}

/* ══════ STRATEGY FITNESS INTEGRATION ══════ */
var _fitnessGlobalRanking=null;
var _fitnessLoading=false;
var _selectedAiphaStrategies=[];
var _fitnessStratNames={
  stable_compounder:'稳健复利',high_delivery_expect:'高送转预期',buyback_signal:'回购信号',
  turnaround_play:'困境反转',dividend_quality:'红利质量',value_quality:'价值质量',
  insider_buying:'内部人增持',earnings_momentum:'盈利动量',garp_select:'GARP选股',
  sector_rotation:'行业轮动',momentum:'趋势动量',value:'价值投资',growth:'成长投资',
  swing:'波段交易',mixed:'混合策略',manipulation:'跟庄策略','spread-arbitrage':'价差套利',
  'rsi-reversal':'RSI反转','vwap-reversion':'VWAP回归','mean-reversion-zscore':'均值回归Z-Score',
  'williams-r':'威廉指标反转','momentum-breakout':'动量突破','range-breakout':'区间突破',
  'dual-ma-crossover':'双均线交叉','trend-combo':'趋势合并','double-bottom':'双底双顶',
  'macd-divergence':'MACD背离','volume-confirmation':'成交量确认',gated:'条件门控',
  scored:'加权仓位','mean-reversion':'均值回归','macd-confirmation':'MACD确认',
  'cta-kdj':'KDJ随机',composite:'综合投票','enhanced-composite':'增强综合',
  'rsi-filter':'RSI动量过滤','rsi-enhanced':'RSI增强选股','rsi-macd-combined':'RSI+MACD组合',
  'trix-momentum':'TRIX动量','mfi-oscillator':'MFI资金流','aroon-trend':'阿隆趋势',
  'cmf-divergence':'蔡金资金流背离','elder-ray':'Elder Ray力量',supertrend:'超级趋势',
  'hull-ma-crossover':'Hull MA交叉','dema-crossover':'DEMA交叉','triple-ma-crossover':'三重均线交叉',
  'ma-envelope':'MA包络','connors-rsi':'Connors RSI','kama-trend':'KAMA自适应趋势',
  demarker:'DeMARK指标','cta-trend':'CTA趋势跟踪','dual-thrust':'Dual Thrust双推力',
  'turtle-breakout':'海龟突破','macd-trend':'MACD趋势','bollinger-breakout':'布林带突破',
  'keltner-channel':'肯特纳通道','r-breaker':'R-Breaker反转',aberration:'标准差偏离',
  'ttm-squeeze':'TTM挤压','dmi-adx':'DMI+ADX','parabolic-sar':'抛物线SAR',
  'volatility-target':'波动率目标','momentum-rotation':'动量轮动','obv-trend':'OBV趋势',
  ichimoku:'一目均衡表','grid-trading':'网格交易','multi-ma-convergence':'多均线收敛',
  'donchian-mid-reversion':'唐奇安中线回归','pivot-point':'枢轴点','chandelier-exit':'烛台止损',
  'roc-momentum':'ROC动量','cci-divergence':'CCI背离','ema-crossover':'EMA均线交叉',
  'atr-trailing':'ATR追踪止损','stochastic-rsi':'随机RSI','macd-histogram-reversal':'MACD柱状反转',
  'bollinger-bandwidth':'布林带带宽','cta-awesome-oscillator':'AO振荡器',
  'cta-elder-force':'Elder力量指数','cta-demarker':'CTA DeMARK',
  'cta-ultimate-oscillator':'终极振荡器','cta-adx-breakout':'ADX突破',
  'cta-trix-trend':'TRIX趋势','cta-hull-ma-trend':'Hull MA趋势','cta-mass-index':'质量指数',
  'cta-chaikin-oscillator':'蔡金振荡器','cta-coppock':'Coppock曲线',
  'cta-rsi-divergence':'RSI背离','cta-volume-breakout':'成交量突破',
  'cta-adaptive-ma':'自适应均线','cta-ma-envelope':'CTA MA包络',
  'cta-connors-rsi':'CTA Connors RSI','cta-aroon-oscillator':'阿隆振荡器',
  'cta-mfi-trend':'MFI趋势','cta-dema-crossover':'CTA DEMA交叉',
  'cta-triple-ma':'CTA三重均线','cta-supertrend':'CTA超级趋势',
  'ema-cross':'EMA均线交叉','adx-trend':'ADX趋势强度',aroon:'阿隆指标',
  vortex:'漩涡指标',cci:'CCI顺势',trix:'TRIX平滑',dmi:'趋向指标DMI',
  'dema-cross':'DEMA交叉','tema-cross':'TEMA交叉','donchian-breakout':'唐奇安突破',
  'atr-channel':'ATR通道',envelope:'包络线','price-channel-breakout':'价格通道突破',
  stochastic:'随机振荡器',mfi:'MFI资金流',cmf:'蔡金资金流',adl:'聚散指标ADL',
  roc:'变化率ROC',vwap:'VWAP均价','adaptive-ma':'自适应均线',obv:'能量潮OBV',
  engulfing:'吞没形态',hammer:'锤子线',doji:'十字星','morning-star':'启明星',
  'three-soldiers':'三兵三鸦',harami:'孕线形态',
  'macd-rsi-combo':'MACD+RSI组合','bollinger-rsi-combo':'布林+RSI组合',
  'ma-volume-combo':'均线+量能组合','kdj-macd-combo':'KDJ+MACD组合',
  'ichimoku-adx-combo':'一目+ADX组合','multi-tf-ma':'多周期均线',
  'volatility-breakout':'波动率突破','regime-switch':'趋势/震荡切换'
};

function _loadFitnessGlobal(callback){
  if(_fitnessGlobalRanking){callback(_fitnessGlobalRanking);return;}
  if(_fitnessLoading)return;
  _fitnessLoading=true;
  var badge=document.getElementById('dp-fitness-badge');
  if(badge)badge.textContent='⏳ 加载排名...';
  api('/quant/admin/api/strategy-fitness/global',{_timeout:15000}).then(function(data){
    _fitnessLoading=false;
    var strats=data&&data.strategies?data.strategies:[];
    _fitnessGlobalRanking=strats;
    if(badge){
      if(strats.length>0)badge.textContent='✅ 已加载 '+strats.length+' 个排名策略';
      else badge.textContent='⚠️ 无适配数据，请先运行策略适配分析';
    }
    callback(strats);
  }).catch(function(e){
    _fitnessLoading=false;
    if(badge)badge.textContent='❌ 加载失败';
    toast('❌ 加载策略排名失败: '+e.message);
    callback([]);
  });
}

function selectFitnessGlobalTop(){
  _loadFitnessGlobal(function(strats){
    if(!strats.length){
      toast('⚠️ 无全局排名数据，请先在「策略适配分析」页面运行分析');
      return;
    }
    // Uncheck all, then only check fitness_optimal
    document.querySelectorAll('.dp-strategy').forEach(function(el){el.checked=false;});
    var fitnessBox=document.querySelector('.dp-strategy[value="fitness_optimal"]');
    if(fitnessBox)fitnessBox.checked=true;
    // Store all ranked aipha strategy IDs for backend
    _selectedAiphaStrategies=strats.map(function(s){return s.strategy_id;});
    _highlightFitnessRankBadges(strats);
    toast('🏆 已启用全局最优模式 — 将使用排名前 '+strats.length+' 个适配策略选股');
  });
}

function selectFitnessStockOptimal(){
  // Uncheck all, only check fitness_optimal
  document.querySelectorAll('.dp-strategy').forEach(function(el){el.checked=false;});
  var box=document.querySelector('.dp-strategy[value="fitness_optimal"]');
  if(box){
    box.checked=true;
    _selectedAiphaStrategies=[];  // empty = use all, no filter
    toast('🎯 已选中「个股最优」策略 — 将根据每只股票的历史回测最优策略进行选股');
  }else{
    toast('🎯 个股最优模式已启用 — 策略池中未找到fitness_optimal选项');
  }
}

function showFitnessRankingPanel(){
  var panel=document.getElementById('dp-fitness-panel');
  panel.style.display='block';
  var body=document.getElementById('dp-fitness-ranking-body');
  body.innerHTML='<span style="color:var(--text-dim)">⏳ 加载中...</span>';
  _loadFitnessGlobal(function(strats){
    if(!strats.length){
      body.innerHTML='<div style="text-align:center;padding:20px;color:var(--text-dim)">暂无数据<br><a href="/quant/admin/strategy-fitness" target="_blank" style="color:var(--accent)">前往策略适配分析页面运行 →</a></div>';
      return;
    }
    // Auto-apply: check fitness_optimal and store all ranked aipha IDs
    document.querySelectorAll('.dp-strategy').forEach(function(el){el.checked=false;});
    var fitnessBox=document.querySelector('.dp-strategy[value="fitness_optimal"]');
    if(fitnessBox)fitnessBox.checked=true;
    _selectedAiphaStrategies=strats.map(function(s){return s.strategy_id;});
    _highlightFitnessRankBadges(strats);
    toast('📊 已选中全部 '+strats.length+' 个排名策略，可在面板中取消勾选再点「应用勾选」');
    var maxScore=Math.max.apply(null,strats.map(function(s){return s.total_score||0}))||1;
    // Get currently checked strategies
    var checked=new Set();
    document.querySelectorAll('.dp-strategy:checked').forEach(function(el){checked.add(el.value);});
    var html='<table style="width:100%;font-size:12px"><tr>';
    html+='<th style="width:30px">选</th><th>排名</th><th>策略ID</th><th>总得分</th><th>覆盖股票</th><th>平均得分</th><th>最高</th><th>最低</th><th>得分分布</th>';
    html+='</tr>';
    strats.forEach(function(s){
      var isChecked=checked.has(s.strategy_id);
      var barW=Math.round((s.total_score/maxScore)*100);
      var rankCls=s.rank===1?'background:var(--green);color:#000':s.rank===2?'background:var(--accent);color:#000':s.rank===3?'background:var(--yellow);color:#000':'background:var(--bg3);color:var(--text)';
      html+='<tr>';
      html+='<td><input type="checkbox" class="dp-fitness-check" value="'+esc(s.strategy_id)+'" '+(isChecked?'checked':'')+' style="vertical-align:middle"></td>';
      html+='<td><span style="display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:50%;font-size:11px;font-weight:700;'+rankCls+'">#'+s.rank+'</span></td>';
      html+='<td><strong>'+esc(s.strategy_id)+'</strong>';
      var cnName=s.strategy_name||_fitnessStratNames[s.strategy_id]||'';
      if(cnName)html+='<br><span style="color:var(--text-dim);font-size:11px">'+esc(cnName)+'</span>';
      html+='</td>';
      html+='<td style="color:var(--green);font-weight:700">'+num(s.total_score)+'</td>';
      html+='<td>'+s.stock_count+'</td>';
      html+='<td>'+num(s.avg_score)+'</td>';
      html+='<td>'+num(s.max_score)+'</td>';
      html+='<td>'+num(s.min_score)+'</td>';
      html+='<td><span style="display:inline-block;height:6px;border-radius:3px;vertical-align:middle;background:var(--purple);width:'+barW+'px"></span></td>';
      html+='</tr>';
    });
    html+='</table>';
    html+='<div style="margin-top:8px;font-size:11px;color:var(--text-dim)">💡 勾选策略后点击「应用勾选」将仅选中这些策略运行综合选股</div>';
    body.innerHTML=html;
  });
}

function applyFitnessSelection(){
  var checks=document.querySelectorAll('.dp-fitness-check:checked');
  if(!checks.length){toast('请至少勾选一个策略');return;}
  var ids=[];
  checks.forEach(function(el){ids.push(el.value);});
  // Store selected aipha strategy IDs for backend
  _selectedAiphaStrategies=ids;
  // Uncheck all, then only check fitness_optimal
  document.querySelectorAll('.dp-strategy').forEach(function(el){el.checked=false;});
  var fitnessBox=document.querySelector('.dp-strategy[value="fitness_optimal"]');
  if(fitnessBox)fitnessBox.checked=true;
  document.getElementById('dp-fitness-panel').style.display='none';
  _highlightFitnessRankBadges(_fitnessGlobalRanking||[]);
  toast('✅ 已应用 '+ids.length+' 个适配策略 — 运行选股时将使用这些策略的适配数据');
}

function _highlightFitnessRankBadges(strats){
  // Remove existing badges
  document.querySelectorAll('.dp-rank-tag').forEach(function(el){el.remove();});
  if(!strats||!strats.length)return;
  var rankMap={};
  strats.forEach(function(s){rankMap[s.strategy_id]=s.rank;});
  document.querySelectorAll('.dp-strategy').forEach(function(el){
    var r=rankMap[el.value];
    if(r!=null){
      var colors=r===1?'background:var(--green);color:#000':r===2?'background:var(--accent);color:#000':r===3?'background:var(--yellow);color:#000':'background:var(--bg3);color:var(--text)';
      var badge=document.createElement('span');
      badge.className='dp-rank-tag';
      badge.style.cssText='display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:16px;border-radius:8px;font-size:9px;font-weight:700;margin-left:2px;padding:0 4px;vertical-align:middle;'+colors;
      badge.textContent='#'+r;
      badge.title='全局策略排名 #'+r;
      el.parentNode.appendChild(badge);
    }
  });
}

`;
}

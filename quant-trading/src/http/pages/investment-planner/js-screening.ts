/** Investment Planner — stock screening, rebalancing, signals, milestones, and monitoring */
export function getScreeningJs(): string {
  return `
function refreshPortfolio(){
  if(!currentPlan||!currentPlan.portfolio||!currentPlan.portfolio.length){
    document.getElementById('portfolio-kpis').innerHTML=kpiHtml('⚠️','请先生成投资计划');
    return;
  }
  toast('🔄 刷新持仓行情…');
  api('/quant/admin/api/investment-monitor',{method:'POST',body:JSON.stringify({action:'monitor',plan:currentPlan}),_timeout:60000}).then(function(d){
    var s=d.summary;
    document.getElementById('portfolio-kpis').innerHTML=[
      kpiHtml('¥'+numInt(s.totalCost),'总成本'),
      kpiHtml('¥'+numInt(s.totalValue),'总市值'),
      kpiHtml('<span style="color:'+pctColor(s.totalPnl)+'">¥'+numInt(s.totalPnl)+'</span>','总盈亏'),
      kpiHtml('<span style="color:'+pctColor(s.totalPnlPct)+'">'+pct(s.totalPnlPct)+'</span>','收益率'),
      kpiHtml(s.positionCount,'持仓数'),
      kpiHtml(s.signalCount,'活跃信号'),
      kpiHtml('<span style="color:var(--red)">'+s.urgentSignals+'</span>','紧急信号'),
    ].join('');

    var tbody=document.querySelector('#portfolio-table tbody');
    tbody.innerHTML=(d.positions||[]).map(function(p){
      var statusBadge='<span class="badge badge-green">正常</span>';
      if(p.pnlPct<=-8)statusBadge='<span class="badge badge-red">⚠ 接近止损</span>';
      else if(p.pnlPct<0)statusBadge='<span class="badge badge-yellow">浮亏</span>';
      else if(p.pnlPct>=20)statusBadge='<span class="badge badge-blue">🎯 接近止盈</span>';
      return '<tr>'+
        '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(p.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(p.symbol)+'</a></td>'+
        '<td>'+esc(p.name)+'</td>'+
        '<td>'+numInt(p.shares)+'</td>'+
        '<td>'+num(p.buyPrice)+'</td>'+
        '<td>'+num(p.currentPrice)+'</td>'+
        '<td>¥'+numInt(p.currentValue)+'</td>'+
        '<td style="color:'+pctColor(p.pnl)+'">¥'+numInt(p.pnl)+'</td>'+
        '<td style="color:'+pctColor(p.pnlPct)+'">'+pct(p.pnlPct)+'</td>'+
        '<td style="color:'+pctColor(p.changePct)+'">'+pct(p.changePct)+'</td>'+
        '<td style="color:var(--red)">'+num(p.stopLoss)+'</td>'+
        '<td style="color:var(--green)">'+num(p.takeProfit)+'</td>'+
        '<td>'+statusBadge+'</td>'+
        '</tr>';
    }).join('')||'<tr><td colspan="12" style="text-align:center;color:var(--text-dim)">暂无持仓数据</td></tr>';

    // Store signals
    if(d.signals&&d.signals.length){
      d.signals.forEach(function(s){s.timestamp=d.timestamp;signalHistory.unshift(s)});
      if(signalHistory.length>100)signalHistory=signalHistory.slice(0,100);
    }
    toast('✅ 持仓已刷新');
  });
}

/* ══════ SCREENING ══════ */
function runScreening(){
  var el=document.getElementById('screen-status');
  el.innerHTML='<span style="color:var(--accent)">⏳ 正在全市场扫描，请稍候…</span>';
  toast('正在筛选股票…');

  var screenSel=document.getElementById('screen-strategy');
  var screenVal=screenSel.value;
  var strategy=screenVal;
  var composedId=null;
  if(screenVal.indexOf('composed:')===0){
    composedId=screenVal.slice(9);
    var selOpt=screenSel.options[screenSel.selectedIndex];
    strategy=selOpt?selOpt.textContent:'auto';
  }

  var body={
    action:'screen',
    strategy:strategy,
    composed_strategy_id:composedId,
    risk_tolerance:document.getElementById('risk-tolerance')?document.getElementById('risk-tolerance').value:'medium',
    top_n:Number(document.getElementById('screen-top-n').value)||20
  };

  api('/quant/admin/api/investment-screen',{method:'POST',body:JSON.stringify(body),_timeout:120000}).then(function(d){
    el.innerHTML='<span style="color:var(--green)">✅ 筛选完成 ('+esc(d.strategy)+' / '+esc(d.riskProfile)+')</span>';
    renderScreenResults(d);
    // Cache results so browser back restores them
    try{sessionStorage.setItem('openclaw-screen-results',JSON.stringify(d))}catch(e){}
    // Auto-refresh history comparison after screening
    loadScreenerHistory();
  }).catch(function(){el.innerHTML='<span style="color:var(--red)">❌ 筛选失败</span>'});
}

function renderScreenResults(d){
    document.getElementById('screen-results').style.display='block';
    document.getElementById('screen-count').textContent=(d.candidates||[]).length;

    // Enrichment KPIs (macro regime, position guidance, lianban pool, hot stocks)
    var kpisEl=document.getElementById('screen-enrichment-kpis');
    if(kpisEl){
      var regimeLabel={'recovery':'🟢 复苏','overheating':'🟡 过热','stagflation':'🟠 滞胀','recession':'🔴 衰退'};
      var regime=regimeLabel[d.macroRegime]||('⚪ '+(d.macroRegime||'未知'));
      var kpis=[kpiHtml(regime,'宏观周期')];
      if(d.lianbanPoolCount!=null)kpis.push(kpiHtml(String(d.lianbanPoolCount),'涨停池'));
      if(d.hotStocksCount!=null)kpis.push(kpiHtml(String(d.hotStocksCount),'热股池'));
      // Position guidance from macro regime
      var pg=d.positionGuidance;
      if(pg){
        var scaleColor=pg.positionScale>=0.9?'var(--green)':pg.positionScale>=0.7?'var(--yellow)':'var(--red)';
        kpis.push(kpiHtml('<span style="color:'+scaleColor+'">'+(pg.positionScale*100).toFixed(0)+'%</span>','建议仓位'));
        kpis.push(kpiHtml(String(pg.maxPositions),'最大持仓'));
      }
      kpisEl.innerHTML=kpis.join('');
      var kpiTitle=d.macroDescription||'';
      if(pg&&pg.regimeAdjustment)kpiTitle+='\\n'+pg.regimeAdjustment;
      if(kpiTitle)kpisEl.title=kpiTitle;
    }

    var tbody=document.querySelector('#screen-table tbody');
    tbody.innerHTML=(d.candidates||[]).map(function(c,i){
      // Build inline enrichment badges
      var tags=[];
      if(c.lianban){
        var lb=c.lianban;
        var lbBadge=lb.board_count+'板';
        if(lb.quality_score>=70)tags.push('<span class="badge badge-purple" style="margin:1px" title="质量'+num(lb.quality_score)+'">🔥'+lbBadge+'</span>');
        else if(lb.quality_score>=40)tags.push('<span class="badge badge-yellow" style="margin:1px">'+lbBadge+'</span>');
        else tags.push('<span class="badge badge-red" style="margin:1px">'+lbBadge+'⚠</span>');
      }
      if(c.smartMoney){
        var sm=c.smartMoney;
        if(sm.score>=70)tags.push('<span class="badge badge-green" style="margin:1px" title="'+esc(sm.conclusion||'')+'">机构'+sm.score+'</span>');
        else if(sm.score<=30)tags.push('<span class="badge badge-red" style="margin:1px" title="'+esc(sm.conclusion||'')+'">机构'+sm.score+'</span>');
        else tags.push('<span class="badge badge-blue" style="margin:1px">机构'+sm.score+'</span>');
      }
      if(c.sentiment){
        var st=c.sentiment;
        if(st.score>=70)tags.push('<span class="badge badge-orange" style="margin:1px" title="'+esc(st.interpretation||'')+'">🔥舆情'+Math.round(st.score)+'</span>');
        else if(st.score>=40)tags.push('<span class="badge badge-sim" style="margin:1px" title="'+esc(st.interpretation||'')+'">舆情'+Math.round(st.score)+'</span>');
        else if(st.score>0)tags.push('<span class="badge" style="margin:1px;background:var(--bg-dim);color:var(--text-dim)" title="'+esc(st.interpretation||'')+'">舆情'+Math.round(st.score)+'</span>');
      }
      if(c.analystConsensus&&c.analystConsensus.total>=3){
        var ac=c.analystConsensus;
        var buyRatio=ac.buy/ac.total;
        if(buyRatio>=0.7)tags.push('<span class="badge badge-green" style="margin:1px">👍 '+ac.buy+'/'+ac.total+'看好</span>');
        else if(ac.sell>ac.buy)tags.push('<span class="badge badge-red" style="margin:1px">👎 '+ac.sell+'/'+ac.total+'看空</span>');
        else tags.push('<span class="badge badge-blue" style="margin:1px">📊 '+ac.buy+'/'+ac.total+'看好</span>');
      }
      if(c.earningsWarning)tags.push('<span class="badge badge-orange" style="margin:1px">📅 财报'+esc(c.earningsWarning)+'</span>');
      var tagHtml=tags.length?tags.join(''):'<span style="color:var(--text-dim);font-size:11px">-</span>';

      return '<tr>'+
        '<td>'+(i+1)+'</td>'+
        '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(c.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(c.symbol)+'</a></td>'+
        '<td>'+esc(c.name)+'</td>'+
        '<td>'+num(c.price)+'</td>'+
        '<td style="color:'+pctColor(c.changePct)+'">'+pct(c.changePct)+'</td>'+
        '<td>'+num(c.pe)+'</td>'+
        '<td>'+num(c.pb||0)+'</td>'+
        '<td>'+num((c.totalMarketCap||0)/1e8)+'</td>'+
        '<td>'+num(c.turnoverRate)+'</td>'+
        '<td>'+num(c.volumeRatio||0)+'</td>'+
        '<td><span class="badge badge-purple">'+c.score+'</span></td>'+
        '<td style="font-size:11px">'+tagHtml+'</td>'+
        '<td style="font-size:11px">'+(c.reasons||[]).slice(0,6).map(function(r){return '<span class="badge badge-blue" style="margin:1px">'+esc(r)+'</span>'}).join(' ')+'</td>'+
        '<td><button class="btn btn-sm" onclick="viewStock(\\''+esc(c.symbol)+'\\')">详情</button></td>'+
        '</tr>';
    }).join('')||'<tr><td colspan="14" style="text-align:center;color:var(--text-dim)">无筛选结果</td></tr>';
}

function restoreScreenResults(){
  try{
    var raw=sessionStorage.getItem('openclaw-screen-results');
    if(raw){
      var d=JSON.parse(raw);
      var el=document.getElementById('screen-status');
      if(el)el.innerHTML='<span style="color:var(--green)">✅ 筛选完成 ('+esc(d.strategy)+' / '+esc(d.riskProfile)+') — 缓存</span>';
      renderScreenResults(d);
    }
  }catch(e){}
}

/* ══════ SCREENER HISTORY ══════ */
function loadScreenerHistory(){
  var statusEl=document.getElementById('screener-history-status');
  statusEl.textContent='加载历史…';
  api('/quant/api/stocklist/history').then(function(d){
    if(!d.comparison||!d.dates||d.dates.length===0){
      statusEl.textContent='暂无历史数据，请先运行选股';
      return;
    }
    var c=d.comparison;
    statusEl.textContent='最新: '+c.date+(c.prevDate?' vs '+c.prevDate:'');
    document.getElementById('screener-history-panel').style.display='block';
    document.getElementById('screener-history-kpis').innerHTML=[
      kpiHtml(c.total,'本次入选'),
      kpiHtml('<span style="color:var(--green)">'+c.new.length+'</span>','新入选'),
      kpiHtml(c.kept.length,'连续入选'),
      kpiHtml('<span style="color:var(--red)">'+c.dropped.length+'</span>','已退出'),
      kpiHtml(d.dates.length+'天','历史记录'),
    ].join('');

    // New stocks
    document.getElementById('sh-new').innerHTML=c.new.length?c.new.map(function(s){
      return '<a href="'+B+'/quant/admin/stock?symbol='+esc(s)+'" target="_blank" class="badge badge-green" style="text-decoration:none;padding:4px 8px;cursor:pointer">🆕 '+esc(s)+'</a>';
    }).join(''):'<span style="color:var(--text-dim);font-size:12px">无</span>';

    // Kept stocks with consecutive days
    document.getElementById('sh-kept').innerHTML=c.kept.length?c.kept.map(function(s){
      var days=c.consecutive[s]||1;
      var badge=days>=5?'badge-purple':days>=3?'badge-blue':'badge-yellow';
      return '<a href="'+B+'/quant/admin/stock?symbol='+esc(s)+'" target="_blank" class="badge '+badge+'" style="text-decoration:none;padding:4px 8px;cursor:pointer">'+esc(s)+' <sup>'+days+'天</sup></a>';
    }).join(''):'<span style="color:var(--text-dim);font-size:12px">无</span>';

    // Dropped stocks
    document.getElementById('sh-dropped').innerHTML=c.dropped.length?c.dropped.map(function(s){
      return '<span class="badge badge-red" style="padding:4px 8px;opacity:0.7">'+esc(s)+'</span>';
    }).join(''):'<span style="color:var(--text-dim);font-size:12px">无</span>';

    // History table
    var dates=d.dates.slice(0,10);
    var tbody=document.querySelector('#sh-history-table tbody');
    tbody.innerHTML=dates.map(function(date){
      var entry=d.history[date]||{};
      var codes=Array.isArray(entry)?entry:(entry.codes||[]);
      var strategy=Array.isArray(entry)?'':(entry.strategy||'');
      return '<tr><td>'+esc(date)+'</td><td>'+esc(strategy||'-')+'</td><td>'+codes.length+'</td><td style="font-size:11px">'+codes.map(function(c){return esc(c)}).join(', ')+'</td></tr>';
    }).join('');
  }).catch(function(){statusEl.textContent='加载失败'});
}

/* ══════ REBALANCE ══════ */
function runRebalance(){
  if(!currentPlan||!currentPlan.portfolio||!currentPlan.portfolio.length){
    toast('⚠️ 请先生成投资计划');return;
  }
  var btn=document.getElementById('btn-rebalance');
  btn.disabled=true;btn.textContent='⏳ 正在分析…';
  var statusEl=document.getElementById('rebalance-status');
  statusEl.textContent='正在获取行情并分析调仓…';
  toast('正在生成调仓建议…');

  // Get latest screener results
  var symVal=(document.getElementById('rec-symbols').value||'').trim();
  var body={
    action:'rebalance',
    plan:currentPlan,
    newCandidates:symVal?symVal.split(',').map(function(s){return s.trim()}).filter(Boolean):[]
  };

  api('/quant/admin/api/rebalance',{method:'POST',body:JSON.stringify(body),_timeout:60000})
    .then(function(d){
      btn.disabled=false;btn.textContent='🔄 生成调仓建议';
      if(d.error){statusEl.textContent='分析失败: '+d.error;toast('❌ '+d.error);return;}
      statusEl.textContent='分析完成';
      renderRebalance(d);
    })
    .catch(function(e){
      btn.disabled=false;btn.textContent='🔄 生成调仓建议';
      statusEl.textContent='分析失败: '+e.message;
    });
}

function renderRebalance(d){
  document.getElementById('rebalance-results').style.display='block';
  var s=d.summary||{};
  document.getElementById('rebalance-kpis').innerHTML=[
    kpiHtml(s.sellCount||0,'<span style="color:var(--red)">卖出</span>'),
    kpiHtml(s.reduceCount||0,'<span style="color:var(--orange)">减仓</span>'),
    kpiHtml(s.holdCount||0,'持有'),
    kpiHtml(s.addCount||0,'<span style="color:var(--blue)">加仓</span>'),
    kpiHtml(s.buyCount||0,'<span style="color:var(--green)">新买</span>'),
  ].join('');

  // Action cards
  var actions=d.actions||[];
  var actionsEl=document.getElementById('rebalance-actions');
  var grouped={sell:[],reduce:[],hold:[],add:[],buy:[]};
  actions.forEach(function(a){
    if(grouped[a.action])grouped[a.action].push(a);
  });
  var groupLabels={sell:'🛑 卖出',reduce:'📉 减仓',hold:'✊ 持有',add:'📈 加仓',buy:'🆕 新买'};
  var groupColors={sell:'var(--red)',reduce:'var(--orange)',hold:'var(--text-dim)',add:'var(--blue)',buy:'var(--green)'};
  var html='';
  Object.keys(groupLabels).forEach(function(key){
    var items=grouped[key]||[];
    if(!items.length)return;
    html+='<div class="card" style="margin-top:10px;border-left:3px solid '+groupColors[key]+'">';
    html+='<h3>'+groupLabels[key]+' ('+items.length+'只)</h3>';
    html+='<div style="display:flex;flex-wrap:wrap;gap:6px">';
    items.forEach(function(a){
      html+='<div style="background:var(--card-bg);border:1px solid var(--border);border-radius:6px;padding:8px 12px;min-width:180px">';
      html+='<div style="font-weight:600"><a href="'+B+'/quant/admin/stock?symbol='+esc(a.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(a.symbol)+'</a> '+esc(a.name||'')+'</div>';
      html+='<div style="font-size:12px;color:var(--text-dim)">'+esc(a.reason||'')+'</div>';
      if(a.currentPrice)html+='<div style="font-size:11px;margin-top:2px">现价: '+num(a.currentPrice)+(a.changePct!=null?' <span style="color:'+pctColor(a.changePct)+'">'+pct(a.changePct)+'</span>':'')+'</div>';
      if(a.suggestedShares)html+='<div style="font-size:11px">建议: '+(a.action==='sell'||a.action==='reduce'?'卖出':'买入')+' '+numInt(a.suggestedShares)+' 股</div>';
      html+='</div>';
    });
    html+='</div></div>';
  });
  actionsEl.innerHTML=html||'<div class="card"><h3>✅ 无需调仓</h3><p style="font-size:12px;color:var(--text-dim)">当前持仓状态良好</p></div>';

  // Detail table
  var tbody=document.querySelector('#rebalance-table tbody');
  tbody.innerHTML=actions.map(function(a){
    var actionBadge={sell:'<span class="badge badge-red">卖出</span>',reduce:'<span class="badge badge-orange">减仓</span>',hold:'<span class="badge badge-yellow">持有</span>',add:'<span class="badge badge-blue">加仓</span>',buy:'<span class="badge badge-green">新买</span>'};
    return '<tr>'+
      '<td>'+(actionBadge[a.action]||esc(a.action))+'</td>'+
      '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(a.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(a.symbol)+'</a></td>'+
      '<td>'+esc(a.name||'')+'</td>'+
      '<td>'+num(a.currentPrice)+'</td>'+
      '<td style="color:'+pctColor(a.changePct)+'">'+pct(a.changePct)+'</td>'+
      '<td>'+pct(a.targetWeight||0)+'</td>'+
      '<td>'+pct(a.currentWeight||0)+'</td>'+
      '<td>'+numInt(a.suggestedShares||0)+'</td>'+
      '<td style="font-size:11px">'+esc(a.reason||'')+'</td>'+
      '</tr>';
  }).join('')||'<tr><td colspan="9" style="text-align:center;color:var(--text-dim)">无需调仓</td></tr>';

  toast('✅ 调仓建议已生成');
}

/* ══════ SIGNALS ══════ */
function loadSignals(){
  if(!currentPlan||!currentPlan.portfolio||!currentPlan.portfolio.length){
    document.getElementById('signal-kpis').innerHTML=kpiHtml('⚠️','请先生成投资计划并刷新持仓');
    return;
  }
  // Refresh portfolio to get latest signals
  api('/quant/admin/api/investment-monitor',{method:'POST',body:JSON.stringify({action:'monitor',plan:currentPlan}),_timeout:60000}).then(function(d){
    var signals=d.signals||[];
    document.getElementById('signal-kpis').innerHTML=[
      kpiHtml(signals.length,'当前信号'),
      kpiHtml(signals.filter(function(s){return s.urgency==='紧急'}).length,'<span style="color:var(--red)">紧急</span>'),
      kpiHtml(signals.filter(function(s){return s.urgency==='重要'}).length,'<span style="color:var(--orange)">重要</span>'),
      kpiHtml(signals.filter(function(s){return s.urgency==='注意'}).length,'注意'),
    ].join('');

    var sl=document.getElementById('signal-list');
    if(!signals.length){
      sl.innerHTML='<div class="card"><h3>✅ 当前无交易信号</h3><p style="font-size:12px;color:var(--text-dim)">所有持仓状态正常，无需操作</p></div>';
    }else{
      sl.innerHTML=signals.map(function(s){
        var cls=s.urgency==='紧急'?'urgent':s.urgency==='重要'?'important':'';
        var icon=s.type==='STOP_LOSS'?'🛑':s.type==='TAKE_PROFIT'?'💰':s.type==='VOLUME_SPIKE'?'📊':'⚠️';
        var actionBadge=s.action==='卖出'?'<span class="badge badge-red">'+esc(s.action)+'</span>':
                        s.action==='减仓'?'<span class="badge badge-orange">'+esc(s.action)+'</span>':
                        '<span class="badge badge-yellow">'+esc(s.action)+'</span>';
        return '<div class="card signal-card '+cls+'">'+
          '<h3>'+icon+' '+esc(s.symbol)+' '+esc(s.name)+' '+actionBadge+
          ' <span class="badge badge-'+(s.urgency==='紧急'?'red':s.urgency==='重要'?'orange':'yellow')+'">'+esc(s.urgency)+'</span></h3>'+
          '<p style="font-size:12px">'+esc(s.message)+'</p>'+
          '<div style="font-size:12px;color:var(--text-dim);margin-top:4px">'+
            '现价: '+num(s.currentPrice)+
            (s.triggerPrice?' | 触发价: '+num(s.triggerPrice):'')+
            (s.pnlPct!=null?' | 盈亏: <span style="color:'+pctColor(s.pnlPct)+'">'+pct(s.pnlPct)+'</span>':'')+
          '</div></div>';
      }).join('');
    }

    // Signal history table
    var ht=document.querySelector('#signal-history-table tbody');
    ht.innerHTML=signalHistory.slice(0,50).map(function(s){
      return '<tr>'+
        '<td style="white-space:nowrap;font-size:11px">'+esc(s.timestamp||'-')+'</td>'+
        '<td><span class="badge badge-'+(s.type==='STOP_LOSS'?'red':s.type==='TAKE_PROFIT'?'green':'yellow')+'">'+esc(s.type)+'</span></td>'+
        '<td>'+esc(s.symbol)+' '+esc(s.name||'')+'</td>'+
        '<td>'+esc(s.action)+'</td>'+
        '<td>'+num(s.currentPrice)+'</td>'+
        '<td style="color:'+(s.pnlPct>=0?'var(--green)':'var(--red)')+'">'+pct(s.pnlPct)+'</td>'+
        '<td style="font-size:11px;max-width:200px;word-break:break-all">'+esc(s.message)+'</td>'+
        '</tr>';
    }).join('')||'<tr><td colspan="7" style="text-align:center;color:var(--text-dim)">暂无历史信号</td></tr>';
  });
}

/* ══════ MILESTONES ══════ */
function loadMilestones(){
  if(!currentPlan||!currentPlan.milestones){
    document.getElementById('milestone-kpis').innerHTML=kpiHtml('⚠️','请先生成投资计划');
    return;
  }
  var plan=currentPlan.plan;
  var milestones=currentPlan.milestones;
  var now=new Date();
  var planDate=new Date(currentPlan.generatedAt||now);
  var monthsElapsed=Math.max(0,(now.getFullYear()-planDate.getFullYear())*12+(now.getMonth()-planDate.getMonth()));

  document.getElementById('milestone-kpis').innerHTML=[
    kpiHtml('¥'+numInt(plan.initialCapital),'初始资金'),
    kpiHtml('¥'+numInt(plan.targetCapital),'目标资金'),
    kpiHtml(plan.requiredReturn+'%','目标收益'),
    kpiHtml(monthsElapsed+'/'+plan.timeframeMonths,'已过/总月数'),
  ].join('');

  // Milestone chart
  var chart=document.getElementById('milestone-chart');
  chart.innerHTML=milestones.map(function(m){
    var progress=Math.min(monthsElapsed>=m.month?100:monthsElapsed>=m.month-1?(monthsElapsed-m.month+1)*100:0,100);
    var isCurrent=m.month===monthsElapsed+1;
    return '<div style="display:flex;align-items:center;gap:8px;padding:4px 0;'+(isCurrent?'background:var(--bg3);border-radius:4px;padding:6px 8px':'')+'">' +
      '<span style="min-width:40px;font-size:12px;color:'+(isCurrent?'var(--accent)':'var(--text-dim)')+'">第'+m.month+'月</span>' +
      '<div class="milestone-bar" style="flex:1"><div class="milestone-fill" style="width:'+progress+'%"></div></div>' +
      '<span style="min-width:100px;font-size:12px;text-align:right">¥'+numInt(m.targetCapital)+'</span>' +
      '<span style="min-width:60px;font-size:11px;color:var(--text-dim);text-align:right">+'+m.targetReturn+'%</span>' +
      '</div>';
  }).join('');

  // Milestone details table
  var tbody=document.querySelector('#milestone-table tbody');
  tbody.innerHTML=milestones.map(function(m){
    var status=monthsElapsed>=m.month?'<span class="badge badge-green">已过</span>':
              m.month===monthsElapsed+1?'<span class="badge badge-blue">当前</span>':
              '<span class="badge badge-yellow">未到</span>';
    return '<tr>'+
      '<td>第 '+m.month+' 月</td>'+
      '<td>¥'+numInt(m.targetCapital)+'</td>'+
      '<td>+'+m.targetReturn+'%</td>'+
      '<td>-</td><td>-</td>'+
      '<td>'+status+'</td></tr>';
  }).join('');
}

function viewStock(sym){
  window.open(B+'/quant/admin/stock?symbol='+encodeURIComponent(sym),'_blank');
}

function startMonitoring(){
  // Hide fill confirmation panel if visible
  var fillPanel=document.getElementById('rec-fill-confirm');
  if(fillPanel)fillPanel.style.display='none';
  stopFillPolling();
  toast('🚀 监控已启用。切换到"交易信号"查看实时信号');
  // Switch to signals tab
  activateTab('signals');
  history.pushState(null,null,'#signals');
}

/* ── Auto-refresh signals every 30s ── */
setInterval(function(){
  var at=document.querySelector('.tab.active');
  if(!at)return;
  if(at.dataset.tab==='portfolio')refreshPortfolio();
  if(at.dataset.tab==='signals')loadSignals();
},30000);

/* Latest plan auto-load is now handled inside loadPlanHistory() */
`;
}
